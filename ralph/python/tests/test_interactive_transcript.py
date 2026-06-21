"""Tests for the pure **live transcript** + **drill-in detail** projections in
``ralph_afk.interactive.state`` (issue #27).

The drill-in shows, for the *active* issue, an interleaved tail of what the
model is doing — dimmed reasoning, assistant message text, and key structured
events (tool calls, commits, closures) in time order — kept in a bounded
ring-buffer so memory can't grow over a long iteration. For a *non-active*
issue it shows details only.

These tests pin that *content* without a TTY (mirroring the ``queue_rows`` /
``format_header`` seams); the Pilot test in ``test_interactive_app.py`` covers
the widget rendering and the active-vs-non-active difference on screen.
"""

from __future__ import annotations

from datetime import datetime

from ralph_afk import events as events_module
from ralph_afk.interactive import state as state_module
from ralph_afk.interactive.state import (
    STATUS_ACTIVE,
    STATUS_GONE,
    STATUS_QUEUED,
    TRANSCRIPT_EVENT,
    TRANSCRIPT_MESSAGE,
    TRANSCRIPT_REASONING,
    LiveRunState,
    format_detail_header,
    issue_detail,
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


def _make_state(clock: _FakeClock | None = None) -> LiveRunState:
    return LiveRunState(
        run_id="01TRANS",
        model="claude-opus-4.8",
        reasoning_effort="max",
        monotonic=clock if clock is not None else _FakeClock(),
        wall_clock=lambda: _FIXED_WALL,
    )


def _texts(state: LiveRunState) -> list[str]:
    return [line.text for line in state.transcript()]


# ---------------------------------------------------------------------------
# Streaming deltas -> interleaved lines
# ---------------------------------------------------------------------------


def test_streamed_reasoning_is_dimmed_and_message_is_plain() -> None:
    state = _make_state()
    state.stream_reasoning("weighing options\n")
    state.stream_message("Hello world\n")
    rendered = [(line.kind, line.text, line.dim) for line in state.transcript()]
    assert (TRANSCRIPT_REASONING, "weighing options", True) in rendered
    assert (TRANSCRIPT_MESSAGE, "Hello world", False) in rendered


def test_open_partial_line_is_visible_before_a_newline() -> None:
    """Output appears as the model produces it, not only on a terminating ``\\n``."""
    state = _make_state()
    state.stream_message("partial without newline")
    last = state.transcript()[-1]
    assert last.kind == TRANSCRIPT_MESSAGE
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
    lines = state.transcript()
    texts = [line.text for line in lines]
    assert "about to call a tool" in texts  # partial flushed before the event
    assert "» bash  command=ls" in texts
    event_line = next(line for line in lines if line.text.startswith("» bash"))
    assert event_line.kind == TRANSCRIPT_EVENT
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
    texts = _texts(state)
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
    lines = state.transcript()
    assert [line.text for line in lines] == ["line one", "line two"]
    assert all(line.kind == TRANSCRIPT_MESSAGE for line in lines)


def test_final_reasoning_without_streaming_appends_a_dimmed_block() -> None:
    state = _make_state()
    state.render(
        {
            "type": events_module.ASSISTANT_REASONING,
            "content": "considered A\nconsidered B",
        }
    )
    lines = state.transcript()
    assert [line.text for line in lines] == ["considered A", "considered B"]
    assert all(line.kind == TRANSCRIPT_REASONING and line.dim for line in lines)


# ---------------------------------------------------------------------------
# Bounded tail + per-iteration reset
# ---------------------------------------------------------------------------


def test_transcript_is_a_bounded_tail() -> None:
    """A long iteration can't grow the pane without limit (acceptance: memory)."""
    state = _make_state()
    cap = state_module._TRANSCRIPT_TAIL_LINES
    total = cap + 50
    for i in range(total):
        state.stream_message(f"line {i}\n")
    lines = state.transcript()
    assert len(lines) == cap
    # The newest lines are retained; the oldest are dropped.
    assert lines[-1].text == f"line {total - 1}"
    assert lines[0].text == f"line {total - cap}"


def test_transcript_resets_on_iteration_start() -> None:
    state = _make_state()
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.stream_message("iter one output\n")
    assert "iter one output" in _texts(state)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    assert state.transcript() == ()
    state.stream_message("iter two output\n")
    assert _texts(state) == ["iter two output"]


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


def test_issue_detail_non_active_issue_shows_details_only() -> None:
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
