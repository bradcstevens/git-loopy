"""``git_loopy.config`` — frozen per-invocation configuration.

The :class:`RunConfig` dataclass is the single load-bearing config seam
between :mod:`git_loopy.cli` (which composes it from CLI flags + env
vars + defaults) and :mod:`git_loopy.loop` (which consumes it).

It also satisfies — structurally, via Python's :pep:`544` Protocol
machinery — the :class:`git_loopy.session.SessionConfig` Protocol, so
the loop can pass the same object to :class:`~git_loopy.session.IterationSession`
without an explicit conversion. The Protocol-conformance contract is:

- ``deny_tools: frozenset[str]``
- ``deny_skills: frozenset[str]``
- ``verbosity: int``
- ``render_reasoning: bool``

Design notes:

* **Frozen.** The loop reuses the same config across every iteration;
  freezing makes accidental mid-run mutation impossible.
* **No I/O at construction time.** Every field is a value or a :class:`Path`
  reference; opening anything is :func:`git_loopy.loop.run`'s job.
* **``otel_enabled`` is plumbed but inert in this slice.** Issue #12
  wires it; this slice just makes sure the flag survives the CLI →
  RunConfig → loop pipe so #12 doesn't have to re-touch the dataclass.
* **stdlib only.** Enforced by ``tests/test_config.py``'s import-guard
  test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Literal, Mapping

__all__ = [
    "RunConfig",
    "SkillPolicyInput",
    "SkillPolicyInputs",
    "REASONING_EFFORT_ORDER",
    "REASONING_EFFORTS",
    "MODEL_REASONING_EFFORTS",
    "SUPPORTED_MODELS",
    "DEFAULT_SEND_TIMEOUT_SECONDS",
    "TASK_TYPE_LABEL_PREFIX",
    "TASK_TYPE_KEYS",
    "RECOMMENDED_ROUTING",
    "TaskTypeError",
    "validate_task_type_key",
    "task_type_refusal",
    "EffortGateWarning",
    "ContextTierGateWarning",
    "GateWarning",
    "GatedEffort",
    "gate_reasoning_effort",
    "CONTEXT_TIERS",
    "DEFAULT_CONTEXT_TIER",
    "MODEL_CONTEXT_TIERS",
    "gate_context_tier",
    "RoutingSource",
    "RoutingLifecyclePosition",
    "RoutingResolution",
    "resolve_iteration_model",
]

#: The triage-label prefix the runner reads to route an Active issue. The key
#: *after* the prefix (e.g. ``task-type:docs`` -> ``docs``) indexes
#: :attr:`RunConfig.routing`. The runner **reads** this label and **never**
#: infers the task type from issue content (mirroring ``parallel-safe`` /
#: ``independent``); the ``[routing]`` table is the source of truth for valid keys.
TASK_TYPE_LABEL_PREFIX = "task-type:"

#: Per-model reasoning-effort capability matrix for the models the kit
#: officially supports. Maps each Copilot model id to the set of
#: reasoning-effort values that model accepts. An **empty set** means the
#: model does not support reasoning-effort configuration at all — the
#: live Copilot CLI hard-rejects ``session.create`` with
#: "does not support reasoning effort configuration" if a non-null
#: ``reasoningEffort`` is sent for such a model, so :mod:`git_loopy.cli`
#: forces ``reasoning_effort=None`` for them. Models absent from this
#: table are treated as "unknown": the CLI warns and passes them through
#: unchanged (the Copilot CLI is the final authority on model validity).
#:
#: Keep this in lockstep with the Copilot CLI's ``models.list`` output.
MODEL_REASONING_EFFORTS: dict[str, frozenset[str]] = {
    "auto": frozenset(),
    "claude-sonnet-5": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "claude-sonnet-4.6": frozenset({"low", "medium", "high", "max"}),
    "claude-sonnet-4.5": frozenset(),
    "claude-haiku-4.5": frozenset(),
    "claude-opus-5": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "claude-opus-4.8": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "claude-opus-4.7": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "claude-opus-4.6": frozenset({"low", "medium", "high", "max"}),
    "gpt-5.5": frozenset({"none", "low", "medium", "high", "xhigh"}),
    "gpt-5.4": frozenset({"none", "low", "medium", "high", "xhigh"}),
    "gpt-5.3-codex": frozenset({"low", "medium", "high", "xhigh"}),
    "gpt-5.4-mini": frozenset({"none", "low", "medium", "high", "xhigh"}),
    "gpt-5-mini": frozenset({"low", "medium", "high"}),
    "gemini-3.1-pro-preview": frozenset({"low", "medium", "high"}),
    "gemini-3.6-flash": frozenset({"minimal", "low", "medium", "high"}),
    "gemini-3.5-flash": frozenset({"minimal", "low", "medium", "high"}),
    "gpt-5.6-luna": frozenset(
        {"none", "low", "medium", "high", "xhigh", "max"}
    ),
    "gpt-5.6-sol": frozenset(
        {"none", "low", "medium", "high", "xhigh", "max"}
    ),
    "gpt-5.6-terra": frozenset(
        {"none", "low", "medium", "high", "xhigh", "max"}
    ),
    "mai-code-1-flash-picker": frozenset({"low", "medium", "high"}),
}

#: The model ids the kit officially supports (the keys of
#: :data:`MODEL_REASONING_EFFORTS`). :mod:`git_loopy.cli` uses this to
#: decide whether a requested model is "known" (full per-model effort
#: gating) or "unknown" (warn-and-pass-through).
SUPPORTED_MODELS: frozenset[str] = frozenset(MODEL_REASONING_EFFORTS)

#: Reasoning-effort values the kit accepts for the ``reasoning_effort``
#: knob, in stable low-to-high display order. Used by :mod:`git_loopy.cli`
#: help and :mod:`git_loopy.init` so operator-facing choices stay aligned.
#:
#: The live Copilot CLI vocabulary includes ``none`` and ``minimal`` as well
#: as the existing levels. ``reasoning_effort`` is forwarded to the SDK as a
#: plain ``str`` so values accepted by the CLI remain usable when an SDK type
#: stub lags. The per-model subset each model actually accepts lives in
#: :data:`MODEL_REASONING_EFFORTS`.
REASONING_EFFORT_ORDER: tuple[str, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
#: Membership form of :data:`REASONING_EFFORT_ORDER`, used as the shared
#: syntactic gate (for example, to reject ``"ultra"``).
REASONING_EFFORTS: frozenset[str] = frozenset(REASONING_EFFORT_ORDER)

#: The root-session context tiers the Copilot CLI exposes. ``inherit`` is a
#: subagent-only setting and has no meaning for an Iteration.
CONTEXT_TIERS: frozenset[str] = frozenset({"default", "long_context"})
DEFAULT_CONTEXT_TIER = "default"

#: Per-model **context tier** capability, the tier half of the model roster
#: (:data:`MODEL_REASONING_EFFORTS` is the effort half). Maps a model id to the
#: root-session tiers it offers. A model **absent** from this table is
#: "unknown": the tier passes through untouched, exactly as an off-roster model
#: keeps its effort, because the live Copilot CLI is the authority.
#:
#: It ships **empty** on purpose. ADR-0017 put tier capability on the roster
#: rather than in a parallel table, and made verification against the harness
#: the kit actually spawns a precondition of extending it — the candidate list
#: of tier-less models comes from a source whose harness claims have already
#: been wrong more than once, and ADR-0019 requires a roster row and its
#: ``cli_version`` stamp to move as one regeneration. Populating it is that
#: regeneration; the gate below is live and pinned in the meantime, so the data
#: is the only thing owed.
MODEL_CONTEXT_TIERS: dict[str, frozenset[str]] = {}

#: The seven permitted ``task-type`` -> ``(model, effort)`` routes
#: (decision #110). The guided setup surfaces, seeds, and renders this fixed
#: taxonomy; no operator-defined task-type key is accepted.
#:
#: Keyed by the bare task-type key (the part *after* :data:`TASK_TYPE_LABEL_PREFIX`,
#: matching :attr:`RunConfig.routing` and the ``[routing]`` config table), in the
#: fixed **presentation order** the guided walk surfaces. A 7-type core:
#:
#: ==================  ====================  ========
#: task-type key       Model                 Effort
#: ==================  ====================  ========
#: ``planning``        ``claude-opus-5``     ``max``
#: ``review``          ``gpt-5.6-terra``     ``xhigh``
#: ``implementation``  ``claude-sonnet-5``   ``low``
#: ``test``            ``claude-sonnet-5``   ``medium``
#: ``docs``            ``claude-sonnet-5``   ``low``
#: ``chore``           ``gpt-5.6-luna``      ``none``
#: ``bugfix``          ``claude-opus-5``     ``xhigh``
#: ==================  ====================  ========
#:
#: **Provenance.** Adopted from the model-routing run of **2026-07-24** (the v3
#: table posted on issue #280, sourced from the live ``GET /models`` response for
#: Copilot CLI ``1.0.75``), locked by #285 and #294, and recorded in
#: ADR-0035. The table **departs from the run twice**, and both departures are
#: what a future reader will otherwise "correct" back toward it:
#:
#: * ``review`` is ``gpt-5.6-terra``, not the run's ``gpt-5.6-sol``. The run's
#:   whole mitigation for Sol's measured task-cheating is that it "writes no
#:   files and has no metric to game" — true of the ``code-review`` *subagent*
#:   and false of the ``review`` *Task type*, which is a full write-capable Lane
#:   holding authority to close its own issue. Terra is the run's own runner-up:
#:   same vendor, so cross-vendor review still holds by construction.
#: * ``chore`` is ``gpt-5.6-luna @ none``, not ``claude-haiku-4.5``. Haiku
#:   exposes **no effort dial at all**, so ``claude-haiku-4.5 @ none`` is not a
#:   configuration that exists: it *reads* as "Haiku with reasoning off" and
#:   *delivers* Haiku with an uncapped internal thinking budget — and an effort
#:   supplied to an effort-incapable model **hard-rejects session creation**
#:   rather than downgrading (ADR-0019). Luna @ ``none`` is deterministic about
#:   not thinking. The boundary rule this cashes out: **a reasoning-incapable
#:   model is unroutable through** ``[routing]``, invariant at four layers —
#:   this mapping's ``tuple[str, str]`` type, ``settings.table_routing``'s
#:   exactly-``{model, effort}`` demand, ``init``'s ``supported_efforts`` filter,
#:   and ``tests/test_config.py``'s uniform gates-clean assertion.
#:
#: ``bugfix`` is #294's addition at ``xhigh`` — ``high`` would make labelling a
#: bug *cheaper* than not labelling it, and ``max`` is #291's escalation rung, so
#: a ``max`` route would make escalation a no-op for bug work.
#:
#: ``planning`` and ``bugfix`` share a model with the **global default**
#: (``claude-opus-5 @ xhigh``, ADR-0036) but not, in ``planning``'s case, its
#: effort: the default deliberately sits one rung below the escalation rung so
#: it *reserves* the ceiling, while ``planning`` spends it outright. That
#: relation is rationale, not mechanism — the default is an independent constant
#: in ``cli.py`` and is never derived from this table.
RECOMMENDED_ROUTING: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "planning": ("claude-opus-5", "max"),
        "review": ("gpt-5.6-terra", "xhigh"),
        "implementation": ("claude-sonnet-5", "low"),
        "test": ("claude-sonnet-5", "medium"),
        "docs": ("claude-sonnet-5", "low"),
        "chore": ("gpt-5.6-luna", "none"),
        "bugfix": ("claude-opus-5", "xhigh"),
    }
)

#: The closed task-type taxonomy in its stable presentation order.
TASK_TYPE_KEYS: tuple[str, ...] = tuple(RECOMMENDED_ROUTING)


class TaskTypeError(ValueError):
    """A task type outside :data:`TASK_TYPE_KEYS` was supplied.

    Carries the offending ``key`` alongside the message so a caller can name the
    remedy — a refusal an operator cannot act on locks them out of the Config
    they need to correct (#375).
    """

    def __init__(
        self, message: str, *, key: str, scope: Literal["global", "project"] | None = None
    ) -> None:
        super().__init__(message)
        self.key = key
        self.scope = scope


def validate_task_type_key(
    key: str, *, scope: Literal["global", "project"] | None = None
) -> str:
    """Return ``key`` when it belongs to the closed task-type taxonomy."""
    if key not in TASK_TYPE_KEYS:
        raise TaskTypeError(
            f"unsupported task type {key!r}; permitted keys: "
            f"{', '.join(TASK_TYPE_KEYS)}",
            key=key,
            scope=scope,
        )
    return key


def task_type_refusal(exc: TaskTypeError) -> str:
    """Render a *persisted* out-of-taxonomy **Task type** with the way to clear it.

    Lives beside the taxonomy because complying with a closed set is part of the
    set: the closure (#375) refuses an unknown key at every write seam, but a
    Config written before it already carries one, and every surface that reads
    that file — a Run, ``config list``, ``config get``, ``config routing set`` —
    is refused by the same key. Without a stated remedy the operator is locked
    out of the Config they have to correct, and hand-edited TOML is the only way
    back.

    The offending key is named because it is usually **not** the one that was
    typed: ``routing set docs`` against a Config carrying a legacy ``custom`` is
    refused by ``custom``, and a message naming only that reads as the tool
    rejecting ``docs``.
    """
    scope = f" --{exc.scope}" if exc.scope is not None else ""
    return (
        f"{exc}. Clear it with `git-loopy config routing unset {exc.key}{scope}`, or "
        f"edit the [routing] table in the file `git-loopy config path` names."
    )


#: Default SDK ``send_and_wait`` timeout (seconds). AFK iterations can run for
#: an hour or more, so the SDK's own 60s default is far too aggressive. Lives
#: here (not in :mod:`git_loopy.loop`) because it is now a persisted, resolver-fed
#: :class:`RunConfig` knob (issue #51): the loop reads
#: :attr:`RunConfig.send_timeout_seconds` rather than the env directly.
DEFAULT_SEND_TIMEOUT_SECONDS: float = 7200.0


@dataclass(frozen=True)
class SkillPolicyInput:
    """One Skill-policy source, preserving absent versus explicit empty."""

    present: bool = False
    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.present and self.names:
            raise ValueError("an absent Skill-policy input cannot contain names")
        object.__setattr__(self, "names", tuple(sorted(set(self.names))))


@dataclass(frozen=True)
class SkillPolicyInputs:
    """Uncombined configured and invocation inputs for Skill-policy resolution."""

    project: SkillPolicyInput = field(default_factory=SkillPolicyInput)
    global_: SkillPolicyInput = field(default_factory=SkillPolicyInput)
    environment: SkillPolicyInput = field(default_factory=SkillPolicyInput)
    enable_skills: frozenset[str] = field(default_factory=frozenset)
    disable_skills: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "enable_skills", frozenset(self.enable_skills))
        object.__setattr__(self, "disable_skills", frozenset(self.disable_skills))


@dataclass(frozen=True)
class ContinuationInput:
    """One operator configuration source for Continuation authority.

    Absent is not the same as empty. A source that declared no mode never asked
    for anything and drops out of the resolution entirely; a source that declared
    `off`, or an empty ceiling, asked for *nothing in particular* and narrows
    every other source down to it. Collapsing the two would let an unconfigured
    global table silently veto a project that adopted report mode.
    """

    mode: str | None = None
    trusted_producers: tuple[str, ...] = ()
    actor: str | None = None
    maintainers: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    action_kinds: tuple[str, ...] = ()
    instruction_modes: tuple[str, ...] = ()
    effect_scopes: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.mode is not None

    def as_source(self, source: str) -> dict[str, Any]:
        """Render this source in the `resolve-authority` request shape."""
        declared: dict[str, Any] = {
            "source": source,
            "mode": self.mode,
            "trusted_producers": list(self.trusted_producers),
            "ceilings": {
                "repositories": list(self.repositories),
                "targets": list(self.targets),
                "action_kinds": list(self.action_kinds),
                "instruction_modes": list(self.instruction_modes),
                "effect_scopes": list(self.effect_scopes),
            },
        }
        if self.actor is not None:
            declared["actor"] = self.actor
        if self.maintainers:
            declared["maintainers"] = list(self.maintainers)
        return declared


@dataclass(frozen=True)
class ContinuationInputs:
    """Uncombined Continuation configuration, in the locked narrowing order.

    These stay uncombined here for the reason the Skill-policy inputs do: the
    combination *is* the contract, it lives in one place
    (`continuation.resolve_authority`), and duplicating it in the config resolver
    would give the family two narrowing implementations to disagree with.
    """

    global_: ContinuationInput = field(default_factory=ContinuationInput)
    project: ContinuationInput = field(default_factory=ContinuationInput)
    runtime: ContinuationInput = field(default_factory=ContinuationInput)

    def declared_sources(self) -> list[dict[str, Any]]:
        """Every source that declared a mode, in narrowing order."""
        return [
            declared.as_source(name)
            for name, declared in (
                ("global", self.global_),
                ("project", self.project),
                ("runtime", self.runtime),
            )
            if declared.present
        ]


class EffortGateWarning(Enum):
    """Why :func:`gate_reasoning_effort` would warn — the caller owns surfacing it.

    A *signal*, not a rendered message: the gate stays presentation-free and pure
    so every caller (the run-wide resolver, the ``init`` seed, the per-issue
    routing seam #147) can phrase the warning in its own voice and apply its own
    suppression (e.g. the resolver only nags about a reasoning-incapable model
    when the operator asked for the effort *explicitly*).
    """

    #: The model id is not in the kit's roster; the pair is passed through
    #: unchanged (the Copilot CLI is the final authority on model validity).
    UNKNOWN_MODEL = "unknown_model"
    #: The model accepts no reasoning-effort configuration at all, but an effort
    #: was requested; it was dropped to ``None``.
    INCAPABLE_MODEL = "incapable_model"
    #: A known model was asked for an effort it does not accept; it was dropped
    #: to ``None`` (rather than passed through to a doomed ``session.create``).
    DROPPED_EFFORT = "dropped_effort"


class ContextTierGateWarning(Enum):
    """Why a requested context tier was downgraded by the model gate."""

    UNSUPPORTED_CONTEXT_TIER = "unsupported_context_tier"


GateWarning = EffortGateWarning | ContextTierGateWarning


@dataclass(frozen=True)
class GatedEffort:
    """The result of gating a ``(model, effort)`` pair against the roster.

    Attributes:
        model: The model id, returned verbatim — the gate never rewrites it.
        effort: The gated reasoning effort: the input effort when accepted (or
            when the model is unknown), otherwise ``None``.
        warning: The :class:`EffortGateWarning` signal, or ``None`` when the pair
            gated cleanly.
    """

    model: str
    effort: str | None
    warning: EffortGateWarning | None = None


def gate_reasoning_effort(model: str, effort: str | None) -> GatedEffort:
    """Gate a ``(model, effort)`` pair against the roster (:data:`MODEL_REASONING_EFFORTS`).

    The single shared effort gate (issue #145). Pure — no I/O — and exhaustively
    table-testable. Implements the locked policy:

    * **Unknown model** (not in the roster): pass the pair through unchanged and
      signal :attr:`~EffortGateWarning.UNKNOWN_MODEL`. Checked *first*, so an
      unknown model still signals even when ``effort`` is already ``None`` (the
      model itself is the concern) — matching the run-wide resolver's behaviour.
    * **Reasoning-incapable model** (empty effort set): force ``effort`` to
      ``None`` (the live CLI hard-rejects ``session.create`` otherwise) and signal
      :attr:`~EffortGateWarning.INCAPABLE_MODEL` *only* when an effort was set.
    * **Known model, effort accepted** (or ``effort`` already ``None``): keep the
      pair, no warning.
    * **Known model, effort not accepted**: drop ``effort`` to ``None`` and signal
      :attr:`~EffortGateWarning.DROPPED_EFFORT`.

    Args:
        model: The (base) model id.
        effort: The requested reasoning effort, or ``None``.

    Returns:
        A :class:`GatedEffort` carrying the gated pair and the warning signal.
    """
    allowed = MODEL_REASONING_EFFORTS.get(model)
    if allowed is None:
        return GatedEffort(
            model=model, effort=effort, warning=EffortGateWarning.UNKNOWN_MODEL
        )
    if not allowed:
        warning = EffortGateWarning.INCAPABLE_MODEL if effort is not None else None
        return GatedEffort(model=model, effort=None, warning=warning)
    if effort is None or effort in allowed:
        return GatedEffort(model=model, effort=effort, warning=None)
    return GatedEffort(
        model=model, effort=None, warning=EffortGateWarning.DROPPED_EFFORT
    )


def gate_context_tier(
    model: str | None, context_tier: str
) -> tuple[str, ContextTierGateWarning | None]:
    """Return the root-session context tier the resolved model actually offers.

    The run-level tier (ADR-0017) meets a per-task-type model, so tier validity
    is model-dependent and can only be settled *after* the model resolves — the
    same seam where reasoning effort is already gated. A model with no
    :data:`MODEL_CONTEXT_TIERS` row is left untouched; a model whose row omits
    the requested tier is **downgraded** to :data:`DEFAULT_CONTEXT_TIER` with a
    signal the resolver carries on its record, because the harness silently
    ignores an unavailable tier and erroring would make git-loopy stricter than
    the thing it wraps.
    """
    offered = MODEL_CONTEXT_TIERS.get(model) if model is not None else None
    if offered is not None and context_tier not in offered:
        return DEFAULT_CONTEXT_TIER, ContextTierGateWarning.UNSUPPORTED_CONTEXT_TIER
    return context_tier, None


class RoutingSource(Enum):
    """The closed provenance vocabulary of a :class:`RoutingResolution`.

    Why a pair was selected, and nothing else: it is deliberately silent about
    whether this is an issue's first attempt (that is
    :class:`RoutingLifecyclePosition`). ``ESCALATED`` is the one value that makes
    a rung pair's provenance true, and it cannot express a same-pair crash retry
    — which is exactly why the two axes are separate fields rather than one
    merged vocabulary.

    ``DEFAULTED_UNKNOWN_TASK_TYPE_KEY`` is a **Task type** the ``[routing]``
    table does not configure. A key outside the closed taxonomy never reaches a
    resolution at all: it is refused (#375, ADR-0029) before a source is chosen,
    because that key is one an unattended writer could mint as a real tracker
    label.
    """

    ROUTED = "routed"
    DEFAULTED_NO_TASK_TYPE_LABEL = "defaulted_no_task_type_label"
    DEFAULTED_UNKNOWN_TASK_TYPE_KEY = "defaulted_unknown_task_type_key"
    DEFAULTED_CONFLICTING_TASK_TYPE_KEYS = "defaulted_conflicting_task_type_keys"
    DEFAULTED_EXPLICIT_OVERRIDE = "defaulted_explicit_override"
    ESCALATED = "escalated"


class RoutingLifecyclePosition(Enum):
    """Where a resolution sits in an issue's per-Run attempt lifecycle."""

    FRESH = "fresh"
    RETRYING = "retrying"


@dataclass(frozen=True)
class RoutingResolution:
    """What one **Pickup** resolved, and why — the single source of routing truth.

    Every surface that reports a Pickup's model settings reads this record rather
    than recomputing the decision from labels and config, which is what made the
    provenance and the gate warnings unavailable everywhere but inside the
    resolver.

    Attributes:
        model: The gated model id, or ``None`` where the default defers the
            choice to the SDK.
        reasoning_effort: The gated effort, or ``None`` for "let the backend
            pick" — including where the gate dropped an effort the model rejects.
        context_tier: The run-level tier (ADR-0017) after gating against
            ``model``. A **triple** here while ``[routing]`` stays pairs: the
            tier does not vary by **Task type**, but its validity depends on the
            model the Task type selected.
        source: Why these settings were chosen.
        task_type_keys: The ``task-type`` label suffixes **exactly** as the
            tracker spelled them, in label order and unnormalised, so a readback
            shows what arrived rather than what was inferred.
        gate_warnings: Every signal the effort and tier gates raised. Carried
            here rather than discarded, which is why per-issue routing had no
            gate diagnostic in any stream.
        lifecycle_position: The attempt's position, independent of ``source``.
    """

    model: str | None
    reasoning_effort: str | None
    context_tier: str
    source: RoutingSource
    task_type_keys: tuple[str, ...]
    gate_warnings: tuple[GateWarning, ...]
    lifecycle_position: RoutingLifecyclePosition

    def as_pickup_payload(self) -> dict[str, Any]:
        """This record as the routing half of a ``wrapper.pickup.bound`` payload.

        The record projects itself (#407) because a serial **Iteration** and a
        **Lane** both publish the pair they resolved, and two hand-written
        projections of one record is how the same Pickup ends up described two
        ways on one wire.

        The pair travels as ``model`` / ``effort``, the family's existing wire
        vocabulary for a **Routed pair** — a reader that can read a
        Contribution's pair reads a Pickup's the same way. The provenance
        travels as ``routing_source`` rather than ``source`` because the same
        payload's ``reason`` already answers "why" in the unrelated Pickup
        vocabulary (``order`` / ``priority`` / ``pin``), and one record
        carrying two differently-scoped answers to one word is unreadable.

        Every value is a JSON scalar or a list of them, and ``None`` is
        **kept** rather than dropped: a null ``effort`` is the gate having left
        the choice to the backend, which is a fact about the Pickup and not an
        absent field.
        """
        return {
            "model": self.model,
            "effort": self.reasoning_effort,
            "context_tier": self.context_tier,
            "routing_source": self.source.value,
            "task_type_keys": list(self.task_type_keys),
            "gate_warnings": [warning.value for warning in self.gate_warnings],
            "lifecycle_position": self.lifecycle_position.value,
        }


@dataclass(frozen=True)
class RunConfig:
    """Frozen per-invocation configuration for the ``git-loopy`` runner.

    Attributes:
        model: Optional Copilot model id override. ``None`` lets the SDK
            pick its default (which respects ``~/.copilot`` config).
        reasoning_effort: Optional reasoning-effort override forwarded to
            ``copilot.CopilotClient.create_session``. One of ``"none"`` /
            ``"minimal"`` / ``"low"`` / ``"medium"`` / ``"high"`` /
            ``"xhigh"`` / ``"max"``, or ``None``. The string ``"none"``
            explicitly requests no reasoning; ``None`` lets the backend pick.
            Model id and reasoning effort are **separate axes**: the value here
            is sent verbatim while :attr:`model` carries a bare base id. Not
            every model accepts every effort — some accept no configuration at
            all — so :mod:`git_loopy.cli`
            gates this against :data:`MODEL_REASONING_EFFORTS` before
            composing the config. ``GIT_LOOPY_REASONING_EFFORT`` overrides the
            value derived from a ``GIT_LOOPY_MODEL`` suffix or the kit default.
        issue_source: ``"github"`` (default) for the GitHub-issue-backed
            collector or ``"prds"`` for the legacy local-markdown layout.
            This slice (#10) only implements ``"github"``; ``"prds"``
            lands in #11 and the loop raises :class:`NotImplementedError`
            for it.
        include_prs: Whether ``ready-for-agent`` pull requests (with an
            agent brief) join the AFK-ready pool alongside issues. ``None``
            (default) means "no explicit override" — the loop auto-detects
            the PR surface from ``docs/agents/issue-tracker.md`` (the
            ``PRs as a request surface: yes/no`` flag that
            ``/setup-agent-skills`` writes and ``/triage`` reads). ``True`` /
            ``False`` force the behaviour regardless of that file. Only
            meaningful for ``issue_source == "github"``.
        max_iterations: Cap on iterations. ``0`` (the default) means
            unlimited.
        max_nmt_strikes: Consecutive no-progress iterations tolerated
            before the loop aborts non-zero. Must be ≥ 1.
        demotion_threshold: How many no-progress **Lane contributions** one
            **Routed pair** may accumulate in a Run before **Demotion** replaces
            its **Measured routing** entry with the next pair up the price
            staircase (#366, ADR-0030). Counted per pair, so it is unrelated to
            ``max_nmt_strikes`` — that one is a single Run-scoped counter every
            Lane shares, and ends the Run. Must be ≥ 1.
        deny_tools: Tool names to reject at the SDK permission gate.
        deny_skills: Skill names (the ``arguments.skill`` value passed
            to the ``skill`` meta-tool) to reject.
        verbosity: 0 (default) / 1 (``-v``) / 2 (``-vv``) / 3 (``-vvv``).
        render_reasoning: ``False`` suppresses assistant reasoning output
            regardless of verbosity. Default ``True``.
        otel_enabled: ``True`` when OpenTelemetry tracing is enabled
            (either ``GIT_LOOPY_OTEL_ENABLED=1`` or
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set). The OTel wiring
            itself lands in issue #12; this slice just plumbs the flag.
        parallel: Opt-in **Parallel mode** cap (ADR-0008). ``1`` (the
            default) is serial — :func:`git_loopy.loop.run` drives the
            existing single-worktree loop byte-for-byte unchanged. ``> 1``
            requests up to that many concurrent **Lanes** per **Wave**;
            :mod:`git_loopy.cli` resolves it from ``--parallel N`` /
            ``GIT_LOOPY_MAX_PARALLEL`` (defaulting to ``N=3`` when Parallel
            mode is requested without an explicit cap). Must be ≥ 1.
        issue_pin: The invocation-scoped **Pin** (#396, ADR-0032): the single
            issue ``--issue N`` named, or ``None``. It bypasses the §3.2
            selection **order** and nothing else — an issue pinned here still
            has to be eligible, and one that is not fails the invocation at
            preflight rather than falling back to the head of the order.
            Deliberately not expressible as a label or an env var: both are
            globally scoped, and would point every concurrent Run at the same
            issue. Exactly one issue may be pinned; :mod:`git_loopy.cli`
            rejects a second ``--issue`` as a usage error.
        send_timeout_seconds: SDK ``send_and_wait`` timeout in seconds. A
            persisted knob (issue #51) resolved through the precedence chain
            (``GIT_LOOPY_SEND_TIMEOUT_SECONDS`` env > project Config > global
            Config > :data:`DEFAULT_SEND_TIMEOUT_SECONDS`) rather than read from
            the env inside the loop. Must be > 0.
        routing: Per-issue-type model routing (issue #146), a frozen map of
            ``task-type key -> (model, effort)`` built **once** by the config
            resolver from the ``[routing]`` config table (project overriding
            global per key). Empty when there is no ``[routing]`` block, or when
            an explicit ``--model`` / ``--reasoning-effort`` override (flag or
            env) suppresses routing run-wide — in which case every issue keeps
            resolving to the single global default, byte-for-byte as before.
            Stored as a read-only :class:`~types.MappingProxyType` so the frozen
            dataclass stays genuinely immutable across Iterations. The per-issue
            resolver (#147) reads this map and gates each pair; nothing consumes
            it in this slice.
        context_tier: Root-session context tier (ADR-0017). Run-level rather than
            a ``[routing]`` entry — it does not vary by **Task type** — and gated
            per-Iteration once the routed model resolves, because its validity
            depends on that model. Nothing configures it yet: the operator-facing
            dial (flag, env, Config) is ADR-0017's own outstanding half, so it
            holds :data:`DEFAULT_CONTEXT_TIER` for every Run today and the
            resolver simply reports what it was handed.
        routing_suppressed: ``True`` only when an explicit model or effort
            override suppressed routing run-wide. Kept on the effective config
            so the per-issue resolver can report that distinct fallback source.
        skill_policy: Presence-aware project/global Config values, optional exact
            environment replacement, and temporary enable/disable overlays. These
            remain uncombined until the Effective Skill policy resolver consumes
            them at Run preflight.
        continuation: Presence-aware global/project/runtime Continuation
            configuration (issue #263) — mode, trusted-Producer policy, optional
            actor and maintainer identities, and the five positive ceilings. Also
            uncombined: the native `resolve-authority` operation narrows them at
            Run preflight, so the Runner and the operator's own
            `git-loopy continuation resolve-authority` answer the same question
            with the same code. Every field absent means mode `off`, which is the
            default and preserves Pool behaviour exactly.
        classifier_model: The model the **Task-type classifier** runs on
            (#377, ADR-0029), or ``None`` for "not configured". Deliberately a
            *separate* knob from :attr:`model`: borrowing the run-wide default
            would let it determine the **Task type**, and so the **Routed pair**,
            for every issue — an unmeasured prior governing every routing
            decision, and one that would appear nowhere as a routing input.
            ``None`` is an **absence, not a default**; the default is the
            cheapest pair on the live roster and is resolved from the price
            staircase by
            :func:`git_loopy.task_type_classifier.resolve_classifier_pair`, which
            is the only place that knows it.
        classifier_effort: The reasoning effort resolved alongside
            :attr:`classifier_model`, on the same terms.
        escalation_rung: The **Escalation rung** (#408) — the single
            ``(model, effort)`` pair an issue whose session ended in silent
            no-progress is retried at, once, for the rest of the Run — or
            ``None`` when escalation is not in force. ``None`` covers both
            ``[escalation] enabled = false`` and an explicit ``--model`` /
            ``--reasoning-effort`` pin, which suppresses escalation for the same
            reason it suppresses :attr:`routing`: an operator who named a model
            has taken that choice away from the runner. A **config-file-only**
            tier with no env var of its own, resolved by :mod:`git_loopy.cli`
            from the ``[escalation]`` block over its built-in
            ``_DEFAULT_ESCALATION_RUNG``. The pair is
            carried as authored; it is gated at resolution like a routed and a
            default pair, never before.
    """

    model: str | None = None
    reasoning_effort: str | None = None
    issue_source: Literal["github", "prds"] = "github"
    include_prs: bool | None = None
    max_iterations: int = 0
    max_nmt_strikes: int = 3
    demotion_threshold: int = 3
    deny_tools: frozenset[str] = field(default_factory=frozenset)
    deny_skills: frozenset[str] = field(default_factory=frozenset)
    verbosity: int = 0
    render_reasoning: bool = True
    otel_enabled: bool = False
    parallel: int = 1
    send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS
    routing: Mapping[str, tuple[str, str | None]] = field(default_factory=dict)
    context_tier: str = DEFAULT_CONTEXT_TIER
    routing_suppressed: bool = False
    skill_policy: SkillPolicyInputs = field(default_factory=SkillPolicyInputs)
    continuation: ContinuationInputs = field(default_factory=ContinuationInputs)
    classifier_model: str | None = None
    classifier_effort: str | None = None
    issue_pin: int | None = None
    escalation_rung: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if self.issue_source not in ("github", "prds"):
            raise ValueError(
                f"issue_source must be 'github' or 'prds', got "
                f"{self.issue_source!r}"
            )
        if self.max_iterations < 0:
            raise ValueError(
                f"max_iterations must be ≥ 0 (0 = unlimited), got "
                f"{self.max_iterations}"
            )
        if self.max_nmt_strikes < 1:
            raise ValueError(
                f"max_nmt_strikes must be ≥ 1, got {self.max_nmt_strikes}"
            )
        if self.demotion_threshold < 1:
            raise ValueError(
                f"demotion_threshold must be ≥ 1, got {self.demotion_threshold}"
            )
        if self.verbosity < 0 or self.verbosity > 3:
            raise ValueError(
                f"verbosity must be in 0..3, got {self.verbosity}"
            )
        if self.parallel < 1:
            raise ValueError(
                f"parallel must be ≥ 1 (1 = serial), got {self.parallel}"
            )
        if self.issue_pin is not None and self.issue_pin < 1:
            raise ValueError(
                f"issue_pin must be a positive issue number, got {self.issue_pin}"
            )
        if self.send_timeout_seconds <= 0:
            raise ValueError(
                f"send_timeout_seconds must be > 0, got "
                f"{self.send_timeout_seconds}"
            )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in REASONING_EFFORTS
        ):
            raise ValueError(
                f"reasoning_effort must be one of "
                f"{list(REASONING_EFFORT_ORDER)} or None, got "
                f"{self.reasoning_effort!r}"
            )
        if self.context_tier not in CONTEXT_TIERS:
            raise ValueError(
                f"context_tier must be one of {sorted(CONTEXT_TIERS)}, got "
                f"{self.context_tier!r}"
            )
        # Normalize `routing` to a read-only view over a *private* copy so the
        # frozen dataclass is genuinely immutable (no aliasing back to the
        # caller's dict, no post-construction mutation) and stays safe to reuse
        # across every Iteration. Built once by the resolver (issue #146).
        routing = dict(self.routing)
        for key in routing:
            validate_task_type_key(key)
        object.__setattr__(self, "routing", MappingProxyType(routing))


def _ignore_routing_warning(_message: str) -> None:
    """Default no-op ``warn`` sink so :func:`resolve_iteration_model` stays pure."""


def _gate_pair(
    pair: tuple[str | None, str | None], context_tier: str
) -> tuple[str | None, str | None, str, tuple[GateWarning, ...]]:
    """Gate a source pair and the run-level context tier against the model roster.

    A ``None`` model means "let the SDK pick its default"; it has nothing to gate,
    so the effort and the tier pass through untouched. A concrete model id is
    gated for effort against :data:`MODEL_REASONING_EFFORTS` and then — the model
    being settled — for tier against :data:`MODEL_CONTEXT_TIERS`. Both signals are
    returned rather than dropped; the caller carries them on its record.
    """
    model, effort = pair
    if model is None:
        return None, effort, context_tier, ()
    gated_effort = gate_reasoning_effort(model, effort)
    gated_context_tier, context_warning = gate_context_tier(
        gated_effort.model, context_tier
    )
    warnings = tuple(
        warning
        for warning in (gated_effort.warning, context_warning)
        if warning is not None
    )
    return gated_effort.model, gated_effort.effort, gated_context_tier, warnings


def resolve_iteration_model(
    run_config: RunConfig,
    issue_labels: Iterable[str],
    *,
    warn: Callable[[str], None] = _ignore_routing_warning,
    lifecycle_position: RoutingLifecyclePosition = RoutingLifecyclePosition.FRESH,
    escalated_pair: tuple[str | None, str | None] | None = None,
) -> RoutingResolution:
    """Resolve the **Routing resolution** for one Iteration attempt (issue #147).

    The single load-bearing seam per-issue routing hangs off. A call site invokes
    it at **Active-issue pickup**: it filters the issue's ``task-type:<key>`` labels
    (:data:`TASK_TYPE_LABEL_PREFIX` — the runner *reads* the label, it never infers
    the type), refuses any key outside the fixed :data:`TASK_TYPE_KEYS` taxonomy,
    selects a source pair per the locked table below, gates that pair
    (:func:`gate_reasoning_effort`, #145) and the run-level context tier
    (:func:`gate_context_tier`, ADR-0017), and returns all of it as one record.
    The global default is the run config's top-level ``(model, effort)``.

    It answers a record rather than a bare pair (#402) because *why* a pair was
    chosen is not recoverable from the pair: two surfaces reporting the same
    Pickup would each have to redo the decision, and the gate warnings raised on
    the way were computed and dropped, which is why per-issue routing had no gate
    diagnostic anywhere.

    Source-pair selection (decision #109):

    A key the table omits resolves to the global default as a *value*, so it is
    compared like any other rather than short-circuiting the comparison:

    ======================================  ===============  =============================
    ``task-type:`` labels on the issue      Source pair      :class:`RoutingSource`
    ======================================  ===============  =============================
    none                                    global default   ``DEFAULTED_NO_TASK_TYPE_LABEL``
    keys agreeing, all configured           that pair        ``ROUTED``
    keys agreeing, any one omitted          that pair        ``DEFAULTED_UNKNOWN_TASK_TYPE_KEY``
    >=2 keys, differing resolved values     global default   ``DEFAULTED_CONFLICTING_TASK_TYPE_KEYS``
    ======================================  ===============  =============================

    Only the conflict warns: it is the one case where the operator's own labelling
    is ambiguous. Every other fallback is *stated* on the record instead, and a
    gate correction is carried as a :data:`GateWarning` rather than narrated —
    surfacing either is a display decision, and the record is what a display reads.

    An **out-of-taxonomy** key never reaches a source at all: it is refused ahead
    of selection, including where routing is suppressed run-wide, because the
    refusal guards the label an unattended writer could mint (#375, ADR-0029).

    **Suppression.** An explicit ``--model`` / ``--reasoning-effort`` override
    (flag or env) suppresses routing for the whole Run: every issue resolves to
    the gated explicit pair under ``DEFAULTED_EXPLICIT_OVERRIDE``, which is a
    different fact from an issue that carried no label, and is recorded as one.

    **No I/O.** This function is pure and exhaustively unit-testable; the one
    warning it can emit surfaces through the injected ``warn`` callback (default
    no-op), which a call site wires to its per-issue warning channel.

    Args:
        run_config: The frozen run configuration carrying the routing map, the
            global-default ``(model, reasoning_effort)``, the run-level context
            tier, and whether an explicit override suppressed routing.
        issue_labels: The Active issue's labels (only ``task-type:`` ones matter).
        warn: Sink for the non-fatal conflicting-label advisory.
        lifecycle_position: The issue's attempt position, independent of source.
        escalated_pair: The configured escalation rung when this is an escalated
            retry. A later lifecycle owner (#408) supplies it; this resolver only
            gates and records it.

    Returns:
        The :class:`RoutingResolution` for this attempt.

    Raises:
        TaskTypeError: A ``task-type:`` label carries a key outside the closed
            taxonomy. The message names the key and the permitted set.
    """
    default: tuple[str | None, str | None] = (
        run_config.model,
        run_config.reasoning_effort,
    )
    routing = run_config.routing

    raw_keys: list[str] = []
    for label in issue_labels:
        if label.startswith(TASK_TYPE_LABEL_PREFIX):
            key = label[len(TASK_TYPE_LABEL_PREFIX) :]
            try:
                validate_task_type_key(key)
            except TaskTypeError:
                raise TaskTypeError(
                    f"unsupported task-type label {label!r} with key {key!r}; "
                    f"permitted keys: "
                    f"{', '.join(TASK_TYPE_KEYS)}",
                    key=key,
                ) from None
            raw_keys.append(key)

    keys = tuple(dict.fromkeys(raw_keys))
    if escalated_pair is not None:
        pair = escalated_pair
        source = RoutingSource.ESCALATED
    elif run_config.routing_suppressed:
        pair = default
        source = RoutingSource.DEFAULTED_EXPLICIT_OVERRIDE
    elif not keys:
        pair = default
        source = RoutingSource.DEFAULTED_NO_TASK_TYPE_LABEL
    else:
        # An omitted key resolves to the global default as a *value*, so it takes
        # part in the comparison rather than short-circuiting it: `docs` routed
        # somewhere and an unconfigured `chore` disagree, and that ambiguity is
        # the one thing §14 asks for a warning about.
        resolved = [routing.get(key, default) for key in keys]
        if all(candidate == resolved[0] for candidate in resolved[1:]):
            pair = resolved[0]
            source = (
                RoutingSource.ROUTED
                if all(key in routing for key in keys)
                else RoutingSource.DEFAULTED_UNKNOWN_TASK_TYPE_KEY
            )
        else:
            labels = sorted(TASK_TYPE_LABEL_PREFIX + key for key in keys)
            warn(
                f"issue carries conflicting task-type labels {labels}; their "
                f"[routing] entries resolve to different (model, effort) pairs — "
                f"using the global default."
            )
            pair = default
            source = RoutingSource.DEFAULTED_CONFLICTING_TASK_TYPE_KEYS

    model, effort, context_tier, gate_warnings = _gate_pair(
        pair, run_config.context_tier
    )
    return RoutingResolution(
        model=model,
        reasoning_effort=effort,
        context_tier=context_tier,
        source=source,
        task_type_keys=tuple(raw_keys),
        gate_warnings=gate_warnings,
        lifecycle_position=lifecycle_position,
    )
