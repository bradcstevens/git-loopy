"""Tests for the Level-1 **Activity** band (issue #69, ADR-0011).

The Activity band is a persistent Dashboard band, positioned **between the Queue
and the Summary**, that streams the live current tail of the **Active issue**'s
**Log** (or the pre-marker pending buffer) so a run reads as active in real time
instead of appearing stuck while issues sit **queued**. It is a UI-layer view
over the existing per-issue Log state (``state.log()`` / ``log_line_views``) —
no new state model (ADR-0011).

Two groups:

* pure unit tests for :func:`~copiloop.interactive.state.format_activity_header`
  (the band's compact one-line header), and
* Pilot tests for the band's placement, live tail rendering, header attribution,
  empty/idle placeholder, non-focusability (the Queue keeps focus), the fixed
  band height vs the flexing Queue, and the Log-open / Esc display ride-along.

Gated behind ``pytest.importorskip("textual")`` so the base (no ``[tui]`` extra)
install skips the Pilot tests; the pure header helper is exercised alongside.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402
from textual.widgets import DataTable, Static  # noqa: E402

from copiloop import events as events_module  # noqa: E402
from copiloop.interactive.app import (  # noqa: E402
    _ACTIVITY_BAND_HEIGHT,
    _ActivityBand,
    _Dashboard,
    _LogView,
    CopiloopApp,
)
from copiloop.interactive.state import (  # noqa: E402
    LiveRunState,
    format_activity_header,
)


# ---------------------------------------------------------------------------
# Pure header helper: names the Active issue independent of the Queue cursor
# ---------------------------------------------------------------------------


def _state_with_active(ref: int = 26) -> LiveRunState:
    """A run whose working marker has lit issue ``ref`` active."""
    state = LiveRunState(run_id="01A", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": [26, 27, 28]}
    )
    state.stream_message(f"<working issue={ref}>")
    return state


def test_activity_header_names_active_ref() -> None:
    state = _state_with_active(26)
    assert format_activity_header(state) == "Activity · #26"


def test_activity_header_without_active_ref_is_bare() -> None:
    """Before the working marker (or a parallel Wave: no serial ``active_ref``)
    the header carries no issue — just the band name."""
    state = LiveRunState(run_id="01A", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    assert state.active_ref is None
    assert format_activity_header(state) == "Activity"


# ---------------------------------------------------------------------------
# Placement: the band sits between the Queue and the Summary, visible by default
# ---------------------------------------------------------------------------


async def test_activity_band_sits_between_queue_and_summary_visible_by_default() -> None:
    """The Dashboard stacks header → Queue → Activity → Summary (order matters).

    The band is present and **visible by default** when the Dashboard mounts;
    the per-issue Log (Level 2) stays hidden until a row is opened.
    """
    app = CopiloopApp(_state_with_active(), refresh_interval=3600)
    async with app.run_test():
        dashboard = app.query_one("#dashboard", _Dashboard)
        # Order: the Activity band is between the Queue and the Summary band.
        assert [c.id for c in dashboard.children] == [
            "header",
            "queue",
            "activity",
            "summary-band",
        ]
        band = app.query_one("#activity", _ActivityBand)
        assert band.display is True
        assert app.query_one("#activity-header", Static) is not None
        assert app.query_one("#activity-body", Static) is not None
        # The Log (Level 2) is still hidden until a Queue row is opened.
        assert app.query_one("#log", _LogView).display is False


# ---------------------------------------------------------------------------
# Content: the band streams the live current tail via ``log_line_views``
# ---------------------------------------------------------------------------


def _state_with_active_log() -> LiveRunState:
    """#26-active, with a little reasoning / message / tool-call Log to render."""
    state = _state_with_active(26)
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


async def test_activity_band_streams_live_tail_with_log_styling() -> None:
    """The band mirrors the Level-2 Log rendering of the live current tail.

    It renders ``state.log()`` (no ref) via ``log_line_views``: reasoning dimmed,
    assistant messages + key structured events plain.
    """
    app = CopiloopApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test():
        body = app.query_one("#activity-body", Static).renderable
        assert isinstance(body, Text)
        # Interleaved live tail: reasoning + message + the tool-call event.
        assert "weighing the options" in body.plain
        assert "Here is my plan" in body.plain
        assert "» bash  command=pytest -q" in body.plain
        # Reasoning is dimmed; the assistant message is plain.
        dimmed = _dimmed_text(body)
        assert "weighing the options" in dimmed
        assert "Here is my plan" not in dimmed


async def test_activity_header_follows_active_ref_not_the_queue_cursor() -> None:
    """The band header names the **Active issue** (#26) even after the Queue
    cursor moves to a different row — it follows ``active_ref``, not the cursor,
    so it stays attributable when the active row scrolls out of a long Queue."""
    app = CopiloopApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        header = app.query_one("#activity-header", Static)
        assert str(header.renderable) == "Activity · #26"

        # Move the Queue cursor off the active row, onto #27 then #28.
        table = app.query_one("#queue", DataTable)
        assert isinstance(app.focused, DataTable)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 2  # a non-active row is now selected
        # The band header still names the Active issue, not the cursor's row.
        assert str(header.renderable) == "Activity · #26"


async def test_activity_header_bare_before_working_marker() -> None:
    """With no Active issue yet (pre-marker) the header is the bare band name."""
    state = LiveRunState(run_id="01A", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    app = CopiloopApp(state, refresh_interval=3600)
    async with app.run_test():
        header = app.query_one("#activity-header", Static)
        assert str(header.renderable) == "Activity"


# ---------------------------------------------------------------------------
# Empty / idle: the placeholder, and the pre-marker pending buffer
# ---------------------------------------------------------------------------


async def test_activity_band_shows_placeholder_when_tail_is_empty() -> None:
    """With no activity yet the band shows one dimmed ``Waiting for the agent...``
    placeholder (issue #69) — nothing else — and the bare header."""
    state = LiveRunState(run_id="01E", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    app = CopiloopApp(state, refresh_interval=3600)
    async with app.run_test():
        body = app.query_one("#activity-body", Static).renderable
        assert isinstance(body, Text)
        # Exactly the placeholder, and it is dimmed.
        assert body.plain == "Waiting for the agent..."
        assert _dimmed_text(body) == "Waiting for the agent..."
        assert str(app.query_one("#activity-header", Static).renderable) == "Activity"


async def test_activity_band_shows_pending_buffer_before_the_working_marker() -> None:
    """Before the working marker the band shows the **pending** pre-marker
    output (``state.log()`` returns the pending buffer while ``active_ref`` is
    ``None``), not the empty placeholder."""
    state = LiveRunState(run_id="01P", model="m", reasoning_effort="x")
    state.render({"type": events_module.WRAPPER_RUN_START, "max_nmt_strikes": 3})
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    # A message with no ``<working issue=N>`` marker: it lands in the pending
    # buffer and does not activate an issue.
    state.stream_message("booting the agent\n")
    assert state.active_ref is None

    app = CopiloopApp(state, refresh_interval=3600)
    async with app.run_test():
        body = app.query_one("#activity-body", Static).renderable
        assert isinstance(body, Text)
        assert "booting the agent" in body.plain
        assert "Waiting for the agent..." not in body.plain
        # No active issue yet -> the bare header.
        assert str(app.query_one("#activity-header", Static).renderable) == "Activity"


# ---------------------------------------------------------------------------
# Not focusable: the Queue keeps focus; the fixed band vs the flexing Queue
# ---------------------------------------------------------------------------


async def test_activity_band_is_not_focusable_queue_keeps_focus() -> None:
    """The band is passive: the Queue holds focus (up/down/enter unchanged) and
    the band's scroll never enters the focus rotation."""
    app = CopiloopApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        table = app.query_one("#queue", DataTable)
        # The Queue holds focus from the start; the band did not steal it.
        assert app.focused is table
        # The band's live-tail scroll is explicitly not focusable.
        scroll = app.query_one("#activity-scroll", VerticalScroll)
        assert scroll.can_focus is False
        # A focus-cycle (Tab) does not land on the band; the Queue keeps focus.
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is table
        # up/down still drive the Queue cursor (interaction unchanged).
        assert table.cursor_row == 0
        await pilot.press("down")
        assert table.cursor_row == 1


async def test_activity_band_is_fixed_height_and_queue_reclaims_the_rest() -> None:
    """The band height is the named constant; the Queue takes the remaining space
    (``1fr``) so a long Queue is never crushed by the fixed band."""
    app = CopiloopApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test():
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        # The band is exactly the named-constant height...
        assert band.size.height == _ACTIVITY_BAND_HEIGHT
        # ...and the flexing Queue reclaims the rest, so it is not crushed.
        assert queue.size.height > band.size.height


# ---------------------------------------------------------------------------
# Ride-along: opening a Log hides the band; Esc restores it; Detach tears it down
# ---------------------------------------------------------------------------


async def test_opening_a_log_hides_the_band_and_esc_restores_it() -> None:
    """Opening a Level-2 Log hides the whole Dashboard — the band included — and
    Esc restores it (both ride the existing display toggle; no new teardown)."""
    app = CopiloopApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        dashboard = app.query_one("#dashboard", _Dashboard)
        band = app.query_one("#activity", _ActivityBand)
        log = app.query_one("#log", _LogView)
        # Level 1: the Dashboard (with its band) shows; the Log is hidden.
        assert dashboard.display is True
        assert log.display is False

        # Enter opens the active issue's Log -> the Dashboard (band incl.) hides.
        await pilot.press("enter")
        await pilot.pause()
        assert log.display is True
        assert dashboard.display is False
        # The band is not rendered while the Dashboard is hidden.
        assert band.size.height == 0

        # Esc returns to the Dashboard -> the band rides back in and re-renders.
        await pilot.press("escape")
        await pilot.pause()
        assert log.display is False
        assert dashboard.display is True
        assert band.size.height == _ACTIVITY_BAND_HEIGHT
        body = app.query_one("#activity-body", Static).renderable
        assert isinstance(body, Text)
        assert "Here is my plan" in body.plain


async def test_detach_tears_down_the_band_with_the_tui() -> None:
    """Detach (``d``) tears the whole TUI down — the band with it — leaving the
    run going (the driver's concern). Here: the app exits on Detach."""
    app = CopiloopApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        assert app.query_one("#activity", _ActivityBand) is not None
        await pilot.press("d")
        await pilot.pause()
    assert app.detach_requested is True
    assert app.is_running is False






