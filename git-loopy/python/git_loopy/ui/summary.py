"""``git_loopy.ui.summary`` — per-iteration counter accumulator + frozen UI artefacts.

This module owns the data side of the UI: it accumulates per-iteration
counter snapshots from the event stream and constructs the two frozen
artefacts the renderer prints:

* The **iteration ``Panel``** at iteration end (rendered once, never
  re-drawn — preserved verbatim in scrollback).
* The **run-end ``Table``** at run end (one row per completed iteration
  plus a totals footer).

The renderer drives this module via :meth:`RunSummary.on_iteration_start`,
:meth:`RunSummary.on_iteration_end`, and the per-event accumulator methods.
At Iteration end, the Orchestrator's normalized rollup replaces the live
best-effort counters before the snapshot is frozen. Persistence consumes that
same rollup directly, so neither replay nor the UI is an accounting authority.

Design notes:

* **Two-dataclass posture.** :class:`IterationSnapshot` (UI) and
  :class:`git_loopy.persist.IterationCounters` (persist) intentionally do NOT
  share a base. Both project the normalized rollup into their own concerns;
  the compatibility :meth:`IterationSnapshot.to_counters_kwargs` helper is not
  used as the production persistence authority.
* **First non-None model wins — via the shared UsageTally.** Some SDK
  versions emit ``usage.tokens`` events with ``model=None``; the tally
  retains the first authoritative model name and ignores subsequent
  ``None``s. A later non-``None`` model ALSO does not overwrite — keeps the
  iteration's recorded model stable even if the SDK changes models
  mid-iteration (which would be unusual but not crashy). That rule (and the
  unknown-model cost guard) is now the :class:`~git_loopy.usage.UsageTally`'s
  single implementation, shared with the Queue's per-issue sink — no second
  copy lives here.
* **Strikes are cumulative-aware.** A ``WRAPPER_STRIKE`` event carrying
  a ``strikes`` integer is used verbatim (the value is the wrapper's
  authoritative count after the iteration). Absent that key, each
  STRIKE event increments the counter — a marker form for diagnostic
  use.
* **context_used = tokens_in + tokens_out.** Read straight off the tally
  (:attr:`~git_loopy.usage.UsageTally.total_tokens`); matches the schema
  example in :mod:`git_loopy.persist`. Labelled as "observed tokens" in the
  rendered panel so the operator doesn't read it as live model
  context-window pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Iterator, Mapping, Optional

from rich.box import ROUNDED, SIMPLE
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from git_loopy.denomination import BilledCreditsDenomination, CostDenomination
from git_loopy.usage import BillingSample, UsageTally

from .console import STYLES


__all__ = ["IterationSnapshot", "RunSummary", "RunTotals"]


# Highlight threshold: matches the PRD's "Smart Zone Ceiling" cue. When
# context utilisation reaches half the model's window we start drawing
# attention to the cost / context line.
_CONTEXT_HIGH_WATERMARK: float = 0.5
_SKILL_PATH_PREFIX = ".copilot/skills/"
_SKILL_PATH_SUFFIX = "/SKILL.md"


def _argument_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _argument_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _argument_strings(item)


def _consulted_skills(tool_name: str, arguments: Any) -> set[str]:
    names: set[str] = set()
    if tool_name == "skill" and isinstance(arguments, dict):
        skill = arguments.get("skill")
        if isinstance(skill, str) and skill:
            names.add(skill)
    for value in _argument_strings(arguments):
        normalized = value.replace("\\", "/")
        search_from = 0
        while (start := normalized.find(_SKILL_PATH_PREFIX, search_from)) >= 0:
            name_start = start + len(_SKILL_PATH_PREFIX)
            name_end = normalized.find(_SKILL_PATH_SUFFIX, name_start)
            if name_end < 0:
                break
            name = normalized[name_start:name_end]
            if (
                name
                and name[0].isalnum()
                and all(char.isalnum() or char in "._-" for char in name)
            ):
                names.add(name)
            search_from = name_end + len(_SKILL_PATH_SUFFIX)
    return names


def _rollup_int(value: Any) -> int:
    """Coerce a normalized-rollup counter, treating unavailable as neutral.

    Unavailability is recorded separately in
    :attr:`IterationSnapshot.unavailable_measurements`; this only keeps the
    legacy integer counters usable for renderers that predate that flag.
    """
    return int(value) if isinstance(value, (int, float)) and not isinstance(
        value, bool
    ) else 0


def _rollup_float(value: Any) -> float:
    """Coerce a normalized-rollup duration, treating unavailable as ``0.0``."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(
        value, bool
    ) else 0.0


def _observed_text(observed: bool, rendered: str) -> str:
    """Show ``rendered`` only when its producer observed the measurement.

    A measurement the Orchestrator declared unavailable renders as the unknown
    em dash; the Wrapper contract forbids re-reporting it as an observed value.
    """
    return rendered if observed else "—"


# ---------------------------------------------------------------------------
# IterationSnapshot
# ---------------------------------------------------------------------------


@dataclass
class IterationSnapshot:
    """Per-iteration counter accumulator. Mutable while the iteration is
    in progress; frozen (by convention) once :meth:`RunSummary.on_iteration_end`
    appends it to :attr:`RunSummary.completed`.

    Fields parallel the persist schema where they overlap but include
    extra UI-only fields (``issue_num``, timestamps) that don't belong
    in the persisted JSON. The per-iteration **Consumption** (tokens + the
    model they were billed against) lives in a shared
    :class:`~git_loopy.usage.UsageTally`; ``model`` / ``tokens_in`` /
    ``tokens_out`` remain as thin read-only accessors onto it so existing
    render call sites and the persist seam read unchanged.
    """

    iter_num: int
    issue_num: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    usage: UsageTally = field(default_factory=UsageTally)
    tool_count: int = 0
    skill_count: int = 0
    skills_consulted: set[str] = field(default_factory=set)
    commits: int = 0
    auto_closures: int = 0
    pr_advances: int = 0
    strikes: int = 0
    outcome: Optional[str] = None
    peak_context_window: Optional[dict[str, int | None]] = None
    issues: tuple[dict[str, Any], ...] = ()
    normalized_duration_seconds: Optional[float] = None
    normalized_observed_tokens: Optional[int] = None
    normalized_cost_usd: Optional[Decimal] = None
    has_normalized_rollup: bool = False
    #: Normalized-rollup measurement keys the producing Orchestrator declared
    #: unavailable (sent as ``null``). Renderers project these as the unknown
    #: em dash; an observed none stays ``0`` / ``[]`` and never appears here.
    unavailable_measurements: frozenset[str] = frozenset()

    def measurement_observed(self, key: str) -> bool:
        """Whether this Iteration's producer actually observed ``key``.

        The run-level twin is :meth:`RunSummary._measurement_observed`.
        """
        return key not in self.unavailable_measurements

    @property
    def model(self) -> Optional[str]:
        """The model this iteration's **Consumption** was billed against."""
        return self.usage.model

    @property
    def tokens_in(self) -> int:
        """Input tokens observed this iteration."""
        return self.usage.tokens_in

    @property
    def tokens_out(self) -> int:
        """Output tokens observed this iteration."""
        return self.usage.tokens_out

    @property
    def context_used(self) -> int:
        """Observed-tokens proxy for context occupancy.

        Sum of input + output tokens within this iteration (delegated to
        :attr:`~git_loopy.usage.UsageTally.total_tokens`). Labelled
        "observed tokens" in the rendered panel; not a true live
        model-context measurement (multiple turns within a session would
        double-count input tokens, which already include prior history).
        """
        if self.normalized_observed_tokens is not None:
            return self.normalized_observed_tokens
        return self.usage.total_tokens

    @property
    def duration_seconds(self) -> float:
        """Wall-clock duration in seconds, or ``0.0`` if not yet closed."""
        if self.normalized_duration_seconds is not None:
            return self.normalized_duration_seconds
        if self.started_at is None or self.ended_at is None:
            return 0.0
        return (self.ended_at - self.started_at).total_seconds()

    def cost_usd(self, denomination: CostDenomination) -> Optional[Decimal]:
        """The iteration's Cost, or ``None`` when the denomination cannot say.

        Delegates to :meth:`~git_loopy.denomination.CostDenomination.cost`,
        which owns the unknown-model guard so callers render the em dash rather
        than silently understating Cost.
        """
        if self.has_normalized_rollup:
            return self.normalized_cost_usd
        return denomination.cost(self.usage)

    def credits(self, denomination: CostDenomination) -> Optional[Decimal]:
        """The Iteration's billed **AI Credits**, or ``None`` when unknown.

        Resolves through the same seam the estimate does, so the **Summary**, the
        **Queue** and the per-issue **Iteration breakdown** cannot disagree about
        what an Iteration cost. A normalized rollup rebuilt this tally from the
        producing Orchestrator's own figures, so the seam answers for a native
        stream on the same terms.
        """
        return denomination.cost(self.usage)

    @property
    def premium_requests(self) -> Optional[Decimal]:
        """Premium requests billed this Iteration, or ``None`` when unknown.

        A count rather than a Cost, so it is read off **Consumption** directly
        rather than through the denomination seam — nothing denominates it.
        """
        return self.usage.premium_requests

    def to_counters_kwargs(self, *, denomination: CostDenomination) -> dict:
        """Return a kwargs dict suitable for constructing
        :class:`git_loopy.persist.IterationCounters`.

        Returning a dict (rather than an :class:`IterationCounters` instance
        directly) keeps this UI module's import graph free of
        ``git_loopy.persist``. Compatibility callers may do::

            from git_loopy.persist import IterationCounters
            counters = IterationCounters(
                **snap.to_counters_kwargs(denomination=d)
            )

        Production persistence instead uses
        :meth:`git_loopy.persist.IterationCounters.from_rollup`.
        """
        return {
            "iter": self.iter_num,
            "duration_seconds": self.duration_seconds,
            "model": self.usage.model,
            "tokens_in": self.usage.tokens_in,
            "tokens_out": self.usage.tokens_out,
            "context_used": self.context_used,
            "est_cost_usd": self.cost_usd(denomination),
            "tool_count": self.tool_count,
            "skill_count": self.skill_count,
            "skills_consulted": tuple(sorted(self.skills_consulted)),
            "commits": self.commits,
            "auto_closures": self.auto_closures,
            "strikes": self.strikes,
        }


# ---------------------------------------------------------------------------
# RunTotals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunTotals:
    """Totals view computed from :attr:`RunSummary.completed`.

    Used by the run-end Table footer and exposed publicly so the loop
    slice (#10) can read the same numbers without duplicating the
    accumulation logic.
    """

    iterations: int
    tokens_in: int
    tokens_out: int
    #: ``None`` only when every completed Iteration declared the measurement
    #: unavailable; otherwise the sum over the Iterations that observed it.
    observed_tokens: Optional[int]
    #: ``False`` only when every completed Iteration declared the matching
    #: measurement unavailable. Unavailability is a producer *declaration*, so a
    #: Run with no completed Iterations has observed none rather than unknown.
    tokens_in_observed: bool
    tokens_out_observed: bool
    skills_observed: bool
    cost_usd: Optional[Decimal]
    #: Billed **AI Credits** and premium requests summed over the Iterations that
    #: reported them, or ``None`` when none did. Folded on the same rule as
    #: ``cost_usd``: an Iteration that could not report contributes nothing
    #: rather than a zero, so the footer never reads as free work.
    credits: Optional[Decimal]
    premium_requests: Optional[Decimal]
    commits: int
    auto_closures: int
    pr_advances: int
    final_strikes: int
    iterations_with_skill: int
    skills_seen: tuple[str, ...]


# ---------------------------------------------------------------------------
# RunSummary
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    """Aggregate per-iteration snapshots; builds the frozen UI artefacts.

    Owned by the caller (typically the loop slice) and passed to the
    :class:`~git_loopy.ui.renderer.Renderer`. The renderer subscribes to
    events and drives the accumulator methods below. The completed snapshots
    are renderer projections of the normalized Iteration-end records.

    Attributes:
        denomination: The injected
            :class:`~git_loopy.denomination.CostDenomination` every Cost figure
            on this Summary resolves through — the one seam, so the Summary and
            the Queue cannot disagree about what an issue cost. Its
            :attr:`~git_loopy.denomination.CostDenomination.provenance` supplies
            the caveat printed beside a per-Iteration cost line.
        credits_denomination: The seam the primary **AI Credits** figure resolves
            through (ADR-0026). Transitional: while the list-price estimate is
            still rendered (#330 retires it) the Summary carries both
            denominations, and when the estimate goes this is the one that
            remains.
        current: The in-progress :class:`IterationSnapshot`, or ``None``
            between iterations.
        completed: Frozen snapshots in iteration order.
    """

    denomination: CostDenomination
    credits_denomination: CostDenomination = field(
        default_factory=BilledCreditsDenomination
    )
    current: Optional[IterationSnapshot] = None
    completed: list[IterationSnapshot] = field(default_factory=list)

    # -- iteration lifecycle ------------------------------------------------

    def on_iteration_start(
        self, *, iter_num: int, issue_num: Optional[int] = None
    ) -> IterationSnapshot:
        """Open a new snapshot at the start of an iteration."""
        snap = IterationSnapshot(
            iter_num=iter_num,
            issue_num=issue_num,
            started_at=datetime.now(timezone.utc),
        )
        self.current = snap
        return snap

    def on_iteration_end(
        self, rollup: Optional[Mapping[str, Any]] = None
    ) -> Optional[IterationSnapshot]:
        """Close the current snapshot, append to :attr:`completed`, return it.

        Returns ``None`` if no iteration is currently open (a stray
        ``WRAPPER_ITERATION_END`` — e.g. the abort path — must not crash).
        """
        snap = self.current
        if snap is None:
            return None
        if rollup is not None:
            self._apply_rollup(snap, rollup)
        snap.ended_at = datetime.now(timezone.utc)
        self.completed.append(snap)
        self.current = None
        return snap

    @staticmethod
    def _apply_rollup(
        snap: IterationSnapshot,
        rollup: Mapping[str, Any],
    ) -> None:
        summary = rollup.get("summary")
        issues = rollup.get("issues")
        if not isinstance(summary, Mapping) or not isinstance(issues, list):
            return
        # An Orchestrator that cannot observe a measurement sends it as `null`.
        # The Wrapper contract forbids re-reporting that as `0` or `[]`, so
        # every null key is recorded as unavailable and the legacy counter
        # falls back to its neutral default for existing renderers.
        unavailable = frozenset(
            key
            for key in (
                "model",
                "tokens_in",
                "tokens_out",
                "observed_tokens",
                "cost_usd",
                "tool_count",
                "skill_call_count",
                "skills_consulted",
                "peak_context_window",
            )
            if summary.get(key, 0) is None
        )
        snap.unavailable_measurements = unavailable
        model = summary.get("model")
        snap.usage = UsageTally(
            model=model if isinstance(model, str) else None,
            tokens_in=_rollup_int(summary.get("tokens_in")),
            tokens_out=_rollup_int(summary.get("tokens_out")),
        )
        # A native Orchestrator's own billed figures (#329). Folded through the
        # same accrual the live path uses, so a normalized stream and a live one
        # answer the denomination seam identically; a producer that reported none
        # latches them to unknown and renders the em dash.
        snap.usage.add(None, 0, 0, BillingSample.from_event(summary))
        snap.normalized_duration_seconds = _rollup_float(
            rollup.get("duration_seconds")
        )
        observed_tokens = summary.get("observed_tokens")
        snap.normalized_observed_tokens = (
            None if observed_tokens is None else _rollup_int(observed_tokens)
        )
        cost = summary.get("cost_usd")
        snap.normalized_cost_usd = (
            Decimal(str(cost)) if cost is not None else None
        )
        snap.has_normalized_rollup = True
        snap.tool_count = _rollup_int(summary.get("tool_count"))
        snap.skill_count = _rollup_int(summary.get("skill_call_count"))
        snap.skills_consulted = {
            str(skill) for skill in (summary.get("skills_consulted") or ())
        }
        snap.commits = _rollup_int(summary.get("commits"))
        snap.auto_closures = _rollup_int(summary.get("auto_closures"))
        snap.pr_advances = _rollup_int(summary.get("pr_advances"))
        snap.strikes = _rollup_int(summary.get("strikes"))
        snap.outcome = str(rollup.get("outcome", "no_progress"))
        peak = summary.get("peak_context_window")
        snap.peak_context_window = (
            dict(peak) if isinstance(peak, Mapping) else None
        )
        snap.issues = tuple(dict(issue) for issue in issues)
        if snap.issue_num is None and len(snap.issues) == 1:
            issue = snap.issues[0].get("issue")
            if isinstance(issue, int):
                snap.issue_num = issue

    # -- per-event accumulators --------------------------------------------

    def record_usage(
        self,
        *,
        model: Optional[str],
        tokens_in: int,
        tokens_out: int,
        billing: Optional[BillingSample] = None,
    ) -> None:
        snap = self.current
        if snap is None:
            return
        # Fold this usage sample into the iteration's shared UsageTally. The
        # accrual rule (first non-None model wins; tokens sum) lives entirely in
        # UsageTally.add; the sink keeps its own int(x or 0) input sanitization.
        # ``billing`` absent latches the billed totals to unknown, so a sink that
        # sees no billing telemetry renders the em dash rather than a zero.
        snap.usage.add(model, int(tokens_in or 0), int(tokens_out or 0), billing)

    def record_tool_call(self, *, tool_name: str, arguments: Any = None) -> None:
        snap = self.current
        if snap is None:
            return
        snap.tool_count += 1
        if tool_name == "skill":
            snap.skill_count += 1
        snap.skills_consulted.update(_consulted_skills(tool_name, arguments))

    def record_commit(self) -> None:
        snap = self.current
        if snap is None:
            return
        snap.commits += 1

    def record_auto_close(self) -> None:
        snap = self.current
        if snap is None:
            return
        snap.auto_closures += 1

    def record_strike(self, *, strikes: Optional[int] = None) -> None:
        snap = self.current
        if snap is None:
            return
        if strikes is not None:
            snap.strikes = int(strikes)
        else:
            snap.strikes += 1

    # -- rollup -------------------------------------------------------------

    def _measurement_observed(self, key: str) -> bool:
        """Did any completed Iteration observe ``key``?

        Unavailability is a producer *declaration*: an Orchestrator marks a
        measurement unavailable by sending it as ``null``. A Run with no
        completed Iterations has therefore declared nothing, so its cumulative
        aggregate is an observed none rather than an unknown.
        """
        return not self.completed or any(
            key not in snap.unavailable_measurements for snap in self.completed
        )

    def totals(self) -> RunTotals:
        """Aggregate counters across :attr:`completed` iterations.

        Cost only sums iterations the denomination could price —
        unpriceable iterations contribute ``None`` and are skipped, so
        the totals row never silently understates cost by treating
        unknown as zero.

        ``final_strikes`` is the last completed iteration's strike count
        (not the sum) — strikes reset on progress in the wrapper
        contract, so summing would mislead.
        """
        tokens_in = sum(s.tokens_in for s in self.completed)
        tokens_out = sum(s.tokens_out for s in self.completed)
        observed_snapshots = [
            s
            for s in self.completed
            if "observed_tokens" not in s.unavailable_measurements
        ]
        observed_tokens = (
            sum(s.context_used for s in observed_snapshots)
            if observed_snapshots or not self.completed
            else None
        )
        tokens_in_observed = self._measurement_observed("tokens_in")
        tokens_out_observed = self._measurement_observed("tokens_out")
        skills_observed = self._measurement_observed("skills_consulted")
        commits = sum(s.commits for s in self.completed)
        auto_closures = sum(s.auto_closures for s in self.completed)
        pr_advances = sum(s.pr_advances for s in self.completed)
        priced_costs = [
            s.cost_usd(self.denomination)
            for s in self.completed
        ]
        defined_costs = [c for c in priced_costs if c is not None]
        cost_usd: Optional[Decimal]
        if defined_costs:
            cost_usd = sum(defined_costs, Decimal(0))
        else:
            cost_usd = None
        credits = _sum_or_unknown(
            s.credits(self.credits_denomination) for s in self.completed
        )
        premium_requests = _sum_or_unknown(
            s.premium_requests for s in self.completed
        )
        final_strikes = self.completed[-1].strikes if self.completed else 0
        iterations_with_skill = sum(
            1
            for snap in self.completed
            if snap.skill_count > 0 or snap.skills_consulted
        )
        skills_seen = tuple(
            sorted(
                {
                    skill
                    for snap in self.completed
                    for skill in snap.skills_consulted
                }
            )
        )
        return RunTotals(
            iterations=len(self.completed),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            observed_tokens=observed_tokens,
            tokens_in_observed=tokens_in_observed,
            tokens_out_observed=tokens_out_observed,
            skills_observed=skills_observed,
            cost_usd=cost_usd,
            credits=credits,
            premium_requests=premium_requests,
            commits=commits,
            auto_closures=auto_closures,
            pr_advances=pr_advances,
            final_strikes=final_strikes,
            iterations_with_skill=iterations_with_skill,
            skills_seen=skills_seen,
        )

    # -- frozen UI artefacts -----------------------------------------------

    def build_iteration_panel(self, snap: IterationSnapshot) -> Panel:
        """Construct the per-iteration frozen Panel.

        Every counter listed in the PRD is rendered. Cost is the only
        field that can render as ``—`` (em dash) — when the model is not
        in the pricing table. Context utilisation is highlighted when it
        crosses the documented half-window threshold.
        """
        body = Text()

        # Header: iter# + issue#
        body.append("Iteration ", style=STYLES["meta"])
        body.append(str(snap.iter_num), style=STYLES["panel_title"])
        if snap.issue_num is not None:
            body.append(f"  •  Issue #{snap.issue_num}", style=STYLES["meta"])
        body.append("\n")

        # Duration + model line
        body.append("Duration: ", style=STYLES["meta"])
        body.append(f"{snap.duration_seconds:.2f}s")
        body.append("    Model: ", style=STYLES["meta"])
        body.append(snap.model if snap.model is not None else "—")
        body.append("\n")

        # Consumption and Observed tokens are cumulative accounting. Keep the
        # distinct peak Context-fill gauge on its own line.
        body.append("Tokens: ", style=STYLES["meta"])
        body.append(
            "in="
            + _observed_text(
                snap.measurement_observed("tokens_in"), f"{snap.tokens_in:,}"
            )
            + "  out="
            + _observed_text(
                snap.measurement_observed("tokens_out"), f"{snap.tokens_out:,}"
            )
        )
        body.append("    Observed tokens: ", style=STYLES["meta"])
        body.append(
            _observed_text(
                snap.measurement_observed("observed_tokens"), f"{snap.context_used:,}"
            )
        )
        body.append("\n")
        body.append("Peak Context fill: ", style=STYLES["meta"])
        body.append_text(self._format_peak_context_line(snap))
        body.append("\n")

        # Cost lines — the billed figure first, the estimate it will replace after.
        body.append("Cost: ", style=STYLES["meta"])
        body.append_text(self._format_credits_line(snap))
        body.append("\n")
        body.append("Est cost: ", style=STYLES["meta"])
        cost_text = self._format_cost_line(snap)
        body.append_text(cost_text)
        body.append("\n")

        # Tool / explicit skill-call counts
        body.append("Tools: ", style=STYLES["meta"])
        body.append(
            _observed_text(snap.measurement_observed("tool_count"), str(snap.tool_count))
        )
        body.append("    Skill calls: ", style=STYLES["meta"])
        body.append(
            _observed_text(
                snap.measurement_observed("skill_call_count"), str(snap.skill_count)
            )
        )
        body.append("\n")
        body.append("Skills consulted: ", style=STYLES["meta"])
        body.append(
            _observed_text(
                snap.measurement_observed("skills_consulted"),
                ", ".join(sorted(snap.skills_consulted)) or "—",
            )
        )
        body.append("\n")

        # Commits / auto-closures / strikes
        body.append("Commits: ", style=STYLES["meta"])
        body.append(str(snap.commits))
        body.append("    Auto-closures: ", style=STYLES["meta"])
        body.append(str(snap.auto_closures))
        body.append("    PR advances: ", style=STYLES["meta"])
        body.append(str(snap.pr_advances))
        body.append("    Strikes: ", style=STYLES["meta"])
        body.append(str(snap.strikes))

        return Panel(
            body,
            title=f"[bold]Iteration {snap.iter_num} done[/bold]",
            border_style=STYLES["panel_rule"],
            box=ROUNDED,
            padding=(0, 1),
        )

    def build_run_table(self) -> Table:
        """Construct the frozen run-end Table.

        One row per completed iteration, plus a totals footer that
        surfaces summed tokens / cost / commits / auto-closures and the
        ``final_strikes`` value from the last iteration. The caption keeps
        run-level skill adoption readable without widening the table.
        """
        totals = self.totals()
        table = Table(
            title="[bold]Run summary[/bold]",
            box=SIMPLE,
            header_style=STYLES["table_header"],
            show_footer=len(self.completed) > 0,
            caption=(
                f"Skill adoption: {totals.iterations_with_skill}/{totals.iterations}"
                f" iterations • Skills: {', '.join(totals.skills_seen) or '—'}"
            ),
            caption_justify="left",
        )
        table.add_column("Iter", justify="right", footer="totals")
        table.add_column("Issue", justify="right", footer="")
        table.add_column("Model", justify="left", footer="")
        table.add_column("Duration", justify="right", footer="")
        table.add_column(
            "Tokens in",
            justify="right",
            footer=_observed_text(totals.tokens_in_observed, f"{totals.tokens_in:,}"),
        )
        table.add_column(
            "Tokens out",
            justify="right",
            footer=_observed_text(totals.tokens_out_observed, f"{totals.tokens_out:,}"),
        )
        table.add_column(
            "Credits",
            justify="right",
            footer=_format_credits(totals.credits),
        )
        table.add_column(
            "Premium",
            justify="right",
            footer=_format_premium(totals.premium_requests),
        )
        table.add_column(
            "Cost USD",
            justify="right",
            footer=_format_decimal_footer(totals.cost_usd),
        )
        table.add_column(
            "Commits",
            justify="right",
            footer=str(totals.commits),
        )
        table.add_column(
            "Closures",
            justify="right",
            footer=str(totals.auto_closures),
        )
        table.add_column(
            "PR advances",
            justify="right",
            footer=str(totals.pr_advances),
        )
        table.add_column(
            "Final strikes",
            justify="right",
            footer=str(totals.final_strikes),
        )

        for snap in self.completed:
            cost = snap.cost_usd(self.denomination)
            cost_str = f"${cost:.4f}" if cost is not None else "—"
            issue_str = f"#{snap.issue_num}" if snap.issue_num is not None else "—"
            model_str = snap.model if snap.model is not None else "—"
            table.add_row(
                str(snap.iter_num),
                issue_str,
                model_str,
                f"{snap.duration_seconds:.1f}s",
                _observed_text(
                    snap.measurement_observed("tokens_in"), f"{snap.tokens_in:,}"
                ),
                _observed_text(
                    snap.measurement_observed("tokens_out"), f"{snap.tokens_out:,}"
                ),
                _format_credits(snap.credits(self.credits_denomination)),
                _format_premium(snap.premium_requests),
                cost_str,
                str(snap.commits),
                str(snap.auto_closures),
                str(snap.pr_advances),
                str(snap.strikes),
            )
        return table

    def build_rollup_band(self) -> Text:
        """Compose the compact **Summary** rollup band for the Dashboard (ADR-0003).

        A single-line, *live* (not frozen) run-level totals strip — the band of
        the Dashboard stacked under the Queue. It mirrors the run-end
        :meth:`build_run_table` footer: summed tokens, estimated cost, commits,
        closures, and the final strike count, plus the iteration count for
        context. Returned as a Rich :class:`~rich.text.Text` the interactive app
        drops into a ``Static``; the full per-iteration table stays the run-end
        artefact. Cost renders as the em dash when no completed iteration had a
        priced model (the same unknown-model treatment as the table footer).
        """
        totals = self.totals()
        text = Text()
        text.append("Summary", style=STYLES["meta"])
        text.append(f"  •  iters {totals.iterations}")
        observed = (
            f"{totals.observed_tokens:,}"
            if totals.observed_tokens is not None
            else "—"
        )
        tokens_in = f"{totals.tokens_in:,}" if totals.tokens_in_observed else "—"
        tokens_out = f"{totals.tokens_out:,}" if totals.tokens_out_observed else "—"
        text.append(
            f"  •  Observed tokens {observed}"
            f" (in={tokens_in} out={tokens_out})"
        )
        text.append(f"  •  cost {_format_decimal_footer(totals.cost_usd)}")
        text.append(f"  •  commits {totals.commits}")
        text.append(f"  •  closures {totals.auto_closures}")
        text.append(f"  •  PR advances {totals.pr_advances}")
        text.append(f"  •  strikes {totals.final_strikes}")
        if not totals.skills_observed:
            skills = "—"
        elif totals.skills_seen:
            skills = ", ".join(totals.skills_seen)
        else:
            skills = "none"
        text.append(f"  •  Skills consulted {skills}")
        return text

    # -- internal -----------------------------------------------------------

    def _format_peak_context_line(self, snap: IterationSnapshot) -> Text:
        """Render the peak live Context-fill sample independently of accounting."""
        text = Text()
        if snap.peak_context_window is None:
            text.append("—", style=STYLES["meta"])
            return text
        peak_used = snap.peak_context_window["current_tokens"]
        used = int(peak_used) if peak_used is not None else 0
        limit = snap.peak_context_window.get("token_limit")
        if limit is None:
            text.append(f"{used:,}")
            return text
        fraction = used / int(limit) if int(limit) > 0 else 0.0
        line = f"{used:,} / {int(limit):,}  ({int(round(fraction * 100))}%)"
        text.append(
            line,
            style=STYLES["warning"]
            if fraction >= _CONTEXT_HIGH_WATERMARK
            else None,
        )
        return text

    def _format_credits_line(self, snap: IterationSnapshot) -> Text:
        """Render the billed Credits line, its premium requests, and provenance.

        The two unavailability reasons stay distinguishable rather than sharing
        one em dash (ADR-0026): an **Orchestrator** that declares Cost
        unavailable says so in its own words, while a Python **Run** that simply
        saw no billing on the stream says *that*.
        """
        text = Text()
        credits = snap.credits(self.credits_denomination)
        if credits is None:
            text.append("—  ", style=STYLES["meta"])
            text.append(
                "(no billing telemetry reported)"
                if snap.measurement_observed("cost_usd")
                else "(this Orchestrator cannot report Cost)",
                style=STYLES["meta"],
            )
            return text
        text.append(f"{credits:.4f} credits")
        premium = snap.premium_requests
        rendered = _format_premium(premium)
        text.append(
            f"  •  {rendered} premium request{'' if rendered == '1' else 's'}",
            style=STYLES["meta"],
        )
        provenance = self.credits_denomination.provenance
        if provenance is not None:
            text.append(f"  ({provenance})", style=STYLES["meta"])
        return text

    def _format_cost_line(self, snap: IterationSnapshot) -> Text:
        """Render the cost line with the denomination's provenance, or the em dash."""
        text = Text()
        cost = snap.cost_usd(self.denomination)
        if cost is None:
            text.append("—  ", style=STYLES["meta"])
            text.append("(model not in pricing table)", style=STYLES["meta"])
            return text
        text.append(f"${cost:.4f} USD")
        provenance = self.denomination.provenance
        if provenance is not None:
            text.append(
                f"  ({provenance})",
                style=STYLES["meta"],
            )
        return text


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _format_decimal_footer(cost: Optional[Decimal]) -> str:
    """Footer-cell formatter for cost totals; em dash for None."""
    if cost is None:
        return "—"
    return f"${cost:.4f}"


def _sum_or_unknown(values: Iterable[Optional[Decimal]]) -> Optional[Decimal]:
    """Total the figures that were reported, or ``None`` when none were.

    Folds on the same rule ``cost_usd`` already uses: an Iteration that could not
    report contributes nothing rather than a zero, so the footer never reads as
    free work, and the two Cost footers stay comparable while both are rendered.
    """
    defined = [value for value in values if value is not None]
    if not defined:
        return None
    return sum(defined, Decimal(0))


def _format_credits(credits: Optional[Decimal]) -> str:
    """Cell formatter for billed **AI Credits**; em dash for unknown, never zero."""
    if credits is None:
        return "—"
    return f"{credits:.4f}"


def _format_premium(premium: Optional[Decimal]) -> str:
    """Cell formatter for the premium-request count; em dash for unknown.

    Rendered without a decimal point when the count is whole — which is the
    ordinary case, one premium request per call — and to two places otherwise, so
    a fractional multiplier is not rounded away into a wrong whole number.
    """
    if premium is None:
        return "—"
    if premium == premium.to_integral_value():
        return f"{premium.to_integral_value():f}".rstrip(".")
    return f"{premium:.2f}"
