"""The routing evidence one Run keeps about its own earlier attempts (#562).

The seam is :class:`~git_loopy.attempt_evidence.AttemptEvidenceLedger`'s public
face — bind a route, observe an ending, read back what a later **Pickup** may
assess with. The classification it applies is ADR-0057's rule that
*infrastructure failure is not automatically evidence of insufficient model
capability*, so these tests hold that rule rather than the table implementing
it.
"""

from __future__ import annotations

from git_loopy.attempt_evidence import AttemptEvidenceLedger
from git_loopy.config import (
    RoutingLifecyclePosition,
    RoutingResolution,
    RoutingSource,
)
from git_loopy.dynamic_route import PriorOutcome
from git_loopy.session_outcome import SessionOutcome


def _resolution(
    model: str = "work-model",
    effort: str | None = "high",
    tier: str = "default",
) -> RoutingResolution:
    return RoutingResolution(
        model=model,
        reasoning_effort=effort,
        context_tier=tier,
        source=RoutingSource.DYNAMIC,
        task_type_keys=("implementation",),
        gate_warnings=(),
        lifecycle_position=RoutingLifecyclePosition.FRESH,
    )


def test_an_ending_is_kept_beside_the_route_that_earned_it() -> None:
    """The evidence is a pairing, and the pairing is made in two places.

    A **Pickup** is the only moment that knows which route the session will run
    on, and the ending is the only moment that knows how it went. Neither can
    state the pair alone, so the ledger takes them separately and joins them —
    which is also what makes a **Lane** safe: both halves are keyed by issue, so
    one Lane's ending can never be attributed to its neighbour's route.
    """
    ledger = AttemptEvidenceLedger()
    assert ledger.prior_attempts(7) == ()

    ledger.bound(7, _resolution())
    assert ledger.prior_attempts(7) == ()

    ledger.observe(7, SessionOutcome.NO_PROGRESS)
    (attempt,) = ledger.prior_attempts(7)

    assert attempt.model == "work-model"
    assert attempt.reasoning_effort == "high"
    assert attempt.context_tier == "default"
    assert attempt.outcome is PriorOutcome.DID_NOT_SOLVE
    assert attempt.detail == "no_progress"
    assert attempt.capability_evidence is True


def test_only_a_silent_no_progress_is_evidence_about_the_configuration() -> None:
    """AC2, as a total table over the closed **Session outcome** vocabulary.

    Stated as the whole mapping rather than as the one interesting row, so a
    sixth ending cannot be classified by omission — the failure mode would be a
    new ending silently becoming capability evidence and blacklisting whatever
    configuration happened to be running when it arrived.
    """
    ledger = AttemptEvidenceLedger()
    expected = {
        None: PriorOutcome.ADVANCED,
        SessionOutcome.NO_PROGRESS: PriorOutcome.DID_NOT_SOLVE,
        SessionOutcome.CRASH: PriorOutcome.INFRASTRUCTURE_FAILURE,
        SessionOutcome.TIMEOUT: PriorOutcome.RAN_OUT_OF_TIME,
        SessionOutcome.CONTENT_FILTERED: PriorOutcome.INFRASTRUCTURE_FAILURE,
        SessionOutcome.NO_MORE_TASKS: PriorOutcome.NOTHING_TO_DO,
    }
    assert set(expected) == {None, *SessionOutcome}

    for ref, (outcome, classified) in enumerate(expected.items()):
        ledger.bound(ref, _resolution())
        ledger.observe(ref, outcome)
        (attempt,) = ledger.prior_attempts(ref)
        assert attempt.outcome is classified, outcome
        assert attempt.capability_evidence is (
            classified is PriorOutcome.DID_NOT_SOLVE
        ), outcome
        assert attempt.detail == (None if outcome is None else outcome.value)


def test_an_advancing_iteration_is_remembered_though_it_spends_no_attempt() -> None:
    """AC6: the evidence and the **Attempt lifecycle** position are two axes.

    An Iteration that advanced its issue without closing it reaches no ending,
    spends no attempt and leaves the issue ``fresh`` — and is still the most
    useful thing a later election could know, because it is direct evidence the
    configuration is doing the work. A ledger that only recorded failures would
    answer "nothing happened" to a question about an issue three commits in.
    """
    ledger = AttemptEvidenceLedger()
    ledger.bound(11, _resolution())
    ledger.observe(11, None)
    ledger.bound(11, _resolution(model="other-model", effort="max"))
    ledger.observe(11, SessionOutcome.CRASH)

    first, second = ledger.prior_attempts(11)

    assert first.outcome is PriorOutcome.ADVANCED
    assert first.model == "work-model"
    assert second.outcome is PriorOutcome.INFRASTRUCTURE_FAILURE
    assert second.model == "other-model"
    assert second.reasoning_effort == "max"


def test_an_ending_with_no_bound_route_records_nothing_rather_than_a_blank() -> None:
    """A row with no configuration could never be compared to a candidate.

    It would pad the assessment with a fact about nothing and — worse — could
    match a candidate whose model the harness also declines to name. Recording
    nothing keeps "no evidence" and "evidence about an unknown route" from
    rendering the same.
    """
    ledger = AttemptEvidenceLedger()
    ledger.observe(3, SessionOutcome.NO_PROGRESS)

    assert ledger.prior_attempts(3) == ()
