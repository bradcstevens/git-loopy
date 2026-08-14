"""Tests for ``git_loopy.issue_pin`` — the invocation-scoped **Pin** (#396).

The pin bypasses **order** and nothing else (ADR-0032), so almost all of this
module is about what it must *not* bypass. Every test below is one way an
operator can name an issue the runner cannot work, and the property under test
is always the same: the invocation stops, and the message says which rule was
broken — never a silent fall back to the head of the order.
"""

from __future__ import annotations

import pytest

from git_loopy import issue_pin
from git_loopy.issue_pin import (
    PIN_REFUSAL_CLOSED,
    PIN_REFUSAL_NOT_AFK_READY,
    PIN_REFUSAL_NOT_PARALLEL_SAFE,
    PIN_REFUSAL_NOT_READY_FOR_AGENT,
    PIN_REFUSAL_UNREADABLE,
    PIN_REFUSALS,
    PinRefusal,
    PinnedIssue,
    refuse_pin,
)
from git_loopy.sources import (
    EXCLUSION_MISSING_ACCEPTANCE_CRITERIA,
    EXCLUSION_MISSING_BOTH_SECTIONS,
    EXCLUSION_MISSING_WHAT_TO_BUILD,
    EXCLUSION_REASONS,
    LABEL_PARALLEL_SAFE,
    LABEL_READY_FOR_AGENT,
)


def _pinned(
    number: int = 42,
    *,
    state: str = "OPEN",
    labels: tuple[str, ...] = (LABEL_READY_FOR_AGENT,),
) -> PinnedIssue:
    return PinnedIssue(number=number, state=state, labels=labels)


class TestAccepted:
    """The cases where the pin stands."""

    def test_an_eligible_pinned_issue_is_not_refused(self) -> None:
        assert refuse_pin(_pinned(), afk_exclusion=None) is None

    def test_a_pinned_issue_may_carry_any_other_label(self) -> None:
        """The pin reads two labels; the rest are none of its business."""
        issue = _pinned(labels=(LABEL_READY_FOR_AGENT, "task-type:bugfix", "priority"))

        assert refuse_pin(issue, afk_exclusion=None) is None

    def test_state_is_read_case_insensitively(self) -> None:
        """``gh`` reports ``OPEN``; the shell and PowerShell ports read the same
        field through ``jq``/``ConvertFrom-Json``. A member that disagreed about
        casing would refuse an invocation the reference member accepts."""
        assert refuse_pin(_pinned(state="open"), afk_exclusion=None) is None


class TestEligibilityIsNotBypassed:
    """Every rule the pin explicitly does *not* skip."""

    def test_an_unreadable_pin_is_refused(self) -> None:
        """A missing or unreadable issue is the tracker declining to answer.

        Indistinguishable from `gh`'s side — both are a non-zero `issue view` —
        and both mean the same thing to an operator: the issue you named is not
        one this runner can see.
        """
        refusal = refuse_pin(None, afk_exclusion=None, number=99)

        assert refusal == PinRefusal(issue=99, reason=PIN_REFUSAL_UNREADABLE)

    def test_a_closed_pin_is_refused(self) -> None:
        refusal = refuse_pin(_pinned(state="CLOSED"), afk_exclusion=None)

        assert refusal is not None
        assert refusal.reason == PIN_REFUSAL_CLOSED
        assert refusal.issue == 42

    def test_a_pin_without_ready_for_agent_is_refused(self) -> None:
        refusal = refuse_pin(_pinned(labels=()), afk_exclusion=None)

        assert refusal is not None
        assert refusal.reason == PIN_REFUSAL_NOT_READY_FOR_AGENT

    @pytest.mark.parametrize(
        ("exclusion", "expected_section"),
        [
            (EXCLUSION_MISSING_WHAT_TO_BUILD, "## What to build"),
            (EXCLUSION_MISSING_ACCEPTANCE_CRITERIA, "## Acceptance criteria"),
            (EXCLUSION_MISSING_BOTH_SECTIONS, "## What to build"),
        ],
    )
    def test_a_pin_failing_the_discriminator_names_the_missing_section(
        self, exclusion: str, expected_section: str
    ) -> None:
        """#396 asks for the *specific* missing section, not "ineligible".

        The operator's next action is to edit the issue, and a message that
        makes them re-read the discriminator to find out which heading is
        absent has failed at the only job it had.
        """
        refusal = refuse_pin(_pinned(), afk_exclusion=exclusion)

        assert refusal is not None
        assert refusal.reason == PIN_REFUSAL_NOT_AFK_READY
        assert refusal.detail == exclusion
        assert expected_section in refusal.message

    def test_missing_both_sections_names_both(self) -> None:
        refusal = refuse_pin(
            _pinned(), afk_exclusion=EXCLUSION_MISSING_BOTH_SECTIONS
        )

        assert refusal is not None
        assert "## What to build" in refusal.message
        assert "## Acceptance criteria" in refusal.message


class TestParallelSafety:
    """Parallel mode adds one rule, and the pin does not skip that one either."""

    def test_a_serial_invocation_does_not_ask_for_parallel_safe(self) -> None:
        assert refuse_pin(_pinned(), afk_exclusion=None) is None

    def test_a_parallel_invocation_refuses_a_pin_that_is_not_parallel_safe(
        self,
    ) -> None:
        """Otherwise the pin would be accepted and then never selected.

        A **Lane** Pool is `ready-for-agent` *and* `parallel-safe`, so a pinned
        issue lacking the second never enters it — and a promotion that cannot
        find its issue is a no-op. The Run would work the head of the order
        instead, which is the silent substitution ADR-0032 refuses.
        """
        refusal = refuse_pin(
            _pinned(), afk_exclusion=None, require_parallel_safe=True
        )

        assert refusal is not None
        assert refusal.reason == PIN_REFUSAL_NOT_PARALLEL_SAFE

    def test_a_parallel_safe_pin_is_accepted_in_parallel_mode(self) -> None:
        issue = _pinned(labels=(LABEL_READY_FOR_AGENT, LABEL_PARALLEL_SAFE))

        assert (
            refuse_pin(issue, afk_exclusion=None, require_parallel_safe=True) is None
        )


class TestPrecedence:
    """One refusal, and it is the one an operator should act on first."""

    def test_a_closed_pin_reports_closure_rather_than_its_missing_label(
        self,
    ) -> None:
        """Reopening is the prerequisite; relabelling a closed issue is wasted work."""
        refusal = refuse_pin(_pinned(state="CLOSED", labels=()), afk_exclusion=None)

        assert refusal is not None
        assert refusal.reason == PIN_REFUSAL_CLOSED

    def test_a_pin_reports_its_missing_label_before_its_missing_section(
        self,
    ) -> None:
        """The label is the cheaper fix and the coarser gate.

        An issue nobody triaged to `ready-for-agent` is not owed a critique of
        its headings — that is a review of work the operator may not have
        started.
        """
        refusal = refuse_pin(
            _pinned(labels=()), afk_exclusion=EXCLUSION_MISSING_BOTH_SECTIONS
        )

        assert refusal is not None
        assert refusal.reason == PIN_REFUSAL_NOT_READY_FOR_AGENT


class TestVocabulary:
    def test_every_refusal_reason_is_declared(self) -> None:
        """Closed, like ``EXCLUSION_REASONS`` and ``PICKUP_REASONS``.

        A reason with no entry here is a reason no port can be pinned against.
        """
        assert PIN_REFUSALS == (
            PIN_REFUSAL_UNREADABLE,
            PIN_REFUSAL_CLOSED,
            PIN_REFUSAL_NOT_READY_FOR_AGENT,
            PIN_REFUSAL_NOT_AFK_READY,
            PIN_REFUSAL_NOT_PARALLEL_SAFE,
        )

    def test_the_reasons_are_distinct(self) -> None:
        assert len(set(PIN_REFUSALS)) == len(PIN_REFUSALS)

    def test_every_reason_has_a_message(self) -> None:
        """No refusal may reach an operator as a bare enum value."""
        for reason in PIN_REFUSALS:
            message = PinRefusal(issue=7, reason=reason).message
            assert "#7" in message
            assert message.strip() != ""


class TestPurity:
    def test_refuse_pin_reads_only_what_it_is_given(self) -> None:
        """No clock, no I/O, no Config — the same discipline as
        ``git_loopy.issue_order``, so the Conformance adapter can call it."""
        issue = _pinned()

        first = refuse_pin(issue, afk_exclusion=None)
        second = refuse_pin(issue, afk_exclusion=None)

        assert first == second

    def test_the_number_comes_from_the_issue_when_one_was_read(self) -> None:
        """A refusal names the issue the tracker returned, so a redirect or a
        transposed argument cannot report the wrong number."""
        refusal = refuse_pin(_pinned(number=13, state="CLOSED"), afk_exclusion=None)

        assert refusal is not None
        assert refusal.issue == 13


class TestTheDuplicatedLiteralsAreHeldToOneDeclaration:
    """``issue_pin`` restates four strings ``sources`` owns. Pin them here.

    It restates rather than imports so that the pin decision stays callable
    without :mod:`git_loopy.sources` — which imports the ordering seam the pin
    composes with, and would make the pair cyclic. That is the family's usual
    trade (``rollup.py`` and ``interactive/state.py`` hold two copies of the
    retroactive-binding set, pinned to one fixture); what it buys has to be paid
    for by a test that fails when the two drift, or the second copy is just a
    place for them to disagree.
    """

    def test_the_required_label_is_the_one_the_pool_filters_on(self) -> None:
        assert issue_pin._LABEL_READY_FOR_AGENT == LABEL_READY_FOR_AGENT

    def test_the_parallel_label_is_the_one_a_lane_filters_on(self) -> None:
        assert issue_pin._LABEL_PARALLEL_SAFE == LABEL_PARALLEL_SAFE

    def test_every_discriminator_reason_names_a_section(self) -> None:
        """A new exclusion reason must not silently degrade to "check both"."""
        assert set(issue_pin._MISSING_SECTIONS) == set(EXCLUSION_REASONS)
