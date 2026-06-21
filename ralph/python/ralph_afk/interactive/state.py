"""``ralph_afk.interactive.state`` — the Textual-agnostic live run model.

:class:`LiveRunState` is the **interactive sink** in the issue #22 fan-out
(ADR-0001): the ralph loop dispatches every wrapper event — and every streaming
reasoning/message delta — to it, and the Textual app *observes* it to paint the
screen. The app reads; the loop writes; both run on the one asyncio event loop,
so no locking is needed.

This module is **deep and pure** — stdlib + ``typing`` only, **no Textual**, no
``rich``, no SDK — so the run model stays unit-testable without a TTY and
honours the repo's import-guard convention (ADR-0001; mirrors
:mod:`ralph_afk.sinks`). Enforced by
``tests/test_interactive_state.py::test_state_module_imports_are_constrained``.

Because importing :mod:`ralph_afk.events` would pull the Copilot SDK (it types
``map_sdk_event`` against the SDK's event package), the handful of event-type
string literals this module switches on are re-declared locally. Their values —
not the constant names — are the contract; a parity test
(``test_state_event_type_constants_match_events``) keeps them in lockstep with
:mod:`ralph_afk.events`.

The model carries what the live **header band** needs (run id, model +
reasoning effort, run-start wall clock, live-ticking elapsed timer, iteration
number, run status, strike count ``x/N``) and, from issue #25, the **per-run
ledger**: a record keyed by issue ref of every issue seen in any pool this run,
with its status (queued / active / closed / advanced / no-progress / gone) and
its waiting + active timing. The active issue is attributed from the agent's
**working marker** (``<working issue=N>``, tapped off the message stream) with a
commit-time ``Closes #N`` backstop. The message-delta **transcript** pane itself
is still parked until #27.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

__all__ = [
    "LiveRunState",
    "IssueLedgerEntry",
    "format_header",
    "STATUS_QUEUED",
    "STATUS_ACTIVE",
    "STATUS_CLOSED",
    "STATUS_ADVANCED",
    "STATUS_NO_PROGRESS",
    "STATUS_GONE",
]

# Event-type string literals this model reacts to. Re-declared locally (rather
# than imported from ``ralph_afk.events``, which would pull the SDK) and kept in
# lockstep by ``test_state_event_type_constants_match_events``.
_RUN_START = "wrapper.run.start"
_RUN_END = "wrapper.run.end"
_ITERATION_START = "wrapper.iteration.start"
_STRIKE = "wrapper.strike"
# Iteration-scope events that drive the per-run ledger (issue #25). The agent's
# pool, commits, closures, and per-iteration boundaries all flow through the
# same #22 fan-out, so the ledger folds out of them with no new plumbing.
_AFK_READY_COLLECTED = "wrapper.afk_ready.collected"
_COMMIT_RECORDED = "wrapper.commit.recorded"
_AUTO_CLOSE = "wrapper.auto_close"
_PR_ADVANCED = "wrapper.pr.advanced"
_ITERATION_END = "wrapper.iteration.end"
# The agent's final assistant message — a fallback marker source for when
# streaming deltas are unavailable (the live path taps ``stream_message``).
_ASSISTANT_MESSAGE = "assistant.message"

#: Status shown before the first ``wrapper.run.start`` is observed.
_STATUS_STARTING = "starting"
#: Status while the loop is driving iterations.
_STATUS_RUNNING = "running"
#: Terminal status when the user Stops (``q`` / ``Ctrl+C``) — distinct from the
#: loop's own natural outcomes (``empty_pool`` / ``iteration_cap`` / ...), which
#: arrive as the ``wrapper.run.end`` ``outcome``.
_STATUS_STOPPED = "stopped"

# ---------------------------------------------------------------------------
# Per-run ledger (issue #25)
# ---------------------------------------------------------------------------
#: Issue-lifecycle statuses within a run (CONTEXT.md glossary). Public so the
#: live Queue (#26) and drill-in (#27) can switch on them without re-declaring.
STATUS_QUEUED = "queued"
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"
STATUS_ADVANCED = "advanced"
STATUS_NO_PROGRESS = "no-progress"
STATUS_GONE = "gone"

#: The agent's up-front working marker (``<working issue=N>``; see PROMPT.md).
#: Tolerant of surrounding whitespace, a leading ``#``, quotes, and case.
_WORKING_MARKER_RE = re.compile(
    r"<\s*working\s+issue\s*=\s*\"?#?(\d+)\"?\s*>", re.IGNORECASE
)
#: Rolling message-buffer cap for marker detection — large enough to span a
#: marker split across streaming deltas, small enough to stay O(1) per delta.
_MARKER_BUFFER_CHARS = 256


@dataclass
class IssueLedgerEntry:
    """One issue's lifecycle within a run, keyed by its source ref.

    All times are monotonic seconds from the run's injected clock (the same
    basis as the header's elapsed timer), so durations are directly comparable
    and unit-testable without a wall clock.

    * ``first_seen_at`` — first appearance in any pool this run.
    * ``started_at`` — first time it became the active issue (first working
      marker, or the iteration-start fallback when inferred).
    * ``waiting_duration`` — ``first_seen`` to first active.
    * ``active_duration`` — time spent active; **sums across iterations** if the
      issue is revisited (live-ticking via :meth:`active_seconds`).
    * ``ended_at`` — when a terminal closure (closed / advanced) was recorded.
    * ``active_since`` — internal: start of the current active stint, or
      ``None`` when not currently active.
    """

    ref: int | str
    first_seen_at: float
    first_seen_iter: int
    status: str = STATUS_QUEUED
    started_at: float | None = None
    waiting_duration: float | None = None
    active_duration: float = 0.0
    ended_at: float | None = None
    active_since: float | None = None

    def active_seconds(self, now: float) -> float:
        """Total active time, live-ticking against ``now`` while active."""
        total = self.active_duration
        if self.active_since is not None:
            total += max(0.0, now - self.active_since)
        return total


def _default_wall_clock() -> datetime:
    """Local wall-clock time, used for the human-readable run-start stamp."""
    return datetime.now()


class LiveRunState:
    """Mutable, Textual-agnostic snapshot of one run, fed via the sink fan-out.

    Satisfies the :class:`ralph_afk.sinks.EventSink` protocol structurally
    (``render`` / ``stream_reasoning`` / ``stream_message``). The loop calls
    those; the app reads the attributes (or :func:`format_header`) on a timer.

    The run-start wall clock and the monotonic elapsed baseline are captured
    when the first ``wrapper.run.start`` (or, defensively, the first
    ``wrapper.iteration.start``) is observed — not at construction — so the
    elapsed timer measures the run, not the time the app spent starting up.
    """

    def __init__(
        self,
        *,
        run_id: str = "",
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_strikes: int = 0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = _default_wall_clock,
    ) -> None:
        self.run_id = run_id
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_strikes = max_strikes
        self._monotonic = monotonic
        self._wall_clock = wall_clock

        self.started_wall: datetime | None = None
        self._started_monotonic: float | None = None
        self._ended_monotonic: float | None = None
        self.iteration = 0
        self.status = _STATUS_STARTING
        self.strikes = 0
        self.ended = False

        # -- per-run ledger (issue #25) -------------------------------------
        #: Every issue seen in any pool this run, keyed by ref in first-seen
        #: order. The live Queue (#26) orders/filters; here it is the record.
        self.ledger: dict[int | str, IssueLedgerEntry] = {}
        #: The issue being worked right now (working marker / inference), or
        #: ``None`` between iterations. Drives the header's active band.
        self.active_ref: int | str | None = None
        # Per-iteration scratch, reset at each ``iteration.start``.
        self._iter_started_monotonic: float | None = None
        self._iter_pool: list[int | str] = []
        self._iter_commits = 0
        self._iter_strike = False
        self._msg_buffer = ""

    # -- EventSink protocol -------------------------------------------------

    def render(self, event: Mapping[str, Any]) -> None:
        """Fold one wrapper (or SDK-mapped) event into the live model.

        Two layers react here:

        * the **header band** (#23) tracks run-scope milestones — run start,
          iteration, strike, run end;
        * the **per-run ledger** (#25) folds the pool, commits, closures, and
          iteration boundaries into per-issue attribution and timing.

        Unknown event types only contribute their ``run_id`` (learned once);
        the transcript pane (#27) hangs off later, richer reactions.
        """
        run_id = event.get("run_id")
        if run_id and not self.run_id:
            self.run_id = str(run_id)

        now = self._monotonic()
        etype = event.get("type")
        if etype == _RUN_START:
            self._mark_started()
            self.status = _STATUS_RUNNING
            max_strikes = event.get("max_nmt_strikes")
            if max_strikes is not None:
                self.max_strikes = _coerce_int(max_strikes, self.max_strikes)
        elif etype == _ITERATION_START:
            self._mark_started()
            self.iteration = _coerce_int(event.get("iter"), self.iteration)
            self.status = _STATUS_RUNNING
            self._begin_iteration(now)
        elif etype == _AFK_READY_COLLECTED:
            self._record_pool(event.get("issues"), now)
        elif etype == _COMMIT_RECORDED:
            self._iter_commits += 1
        elif etype == _AUTO_CLOSE:
            self._record_closure(event.get("issue"), now, status=STATUS_CLOSED)
        elif etype == _PR_ADVANCED:
            self._record_closure(event.get("pr"), now, status=STATUS_ADVANCED)
        elif etype == _STRIKE:
            self.strikes = _coerce_int(event.get("strikes"), self.strikes)
            self.max_strikes = _coerce_int(
                event.get("max_strikes"), self.max_strikes
            )
            self._iter_strike = True
        elif etype == _ITERATION_END:
            self._finalize_iteration(now)
        elif etype == _ASSISTANT_MESSAGE:
            self._scan_for_marker(event.get("content"))
        elif etype == _RUN_END:
            outcome = event.get("outcome")
            self.status = str(outcome) if outcome is not None else "ended"
            self._mark_ended()

    def stream_reasoning(self, delta: str) -> None:
        """Accept a reasoning delta. Parked until the #27 transcript pane."""

    def stream_message(self, delta: str) -> None:
        """Tap the agent message stream for the working marker (issue #25).

        Streaming deltas can split ``<working issue=N>`` across chunks, so the
        scan runs over a small rolling buffer. Detection lights up the active
        issue live in the ledger; the transcript pane itself lands in #27.
        """
        self._scan_for_marker(delta)

    # -- driver-facing controls --------------------------------------------

    def mark_stopped(self) -> None:
        """Record a user **Stop** (``q`` / ``Ctrl+C``) as the terminal status.

        Called by the interactive driver when the user ends the run from the
        TUI, so the final header reads ``stopped`` rather than freezing on
        ``running`` — distinct from the loop's own natural ``wrapper.run.end``
        outcomes.
        """
        self.status = _STATUS_STOPPED
        self._mark_ended()

    # -- live timers --------------------------------------------------------

    def elapsed_seconds(self, now: float | None = None) -> float:
        """Seconds since the run started, frozen once the run has ended.

        Returns ``0.0`` before the run-start baseline is captured. While the
        run is live the elapsed time is measured against ``now`` (defaulting to
        the injected monotonic clock), so the header ticks; once ended it is
        pinned to the end baseline so the final frame is stable.
        """
        if self._started_monotonic is None:
            return 0.0
        end = self._ended_monotonic
        if end is None:
            end = now if now is not None else self._monotonic()
        return max(0.0, end - self._started_monotonic)

    def active_seconds(self, now: float | None = None) -> float:
        """Live active time of the current active issue, ``0.0`` if none.

        Mirrors :meth:`elapsed_seconds`: while the issue is active the value
        ticks against ``now`` (defaulting to the injected monotonic clock);
        once the run ends or is Stopped the active stint is folded into the
        ledger entry and ``active_since`` cleared, so the value freezes.
        """
        ref = self.active_ref
        if ref is None:
            return 0.0
        entry = self.ledger.get(ref)
        if entry is None:
            return 0.0
        base = now if now is not None else self._monotonic()
        return entry.active_seconds(base)

    # -- internals ----------------------------------------------------------

    def _mark_started(self) -> None:
        if self._started_monotonic is None:
            self._started_monotonic = self._monotonic()
            self.started_wall = self._wall_clock()

    def _mark_ended(self) -> None:
        self.ended = True
        if self._ended_monotonic is None and self._started_monotonic is not None:
            self._ended_monotonic = self._monotonic()
        # Freeze the active issue's live timer on the final frame (a Stop can
        # land mid-iteration, with an issue still active). The ref is kept so
        # the header still shows what was active when the run ended.
        ref = self.active_ref
        if ref is not None:
            entry = self.ledger.get(ref)
            if entry is not None and entry.active_since is not None:
                at = self._ended_monotonic
                if at is None:
                    at = self._monotonic()
                entry.active_duration += max(0.0, at - entry.active_since)
                entry.active_since = None

    # -- ledger (issue #25) -------------------------------------------------

    def _begin_iteration(self, now: float) -> None:
        """Open a new iteration: record its start and reset per-iter scratch.

        The previous ``iteration.end`` clears the active issue; defensively
        finalise any lingering active stint so the timer can never run across
        iteration boundaries.
        """
        if self.active_ref is not None:
            self._deactivate(self.active_ref, at=now)
        self._iter_started_monotonic = now
        self._iter_pool = []
        self._iter_commits = 0
        self._iter_strike = False
        self._msg_buffer = ""

    def _record_pool(self, issues: Any, now: float) -> None:
        """Fold one ``afk_ready.collected`` pool into the ledger.

        New refs enter as ``queued`` (capturing ``first_seen_at``); a ref that
        had gone ``gone`` and reappears returns to ``queued``. Any still-
        ``queued`` issue absent from this pool left without ever being worked —
        ``gone`` (decisions D4b; CONTEXT.md).
        """
        refs = [self._normalize_ref(r) for r in issues] if issues else []
        self._iter_pool = refs
        present = set(refs)
        for ref in refs:
            entry = self.ledger.get(ref)
            if entry is None:
                self.ledger[ref] = IssueLedgerEntry(
                    ref=ref, first_seen_at=now, first_seen_iter=self.iteration
                )
            elif entry.status == STATUS_GONE:
                entry.status = STATUS_QUEUED
        for ref, entry in self.ledger.items():
            if entry.status == STATUS_QUEUED and ref not in present:
                entry.status = STATUS_GONE

    def _scan_for_marker(self, text: Any) -> None:
        """Scan agent message text for ``<working issue=N>`` and light it up.

        A small rolling buffer lets a marker split across streaming deltas be
        detected; once matched, the buffer is trimmed past it so the same
        marker is not re-fired.
        """
        if not text:
            return
        self._msg_buffer = (self._msg_buffer + str(text))[-_MARKER_BUFFER_CHARS:]
        match = _WORKING_MARKER_RE.search(self._msg_buffer)
        if match is None:
            return
        self._msg_buffer = self._msg_buffer[match.end():]
        self._activate(int(match.group(1)), since=self._monotonic())

    def _activate(self, ref: int | str, *, since: float) -> None:
        """Make ``ref`` the active issue, starting its active stint at ``since``.

        ``started_at`` / ``waiting_duration`` are set once (first activation);
        ``active_since`` opens a stint whose duration is folded into
        ``active_duration`` on the next deactivation, so revisits sum.
        """
        ref = self._normalize_ref(ref)
        entry = self.ledger.get(ref)
        if entry is None:
            entry = IssueLedgerEntry(
                ref=ref, first_seen_at=since, first_seen_iter=self.iteration
            )
            self.ledger[ref] = entry
        if self.active_ref is not None and self.active_ref != ref:
            # One active issue per iteration: park the previous one.
            self._deactivate(self.active_ref, at=since, status=STATUS_QUEUED)
        if entry.started_at is None:
            entry.started_at = since
            entry.waiting_duration = max(0.0, since - entry.first_seen_at)
        if entry.active_since is None:
            entry.active_since = since
        entry.status = STATUS_ACTIVE
        self.active_ref = ref

    def _record_closure(self, ref: Any, now: float, *, status: str) -> None:
        """Record an authoritative commit-time outcome (closed / advanced).

        When no working marker arrived this iteration, the closure is also the
        active-issue attribution: the iteration's active time falls back to the
        iteration-start baseline (decision D1b — the ``Closes #N`` backstop).
        """
        if ref is None:
            return
        ref = self._normalize_ref(ref)
        entry = self.ledger.get(ref)
        if entry is None:
            seen = self._iter_started_monotonic
            entry = IssueLedgerEntry(
                ref=ref,
                first_seen_at=seen if seen is not None else now,
                first_seen_iter=self.iteration,
            )
            self.ledger[ref] = entry
        if self.active_ref is None:
            baseline = self._iter_started_monotonic
            self._activate(ref, since=baseline if baseline is not None else now)
        entry.status = status
        entry.ended_at = now

    def _deactivate(
        self, ref: int | str, *, at: float, status: str | None = None
    ) -> None:
        """Close the active stint for ``ref``, folding it into ``active_duration``."""
        entry = self.ledger.get(ref)
        if entry is None:
            return
        if entry.active_since is not None:
            entry.active_duration += max(0.0, at - entry.active_since)
            entry.active_since = None
        if status is not None:
            entry.status = status
        if self.active_ref == ref:
            self.active_ref = None

    def _finalize_iteration(self, now: float) -> None:
        """Reconcile the active issue's terminal status at ``iteration.end``.

        A closure already set ``closed`` / ``advanced``; otherwise an active
        issue with commits is ``advanced`` and one without is ``no-progress``
        (a strike). With no marker and no closure, a single-member pool is
        inferred as the active issue.
        """
        ref = self.active_ref
        if (
            ref is None
            and len(self._iter_pool) == 1
            and (self._iter_commits > 0 or self._iter_strike)
        ):
            baseline = self._iter_started_monotonic
            self._activate(
                self._iter_pool[0],
                since=baseline if baseline is not None else now,
            )
            ref = self.active_ref
        if ref is not None:
            entry = self.ledger.get(ref)
            if entry is not None and entry.status == STATUS_ACTIVE:
                if self._iter_commits > 0:
                    entry.status = STATUS_ADVANCED
                    entry.ended_at = now
                else:
                    entry.status = STATUS_NO_PROGRESS
            self._deactivate(ref, at=now)
        self._iter_pool = []
        self._iter_commits = 0
        self._iter_strike = False

    def _normalize_ref(self, ref: Any) -> int | str:
        """Resolve a ref to its existing ledger key, tolerating int/str skew.

        Pool refs, closures, and markers all arrive as issue numbers for the
        GitHub backend; this keeps a marker's parsed ``int`` and a pool's ref
        pointing at the same entry, and leaves PRD path refs (``str``) intact.
        """
        if ref in self.ledger:
            return ref
        try:
            as_int = int(ref)
        except (TypeError, ValueError):
            as_int = None
        if as_int is not None and as_int in self.ledger:
            return as_int
        as_str = str(ref)
        if as_str in self.ledger:
            return as_str
        return as_int if as_int is not None else ref


def _coerce_int(value: Any, fallback: int) -> int:
    """Best-effort int coercion: malformed payloads keep the prior value."""
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _format_elapsed(seconds: float) -> str:
    """Render elapsed seconds as ``H:MM:SS`` (hours never zero-padded)."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"


def format_header(state: LiveRunState, *, now: float | None = None) -> str:
    """Compose the single-line header band from a :class:`LiveRunState`.

    Pure and Textual-free so the header's *content* is unit-testable without a
    TTY; the app simply drops the returned string into a widget. The fields
    mirror issue #23's header contract: run id, model + reasoning effort,
    run-start clock, live-ticking elapsed, iteration number, status, strikes.
    """
    run_id = state.run_id or "—"

    if state.model:
        model = state.model
        if state.reasoning_effort:
            model = f"{model} ({state.reasoning_effort})"
    else:
        model = "default"

    started = state.started_wall.strftime("%H:%M:%S") if state.started_wall else "—"
    elapsed = _format_elapsed(state.elapsed_seconds(now))

    if state.active_ref is not None:
        active = f"#{state.active_ref} {_format_elapsed(state.active_seconds(now))}"
    else:
        active = "—"

    return (
        f"ralph-afk  run {run_id}"
        f"  •  model {model}"
        f"  •  start {started}  elapsed {elapsed}"
        f"  •  iter {state.iteration}"
        f"  •  active {active}"
        f"  •  {state.status}"
        f"  •  strikes {state.strikes}/{state.max_strikes}"
    )
