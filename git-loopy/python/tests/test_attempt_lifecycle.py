"""The per-issue **Attempt lifecycle** — fresh, retrying, skipped (#412).

A Run that could not make progress on an issue re-picked the same issue on the
same pair until its **Strike** ceiling aborted it. These are the tests for the
ledger that ends that: which **Session outcome** moves an issue one step, which
defeats it outright, and — just as load-bearing — that nothing ever moves it
back.
"""

from __future__ import annotations

import pytest

from git_loopy.attempt_lifecycle import AttemptLedger, AttemptState
from git_loopy.config import RoutingLifecyclePosition
from git_loopy.session_outcome import SessionOutcome


def test_a_silent_no_progress_ending_retries_the_issue_before_skipping_it() -> None:
    """The ending that buys a second attempt, because the pair may be the fault.

    Silent no-progress is the one ending the **Escalation rung** answers with a
    harder pair, so the issue is owed the attempt that pair is for. It is owed
    exactly one: a second stall at the rung has nowhere further to go.
    """
    ledger = AttemptLedger()

    assert ledger.state(412) is AttemptState.FRESH

    assert ledger.observe(412, SessionOutcome.NO_PROGRESS) is AttemptState.RETRYING
    assert ledger.observe(412, SessionOutcome.NO_PROGRESS) is AttemptState.SKIPPED


@pytest.mark.parametrize(
    "outcome",
    [
        SessionOutcome.TIMEOUT,
        SessionOutcome.NO_MORE_TASKS,
        SessionOutcome.CONTENT_FILTERED,
    ],
)
def test_three_endings_defeat_a_fresh_issue_outright(
    outcome: SessionOutcome,
) -> None:
    """No second attempt, because nothing about the second one would differ.

    A timeout would be re-run at the same pair with the same clock; no-more-tasks
    and a filtered turn are the session stating the work is not there to be done.
    Each of them is a *retry* the runner already knows the answer to, so the
    issue skips the retrying rung entirely.
    """
    ledger = AttemptLedger()

    assert ledger.observe(412, outcome) is AttemptState.SKIPPED


def test_a_crash_retries_once_on_the_same_pair() -> None:
    """The harness failed, not the work — so the same pair is worth one more go.

    A crash is the one ending that says nothing about the issue at all, which is
    why it moves the lifecycle (something was spent) but never the pair (nothing
    suggested the pair was wrong). One retry, then defeat: a second crash on one
    issue this Run is a pattern rather than an accident.
    """
    ledger = AttemptLedger()

    assert ledger.observe(412, SessionOutcome.CRASH) is AttemptState.RETRYING
    assert ledger.observe(412, SessionOutcome.CRASH) is AttemptState.SKIPPED


def test_a_session_that_advanced_its_issue_moves_nothing() -> None:
    """No ending is not a sixth ending.

    An **Iteration** that committed reached no ending at all, and the ledger
    exists to count *failures to advance*. Spending a lifecycle step on work that
    landed would defeat any issue that simply takes three Iterations to finish.
    """
    ledger = AttemptLedger()

    assert ledger.observe(412, None) is AttemptState.FRESH
    assert ledger.observe(412, None) is AttemptState.FRESH
    assert ledger.state(412) is AttemptState.FRESH


def test_a_skipped_issue_never_comes_back() -> None:
    """Monotonic, including against the ending that means *it worked*.

    A defeated issue that advanced once would be re-picked, and the Iteration
    that advanced it is not evidence the Run can finish it — it is the ordinary
    shape of a Run grinding at work it cannot land. The lifecycle is a claim
    about the *Run*, and only a new Run withdraws it.
    """
    ledger = AttemptLedger()
    ledger.observe(412, SessionOutcome.TIMEOUT)

    for outcome in (None, SessionOutcome.NO_PROGRESS, SessionOutcome.CRASH):
        assert ledger.observe(412, outcome) is AttemptState.SKIPPED


def test_each_issue_carries_its_own_lifecycle() -> None:
    """Per issue, so one defeated issue never spends another's attempts."""
    ledger = AttemptLedger()

    ledger.observe(412, SessionOutcome.TIMEOUT)

    assert ledger.state(412) is AttemptState.SKIPPED
    assert ledger.state(413) is AttemptState.FRESH


def test_a_skipped_issue_is_the_one_a_pickup_refuses() -> None:
    """The predicate a **Pickup** filters on, so no call site re-derives it."""
    ledger = AttemptLedger()

    assert ledger.skipped(412) is False
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.skipped(412) is False
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.skipped(412) is True


def test_the_lifecycle_projects_onto_the_routing_record_it_is_reported_on() -> None:
    """One projection, because a **Routing resolution** states the position too.

    A same-pair crash retry has to read ``retrying`` (contract §14) exactly as an
    escalated stall does, and a Pickup that derived the position from the
    **Escalation rung**'s ledger could only ever report the escalated half. The
    routing vocabulary has no ``skipped`` member and needs none — a skipped issue
    is never picked up, so it never resolves a pair to report a position on.
    """
    ledger = AttemptLedger()

    assert ledger.lifecycle_position(412) is RoutingLifecyclePosition.FRESH
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.lifecycle_position(412) is RoutingLifecyclePosition.RETRYING


def test_the_ledger_remembers_which_ending_was_the_one_too_many() -> None:
    """The **Pickup skip** reports it, so an operator reads *why* not just *that*.

    The defeating ending is the first one that reached ``skipped`` and stays
    that one: a later ending on an issue already out of contention did not take
    it out, and re-stamping it would rewrite the diagnosis every Iteration.
    """
    ledger = AttemptLedger()

    assert ledger.defeated_by(412) is None
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.defeated_by(412) is None

    ledger.observe(412, SessionOutcome.NO_PROGRESS)
    assert ledger.defeated_by(412) is SessionOutcome.NO_PROGRESS

    ledger.observe(412, SessionOutcome.TIMEOUT)
    assert ledger.defeated_by(412) is SessionOutcome.NO_PROGRESS
