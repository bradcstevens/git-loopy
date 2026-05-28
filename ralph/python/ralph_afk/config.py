"""``ralph_afk.config`` — frozen per-invocation configuration.

The :class:`RunConfig` dataclass is the single load-bearing config seam
between :mod:`ralph_afk.cli` (which composes it from CLI flags + env
vars + defaults) and :mod:`ralph_afk.loop` (which consumes it).

It also satisfies — structurally, via Python's :pep:`544` Protocol
machinery — the :class:`ralph_afk.session.SessionConfig` Protocol, so
the loop can pass the same object to :class:`~ralph_afk.session.IterationSession`
without an explicit conversion. The Protocol-conformance contract is:

- ``deny_tools: frozenset[str]``
- ``deny_skills: frozenset[str]``
- ``verbosity: int``
- ``render_reasoning: bool``

Design notes:

* **Frozen.** The loop reuses the same config across every iteration;
  freezing makes accidental mid-run mutation impossible.
* **No I/O at construction time.** ``pricing_file`` is a :class:`Path`
  reference — actually opening it is :func:`ralph_afk.pricing.load_pricing`'s
  job and only happens inside :func:`ralph_afk.loop.run`.
* **``otel_enabled`` is plumbed but inert in this slice.** Issue #12
  wires it; this slice just makes sure the flag survives the CLI →
  RunConfig → loop pipe so #12 doesn't have to re-touch the dataclass.
* **stdlib only.** Enforced by ``tests/test_config.py``'s import-guard
  test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

__all__ = ["RunConfig", "REASONING_EFFORTS"]

#: Reasoning-effort values the Copilot SDK's ``ReasoningEffort`` literal
#: accepts. Kept as a module-level constant so :mod:`ralph_afk.cli`'s
#: auto-derivation helper and ``__post_init__`` validation share one
#: source of truth. The list mirrors
#: ``copilot.session.ReasoningEffort`` (a stdlib ``Literal``); a future
#: SDK version that adds an effort will need this set updated in lockstep.
REASONING_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh"})


@dataclass(frozen=True)
class RunConfig:
    """Frozen per-invocation configuration for the ``ralph-afk`` runner.

    Attributes:
        model: Optional Copilot model id override. ``None`` lets the SDK
            pick its default (which respects ``~/.copilot`` config).
        reasoning_effort: Optional reasoning-effort override forwarded to
            ``copilot.CopilotClient.create_session``. Must be one of
            ``"low"`` / ``"medium"`` / ``"high"`` / ``"xhigh"`` or
            ``None`` (let the SDK / service pick). Some model variants
            pin reasoning effort to a single value — e.g.
            ``claude-opus-4.7-xhigh`` only accepts ``"xhigh"`` — and the
            service rejects mismatched defaults with a CAPI 400. The
            CLI auto-derives a safe default from the model id suffix so
            the kit's default model works out-of-the-box; the
            ``REASONING_EFFORT`` env var overrides the auto-derived value.
        issue_source: ``"github"`` (default) for the GitHub-issue-backed
            collector or ``"prds"`` for the legacy local-markdown layout.
            This slice (#10) only implements ``"github"``; ``"prds"``
            lands in #11 and the loop raises :class:`NotImplementedError`
            for it.
        max_iterations: Cap on iterations. ``0`` (the default) means
            unlimited — mirrors the bash positional arg semantics at
            ``ralph/afk.sh:307-310``.
        max_nmt_strikes: Consecutive no-progress iterations tolerated
            before the loop aborts non-zero. Must be ≥ 1.
        deny_tools: Tool names to reject at the SDK permission gate.
        deny_skills: Skill names (the ``arguments.skill`` value passed
            to the ``skill`` meta-tool) to reject.
        verbosity: 0 (default) / 1 (``-v``) / 2 (``-vv``) / 3 (``-vvv``).
        render_reasoning: ``False`` suppresses assistant reasoning output
            regardless of verbosity. Default ``True``.
        otel_enabled: ``True`` when OpenTelemetry tracing is enabled
            (either ``RALPH_OTEL_ENABLED=1`` or
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set). The OTel wiring
            itself lands in issue #12; this slice just plumbs the flag.
        pricing_file: Optional explicit path to a ``pricing.toml``.
            ``None`` lets :func:`ralph_afk.pricing.load_pricing` resolve
            from ``RALPH_PRICING_FILE`` or the packaged default.
    """

    model: str | None = None
    reasoning_effort: str | None = None
    issue_source: Literal["github", "prds"] = "github"
    max_iterations: int = 0
    max_nmt_strikes: int = 3
    deny_tools: frozenset[str] = field(default_factory=frozenset)
    deny_skills: frozenset[str] = field(default_factory=frozenset)
    verbosity: int = 0
    render_reasoning: bool = True
    otel_enabled: bool = False
    pricing_file: Path | None = None

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
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in REASONING_EFFORTS
        ):
            raise ValueError(
                f"reasoning_effort must be one of "
                f"{sorted(REASONING_EFFORTS)} or None, got "
                f"{self.reasoning_effort!r}"
            )
