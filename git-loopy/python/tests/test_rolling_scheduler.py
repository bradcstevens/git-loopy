"""Tests for :mod:`git_loopy.rolling_scheduler` — the Rolling dispatch core.

Covers PRD #219 §1 ("Rolling scheduler state machine") and §3 ("Reservation,
setup, and Lane-work boundary"), plus the §7 bookkeeping the scheduler owns:
reusable **Lane** slots, provisional reservations, **Lane contribution**
lifecycle, the Run-scoped worked guard, ``max_iterations`` reservation, the H=2
**Integration** backlog, the serial-demand latch, and quiescence.

Every test drives the public :class:`~git_loopy.rolling_scheduler.RollingScheduler`
surface over a real :class:`~git_loopy.rolling_pool.RollingPool` and a scripted
:class:`~git_loopy.sources.RollingIssueSource` — no sleeping, no monkeypatching,
no reaching inside.
"""

from __future__ import annotations

import logging

from git_loopy.rolling_pool import RollingPool
from git_loopy.rolling_scheduler import RollingScheduler
from git_loopy.sources import (
    AfkReadyItem,
    MembershipSnapshot,
    Pickup,
    PoolCandidate,
    PICKUP_VALIDATED,
)


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger(f"git_loopy.tests.rolling_scheduler.{id(object())}")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def _candidate(ref: int) -> PoolCandidate:
    return PoolCandidate(
        ref=ref, title=f"issue {ref}", labels=("ready-for-agent", "parallel-safe")
    )


class ScriptedSource:
    """A :class:`RollingIssueSource` whose membership is a fixed ref list."""

    def __init__(self, refs: list[int]) -> None:
        self.refs = list(refs)
        self.complete = True
        self.membership_calls = 0
        self.pickup_calls: list[int | str] = []

    def shallow_membership(self) -> MembershipSnapshot:
        self.membership_calls += 1
        return MembershipSnapshot(
            candidates=tuple(_candidate(r) for r in self.refs), complete=self.complete
        )

    def pickup(self, ref: int | str) -> Pickup:
        self.pickup_calls.append(ref)
        return Pickup(
            outcome=PICKUP_VALIDATED,
            item=AfkReadyItem(
                ref=ref,
                title=f"issue {ref}",
                rendered_block=f"### Issue #{ref}",
                labels=("ready-for-agent", "parallel-safe"),
            ),
        )


class Clock:
    """An injected monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _scheduler(
    refs: list[int], *, lane_cap: int = 3, max_iterations: int = 0
) -> tuple[RollingScheduler, ScriptedSource]:
    scheduler, source, _clock = _scheduler_with_clock(
        refs, lane_cap=lane_cap, max_iterations=max_iterations
    )
    return scheduler, source


def _scheduler_with_clock(
    refs: list[int], *, lane_cap: int = 3, max_iterations: int = 0
) -> tuple[RollingScheduler, ScriptedSource, Clock]:
    diag = _silent_logger()
    source = ScriptedSource(refs)
    clock = Clock()
    pool = RollingPool(diag=diag, source=source, clock=clock, jitter=lambda i: i)
    scheduler = RollingScheduler(
        diag=diag, pool=pool, lane_cap=lane_cap, max_iterations=max_iterations
    )
    return scheduler, source, clock


# --------------------------------------------------------------------------- #
# §1.3-1.4 — one refill decision fills every currently refillable Lane
# --------------------------------------------------------------------------- #


def test_refill_reserves_every_free_lane_in_one_decision() -> None:
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=3)
    scheduler.start()

    reservations = scheduler.reserve()

    assert [r.item.ref for r in reservations] == [11, 12, 13]
    assert [r.lane_id for r in reservations] == ["L1", "L2", "L3"]


def test_single_eligible_issue_takes_one_lane() -> None:
    """#219 §1.4: the Wave-only 'two or more, otherwise serial' rule is retired."""
    scheduler, _source = _scheduler([11], lane_cap=3)
    scheduler.start()

    reservations = scheduler.reserve()

    assert [r.item.ref for r in reservations] == [11]


def test_effective_limit_starts_static_safe_at_three() -> None:
    """#219 §6: a Run starts at ``min(configured Lane cap, 3)``."""
    scheduler, _source = _scheduler([11, 12, 13, 14, 15], lane_cap=5)
    scheduler.start()

    assert scheduler.effective_limit == 3
    assert [r.item.ref for r in scheduler.reserve()] == [11, 12, 13]


def test_configured_cap_below_three_is_the_bound() -> None:
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=2)
    scheduler.start()

    assert scheduler.effective_limit == 2
    assert [r.item.ref for r in scheduler.reserve()] == [11, 12]


# --------------------------------------------------------------------------- #
# §3.2-3.3 — a reservation is provisional until its agent session starts
# --------------------------------------------------------------------------- #


def test_setup_failure_releases_the_lane_and_leaves_no_trace() -> None:
    scheduler, _source = _scheduler([11], lane_cap=1, max_iterations=5)
    scheduler.start()
    (reservation,) = scheduler.reserve()

    scheduler.release(reservation)

    assert scheduler.finalized == ()
    assert scheduler.remaining_units == 5
    assert scheduler.reserve() != ()


def test_a_reservation_still_in_setup_holds_its_lane() -> None:
    scheduler, _source = _scheduler([11, 12], lane_cap=1)
    scheduler.start()

    first = scheduler.reserve()
    second = scheduler.reserve()

    assert [r.item.ref for r in first] == [11]
    assert second == ()


def test_a_refresh_cannot_re_offer_a_ref_already_in_setup() -> None:
    """A provisional reservation excludes its ref before the worked guard exists."""
    scheduler, source, clock = _scheduler_with_clock([11], lane_cap=3)
    scheduler.start()
    (first,) = scheduler.reserve()

    source.refs = [11, 12]
    clock.advance(120.0)
    again = scheduler.reserve()

    assert first.item.ref == 11
    assert [r.item.ref for r in again] == [12]


# --------------------------------------------------------------------------- #
# §3.4, §1.7, §7.9 — the agent session start is where a contribution begins
# --------------------------------------------------------------------------- #


def test_session_start_opens_a_contribution_and_spends_one_unit() -> None:
    scheduler, _source = _scheduler([11], lane_cap=1, max_iterations=5)
    scheduler.start()
    (reservation,) = scheduler.reserve()

    contribution = scheduler.start_session(reservation)

    assert contribution.contribution_id == "c1"
    assert contribution.ref == 11
    assert contribution.lane_id == "L1"
    assert scheduler.remaining_units == 4
    assert scheduler.open_count == 1


def test_contribution_ids_are_unique_within_the_run() -> None:
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=3)
    scheduler.start()

    ids = [scheduler.start_session(r).contribution_id for r in scheduler.reserve()]

    assert ids == ["c1", "c2", "c3"]


def test_worked_guard_latches_at_session_start_and_never_releases() -> None:
    """#219 §1.7: one issue may take at most one Lane in a Run.

    The source keeps listing 11 — it is still open and still labelled, because
    a terminal unpublished contribution does not close anything. Only the
    Run-scoped guard keeps it out of a second Lane.
    """
    scheduler, source, clock = _scheduler_with_clock([11], lane_cap=2)
    scheduler.start()
    (reservation,) = scheduler.reserve()
    contribution = scheduler.start_session(reservation)
    scheduler.finish_work(contribution, changed=False)

    clock.advance(120.0)
    assert scheduler.reserve() == ()
    assert source.refs == [11]
    assert source.membership_calls > 1


# --------------------------------------------------------------------------- #
# §3.8-3.11, §7.6 — the terminal dispositions at the Lane-work boundary
# --------------------------------------------------------------------------- #


def test_unchanged_branch_finalizes_terminal_unpublished_with_one_strike() -> None:
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])

    disposition = scheduler.finish_work(contribution, changed=False)

    assert disposition == "terminal"
    assert scheduler.finalized == (contribution,)
    assert contribution.published is False
    assert contribution.reason == "unchanged_branch"
    assert contribution.strike_reaction == "+1"
    assert scheduler.admitted == ()


def test_checkpoint_failure_never_admits_and_adds_one_strike() -> None:
    """#219 §3.10: a failed Checkpoint may never admit incomplete state."""
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])

    disposition = scheduler.finish_work(
        contribution, changed=True, checkpoint_ok=False
    )

    assert disposition == "terminal"
    assert contribution.reason == "checkpoint_failed"
    assert contribution.strike_reaction == "+1"
    assert scheduler.admitted == ()


def test_a_terminal_contribution_releases_its_lane_for_refill() -> None:
    scheduler, _source = _scheduler([11, 12], lane_cap=1)
    scheduler.start()
    first = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(first, changed=False)

    refill = scheduler.reserve()

    assert [(r.lane_id, r.item.ref) for r in refill] == [("L1", 12)]


# --------------------------------------------------------------------------- #
# §3.9, §4.1-4.3 — a changed branch is offered; H = 2 decides admitted vs parked
# --------------------------------------------------------------------------- #


def test_a_changed_branch_is_admitted_and_frees_its_lane_immediately() -> None:
    """#219 §4.2: admission releases the Lane while the contribution lives on."""
    scheduler, _source = _scheduler([11, 12], lane_cap=1)
    scheduler.start()
    first = scheduler.start_session(scheduler.reserve()[0])

    disposition = scheduler.finish_work(first, changed=True)

    assert disposition == "admitted"
    assert scheduler.admitted == (first,)
    assert scheduler.open_count == 1
    assert [(r.lane_id, r.item.ref) for r in scheduler.reserve()] == [("L1", 12)]


def test_a_checkpoint_only_branch_counts_as_changed() -> None:
    """#219 §3.9: a Checkpoint-only branch is durable work and may integrate."""
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])

    assert scheduler.finish_work(contribution, changed=True) == "admitted"


def test_h_is_exactly_two_and_the_third_finisher_parks() -> None:
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=3)
    scheduler.start()
    contributions = [scheduler.start_session(r) for r in scheduler.reserve()]

    dispositions = [
        scheduler.finish_work(c, changed=True) for c in contributions
    ]

    assert dispositions == ["admitted", "admitted", "parked"]
    assert scheduler.admitted == (contributions[0], contributions[1])
    assert scheduler.parked == (contributions[2],)


def test_a_parked_contribution_retains_its_lane() -> None:
    """#219 §4.3 / §1.5: a parked finisher still owns its Lane slot."""
    scheduler, source = _scheduler([11, 12, 13], lane_cap=3)
    scheduler.start()
    contributions = [scheduler.start_session(r) for r in scheduler.reserve()]
    for c in contributions:
        scheduler.finish_work(c, changed=True)

    source.refs = [14, 15]

    assert [r.item.ref for r in scheduler.reserve()] == [14, 15]


# --------------------------------------------------------------------------- #
# §4.4, §4.12, §4.14, §7.4-7.6 — finalizing an admitted contribution
# --------------------------------------------------------------------------- #


def test_published_contribution_resets_strike_and_frees_the_h_slot() -> None:
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(contribution, changed=True)

    admitted = scheduler.finalize(contribution, published=True)

    assert admitted == ()
    assert contribution.reason == "published"
    assert contribution.strike_reaction == "reset"
    assert scheduler.admitted == ()
    assert scheduler.open_count == 0


def test_recovery_exhaustion_is_terminal_unpublished_and_requests_serial() -> None:
    """#219 §4.14: one Strike, and validated serial demand latches at once."""
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(contribution, changed=True)

    scheduler.finalize(contribution, published=False, reason="serial_fallback")

    assert contribution.published is False
    assert contribution.strike_reaction == "+1"
    assert scheduler.serial_latched is True


def test_freeing_an_h_slot_admits_the_parked_fifo_head() -> None:
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=3)
    scheduler.start()
    a, b, c = (scheduler.start_session(r) for r in scheduler.reserve())
    for contribution in (a, b, c):
        scheduler.finish_work(contribution, changed=True)

    newly = scheduler.finalize(a, published=True)

    assert newly == (c,)
    assert scheduler.admitted == (b, c)
    assert scheduler.parked == ()


def test_a_newly_finished_lower_number_cannot_overtake_older_admitted_work() -> None:
    """#219 §4.4: the admitted backlog drains FIFO, not by issue number."""
    scheduler, source = _scheduler([21, 22], lane_cap=2)
    scheduler.start()
    older, second = (scheduler.start_session(r) for r in scheduler.reserve())
    scheduler.finish_work(older, changed=True)
    scheduler.finish_work(second, changed=True)

    source.refs = [11]
    (late,) = scheduler.reserve()
    late_contribution = scheduler.start_session(late)
    assert scheduler.finish_work(late_contribution, changed=True) == "parked"

    assert scheduler.finalize(older, published=True) == (late_contribution,)
    assert scheduler.admitted == (second, late_contribution)


def test_contributions_parked_in_one_turn_admit_by_ascending_issue_number() -> None:
    """#219 §4.3: same-turn ties use ascending issue number, not finish order."""
    scheduler, source = _scheduler([11, 12, 44], lane_cap=3)
    scheduler.start()
    first, second, high = (scheduler.start_session(r) for r in scheduler.reserve())
    scheduler.finish_work(first, changed=True)
    scheduler.finish_work(second, changed=True)

    source.refs = [22]
    (late,) = scheduler.reserve()
    low = scheduler.start_session(late)
    # H is full, so both park without any admission decision in between —
    # one scheduler turn, and 44 finished first.
    assert scheduler.finish_work(high, changed=True) == "parked"
    assert scheduler.finish_work(low, changed=True) == "parked"

    assert scheduler.parked == (low, high)
    assert scheduler.finalize(first, published=True) == (low,)
    assert scheduler.finalize(second, published=True) == (high,)


# --------------------------------------------------------------------------- #
# §5 — full-drain alternating serial interleave
# --------------------------------------------------------------------------- #


def test_serial_demand_stops_new_reservations() -> None:
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=3)
    scheduler.start()

    scheduler.request_serial(ref=99, reason="not_parallel_safe")

    assert scheduler.refillable == 0
    assert scheduler.reserve() == ()


def test_serial_demand_does_not_cancel_setup_already_in_progress() -> None:
    """#219 §5.4: an existing reservation still completes setup and starts."""
    scheduler, _source = _scheduler([11], lane_cap=2)
    scheduler.start()
    (reservation,) = scheduler.reserve()

    scheduler.request_serial(ref=99, reason="not_parallel_safe")
    contribution = scheduler.start_session(reservation)

    assert contribution.ref == 11
    assert scheduler.open_count == 1


def test_serial_ownership_waits_for_full_parallel_quiescence() -> None:
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.request_serial(ref=99, reason="not_parallel_safe")

    assert scheduler.quiescent is False
    assert scheduler.serial_turn() is False

    scheduler.finish_work(contribution, changed=True)
    assert scheduler.quiescent is False  # admitted work is still in flight

    scheduler.finalize(contribution, published=True)
    assert scheduler.quiescent is True
    assert scheduler.serial_turn() is True


def test_phase_tracks_the_serial_handoff() -> None:
    """#219 §9: draining for serial -> serial ownership -> rolling refill turn."""
    scheduler, source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    assert scheduler.phase == "rolling"

    scheduler.request_serial(ref=99, reason="not_parallel_safe")
    assert scheduler.phase == "draining_for_serial"

    scheduler.finish_work(contribution, changed=False)
    scheduler.serial_turn()
    assert scheduler.phase == "serial_ownership"

    source.refs = [12]
    scheduler.serial_finished()
    assert scheduler.phase == "rolling_refill_turn"

    assert [r.item.ref for r in scheduler.reserve()] == [12]
    assert scheduler.phase == "rolling"


def test_serial_demand_relatches_only_after_one_full_refill_turn() -> None:
    """#219 §5.9-5.10: the refill turn is granted before serial may relatch."""
    scheduler, source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.request_serial(ref=98, reason="not_parallel_safe")
    scheduler.finish_work(contribution, changed=False)
    scheduler.serial_turn()
    scheduler.serial_finished()

    scheduler.request_serial(ref=99, reason="not_parallel_safe")
    source.refs = [12]

    assert [r.item.ref for r in scheduler.reserve()] == [12]
    assert scheduler.serial_latched is True
    assert scheduler.reserve() == ()


# --------------------------------------------------------------------------- #
# §7.7, §7.10, §7.16-7.17 — cap, abort drain, and Run-boundary termination
# --------------------------------------------------------------------------- #


def test_strike_limit_latches_a_drain_confirmed_abort() -> None:
    scheduler, _source = _scheduler([11, 12], lane_cap=2)
    scheduler.start()
    scheduler.start_session(scheduler.reserve()[0])

    scheduler.strike_limit_reached()

    assert scheduler.phase == "draining_for_abort"
    assert scheduler.refillable == 0
    assert scheduler.reserve() == ()
    assert scheduler.open_count == 1  # §7.7: started work still finishes


def test_a_later_publication_cancels_the_pending_abort() -> None:
    scheduler, source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(contribution, changed=True)
    scheduler.strike_limit_reached()

    scheduler.finalize(contribution, published=True)

    assert scheduler.phase == "rolling"
    source.refs = [12]
    assert [r.item.ref for r in scheduler.reserve()] == [12]


def test_the_iteration_cap_stops_refill_and_drains() -> None:
    """#219 §7.9-7.10: each session start spends one unit; the cap ends refill."""
    scheduler, source = _scheduler([11, 12], lane_cap=3, max_iterations=1)
    scheduler.start()
    (reservation,) = scheduler.reserve()
    scheduler.start_session(reservation)

    assert scheduler.remaining_units == 0
    assert scheduler.refillable == 0
    assert scheduler.reserve() == ()


def test_a_provisional_reservation_withholds_its_cap_unit_from_refill() -> None:
    """#219 §1.3: concurrent setup cannot oversubscribe max_iterations."""
    scheduler, _source = _scheduler([11, 12, 13], lane_cap=3, max_iterations=2)
    scheduler.start()

    assert [r.item.ref for r in scheduler.reserve()] == [11, 12]


def test_empty_pool_end_requires_the_final_authoritative_refresh() -> None:
    scheduler, source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(contribution, changed=True)
    scheduler.finalize(contribution, published=True)

    source.refs = []
    source.complete = False
    assert scheduler.confirm_empty() is False

    source.complete = True
    assert scheduler.confirm_empty() is True


def test_run_end_is_invalid_until_the_pipeline_is_quiescent() -> None:
    """#219 §7.16: no wrapper.run.end while anything is still open."""
    scheduler, _source = _scheduler([11], lane_cap=1)
    scheduler.start()
    contribution = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(contribution, changed=True)

    assert scheduler.quiescent is False

    scheduler.finalize(contribution, published=True)

    assert scheduler.quiescent is True


# --------------------------------------------------------------------------- #
# §1.5, §7.2 — a contribution outlives the Lane slot it started in
# --------------------------------------------------------------------------- #


def test_a_reused_lane_does_not_disturb_the_original_contribution() -> None:
    """The whole reason Parallel accounting is keyed by contribution, not Lane."""
    scheduler, source = _scheduler([11], lane_cap=1)
    scheduler.start()
    first = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(first, changed=True)

    source.refs = [12]
    (reused,) = scheduler.reserve()
    second = scheduler.start_session(reused)

    assert reused.lane_id == "L1"
    assert second.lane_id == "L1"
    assert first.lane_id == "L1"
    assert first.contribution_id != second.contribution_id
    assert scheduler.admitted == (first,)
    assert scheduler.open_count == 2


# --------------------------------------------------------------------------- #
# §2.6-2.7 — no polling without unmet demand
# --------------------------------------------------------------------------- #


def test_no_membership_read_while_every_lane_is_busy() -> None:
    scheduler, source = _scheduler([11], lane_cap=1)
    scheduler.start()
    scheduler.start_session(scheduler.reserve()[0])
    reads = source.membership_calls

    scheduler.reserve()

    assert source.membership_calls == reads


def test_no_membership_read_while_serial_is_latched() -> None:
    scheduler, source = _scheduler([11, 12], lane_cap=3)
    scheduler.start()
    scheduler.request_serial(ref=99, reason="not_parallel_safe")
    reads = source.membership_calls

    scheduler.reserve()

    assert source.membership_calls == reads


def test_no_membership_read_while_draining_for_abort() -> None:
    scheduler, source = _scheduler([11, 12], lane_cap=3)
    scheduler.start()
    scheduler.strike_limit_reached()
    reads = source.membership_calls

    scheduler.reserve()

    assert source.membership_calls == reads
