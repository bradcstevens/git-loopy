"""Tests for :mod:`git_loopy.serial_pickup` — the serial Iteration's Pickup."""

from __future__ import annotations

import pytest

from git_loopy.serial_pickup import (
    PICKUP_REASON_ORDER,
    PICKUP_REASON_PRIORITY,
    SerialPickup,
    SerialSkip,
    pick_serial,
)
from git_loopy.sources import AfkReadyItem


def _item(ref: int | str, *, labels: tuple[str, ...] = ()) -> AfkReadyItem:
    return AfkReadyItem(
        ref=ref,
        title=f"Issue {ref}",
        rendered_block=f"=== Issue #{ref} ===",
        labels=labels,
    )


class TestBinding:
    """A Pickup binds exactly one candidate, before any session exists."""

    def test_takes_the_head_of_the_order(self) -> None:
        pickup = pick_serial([_item(7), _item(12), _item(31)])

        assert pickup.item is not None
        assert pickup.item.ref == 7
        assert pickup.bound is True

    def test_an_empty_pool_binds_nothing(self) -> None:
        pickup = pick_serial([])

        assert pickup.item is None
        assert pickup.bound is False
        assert pickup.position is None
        assert pickup.reason is None
        assert pickup.skipped == ()

    def test_the_position_is_one_based_and_names_where_in_the_order_it_sat(
        self,
    ) -> None:
        """"The runner took the oldest" and "the only one left" must differ.

        #397 puts this on the **Pickup** Event; it is carried here because the
        position is only knowable at the moment of selection — afterwards the
        Pool that gave it meaning is gone.
        """
        pickup = pick_serial(
            [_item(7), _item(12), _item(31)],
            admit=lambda item: "unroutable" if item.ref == 7 else None,
        )

        assert pickup.item is not None
        assert pickup.item.ref == 12
        assert pickup.position == 2

    def test_the_pool_is_not_reordered(self) -> None:
        """Sequence is the read's decision (§3.2), never re-derived here.

        A Pickup that sorted would be a second implementation of the ordering
        decision ``conformance/issue-ordering.json`` exists to keep single — and
        it would silently disagree with the Pool the completion whitelist and
        the prompt were built from.
        """
        pool = [_item(31), _item(7), _item(12)]

        pickup = pick_serial(pool)

        assert pickup.item is not None
        assert pickup.item.ref == 31


class TestReason:
    """Why this candidate — because it was next, or because a human said so."""

    def test_an_unprioritised_head_is_taken_for_its_place_in_the_order(self) -> None:
        pickup = pick_serial([_item(7)])

        assert pickup.reason == PICKUP_REASON_ORDER

    def test_a_priority_head_is_taken_for_its_label(self) -> None:
        pickup = pick_serial([_item(7, labels=("ready-for-agent", "priority"))])

        assert pickup.reason == PICKUP_REASON_PRIORITY

    def test_the_reason_is_read_off_the_bound_item_not_the_pool(self) -> None:
        """A skipped Priority issue does not lend its reason to the successor."""
        pickup = pick_serial(
            [_item(7, labels=("priority",)), _item(12)],
            admit=lambda item: "unroutable" if item.ref == 7 else None,
        )

        assert pickup.item is not None
        assert pickup.item.ref == 12
        assert pickup.reason == PICKUP_REASON_ORDER

    def test_priority_is_matched_exactly(self) -> None:
        pickup = pick_serial([_item(7, labels=("priority:high", "Priority"))])

        assert pickup.reason == PICKUP_REASON_ORDER


class TestSkipping:
    """Selection is unattended: a candidate it cannot take, it passes over."""

    def test_a_refused_candidate_is_skipped_and_the_next_is_taken(self) -> None:
        pickup = pick_serial(
            [_item(7), _item(12)],
            admit=lambda item: "routing refused" if item.ref == 7 else None,
        )

        assert pickup.item is not None
        assert pickup.item.ref == 12

    def test_a_skip_carries_the_issue_its_reason_and_its_position(self) -> None:
        pickup = pick_serial(
            [_item(7), _item(12)],
            admit=lambda item: "routing refused" if item.ref == 7 else None,
        )

        assert pickup.skipped == (
            SerialSkip(ref=7, position=1, reason="routing refused"),
        )

    def test_every_candidate_refused_binds_nothing_and_reports_all_of_them(
        self,
    ) -> None:
        """Not the same outcome as an empty Pool, and it must not read as one.

        An empty Pool is "there is no work" and ends a Run cleanly. This is
        "there is work and none of it could be taken", which is a Run going
        nowhere — so the caller has to be able to tell them apart.
        """
        pickup = pick_serial([_item(7), _item(12)], admit=lambda item: "nope")

        assert pickup.item is None
        assert pickup.bound is False
        assert [skip.ref for skip in pickup.skipped] == [7, 12]
        assert [skip.position for skip in pickup.skipped] == [1, 2]

    def test_admission_stops_at_the_first_acceptance(self) -> None:
        """Nothing past the bound candidate is asked, because nothing needs to be.

        ``admit`` is allowed to be expensive — the loop's resolves a **Routed
        pair** — so a Pickup that pre-admitted the whole Pool would pay for
        every issue it was never going to work.
        """
        asked: list[int | str] = []

        def admit(item: AfkReadyItem) -> str | None:
            asked.append(item.ref)
            return None

        pick_serial([_item(7), _item(12), _item(31)], admit=admit)

        assert asked == [7]

    def test_a_candidate_is_admitted_exactly_once(self) -> None:
        asked: list[int | str] = []

        def admit(item: AfkReadyItem) -> str | None:
            asked.append(item.ref)
            return "nope"

        pick_serial([_item(7), _item(12)], admit=admit)

        assert asked == [7, 12]


class TestAutonomy:
    """§7: selection never waits for a human, with a TTY or without one."""

    def test_the_seam_takes_no_input_and_reads_no_terminal(self) -> None:
        """Structural, not behavioural: there is no branch to be interactive in.

        Pinned by the module's own imports rather than by running it under a
        fake TTY, because the hazard is a *future* prompt-for-confirmation, and
        the cheapest way to make that impossible is to keep the module unable to
        reach a terminal at all.
        """
        import git_loopy.serial_pickup as module

        source = module.__file__
        assert source is not None
        text = open(source, encoding="utf-8").read()
        for forbidden in ("input(", "sys.stdin", "isatty", "getpass"):
            assert forbidden not in text, forbidden


class TestPurity:
    """The seam is comparable in isolation, so a port can be pinned against it."""

    def test_no_clock_no_config_no_io(self) -> None:
        import git_loopy.serial_pickup as module

        source = module.__file__
        assert source is not None
        text = open(source, encoding="utf-8").read()
        for forbidden in ("datetime", "subprocess", "logging", "os.environ"):
            assert forbidden not in text, forbidden

    def test_the_default_admits_every_candidate(self) -> None:
        """A caller with no admission policy still gets the head of the order."""
        assert pick_serial([_item(7)]).item is not None

    def test_the_result_is_frozen(self) -> None:
        pickup = pick_serial([_item(7)])

        with pytest.raises(Exception):
            pickup.item = None  # type: ignore[misc]

    def test_the_result_carries_the_pool_it_decided_over(self) -> None:
        """``considered`` is the sequence the position indexes into."""
        pickup: SerialPickup = pick_serial([_item(7), _item(12)])

        assert [item.ref for item in pickup.considered] == [7, 12]


class TestReasonVocabulary:
    """The selection reason is a closed vocabulary, declared once (#397)."""

    def test_the_vocabulary_is_the_three_reasons_a_pickup_can_give(self) -> None:
        from git_loopy.serial_pickup import PICKUP_REASONS

        assert PICKUP_REASONS == ("order", "priority", "pin")

    def test_every_reason_a_walk_can_produce_is_in_the_vocabulary(self) -> None:
        """A reason the Event schema does not know is a reason nothing renders."""
        from git_loopy.serial_pickup import PICKUP_REASONS

        for pool in ([_item(7)], [_item(7, labels=("priority",))]):
            assert pick_serial(pool).reason in PICKUP_REASONS

    def test_pin_has_no_producer_yet(self) -> None:
        """#396 is the only thing that may ever emit it; nothing here does."""
        from git_loopy.serial_pickup import PICKUP_REASON_PIN

        assert PICKUP_REASON_PIN == "pin"
        assert pick_serial([_item(7, labels=("pin", "priority"))]).reason != "pin"


class TestReasonIsOneDecision:
    """A Lane and a serial Iteration must answer "why this issue?" the same way.

    ADR-0032 gave every unit of work a Pickup, so the reason it reports is a
    property of the issue's labels rather than of the mode that took it. Two
    implementations would let a Parallel Run and a serial Run disagree about
    whether a **Priority** label did anything.
    """

    def test_the_labels_alone_decide_the_reason(self) -> None:
        from git_loopy.serial_pickup import reason_for_labels

        assert reason_for_labels(()) == "order"
        assert reason_for_labels(("ready-for-agent",)) == "order"
        assert reason_for_labels(("priority",)) == "priority"

    def test_a_prefixed_neighbour_is_not_the_priority_label(self) -> None:
        """``priority:high`` is a vocabulary nobody decided (issue_order.py)."""
        from git_loopy.serial_pickup import reason_for_labels

        assert reason_for_labels(("priority:high",)) == "order"

    def test_the_walk_reports_what_the_labels_decide(self) -> None:
        from git_loopy.serial_pickup import reason_for_labels

        item = _item(7, labels=("priority",))
        assert pick_serial([item]).reason == reason_for_labels(item.labels)
