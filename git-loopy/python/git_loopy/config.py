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
* **No I/O at construction time.** ``pricing_file`` is a :class:`Path`
  reference — actually opening it is :func:`git_loopy.pricing.load_pricing`'s
  job and only happens inside :func:`git_loopy.loop.run`.
* **``otel_enabled`` is plumbed but inert in this slice.** Issue #12
  wires it; this slice just makes sure the flag survives the CLI →
  RunConfig → loop pipe so #12 doesn't have to re-touch the dataclass.
* **stdlib only.** Enforced by ``tests/test_config.py``'s import-guard
  test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Literal, Mapping

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
    "RECOMMENDED_ROUTING",
    "EffortGateWarning",
    "GatedEffort",
    "gate_reasoning_effort",
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

#: The kit's opinionated, recommended ``task-type`` -> ``(model, effort)`` core
#: (decision #110) — the "batteries-included" routing mapping the guided-setup
#: surfaces seed and render (``git-loopy init``'s opt-in routing step and
#: ``git-loopy config routing use-recommended`` / its guided walk). It is the
#: *recommended* core, **not** a closed set: the ``task-type:`` taxonomy stays
#: open and operator-extensible — any key with a ``[routing]`` entry routes, this
#: is just the shipped starting point.
#:
#: Keyed by the bare task-type key (the part *after* :data:`TASK_TYPE_LABEL_PREFIX`,
#: matching :attr:`RunConfig.routing` and the ``[routing]`` config table), in the
#: fixed **presentation order** the guided walk surfaces. A 6-type core:
#:
#: ==================  =================  ========
#: task-type key       Model              Effort
#: ==================  =================  ========
#: ``planning``        ``gpt-5.6-sol``      ``xhigh``
#: ``review``          ``claude-opus-4.8``  ``high``
#: ``implementation``  ``gpt-5.6-terra``    ``high``
#: ``test``            ``claude-sonnet-5``  ``medium``
#: ``docs``            ``gpt-5.6-terra``    ``low``
#: ``chore``           ``gpt-5.6-luna``     ``low``
#: ==================  =================  ========
#:
#: The **global default stays** ``claude-opus-4.8 @ max`` (today's built-in), so
#: an unlabelled issue is unaffected: it routes through the global default, not
#: the ``planning`` route, which now deliberately diverges from the default. Every
#: pair is valid against :data:`MODEL_REASONING_EFFORTS` (it survives
#: :func:`gate_reasoning_effort` unchanged) — pinned by ``tests/test_config.py``.
RECOMMENDED_ROUTING: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "planning": ("gpt-5.6-sol", "xhigh"),
        "review": ("claude-opus-4.8", "high"),
        "implementation": ("gpt-5.6-terra", "high"),
        "test": ("claude-sonnet-5", "medium"),
        "docs": ("gpt-5.6-terra", "low"),
        "chore": ("gpt-5.6-luna", "low"),
    }
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
        pricing_file: Optional explicit path to a ``pricing.toml``.
            ``None`` lets :func:`git_loopy.pricing.load_pricing` resolve
            from ``GIT_LOOPY_PRICING_FILE`` or the packaged default.
        parallel: Opt-in **Parallel mode** cap (ADR-0008). ``1`` (the
            default) is serial — :func:`git_loopy.loop.run` drives the
            existing single-worktree loop byte-for-byte unchanged. ``> 1``
            requests up to that many concurrent **Lanes** per **Wave**;
            :mod:`git_loopy.cli` resolves it from ``--parallel N`` /
            ``GIT_LOOPY_MAX_PARALLEL`` (defaulting to ``N=3`` when Parallel
            mode is requested without an explicit cap). Must be ≥ 1.
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
        skill_policy: Presence-aware project/global Config values, optional exact
            environment replacement, and temporary enable/disable overlays. These
            remain uncombined until the Effective Skill policy resolver consumes
            them at Run preflight.
    """

    model: str | None = None
    reasoning_effort: str | None = None
    issue_source: Literal["github", "prds"] = "github"
    include_prs: bool | None = None
    max_iterations: int = 0
    max_nmt_strikes: int = 3
    deny_tools: frozenset[str] = field(default_factory=frozenset)
    deny_skills: frozenset[str] = field(default_factory=frozenset)
    verbosity: int = 0
    render_reasoning: bool = True
    otel_enabled: bool = False
    pricing_file: Path | None = None
    parallel: int = 1
    send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS
    routing: Mapping[str, tuple[str, str | None]] = field(default_factory=dict)
    skill_policy: SkillPolicyInputs = field(default_factory=SkillPolicyInputs)

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
        if self.verbosity < 0 or self.verbosity > 3:
            raise ValueError(
                f"verbosity must be in 0..3, got {self.verbosity}"
            )
        if self.parallel < 1:
            raise ValueError(
                f"parallel must be ≥ 1 (1 = serial), got {self.parallel}"
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
        # Normalize `routing` to a read-only view over a *private* copy so the
        # frozen dataclass is genuinely immutable (no aliasing back to the
        # caller's dict, no post-construction mutation) and stays safe to reuse
        # across every Iteration. Built once by the resolver (issue #146).
        object.__setattr__(self, "routing", MappingProxyType(dict(self.routing)))


def _ignore_routing_warning(_message: str) -> None:
    """Default no-op ``warn`` sink so :func:`resolve_iteration_model` stays pure."""


def _gate_pair(
    pair: tuple[str | None, str | None],
) -> tuple[str | None, str | None]:
    """Pass a ``(model, effort)`` source pair through the shared effort gate.

    A ``None`` model means "let the SDK pick its default"; it has nothing to gate,
    so the effort passes through untouched. A concrete model id is gated against
    :data:`MODEL_REASONING_EFFORTS` via :func:`gate_reasoning_effort`.
    """
    model, effort = pair
    if model is None:
        return None, effort
    gated = gate_reasoning_effort(model, effort)
    return gated.model, gated.effort


def resolve_iteration_model(
    run_config: RunConfig,
    issue_labels: Iterable[str],
    *,
    warn: Callable[[str], None] = _ignore_routing_warning,
) -> tuple[str | None, str | None]:
    """Resolve the gated ``(model, effort)`` pair an Iteration runs on (issue #147).

    The single load-bearing seam per-issue routing hangs off. A call site invokes
    it at **Active-issue pickup**: it filters the issue's ``task-type:<key>`` labels
    (:data:`TASK_TYPE_LABEL_PREFIX` — the runner *reads* the label, it never infers
    the type), selects a source pair per the locked table below, passes that pair
    through the shared effort gate (:func:`gate_reasoning_effort`, #145), and returns
    it. The ``[routing]`` table on :attr:`run_config.routing <RunConfig.routing>` is
    the source of truth for valid keys; the global default is the run config's
    top-level ``(model, effort)``.

    Source-pair selection (decision #109):

    ======================================  ===============  =========================
    ``task-type:`` labels on the issue      Source pair      Warn?
    ======================================  ===============  =========================
    none                                    global default   no (silent — normal path)
    one known key                           that entry       no
    one unknown key                         global default   yes — unknown key
    >=2 keys, differing resolved values     global default   yes — conflict (labels)
    >=2 keys, all resolving to same value   that pair         no
    ======================================  ===============  =========================

    **Suppression / back-compat.** When :attr:`run_config.routing <RunConfig.routing>`
    is empty — no ``[routing]`` block, or an explicit ``--model`` /
    ``--reasoning-effort`` override suppressing routing run-wide — every issue resolves
    to the single gated global/explicit pair, with **no** label inspection and no
    warning (so a labelled issue never raises a spurious "unknown key").

    **No I/O.** This function is pure and exhaustively unit-testable; warnings surface
    through the injected ``warn`` callback (default no-op), which a call site wires to
    its per-issue warning channel.

    Args:
        run_config: The frozen run configuration carrying the routing map and the
            global-default ``(model, reasoning_effort)``.
        issue_labels: The Active issue's labels (only ``task-type:`` ones matter).
        warn: Sink for the non-fatal unknown-key / conflict advisories.

    Returns:
        The gated ``(model, effort)`` pair the Iteration should run on. ``model`` is
        ``None`` only when the global default itself defers model choice to the SDK.
    """
    default: tuple[str | None, str | None] = (
        run_config.model,
        run_config.reasoning_effort,
    )
    routing = run_config.routing

    # Suppression / back-compat: routing off run-wide -> gated global default, and
    # we never inspect labels, so a labelled issue raises no spurious warning.
    if not routing:
        return _gate_pair(default)

    keys: list[str] = []
    for label in issue_labels:
        if label.startswith(TASK_TYPE_LABEL_PREFIX):
            key = label[len(TASK_TYPE_LABEL_PREFIX) :]
            if key not in keys:
                keys.append(key)

    source: tuple[str | None, str | None]
    if not keys:
        source = default
    elif len(keys) == 1:
        key = keys[0]
        if key in routing:
            source = routing[key]
        else:
            warn(
                f"issue task-type label "
                f"{TASK_TYPE_LABEL_PREFIX + key!r} has no [routing] entry; "
                f"using the global default (model, effort)."
            )
            source = default
    else:
        resolved = [routing.get(key, default) for key in keys]
        if all(pair == resolved[0] for pair in resolved[1:]):
            source = resolved[0]
        else:
            labels = sorted(TASK_TYPE_LABEL_PREFIX + key for key in keys)
            warn(
                f"issue carries conflicting task-type labels {labels}; their "
                f"[routing] entries resolve to different (model, effort) pairs — "
                f"using the global default."
            )
            source = default

    return _gate_pair(source)
