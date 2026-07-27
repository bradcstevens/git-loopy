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

from git_loopy.rolling_concurrency import ConcurrencyController
from git_loopy.rolling_pool import RollingPool
from git_loopy.rolling_scheduler import RollingScheduler
from git_loopy.sources import (
    AfkReadyItem,
    MembershipSnapshot,
    Pickup,
    PoolCandidate,
    PICKUP_UNAVAILABLE,
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

    def __init__(self, refs: list[int], *, unavailable: set[int] | None = None) -> None:
        self.refs = list(refs)
        self.complete = True
        self.membership_calls = 0
        self.pickup_calls: list[int | str] = []
        self.unavailable = set(unavailable or ())

    def shallow_membership(self) -> MembershipSnapshot:
        self.membership_calls += 1
        return MembershipSnapshot(
            candidates=tuple(_candidate(r) for r in self.refs), complete=self.complete
        )

    def pickup(self, ref: int | str) -> Pickup:
        self.pickup_calls.append(ref)
        if ref in self.unavailable:
            return Pickup(outcome=PICKUP_UNAVAILABLE)
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
    refs: list[int],
    *,
    lane_cap: int = 3,
    max_iterations: int = 0,
    unavailable: set[int] | None = None,
) -> tuple[RollingScheduler, ScriptedSource]:
    scheduler, source, _clock = _scheduler_with_clock(
        refs,
        lane_cap=lane_cap,
        max_iterations=max_iterations,
        unavailable=unavailable,
    )
    return scheduler, source


def _scheduler_with_clock(
    refs: list[int],
    *,
    lane_cap: int = 3,
    max_iterations: int = 0,
    unavailable: set[int] | None = None,
) -> tuple[RollingScheduler, ScriptedSource, Clock]:
    diag = _silent_logger()
    source = ScriptedSource(refs, unavailable=unavailable)
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


def test_a_full_integration_backlog_stops_new_lane_work() -> None:
    """#219 §1.3, §4.1: a full **Integration backlog** is refill backpressure.

    Free Lane slots are not permission to start more work. Once H = 2
    admitted-but-not-landed contributions are in the backlog, **Integration** is
    the governing serialized resource and configured Lane capacity is
    deliberately idle (ADR-0020) — otherwise finished branches pile up, drift
    from base, conflict more often, and burn API and AI-credit capacity on
    recovery without increasing throughput.

    This bound subsumes the throttle §4.3 described as "a parked contribution
    retains its Lane slot": parking is only ever reachable *because* H is full,
    so retention can no longer change a refill outcome on its own. It survives
    as effective-concurrency accounting ("parked contributions consume their
    Lane slots", ADR-0020's reaction table), which is #309's.
    """
    scheduler, source = _scheduler([11, 12, 13, 14], lane_cap=3)
    scheduler.start()
    a, b, _c = (scheduler.start_session(r) for r in scheduler.reserve())
    scheduler.finish_work(a, changed=True)
    scheduler.finish_work(b, changed=True)

    # Both Lanes freed on admission, and #14 is still eligible.
    assert scheduler.admitted == (a, b)
    reads = source.membership_calls

    assert scheduler.refillable == 0
    assert scheduler.reserve() == ()
    # §2.6: no membership poll while Integration backpressure has stopped refill.
    assert source.membership_calls == reads


def test_backpressure_lifts_the_moment_an_h_slot_frees() -> None:
    """#219 §4.4: landing one contribution re-opens Rolling dispatch."""
    scheduler, _source = _scheduler([11, 12, 13, 14], lane_cap=3)
    scheduler.start()
    a, b, _c = (scheduler.start_session(r) for r in scheduler.reserve())
    scheduler.finish_work(a, changed=True)
    scheduler.finish_work(b, changed=True)

    scheduler.finalize(a, published=True)

    assert [r.item.ref for r in scheduler.reserve()] == [14]


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
    scheduler, _source = _scheduler([21, 22, 11], lane_cap=2)
    scheduler.start()
    older, second = (scheduler.start_session(r) for r in scheduler.reserve())
    # Admitting #21 frees its Lane while the backlog still has capacity, so the
    # lower-numbered #11 starts *after* both of the older contributions.
    scheduler.finish_work(older, changed=True)
    (late,) = scheduler.reserve()
    late_contribution = scheduler.start_session(late)
    scheduler.finish_work(second, changed=True)
    assert scheduler.finish_work(late_contribution, changed=True) == "parked"

    assert scheduler.finalize(older, published=True) == (late_contribution,)
    assert scheduler.admitted == (second, late_contribution)


def test_contributions_parked_in_one_turn_admit_by_ascending_issue_number() -> None:
    """#219 §4.3: same-turn ties use ascending issue number, not finish order."""
    scheduler, _source = _scheduler([11, 12, 44, 22], lane_cap=3)
    scheduler.start()
    first, second, high = (scheduler.start_session(r) for r in scheduler.reserve())
    # #11 lands an admission and frees its Lane, which #22 refills before the
    # backlog reaches H — so four contributions are live with H = 2.
    scheduler.finish_work(first, changed=True)
    low = scheduler.start_session(scheduler.reserve()[0])
    scheduler.finish_work(second, changed=True)

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


# --------------------------------------------------------------------------- #
# #304 — why Parallel mode is not engaging right now                           #
# --------------------------------------------------------------------------- #


def test_no_serial_fallback_while_eligible_lane_work_remains() -> None:
    """Eligible Parallel-safe work left is not a fallback, it is interleaving.

    A serial **Iteration** that runs alongside remaining Lane work is #219
    §5's drain-everything design, not Parallel mode failing to engage — so the
    scheduler answers "nothing to report".
    """
    scheduler, _source = _scheduler([11, 12], lane_cap=3)
    scheduler.start()

    assert scheduler.serial_fallback() is None


def test_serial_fallback_reports_no_parallel_safe_candidates() -> None:
    """The overwhelmingly common cause: nobody applied the label (#304).

    ``parallel-safe`` is a human assertion the runner never infers, so an empty
    **Pool** of eligible candidates with nothing worked yet means no
    ``ready-for-agent`` issue carries it.
    """
    scheduler, _source = _scheduler([], lane_cap=3)
    scheduler.start()

    fallback = scheduler.serial_fallback()

    assert fallback is not None
    assert fallback.reason == "no_parallel_safe_candidates"
    assert (fallback.eligible, fallback.unavailable, fallback.worked) == (0, 0, 0)


def test_serial_fallback_distinguishes_already_worked_candidates() -> None:
    """Eligible candidates existed — this Run already worked all of them (#304).

    A different operator situation from "label some issues": the Run-scoped
    worked guard (#219 §1.7) latches at agent-session start and never releases,
    so the same issue can never take a second Lane in this Run.
    """
    scheduler, _source = _scheduler([11], lane_cap=3)
    scheduler.start()
    (reservation,) = scheduler.reserve()
    scheduler.start_session(reservation)

    fallback = scheduler.serial_fallback()

    assert fallback is not None
    assert fallback.reason == "all_parallel_safe_worked"
    assert (fallback.eligible, fallback.unavailable, fallback.worked) == (0, 0, 1)


def test_serial_fallback_reports_unreadable_candidates_as_unavailable() -> None:
    """A quarantined candidate is labelled but unreadable (#219 §2.11, #304).

    Reporting it as "no candidate carries parallel-safe" would send the
    operator off to label work that is already labelled.
    """
    scheduler, _source = _scheduler([11], lane_cap=3, unavailable={11})
    scheduler.start()
    scheduler.reserve()

    fallback = scheduler.serial_fallback()

    assert fallback is not None
    assert fallback.reason == "parallel_safe_unavailable"
    assert (fallback.eligible, fallback.unavailable, fallback.worked) == (0, 1, 0)


def test_every_serial_fallback_reason_is_in_the_declared_vocabulary() -> None:
    """The reason is a closed vocabulary the conformance fixture pins."""
    from git_loopy import rolling_scheduler as module

    assert module.SERIAL_FALLBACK_REASONS == (
        "no_parallel_safe_candidates",
        "all_parallel_safe_worked",
        "parallel_safe_unavailable",
    )


# --------------------------------------------------------------------------- #
# §6 — bounded adaptive Lane concurrency                                       #
# --------------------------------------------------------------------------- #


def test_pressure_narrows_how_many_lanes_may_be_refilled() -> None:
    """#219 §6: the *effective* limit bounds refill; the configured cap never moves."""
    scheduler, _source = _scheduler([11, 12, 13, 14, 15, 16], lane_cap=6)
    scheduler.start()

    for _ in range(3):
        scheduler.observe_pressure(rate_limits=1)

    assert scheduler.lane_cap == 6
    assert scheduler.effective_limit == 1
    assert scheduler.refillable == 1
    assert [r.item.ref for r in scheduler.reserve()] == [11]


def test_the_scheduler_supplies_the_pipeline_half_of_an_observation() -> None:
    """#219 §6: the caller reports only telemetry it alone can see.

    H-full, parked work, **Lane** occupancy, and remaining demand are the
    scheduler's own state — asking an orchestrator to restate them would let
    the two disagree about the pipeline the policy is reacting to.
    """
    scheduler, _source = _scheduler([11, 12, 13, 14], lane_cap=3)
    scheduler.start()
    for reservation in scheduler.reserve():
        contribution = scheduler.start_session(reservation)
        scheduler.finish_work(contribution, changed=True)

    assert len(scheduler.admitted) == 2
    assert len(scheduler.parked) == 1

    changes = [
        scheduler.observe_pressure(rate_limits=0, credit_burn=1.0, host_pressure=0.5)
        for _ in range(6)
    ]
    fired = [c for c in changes if c is not None]
    assert [c.pressure for c in fired] == ["integration_backlog"]
    assert scheduler.effective_limit == 2


def _budgeted_scheduler(refs: list[int]) -> RollingScheduler:
    """A scheduler whose controller has every operator budget configured."""
    diag = _silent_logger()
    pool = RollingPool(
        diag=diag, source=ScriptedSource(refs), clock=Clock(), jitter=lambda i: i
    )
    scheduler = RollingScheduler(
        diag=diag,
        pool=pool,
        lane_cap=6,
        concurrency=ConcurrencyController(configured_lane_cap=6, credit_target=10.0),
    )
    scheduler.start()
    for _ in range(3):
        scheduler.observe_pressure(rate_limits=1)
    assert scheduler.effective_limit == 1
    return scheduler


def test_only_a_pool_with_work_left_earns_a_lane_back() -> None:
    """#219 §6: "remaining eligible demand" is the scheduler's own **Pool**.

    A Run that has run out of work looks perfectly healthy on every external
    signal, so demand is what stops it buying Lane slots for issues that do not
    exist. The two halves are asserted together because either one alone would
    pass for the wrong reason.
    """
    calm = {"rate_limits": 0, "credit_burn": 1.0, "host_pressure": 0.5}

    drained = _budgeted_scheduler([])
    assert [
        c for _ in range(30) if (c := drained.observe_pressure(**calm)) is not None
    ] == []
    assert drained.effective_limit == 1

    busy = _budgeted_scheduler([11, 12, 13, 14, 15, 16])
    changes = [
        c for _ in range(30) if (c := busy.observe_pressure(**calm)) is not None
    ]
    assert [c.effective_lane_limit for c in changes] == [2, 3, 4]
