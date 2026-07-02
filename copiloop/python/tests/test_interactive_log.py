"""Tests for the pure **per-issue Log** + **drill-in detail** projections in
``copiloop.interactive.state`` (issue #34, ADR-0003).

Each issue keeps **its own** Log buffer that **accumulates across every
iteration** that worked it and is **bounded per issue** (a generous ring-buffer
tail), replacing the single iteration-scoped, active-only transcript of issue
#27. Output produced before the iteration's working marker is attributed to the
active issue once it is known, and per-issue buffers stay isolated; the full
record stays in the always-on JSONL replay log on disk.

These tests pin that *content* without a TTY (mirroring the ``queue_rows`` /
``format_header`` seams); the Pilot test in ``test_interactive_app.py`` covers
the widget rendering and the active-vs-historical difference on screen.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from copiloop import events as events_module
from copiloop.interactive import state as state_module
from copiloop.interactive.state import (
    LOG_EVENT,
    LOG_MESSAGE,
    LOG_REASONING,
    STATUS_ACTIVE,
    STATUS_GONE,
    STATUS_QUEUED,
    LiveRunState,
    LogLine,
    LogLineView,
    format_detail_header,
    issue_detail,
    log_line_views,
)


class _FakeClock:
    """A controllable monotonic clock: ``advance`` then call to read."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, by: float) -> None:
        self.value += by


_FIXED_WALL = datetime(2026, 6, 21, 12, 0, 0)


class _FakeWallClock:
    """A controllable wall clock: ``advance`` (seconds) then call to read."""

    def __init__(self, start: datetime = _FIXED_WALL) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _make_state(
    clock: _FakeClock | None = None,
    wall: _FakeWallClock | None = None,
) -> LiveRunState:
    return LiveRunState(
        run_id="01LOG",
        model="claude-opus-4.8",
        reasoning_effort="max",
        monotonic=clock if clock is not None else _FakeClock(),
        wall_clock=wall if wall is not None else (lambda: _FIXED_WALL),
    )


def _texts(state: LiveRunState, ref: int | str | None = None) -> list[str]:
    return [line.text for line in state.log(ref)]


def _activate(state: LiveRunState, ref: int, *, iteration: int, pool=None) -> None:
    """Open an iteration and light up ``ref`` as the active issue via its marker.

    The marker is given its own trailing newline so it commits as a discrete
    line rather than gluing onto the next message.
    """
    state.render(
        {"type": events_module.WRAPPER_ITERATION_START, "iter": iteration}
    )
    state.render(
        {
            "type": events_module.WRAPPER_AFK_READY_COLLECTED,
            "issues": pool if pool is not None else [ref],
        }
    )
    state.stream_message(f"<working issue={ref}>\n")


# ---------------------------------------------------------------------------
# Streaming deltas -> interleaved lines (the live current tail)
# ---------------------------------------------------------------------------


def test_streamed_reasoning_is_dimmed_and_message_is_plain() -> None:
    state = _make_state()
    state.stream_reasoning("weighing options\n")
    state.stream_message("Hello world\n")
    rendered = [(line.kind, line.text, line.dim) for line in state.log()]
    assert (LOG_REASONING, "weighing options", True) in rendered
    assert (LOG_MESSAGE, "Hello world", False) in rendered


def test_open_partial_line_is_visible_before_a_newline() -> None:
    """Output appears as the model produces it, not only on a terminating ``\\n``."""
    state = _make_state()
    state.stream_message("partial without newline")
    last = state.log()[-1]
    assert last.kind == LOG_MESSAGE
    assert last.text == "partial without newline"
    # Continuing the same logical line replaces the provisional partial.
    state.stream_message(" continued\n")
    assert _texts(state)[-1] == "partial without newline continued"


def test_switching_stream_kind_flushes_the_open_partial_first() -> None:
    state = _make_state()
    state.stream_reasoning("thinking")  # no newline -> open reasoning partial
    state.stream_message("answer\n")
    texts = _texts(state)
    assert "thinking" in texts  # flushed when the message stream began
    assert "answer" in texts
    assert texts.index("thinking") < texts.index("answer")


# ---------------------------------------------------------------------------
# Key structured events -> interleaved event lines (faithful to the printer)
# ---------------------------------------------------------------------------


def test_tool_call_appends_event_line_and_flushes_open_partial() -> None:
    state = _make_state()
    state.stream_reasoning("about to call a tool")  # open partial, no newline
    state.render(
        {
            "type": events_module.TOOL_CALL,
            "tool_name": "bash",
            "arguments": {"command": "ls"},
        }
    )
    lines = state.log()
    texts = [line.text for line in lines]
    assert "about to call a tool" in texts  # partial flushed before the event
    assert "» bash  command=ls" in texts
    event_line = next(line for line in lines if line.text.startswith("» bash"))
    assert event_line.kind == LOG_EVENT
    assert event_line.dim is False


def test_skill_tool_call_renders_the_skill_name() -> None:
    state = _make_state()
    state.render(
        {
            "type": events_module.TOOL_CALL,
            "tool_name": "skill",
            "arguments": {"skill": "tdd"},
        }
    )
    assert "◇ skill tdd" in _texts(state)


def test_commit_auto_close_and_pr_advanced_event_lines() -> None:
    state = _make_state()
    state.render(
        {
            "type": events_module.WRAPPER_COMMIT_RECORDED,
            "sha": "abcdef1234567",
            "subject": "Fix the bug\nbody ignored",
        }
    )
    state.render(
        {
            "type": events_module.WRAPPER_AUTO_CLOSE,
            "issue": 26,
            "sha": "1234567890abc",
        }
    )
    state.render(
        {
            "type": events_module.WRAPPER_PR_ADVANCED,
            "pr": 27,
            "sha": "9876543210def",
        }
    )
    # The auto-close attributes the iteration's work to #26 (the Closes #N
    # backstop), so all three event lines land in #26's own Log.
    texts = _texts(state, 26)
    assert "✓ commit abcdef1234  Fix the bug" in texts
    assert "✓ auto-closed #26  (1234567890)" in texts
    assert "↑ advanced PR #27  (9876543210)" in texts


# ---------------------------------------------------------------------------
# Final assistant.* events: de-dup when streamed, append when not
# ---------------------------------------------------------------------------


def test_final_message_after_streaming_does_not_duplicate() -> None:
    state = _make_state()
    state.stream_message("Hello ")
    state.stream_message("world")  # no newline -> open partial
    state.render(
        {"type": events_module.ASSISTANT_MESSAGE, "content": "Hello world"}
    )
    assert _texts(state).count("Hello world") == 1


def test_final_message_without_streaming_appends_the_block() -> None:
    state = _make_state()
    state.render(
        {"type": events_module.ASSISTANT_MESSAGE, "content": "line one\nline two"}
    )
    lines = state.log()
    assert [line.text for line in lines] == ["line one", "line two"]
    assert all(line.kind == LOG_MESSAGE for line in lines)


def test_final_reasoning_without_streaming_appends_a_dimmed_block() -> None:
    state = _make_state()
    state.render(
        {
            "type": events_module.ASSISTANT_REASONING,
            "content": "considered A\nconsidered B",
        }
    )
    lines = state.log()
    # The block opens with the ✻ Thinking: marker (issue #37), then its lines.
    assert [line.text for line in lines] == [
        "✻ Thinking:",
        "considered A",
        "considered B",
    ]
    assert all(line.kind == LOG_REASONING and line.dim for line in lines)


# ---------------------------------------------------------------------------
# Per-issue buffers: bounded, accumulating, isolated, pre-marker attribution
# ---------------------------------------------------------------------------


def test_log_is_bounded_per_issue() -> None:
    """A long iteration can't grow one issue's Log without limit (acceptance)."""
    state = _make_state()
    _activate(state, 3, iteration=1)
    cap = state_module._LOG_TAIL_LINES
    total = cap + 50
    for i in range(total):
        state.stream_message(f"line {i}\n")
    lines = state.log(3)
    assert len(lines) == cap
    # The newest lines are retained; the oldest (incl. the marker) are dropped.
    assert lines[-1].text == f"line {total - 1}"
    assert lines[0].text == f"line {total - cap}"


def test_log_accumulates_across_iterations() -> None:
    """The same issue worked twice keeps BOTH iterations' lines (issue #34)."""
    state = _make_state()
    _activate(state, 7, iteration=1)
    state.stream_message("iter one line\n")
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    _activate(state, 7, iteration=2)
    state.stream_message("iter two line\n")

    texts = _texts(state, 7)
    assert "iter one line" in texts
    assert "iter two line" in texts
    # Accumulated in time order: the first iteration's line precedes the second.
    assert texts.index("iter one line") < texts.index("iter two line")


def test_per_issue_logs_are_isolated() -> None:
    """Each issue's Log holds only its own lines, never a global stream."""
    state = _make_state()
    _activate(state, 7, iteration=1, pool=[7, 8])
    state.stream_message("seven only\n")
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    _activate(state, 8, iteration=2, pool=[7, 8])
    state.stream_message("eight only\n")

    seven = _texts(state, 7)
    eight = _texts(state, 8)
    assert "seven only" in seven
    assert "eight only" not in seven
    assert "eight only" in eight
    assert "seven only" not in eight


def test_pre_marker_output_is_attributed_to_active_issue_once_known() -> None:
    """Output before the working marker lands in the active issue when known."""
    state = _make_state()
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [9]}
    )
    # Output produced BEFORE the working marker arrives.
    state.stream_reasoning("pre-marker thinking\n")
    state.stream_message("pre-marker message\n")
    # No issue is active yet, so nothing is attributed to #9.
    assert state.log(9) == ()

    # The working marker arrives mid-iteration.
    state.stream_message("<working issue=9>\n")

    # The earlier pre-marker output is now attributed to #9, in time order.
    texts = _texts(state, 9)
    assert "pre-marker thinking" in texts
    assert "pre-marker message" in texts
    assert texts.index("pre-marker thinking") < texts.index("pre-marker message")


def test_historical_issue_log_is_retained_after_deactivation() -> None:
    """A no-longer-active issue keeps its retained Log tail (issue #34)."""
    state = _make_state()
    _activate(state, 5, iteration=1)
    state.stream_message("historical line\n")
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    # #5 is no longer active, but opening its Log still shows its own lines.
    assert state.active_ref is None
    assert "historical line" in _texts(state, 5)


def test_log_for_unworked_issue_is_empty() -> None:
    """A queued-but-never-worked issue has no Log lines (its own, isolated)."""
    state = _make_state()
    _activate(state, 1, iteration=1, pool=[1, 2])
    state.stream_message("one only\n")
    assert state.log(2) == ()


# ---------------------------------------------------------------------------
# Drill-in detail projection
# ---------------------------------------------------------------------------


def _run_with_active(clock: _FakeClock) -> LiveRunState:
    """A run mid-iteration: #26 active (marker), #27 still queued."""
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [26, 27]}
    )
    state.stream_message("<working issue=26>")
    return state


def test_issue_detail_active_issue_is_active_with_ticking_active_time() -> None:
    clock = _FakeClock()
    state = _run_with_active(clock)
    clock.advance(30)
    detail = issue_detail(state, 26)
    assert detail.is_active is True
    assert detail.status == STATUS_ACTIVE
    assert detail.active_seconds == 30
    assert detail.first_seen_iter == 1


def test_issue_detail_non_active_issue_is_not_active() -> None:
    clock = _FakeClock()
    state = _run_with_active(clock)
    clock.advance(30)
    detail = issue_detail(state, 27)
    assert detail.is_active is False
    assert detail.status == STATUS_QUEUED
    assert detail.waiting_seconds == 30


def test_issue_detail_accepts_the_widget_string_row_key() -> None:
    state = _make_state()
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [26]}
    )
    detail = issue_detail(state, "26")  # the DataTable row-key is a str
    assert detail.ref == 26
    assert detail.status == STATUS_QUEUED


def test_issue_detail_unknown_ref_degrades_to_gone() -> None:
    detail = issue_detail(_make_state(), 999)
    assert detail.status == STATUS_GONE
    assert detail.is_active is False


def test_format_detail_header_contains_all_fields() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [42]}
    )
    state.stream_message("<working issue=42>")
    clock.advance(65)  # 1m 5s
    header = format_detail_header(issue_detail(state, 42))
    assert "#42" in header
    assert "status active" in header
    assert "active 0:01:05" in header
    assert "first seen iter 2" in header


# ---------------------------------------------------------------------------
# Timestamps (issue #37): each Log line is stamped at append time
# ---------------------------------------------------------------------------


def test_event_line_is_stamped_with_the_wall_clock_at_append() -> None:
    """A structured-event line carries the wall-clock time it was appended."""
    wall = _FakeWallClock()
    state = _make_state(wall=wall)
    wall.advance(7)  # 12:00:07
    state.render(
        {
            "type": events_module.TOOL_CALL,
            "tool_name": "bash",
            "arguments": {"command": "ls"},
        }
    )
    line = state.log()[-1]
    assert line.text == "» bash  command=ls"
    assert line.timestamp == _FIXED_WALL + timedelta(seconds=7)


def test_streamed_line_is_stamped_when_the_line_begins() -> None:
    """A streamed line keeps the wall clock from when its first delta arrived."""
    wall = _FakeWallClock()
    state = _make_state(wall=wall)
    state.stream_message("hello ")  # open partial begins at 12:00:00
    wall.advance(3)  # the terminating delta arrives later, same logical line
    state.stream_message("world\n")
    line = state.log()[-1]
    assert line.text == "hello world"
    assert line.timestamp == _FIXED_WALL  # stamped at line start, not at commit


def test_open_partial_is_stamped_live() -> None:
    """The provisional (newline-less) partial surfaced by ``log`` is stamped."""
    wall = _FakeWallClock()
    state = _make_state(wall=wall)
    wall.advance(2)
    state.stream_message("still typing")
    line = state.log()[-1]
    assert line.text == "still typing"
    assert line.timestamp == _FIXED_WALL + timedelta(seconds=2)


# ---------------------------------------------------------------------------
# log_line_views (issue #37): 12h stamp on the first line of each second
# ---------------------------------------------------------------------------


def test_log_line_views_render_12_hour_stamp_and_carry_dim() -> None:
    """The first line of a second shows a 12h AM/PM stamp; dim follows the kind."""
    t0 = datetime(2026, 6, 21, 13, 42, 7)
    views = log_line_views(
        [
            LogLine(kind=LOG_REASONING, text="thinking", timestamp=t0),
            LogLine(kind=LOG_MESSAGE, text="answer", timestamp=t0),
        ]
    )
    assert isinstance(views[0], LogLineView)
    assert views[0].stamp == "1:42:07 PM"
    assert (views[0].text, views[0].dim) == ("thinking", True)
    assert (views[1].text, views[1].dim) == ("answer", False)


def test_log_line_views_collapse_repeats_within_the_same_second() -> None:
    """Only the first line in a given second carries the stamp (issue #37)."""
    t0 = datetime(2026, 6, 21, 13, 42, 7)
    views = log_line_views(
        [
            LogLine(kind=LOG_MESSAGE, text="a", timestamp=t0),
            LogLine(kind=LOG_MESSAGE, text="b", timestamp=t0 + timedelta(microseconds=300_000)),
            LogLine(kind=LOG_MESSAGE, text="c", timestamp=t0 + timedelta(seconds=1)),
            LogLine(kind=LOG_MESSAGE, text="d", timestamp=t0 + timedelta(seconds=1, microseconds=1)),
        ]
    )
    assert [v.stamp for v in views] == ["1:42:07 PM", "", "1:42:08 PM", ""]


def test_log_line_views_blank_stamp_when_timestamp_missing() -> None:
    """A line with no timestamp renders a blank stamp (defensive)."""
    views = log_line_views([LogLine(kind=LOG_MESSAGE, text="x", timestamp=None)])
    assert views[0].stamp == ""
    assert views[0].text == "x"


# ---------------------------------------------------------------------------
# Thinking marker (issue #37): a reasoning block opens with ✻ Thinking:
# ---------------------------------------------------------------------------

_THINKING = "✻ Thinking:"


def test_streamed_reasoning_block_opens_with_a_stamped_thinking_marker() -> None:
    wall = _FakeWallClock()
    state = _make_state(wall=wall)
    wall.advance(7)  # 12:00:07
    state.stream_reasoning("weighing options\n")
    lines = state.log()
    assert [line.text for line in lines][:2] == [_THINKING, "weighing options"]
    # The marker is reasoning-styled (dimmed) and stamped at the block open.
    assert lines[0].kind == LOG_REASONING and lines[0].dim
    assert lines[0].timestamp == _FIXED_WALL + timedelta(seconds=7)


def test_non_streamed_reasoning_block_opens_with_a_thinking_marker() -> None:
    state = _make_state()
    state.render(
        {"type": events_module.ASSISTANT_REASONING, "content": "considered A"}
    )
    assert [line.text for line in state.log()] == [_THINKING, "considered A"]


def test_each_reasoning_block_gets_its_own_marker() -> None:
    """A fresh block (after its closing event) re-opens with a new marker."""
    state = _make_state()
    state.stream_reasoning("first\n")
    state.render({"type": events_module.ASSISTANT_REASONING, "content": "first"})
    state.stream_reasoning("second\n")
    assert [line.text for line in state.log()].count(_THINKING) == 2


def test_empty_non_streamed_reasoning_emits_no_marker() -> None:
    """A reasoning event with no content does not leave a dangling marker."""
    state = _make_state()
    state.render({"type": events_module.ASSISTANT_REASONING, "content": ""})
    assert state.log() == ()


def test_thinking_marker_matches_the_line_printer_prefix() -> None:
    """The Log's reasoning marker stays in lockstep with the renderer's prefix."""
    from copiloop.ui import renderer as renderer_module

    assert state_module._THINKING_MARKER == renderer_module._THINKING_PREFIX
    assert state_module._THINKING_MARKER == _THINKING
