"""``git_loopy.interactive.state`` — the Textual-agnostic live run model.

:class:`LiveRunState` is the **interactive sink** in the issue #22 fan-out
(ADR-0001): the ralph loop dispatches every wrapper event — and every streaming
reasoning/message delta — to it, and the Textual app *observes* it to paint the
screen. The app reads; the loop writes; both run on the one asyncio event loop,
so no locking is needed.

This module is **deep and pure** — stdlib + ``typing`` only, **no Textual**, no
``rich``, no SDK — so the run model stays unit-testable without a TTY and
honours the repo's import-guard convention (ADR-0001; mirrors
:mod:`git_loopy.sinks`). Enforced by
``tests/test_interactive_state.py::test_state_module_imports_are_constrained``.

Because importing :mod:`git_loopy.events` would pull the Copilot SDK (it types
``map_sdk_event`` against the SDK's event package), the handful of event-type
string literals this module switches on are re-declared locally. Their values —
not the constant names — are the contract; a parity test
(``test_state_event_type_constants_match_events``) keeps them in lockstep with
:mod:`git_loopy.events`.

The model carries what the live **header band** needs (run id, model +
reasoning effort, run-start wall clock, live-ticking elapsed timer, iteration
number, run status, strike count ``x/N``) and, from issue #25, the **per-run
ledger**: a record keyed by issue ref of every issue seen in any pool this run,
with its status (queued / active / closed / advanced / no-progress / gone) and
its waiting + active timing. The active issue is attributed from the
Orchestrator's immutable ``wrapper.issue.activated`` event; marker and fallback
selection stay producer-owned.

From issue #36 (ADR-0003) each ledger entry also carries **per-issue
consumption**: the input/output tokens of every ``usage.tokens`` event observed
while the issue was active (the model they were billed against too), **summed
across every iteration** that worked it — attributed to the same Active issue as
the timing and Log, with the same pending-pre-marker flush. The Queue renders
these as live tokens-in / tokens-out / estimated-cost columns; summed they
reconcile with the run-level **Summary** totals (which still account per
iteration).

From issue #34 (ADR-0003) the model also carries the **per-issue Logs**: one
bounded ring-buffer tail *per issue*, keyed by ref, of interleaved reasoning
(dimmed), assistant message text, and key structured events (tool calls,
commits, closures) in time order. Each issue's Log **accumulates across every
iteration** that worked it (it is *not* reset at iteration boundaries) and is
bounded per issue, so opening any Queue row's Log shows that issue's own record.
Output produced before the Iteration's activation event is held in a pending
buffer and attributed to the Active issue once it is known. The *full* record
stays in the always-on JSONL replay log on disk, so each per-issue tail can stay
bounded over a long (up to ~2-hour) run. (This supersedes issue #27's single
iteration-scoped, active-only transcript; the term "transcript" is retired in
favour of **Log**.)
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping

from git_loopy.usage import UsageTally

__all__ = [
    "LiveRunState",
    "IssueLedgerEntry",
    "IssueContribution",
    "ContextWindowSnapshot",
    "QueueRow",
    "LogLine",
    "LogLineView",
    "IssueDetail",
    "format_header",
    "format_context_fill",
    "format_duration",
    "format_wall_clock",
    "format_detail_header",
    "format_activity_header",
    "queue_rows",
    "log_line_views",
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
    "LOG_UNCLASSIFIED",
]

# Event-type string literals this model reacts to. Re-declared locally (rather
# than imported from ``git_loopy.events``, which would pull the SDK) and kept in
# lockstep by ``test_state_event_type_constants_match_events``.
_RUN_START = "wrapper.run.start"
_RUN_END = "wrapper.run.end"
_ISSUE_ACTIVATED = "wrapper.issue.activated"
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
_AGENT_OUTPUT = "agent.output"
# Log-driving event literals (issue #34): the agent's reasoning blocks and tool
# calls join the streamed deltas + commit/closure events in the per-issue Log
# tail. Re-declared locally (importing ``git_loopy.events`` would pull the SDK)
# and kept in lockstep by the parity test.
_ASSISTANT_REASONING = "assistant.reasoning"
_TOOL_CALL = "tool.call"
#: Per-turn token usage (issue #36): the SDK's ``assistant.usage`` mapped to
#: ``{model, input, output}``. Folded into the **Active issue**'s per-issue
#: consumption (tokens in/out + cost), summing across every iteration that
#: worked it. Re-declared locally (importing ``git_loopy.events`` would pull the
#: SDK) and kept in lockstep by the parity test.
_USAGE_TOKENS = "usage.tokens"
_USAGE_CONTEXT_WINDOW = "usage.context_window"

# Historical schema-1 traces predate ``wrapper.issue.activated``. Keep their
# marker projection readable, but disable it as soon as the authoritative event
# appears for the current Iteration.
_WORKING_MARKER_RE = re.compile(
    r"<\s*working\s+issue\s*=\s*\"?#?(\d+)\"?\s*>", re.IGNORECASE
)
_MARKER_BUFFER_CHARS = 256

#: Event types that, when carrying a runner-stamped ``lane_issue`` (issue #66,
#: ADR-0008), route to the **multi-active** per-Lane handler instead of the
#: serial single-active dispatch: the per-iteration agent output the live
#: Dashboard folds into a Lane's own timer / Log / Consumption. An event without
#: ``lane_issue`` (the serial path) never consults this set — dispatch is
#: byte-for-byte unchanged. Run/iteration-boundary events are deliberately
#: excluded: they are Wave-scoped (one per Wave), never Lane-stamped.
_LANE_EVENTS = frozenset(
    {
        _TOOL_CALL,
        _COMMIT_RECORDED,
        _CHECKPOINT_RECORDED,
        _AUTO_CLOSE,
        _PR_ADVANCED,
        _ASSISTANT_REASONING,
        _ASSISTANT_MESSAGE,
        _AGENT_OUTPUT,
        _USAGE_TOKENS,
    }
)

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

# ---------------------------------------------------------------------------
# Per-issue Log buffers (issue #34, ADR-0003)
# ---------------------------------------------------------------------------
#: Public Log-line kinds. The Log view dims **reasoning**; **event** lines carry
#: a leading glyph so the key structured events (tool calls, commits, closures)
#: stand out without colour; **message** is plain.
LOG_REASONING = "reasoning"
LOG_MESSAGE = "message"
LOG_EVENT = "event"
LOG_UNCLASSIFIED = "unclassified"
#: Opens each reasoning block in the Log (issue #37), mirroring the line
#: printer's ``✻ Thinking:`` prefix. Re-declared locally (state.py stays
#: SDK/rich-free, like the event-type literals) and kept in lockstep by a parity
#: test (``test_thinking_marker_matches_the_line_printer_prefix``).
_THINKING_MARKER = "✻ Thinking:"
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
    * ``usage`` — per-issue **Consumption** (issue #36) as a shared
      :class:`~git_loopy.usage.UsageTally` (issue #41): the input/output tokens
      of every ``usage.tokens`` event attributed to this issue while it was the
      Active issue, plus the model they were billed against (first non-None
      wins), **summed across every iteration** that worked it (the same
      accumulate-not-reset rule as ``active_duration``). The Queue's per-issue
      **Cost** is :meth:`UsageTally.cost` over it. The accrual rule and the
      unknown-model cost guard live in the tally, not a second copy here.
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
    usage: UsageTally = field(default_factory=UsageTally)
    ended_at: float | None = None
    active_since: float | None = None
    usage_observed: bool = False
    closed_wall: datetime | None = None
    issue_elapsed_seconds: float | None = None
    contributions: list[IssueContribution] = field(default_factory=list)
    normalized_cost_usd: float | None = None

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
    ``↑``). ``timestamp`` is the **local wall-clock** time the line was appended
    (issue #37), rendered as a 12-hour AM/PM stamp by :func:`log_line_views`
    (which collapses repeats within the same second); ``None`` only if the run's
    wall clock yields none.
    """

    kind: str
    text: str
    timestamp: datetime | None = None

    @property
    def dim(self) -> bool:
        """Whether the Log view should render this line dimmed (reasoning)."""
        return self.kind == LOG_REASONING


@dataclass(frozen=True)
class ContextWindowSnapshot:
    """One truthful live **Context fill** sample for the current Iteration."""

    current_tokens: int
    token_limit: int | None
    effective_target_tokens: int | None
    effective_ceiling_tokens: int | None


@dataclass(frozen=True)
class IssueContribution:
    """One finalized Iteration or Lane contribution for an Active issue."""

    kind: str
    iteration: int | None
    lane: int | str | None
    status: str
    active_seconds: float
    usage: UsageTally
    cost_usd: float | None
    peak_context_window: ContextWindowSnapshot | None


def _default_wall_clock() -> datetime:
    """Local wall-clock time, used for the human-readable run-start stamp."""
    return datetime.now().astimezone()


@dataclass
class _StreamState:
    """Per-attribution streaming-assembly scratch for the per-issue Log.

    Bundles the in-flight state of *one* streamed reasoning/message line so it
    can be tracked independently per attribution target. Serial mode uses a
    single instance (:attr:`LiveRunState._stream`); in **Parallel mode** (issue
    #66, ADR-0008) each concurrent **Lane** gets its own instance keyed by issue
    ref, so N Lanes' interleaved reasoning/message deltas each assemble into the
    right per-issue **Log** without gluing onto one another's open line.

    * ``partial_kind`` / ``partial_text`` — the open (newline-less) streamed
      line and its kind, surfaced live by :meth:`LiveRunState.log`.
    * ``partial_started`` — the wall clock captured when the open line *began*
      (issue #37), reused when it commits so the live and committed stamps agree.
    * ``streamed_reasoning`` / ``streamed_message`` — whether the current
      reasoning / message block arrived as deltas, so the matching final event
      finalises instead of re-adding the whole block (mirrors the line printer's
      de-dup).
    """

    partial_kind: str | None = None
    partial_text: str = ""
    partial_started: datetime | None = None
    streamed_reasoning: bool = False
    streamed_message: bool = False


class LiveRunState:
    """Mutable, Textual-agnostic snapshot of one run, fed via the sink fan-out.

    Satisfies the :class:`git_loopy.sinks.EventSink` protocol structurally
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
        self.context_window_available: bool | None = None
        self.context_window: ContextWindowSnapshot | None = None
        self.peak_context_window: ContextWindowSnapshot | None = None

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
        self._authoritative_binding = False
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
        #: Pending pre-marker token usage for the current iteration (issue #36),
        #: the consumption analogue of ``_pending``: a shared
        #: :class:`~git_loopy.usage.UsageTally` (issue #41) that ``usage.tokens``
        #: arriving before the active issue is known accrue into, flushed onto
        #: the active issue on activation (including the late ``Closes #N`` /
        #: single-member-pool backstop). Reset at each ``iteration.start``; an
        #: iteration that never names an active issue discards it, the same
        #: orphan treatment as ``_pending``.
        self._pending_usage = UsageTally()
        self._pending_usage_observed = False
        #: Serial streaming-assembly scratch (issue #34): the single active
        #: attribution's in-flight streamed line + streamed-block flags. In
        #: **Parallel mode** (issue #66) each Lane instead assembles into its own
        #: :class:`_StreamState` in :attr:`_lane_streams`, keyed by issue ref, so
        #: concurrent Lane deltas never interleave onto one open line.
        self._stream = _StreamState()
        #: Per-Lane streaming scratch (issue #66, ADR-0008): one
        #: :class:`_StreamState` per concurrently-active Lane, keyed by issue
        #: ref. Reset per Wave alongside :attr:`_stream` (the Logs themselves,
        #: in :attr:`_logs`, accumulate across iterations and are *not* reset).
        self._lane_streams: dict[int | str, _StreamState] = {}
        self._iter_lane_refs: set[int | str] = set()
        #: Per-Lane commit tally for the current Wave (issue #66): a Lane's
        #: ``commit.recorded`` count, kept apart from the serial
        #: :attr:`_iter_commits` so a Lane's advanced/no-progress reconciliation
        #: at Wave end uses its *own* progress, not the serial single-active
        #: counter. Reset per Wave.
        self._lane_commits: dict[int | str, int] = {}

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
          :meth:`stream_message`;
        * the **per-issue consumption** (#36) folds ``usage.tokens`` into the
          active issue's token tallies (the basis for the Queue's per-issue
          Cost), summing across every iteration that worked it.

        Unknown event types only contribute their ``run_id`` (learned once).
        """
        run_id = event.get("run_id")
        if run_id and not self.run_id:
            self.run_id = str(run_id)

        now = self._monotonic()
        etype = event.get("type")
        # Multi-active dispatch (issue #66, ADR-0008): a runner-stamped
        # ``lane_issue`` routes this Lane's per-iteration output to its own
        # timer / Log / Consumption, bypassing the serial single-active
        # inference. Absent stamp = serial path below, byte-for-byte unchanged.
        lane_issue = event.get("lane_issue")
        if etype == _ISSUE_ACTIVATED:
            ref = event.get("issue")
            if ref is None:
                return
            self._authoritative_binding = True
            if lane_issue is not None:
                self._lane_touch(
                    self._normalize_ref(ref),
                    now,
                    started_wall=self._local_timestamp(event.get("activated_at")),
                )
            elif self.active_ref is None:
                source = event.get("binding_source")
                since = (
                    self._iter_started_monotonic
                    if source in {"closure", "commit", "single_member_pool"}
                    and self._iter_started_monotonic is not None
                    else now
                )
                self._activate(
                    ref,
                    since=since,
                    started_wall=self._local_timestamp(event.get("activated_at")),
                )
            return
        if lane_issue is not None and etype in _LANE_EVENTS:
            self._render_lane_event(str(etype), lane_issue, event, now)
            return
        if etype == _RUN_START:
            self._mark_started()
            self.status = _STATUS_RUNNING
            capabilities = event.get("insight_capabilities")
            if isinstance(capabilities, Mapping):
                available = capabilities.get("context_window")
                if isinstance(available, bool):
                    self.context_window_available = available
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
            self.max_strikes = _coerce_int(event.get("max_strikes"), self.max_strikes)
            self._iter_strike = True
        elif etype == _ITERATION_END:
            self._finalize_iteration(now)
            self._record_normalized_contributions(event)
        elif etype == _ASSISTANT_REASONING:
            self._finalize_reasoning(event.get("content"))
        elif etype == _ASSISTANT_MESSAGE:
            self._finalize_message(event.get("content"))
            self._scan_for_marker(event.get("content"))
        elif etype == _AGENT_OUTPUT:
            self._append_block(LOG_UNCLASSIFIED, event.get("text"))
        elif etype == _USAGE_TOKENS:
            self._record_usage(
                event.get("model"),
                event.get("input"),
                event.get("output"),
            )
        elif etype == _USAGE_CONTEXT_WINDOW:
            snapshot = _context_window_snapshot(event)
            if snapshot is not None:
                self.context_window_available = True
                self.context_window = snapshot
                if (
                    self.peak_context_window is None
                    or snapshot.current_tokens > self.peak_context_window.current_tokens
                ):
                    self.peak_context_window = snapshot
        elif etype == _RUN_END:
            outcome = event.get("outcome")
            self.status = str(outcome) if outcome is not None else "ended"
            self._mark_ended()

    def stream_reasoning(self, delta: str, issue: int | str | None = None) -> None:
        """Fold a reasoning delta into the right issue's Log (issues #34/#66).

        Streamed deltas build the dimmed reasoning lines of the per-issue Log;
        the open (newline-less) line is surfaced live by :meth:`log` so output
        appears as the model thinks. The first delta of a block opens it with a
        timestamped ``✻ Thinking:`` marker (issue #37), mirroring the line
        printer's prefix. The matching final ``assistant.reasoning`` event then
        finalises the block without re-adding it (see :meth:`_finalize_reasoning`).

        Serial path (``issue is None``): before activation is known the
        delta lands in the pending buffer (attributed on activation). Parallel
        mode (``issue`` set, issue #66): the delta is assembled into that Lane's
        own :class:`_StreamState` and Log directly — deterministic attribution,
        no marker inference — so concurrent Lanes never interleave.
        """
        if issue is not None:
            self._lane_stream_delta(issue, LOG_REASONING, delta)
            return
        if not delta:
            return
        if not self._stream.streamed_reasoning:
            self._flush_partial()
            self._record_reasoning_marker()
            self._stream.streamed_reasoning = True
        self._stream_into(LOG_REASONING, delta)

    def stream_message(self, delta: str, issue: int | str | None = None) -> None:
        """Fold a message delta into the Log and tap the working marker.

        Serial path (``issue is None``): two jobs (issue #25 + #34) — the delta
        builds the assistant-message lines of the per-issue Log. Active-issue
        attribution arrives separately as ``wrapper.issue.activated``.

        Parallel mode (``issue`` set, issue #66): the delta is assembled into
        that Lane's own Log with **no** marker scan — the runner's deterministic
        Lane-to-issue assignment already names the attribution, so the
        ``<working issue=N>`` marker is redundant.
        """
        if issue is not None:
            self._lane_stream_delta(issue, LOG_MESSAGE, delta)
            return
        if delta:
            self._stream.streamed_message = True
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

    def _local_timestamp(self, value: Any) -> datetime | None:
        """Parse one UTC Event timestamp into the Run's sampled local zone."""
        parsed = _parse_utc_timestamp(value)
        zone = self.started_wall.tzinfo if self.started_wall is not None else None
        if parsed is not None and zone is not None:
            return parsed.astimezone(zone)
        return parsed

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
            st: _StreamState | None = self._stream
        else:
            key = self._normalize_ref(ref)
            committed = self._logs.get(key) or ()
            # A Lane (issue #66) surfaces its own open line; the serial active
            # issue surfaces the shared serial partial; any other issue has no
            # in-flight line of its own.
            if key in self._lane_streams:
                st = self._lane_streams[key]
            elif key == self.active_ref:
                st = self._stream
            else:
                st = None
        lines = list(committed)
        if st is not None and st.partial_kind is not None and st.partial_text:
            lines.append(
                LogLine(
                    kind=st.partial_kind,
                    text=st.partial_text,
                    timestamp=st.partial_started,
                )
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
        return self._logs.setdefault(self.active_ref, deque(maxlen=_LOG_TAIL_LINES))

    # -- per-issue consumption (issue #36) ---------------------------------

    def _record_usage(self, model: Any, tokens_in: Any, tokens_out: Any) -> None:
        """Attribute one ``usage.tokens`` event to the Active issue's tally.

        While an issue is active the tokens accrue to its own entry (summing
        across every iteration that worked it). Before the iteration's working
        marker is known they accrue to the pending buckets and are flushed onto
        the active issue on :meth:`_activate` — the consumption analogue of the
        Log's pending pre-marker buffer, so the late ``Closes #N`` /
        single-member-pool backstop attributes the whole iteration's usage too.
        """
        tin = max(0, _coerce_int(tokens_in, 0))
        tout = max(0, _coerce_int(tokens_out, 0))
        name = str(model) if model else None
        if self.active_ref is not None:
            entry = self.ledger.get(self.active_ref)
            if entry is not None:
                self._accrue_usage(entry, name, tin, tout)
                entry.usage_observed = True
            return
        # Pre-marker: hold until the active issue is known (flushed in _activate).
        self._pending_usage.add(name, tin, tout)
        self._pending_usage_observed = True

    @staticmethod
    def _accrue_usage(
        entry: IssueLedgerEntry, model: str | None, tokens_in: int, tokens_out: int
    ) -> None:
        """Fold a token sample into ``entry``'s tally via the shared rule.

        Delegates to :meth:`UsageTally.add` (issues #39/#41) — *first non-None
        model wins; tokens sum* — so the per-issue cost basis matches the
        run-level Summary's per-iteration basis, keeping the two reconcilable by
        construction rather than by a duplicated rule.
        """
        entry.usage.add(model, tokens_in, tokens_out)

    def _flush_pending_usage(self, entry: IssueLedgerEntry) -> None:
        """Drain the pending pre-marker token tally onto ``entry`` and reset.

        Called from :meth:`_activate` once the active issue is known; folds the
        pending tally in via :meth:`UsageTally.merge` (the same shared accrual
        rule). After the first activation the buffer is empty, so merging it is a
        no-op — a later same-iteration switch of active issue leaves the
        pre-marker usage with the first one.
        """
        entry.usage.merge(self._pending_usage)
        entry.usage_observed = entry.usage_observed or self._pending_usage_observed
        self._pending_usage = UsageTally()
        self._pending_usage_observed = False

    # -- streaming-assembly cores (parametrized on a stream state + a Log
    #    buffer provider so serial and each Lane reuse one implementation) -----

    def _flush(self, st: _StreamState, provider: Callable[[], deque[LogLine]]) -> None:
        """Commit ``st``'s open (newline-less) streamed line, if any, and reset."""
        if st.partial_kind is None:
            return
        if st.partial_text != "":
            provider().append(
                LogLine(
                    kind=st.partial_kind,
                    text=st.partial_text,
                    timestamp=st.partial_started,
                )
            )
        st.partial_kind = None
        st.partial_text = ""
        st.partial_started = None

    def _stream_delta(
        self,
        st: _StreamState,
        provider: Callable[[], deque[LogLine]],
        kind: str,
        delta: str,
    ) -> None:
        """Append a streamed delta to ``st``, committing each completed line.

        A switch of stream kind (reasoning <-> message) flushes ``st``'s open
        partial first, so the two streams never glue onto one line. Completed
        lines land in the buffer ``provider`` returns (the serial active issue's
        Log / pending buffer, or a Lane's own Log). Each line is stamped (issue
        #37) with the wall clock from when its *open* line began; lines that both
        begin and end inside this one delta share this delta's sample.
        """
        if st.partial_kind is not None and st.partial_kind != kind:
            self._flush(st, provider)
        now = self._wall_clock()
        if st.partial_text == "":
            st.partial_started = now
        st.partial_kind = kind
        st.partial_text += str(delta)
        if "\n" in st.partial_text:
            buf = provider()
            first = True
            while "\n" in st.partial_text:
                line, st.partial_text = st.partial_text.split("\n", 1)
                buf.append(
                    LogLine(
                        kind=kind,
                        text=line,
                        timestamp=st.partial_started if first else now,
                    )
                )
                first = False
            st.partial_started = now if st.partial_text else None

    def _emit_block(
        self,
        provider: Callable[[], deque[LogLine]],
        kind: str,
        content: Any,
    ) -> None:
        """Append a whole (non-streamed) reasoning/message block as lines."""
        if not isinstance(content, str) or content == "":
            return
        now = self._wall_clock()
        buf = provider()
        for line in content.split("\n"):
            buf.append(LogLine(kind=kind, text=line, timestamp=now))

    def _emit_event_line(
        self,
        st: _StreamState,
        provider: Callable[[], deque[LogLine]],
        text: str,
    ) -> None:
        """Append a key structured-event line (flushing ``st``'s open line)."""
        if not text:
            return
        self._flush(st, provider)
        provider().append(
            LogLine(kind=LOG_EVENT, text=text, timestamp=self._wall_clock())
        )

    def _emit_reasoning_marker(self, provider: Callable[[], deque[LogLine]]) -> None:
        """Open a reasoning block with a stamped ``✻ Thinking:`` marker (#37)."""
        provider().append(
            LogLine(
                kind=LOG_REASONING,
                text=_THINKING_MARKER,
                timestamp=self._wall_clock(),
            )
        )

    def _finalize_reasoning_into(
        self,
        st: _StreamState,
        provider: Callable[[], deque[LogLine]],
        content: Any,
    ) -> None:
        """Finalise ``st``'s reasoning block: close the streamed line, else add it.

        A streamed block already opened with its ``✻ Thinking:`` marker on the
        first delta, so finalising only clears the per-block flag. A non-streamed
        block (deltas absent) opens its marker here, before the block's lines.
        """
        self._flush(st, provider)
        if st.streamed_reasoning:
            st.streamed_reasoning = False
            return
        if isinstance(content, str) and content != "":
            self._emit_reasoning_marker(provider)
            self._emit_block(provider, LOG_REASONING, content)

    def _finalize_message_into(
        self,
        st: _StreamState,
        provider: Callable[[], deque[LogLine]],
        content: Any,
    ) -> None:
        """Finalise ``st``'s message block: close the streamed line, else add it."""
        self._flush(st, provider)
        if st.streamed_message:
            st.streamed_message = False
            return
        self._emit_block(provider, LOG_MESSAGE, content)

    # -- serial streaming wrappers (bind the shared serial stream + buffer) ----

    def _stream_into(self, kind: str, delta: str) -> None:
        """Serial: append a streamed delta to the shared serial stream state."""
        self._stream_delta(self._stream, self._commit_buffer, kind, delta)

    def _flush_partial(self) -> None:
        """Serial: commit the shared serial stream's open line, if any."""
        self._flush(self._stream, self._commit_buffer)

    def _append_block(self, kind: str, content: Any) -> None:
        """Serial: append a whole (non-streamed) block to the serial buffer."""
        self._emit_block(self._commit_buffer, kind, content)

    def _record_event_line(self, text: str) -> None:
        """Serial: append a key structured-event line to the serial buffer."""
        self._emit_event_line(self._stream, self._commit_buffer, text)

    def _record_reasoning_marker(self) -> None:
        """Serial: open a reasoning block on the serial buffer."""
        self._emit_reasoning_marker(self._commit_buffer)

    def _finalize_reasoning(self, content: Any) -> None:
        """Serial: finalise the shared serial stream's reasoning block."""
        self._finalize_reasoning_into(self._stream, self._commit_buffer, content)

    def _finalize_message(self, content: Any) -> None:
        """Serial: finalise the shared serial stream's message block."""
        self._finalize_message_into(self._stream, self._commit_buffer, content)

    # -- multi-active Lane streaming (issue #66, ADR-0008) ------------------

    def _lane_buffer(self, key: int | str) -> deque[LogLine]:
        """A Lane's own accumulating, bounded Log buffer (get-or-create).

        Keyed by the (already-normalized) issue ref in the shared per-issue
        :attr:`_logs`, so a Lane's Log accumulates across the Wave — and across
        Waves that revisit the issue — exactly like the serial per-issue Log,
        just reached by explicit attribution rather than the active-issue pivot.
        """
        return self._logs.setdefault(key, deque(maxlen=_LOG_TAIL_LINES))

    def _lane_stream_state(self, key: int | str) -> _StreamState:
        """A Lane's own streaming-assembly scratch (get-or-create)."""
        return self._lane_streams.setdefault(key, _StreamState())

    def _lane_provider(self, key: int | str) -> Callable[[], deque[LogLine]]:
        """A zero-arg provider of a Lane's stable Log buffer for the cores."""
        buf = self._lane_buffer(key)
        return lambda: buf

    def _lane_touch(
        self,
        key: int | str,
        now: float,
        *,
        started_wall: datetime | None = None,
    ) -> None:
        """Activate a Lane's ledger entry **without** disturbing sibling Lanes.

        Unlike :meth:`_activate` (one-active-per-iteration, which parks the
        previous active issue and moves ``active_ref``), a Lane activation
        leaves every other Lane active and never touches ``active_ref`` — the
        serial single-active header signal stays ``None`` under a pure Wave. It
        sets the entry's ``started_at`` / ``started_wall`` / ``waiting_duration``
        once and opens an ``active_since`` stint once; a Lane already at a
        terminal status this run is left untouched (a late delta never
        resurrects a closed Lane).
        """
        self._iter_lane_refs.add(key)
        entry = self.ledger.get(key)
        if entry is None:
            entry = IssueLedgerEntry(
                ref=key, first_seen_at=now, first_seen_iter=self.iteration
            )
            self.ledger[key] = entry
        if entry.status in (
            STATUS_CLOSED,
            STATUS_ADVANCED,
            STATUS_NO_PROGRESS,
            STATUS_GONE,
        ):
            return
        if entry.started_at is None:
            entry.started_at = now
            entry.started_wall = started_wall or self._wall_at(now)
            entry.waiting_duration = max(0.0, now - entry.first_seen_at)
        if entry.active_since is None:
            entry.active_since = now
        entry.status = STATUS_ACTIVE

    def _lane_stream_delta(self, ref: int | str, kind: str, delta: str) -> None:
        """Assemble a Lane's streamed reasoning/message delta into its own Log."""
        if not delta:
            return
        key = self._normalize_ref(ref)
        self._lane_touch(key, self._monotonic())
        st = self._lane_stream_state(key)
        provider = self._lane_provider(key)
        if kind == LOG_REASONING:
            if not st.streamed_reasoning:
                self._flush(st, provider)
                self._emit_reasoning_marker(provider)
                st.streamed_reasoning = True
        else:
            st.streamed_message = True
        self._stream_delta(st, provider, kind, delta)

    def _lane_close(self, key: int | str, now: float, *, status: str) -> None:
        """Record a Lane's terminal closure (closed / advanced), folding its timer."""
        entry = self.ledger.get(key)
        if entry is None:
            entry = IssueLedgerEntry(
                ref=key, first_seen_at=now, first_seen_iter=self.iteration
            )
            self.ledger[key] = entry
        if entry.active_since is not None:
            entry.active_duration += max(0.0, now - entry.active_since)
            entry.active_since = None
        entry.status = status
        entry.ended_at = now

    def _render_lane_event(
        self, etype: str, issue: int | str, event: Mapping[str, Any], now: float
    ) -> None:
        """Fold one runner-stamped Lane event into that Lane's own view (#66).

        The multi-active analogue of the serial dispatch in :meth:`render`: tool
        calls, commits, checkpoints, closures, the final reasoning/message
        blocks, and token usage all land in the Lane's *own* Log / timer /
        Consumption keyed by ``issue`` — no ``active_ref`` pivot, no
        ``<working issue=N>`` marker scan (attribution is explicit).
        """
        key = self._normalize_ref(issue)
        self._lane_touch(key, now)
        st = self._lane_stream_state(key)
        provider = self._lane_provider(key)
        if etype == _TOOL_CALL:
            self._emit_event_line(st, provider, _log_tool_text(event))
        elif etype == _COMMIT_RECORDED:
            self._lane_commits[key] = self._lane_commits.get(key, 0) + 1
            self._emit_event_line(st, provider, _log_commit_text(event))
        elif etype == _CHECKPOINT_RECORDED:
            self._emit_event_line(st, provider, _log_checkpoint_text(event))
        elif etype == _AUTO_CLOSE:
            self._lane_close(key, now, status=STATUS_CLOSED)
            self._emit_event_line(st, provider, _log_auto_close_text(event))
        elif etype == _PR_ADVANCED:
            self._lane_close(key, now, status=STATUS_ADVANCED)
            self._emit_event_line(st, provider, _log_pr_advanced_text(event))
        elif etype == _ASSISTANT_REASONING:
            self._finalize_reasoning_into(st, provider, event.get("content"))
        elif etype == _ASSISTANT_MESSAGE:
            self._finalize_message_into(st, provider, event.get("content"))
        elif etype == _AGENT_OUTPUT:
            self._emit_block(provider, LOG_UNCLASSIFIED, event.get("text"))
        elif etype == _USAGE_TOKENS:
            entry = self.ledger.get(key)
            if entry is not None:
                model = event.get("model")
                self._accrue_usage(
                    entry,
                    str(model) if model else None,
                    max(0, _coerce_int(event.get("input"), 0)),
                    max(0, _coerce_int(event.get("output"), 0)),
                )
                entry.usage_observed = True

    # -- internals ----------------------------------------------------------

    def _mark_started(self) -> None:
        if self._started_monotonic is None:
            self._started_monotonic = self._monotonic()
            self.started_wall = self._wall_clock()

    def _mark_ended(self) -> None:
        self.ended = True
        if self._ended_monotonic is None and self._started_monotonic is not None:
            self._ended_monotonic = self._monotonic()
        # Freeze every still-live issue's timer on the final frame (a Stop can
        # land mid-iteration). In serial mode only the ``active_ref`` entry ever
        # carries an open ``active_since`` (siblings are parked as QUEUED), so
        # this folds exactly that one — unchanged; under a Parallel Wave (issue
        # #66) it folds every concurrently-active Lane. ``active_ref`` is left
        # as-is so the serial header still shows what was active at run end.
        at = self._ended_monotonic
        if at is None:
            at = self._monotonic()
        for entry in self.ledger.values():
            if entry.active_since is not None:
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
        self._authoritative_binding = False
        self._msg_buffer = ""
        self.context_window = None
        self.peak_context_window = None
        # Per-issue Logs (and per-issue token tallies) ACCUMULATE across
        # iterations (issues #34 / #36), so they are never cleared here. Only the
        # per-iteration streaming scratch resets: the pending pre-activation
        # buffer and token usage, the serial open streamed line, and
        # (issue #66) the per-Lane streaming scratch + per-Lane commit tally. Any
        # orphan pre-activation output / usage from an iteration that never
        # identified an active issue is discarded here (it lives on in the JSONL
        # replay log / the run-level Summary).
        self._pending.clear()
        self._pending_usage = UsageTally()
        self._pending_usage_observed = False
        self._stream = _StreamState()
        self._lane_streams = {}
        self._lane_commits = {}
        self._iter_lane_refs = set()

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
        """Project legacy traces that lack an authoritative activation event."""
        if self._authoritative_binding or not text:
            return
        self._msg_buffer = (self._msg_buffer + str(text))[-_MARKER_BUFFER_CHARS:]
        match = _WORKING_MARKER_RE.search(self._msg_buffer)
        if match is None:
            return
        self._msg_buffer = self._msg_buffer[match.end() :]
        self._activate(int(match.group(1)), since=self._monotonic())

    def _activate(
        self,
        ref: int | str,
        *,
        since: float,
        started_wall: datetime | None = None,
    ) -> None:
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
            if self._authoritative_binding:
                return
            self._deactivate(self.active_ref, at=since, status=STATUS_QUEUED)
        if entry.started_at is None:
            entry.started_at = since
            entry.started_wall = started_wall or self._wall_at(since)
            entry.waiting_duration = max(0.0, since - entry.first_seen_at)
        if entry.active_since is None:
            entry.active_since = since
        entry.status = STATUS_ACTIVE
        self.active_ref = ref
        # Attribute this iteration's pre-activation output (issue #34): flush the
        # pending buffer into the now-active issue's own accumulating Log, then
        # clear it so subsequent output lands directly in the issue's buffer.
        buf = self._logs.setdefault(ref, deque(maxlen=_LOG_TAIL_LINES))
        if self._pending:
            buf.extend(self._pending)
            self._pending.clear()
        # Likewise attribute this iteration's pre-activation token usage (issue #36)
        # to the now-active issue, then reset the pending buckets.
        self._flush_pending_usage(entry)

    def _record_closure(self, ref: Any, now: float, *, status: str) -> None:
        """Record an authoritative commit-time outcome (closed / advanced).

        Active-issue attribution is owned by ``wrapper.issue.activated``.
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
        if self.active_ref is None and not self._authoritative_binding:
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
        """Reconcile each still-active issue's terminal status at ``iteration.end``.

        Serial (issue #25): a closure already set ``closed`` / ``advanced``;
        otherwise the single active issue with commits is ``advanced`` and one
        without is ``no-progress`` (a strike).

        Parallel Wave (issue #66): each concurrently-active **Lane** that never
        reached a terminal closure is reconciled independently — ``advanced`` if
        it landed at least one commit this Wave (its *own* :attr:`_lane_commits`
        count), else ``no-progress`` — and its live timer folded. Lanes set no
        ``active_ref`` and do not touch the serial ``_iter_commits``, so the
        serial block below is a no-op under a pure Wave and the Lane pass is a
        no-op under a serial iteration.
        """
        # Commit any open serial streamed line into the active issue's Log (or
        # the pending buffer) before the active issue is parked, so the last
        # in-progress line is retained in the per-issue Log (issue #34).
        self._flush_partial()
        self.context_window = None
        ref = self.active_ref
        if (
            ref is None
            and not self._authoritative_binding
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
        # Multi-active Lane finalisation (issue #66): fold + reconcile every Lane
        # still active this Wave. In serial mode no non-active_ref entry carries
        # an open ``active_since`` (siblings are parked), so this loop never
        # fires — the serial path is byte-for-byte unchanged.
        for key, entry in list(self.ledger.items()):
            if entry.status != STATUS_ACTIVE or entry.active_since is None:
                continue
            lane_stream = self._lane_streams.get(key)
            if lane_stream is not None:
                self._flush(lane_stream, self._lane_provider(key))
            entry.active_duration += max(0.0, now - entry.active_since)
            entry.active_since = None
            if self._lane_commits.get(key, 0) > 0:
                entry.status = STATUS_ADVANCED
                entry.ended_at = now
            else:
                entry.status = STATUS_NO_PROGRESS
        self._iter_pool = []
        self._iter_commits = 0
        self._iter_strike = False

    def _record_normalized_contributions(self, event: Mapping[str, Any]) -> None:
        """Project authoritative finalized issue rows from Iteration end."""
        issues = event.get("issues")
        if not isinstance(issues, list):
            return
        iter_num = _optional_nonnegative_int(event.get("iter"))
        for payload in issues:
            if not isinstance(payload, Mapping) or payload.get("issue") is None:
                continue
            key = self._normalize_ref(payload["issue"])
            entry = self.ledger.get(key)
            if entry is None:
                entry = IssueLedgerEntry(
                    ref=key,
                    first_seen_at=self._monotonic(),
                    first_seen_iter=iter_num or self.iteration,
                )
                self.ledger[key] = entry
            consumption = payload.get("consumption")
            usage = UsageTally()
            if isinstance(consumption, Mapping):
                model = consumption.get("model")
                usage.add(
                    str(model) if isinstance(model, str) and model else None,
                    max(0, _coerce_int(consumption.get("tokens_in"), 0)),
                    max(0, _coerce_int(consumption.get("tokens_out"), 0)),
                )
            cost = payload.get("cost_usd")
            cost_usd = float(cost) if isinstance(cost, (int, float)) else None
            is_lane = key in self._iter_lane_refs
            contribution = IssueContribution(
                kind="lane" if is_lane else "iteration",
                iteration=None if is_lane else iter_num,
                lane=key if is_lane else None,
                status=str(payload.get("status") or STATUS_NO_PROGRESS),
                active_seconds=max(
                    0.0, _coerce_float(payload.get("active_seconds"), 0.0)
                ),
                usage=usage,
                cost_usd=cost_usd,
                peak_context_window=_context_window_snapshot(
                    payload.get("peak_context_window")
                ),
            )
            entry.contributions.append(contribution)
            entry.usage_observed = True
            entry.status = contribution.status
            entry.active_duration = max(
                0.0,
                _coerce_float(
                    payload.get("cumulative_active_seconds"),
                    entry.active_duration,
                ),
            )
            started = self._local_timestamp(payload.get("first_started_at"))
            if started is not None:
                entry.started_wall = started
            entry.closed_wall = self._local_timestamp(payload.get("closed_at"))
            elapsed = payload.get("issue_elapsed_seconds")
            entry.issue_elapsed_seconds = (
                max(0.0, float(elapsed)) if isinstance(elapsed, (int, float)) else None
            )
            entry.usage = UsageTally()
            for item in entry.contributions:
                entry.usage.merge(item.usage)
            entry.normalized_cost_usd = (
                sum(
                    item.cost_usd
                    for item in entry.contributions
                    if item.cost_usd is not None
                )
                if all(item.cost_usd is not None for item in entry.contributions)
                else None
            )

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


def _context_window_snapshot(
    event: Any,
) -> ContextWindowSnapshot | None:
    if not isinstance(event, Mapping):
        return None
    current = _optional_nonnegative_int(event.get("current_tokens"))
    if current is None:
        return None
    return ContextWindowSnapshot(
        current_tokens=current,
        token_limit=_optional_positive_int(event.get("token_limit")),
        effective_target_tokens=_optional_positive_int(
            event.get("effective_target_tokens")
        ),
        effective_ceiling_tokens=_optional_positive_int(
            event.get("effective_ceiling_tokens")
        ),
    )


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_int(value: Any, fallback: int) -> int:
    """Best-effort int coercion: malformed payloads keep the prior value."""
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(value: Any, fallback: float) -> float:
    if value is None or isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _optional_positive_int(value: Any) -> int | None:
    number = _optional_nonnegative_int(value)
    return number if number is not None and number > 0 else None


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


@dataclass(frozen=True)
class LogLineView:
    """One :class:`LogLine` projected for display (issue #37).

    A pure, Textual-free row the Log view renders directly: ``text`` and ``dim``
    come straight from the line, and ``stamp`` is its 12-hour AM/PM wall-clock
    time **only on the first line of each second** — repeats within the same
    second carry a blank ``stamp`` so the timestamp column stays uncluttered
    (mirrors how a long burst of output reads). A line with no timestamp renders
    a blank stamp.
    """

    stamp: str
    text: str
    dim: bool


def log_line_views(lines: Iterable[LogLine]) -> list[LogLineView]:
    """Project Log lines into display rows, collapsing same-second stamps (#37).

    Each line keeps the wall clock captured when it was appended; here that is
    rendered (:func:`format_wall_clock`, 12-hour AM/PM) for the **first** line of
    each distinct second only, so a burst of lines sharing a second shows the
    stamp once. Lines without a timestamp render a blank stamp. The wall-clock
    rule: stamps are 12-hour AM/PM; *durations* (elsewhere) stay ``H:MM:SS``.
    """
    views: list[LogLineView] = []
    prev_second: datetime | None = None
    for line in lines:
        when = line.timestamp
        if when is None:
            stamp = ""
        else:
            second = when.replace(microsecond=0)
            stamp = "" if second == prev_second else format_wall_clock(when)
            prev_second = second
        views.append(LogLineView(stamp=stamp, text=line.text, dim=line.dim))
    return views


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

    started = format_wall_clock(state.started_wall)
    elapsed = _format_elapsed(state.elapsed_seconds(now))
    context_fill = format_context_fill(state.context_window)

    if state.active_ref is not None:
        # Serial single-active header — byte-for-byte unchanged.
        active = f"#{state.active_ref} {_format_elapsed(state.active_seconds(now))}"
    else:
        # Parallel Wave (issue #66): no serial ``active_ref``, so list the
        # concurrently-active Lanes. A single active Lane still shows its live
        # timer; several show their refs. No active issue → the em-dash, exactly
        # as the serial between-iterations header reads.
        base = now if now is not None else state._monotonic()
        actives = [
            entry
            for entry in state.ledger.values()
            if entry.status == STATUS_ACTIVE and entry.active_since is not None
        ]
        if not actives:
            active = "—"
        elif len(actives) == 1:
            entry = actives[0]
            active = f"#{entry.ref} {_format_elapsed(entry.active_seconds(base))}"
        else:
            active = " ".join(f"#{entry.ref}" for entry in actives)

    return (
        f"git-loopy  run {run_id}"
        f"  •  model {model}"
        f"  •  start {started}  elapsed {elapsed}"
        f"  •  iter {state.iteration}"
        f"  •  active {active}"
        f"  •  context {context_fill}"
        f"  •  {state.status}"
        f"  •  strikes {state.strikes}/{state.max_strikes}"
    )


def format_context_fill(snapshot: ContextWindowSnapshot | None) -> str:
    """Render the Header's compact, current-Iteration **Context fill**."""
    if snapshot is None:
        return "—"
    current = f"{snapshot.current_tokens:,}"
    limit = snapshot.token_limit
    if limit is None:
        return f"{current}/—"

    fraction = snapshot.current_tokens / limit
    percentage = f"{fraction:.0%}"
    filled = min(10, max(0, int(fraction * 10)))
    bar = f"[{'█' * filled}{'░' * (10 - filled)}]"
    parts = [f"{current}/{limit:,}", percentage, bar]

    target = snapshot.effective_target_tokens
    if target is not None:
        label = "TARGET" if snapshot.current_tokens >= target else "target"
        parts.append(f"{label} {target:,}")
    ceiling = snapshot.effective_ceiling_tokens
    if ceiling is not None:
        label = "CEILING" if snapshot.current_tokens >= ceiling else "ceiling"
        parts.append(f"{label} {ceiling:,}")
    return " ".join(parts)


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
    Queue's content and ordering are unit-testable without a TTY. The canonical
    columns are **Issue | Status | Started | Active | Closed | Iters | Tokens in |
    Tokens out | Cost**. ``Closed`` and Issue elapsed exist only for authoritative
    source closure, and ``Iters`` is the exact number of finalized Iteration or
    Lane contribution rows retained for the drill-in. Live timers and Consumption
    remain responsive before finalization; normalized Iteration-end issue rows
    become the authority once present.
    """

    ref: int | str
    status: str
    started_wall: datetime | None
    active_seconds: float
    is_active: bool
    usage: UsageTally
    usage_observed: bool
    closed_wall: datetime | None
    iteration_count: int
    cost_usd: float | None

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
    summing across every iteration that worked it.     It also carries the issue's accumulated **Consumption** and normalized Cost
    across the same contribution rows. Before the first finalized contribution,
    live observed usage remains available while an absent sample stays unknown.
    """
    base = now if now is not None else state._monotonic()
    rows: list[QueueRow] = []
    for ref, entry in state.ledger.items():
        # One row per **active** issue lights as active (issue #66): under a
        # Parallel Wave every concurrently-active Lane shows its own live timer.
        # Serial-equivalent — serial has exactly one ACTIVE entry, which is the
        # ``active_ref`` — so the single-active Dashboard is unchanged.
        is_active = entry.status == STATUS_ACTIVE
        rows.append(
            QueueRow(
                ref=ref,
                status=entry.status,
                started_wall=entry.started_wall,
                active_seconds=entry.active_seconds(base),
                is_active=is_active,
                usage=entry.usage,
                usage_observed=entry.usage_observed,
                closed_wall=entry.closed_wall,
                iteration_count=len(entry.contributions),
                cost_usd=entry.normalized_cost_usd,
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
    started_wall: datetime | None
    closed_wall: datetime | None
    issue_elapsed_seconds: float | None
    contributions: tuple[IssueContribution, ...]

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

    ``is_active`` is true whenever this is an issue being worked *now* (its
    status is ``active``) — the signal the Log view uses to stream the issue
    live versus showing its retained tail. Under a Parallel Wave (issue #66)
    that holds for every concurrently-active **Lane**; serial has exactly one
    active issue (the ``active_ref``), so the single-active drill-in is
    unchanged.
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
            started_wall=None,
            closed_wall=None,
            issue_elapsed_seconds=None,
            contributions=(),
        )
    if entry.waiting_duration is not None:
        waiting = entry.waiting_duration
    else:
        waiting = max(0.0, base - entry.first_seen_at)
    return IssueDetail(
        ref=key,
        status=entry.status,
        is_active=entry.status == STATUS_ACTIVE,
        active_seconds=entry.active_seconds(base),
        waiting_seconds=waiting,
        first_seen_iter=entry.first_seen_iter,
        started_wall=entry.started_wall,
        closed_wall=entry.closed_wall,
        issue_elapsed_seconds=entry.issue_elapsed_seconds,
        contributions=tuple(entry.contributions),
    )


def format_detail_header(detail: IssueDetail) -> str:
    """Compose the single-line drill-in header from an :class:`IssueDetail`.

    Pure and Textual-free (mirrors :func:`format_header`) so the detail header's
    closure-only timing and contribution count are testable without a TTY.
    """
    return (
        f"{detail.label}"
        f"  •  status {detail.status}"
        f"  •  started {format_wall_clock(detail.started_wall)}"
        f"  •  active {format_duration(detail.active_seconds)}"
        f"  •  closed {format_wall_clock(detail.closed_wall)}"
        f"  •  issue elapsed "
        f"{format_duration(detail.issue_elapsed_seconds) if detail.issue_elapsed_seconds is not None else '—'}"
        f"  •  iters {len(detail.contributions)}"
        f"  •  waiting {format_duration(detail.waiting_seconds)}"
        f"  •  first seen iter {detail.first_seen_iter}"
    )


def format_activity_header(state: LiveRunState) -> str:
    """Compose the **Activity** band's compact one-line header (issue #69).

    Names the current serial ``active_ref`` — e.g. ``Activity · #123`` — so the
    band stays attributable when the active row has scrolled out of a long
    Queue. It follows ``active_ref`` **independent of the Queue cursor** (the
    band is an active-only glance, not a projection of the selected row). With
    no active issue — before the iteration's working marker, or in a parallel
    **Wave** where the serial ``active_ref`` is ``None`` (ADR-0011: serial scope
    only for v1) — it reads simply ``Activity``.

    Pure and Textual-free (mirrors :func:`format_header` /
    :func:`format_detail_header`) so the header's *content* is unit-testable
    without a TTY.
    """
    if state.active_ref is not None:
        return f"Activity · #{state.active_ref}"
    return "Activity"
