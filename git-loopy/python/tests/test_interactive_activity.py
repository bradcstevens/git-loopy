"""Tests for the Level-1 **Activity** band (issue #69, ADR-0011).

The Activity band is a persistent Dashboard band, positioned **between the Queue
and the Summary**, that streams the live current tail of the **Active issue**'s
**Log** (or the pre-marker pending buffer) so a run reads as active in real time
instead of appearing stuck while issues sit **queued**. It is a UI-layer view
over the existing per-issue Log state (``state.log()`` / ``log_line_views``) —
no new state model (ADR-0011).

Two groups:

* pure unit tests for :func:`~git_loopy.interactive.state.format_activity_header`
  (the band's compact one-line header), and
* Pilot tests for the band's placement, live tail rendering, header attribution,
  empty/idle placeholder, non-focusability (the Queue keeps focus), the
  operator-sized band vs the flexing Queue (keyboard and mouse), and the
  Log-open / Esc display ride-along.

Gated behind ``pytest.importorskip("textual")`` so the base (no ``[tui]`` extra)
install skips the Pilot tests; the pure header helper is exercised alongside.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from textual import events  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402
from textual.widget import Widget  # noqa: E402
from textual.widgets import DataTable, Static  # noqa: E402
from textual.widgets._footer import FooterKey  # noqa: E402

from git_loopy import events as events_module  # noqa: E402
from git_loopy.interactive.app import (  # noqa: E402
    _ACTIVITY_BAND_COLLAPSED_HEIGHT,
    _ACTIVITY_BAND_HEIGHT,
    _ACTIVITY_BAND_MIN_HEIGHT,
    _ActivityBand,
    _Dashboard,
    _LogView,
    _QUEUE_MIN_HEIGHT,
    GitLoopyApp,
)
from git_loopy.interactive.state import (  # noqa: E402
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
    app = GitLoopyApp(_state_with_active(), refresh_interval=3600)
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
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
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
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
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
    app = GitLoopyApp(state, refresh_interval=3600)
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
    app = GitLoopyApp(state, refresh_interval=3600)
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

    app = GitLoopyApp(state, refresh_interval=3600)
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
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
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


async def test_activity_band_starts_at_the_named_height_and_queue_reclaims_the_rest() -> (
    None
):
    """The band opens at the named starting **requested** height and the Queue
    takes the remaining space (``1fr``), so a long Queue is never crushed.

    Rewritten from the #69 fixed-height assertion: ADR-0031 makes the band
    operator-sized, so the constant is its *starting* request rather than an
    invariant. The Queue still reclaims what the band does not take — but now
    from a band the operator can size.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test():
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        # The band opens at the named starting request...
        assert band.requested == _ACTIVITY_BAND_HEIGHT
        assert band.size.height == _ACTIVITY_BAND_HEIGHT
        # ...and the flexing Queue reclaims the rest, so it is not crushed.
        assert queue.size.height > band.size.height


# ---------------------------------------------------------------------------
# Sizing: ``shift+down`` / ``shift+up`` move the band one row per press
# (ADR-0031 — the operator sizes the band from the keyboard)
# ---------------------------------------------------------------------------


async def test_shift_down_shrinks_the_band_one_row_and_the_queue_takes_it() -> None:
    """``shift+down`` is a **sizing** gesture: it writes ``requested`` one row
    shorter, and the Queue's ``1fr`` takes the freed row."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height

        await pilot.press("shift+down")
        await pilot.pause()

        assert band.requested == _ACTIVITY_BAND_HEIGHT - 1
        assert band.size.height == _ACTIVITY_BAND_HEIGHT - 1
        assert queue.size.height == queue_before + 1


async def test_shift_up_grows_the_band_one_row_and_the_queue_gives_it_back() -> None:
    """``shift+up`` is the peer sizing gesture: one row taller per press, and
    the Queue gives the row back."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height

        await pilot.press("shift+up")
        await pilot.pause()

        assert band.requested == _ACTIVITY_BAND_HEIGHT + 1
        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 1
        assert queue.size.height == queue_before - 1


async def test_shift_up_never_grows_past_the_queues_three_row_floor() -> None:
    """The band's ceiling is the largest height that still leaves the Queue its
    three rows, so leaning on ``shift+up`` cannot crush the Queue."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)

        for _ in range(30):
            await pilot.press("shift+up")
        await pilot.pause()

        # Dashboard (23 rows: the terminal less the Footer) less the #23 header
        # and the Summary band (1 each), less the Queue's own three-row floor.
        assert band.size.height == 23 - 2 - 3
        assert queue.size.height == 3
        # The cap is on ``requested`` too: the band never asks for more than it
        # can have, so nothing is stored up to spring out on the next repaint.
        assert band.requested == band.size.height


# ---------------------------------------------------------------------------
# Collapsed: the band's one-line header stub — a state, not an absence
# (ADR-0031, superseding ADR-0021's snap-to-collapsed clause)
# ---------------------------------------------------------------------------


async def test_shift_down_below_the_floor_collapses_to_the_header_stub() -> None:
    """Sizing the band below its three-row floor lands it in **Collapsed**: the
    band's one-line header and nothing else.

    The band stays in the Dashboard layout (``display`` is untouched), so the
    operator can always see that an Activity band is there — and the Queue takes
    every row the band gave up but that one.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height

        # Down to the floor, which is still Expanded.
        for _ in range(_ACTIVITY_BAND_HEIGHT - _ACTIVITY_BAND_MIN_HEIGHT):
            await pilot.press("shift+down")
        await pilot.pause()
        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_MIN_HEIGHT

        # One press past it collapses to the stub.
        await pilot.press("shift+down")
        await pilot.pause()
        assert band.collapsed is True
        # A state, not an absence: still in the layout, still one row tall.
        assert band.display is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        # The header survives; the live tail below it does not.
        assert app.query_one("#activity-scroll", VerticalScroll).display is False
        assert (
            str(app.query_one("#activity-header", Static).renderable)
            == "Activity · #26"
        )
        assert queue.size.height == (
            queue_before + _ACTIVITY_BAND_HEIGHT - _ACTIVITY_BAND_COLLAPSED_HEIGHT
        )
        # The press that crossed the floor still stated its intent — sizing
        # gestures write ``requested`` even when the state changes under them —
        # so the restore comes back to the floor rather than to the old height.
        assert band.requested == _ACTIVITY_BAND_MIN_HEIGHT - 1
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_MIN_HEIGHT


async def test_a_sizing_gesture_counts_from_the_height_on_screen() -> None:
    """One press is one *visible* row, even while a short terminal has the band
    clamped below what was asked for.

    Counting from ``requested`` instead would spend a press moving a number the
    operator cannot see — pressing ``shift+down`` on a band clamped from 9 to 8
    would set ``requested`` to 8 and move nothing.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)

        # 15 rows -> a ceiling of 9... 14 -> 8, one short of the request.
        await pilot.resize_terminal(80, 14)
        await pilot.pause()
        assert band.requested == _ACTIVITY_BAND_HEIGHT
        assert band.size.height == 8

        await pilot.press("shift+down")
        await pilot.pause()
        assert band.size.height == 7
        assert band.requested == 7


async def test_shift_up_from_the_stub_reopens_the_band_at_its_floor() -> None:
    """``shift+up`` out of **Collapsed** lands on the three-row floor, not on the
    remembered height: it is a sizing gesture, and sizing gestures state fresh
    intent (ADR-0031). ``a`` is the gesture that restores."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)

        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is True
        # The remembered height is still the one ``a`` would restore...
        assert band.requested == _ACTIVITY_BAND_HEIGHT

        await pilot.press("shift+up")
        await pilot.pause()
        # ...but ``shift+up`` states fresh intent: the floor.
        assert band.collapsed is False
        assert band.requested == _ACTIVITY_BAND_MIN_HEIGHT
        assert band.size.height == _ACTIVITY_BAND_MIN_HEIGHT
        assert app.query_one("#activity-scroll", VerticalScroll).display is True


async def test_shift_down_from_the_stub_is_a_no_op() -> None:
    """There is nothing shorter than the stub to ask for, so ``shift+down`` from
    **Collapsed** changes neither the state nor the remembered height."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)
        await pilot.press("a")
        await pilot.pause()

        for _ in range(3):
            await pilot.press("shift+down")
        await pilot.pause()

        assert band.collapsed is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        assert band.requested == _ACTIVITY_BAND_HEIGHT


# ---------------------------------------------------------------------------
# The mouse: the band's header row is its drag handle, and a bare click on it
# toggles collapse (ADR-0031 — the drag → click → keys ladder)
# ---------------------------------------------------------------------------


def _handle_row(app: GitLoopyApp) -> int:
    """The screen row the band's header — its drag handle — currently occupies."""
    return app.query_one("#activity-header", Static).region.y


async def test_dragging_the_header_up_grows_the_band_with_the_pointer() -> None:
    """Grab the band's header row and pull up: the band follows the pointer row
    for row, and the Queue's ``1fr`` gives the rows back.

    The drag is a **sizing** gesture, so it writes ``requested`` (ADR-0031) — the
    same number ``shift+up`` writes, reached with the mouse.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height
        grab = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, grab - 2))
        await pilot.mouse_up(offset=(10, grab - 2))
        await pilot.pause()

        assert band.collapsed is False
        assert band.requested == _ACTIVITY_BAND_HEIGHT + 2
        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 2
        assert queue.size.height == queue_before - 2


async def test_dragging_the_header_down_shrinks_the_band_with_the_pointer() -> None:
    """Push the header down and the band shrinks row for row, the Queue taking
    every row it gives up — the drag undoes itself in the direction it came."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height
        grab = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, grab + 3))
        await pilot.mouse_up(offset=(10, grab + 3))
        await pilot.pause()

        assert band.collapsed is False
        assert band.requested == _ACTIVITY_BAND_HEIGHT - 3
        assert band.size.height == _ACTIVITY_BAND_HEIGHT - 3
        assert queue.size.height == queue_before + 3


async def test_dragging_up_never_squeezes_the_queue_below_its_floor() -> None:
    """A drag that runs to the top of the terminal stops at the band's ceiling:
    the largest height that still leaves the Queue its three rows.

    The cap is written into ``requested`` too, as ``shift+up``'s is — a pointer
    dragged off the top of the screen must not store up height that springs out
    the moment the terminal grows.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, 0))
        await pilot.mouse_up(offset=(10, 0))
        await pilot.pause()

        # Dashboard (23 rows: the terminal less the Footer) less the #23 header
        # and the Summary band (1 each), less the Queue's own three-row floor.
        assert band.size.height == 23 - 2 - 3
        assert queue.size.height == _QUEUE_MIN_HEIGHT
        assert band.requested == band.size.height


async def test_dragging_below_the_floor_releases_into_the_collapsed_stub() -> None:
    """Push the header past the band's three-row floor and it releases into
    **Collapsed**: the one-line header and nothing else, still in the layout.

    It lands in exactly the state ``shift+down`` past the floor lands in — the
    same remembered intent, one row short of the floor — so what ``a`` restores
    does not depend on which gesture collapsed the band.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height
        grab = _handle_row(app)

        # Nine rows down from a nine-row band: a band of zero rows was asked for.
        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, grab + _ACTIVITY_BAND_HEIGHT))
        await pilot.mouse_up(offset=(10, grab + _ACTIVITY_BAND_HEIGHT))
        await pilot.pause()

        assert band.collapsed is True
        # A state, not an absence: still in the layout, still one row tall.
        assert band.display is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        assert app.query_one("#activity-scroll", VerticalScroll).display is False
        assert queue.size.height == (
            queue_before + _ACTIVITY_BAND_HEIGHT - _ACTIVITY_BAND_COLLAPSED_HEIGHT
        )
        assert band.requested == _ACTIVITY_BAND_MIN_HEIGHT - 1


async def test_one_drag_can_collapse_the_band_and_pull_it_back_out() -> None:
    """The whole point of the one-row stub (ADR-0031): the handle survives the
    gesture, so a single unbroken drag can push the band into **Collapsed** and
    pull it straight back out. No keyboard is needed to recover.

    The drag is measured from where it was grabbed, not from the band's current
    height, so coming back past the grab row comes back to the height that was
    there before it.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        grab = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, grab + _ACTIVITY_BAND_HEIGHT))
        assert band.collapsed is True

        # Still holding the button: pull back up, past where it was grabbed.
        await pilot.hover(offset=(10, grab - 1))
        await pilot.mouse_up(offset=(10, grab - 1))
        await pilot.pause()

        assert band.collapsed is False
        assert band.requested == _ACTIVITY_BAND_HEIGHT + 1
        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 1
        assert app.query_one("#activity-scroll", VerticalScroll).display is True


async def test_a_fresh_drag_on_the_stub_reopens_the_band_at_the_floor() -> None:
    """The stub is a drag handle in its own right: grabbing the collapsed band's
    one row and pulling up reopens it, floored at three rows.

    A one-row pull asks for a two-row band, which is not a height this band has,
    so it stays collapsed — the floor is a floor and not a snap. Two rows is the
    smallest deliberate pull that reopens it, and it lands the header back under
    the pointer.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is True
        stub = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, stub - 1))
        assert band.collapsed is True

        await pilot.hover(offset=(10, stub - 4))
        await pilot.mouse_up(offset=(10, stub - 4))
        await pilot.pause()

        assert band.collapsed is False
        assert band.requested == _ACTIVITY_BAND_COLLAPSED_HEIGHT + 4
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT + 4


async def test_clicking_the_header_collapses_the_band_like_the_a_key() -> None:
    """A bare click on the band header is a **toggle** gesture: it reaches the
    same **Collapsed** state ``a`` produces, and preserves ``requested``.

    A terminal that reports clicks but not motion — the middle rung of the
    drag → click → keys ladder — has exactly this and nothing else, which is why
    the click is driven here without any intervening pointer motion.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        queue_before = queue.size.height
        # Size it away from the named default first, so a click that wrote
        # ``requested`` instead of preserving it could not pass unnoticed.
        await pilot.press("shift+down")
        await pilot.pause()
        assert band.requested == _ACTIVITY_BAND_HEIGHT - 1

        await pilot.click("#activity-header")
        await pilot.pause()

        assert band.collapsed is True
        assert band.display is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        assert app.query_one("#activity-scroll", VerticalScroll).display is False
        assert band.requested == _ACTIVITY_BAND_HEIGHT - 1
        assert queue.size.height == (
            queue_before + _ACTIVITY_BAND_HEIGHT - _ACTIVITY_BAND_COLLAPSED_HEIGHT
        )


async def test_clicking_the_stub_reopens_the_band_at_the_requested_height() -> None:
    """From **Collapsed** the click restores the operator's height, not the
    named default and not the floor — it is a toggle, and toggles preserve
    ``requested`` so there is something of the operator's to come back to.

    This is the forgiving half of the gesture pair: a one-row stub is an awkward
    thing to drag and an easy thing to click.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        await pilot.press("shift+up")
        await pilot.press("shift+up")
        await pilot.pause()
        assert band.requested == _ACTIVITY_BAND_HEIGHT + 2

        await pilot.click("#activity-header")
        await pilot.pause()
        assert band.collapsed is True

        await pilot.click("#activity-header")
        await pilot.pause()

        assert band.collapsed is False
        assert band.requested == _ACTIVITY_BAND_HEIGHT + 2
        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 2
        assert app.query_one("#activity-scroll", VerticalScroll).display is True


async def test_a_drag_that_ends_where_it_began_is_read_as_a_click() -> None:
    """Press, release, no motion in between: indistinguishable from a click, and
    therefore treated as one (ADR-0031).

    A drag that *did* wander and came back is not: it stated a size, and
    collapsing a band the operator has just finished sizing would be the
    opposite of what they asked for.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        grab = _handle_row(app)

        # Press and release on the very row it was grabbed on -> a click.
        await pilot.mouse_down("#activity-header")
        await pilot.mouse_up("#activity-header")
        await pilot.pause()
        assert band.collapsed is True
        assert band.requested == _ACTIVITY_BAND_HEIGHT

        # Back out with a click, then drag away and back: a drag, not a click.
        await pilot.click("#activity-header")
        await pilot.pause()
        assert band.collapsed is False

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, grab - 3))
        await pilot.hover(offset=(10, grab))
        await pilot.mouse_up(offset=(10, grab))
        await pilot.pause()

        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_HEIGHT


async def test_a_drag_that_wanders_over_the_queue_leaves_the_queue_alone() -> None:
    """The mouse is **captured** for the duration of the drag, so every event
    goes to the handle wherever the pointer happens to be.

    Dragging the band taller means dragging *over* the Queue, and without capture
    that pointer would be moving the Queue's cursor, selecting a row, opening a
    **Log**, and dragging out a text selection across everything it crossed. The
    band grows and the Queue is otherwise untouched.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        cursor_before = queue.cursor_row
        grab = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        # Straight up through the Queue's rows, then let go over one of them.
        await pilot.hover(offset=(10, grab - 3))
        await pilot.hover(offset=(10, grab - 6))
        await pilot.mouse_up(offset=(10, grab - 6))
        await pilot.pause()

        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 6
        assert queue.cursor_row == cursor_before
        assert app.query_one("#log", _LogView).display is False
        assert app.screen.get_selected_text() is None


async def test_a_log_opened_mid_drag_ends_the_drag_and_frees_the_mouse() -> None:
    """A handle taken off screen is not holding a drag any more.

    ``enter`` opens a Level-2 **Log**, which hides the whole Dashboard — handle
    included — and it needs no mouse, so it can arrive with the button still
    held. A capture kept by a widget the operator can no longer see outlives the
    gesture: the release lands on nothing, and the next press after Esc is
    delivered to the handle instead of to what was clicked. One Queue row click
    would then drill into that issue's Log *and* collapse the band on the way
    past.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        grab = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#log", _LogView).display is True
        assert app.mouse_captured is None

        # The release the Dashboard never saw, then back to it.
        await pilot.mouse_up(offset=(10, grab))
        await pilot.press("escape")
        await pilot.pause()
        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_HEIGHT

        row = queue.region.y + queue.header_height
        await pilot.click(offset=(2, row))
        await pilot.pause()

        assert app.query_one("#log", _LogView).display is True
        assert band.collapsed is False


async def test_dragging_the_stub_further_down_keeps_the_remembered_height() -> None:
    """A drag that asks a **Collapsed** band to be shorter still is a no-op on
    ``requested``, exactly as ``shift+down`` from the stub is (ADR-0031).

    All three controls drive one state machine, so the mouse must not be able to
    destroy what the keys preserve. There is nothing shorter than the stub to
    ask for, and an operator who nudges a one-row target downwards has stated no
    height — so the height they *did* state is still there for the click to
    restore.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        await pilot.click("#activity-header")
        await pilot.pause()
        assert band.collapsed is True
        assert band.requested == _ACTIVITY_BAND_HEIGHT
        stub = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        await pilot.hover(offset=(10, stub + 2))
        await pilot.mouse_up(offset=(10, stub + 2))
        await pilot.pause()

        assert band.collapsed is True
        assert band.requested == _ACTIVITY_BAND_HEIGHT

        # And the click still has the operator's height to come back to.
        await pilot.click("#activity-header")
        await pilot.pause()
        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_HEIGHT


async def test_the_drag_gives_the_mouse_back_when_it_ends() -> None:
    """Capture lasts *for the duration of* the drag and no longer.

    The capture that protects the Queue during a drag would swallow every
    later mouse event if it outlived it — the Queue's row click, which is the
    **Log**'s own mouse path (ADR-0031), would go to the band's handle instead
    and drill-in would be gone for the rest of the Run. So the release is
    asserted twice over: the app holds no capture, and the very next click on a
    Queue row still opens that issue's Log.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        grab = _handle_row(app)

        await pilot.mouse_down("#activity-header")
        assert app.mouse_captured is app.query_one("#activity-header", Static)
        await pilot.hover(offset=(10, grab - 2))
        await pilot.mouse_up(offset=(10, grab - 2))
        await pilot.pause()

        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 2
        assert app.mouse_captured is None

        row = queue.region.y + queue.header_height
        await pilot.click(offset=(2, row))
        await pilot.pause()

        assert app.query_one("#log", _LogView).display is True


async def _wheel(pilot, widget: Widget, event_cls, turns: int = 3) -> None:
    """Turn the wheel over ``widget``.

    ``Pilot`` has no wheel gesture, so the event is posted the way the driver
    posts a real one — through the screen, which is also how ``Pilot`` posts the
    gestures it does have.
    """
    for _ in range(turns):
        x, y = widget.region.offset
        pilot.app.screen._forward_event(
            event_cls(
                widget, x, y, 0, 0, 0, False, False, False, screen_x=x, screen_y=y
            )
        )
        await pilot.pause()


async def test_the_wheel_never_resizes_the_expanded_band() -> None:
    """Resize-by-wheel is the accidental gesture ADR-0021's Context section is an
    argument against, so the wheel keeps doing what it does today and the band's
    height is not one of the things it does."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        header = app.query_one("#activity-header", Static)
        body = app.query_one("#activity-body", Static)

        # Each direction is checked on its own: a wheel that grew the band one
        # way and shrank it the other would net out to zero across a pair.
        for target in (header, body):
            for event_cls in (events.MouseScrollUp, events.MouseScrollDown):
                await _wheel(pilot, target, event_cls)

                assert band.collapsed is False
                assert band.requested == _ACTIVITY_BAND_HEIGHT
                assert band.size.height == _ACTIVITY_BAND_HEIGHT


async def test_the_wheel_never_reopens_the_collapsed_stub() -> None:
    """The stub is one row of header under the pointer and the wheel is the
    gesture most likely to be turned over it by accident. It does not resize the
    band and it does not reopen it — that is the drag's and the click's job."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is True

        header = app.query_one("#activity-header", Static)
        for event_cls in (events.MouseScrollUp, events.MouseScrollDown):
            await _wheel(pilot, header, event_cls)

            assert band.collapsed is True
            assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
            assert band.requested == _ACTIVITY_BAND_HEIGHT


# ---------------------------------------------------------------------------
# Ride-along: opening a Log hides the band; Esc restores it; Detach tears it down
# ---------------------------------------------------------------------------


async def test_opening_a_log_hides_the_band_and_esc_restores_it() -> None:
    """Opening a Level-2 Log hides the whole Dashboard — the band included — and
    Esc restores it (both ride the existing display toggle; no new teardown)."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
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
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        assert app.query_one("#activity", _ActivityBand) is not None
        await pilot.press("d")
        await pilot.pause()
    assert app.detach_requested is True
    assert app.is_running is False


# ---------------------------------------------------------------------------
# Collapse / expand: the ``a`` key toggles the band; the Queue reclaims the
# freed space (issue #70 — in-session only, no persisted Config / state)
# ---------------------------------------------------------------------------


async def test_a_key_toggles_between_the_stub_and_the_requested_height() -> None:
    """``a`` is a **toggle** gesture: it collapses the band to its one-line
    header stub and restores it at the operator's ``requested`` height, which it
    preserves throughout (ADR-0031).

    It no longer removes the band from the layout (issue #70's ``display =
    False``): the Queue reclaims one row fewer, and in exchange the operator can
    always see that an Activity band exists.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        # Expanded by default, at the named starting request.
        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_HEIGHT
        queue_expanded = queue.size.height

        # Size it first, so the restore has something of the operator's to keep.
        await pilot.press("shift+down")
        await pilot.pause()
        assert band.requested == _ACTIVITY_BAND_HEIGHT - 1

        # ``a`` collapses to the stub -> the Queue reclaims all but that one row.
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is True
        assert band.display is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        assert band.requested == _ACTIVITY_BAND_HEIGHT - 1
        assert queue.size.height == (
            queue_expanded + _ACTIVITY_BAND_HEIGHT - _ACTIVITY_BAND_COLLAPSED_HEIGHT
        )

        # ``a`` again restores the operator's height, not the named default.
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is False
        assert band.size.height == _ACTIVITY_BAND_HEIGHT - 1
        assert queue.size.height == queue_expanded + 1


async def test_activity_binding_appears_in_footer_labelled_activity() -> None:
    """The band's three keyboard gestures are surfaced in the Footer — ``a`` to
    collapse/expand (issue #70) and ``shift+up`` / ``shift+down`` to size it
    (ADR-0031) — alongside the unchanged Stop / Detach / Back."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test():
        entries = {key.key: key.description for key in app.query(FooterKey)}
        # The ``a`` -> Activity entry is present...
        assert entries.get("a") == "Activity"
        # ...the sizing keys are discoverable beside it...
        assert entries.get("shift+up") == "Taller"
        assert entries.get("shift+down") == "Shorter"
        # ...and the existing bindings are unaffected.
        assert entries.get("q") == "Stop"
        assert entries.get("d") == "Detach"
        assert entries.get("escape") == "Back"


async def test_collapse_state_persists_across_a_log_open_and_close() -> None:
    """The in-session collapse rides the existing Log open/close display toggle:
    collapse the band, open then Esc-close a Level-2 Log, and it comes back
    collapsed — with the operator's requested height still remembered."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test() as pilot:
        band = app.query_one("#activity", _ActivityBand)
        dashboard = app.query_one("#dashboard", _Dashboard)

        # Collapse the band on the Dashboard.
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is True

        # Open the active issue's Log (hides the whole Dashboard)...
        await pilot.press("enter")
        await pilot.pause()
        assert dashboard.display is False

        # ...and Esc back to the Dashboard: the band is still collapsed.
        await pilot.press("escape")
        await pilot.pause()
        assert dashboard.display is True
        assert band.collapsed is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        assert band.requested == _ACTIVITY_BAND_HEIGHT


# ---------------------------------------------------------------------------
# Resize: a clamp is never destructive of the operator's setting (ADR-0031)
# ---------------------------------------------------------------------------


async def test_shrinking_the_terminal_clamps_the_band_and_growing_restores_it() -> None:
    """A terminal too short for the requested height clamps ``effective`` and
    leaves ``requested`` alone, so growing it back returns the band to the
    height the operator asked for — not to whatever a transient geometry
    allowed."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        queue = app.query_one("#queue", DataTable)
        assert band.size.height == _ACTIVITY_BAND_HEIGHT

        # 14 rows: a 13-row Dashboard, less the header and Summary band, less
        # the Queue's floor -> a ceiling of 8, one short of the request.
        await pilot.resize_terminal(80, 14)
        await pilot.pause()
        assert band.size.height == 8
        assert queue.size.height == 3
        # The clamp did not touch what was asked for.
        assert band.requested == _ACTIVITY_BAND_HEIGHT

        # Grow it back: the operator's height returns.
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert band.size.height == _ACTIVITY_BAND_HEIGHT
        assert band.requested == _ACTIVITY_BAND_HEIGHT


async def test_a_collapsed_band_stays_collapsed_across_a_terminal_resize() -> None:
    """Resizing re-clamps ``effective`` and nothing else, so a **Collapsed** band
    stays collapsed however the terminal moves — and the height it is holding for
    the operator survives the trip."""
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)
        # Size it away from the named default first, so a clamp that reset the
        # request to that default could not pass unnoticed.
        await pilot.press("shift+up")
        await pilot.press("shift+up")
        await pilot.press("a")
        await pilot.pause()
        assert band.collapsed is True
        assert band.requested == _ACTIVITY_BAND_HEIGHT + 2

        await pilot.resize_terminal(80, 14)
        await pilot.pause()
        assert band.collapsed is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT

        await pilot.resize_terminal(80, 40)
        await pilot.pause()
        assert band.collapsed is True
        assert band.size.height == _ACTIVITY_BAND_COLLAPSED_HEIGHT
        # And ``a`` still restores the height the operator asked for.
        await pilot.press("a")
        await pilot.pause()
        assert band.size.height == _ACTIVITY_BAND_HEIGHT + 2


async def test_sizing_is_inert_while_a_log_hides_the_dashboard() -> None:
    """A sizing gesture states intent about a band the operator can see.

    While a Level-2 Log hides the Dashboard there is no band on screen and no
    current ceiling to cap against — the Dashboard stops being resized with the
    terminal — so ``shift+up`` / ``shift+down`` do nothing rather than writing a
    request against a stale ceiling that Esc would then hand back clamped.
    """
    app = GitLoopyApp(_state_with_active_log(), refresh_interval=3600)
    async with app.run_test(size=(80, 24)) as pilot:
        band = app.query_one("#activity", _ActivityBand)

        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#dashboard", _Dashboard).display is False

        # The terminal moves under the open Log, then the operator leans on the
        # sizing keys anyway.
        await pilot.resize_terminal(80, 14)
        for _ in range(5):
            await pilot.press("shift+up")
        await pilot.press("shift+down")
        await pilot.pause()
        assert band.requested == _ACTIVITY_BAND_HEIGHT

        # Esc back: the band is the operator's height, clamped to what now fits.
        await pilot.press("escape")
        await pilot.pause()
        assert band.requested == _ACTIVITY_BAND_HEIGHT
        assert band.size.height == 8






