"""Preparing **Routing proposals** across the eligible Pool (#566)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from git_loopy import dynamic_route
from git_loopy.route_preparation import (
    PreparationOutcome,
    PreparedRoute,
    RoutePreparation,
)

_NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Candidate:
    """The one thing preparation needs of a **Pool** candidate."""

    ref: int


def _proposal(ref: int, *, valid_for: float = 300.0) -> dynamic_route.RoutingProposal:
    """A nonbinding proposal shaped exactly as the router hands one over."""
    evidence = dynamic_route.EvidenceRecord(
        source_identity="artificial-analysis",
        retrieved_at=_NOW,
        model_identity=f"aa/work-{ref}",
        associated_copilot_model=f"work-{ref}",
        associated_copilot_effort="high",
        association_verified=True,
        intelligence_index=None,
        speed=None,
        benchmark_version="2026-09",
        conditions="standard",
    )
    return dynamic_route.RoutingProposal(
        proposal_id=f"proposal-{ref}",
        issue_ref=ref,
        route=dynamic_route.WorkRoute(
            model=f"work-{ref}", reasoning_effort="high", context_tier="default"
        ),
        work_evidence=dynamic_route.AssessmentCandidate(
            stable_identity="d" * 64,
            model=f"work-{ref}",
            reasoning_effort="high",
            context_tier="default",
            source_identity="artificial-analysis",
            source_model_identity=f"aa/work-{ref}",
            intelligence_index=Decimal("80"),
            public_output_tokens_per_second=None,
            measurement_at=None,
            benchmark_version="2026-09",
            conditions="standard",
        ),
        summary="Highest published index among eligible configurations.",
        selector=dynamic_route.SelectorSettings(
            model="selector",
            reasoning_effort="max",
            context_tier="default",
            evidence=evidence,
        ),
        relevant_input_identity=f"identity-{ref}",
        prepared_at=_NOW,
        valid_until=_NOW + timedelta(seconds=valid_for),
        evidence_retrieved_at=_NOW,
        capabilities_retrieved_at=_NOW,
        usage=dynamic_route.RoutingUsage(
            routing_credits=Decimal("0.25"),
            classification_attempts=0,
            selector_attempts=1,
            in_flight=0,
            overshoot_count=0,
        ),
    )


@pytest.mark.asyncio
async def test_the_next_pickup_is_prepared_before_anything_else_starts() -> None:
    """Preparation prioritises the next **Pickup** (AC1).

    The whole point of preparing ahead is that the issue about to be worked
    already has a proposal. A desk that started every eligible candidate at
    once would leave the head of the order queued behind speculative work —
    which is the "without delaying Pickup" half failing in the one case it is
    for.
    """
    started: list[int] = []
    head_reached = asyncio.Event()
    release_head = asyncio.Event()

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        started.append(candidate.ref)
        if candidate.ref == 11:
            head_reached.set()
            await release_head.wait()
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=3, clock=lambda: _NOW)
    ahead = asyncio.create_task(
        desk.prepare_ahead([_Candidate(11), _Candidate(12), _Candidate(13)])
    )
    await head_reached.wait()
    await asyncio.sleep(0)

    assert started == [11]
    release_head.set()
    prepared = await ahead

    assert sorted(entry.ref for entry in prepared) == [11, 12, 13]
    assert desk.take(12) is not None


@pytest.mark.asyncio
async def test_pickup_can_interrupt_a_head_that_has_not_started() -> None:
    started = []
    observed = []

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        started.append(candidate.ref)
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(
        prepare=prepare, concurrency=1, clock=lambda: _NOW,
        on_prepared=observed.append,
    )
    ahead = asyncio.create_task(desk.prepare_ahead([_Candidate(11), _Candidate(12)]))
    await asyncio.sleep(0)
    try:
        async with asyncio.timeout(0.2):
            await desk.prioritize(12)
        await ahead
        assert started == [12]
        assert desk.take(12) is not None
        assert next(entry for entry in observed if entry.ref == 11).outcome is (
            PreparationOutcome.UNAVAILABLE
        )
    finally:
        await desk.finish_pickup(12)
        ahead.cancel()
        await asyncio.gather(ahead, return_exceptions=True)


@pytest.mark.asyncio
async def test_preparation_never_exceeds_the_configured_selector_concurrency() -> None:
    """Speculative work stays inside the bound the operator authorized (AC1)."""
    live = 0
    peak = 0
    release = asyncio.Event()

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        nonlocal live, peak
        if candidate.ref != 11:
            live += 1
            peak = max(peak, live)
            await release.wait()
            live -= 1
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=2, clock=lambda: _NOW)
    ahead = asyncio.create_task(
        desk.prepare_ahead([_Candidate(ref) for ref in (11, 12, 13, 14, 15)])
    )
    for _ in range(12):
        await asyncio.sleep(0)
    release.set()
    await ahead

    assert peak == 2


@pytest.mark.asyncio
async def test_an_exhausted_allowance_ends_preparation_for_the_run() -> None:
    """Exhaustion is a latch, not a per-candidate refusal (AC8).

    ADR-0057 admits no further routing call once the allowance or deadline is
    spent. A desk that asked again per candidate would spend the rest of the
    Run refusing, which is the speculative loop AC8 forbids — and would do it
    once per driver turn, forever.
    """
    asked: list[int | str] = []

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        asked.append(candidate.ref)
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.UNAVAILABLE,
            reason=dynamic_route.RoutingUnavailableReason.QUOTA_EXHAUSTED,
            detail="routing allowance exhausted",
        )

    desk = RoutePreparation(prepare=prepare, concurrency=4, clock=lambda: _NOW)

    reached = await desk.prepare_ahead([_Candidate(ref) for ref in (11, 12, 13)])

    assert asked == [11]
    assert [entry.outcome for entry in reached] == [PreparationOutcome.UNAVAILABLE]
    assert desk.halted is True
    assert await desk.prepare_ahead([_Candidate(21)]) == ()
    assert asked == [11]


@pytest.mark.asyncio
async def test_a_proposal_that_outlived_its_window_is_refused_not_handed_over() -> None:
    """A Pickup never starts work under a stale preparation (AC8)."""
    now = _NOW

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref, valid_for=60.0),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=1, clock=lambda: now)
    await desk.prepare_ahead([_Candidate(11)])

    now = _NOW + timedelta(seconds=61)

    assert desk.take(11) is None
    assert desk.outstanding() == ()


@pytest.mark.asyncio
async def test_a_proposal_is_handed_to_exactly_one_pickup() -> None:
    """Binding a proposal twice would bind one assessment to two attempts."""

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=1, clock=lambda: _NOW)
    await desk.prepare_ahead([_Candidate(11)])

    assert desk.take(11) is not None
    assert desk.take(11) is None


@pytest.mark.asyncio
async def test_a_settled_candidate_is_not_assessed_twice_in_one_run() -> None:
    """One attempt per candidate per Run, whatever preparation reached (AC8)."""
    asked: list[int | str] = []
    outcomes = {
        11: PreparationOutcome.STATIC,
        12: PreparationOutcome.REUSABLE,
        13: PreparationOutcome.UNAVAILABLE,
    }

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        asked.append(candidate.ref)
        return PreparedRoute(
            ref=candidate.ref,
            outcome=outcomes[int(candidate.ref)],
            reason=(
                dynamic_route.RoutingUnavailableReason.SOURCE_UNAVAILABLE
                if outcomes[int(candidate.ref)] is PreparationOutcome.UNAVAILABLE
                else None
            ),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=3, clock=lambda: _NOW)
    await desk.prepare_ahead([_Candidate(ref) for ref in (11, 12, 13)])
    await desk.prepare_ahead([_Candidate(ref) for ref in (11, 12, 13)])

    assert sorted(asked) == [11, 12, 13]
    assert desk.halted is False


@pytest.mark.asyncio
async def test_a_worked_issue_may_be_prepared_again_once_forgotten() -> None:
    """A new attempt has a new history, so its old preparation is dropped."""
    asked: list[int | str] = []

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        asked.append(candidate.ref)
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=1, clock=lambda: _NOW)
    await desk.prepare_ahead([_Candidate(11)])
    desk.forget([11])

    assert desk.take(11) is None
    await desk.prepare_ahead([_Candidate(11)])

    assert asked == [11, 11]


@pytest.mark.asyncio
async def test_every_outcome_is_reported_in_the_order_it_was_reached() -> None:
    """Proposal state is visible without being presented as a binding (AC4)."""
    seen: list[PreparedRoute] = []

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(
        prepare=prepare,
        concurrency=1,
        clock=lambda: _NOW,
        on_prepared=seen.append,
    )
    await desk.prepare_ahead([_Candidate(11), _Candidate(12)])

    assert [entry.ref for entry in seen] == [11, 12]
    assert all(entry.proposal is not None for entry in seen)
    assert all(entry.proposal.nonbinding for entry in seen)


@pytest.mark.asyncio
async def test_a_failed_preparation_leaves_the_candidate_to_its_own_pickup() -> None:
    """No way of failing to prepare may take a candidate out of the Run."""

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        raise RuntimeError("selector session exploded")

    desk = RoutePreparation(prepare=prepare, concurrency=1, clock=lambda: _NOW)
    reached = await desk.prepare_ahead([_Candidate(11)])

    assert len(reached) == 1
    assert reached[0].outcome is PreparationOutcome.UNAVAILABLE
    assert reached[0].reason is dynamic_route.RoutingUnavailableReason.SELECTOR_UNAVAILABLE
    assert desk.take(11) is None
    assert desk.halted is False


def test_preparation_refuses_a_concurrency_bound_it_cannot_honour() -> None:
    """An unbounded desk is exactly what ADR-0057 requires a finite value for."""

    async def prepare(candidate: _Candidate) -> PreparedRoute:  # pragma: no cover
        raise AssertionError("never reached")

    for invalid in (0, -1, True):
        with pytest.raises(ValueError):
            RoutePreparation(prepare=prepare, concurrency=invalid)


@pytest.mark.asyncio
async def test_pickup_keeps_its_proposal_and_cancels_unrelated_preparation() -> None:
    tail_started = asyncio.Event()
    outcomes: list[PreparedRoute] = []

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        if candidate.ref == 12:
            tail_started.set()
            await asyncio.Event().wait()
        return PreparedRoute(
            ref=candidate.ref,
            outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(
        prepare=prepare, concurrency=1, clock=lambda: _NOW,
        on_prepared=outcomes.append,
    )
    ahead = asyncio.create_task(desk.prepare_ahead([_Candidate(11), _Candidate(12)]))
    await tail_started.wait()
    await asyncio.wait_for(desk.prioritize(11), timeout=1)
    await ahead

    assert desk.take(11) is not None
    assert desk.take(12) is None
    assert outcomes[-1].ref == 12
    assert outcomes[-1].outcome is PreparationOutcome.UNAVAILABLE
    assert "cancelled" in outcomes[-1].detail


@pytest.mark.asyncio
async def test_concurrent_pickups_do_not_cancel_each_others_claimed_preparation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        if candidate.ref == 12:
            started.set()
            await release.wait()
        return PreparedRoute(
            ref=candidate.ref, outcome=PreparationOutcome.PROPOSED,
            proposal=_proposal(candidate.ref),
        )

    desk = RoutePreparation(prepare=prepare, concurrency=2, clock=lambda: _NOW)
    ahead = asyncio.create_task(desk.prepare_ahead([_Candidate(11), _Candidate(12)]))
    await started.wait()
    first = asyncio.create_task(desk.prioritize(12))
    await asyncio.sleep(0)
    await desk.prioritize(13)
    release.set()
    await first
    await ahead
    assert desk.take(12) is not None


@pytest.mark.asyncio
async def test_a_new_preparation_pass_waits_until_foreground_routing_finishes() -> None:
    started = []

    async def prepare(candidate: _Candidate) -> PreparedRoute:
        started.append(candidate.ref)
        return PreparedRoute(ref=candidate.ref, outcome=PreparationOutcome.STATIC)

    desk = RoutePreparation(prepare=prepare, concurrency=1, clock=lambda: _NOW)
    await desk.prioritize(99)
    ahead = asyncio.create_task(desk.prepare_ahead([_Candidate(11)]))
    for _ in range(3):
        await asyncio.sleep(0)
    assert started == []
    await desk.finish_pickup(99)
    await ahead
    assert started == [11]
