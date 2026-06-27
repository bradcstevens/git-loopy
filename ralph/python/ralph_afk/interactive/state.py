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
commit-time ``Closes #N`` backstop.

From issue #34 (ADR-0003) the model also carries the **per-issue Logs**: one
bounded ring-buffer tail *per issue*, keyed by ref, of interleaved reasoning
(dimmed), assistant message text, and key structured events (tool calls,
commits, closures) in time order. Each issue's Log **accumulates across every
iteration** that worked it (it is *not* reset at iteration boundaries) and is
bounded per issue, so opening any Queue row's Log shows that issue's own record.
Output produced before the iteration's **working marker** is held in a pending
buffer and attributed to the active issue once it is known. The *full* record
stays in the always-on JSONL replay log on disk, so each per-issue tail can stay
bounded over a long (up to ~2-hour) run. (This supersedes issue #27's single
iteration-scoped, active-only transcript; the term "transcript" is retired in
favour of **Log**.)
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "LiveRunState",
    "IssueLedgerEntry",
    "QueueRow",
    "LogLine",
    "IssueDetail",
    "format_header",
    "format_duration",
    "format_wall_clock",
    "format_detail_header",
    "queue_rows",
    "issue_detail",
    "STATUS_QUEUED",
    "STATUS_ACTIVE",
    "STATUS_CLOSED",
    "STATUS_ADVANCED",
    "STATUS_NO_PROGRESS",
    "STATUS_GONE",
    "LOG_REASONING",
    "LOG_MESSAGE",
    "LOG_EVENT",
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
#: A runner-authored **Checkpoint** (issue #32 / ADR-0004). Folded into the
#: per-issue Log as a distinct event line but, unlike a commit, it does NOT
#: increment the iteration's commit tally — Checkpoints never count as agent
#: progress.
_CHECKPOINT_RECORDED = "wrapper.checkpoint.recorded"
_AUTO_CLOSE = "wrapper.auto_close"
_PR_ADVANCED = "wrapper.pr.advanced"
_ITERATION_END = "wrapper.iteration.end"
# The agent's final assistant message — a fallback marker source for when
# streaming deltas are unavailable (the live path taps ``stream_message``).
_ASSISTANT_MESSAGE = "assistant.message"
# Log-driving event literals (issue #34): the agent's reasoning blocks and tool
# calls join the streamed deltas + commit/closure events in the per-issue Log
# tail. Re-declared locally (importing ``ralph_afk.events`` would pull the SDK)
# and kept in lockstep by the parity test.
_ASSISTANT_REASONING = "assistant.reasoning"
_TOOL_CALL = "tool.call"

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

# ---------------------------------------------------------------------------
# Per-issue Log buffers (issue #34, ADR-0003)
# ---------------------------------------------------------------------------
#: Public Log-line kinds. The Log view dims **reasoning**; **event** lines carry
#: a leading glyph so the key structured events (tool calls, commits, closures)
#: stand out without colour; **message** is plain.
LOG_REASONING = "reasoning"
LOG_MESSAGE = "message"
LOG_EVENT = "event"
#: Bounded ring-buffer cap (lines) **per issue**: each issue's Log shows only
#: this tail, so no single issue's buffer can grow without limit over a long
#: run. The *full* record stays in the always-on JSONL replay log on disk
#: (issue #34 / ADR-0003 acceptance criterion).
_LOG_TAIL_LINES = 200


@dataclass
class IssueLedgerEntry:
    """One issue's lifecycle within a run, keyed by its source ref.

    All times are monotonic seconds from the run's injected clock (the same
    basis as the header's elapsed timer), so durations are directly comparable
    and unit-testable without a wall clock.

    * ``first_seen_at`` — first appearance in any pool this run.
    * ``started_at`` — first time it became the active issue (first working
      marker, or the iteration-start fallback when inferred).
    * ``started_wall`` — the **local wall-clock** time of ``started_at``, derived
      from the run-start reference (issue #33): the Queue's **Started** column.
      ``None`` until the issue first becomes active.
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
    started_wall: datetime | None = None
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


@dataclass(frozen=True)
class LogLine:
    """One line of a per-issue **Log** tail (issue #34, ADR-0003).

    A pure, Textual-free snapshot (mirrors :class:`QueueRow`) so the Log
    *content* is unit-testable without a TTY. ``kind`` is one of
    :data:`LOG_REASONING` / :data:`LOG_MESSAGE` / :data:`LOG_EVENT`; the Log
    view renders each line and dims the reasoning ones (see :attr:`dim`).
    ``text`` is the faithful display text — for ``event`` lines it already
    carries the leading glyph the line printer uses (``✓`` / ``»`` / ``◇`` /
    ``↑``).
    """

    kind: str
    text: str

    @property
    def dim(self) -> bool:
        """Whether the Log view should render this line dimmed (reasoning)."""
        return self.kind == LOG_REASONING


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

        # -- per-issue Log buffers (issue #34, ADR-0003) --------------------
        #: One bounded ring buffer of completed Log lines **per issue**, keyed
        #: by ref. Each accumulates across every iteration that worked the issue
        #: (it is *not* cleared at ``iteration.start``) and is bounded per issue
        #: so no single issue's buffer can grow without limit.
        self._logs: dict[int | str, deque[LogLine]] = {}
        #: Pending pre-marker lines for the current iteration: output produced
        #: before the active issue is known is held here and flushed into the
        #: active issue's buffer the moment it is activated (the working-marker
        #: attribution). Reset at each ``iteration.start``; bounded like a Log.
        self._pending: deque[LogLine] = deque(maxlen=_LOG_TAIL_LINES)
        #: The in-progress (newline-less) streamed line and which stream it
        #: belongs to, surfaced as a provisional trailing line by :meth:`log`
        #: so output appears live (not only on newline).
        self._partial_kind: str | None = None
        self._partial_text = ""
        #: Whether the *current* reasoning / message block arrived as streamed
        #: deltas, so the matching final event finalises instead of re-adding
        #: the whole block (mirrors the line printer's de-dup).
        self._streamed_reasoning = False
        self._streamed_message = False

    # -- EventSink protocol -------------------------------------------------

    def render(self, event: Mapping[str, Any]) -> None:
        """Fold one wrapper (or SDK-mapped) event into the live model.

        Two layers react here:

        * the **header band** (#23) tracks run-scope milestones — run start,
          iteration, strike, run end;
        * the **per-run ledger** (#25) folds the pool, commits, closures, and
          iteration boundaries into per-issue attribution and timing;
        * the **per-issue Log** (#34) folds tool calls, commits, and closures
          into the active issue's bounded tail here, joining the streamed
          reasoning/message deltas taken in :meth:`stream_reasoning` /
          :meth:`stream_message`.

        Unknown event types only contribute their ``run_id`` (learned once).
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
        elif etype == _TOOL_CALL:
            self._record_event_line(_log_tool_text(event))
        elif etype == _COMMIT_RECORDED:
            self._iter_commits += 1
            self._record_event_line(_log_commit_text(event))
        elif etype == _CHECKPOINT_RECORDED:
            # A runner Checkpoint: a distinct Log line, but NOT a commit — it
            # must not advance the issue or reset strikes.
            self._record_event_line(_log_checkpoint_text(event))
        elif etype == _AUTO_CLOSE:
            self._record_closure(event.get("issue"), now, status=STATUS_CLOSED)
            self._record_event_line(_log_auto_close_text(event))
        elif etype == _PR_ADVANCED:
            self._record_closure(event.get("pr"), now, status=STATUS_ADVANCED)
            self._record_event_line(_log_pr_advanced_text(event))
        elif etype == _STRIKE:
            self.strikes = _coerce_int(event.get("strikes"), self.strikes)
            self.max_strikes = _coerce_int(
                event.get("max_strikes"), self.max_strikes
            )
            self._iter_strike = True
        elif etype == _ITERATION_END:
            self._finalize_iteration(now)
        elif etype == _ASSISTANT_REASONING:
            self._finalize_reasoning(event.get("content"))
        elif etype == _ASSISTANT_MESSAGE:
            self._finalize_message(event.get("content"))
            self._scan_for_marker(event.get("content"))
        elif etype == _RUN_END:
            outcome = event.get("outcome")
            self.status = str(outcome) if outcome is not None else "ended"
            self._mark_ended()

    def stream_reasoning(self, delta: str) -> None:
        """Fold a reasoning delta into the active issue's Log (issue #34).

        Streamed deltas build the dimmed reasoning lines of the per-issue Log;
        the open (newline-less) line is surfaced live by :meth:`log` so output
        appears as the model thinks. The matching final ``assistant.reasoning``
        event then finalises the block without re-adding it (see
        :meth:`_finalize_reasoning`). Before the working marker is known the
        delta lands in the pending buffer (attributed on activation).
        """
        if not delta:
            return
        self._streamed_reasoning = True
        self._stream_into(LOG_REASONING, delta)

    def stream_message(self, delta: str) -> None:
        """Fold a message delta into the Log and tap the working marker.

        Two jobs (issue #25 + #34): the delta builds the assistant-message
        lines of the per-issue Log, and — because streaming can split
        ``<working issue=N>`` across chunks — the same text is scanned over a
        small rolling buffer to light up the active issue in the ledger (which
        also flushes any pending pre-marker output to it).
        """
        if delta:
            self._streamed_message = True
            self._stream_into(LOG_MESSAGE, delta)
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

    def _wall_at(self, instant: float) -> datetime | None:
        """The local wall-clock time for a monotonic ``instant``.

        The run samples the wall clock **once** (at run start, alongside the
        monotonic baseline); every per-issue **Started** stamp (issue #33) is then
        *derived* from its monotonic activation instant — ``started_wall +
        (instant - started_monotonic)`` — exactly as :meth:`elapsed_seconds`
        derives elapsed. This keeps Started consistent with the elapsed timer
        (one clock basis), is accurate for the iteration-end inference path (whose
        ``instant`` is the iteration start, not the moment the fallback runs), and
        is immune to a mid-run wall-clock adjustment. Returns ``None`` before the
        run-start reference is captured (no activation can precede it in practice).
        """
        if self.started_wall is None or self._started_monotonic is None:
            return None
        return self.started_wall + timedelta(seconds=instant - self._started_monotonic)

    # -- per-issue Log (issue #34, ADR-0003) -------------------------------

    def log(self, ref: int | str | None = None) -> tuple[LogLine, ...]:
        """One issue's bounded **Log** tail (or the live current tail).

        With ``ref`` given, returns that issue's accumulated, bounded Log — its
        own lines across every iteration that worked it, newest activity last —
        so opening any Queue row shows that issue's own record, isolated from
        the others. With no ``ref`` it returns the *live current* tail: the
        active issue's Log, or the pre-marker pending buffer when no issue is
        active yet. In both cases the in-progress (newline-less) streamed line
        is appended as a provisional trailing entry when it belongs to the
        returned issue, so output appears as the model produces it — not only
        once a line is terminated by a newline.
        """
        if ref is None:
            # The live current tail: the active issue's Log (always present once
            # activated — see _activate / _commit_buffer), else the pre-marker
            # pending buffer. The open partial belongs to whichever it is.
            committed: Iterable[LogLine] = (
                self._logs[self.active_ref]
                if self.active_ref is not None
                else self._pending
            )
            include_partial = True
        else:
            key = self._normalize_ref(ref)
            committed = self._logs.get(key) or ()
            include_partial = key == self.active_ref
        lines = list(committed)
        if include_partial and self._partial_kind is not None and self._partial_text:
            lines.append(
                LogLine(kind=self._partial_kind, text=self._partial_text)
            )
        return tuple(lines)

    def _commit_buffer(self) -> deque[LogLine]:
        """The buffer completed lines append to: the active issue's, else pending.

        Before the iteration's working marker is known (no active issue) lines
        land in :attr:`_pending` and are flushed into the active issue's buffer
        on activation; once an issue is active they land directly in its own
        accumulating, bounded Log.
        """
        if self.active_ref is None:
            return self._pending
        return self._logs.setdefault(
            self.active_ref, deque(maxlen=_LOG_TAIL_LINES)
        )

    def _stream_into(self, kind: str, delta: str) -> None:
        """Append a streamed delta, committing each completed (``\\n``) line.

        A switch of stream kind (reasoning <-> message) flushes the open
        partial first, so the two streams never glue onto one line. Completed
        lines land in the active issue's Log (or the pending pre-marker buffer)
        via :meth:`_commit_buffer`.
        """
        if self._partial_kind is not None and self._partial_kind != kind:
            self._flush_partial()
        self._partial_kind = kind
        self._partial_text += str(delta)
        if "\n" in self._partial_text:
            buf = self._commit_buffer()
            while "\n" in self._partial_text:
                line, self._partial_text = self._partial_text.split("\n", 1)
                buf.append(LogLine(kind=kind, text=line))

    def _flush_partial(self) -> None:
        """Commit the open (newline-less) streamed line, if any, and reset it."""
        if self._partial_kind is None:
            return
        if self._partial_text != "":
            self._commit_buffer().append(
                LogLine(kind=self._partial_kind, text=self._partial_text)
            )
        self._partial_kind = None
        self._partial_text = ""

    def _append_block(self, kind: str, content: Any) -> None:
        """Append a whole (non-streamed) reasoning/message block as lines."""
        if not isinstance(content, str) or content == "":
            return
        buf = self._commit_buffer()
        for line in content.split("\n"):
            buf.append(LogLine(kind=kind, text=line))

    def _record_event_line(self, text: str) -> None:
        """Append a key structured-event line (flushing any open stream line)."""
        if not text:
            return
        self._flush_partial()
        self._commit_buffer().append(LogLine(kind=LOG_EVENT, text=text))

    def _finalize_reasoning(self, content: Any) -> None:
        """Finalise a reasoning block: close the streamed line, else append it."""
        self._flush_partial()
        if self._streamed_reasoning:
            self._streamed_reasoning = False
            return
        self._append_block(LOG_REASONING, content)

    def _finalize_message(self, content: Any) -> None:
        """Finalise a message block: close the streamed line, else append it."""
        self._flush_partial()
        if self._streamed_message:
            self._streamed_message = False
            return
        self._append_block(LOG_MESSAGE, content)

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
        # Per-issue Logs ACCUMULATE across iterations (issue #34), so they are
        # never cleared here. Only the per-iteration streaming scratch resets:
        # the pending pre-marker buffer and the open streamed line. Any orphan
        # pre-marker output from an iteration that never identified an active
        # issue is discarded here (it lives on in the JSONL replay log).
        self._pending.clear()
        self._partial_kind = None
        self._partial_text = ""
        self._streamed_reasoning = False
        self._streamed_message = False

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
            entry.started_wall = self._wall_at(since)
            entry.waiting_duration = max(0.0, since - entry.first_seen_at)
        if entry.active_since is None:
            entry.active_since = since
        entry.status = STATUS_ACTIVE
        self.active_ref = ref
        # Attribute this iteration's pre-marker output (issue #34): flush the
        # pending buffer into the now-active issue's own accumulating Log, then
        # clear it so subsequent output lands directly in the issue's buffer.
        buf = self._logs.setdefault(ref, deque(maxlen=_LOG_TAIL_LINES))
        if self._pending:
            buf.extend(self._pending)
            self._pending.clear()

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
        # Commit any open streamed line into the active issue's Log (or the
        # pending buffer) before the active issue is parked, so the last
        # in-progress line is retained in the per-issue Log (issue #34).
        self._flush_partial()
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


# ---------------------------------------------------------------------------
# Log line formatting (issue #34) — faithful to the line printer's text
# ---------------------------------------------------------------------------
#: Cap on a single rendered argument/value so a tool line stays one tidy row.
_COMPACT_VALUE_CHARS = 60


def _compact_value(value: Any) -> str:
    """One-line, length-capped rendering of a tool-argument value."""
    text = str(value).replace("\n", " ")
    if len(text) > _COMPACT_VALUE_CHARS:
        return text[: _COMPACT_VALUE_CHARS - 3] + "..."
    return text


def _compact_args(arguments: Any) -> str:
    """Render tool-call arguments compactly (``k=v k=v`` for a dict)."""
    if isinstance(arguments, dict):
        return " ".join(f"{k}={_compact_value(v)}" for k, v in arguments.items())
    if arguments is None:
        return ""
    return _compact_value(arguments)


def _short_sha(event: Mapping[str, Any]) -> str:
    """The 10-char short SHA from a commit/closure event (``""`` if absent)."""
    sha = event.get("sha", "")
    return sha[:10] if isinstance(sha, str) else ""


def _log_tool_text(event: Mapping[str, Any]) -> str:
    """A tool call as a Log ``event`` line (mirrors the line printer)."""
    tool_name = event.get("tool_name", "")
    arguments = event.get("arguments")
    if tool_name == "skill":
        skill = ""
        if isinstance(arguments, dict):
            raw = arguments.get("skill")
            if isinstance(raw, str):
                skill = raw
        return f"◇ skill {skill or '(unknown)'}"
    args = _compact_args(arguments)
    return f"» {tool_name}  {args}" if args else f"» {tool_name}"


def _log_commit_text(event: Mapping[str, Any]) -> str:
    """A recorded commit as a Log ``event`` line."""
    text = f"✓ commit {_short_sha(event)}"
    subject = event.get("subject", "")
    if subject:
        lines = str(subject).splitlines()
        text += f"  {lines[0] if lines else str(subject)}"
    return text


def _log_checkpoint_text(event: Mapping[str, Any]) -> str:
    """A runner Checkpoint as a Log ``event`` line (distinct glyph)."""
    issue = event.get("issue")
    short = _short_sha(event)
    text = "⎘ checkpoint"
    if short:
        text += f" {short}"
    if issue is not None:
        label = f"#{issue}" if isinstance(issue, int) else str(issue)
        text += f"  ({label})"
    return text


def _log_auto_close_text(event: Mapping[str, Any]) -> str:
    """An auto-closed issue as a Log ``event`` line."""
    issue = event.get("issue")
    short = _short_sha(event)
    text = "✓ auto-closed"
    if issue is not None:
        text += f" #{issue}"
    if short:
        text += f"  ({short})"
    return text


def _log_pr_advanced_text(event: Mapping[str, Any]) -> str:
    """An advanced PR as a Log ``event`` line."""
    pr = event.get("pr")
    short = _short_sha(event)
    text = "↑ advanced PR"
    if pr is not None:
        text += f" #{pr}"
    if short:
        text += f"  ({short})"
    return text


def _format_elapsed(seconds: float) -> str:
    """Render elapsed seconds as ``H:MM:SS`` (hours never zero-padded)."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"


def format_duration(seconds: float) -> str:
    """Public ``H:MM:SS`` duration formatter (issue #26 live Queue timers).

    The same renderer the header's elapsed/active segments use, exposed so the
    Dashboard tab formats the per-issue queue timers identically (one place to
    change the clock format).
    """
    return _format_elapsed(seconds)


def format_wall_clock(when: datetime | None) -> str:
    """Public 12-hour AM/PM **wall-clock** stamp, e.g. ``1:42:07 PM`` (issue #33).

    The single renderer for every wall-clock surface — the Queue's per-issue
    **Started** column here, and (issue #37) the header run-start and the Log
    line stamps — so the AM/PM format lives in one place, the way
    :func:`format_duration` centralises *durations*. Wall-clock times use 12-hour
    AM/PM; durations stay ``H:MM:SS``. The hour drops its leading zero (``1:`` not
    ``01:``) while the minute/second padding is kept. ``None`` (an issue not yet
    active, or no run-start yet) renders as the em-dash placeholder.
    """
    if when is None:
        return "—"
    return when.strftime("%I:%M:%S %p").lstrip("0")


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


# ---------------------------------------------------------------------------
# Queue projection (issue #26)
# ---------------------------------------------------------------------------
#: Display-group rank for queue ordering: the active issue first, then
#: still-queued issues, then everything terminal (closed / advanced /
#: no-progress / gone) as trailing history. Within a group the ledger's
#: first-seen insertion order is preserved by the stable sort below.
_QUEUE_GROUP_RANK: dict[str, int] = {STATUS_ACTIVE: 0, STATUS_QUEUED: 1}
_QUEUE_GROUP_HISTORY = 2


@dataclass(frozen=True)
class QueueRow:
    """One projected Queue row: a ledger entry ready for the Dashboard list.

    A pure, Textual-free snapshot (mirrors :func:`format_header`) so the live
    Queue's *content + ordering* is unit-testable without a TTY. The columns are
    **Issue | Status | Started | Active** (issue #33, ADR-0003): ``started_wall``
    is the **wall-clock** time the issue first became active (the widget formats
    it via :func:`format_wall_clock`; ``None`` until it has been active), and
    ``active_seconds`` is the live ``H:MM:SS`` duration that sums across every
    iteration that worked the issue (the widget formats it via
    :func:`format_duration`, ticking against the caller's ``now`` — see
    :func:`queue_rows`). There is no Waiting column.
    """

    ref: int | str
    status: str
    started_wall: datetime | None
    active_seconds: float
    is_active: bool

    @property
    def label(self) -> str:
        """The issue identity as shown in the Queue (``#26`` / a PRD path)."""
        return f"#{self.ref}"


def queue_rows(state: LiveRunState, *, now: float | None = None) -> list[QueueRow]:
    """Project the per-run ledger into ordered Queue rows (issue #33 columns).

    Ordering (decision D5/CONTEXT.md): the **active** issue first, then
    **queued** issues, then the completed history (closed / advanced /
    no-progress / gone). Within each group the ledger's first-seen order is
    preserved (the sort is stable over ``ledger`` insertion order).

    Each row carries the issue's **Started** wall clock (``started_wall`` — the
    time it first became active, ``None`` while still only queued) and its
    **Active** duration (``active_seconds``), which ticks against ``now``
    (defaulting to the injected monotonic clock, the same basis as the header)
    while the issue is being worked and freezes once it ends / the run stops,
    summing across every iteration that worked it.
    """
    base = now if now is not None else state._monotonic()
    rows: list[QueueRow] = []
    for ref, entry in state.ledger.items():
        is_active = entry.status == STATUS_ACTIVE and ref == state.active_ref
        rows.append(
            QueueRow(
                ref=ref,
                status=entry.status,
                started_wall=entry.started_wall,
                active_seconds=entry.active_seconds(base),
                is_active=is_active,
            )
        )
    rows.sort(key=lambda r: _QUEUE_GROUP_RANK.get(r.status, _QUEUE_GROUP_HISTORY))
    return rows


# ---------------------------------------------------------------------------
# Per-issue drill-in projection (issue #27; Log view, issue #34)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IssueDetail:
    """One issue's drill-in detail: identity, status, timers, light history.

    A pure, Textual-free snapshot (mirrors :class:`QueueRow`) so the drill-in
    *content* is unit-testable without a TTY. ``is_active`` decides whether the
    Log view streams the active issue live (:meth:`LiveRunState.log`) or shows
    the issue's retained tail; the timers are raw seconds (the widget formats
    them via :func:`format_duration`) and tick against the caller's ``now``.
    """

    ref: int | str
    status: str
    is_active: bool
    active_seconds: float
    waiting_seconds: float
    first_seen_iter: int

    @property
    def label(self) -> str:
        """The issue identity as shown in the detail header (``#26``)."""
        return f"#{self.ref}"


def issue_detail(
    state: LiveRunState, ref: int | str, *, now: float | None = None
) -> IssueDetail:
    """Project one ledger entry into its drill-in :class:`IssueDetail`.

    ``ref`` may arrive as the widget's string row-key; it is normalised to the
    ledger's key (tolerating int/str skew) the same way pool refs and markers
    are. An unknown ref (never in any pool) degrades to a ``gone`` detail rather
    than raising, so a stale drill-in target never crashes the app.

    ``is_active`` is true only when this is the issue being worked *now* (its
    status is ``active`` and it is the run's ``active_ref``) — the signal the
    Log view uses to stream the active issue live versus showing the issue's
    retained tail.
    """
    key = state._normalize_ref(ref)
    entry = state.ledger.get(key)
    base = now if now is not None else state._monotonic()
    if entry is None:
        return IssueDetail(
            ref=ref,
            status=STATUS_GONE,
            is_active=False,
            active_seconds=0.0,
            waiting_seconds=0.0,
            first_seen_iter=0,
        )
    if entry.waiting_duration is not None:
        waiting = entry.waiting_duration
    else:
        waiting = max(0.0, base - entry.first_seen_at)
    return IssueDetail(
        ref=key,
        status=entry.status,
        is_active=entry.status == STATUS_ACTIVE and key == state.active_ref,
        active_seconds=entry.active_seconds(base),
        waiting_seconds=waiting,
        first_seen_iter=entry.first_seen_iter,
    )


def format_detail_header(detail: IssueDetail) -> str:
    """Compose the single-line drill-in header from an :class:`IssueDetail`.

    Pure and Textual-free (mirrors :func:`format_header`) so the detail header's
    *content* is unit-testable without a TTY: identity, status, the active and
    waiting timers, and the iteration the issue was first seen in.
    """
    return (
        f"{detail.label}"
        f"  •  status {detail.status}"
        f"  •  active {format_duration(detail.active_seconds)}"
        f"  •  waiting {format_duration(detail.waiting_seconds)}"
        f"  •  first seen iter {detail.first_seen_iter}"
    )
