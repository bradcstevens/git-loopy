"""Tests for the pure **Queue projection** in ``ralph_afk.interactive.state``
(issue #26 — live Queue rendering the #25 per-run ledger).

:func:`~ralph_afk.interactive.state.queue_rows` folds the Textual-agnostic
:class:`LiveRunState` ledger into an ordered, status-bearing,
live-ticking-timer row list that the Dashboard tab renders. These tests pin
the *content + ordering* without a TTY (mirroring the ``format_header`` seam);
the Pilot test in ``test_interactive_app.py`` covers the widget rendering.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ralph_afk import events as events_module
from ralph_afk.interactive.state import (
    STATUS_ACTIVE,
    STATUS_ADVANCED,
    STATUS_CLOSED,
    STATUS_GONE,
    STATUS_NO_PROGRESS,
    STATUS_QUEUED,
    LiveRunState,
    format_duration,
    format_wall_clock,
    queue_rows,
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


def _make_state(clock: _FakeClock) -> LiveRunState:
    return LiveRunState(
        run_id="01QUEUE",
        model="claude-opus-4.8",
        reasoning_effort="max",
        monotonic=clock,
        wall_clock=lambda: _FIXED_WALL,
    )


def _collect(state: LiveRunState, *issues: int) -> None:
    state.render(
        {"type": events_module.WRAPPER_AFK_READY_COLLECTED, "issues": list(issues)}
    )


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


def test_format_duration_renders_h_mm_ss() -> None:
    assert format_duration(0) == "0:00:00"
    assert format_duration(65) == "0:01:05"
    assert format_duration(3661) == "1:01:01"


# ---------------------------------------------------------------------------
# format_wall_clock — 12-hour AM/PM local stamp (issue #33; reused by #37)
# ---------------------------------------------------------------------------


def test_format_wall_clock_renders_12_hour_am_pm() -> None:
    # Afternoon: 13:42:07 -> 1:42:07 PM (hour leading zero stripped).
    assert format_wall_clock(datetime(2026, 6, 21, 13, 42, 7)) == "1:42:07 PM"
    # Morning single-digit hour: only the HOUR loses its leading zero; the
    # minute/second zero-padding is preserved.
    assert format_wall_clock(datetime(2026, 6, 21, 9, 8, 5)) == "9:08:05 AM"


def test_format_wall_clock_handles_noon_and_midnight() -> None:
    assert format_wall_clock(datetime(2026, 6, 21, 12, 0, 0)) == "12:00:00 PM"
    assert format_wall_clock(datetime(2026, 6, 21, 0, 5, 3)) == "12:05:03 AM"


def test_format_wall_clock_none_renders_placeholder() -> None:
    # An issue not yet active (no Started time) renders the em-dash placeholder.
    assert format_wall_clock(None) == "—"


# ---------------------------------------------------------------------------
# queue_rows — population + status
# ---------------------------------------------------------------------------


def test_empty_ledger_yields_no_rows() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    assert queue_rows(state) == []


def test_collected_pool_lists_every_issue_as_queued() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 26, 27, 28)

    rows = queue_rows(state)
    assert [r.ref for r in rows] == [26, 27, 28]
    assert all(r.status == STATUS_QUEUED for r in rows)
    assert all(not r.is_active for r in rows)
    assert [r.label for r in rows] == ["#26", "#27", "#28"]


# ---------------------------------------------------------------------------
# queue_rows — ordering: active first, then queued, then completed
# ---------------------------------------------------------------------------


def test_rows_ordered_active_then_queued_then_completed() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    # First-seen order 26, 27, 28, 29 — deliberately NOT the display order.
    _collect(state, 26, 27, 28, 29)

    # 26 was closed in an earlier iteration (completed history).
    state.stream_message("<working issue=26>")
    state.render({"type": events_module.WRAPPER_COMMIT_RECORDED})
    state.render({"type": events_module.WRAPPER_AUTO_CLOSE, "issue": 26})
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    # 28 is the current active issue this iteration.
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    _collect(state, 27, 28, 29)
    state.stream_message("<working issue=28>")

    rows = queue_rows(state)
    # Active (28) first, then queued (27, 29 in first-seen order), then the
    # completed history row (26).
    assert [r.ref for r in rows] == [28, 27, 29, 26]
    assert rows[0].status == STATUS_ACTIVE
    assert rows[0].is_active is True
    assert rows[1].status == STATUS_QUEUED
    assert rows[2].status == STATUS_QUEUED
    assert rows[3].status == STATUS_CLOSED


def test_completed_group_includes_advanced_no_progress_and_gone() -> None:
    clock = _FakeClock()
    state = _make_state(clock)

    # iter 1: 41 advances (commit, no close), 42 goes (queued then absent).
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 41, 42)
    state.stream_message("<working issue=41>")
    state.render({"type": events_module.WRAPPER_COMMIT_RECORDED})
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    # iter 2: 43 makes no progress (a strike); 42 absent -> gone.
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    _collect(state, 43)
    state.stream_message("<working issue=43>")
    state.render(
        {"type": events_module.WRAPPER_STRIKE, "strikes": 1, "max_strikes": 3}
    )
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    by_ref = {r.ref: r for r in queue_rows(state)}
    assert by_ref[41].status == STATUS_ADVANCED
    assert by_ref[42].status == STATUS_GONE
    assert by_ref[43].status == STATUS_NO_PROGRESS
    # None are active or queued -> all in the trailing completed group.
    refs = [r.ref for r in queue_rows(state)]
    assert set(refs[-3:]) == {41, 42, 43}


# ---------------------------------------------------------------------------
# queue_rows — live-ticking Active timer + per-issue Started wall clock
# ---------------------------------------------------------------------------


def test_active_row_active_timer_ticks_and_started_wall_is_set() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 50)
    clock.advance(7)  # 7s queued before the marker
    state.stream_message("<working issue=50>")
    clock.advance(20)  # 20s active so far

    row = queue_rows(state)[0]
    assert row.is_active is True
    # Started is the wall clock of when the issue first became active (the
    # marker fired at monotonic 7, i.e. 7s after the run-start reference).
    assert row.started_wall == _FIXED_WALL + timedelta(seconds=7)
    assert row.active_seconds == 20.0  # ticks against the clock

    clock.advance(5)
    assert queue_rows(state)[0].active_seconds == 25.0


def test_queued_row_has_no_started_wall() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 60)
    clock.advance(12)

    row = queue_rows(state)[0]
    assert row.status == STATUS_QUEUED
    # A still-queued issue has never been active -> no Started time yet.
    assert row.started_wall is None
    assert row.active_seconds == 0.0


def test_started_wall_is_set_once_across_revisits() -> None:
    """Started is the FIRST activation's wall clock; a later revisit never moves
    it, even though the issue's Active time keeps summing (issue #33 / CONTEXT)."""
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 70)
    clock.advance(5)
    state.stream_message("<working issue=70>")  # first active at monotonic 5
    clock.advance(10)
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    # A later iteration works #70 again, much later on the wall clock.
    clock.advance(900)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    _collect(state, 70)
    state.stream_message("<working issue=70>")

    row = queue_rows(state)[0]
    assert row.is_active is True
    assert row.started_wall == _FIXED_WALL + timedelta(seconds=5)  # frozen


def test_active_seconds_sums_across_iterations() -> None:
    """The Active duration carries across iterations and keeps ticking on the
    revisit — the queue keeps one entry per issue (issue #33 / CONTEXT.md)."""
    clock = _FakeClock()
    state = _make_state(clock)
    # iter 1: #90 is active for 10s, then the iteration ends (folding 10s in).
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 90)
    state.stream_message("<working issue=90>")
    clock.advance(10)
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    # iter 2: #90 is worked again; its Active resumes from the carried 10s.
    clock.advance(100)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    _collect(state, 90)
    state.stream_message("<working issue=90>")
    assert queue_rows(state)[0].active_seconds == 10.0  # iter-1 time carried
    clock.advance(6)
    assert queue_rows(state)[0].active_seconds == 16.0  # ticks on, summed


def test_now_override_is_used_for_the_active_timer() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 80)
    state.stream_message("<working issue=80>")  # active at monotonic 0
    # now-override drives the active timer regardless of the clock value.
    row = queue_rows(state, now=9.0)[0]
    assert row.active_seconds == 9.0


def test_completed_row_timers_are_frozen() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 80)
    clock.advance(3)
    state.stream_message("<working issue=80>")
    clock.advance(10)
    state.render({"type": events_module.WRAPPER_COMMIT_RECORDED})
    state.render({"type": events_module.WRAPPER_AUTO_CLOSE, "issue": 80})
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    frozen = queue_rows(state)[0]
    assert frozen.status == STATUS_CLOSED
    assert frozen.active_seconds == 10.0
    clock.advance(1000)
    assert queue_rows(state)[0].active_seconds == 10.0  # still frozen
