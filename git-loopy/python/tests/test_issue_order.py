"""Tests for ``git_loopy.issue_order`` — the total order over eligible issues.

#391 (ADR-0032, slice 1). The seam is a pure comparison and nothing calls it
yet, so these tests *are* its only consumer besides the Conformance adapter.
They are written against the properties the order must have — total, stable,
oldest-first, Priority ahead — rather than against a sort key's internals, so
the key can be re-expressed without rewriting the suite.
"""

from __future__ import annotations

import pytest

from git_loopy.issue_order import (
    LABEL_PRIORITY,
    OrderableIssue,
    TimestampDefect,
    head_of_order,
    issue_order_key,
    order_issues,
    priority_rank,
)


def _issue(number: int, created_at: str | None, *labels: str) -> OrderableIssue:
    return OrderableIssue(number=number, created_at=created_at, labels=tuple(labels))


def _numbers(issues: tuple[OrderableIssue, ...]) -> list[int]:
    return [issue.number for issue in issues]


def test_an_empty_pool_orders_to_nothing_and_has_no_head() -> None:
    """The head of an empty order is an absence, not an error.

    A Run reaching the clean-exit-on-empty condition asks this seam the same
    question a full Pool does, so it must answer rather than raise.
    """
    result = order_issues(())

    assert result.order == ()
    assert result.head is None
    assert result.undated == ()


def test_a_single_member_pool_is_its_own_head() -> None:
    result = order_issues([_issue(7, "2026-01-02T03:04:05Z")])

    assert _numbers(result.order) == [7]
    assert result.head is not None
    assert result.head.number == 7


def test_issues_order_oldest_first_by_created_at() -> None:
    """The whole point of ADR-0032: an issue filed in January goes first."""
    result = order_issues(
        [
            _issue(30, "2026-08-13T19:52:03Z"),
            _issue(10, "2026-01-04T00:00:00Z"),
            _issue(20, "2026-05-09T12:00:00Z"),
        ]
    )

    assert _numbers(result.order) == [10, 20, 30]


def test_a_priority_issue_sorts_ahead_of_every_older_issue() -> None:
    """**Priority** is a jump to the front of the queue, not a nudge."""
    result = order_issues(
        [
            _issue(10, "2020-01-01T00:00:00Z"),
            _issue(20, "2026-08-13T00:00:00Z", LABEL_PRIORITY),
            _issue(30, "2021-01-01T00:00:00Z"),
        ]
    )

    assert _numbers(result.order) == [20, 10, 30]


def test_two_priority_issues_order_oldest_first_against_each_other() -> None:
    """Priority is a jump to the front of the queue, not an escape from it."""
    result = order_issues(
        [
            _issue(10, "2026-08-01T00:00:00Z", LABEL_PRIORITY),
            _issue(20, "2026-01-01T00:00:00Z", LABEL_PRIORITY),
        ]
    )

    assert _numbers(result.order) == [20, 10]


def test_issues_sharing_a_created_at_order_by_ascending_number() -> None:
    """The tie-break that makes the order total rather than merely defined."""
    result = order_issues(
        [
            _issue(30, "2026-01-01T00:00:00Z"),
            _issue(10, "2026-01-01T00:00:00Z"),
            _issue(20, "2026-01-01T00:00:00Z"),
        ]
    )

    assert _numbers(result.order) == [10, 20, 30]


def test_no_two_distinct_issues_compare_equal() -> None:
    """Totality stated as the property, not inferred from one example.

    Two issues can agree on Priority *and* on the instant; they can never agree
    on the issue number, so the key separates every distinct pair.
    """
    issues = [
        _issue(10, "2026-01-01T00:00:00Z"),
        _issue(11, "2026-01-01T00:00:00Z"),
        _issue(12, "2026-01-01T00:00:00Z", LABEL_PRIORITY),
        _issue(13, None),
        _issue(14, "not-a-timestamp"),
    ]

    keys = {issue_order_key(issue) for issue in issues}

    assert len(keys) == len(issues)


def test_ordering_the_same_input_twice_yields_the_same_sequence() -> None:
    """Stability, and the reason the fixture can pin an exact sequence."""
    issues = [
        _issue(30, "2026-01-01T00:00:00Z"),
        _issue(10, "2026-01-01T00:00:00Z"),
        _issue(20, None),
    ]

    assert _numbers(order_issues(issues).order) == _numbers(order_issues(issues).order)


def test_input_order_does_not_affect_the_result() -> None:
    """A pure comparison, not a stable sort over whatever ``gh`` returned.

    ``gh issue list`` order is an inherited CLI default (ADR-0032); if reversing
    the input reversed any tie, the order would still be that default's.
    """
    issues = [
        _issue(10, "2026-01-01T00:00:00Z"),
        _issue(20, "2026-01-01T00:00:00Z"),
        _issue(30, None),
        _issue(40, "2025-01-01T00:00:00Z", LABEL_PRIORITY),
    ]

    forward = _numbers(order_issues(issues).order)
    backward = _numbers(order_issues(list(reversed(issues))).order)

    assert forward == backward == [40, 10, 20, 30]


def test_only_the_priority_label_is_read_off_the_labels() -> None:
    """A **Task type** selects a **Routed pair** and never affects order."""
    assert priority_rank(("task-type:bugfix", "ready-for-agent", "parallel-safe")) == 1
    assert priority_rank(("ready-for-agent", LABEL_PRIORITY)) == 0


def test_a_priority_label_is_matched_exactly() -> None:
    """``priority`` is the label; ``priority:high`` is a different one.

    Prefix matching would silently admit a vocabulary nobody decided, which is
    the same failure the closed ``task-type:`` taxonomy exists to prevent.
    """
    assert priority_rank(("priority:high",)) == 1
    assert priority_rank(("Priority",)) == 1


def test_timezone_offsets_compare_as_instants() -> None:
    """``+01:00`` is an hour *earlier* than the same wall clock in UTC.

    Comparing the strings would put them the other way round, which is why the
    seam normalises rather than sorting the text GitHub happened to emit.
    """
    result = order_issues(
        [
            _issue(10, "2026-03-01T00:45:00Z"),
            _issue(20, "2026-03-01T01:00:00+01:00"),
        ]
    )

    assert _numbers(result.order) == [20, 10]


def test_a_negative_offset_compares_as_an_instant() -> None:
    result = order_issues(
        [
            _issue(10, "2026-03-01T00:00:00Z"),
            _issue(20, "2026-02-28T20:00:00-05:00"),
        ]
    )

    assert _numbers(result.order) == [10, 20]


def test_fractional_seconds_are_honoured() -> None:
    """Discarding them would tie two issues the source distinguished."""
    result = order_issues(
        [
            _issue(10, "2026-01-01T00:00:00.500Z"),
            _issue(20, "2026-01-01T00:00:00.250Z"),
        ]
    )

    assert _numbers(result.order) == [20, 10]


def test_an_absent_created_at_sorts_last_and_is_reported() -> None:
    """Warn and skip, never fail: the Run does not stop over a missing field."""
    result = order_issues(
        [
            _issue(10, None),
            _issue(20, "2026-08-13T00:00:00Z"),
        ]
    )

    assert _numbers(result.order) == [20, 10]
    assert [(u.number, u.defect) for u in result.undated] == [
        (10, TimestampDefect.ABSENT)
    ]


def test_an_unparseable_created_at_sorts_last_and_is_reported() -> None:
    result = order_issues(
        [
            _issue(10, "yesterday"),
            _issue(20, "2026-08-13T00:00:00Z"),
        ]
    )

    assert _numbers(result.order) == [20, 10]
    assert [(u.number, u.defect) for u in result.undated] == [
        (10, TimestampDefect.MALFORMED)
    ]


def test_an_undated_issue_sorts_last_within_its_own_priority_rank() -> None:
    """An undated issue is last *among its rank*, not last overall.

    A **Priority** issue with an unreadable timestamp is still Priority — the
    defect is in the ordering field, not in the human's assertion.
    """
    result = order_issues(
        [
            _issue(10, "2020-01-01T00:00:00Z"),
            _issue(20, None, LABEL_PRIORITY),
            _issue(30, "2019-01-01T00:00:00Z", LABEL_PRIORITY),
        ]
    )

    assert _numbers(result.order) == [30, 20, 10]


def test_two_undated_issues_order_by_ascending_number() -> None:
    result = order_issues([_issue(30, None), _issue(10, ""), _issue(20, "nonsense")])

    assert _numbers(result.order) == [10, 20, 30]


def test_an_empty_created_at_is_absent_rather_than_malformed() -> None:
    """``gh`` renders a null field as an empty string; that is a missing value.

    Reporting it as malformed would send an operator looking for a corrupt
    timestamp that was never there.
    """
    result = order_issues([_issue(10, "")])

    assert result.undated[0].defect is TimestampDefect.ABSENT


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01T00:00:00",
        "2026-01-01 00:00:00Z",
        "2026-01-01T00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-00-01T00:00:00Z",
        "2026-01-00T00:00:00Z",
        "2026-01-01T24:00:00Z",
        "2026-01-01T00:60:00Z",
        "2026-01-01T00:00:60Z",
        "2026-01-01T00:00:00+24:00",
        "2026-01-01T00:00:00+0100",
        "1969-12-31T23:59:59Z",
        "26-01-01T00:00:00Z",
    ],
)
def test_a_timestamp_outside_the_accepted_grammar_is_malformed(value: str) -> None:
    """The grammar is narrow *on purpose*.

    Every widening is a place three languages can disagree, and an ordering the
    ports disagree on is worse than one that refuses a value and says so.
    """
    result = order_issues([_issue(10, value)])

    assert [(u.number, u.defect) for u in result.undated] == [
        (10, TimestampDefect.MALFORMED)
    ]


@pytest.mark.parametrize(
    "value",
    [
        "2024-02-29T00:00:00Z",
        "2000-02-29T12:00:00Z",
        "1970-01-01T00:00:00Z",
        "9999-12-31T23:59:59Z",
        "2026-01-01T00:00:00z",
        "2026-01-01T00:00:00.123456789Z",
        "2026-01-01T00:00:00-00:00",
    ],
)
def test_a_timestamp_inside_the_accepted_grammar_is_usable(value: str) -> None:
    result = order_issues([_issue(10, value)])

    assert result.undated == ()


def test_a_non_leap_century_february_twentyninth_is_malformed() -> None:
    """1900 is not a leap year; 2000 is. The rule is the calendar's, not a modulo."""
    result = order_issues([_issue(10, "1900-02-29T00:00:00Z")])

    assert result.undated[0].defect is TimestampDefect.MALFORMED


def test_head_of_order_is_the_first_issue_in_the_order() -> None:
    """The one question **Pickup** will ask (#394), named rather than indexed."""
    issues = [
        _issue(30, "2026-08-13T00:00:00Z"),
        _issue(10, "2026-01-01T00:00:00Z"),
    ]

    head = head_of_order(issues)

    assert head is not None
    assert head.number == 10
    assert head_of_order(()) is None


def test_undated_issues_are_reported_in_the_order_they_appear() -> None:
    """Report order follows the *ordered* Pool, so it reads like the Pool does."""
    result = order_issues(
        [
            _issue(30, None),
            _issue(10, "garbage"),
            _issue(20, "2026-01-01T00:00:00Z"),
        ]
    )

    assert [u.number for u in result.undated] == [10, 30]


def test_ordering_accepts_a_one_shot_iterable() -> None:
    """A generator is a legitimate Pool; ordering must not depend on re-reading it."""
    issues = (
        _issue(number, created_at)
        for number, created_at in ((30, "2026-03-01T00:00:00Z"), (10, None))
    )

    result = order_issues(issues)

    assert _numbers(result.order) == [30, 10]
