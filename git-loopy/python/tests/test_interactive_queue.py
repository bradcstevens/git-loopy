"""Tests for the pure **Queue projection** in ``git_loopy.interactive.state``
(issue #26 — live Queue rendering the #25 per-run ledger).

:func:`~git_loopy.interactive.state.queue_rows` folds the Textual-agnostic
:class:`LiveRunState` ledger into an ordered, status-bearing,
live-ticking-timer row list that the Dashboard tab renders. These tests pin
the *content + ordering* without a TTY (mirroring the ``format_header`` seam);
the Pilot test in ``test_interactive_app.py`` covers the widget rendering.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from git_loopy import events as events_module
from git_loopy.interactive.state import (
    STATUS_ACTIVE,
    STATUS_ADVANCED,
    STATUS_CLOSED,
    STATUS_GONE,
    STATUS_NO_PROGRESS,
    STATUS_QUEUED,
    IssueLedgerEntry,
    LiveRunState,
    format_duration,
    format_wall_clock,
    queue_rows,
)
from git_loopy.pricing import ModelPricing, Pricing, estimate_cost
from git_loopy.ui.summary import RunSummary
from git_loopy.usage import UsageTally


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


def _usage(state: LiveRunState, *, model: str | None, tin: int, tout: int) -> None:
    """Drive one ``usage.tokens`` session event into the state sink."""
    state.render(
        {
            "type": events_module.USAGE_TOKENS,
            "model": model,
            "input": tin,
            "output": tout,
        }
    )


#: A small, explicit pricing table so per-issue cost is exactly assertable and
#: independent of the packaged ``pricing.toml`` figures (15 / 75 USD per Mtok —
#: the ``claude-opus-4.8`` shape).
_PRICING = Pricing(
    models={
        "claude-opus-4.8": ModelPricing(
            input_per_mtok=Decimal("15"),
            output_per_mtok=Decimal("75"),
            context_window=200_000,
        )
    }
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


# ---------------------------------------------------------------------------
# queue_rows — per-issue consumption: tokens in / out + model for cost (#36)
# ---------------------------------------------------------------------------


def test_usage_accrues_tokens_and_model_to_the_active_issue() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 36, 37)
    state.stream_message("<working issue=36>")
    _usage(state, model="claude-opus-4.8", tin=1000, tout=200)

    by_ref = {r.ref: r for r in queue_rows(state)}
    # The Active issue carries the iteration's tokens + the usage event's model.
    assert by_ref[36].usage.tokens_in == 1000
    assert by_ref[36].usage.tokens_out == 200
    assert by_ref[36].usage.model == "claude-opus-4.8"
    # A still-queued issue accrues nothing (no usage attributed to it).
    assert by_ref[37].usage.tokens_in == 0
    assert by_ref[37].usage.tokens_out == 0
    assert by_ref[37].usage.model is None


def test_issue_ledger_entry_embeds_usage_tally_and_delegates() -> None:
    """#41: a ledger entry's Consumption lives in a shared ``UsageTally``.

    The per-issue accrual rule (*first non-None model wins; tokens sum*) is the
    ``UsageTally``'s — not a second copy in ``state.py``. ``_record_usage`` /
    ``_accrue_usage`` / the pending pre-marker buffer / ``_flush_pending_usage``
    all fold through :meth:`UsageTally.add` / :meth:`~UsageTally.merge`, and the
    Queue row carries that same tally whole (issue #42).
    """
    # A fresh ledger entry default-constructs a real UsageTally.
    entry = IssueLedgerEntry(ref=1, first_seen_at=0.0, first_seen_iter=1)
    assert isinstance(entry.usage, UsageTally)

    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 42)
    state.stream_message("<working issue=42>")
    # A leading None model, then the authoritative model, then a *different*
    # non-None model that must NOT overwrite it (first non-None wins absolutely).
    _usage(state, model=None, tin=10, tout=5)
    _usage(state, model="claude-opus-4.8", tin=20, tout=5)
    _usage(state, model="gpt-5.4", tin=1, tout=1)

    # The rule now lives solely in the tally on the entry.
    tally = state.ledger[42].usage
    assert isinstance(tally, UsageTally)
    assert tally.model == "claude-opus-4.8"
    assert tally.tokens_in == 31
    assert tally.tokens_out == 11
    # The Queue projection carries the whole tally (issue #42).
    row = queue_rows(state)[0]
    assert (row.usage.tokens_in, row.usage.tokens_out, row.usage.model) == (
        31,
        11,
        "claude-opus-4.8",
    )


def test_queue_row_carries_usage_tally_and_derives_cost() -> None:
    """#42: a Queue row carries the ledger entry's shared ``UsageTally`` whole.

    ``queue_rows`` passes ``entry.usage`` straight through (no flattened
    token / model copy on the row), so the per-issue **Cost** derives from
    :meth:`UsageTally.cost` — the one unknown-model em-dash guard every Cost
    figure shares — over the *same* object the ledger accrues into.
    """
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 55, 56)  # #55 is worked; #56 stays queued (no usage)
    state.stream_message("<working issue=55>")
    _usage(state, model="claude-opus-4.8", tin=1000, tout=200)

    by_ref = {r.ref: r for r in queue_rows(state)}
    worked = by_ref[55]
    # The row carries the whole tally — the *same* object the ledger entry
    # accrues into (passed straight through, not a flattened copy).
    assert isinstance(worked.usage, UsageTally)
    assert worked.usage is state.ledger[55].usage
    assert (worked.usage.tokens_in, worked.usage.tokens_out, worked.usage.model) == (
        1000,
        200,
        "claude-opus-4.8",
    )
    # The per-issue Cost derives from the tally's own guard: 1000 * 15/1e6 +
    # 200 * 75/1e6 = 0.0300 for the priced model (the widget renders "$0.0300").
    assert worked.usage.cost(_PRICING) == Decimal("0.0300")
    # A still-queued issue carries a default tally (None model) whose cost is
    # None — the em-dash source the widget renders, never zero.
    assert by_ref[56].usage.cost(_PRICING) is None


def test_usage_sums_across_iterations_for_one_issue() -> None:
    """Per-issue tokens sum across every iteration that worked the issue — the
    queue keeps one entry per issue (issue #36 / CONTEXT.md)."""
    clock = _FakeClock()
    state = _make_state(clock)
    # iter 1: #90 active, two usage events.
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 90)
    state.stream_message("<working issue=90>")
    _usage(state, model="claude-opus-4.8", tin=1000, tout=200)
    _usage(state, model="claude-opus-4.8", tin=300, tout=50)
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    # iter 2: #90 worked again; its tokens resume from the carried totals.
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 2})
    _collect(state, 90)
    state.stream_message("<working issue=90>")
    _usage(state, model="claude-opus-4.8", tin=500, tout=100)

    row = queue_rows(state)[0]
    assert row.usage.tokens_in == 1000 + 300 + 500
    assert row.usage.tokens_out == 200 + 50 + 100


def test_pre_marker_usage_is_attributed_to_the_active_issue() -> None:
    """Usage produced before the working marker lands is held pending and
    flushed onto the active issue once it is known (mirrors the Log)."""
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 50)
    # Tokens arrive BEFORE the marker (no active issue yet).
    _usage(state, model="claude-opus-4.8", tin=700, tout=90)
    state.stream_message("<working issue=50>")
    # And more after activation.
    _usage(state, model="claude-opus-4.8", tin=100, tout=10)

    row = queue_rows(state)[0]
    assert row.ref == 50
    assert row.usage.tokens_in == 800
    assert row.usage.tokens_out == 100
    assert row.usage.model == "claude-opus-4.8"


def test_usage_attributed_via_closure_backstop_when_no_marker() -> None:
    """With no working marker, the ``Closes #N`` backstop activates the issue at
    closure time; pre-activation usage is still attributed to it (issue #36)."""
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 61)
    # All usage arrives before the (late) activation — no marker this iteration.
    _usage(state, model="claude-opus-4.8", tin=1200, tout=300)
    state.render({"type": events_module.WRAPPER_COMMIT_RECORDED})
    state.render({"type": events_module.WRAPPER_AUTO_CLOSE, "issue": 61})
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    row = queue_rows(state)[0]
    assert row.ref == 61
    assert row.status == STATUS_CLOSED
    assert row.usage.tokens_in == 1200
    assert row.usage.tokens_out == 300


def test_orphan_usage_without_an_active_issue_is_not_attributed() -> None:
    """Usage in an iteration that never produces an active issue (no marker, no
    closure, multi-issue pool) is discarded at the next iteration boundary — the
    same orphan treatment as pre-marker Log output."""
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 71, 72)  # two issues, so no single-member inference
    _usage(state, model="claude-opus-4.8", tin=999, tout=99)
    state.render({"type": events_module.WRAPPER_ITERATION_END})

    for row in queue_rows(state):
        assert row.usage.tokens_in == 0
        assert row.usage.tokens_out == 0
        assert row.usage.model is None


def test_unknown_model_cost_is_none_not_a_crash() -> None:
    """An issue worked on a model absent from the pricing table keeps its tokens
    but yields ``None`` cost — the existing unknown-model treatment (the renderer
    shows the em dash), never a crash (issue #36 acceptance criterion)."""
    clock = _FakeClock()
    state = _make_state(clock)
    state.render({"type": events_module.WRAPPER_ITERATION_START, "iter": 1})
    _collect(state, 80)
    state.stream_message("<working issue=80>")
    _usage(state, model="mystery-model", tin=400, tout=60)

    row = queue_rows(state)[0]
    assert row.usage.model == "mystery-model"
    assert row.usage.tokens_in == 400
    # The unknown model yields None cost (not zero, not a crash) — the em-dash
    # source. UsageTally.cost now owns the guard (issue #42), and it agrees with
    # the underlying estimate_cost oracle.
    assert row.usage.cost(_PRICING) is None
    assert (
        estimate_cost(
            row.usage.model, row.usage.tokens_in, row.usage.tokens_out, _PRICING
        )
        is None
    )


def test_per_issue_usage_reconciles_with_run_summary_totals() -> None:
    """Summing per-issue tokens + cost reconciles with the run-level Summary
    totals (issue #36 acceptance criterion). The same usage drives both: the
    per-issue ledger (Active-issue attribution) and a ``RunSummary`` (per
    iteration), and the totals agree."""
    clock = _FakeClock()
    state = _make_state(clock)
    summary = RunSummary(pricing=_PRICING)

    # Three iterations: #100 worked twice, #101 once, each with its own usage.
    plan = [
        (1, 100, 1000, 200),
        (2, 100, 500, 100),
        (3, 101, 300, 50),
    ]
    for iter_num, issue, tin, tout in plan:
        state.render(
            {"type": events_module.WRAPPER_ITERATION_START, "iter": iter_num}
        )
        _collect(state, issue)
        state.stream_message(f"<working issue={issue}>")
        _usage(state, model="claude-opus-4.8", tin=tin, tout=tout)
        state.render({"type": events_module.WRAPPER_ITERATION_END})

        summary.on_iteration_start(iter_num=iter_num, issue_num=issue)
        summary.record_usage(model="claude-opus-4.8", tokens_in=tin, tokens_out=tout)
        summary.on_iteration_end()

    rows = queue_rows(state)
    totals = summary.totals()
    # Tokens reconcile exactly.
    assert sum(r.usage.tokens_in for r in rows) == totals.tokens_in
    assert sum(r.usage.tokens_out for r in rows) == totals.tokens_out
    # Per-issue accumulation: #100 summed both of its iterations.
    by_ref = {r.ref: r for r in rows}
    assert (by_ref[100].usage.tokens_in, by_ref[100].usage.tokens_out) == (1500, 300)
    assert (by_ref[101].usage.tokens_in, by_ref[101].usage.tokens_out) == (300, 50)
    # Cost reconciles too (cost is linear in tokens; one model across the run).
    per_issue_cost = Decimal(0)
    for r in rows:
        assert r.usage.model is not None  # every worked issue recorded its model
        per_issue_cost += r.usage.cost(_PRICING) or Decimal(0)
    assert per_issue_cost == totals.cost_usd
