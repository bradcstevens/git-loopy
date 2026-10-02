"""The per-issue **Attempt lifecycle** — Strikes charged to one issue (#412, ADR-0070).

A Run that could not make progress on an issue re-picked the same issue on the
same pair until something stopped it. These are the tests for the ledger that
stops it: every **Session outcome** charges the issue it belongs to one
**Strike**, an issue out of Strikes is skipped for the rest of the Run, and —
just as load-bearing — nothing ever moves it back.
"""

from __future__ import annotations

import pytest

from git_loopy.attempt_lifecycle import AttemptLedger, AttemptState
from git_loopy.config import DEFAULT_MAX_NMT_STRIKES, RoutingLifecyclePosition
from git_loopy.session_outcome import SessionOutcome

_EVERY_ENDING = tuple(SessionOutcome)


def test_the_default_limit_is_the_default_max_nmt_strikes() -> None:
    """A ledger built without a Config disposes of an issue as a default Run does."""
    assert DEFAULT_MAX_NMT_STRIKES == 3
    assert AttemptLedger().max_strikes == 3


@pytest.mark.parametrize("outcome", _EVERY_ENDING, ids=lambda o: o.value)
def test_every_ending_charges_one_strike_and_retries_until_the_limit(
    outcome: SessionOutcome,
) -> None:
    """No ending is special: each one costs the issue exactly one Strike.

    ADR-0070 retired the per-ending table that sent a timeout, a no-more-tasks
    or a filtered turn straight to ``skipped``. Every ending now buys a retry
    until the issue's own count reaches the limit.
    """
    ledger = AttemptLedger(max_strikes=3)

    assert ledger.state(412) is AttemptState.FRESH
    assert ledger.observe(412, outcome) is AttemptState.RETRYING
    assert ledger.strikes(412) == 1
    assert ledger.observe(412, outcome) is AttemptState.RETRYING
    assert ledger.strikes(412) == 2
    assert ledger.observe(412, outcome) is AttemptState.SKIPPED
    assert ledger.strikes(412) == 3


def test_a_limit_of_one_skips_on_the_first_ending() -> None:
    """``max_nmt_strikes = 1`` is the strictest Run: one ending and the issue is out."""
    ledger = AttemptLedger(max_strikes=1)

    assert ledger.observe(412, SessionOutcome.CRASH) is AttemptState.SKIPPED
    assert ledger.strikes(412) == 1


@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_below_one_is_refused(bad: int) -> None:
    """A limit of zero would skip every issue before its first session."""
    with pytest.raises(ValueError, match="max_strikes"):
        AttemptLedger(max_strikes=bad)


def test_a_session_that_advanced_its_issue_charges_nothing() -> None:
    """No ending is not a sixth ending.

    An **Iteration** that committed reached no ending at all, and the ledger
    exists to count *failures to advance*. Charging a Strike to work that
    landed would skip any issue that simply takes several Iterations to finish.
    """
    ledger = AttemptLedger()

    assert ledger.observe(412, None) is AttemptState.FRESH
    ledger.observe(412, SessionOutcome.NO_PROGRESS)
    assert ledger.observe(412, None) is AttemptState.RETRYING
    assert ledger.strikes(412) == 1


def test_strikes_are_never_refunded() -> None:
    """Monotonic: an advancing Iteration between two endings refunds nothing.

    The count is the Run's claim about the issue. A Run that advanced it once
    and then stalled again is still spending budget on it, and only a fresh
    Run withdraws the claim.
    """
    ledger = AttemptLedger(max_strikes=3)

    ledger.observe(412, SessionOutcome.NO_PROGRESS)
    ledger.observe(412, None)
    ledger.observe(412, SessionOutcome.CRASH)
    ledger.observe(412, None)
    assert ledger.observe(412, SessionOutcome.TIMEOUT) is AttemptState.SKIPPED


def test_a_skipped_issue_never_comes_back_and_takes_no_further_strikes() -> None:
    """Monotonic, including against the ending that means *it worked*."""
    ledger = AttemptLedger(max_strikes=1)
    ledger.observe(412, SessionOutcome.TIMEOUT)

    for outcome in (None, SessionOutcome.NO_PROGRESS, SessionOutcome.CRASH):
        assert ledger.observe(412, outcome) is AttemptState.SKIPPED
    assert ledger.strikes(412) == 1


def test_each_issue_carries_its_own_strikes() -> None:
    """Per issue, so one stubborn issue never spends another's Strikes.

    The bug ADR-0070 fixes: a Run-wide counter let two issues that each ended
    once with no more tasks bring the Run to the edge of its ceiling.
    """
    ledger = AttemptLedger(max_strikes=3)

    ledger.observe(680, SessionOutcome.NO_MORE_TASKS)
    ledger.observe(692, SessionOutcome.NO_MORE_TASKS)

    assert ledger.strikes(680) == 1
    assert ledger.strikes(692) == 1
    assert ledger.strikes(701) == 0
    assert ledger.state(680) is AttemptState.RETRYING
    assert ledger.state(701) is AttemptState.FRESH


def test_a_skipped_issue_is_the_one_a_pickup_refuses() -> None:
    """The predicate a **Pickup** filters on, so no call site re-derives it."""
    ledger = AttemptLedger(max_strikes=2)

    assert ledger.skipped(412) is False
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.skipped(412) is False
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.skipped(412) is True


def test_the_lifecycle_projects_onto_the_routing_record_it_is_reported_on() -> None:
    """One projection, because a **Routing resolution** states the position too.

    A same-pair crash retry has to read ``retrying`` (contract §14) exactly as an
    escalated stall does. The routing vocabulary has no ``skipped`` member and
    needs none — a skipped issue is never picked up, so it never resolves a pair
    to report a position on.
    """
    ledger = AttemptLedger()

    assert ledger.lifecycle_position(412) is RoutingLifecyclePosition.FRESH
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.lifecycle_position(412) is RoutingLifecyclePosition.RETRYING


def test_the_ledger_remembers_which_ending_charged_the_last_strike() -> None:
    """The **Pickup skip** reports it, so an operator reads *why* not just *that*.

    The defeating ending is the one that charged the limit-th Strike and stays
    that one: a later ending on an issue already out of contention did not take
    it out, and re-stamping it would rewrite the diagnosis every Iteration.
    """
    ledger = AttemptLedger(max_strikes=2)

    assert ledger.defeated_by(412) is None
    ledger.observe(412, SessionOutcome.CRASH)
    assert ledger.defeated_by(412) is None

    ledger.observe(412, SessionOutcome.NO_PROGRESS)
    assert ledger.defeated_by(412) is SessionOutcome.NO_PROGRESS

    ledger.observe(412, SessionOutcome.TIMEOUT)
    assert ledger.defeated_by(412) is SessionOutcome.NO_PROGRESS
