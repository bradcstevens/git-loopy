"""``git_loopy.run_readback`` — what this **Run** parsed, echoed back and checked.

One question, asked once at **Run** start: *what did the kit parse, and does
every pair it is holding survive the gate?*

**Why a readback is the only validation there is.** No validator for the
``[routing]`` table can exist. :func:`git_loopy.settings.table_routing` validates
an entry's *shape* — exactly ``{model, effort}``, both strings — and stops there,
because the key is the operator's vocabulary and the pair is the vendor's, and
nothing outside the Run can say whether a well-formed table is the one the
operator meant. ``implmentation`` parses cleanly, matches no issue label, and is
dead config that costs an **Iteration** to discover. So the operator reading back
what the kit parsed *is* the validation, which is why the block echoes the keys
themselves and never a count of them: a count cannot reveal a misspelling.

Design notes:

* **It gate-checks what nothing else reaches.** ``[routing]`` entries and the
  **Escalation rung** arrive from Config verbatim and are gated at *resolution*,
  per issue — so a route no issue carries is never gated at all, and the rung is
  gated for the first time at a stalled issue's next **Pickup**, which is the one
  moment the Run is already going badly. Both are checked here through the shared
  gates, so a read-back pair and a **Routed pair** are gated by identical code.
  The run-wide default is *not* re-gated: :mod:`git_loopy.cli` already gated it
  before it reached the :class:`~git_loopy.config.RunConfig`, and a second gate
  could only agree (noise) or disagree (two answers to one question).
* **It is non-fatal by construction.** Nothing here raises, nothing here decides,
  and no verdict it reaches changes what the Run does. A readback that could stop
  a Run would be a validator, which is the thing that cannot exist.
* **It reads the harness version; it never reads the roster.** The spawned CLI
  version is the single fact whose absence produced ADR-0019, and comparing it to
  :data:`~git_loopy.config.MODEL_ROSTER_CLI_VERSION` is an offline comparison that
  can never fail, never costs a round trip and never depends on a network the
  block would otherwise be conditional on. Comparing live roster *content* needs
  the Run's model listing and belongs to the preflight that already holds it
  (:mod:`git_loopy.roster_preflight`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from git_loopy.config import (
    MODEL_ROSTER_CLI_VERSION,
    TASK_TYPE_KEYS,
    GateWarning,
    RunConfig,
    gate_context_tier,
    gate_reasoning_effort,
)

__all__ = [
    "PairReadback",
    "RouteReadback",
    "RunReadback",
    "build_run_readback",
    "run_start_payload",
    "spawned_harness_version",
]


@dataclass(frozen=True)
class PairReadback:
    """One configured ``(model, effort)`` pair, as authored and as gated.

    Both efforts travel because the readback answers two questions with one
    line. ``configured_effort`` is what the operator wrote — the echo, which is
    the only validation the table can have — and :attr:`effort` is what would
    actually be sent. Where they differ, :attr:`gate_warnings` says why, and a
    readback carrying only the gated value would report the drop without naming
    the effort that was dropped.

    Attributes:
        model: The model id, verbatim. The gate never rewrites a model id.
        effort: The gated effort, or ``None`` for "let the backend pick" —
            including where the gate dropped an effort the model refuses.
        configured_effort: The effort exactly as Config supplied it.
        gate_warnings: Every signal the effort gate raised, kept rather than
            discarded, because the verdict is the whole point of checking.
    """

    model: str
    effort: str | None
    configured_effort: str | None
    gate_warnings: tuple[GateWarning, ...]

    def as_payload(self) -> dict[str, Any]:
        """This pair as a JSON object on ``wrapper.run.start``."""
        return {
            "model": self.model,
            "effort": self.effort,
            "configured_effort": self.configured_effort,
            "gate_warnings": [warning.value for warning in self.gate_warnings],
        }


@dataclass(frozen=True)
class RouteReadback(PairReadback):
    """One ``[routing]`` entry: its key, echoed verbatim, and its pair, gated.

    There is deliberately **no** "is this key real" flag. #375 closed the
    task-type taxonomy at the Config reader (ADR-0029), so a routing key outside
    :data:`~git_loopy.config.TASK_TYPE_KEYS` is refused at load — naming the
    value and the permitted keys — and never reaches a Run at all. A flag that
    could only ever answer yes would be a branch nothing can enter.

    Attributes:
        key: The routing key **exactly** as Config spelled it, so the readback
            shows what arrived rather than what was inferred — the same rule
            :class:`~git_loopy.config.RoutingResolution` keeps for the keys a
            tracker supplied.
    """

    key: str

    def as_payload(self) -> dict[str, Any]:
        return {"key": self.key, **super().as_payload()}


@dataclass(frozen=True)
class RunReadback:
    """Everything a **Run** parsed that decides what its work costs.

    Attributes:
        model: The run-wide **Default pair**'s model, or ``None`` where the
            default defers the choice to the SDK.
        effort: The Default pair's reasoning effort, already gated by ``cli``.
        context_tier: The run-level tier (ADR-0017) completing the triple.
        escalation_rung: The **Escalation rung**, gate-checked, or ``None`` when
            escalation is not in force this Run.
        routes: Every ``[routing]`` entry, in the order Config yielded them.
        unconfigured_keys: The taxonomy members no route names, in the
            taxonomy's own presentation order. The misconfiguration a closed
            taxonomy cannot catch: every key is spelled correctly and the table
            is still half a table, and the only place that fact surfaces today
            is a **Routing source** on a Pickup that already happened.
        routing_suppressed: Whether an explicit ``--model`` / ``--reasoning-effort``
            suppressed routing run-wide. A suppressed table is still read back:
            what was parsed is a fact about the Config even when it is not in
            force, and silence would read as an absent table.
        harness_version: The Copilot CLI version the SDK spawns, or ``None`` when
            it could not be read.
        roster_cli_version: The CLI version the kit's roster was captured against.
    """

    model: str | None
    effort: str | None
    context_tier: str
    escalation_rung: PairReadback | None
    routes: tuple[RouteReadback, ...]
    unconfigured_keys: tuple[str, ...]
    routing_suppressed: bool
    harness_version: str | None
    roster_cli_version: str

    @property
    def roster_diverged(self) -> bool | None:
        """Whether the roster describes a harness other than the one spawned.

        Three-valued on purpose. ``None`` is an unreadable harness version —
        nobody knows whether the roster describes the binary this Run runs on —
        and reporting that as agreement would be the readback stating the one
        thing it cannot check.
        """
        if self.harness_version is None:
            return None
        return self.harness_version != self.roster_cli_version

    def as_run_start_payload(self) -> dict[str, Any]:
        """This record as the readback half of a ``wrapper.run.start`` payload.

        The record projects itself, for the reason
        :meth:`~git_loopy.config.RoutingResolution.as_pickup_payload` does: three
        drive paths emit this Event, and three hand-written projections of one
        record is how one Run start ends up described three ways on one wire.

        ``model`` / ``effort`` are the family's existing pair vocabulary, and at
        Run start there is no issue for them to be ambiguous about: they are the
        **Default pair**. ``roster_diverged`` travels rather than being left to
        each reader to derive, because a reader that recomputed it would have to
        know that a ``null`` harness version means *unknown* and not *agrees*.
        """
        return {
            "model": self.model,
            "effort": self.effort,
            "context_tier": self.context_tier,
            "escalation_rung": (
                None
                if self.escalation_rung is None
                else self.escalation_rung.as_payload()
            ),
            "routes": [route.as_payload() for route in self.routes],
            "unconfigured_task_type_keys": list(self.unconfigured_keys),
            "routing_suppressed": self.routing_suppressed,
            "harness_version": self.harness_version,
            "roster_cli_version": self.roster_cli_version,
            "roster_diverged": self.roster_diverged,
        }


def _gate_pair(model: str, effort: str | None, *, context_tier: str) -> PairReadback:
    """Gate one configured pair the way a **Pickup** would gate it.

    Both gates run — effort against the model, then the run-level tier against
    the model — because a routed pair meets both and a readback that checked one
    of them would clear a pair the Run would still downgrade.
    """
    gated = gate_reasoning_effort(model, effort)
    _, tier_warning = gate_context_tier(model, context_tier)
    warnings: tuple[GateWarning, ...] = tuple(
        warning for warning in (gated.warning, tier_warning) if warning is not None
    )
    return PairReadback(
        model=gated.model,
        effort=gated.effort,
        configured_effort=effort,
        gate_warnings=warnings,
    )


def build_run_readback(
    config: RunConfig, *, harness_version: str | None = None
) -> RunReadback:
    """Read one **Run**'s Config back, gate-checking every pair it holds.

    Pure and total: it performs no I/O, raises nothing, and decides nothing. The
    harness version is injected rather than read here so the seam stays testable
    without an SDK — :func:`spawned_harness_version` is the production supplier.
    """
    routes = tuple(
        RouteReadback(
            key=key,
            **vars(_gate_pair(model, effort, context_tier=config.context_tier)),
        )
        for key, (model, effort) in config.routing.items()
    )
    rung = config.escalation_rung
    return RunReadback(
        model=config.model,
        effort=config.reasoning_effort,
        context_tier=config.context_tier,
        escalation_rung=(
            None
            if rung is None
            else _gate_pair(rung[0], rung[1], context_tier=config.context_tier)
        ),
        routes=routes,
        unconfigured_keys=tuple(
            key for key in TASK_TYPE_KEYS if key not in config.routing
        ),
        routing_suppressed=config.routing_suppressed,
        harness_version=harness_version,
        roster_cli_version=MODEL_ROSTER_CLI_VERSION,
    )


def spawned_harness_version() -> str | None:
    """The Copilot CLI version the SDK spawns, or ``None`` when unreadable.

    The SDK pins the binary it downloads and runs, and that pin is the version
    the roster has to be true of (ADR-0019). Read through a lazy import and
    behind a total ``except`` because this is an *observability* fact: an SDK
    that renamed the constant, or an environment without the SDK at all, must
    cost the block one line and never the Run.

    ``COPILOT_CLI_PATH`` can relocate the harness at runtime, in which case the
    pin names a binary this Run does not spawn. That is the same class of
    divergence the comparison exists to surface and is not silently corrected
    here: guessing a version from a relocated path would replace a checkable
    fact with an inference.
    """
    try:
        from copilot import _cli_version

        version = _cli_version.CLI_VERSION
    except Exception:
        return None
    return version if isinstance(version, str) and version else None


def run_start_payload(config: RunConfig) -> dict[str, Any]:
    """The readback half of a ``wrapper.run.start`` payload for this **Run**.

    The one call the drive paths make. Composing the pure builder with the live
    harness read here rather than at each emit site is what keeps the three
    ``wrapper.run.start`` sites — a serial Run and a **Parallel mode** Run —
    describing one Run start one way.
    """
    return build_run_readback(
        config, harness_version=spawned_harness_version()
    ).as_run_start_payload()
