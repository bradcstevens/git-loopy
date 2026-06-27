"""Pilot tests for ``ralph_afk.interactive.app`` — the tabless two-level live
interface (ADR-0003, issue #30).

Gated behind ``pytest.importorskip("textual")`` so the base (no ``[tui]`` extra)
install skips it. These cover the structural backbone:

* **Level 1 — the Dashboard** (the only top-level screen, no tab bar): the
  header band, the live **Queue**, and a compact **Summary** rollup band,
  stacked.
* **Level 2 — the per-issue Log**: ``enter`` on a Queue row opens that issue's
  **Log**; ``escape`` returns to the Dashboard with the Queue cursor preserved.

plus the unchanged exit model — **Stop** (``q`` / ``Ctrl+C``) and **Detach**
(``d``). The pure Queue / Log projections are unit-tested without a TTY in
``test_interactive_queue.py`` / ``test_interactive_log.py``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from textual.widgets import ContentSwitcher, DataTable, Static  # noqa: E402

from ralph_afk import events as events_module  # noqa: E402
from ralph_afk.interactive.app import (  # noqa: E402
    RalphApp,
    _Dashboard,
    _LogScroll,
    _LogView,
)
from ralph_afk.interactive.state import LiveRunState  # noqa: E402


class _FakeSummary:
    """Duck-typed stand-in: the app only calls ``build_rollup_band()``."""

    def build_rollup_band(self) -> str:
        return "ROLLUP-BAND-MARKER"


def _make_state() -> LiveRunState:
    state = LiveRunState(
        run_id="01HEADER",
        model="claude-opus-4.8",
        reasoning_effort="max",
    )
    state.render(
        {"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3}
    )
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 3})
    state.render(
        {"type": events_module.WRAPPER_STRIKE, "strikes": 1, "max_strikes": 3}
    )
    return state


# ---------------------------------------------------------------------------
# Header + exit model
# ---------------------------------------------------------------------------


async def test_header_renders_run_identity_and_state() -> None:
    app = RalphApp(_make_state())
    async with app.run_test():
        header = str(app.query_one("#header", Static).renderable)
    assert "01HEADER" in header
    assert "claude-opus-4.8 (max)" in header
    assert "iter 3" in header
    assert "running" in header
    assert "strikes 1/3" in header


async def test_q_requests_stop_and_app_exits() -> None:
    app = RalphApp(_make_state())
    async with app.run_test() as pilot:
        assert app.stop_requested is False
        await pilot.press("q")
        await pilot.pause()
    # The binding fired and the app left its run loop.
    assert app.stop_requested is True
    assert app.is_running is False


async def test_d_requests_detach_and_app_exits() -> None:
    """#28: ``d`` tears the TUI down as a **Detach** (not a Stop).

    The app only *signals* the intent (``detach_requested``) and exits; the
    interactive driver — the app's peer — observes the flag and swaps the live
    sink back to the line printer so the run keeps printing to scrollback.
    """
    app = RalphApp(_make_state())
    async with app.run_test() as pilot:
        assert app.detach_requested is False
        await pilot.press("d")
        await pilot.pause()
    # The binding fired and the app left its run loop — as a Detach, not a Stop.
    assert app.detach_requested is True
    assert app.stop_requested is False
    assert app.is_running is False


# ---------------------------------------------------------------------------
# Level 1: the tabless Dashboard
# ---------------------------------------------------------------------------


def _state_with_queue() -> LiveRunState:
    """A run with one active issue (#26) and two still-queued (#27, #28)."""
    state = LiveRunState(run_id="01Q", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {
            "type": events_module.WRAPPER_AFK_READY_COLLECTED,
            "issues": [26, 27, 28],
        }
    )
    state.stream_message("<working issue=26>")
    return state


async def test_no_tab_bar_dashboard_is_the_only_top_level() -> None:
    """No tab bar / ContentSwitcher; the Dashboard stacks header + Queue + band."""
    app = RalphApp(_make_state(), refresh_interval=3600)
    async with app.run_test():
        # The retired tabbed structure is gone.
        assert len(app.query(ContentSwitcher)) == 0
        # The Dashboard is the only top-level screen and stacks the three bands.
        dashboard = app.query_one("#dashboard", _Dashboard)
        assert dashboard.display is True
        assert app.query_one("#header", Static) is not None
        assert app.query_one("#queue", DataTable) is not None
        assert app.query_one("#summary-band", Static) is not None
        # The per-issue Log (Level 2) exists but is hidden until a row is opened.
        assert app.query_one("#log", _LogView).display is False
        # The Queue holds focus from the start (no tab bar to traverse first).
        assert isinstance(app.focused, DataTable)


async def test_dashboard_queue_lists_issues_active_first_and_cursor_moves() -> None:
    app = RalphApp(_state_with_queue(), refresh_interval=3600)
    async with app.run_test() as pilot:
        table = app.query_one("#queue", DataTable)
        assert table.row_count == 3
        # Active-first ordering: #26 (active) leads, then queued #27, #28.
        assert [table.get_row_at(i)[0] for i in range(3)] == ["#26", "#27", "#28"]
        assert table.get_row_at(0)[1] == "active"
        assert table.get_row_at(1)[1] == "queued"

        # The Queue already has focus; arrow keys move its cursor.
        assert isinstance(app.focused, DataTable)
        assert table.cursor_row == 0
        await pilot.press("down")
        assert table.cursor_row == 1


async def test_dashboard_queue_columns_drop_waiting_add_started() -> None:
    """The Queue is Issue | Status | Started | Active — no Waiting (issue #33).

    Started is the 12-hour AM/PM wall clock of when the issue first became
    active; a still-queued issue shows the em-dash placeholder until it has been
    active. Clocks are injected so the rendered stamp is deterministic.
    """
    fixed = datetime(2026, 6, 21, 13, 42, 7)
    state = LiveRunState(
        run_id="01Q",
        model="m",
        reasoning_effort="x",
        monotonic=lambda: 0.0,
        wall_clock=lambda: fixed,
    )
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {
            "type": events_module.WRAPPER_AFK_READY_COLLECTED,
            "issues": [26, 27, 28],
        }
    )
    state.stream_message("<working issue=26>")

    app = RalphApp(state, refresh_interval=3600)
    async with app.run_test():
        table = app.query_one("#queue", DataTable)
        labels = [str(col.label) for col in table.columns.values()]
        assert labels == ["Issue", "Status", "Started", "Active"]
        assert "Waiting" not in labels
        # The active row (#26) shows its Started wall clock in 12h AM/PM.
        active_row = table.get_row_at(0)
        assert active_row[1] == "active"
        assert active_row[2] == "1:42:07 PM"
        # Still-queued rows (#27, #28) show the placeholder until first active.
        assert table.get_row_at(1)[1] == "queued"
        assert table.get_row_at(1)[2] == "—"
        assert table.get_row_at(2)[2] == "—"


async def test_summary_band_renders_rollup() -> None:
    app = RalphApp(
        _make_state(),
        summary=_FakeSummary(),  # type: ignore[arg-type]
        refresh_interval=3600,
    )
    async with app.run_test():
        band = app.query_one("#summary-band", Static)
        assert "ROLLUP-BAND-MARKER" in str(band.renderable)


# ---------------------------------------------------------------------------
# Level 2: Enter opens the per-issue Log, Esc returns to the Dashboard
# ---------------------------------------------------------------------------


def _state_with_active_log() -> LiveRunState:
    """The #26-active run plus a little reasoning / message / tool Log."""
    state = _state_with_queue()  # ends with an open "<working issue=26>" message
    state.stream_reasoning("weighing the options\n")
    state.stream_message("Here is my plan\n")
    state.render(
        {
            "type": events_module.TOOL_CALL,
            "tool_name": "bash",
            "arguments": {"command": "pytest -q"},
        }
    )
    return state


def _state_with_history() -> LiveRunState:
    """A run where #26 was worked in iter 1 (now historical) and #27 is active.

    #26 streams a line then the iteration ends (it goes ``no-progress``, keeping
    its retained Log); iteration 2 lights up #27 and streams its own line. Used
    to assert per-issue isolation and the historical-vs-live split (issue #34).
    """
    state = LiveRunState(run_id="01H", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [26, 27]}
    )
    state.stream_message("<working issue=26>\n")
    state.stream_message("twenty-six history\n")
    state.render({"type": events_module.WRAPPER_ITERATION_END})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [26, 27]}
    )
    state.stream_message("<working issue=27>\n")
    state.stream_message("twenty-seven live\n")
    return state


def _dimmed_text(text: Text) -> str:
    """The substring(s) carrying the ``dim`` style — i.e. the reasoning lines."""
    return "".join(
        text.plain[span.start : span.end]
        for span in text.spans
        if span.style == "dim"
    )


async def test_enter_opens_active_issue_log_and_esc_returns() -> None:
    app = RalphApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        # The Queue holds focus; Enter on the active row (#26) opens its Log.
        assert isinstance(app.focused, DataTable)
        await pilot.press("enter")
        await pilot.pause()

        # Level 2: the Log replaces the Dashboard (no tab bar, no switcher).
        dashboard = app.query_one("#dashboard", _Dashboard)
        log = app.query_one("#log", _LogView)
        assert log.display is True
        assert dashboard.display is False

        header = str(app.query_one("#log-header", Static).renderable)
        assert "#26" in header
        assert "status active" in header

        body = app.query_one("#log-body", Static).renderable
        assert isinstance(body, Text)
        # Interleaved Log: reasoning + message + the tool-call event.
        assert "weighing the options" in body.plain
        assert "Here is my plan" in body.plain
        assert "» bash  command=pytest -q" in body.plain
        # Reasoning is dimmed; the assistant message is plain.
        dimmed = _dimmed_text(body)
        assert "weighing the options" in dimmed
        assert "Here is my plan" not in dimmed

        # Esc returns to the Dashboard (Level 1); the Queue regains focus.
        await pilot.press("escape")
        await pilot.pause()
        assert log.display is False
        assert dashboard.display is True
        assert isinstance(app.focused, DataTable)


async def test_enter_opens_queued_issue_log_shows_no_lines_and_footer() -> None:
    """A queued-but-never-worked issue's Log: no lines + the JSONL-replay footer.

    Crucially, the active issue's live Log never leaks into it (per-issue
    isolation, issue #34).
    """
    app = RalphApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        table = app.query_one("#queue", DataTable)
        await pilot.press("down")  # move to the queued row (#27)
        assert table.cursor_row == 1
        await pilot.press("enter")  # open the non-active issue's Log
        await pilot.pause()

        header = str(app.query_one("#log-header", Static).renderable)
        assert "#27" in header
        assert "status queued" in header

        body = app.query_one("#log-body", Static).renderable
        assert isinstance(body, Text)
        # Isolation: the active issue's live Log does not leak into #27's.
        assert "weighing the options" not in body.plain
        assert "» bash" not in body.plain
        # No retained lines yet, and the JSONL-replay footer is shown.
        assert "no Log lines for this issue" in body.plain
        assert "JSONL replay log" in body.plain

        # Esc returns to the Dashboard with the Queue cursor preserved on #27.
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#log", _LogView).display is False
        assert isinstance(app.focused, DataTable)
        assert table.cursor_row == 1


async def test_enter_opens_historical_issue_log_shows_its_retained_tail() -> None:
    """A historical issue shows ITS OWN retained Log tail + the JSONL footer.

    The active issue (#27) streams live; opening the historical issue (#26)
    shows #26's own retained lines, never #27's (per-issue isolation, #34).
    """
    app = RalphApp(_state_with_history(), refresh_interval=3600)
    async with app.run_test() as pilot:
        table = app.query_one("#queue", DataTable)
        # Active-first ordering: #27 (active) leads, then historical #26.
        assert [table.get_row_at(i)[0] for i in range(2)] == ["#27", "#26"]
        await pilot.press("down")  # move to the historical row (#26)
        assert table.cursor_row == 1
        await pilot.press("enter")
        await pilot.pause()

        header = str(app.query_one("#log-header", Static).renderable)
        assert "#26" in header

        body = app.query_one("#log-body", Static).renderable
        assert isinstance(body, Text)
        # #26's OWN retained tail is shown, with the JSONL-replay footer.
        assert "twenty-six history" in body.plain
        assert "JSONL replay log" in body.plain
        # Isolation: the live active issue's (#27) output never leaks in.
        assert "twenty-seven live" not in body.plain

        # Opening the active issue (#27) instead streams its live line.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("up")
        assert table.cursor_row == 0
        await pilot.press("enter")
        await pilot.pause()
        active_body = app.query_one("#log-body", Static).renderable
        assert isinstance(active_body, Text)
        assert "twenty-seven live" in active_body.plain
        assert "twenty-six history" not in active_body.plain


async def test_log_view_stamps_lines_12h_and_opens_reasoning_with_marker() -> None:
    """The Log view renders 12h AM/PM stamps (collapsed per second) + ✻ Thinking:.

    Clocks are injected so the rendered stamp is deterministic (issue #37).
    """
    fixed = datetime(2026, 6, 21, 13, 42, 7)
    state = LiveRunState(
        run_id="01TS",
        model="m",
        reasoning_effort="x",
        monotonic=lambda: 0.0,
        wall_clock=lambda: fixed,
    )
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [26]}
    )
    state.stream_message("<working issue=26>\n")
    state.stream_reasoning("weighing options\n")

    app = RalphApp(state, refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")  # open #26's live Log
        await pilot.pause()
        body = app.query_one("#log-body", Static).renderable
        assert isinstance(body, Text)
        # The reasoning block opens with the ✻ Thinking: marker.
        assert "✻ Thinking:" in body.plain
        assert "weighing options" in body.plain
        # 12-hour AM/PM stamp, shown once: all lines share the injected second.
        assert "1:42:07 PM" in body.plain
        assert body.plain.count("1:42:07 PM") == 1


# ---------------------------------------------------------------------------
# Level 2: sticky-with-release autoscroll in the per-issue Log (issue #38)
# ---------------------------------------------------------------------------


def _state_with_long_log(count: int = 40) -> LiveRunState:
    """The #26-active run with enough Log lines to overflow the Log viewport."""
    state = _state_with_queue()  # active issue #26
    for i in range(count):
        state.stream_message(f"log line {i:02d}\n")
    return state


async def test_log_sticks_to_latest_line_by_default() -> None:
    """AC1: while at the bottom, the Log auto-scrolls to the latest line.

    The Log opens anchored (auto-bottom) and stays pinned to the newest line as
    fresh lines arrive; the new-lines-below indicator stays hidden.
    """
    app = RalphApp(_state_with_long_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")  # open #26's Log
        await pilot.pause()
        scroll = app.query_one("#log-scroll", _LogScroll)
        # Opened anchored and pinned to the latest line (auto-bottom).
        assert scroll.is_anchored
        assert scroll.is_vertical_scroll_end
        assert app.query_one("#log-indicator", Static).display is False

        # New lines arrive: the view sticks to the new bottom.
        before = scroll.scroll_y
        for i in range(12):
            app._state.stream_message(f"streamed {i:02d}\n")
        app._refresh()
        await pilot.pause()
        await pilot.pause()
        assert scroll.is_vertical_scroll_end
        assert scroll.scroll_y > before
        assert app.query_one("#log-indicator", Static).display is False


async def test_scrolling_up_pauses_autoscroll_and_shows_indicator() -> None:
    """AC2: scrolling up releases the stick and reveals the new-lines indicator.

    While paused, fresh lines do NOT yank the view back to the bottom.
    """
    app = RalphApp(_state_with_long_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        scroll = app.query_one("#log-scroll", _LogScroll)
        assert scroll.is_vertical_scroll_end  # starts stuck to the bottom

        await pilot.press("up")
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        # Autoscroll paused: off the bottom, indicator visible.
        assert not scroll.is_vertical_scroll_end
        indicator = app.query_one("#log-indicator", Static)
        assert indicator.display is True
        assert "new lines below" in str(indicator.renderable).lower()

        # Fresh lines while paused: the view stays put (no jump to bottom).
        held = scroll.scroll_y
        for i in range(8):
            app._state.stream_message(f"while paused {i:02d}\n")
        app._refresh()
        await pilot.pause()
        await pilot.pause()
        assert scroll.scroll_y == held
        assert not scroll.is_vertical_scroll_end
        assert app.query_one("#log-indicator", Static).display is True


async def test_end_and_return_to_bottom_reengage_autobottom() -> None:
    """AC3: End (or scrolling back to the bottom) re-engages and clears it."""
    app = RalphApp(_state_with_long_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        scroll = app.query_one("#log-scroll", _LogScroll)

        # Release by scrolling up.
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        assert not scroll.is_vertical_scroll_end
        assert app.query_one("#log-indicator", Static).display is True

        # End re-engages auto-bottom and clears the indicator.
        await pilot.press("end")
        await pilot.pause()
        assert scroll.is_vertical_scroll_end
        assert app.query_one("#log-indicator", Static).display is False

        # Release again, then scroll back down to the bottom: also re-engages.
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        assert app.query_one("#log-indicator", Static).display is True
        for _ in range(12):
            await pilot.press("down")
        await pilot.pause()
        assert scroll.is_vertical_scroll_end
        assert app.query_one("#log-indicator", Static).display is False


async def test_esc_on_dashboard_is_a_noop() -> None:
    """With no tab bar, Esc on the Dashboard does nothing (and never crashes)."""
    app = RalphApp(_state_with_queue(), refresh_interval=3600)
    async with app.run_test() as pilot:
        assert app.query_one("#dashboard", _Dashboard).display is True
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#dashboard", _Dashboard).display is True
        assert app.query_one("#log", _LogView).display is False
