"""Pilot smoke test for ``ralph_afk.interactive.app`` (issue #23).

Gated behind ``pytest.importorskip("textual")`` so the base (no ``[tui]`` extra)
install skips it. Asserts the live header renders the run's identity/state and
that **Stop** (``q``) tears the app down — the end-to-end proof that the Textual
observer is wired to the :class:`~ralph_afk.interactive.state.LiveRunState`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from textual.widgets import ContentSwitcher, DataTable, Static  # noqa: E402

from ralph_afk import events as events_module  # noqa: E402
from ralph_afk.interactive.app import RalphApp, TabBar  # noqa: E402
from ralph_afk.interactive.state import LiveRunState  # noqa: E402


class _FakeSummary:
    """Duck-typed stand-in: the app only calls ``build_run_table()``."""

    def build_run_table(self) -> str:
        return "SUMMARY-TABLE-MARKER"


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
# Tabbed navigation (issue #26)
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


async def test_tabs_switch_with_arrow_enter_and_esc() -> None:
    app = RalphApp(_make_state(), refresh_interval=3600)
    async with app.run_test() as pilot:
        switcher = app.query_one(ContentSwitcher)
        tabs = app.query_one(TabBar)
        # The tab bar holds focus and the Dashboard is the initial tab.
        assert isinstance(app.focused, TabBar)
        assert switcher.current == "dashboard-pane"

        # Arrow moves the selection but does NOT switch until Enter.
        await pilot.press("right")
        assert tabs.selected == 1
        assert switcher.current == "dashboard-pane"
        await pilot.press("enter")
        assert switcher.current == "log-pane"

        # Esc returns to the tab bar; arrow + Enter reaches the Summary tab.
        await pilot.press("escape")
        assert isinstance(app.focused, TabBar)
        await pilot.press("right", "enter")
        assert switcher.current == "summary-pane"

        # ...and back to the Dashboard.
        await pilot.press("escape", "left", "left", "enter")
        assert switcher.current == "dashboard-pane"


async def test_dashboard_queue_lists_issues_active_first_and_cursor_moves() -> None:
    app = RalphApp(_state_with_queue(), refresh_interval=3600)
    async with app.run_test() as pilot:
        table = app.query_one("#queue", DataTable)
        assert table.row_count == 3
        # Active-first ordering: #26 (active) leads, then queued #27, #28.
        assert [table.get_row_at(i)[0] for i in range(3)] == ["#26", "#27", "#28"]
        assert table.get_row_at(0)[1] == "active"
        assert table.get_row_at(1)[1] == "queued"

        # Enter on the (already-selected) Dashboard tab focuses the queue.
        await pilot.press("enter")
        assert isinstance(app.focused, DataTable)
        assert table.cursor_row == 0
        await pilot.press("down")
        assert table.cursor_row == 1


async def test_log_pane_shows_captured_output() -> None:
    app = RalphApp(
        _make_state(), log_source=lambda: "LOG-LINE-XYZZY", refresh_interval=3600
    )
    async with app.run_test() as pilot:
        await pilot.press("right", "enter")  # activate the Log tab
        body = app.query_one("#log-body", Static)
        assert "LOG-LINE-XYZZY" in str(body.renderable)


async def test_summary_pane_renders_summary_table() -> None:
    app = RalphApp(
        _make_state(),
        summary=_FakeSummary(),  # type: ignore[arg-type]
        refresh_interval=3600,
    )
    async with app.run_test() as pilot:
        await pilot.press("right", "right", "enter")  # activate the Summary tab
        body = app.query_one("#summary-body", Static)
        assert "SUMMARY-TABLE-MARKER" in str(body.renderable)


async def test_esc_returns_focus_to_tab_bar_from_queue() -> None:
    app = RalphApp(_state_with_queue(), refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")  # focus the queue
        assert isinstance(app.focused, DataTable)
        await pilot.press("escape")
        assert isinstance(app.focused, TabBar)


# ---------------------------------------------------------------------------
# Queue drill-in + live streaming detail (issue #27)
# ---------------------------------------------------------------------------


def _state_with_active_transcript() -> LiveRunState:
    """The #26-active run plus a little reasoning / message / tool transcript."""
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


def _dimmed_text(text: Text) -> str:
    """The substring(s) carrying the ``dim`` style — i.e. the reasoning lines."""
    return "".join(
        text.plain[span.start : span.end]
        for span in text.spans
        if span.style == "dim"
    )


async def test_drill_in_active_issue_shows_live_transcript_and_esc_returns() -> None:
    app = RalphApp(_state_with_active_transcript(), refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")  # Dashboard tab -> focus the queue
        assert isinstance(app.focused, DataTable)
        await pilot.press("enter")  # Enter on the active row (#26) -> drill in
        await pilot.pause()

        # The detail view replaces the queue in the same region (no new tab).
        queue = app.query_one("#queue", DataTable)
        detail = app.query_one("#detail")
        assert detail.display is True
        assert queue.display is False
        assert app.query_one(ContentSwitcher).current == "dashboard-pane"

        header = str(app.query_one("#detail-header", Static).renderable)
        assert "#26" in header
        assert "status active" in header

        body = app.query_one("#detail-body", Static).renderable
        assert isinstance(body, Text)
        # Interleaved transcript: reasoning + message + the tool-call event.
        assert "weighing the options" in body.plain
        assert "Here is my plan" in body.plain
        assert "» bash  command=pytest -q" in body.plain
        # Reasoning is dimmed; the assistant message is plain.
        dimmed = _dimmed_text(body)
        assert "weighing the options" in dimmed
        assert "Here is my plan" not in dimmed

        # Esc returns to the queue; the tab bar stayed on the Dashboard.
        await pilot.press("escape")
        await pilot.pause()
        assert detail.display is False
        assert queue.display is True
        assert isinstance(app.focused, DataTable)
        assert app.query_one(ContentSwitcher).current == "dashboard-pane"


async def test_drill_in_non_active_issue_shows_details_only() -> None:
    app = RalphApp(_state_with_active_transcript(), refresh_interval=3600)
    async with app.run_test() as pilot:
        await pilot.press("enter")  # focus the queue
        await pilot.press("down")  # move to the queued row (#27)
        await pilot.press("enter")  # drill into the non-active issue
        await pilot.pause()

        header = str(app.query_one("#detail-header", Static).renderable)
        assert "#27" in header
        assert "status queued" in header

        body = app.query_one("#detail-body", Static).renderable
        assert isinstance(body, Text)
        # Details only: no live transcript leaks into a non-active issue's view.
        assert "weighing the options" not in body.plain
        assert "» bash" not in body.plain
        assert "details only" in body.plain
