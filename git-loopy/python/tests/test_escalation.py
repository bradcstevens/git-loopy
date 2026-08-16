"""The **Escalation rung** — one higher pair for an issue that stalled (#408).

An **Iteration** that made no progress was worked once, on one pair, and the Run
had no way to try it differently. These are the tests for the ledger that ends
that: which issues this Run owes the rung, on the strength of which **Session
outcome**, and — just as load-bearing — which endings buy nothing.
"""

from __future__ import annotations

import pytest

# The built-in rung lives beside the **Default pair** it is defined one rung
# above; imported rather than restated so a test cannot pass against a rung
# nobody ships.
from git_loopy.cli import _DEFAULT_ESCALATION_RUNG as DEFAULT_ESCALATION_RUNG
from git_loopy.escalation import EscalationLedger
from git_loopy.session_outcome import SessionOutcome


def test_a_silent_no_progress_ending_owes_the_issue_the_rung() -> None:
    """The one ending escalation is triggered by, and the whole mechanism.

    A session that ran to the end, said nothing about failing, and left no
    commit behind is exactly how a pair too cheap for the work fails — so it is
    the one ending that buys the issue a harder pair.
    """
    ledger = EscalationLedger(rung=("claude-opus-5", "max"))

    assert ledger.owed(408) is None
    ledger.observe(408, SessionOutcome.NO_PROGRESS)

    assert ledger.owed(408) == ("claude-opus-5", "max")


@pytest.mark.parametrize(
    "outcome",
    [
        SessionOutcome.TIMEOUT,
        SessionOutcome.CRASH,
        SessionOutcome.NO_MORE_TASKS,
        SessionOutcome.CONTENT_FILTERED,
    ],
)
def test_no_other_ending_escalates(outcome: SessionOutcome) -> None:
    """The four endings that say nothing about the pair being too cheap.

    A timeout answered with a slower, higher-reasoning pair near-guarantees a
    second timeout; a crash is evidence about the harness; no-more-tasks and a
    filtered turn are the session telling us the work is not there to be done.
    Escalating on any of them spends the ceiling on a diagnosis nobody made.
    """
    ledger = EscalationLedger(rung=DEFAULT_ESCALATION_RUNG)

    assert ledger.observe(408, outcome) is False
    assert ledger.owed(408) is None


def test_the_rung_is_owed_for_the_rest_of_the_run_whatever_follows() -> None:
    """Sticky by construction: the ledger only grows, so nothing clears it.

    Re-testing the cheap pair on an issue the last Iteration just proved it
    cannot work pays to relearn what was already learned — and a non-sticky
    rung admits an oscillation that spends half its sessions at the ceiling
    without ever tripping a **Strike**.
    """
    ledger = EscalationLedger(rung=DEFAULT_ESCALATION_RUNG)
    ledger.observe(408, SessionOutcome.NO_PROGRESS)

    ledger.observe(408, SessionOutcome.CRASH)

    assert ledger.owed(408) == DEFAULT_ESCALATION_RUNG


def test_one_issue_stalling_leaves_every_other_issue_where_it_was() -> None:
    """The ledger is per issue. A stalled #408 says nothing about #409."""
    ledger = EscalationLedger(rung=DEFAULT_ESCALATION_RUNG)

    ledger.observe(408, SessionOutcome.NO_PROGRESS)

    assert ledger.owed(409) is None


def test_an_iteration_that_advanced_its_issue_reached_no_ending_to_escalate_on() -> None:
    """A productive session is the clearest evidence its pair was adequate.

    The **Session outcome** record carries ``None`` where the Iteration advanced
    its issue, and that absence is not a fifth ending to be cagey about.
    """
    ledger = EscalationLedger(rung=DEFAULT_ESCALATION_RUNG)

    assert ledger.observe(408, None) is False
    assert ledger.owed(408) is None


def test_a_run_with_no_rung_owes_nothing_to_anybody() -> None:
    """Escalation configured off, or suppressed by a pin, is one ``None``.

    Held here rather than at each **Pickup**, so a call site cannot escalate a
    Run that disabled escalation by forgetting to ask a second question.
    """
    ledger = EscalationLedger(rung=None)

    assert ledger.observe(408, SessionOutcome.NO_PROGRESS) is False
    assert ledger.owed(408) is None
