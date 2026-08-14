"""``git_loopy.issue_order`` — one total order over eligible issues (#391, ADR-0032).

Issues were worked newest-first and nobody decided that: all three Orchestrators
call ``gh issue list`` with no sort qualifier and inherit GitHub's unstated
``sort=created&direction=desc``. What the code calls *"source order"* is a CLI
default. This module is the decision that replaces it — a pure comparison over
fetched issue fields that every Orchestrator shares and
``conformance/issue-ordering.json`` pins.

The order is ``(priority_rank, created_at, number)`` ascending: an issue carrying
the **Priority** label ranks ahead of one that does not, ``created_at`` is
GitHub's own creation timestamp, and the issue number breaks every remaining tie.

Design notes:

* **#393 is the first consumer.** ``GitHubIssueSource.shallow_membership`` hands
  its candidates back in this order, so **Parallel mode** works its backlog
  oldest-first — the scheduler already walked its cache front to back, and only
  its input was wrong. A serial **Iteration** still self-selects; #394 gives it
  a **Pickup** that consumes this seam, #396 pins one issue past it. Landing the
  decision one slice ahead of its consumers is the same deliberate cost #361 and
  #379 paid, and it is what lets the three ports be pinned against one another
  before any behaviour moves.
* **Total, or it is not an order.** Two issues can share a **Priority** rank and
  an instant; they can never share an issue number. Without that last component
  "the head of the order" would be a set, and a Run would pick differently on
  two hosts from identical input.
* **A defect in the field is not a defect in the rank.** An issue whose
  ``created_at`` is missing or unreadable sorts last *within its priority rank*
  and is reported — the Run does not fail. **Priority** is a human assertion and
  survives a broken timestamp; sorting such an issue to the very back would let
  a bad field overrule a person.
* **Reported structurally, not as prose.** :class:`IssueOrder` carries the
  undated issues and *which* defect each hit, because a Conformance fixture has
  to pin which issue was diagnosed and three languages cannot agree on a
  sentence. This follows :class:`~git_loopy.config.GatedEffort`'s signal-not-
  message precedent rather than ``resolve_iteration_model``'s ``warn`` sink.
* **A narrow grammar, on purpose.** Only ``YYYY-MM-DDThh:mm:ss[.frac]`` with a
  ``Z`` or ``±hh:mm`` zone, in years 1970-9999, is usable. Every widening is a
  place the ports can disagree, and an order they disagree on is worse than one
  that refuses a value and says which issue it refused. The year floor also
  keeps every division in :func:`_days_from_civil` non-negative, so Bash's
  truncating ``/`` and Python's flooring ``//`` cannot diverge.
* **The instant is computed, never fetched.** ``created_at`` is read from the
  source and is never computed locally or mutated (ADR-0032); what is computed
  here is only its *comparison*, from the value the source gave.
* **Pure.** No clock, no I/O, no Config. The seam is comparable in isolation,
  which is what lets the Conformance adapter call it directly rather than
  reproducing it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol, TypeVar


class _Numbered(Protocol):
    """Anything with an issue number — all :func:`promote_pinned` needs to read.

    Structural rather than :class:`OrderableIssue`, so a caller holding richer
    records (``gh.Issue``, an ``AfkReadyItem``) promotes the records it already
    has instead of mapping to this module's view and back again.
    """

    @property
    def number(self) -> int: ...


_OrderedT = TypeVar("_OrderedT", bound=_Numbered)

__all__ = [
    "LABEL_PRIORITY",
    "PRIORITY_RANK",
    "UNPRIORITISED_RANK",
    "MIN_ACCEPTED_YEAR",
    "MAX_ACCEPTED_YEAR",
    "TimestampDefect",
    "OrderableIssue",
    "UndatedIssue",
    "IssueOrder",
    "priority_rank",
    "issue_order_key",
    "order_issues",
    "head_of_order",
    "promote_pinned",
]

#: The label that carries **Priority**. A human assertion, exactly like
#: ``parallel-safe``: read at selection, never inferred from issue content.
#: Provisioning it is #395's job — this module only needs to know its name, and
#: naming it here keeps the ordering decision readable without a tracker.
LABEL_PRIORITY: Final[str] = "priority"

#: Rank of an issue carrying :data:`LABEL_PRIORITY`. Lower sorts first.
PRIORITY_RANK: Final[int] = 0

#: Rank of every other issue.
UNPRIORITISED_RANK: Final[int] = 1

#: Oldest year a usable ``created_at`` may name. GitHub predates none of its own
#: issues, so the floor costs nothing real — and it is what keeps the civil-date
#: arithmetic below on non-negative operands in every port.
MIN_ACCEPTED_YEAR: Final[int] = 1970

#: Newest year a usable ``created_at`` may name — the largest a four-digit field
#: can express, so the ceiling refuses only values that were never a date.
MAX_ACCEPTED_YEAR: Final[int] = 9999

#: Sort component standing in for an instant that could not be read. It is never
#: compared against a real instant, because :func:`issue_order_key` puts the
#: has-a-timestamp bit ahead of it.
_NO_INSTANT: Final[tuple[int, int]] = (0, 0)

#: Sorts *before* every undated issue of the same rank.
_DATED: Final[int] = 0

#: Sorts *after* every dated issue of the same rank.
_UNDATED: Final[int] = 1

_TIMESTAMP_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"T(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?"
    r"(?:[Zz]|(?P<sign>[+-])(?P<offset_hour>\d{2}):(?P<offset_minute>\d{2}))$"
)

_DAYS_IN_MONTH: Final[tuple[int, ...]] = (
    31,
    28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
)

_SECONDS_PER_DAY: Final[int] = 86_400
_SECONDS_PER_HOUR: Final[int] = 3_600
_SECONDS_PER_MINUTE: Final[int] = 60
_NANOSECOND_DIGITS: Final[int] = 9

#: Days from 1970-01-01 back to 0000-03-01, the epoch shift in the civil-date
#: algorithm this module uses (Howard Hinnant's ``days_from_civil``).
_EPOCH_SHIFT_DAYS: Final[int] = 719_468


class TimestampDefect(Enum):
    """Why an issue's ``created_at`` could not be used for ordering.

    A *signal*, not a rendered message: three Orchestrators have to agree on
    which defect an issue hit, and they cannot agree on a sentence. The two are
    kept apart because they send an operator to different places — ``ABSENT``
    means the field was never fetched or the source returned nothing, and
    ``MALFORMED`` means a value arrived that is not a timestamp.
    """

    ABSENT = "absent"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class OrderableIssue:
    """The fields an eligible issue is ordered on, and nothing else.

    Deliberately not :class:`git_loopy.gh.Issue`: ordering reads three fields,
    and a seam that took the whole issue would invite a fourth ordering input
    nobody decided. Eligibility — ``ready-for-agent``, the AFK-ready body
    discriminator, ``parallel-safe`` — is decided before an issue reaches here
    and is not re-decided by this module.

    Attributes:
        number: The issue number. Breaks every remaining tie, so it is what
            makes the order total.
        created_at: The issue's creation timestamp as the source gave it, or
            ``None`` when the source carried no value. Never computed here.
        labels: The issue's label names. Only :data:`LABEL_PRIORITY` is read.
    """

    number: int
    created_at: str | None
    labels: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class UndatedIssue:
    """One issue whose ``created_at`` could not be read, and why."""

    number: int
    defect: TimestampDefect


@dataclass(frozen=True)
class IssueOrder:
    """The ordered Pool plus every issue that could not be dated.

    Both halves come out of one pass, so the order and the diagnostics cannot
    disagree about which issue was undated — the same reason a **Pool
    exclusion**'s reason is derived from the discriminator pass that decided
    membership (Wrapper contract §3.1).
    """

    order: tuple[OrderableIssue, ...]
    undated: tuple[UndatedIssue, ...]

    @property
    def head(self) -> OrderableIssue | None:
        """The issue a **Pickup** takes, or ``None`` when the Pool is empty."""
        return self.order[0] if self.order else None


def priority_rank(labels: Iterable[str]) -> int:
    """:data:`PRIORITY_RANK` when these labels carry **Priority**, else
    :data:`UNPRIORITISED_RANK`.

    Matching is exact. A prefixed neighbour like ``priority:high`` is a
    different label and a vocabulary nobody decided, so it does not rank.
    """
    return PRIORITY_RANK if LABEL_PRIORITY in tuple(labels) else UNPRIORITISED_RANK


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and _is_leap_year(year):
        return 29
    return _DAYS_IN_MONTH[month - 1]


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days from 1970-01-01 to this civil date, by integer arithmetic only.

    Howard Hinnant's ``days_from_civil``. It is spelled out rather than
    delegated to :mod:`datetime` because the shell and PowerShell ports run the
    same arithmetic, and a port that reached for its own date library would
    inherit that library's tolerances instead of this contract's.
    ``year >= 1970`` keeps every operand non-negative, so truncating and
    flooring division agree.
    """
    shifted_year = year - (1 if month <= 2 else 0)
    era = shifted_year // 400
    year_of_era = shifted_year - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = (
        year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    )
    return era * 146_097 + day_of_era - _EPOCH_SHIFT_DAYS


def _parse_instant(created_at: str) -> tuple[int, int] | None:
    """This timestamp as ``(UTC seconds, nanoseconds)``, or ``None`` if unusable."""
    match = _TIMESTAMP_RE.match(created_at)
    if match is None:
        return None

    year = int(match["year"])
    month = int(match["month"])
    day = int(match["day"])
    hour = int(match["hour"])
    minute = int(match["minute"])
    second = int(match["second"])

    if not MIN_ACCEPTED_YEAR <= year <= MAX_ACCEPTED_YEAR:
        return None
    if not 1 <= month <= 12:
        return None
    if not 1 <= day <= _days_in_month(year, month):
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None

    offset_seconds = 0
    if match["sign"] is not None:
        offset_hour = int(match["offset_hour"])
        offset_minute = int(match["offset_minute"])
        if offset_hour > 23 or offset_minute > 59:
            return None
        offset_seconds = offset_hour * _SECONDS_PER_HOUR + (
            offset_minute * _SECONDS_PER_MINUTE
        )
        if match["sign"] == "-":
            offset_seconds = -offset_seconds

    seconds = (
        _days_from_civil(year, month, day) * _SECONDS_PER_DAY
        + hour * _SECONDS_PER_HOUR
        + minute * _SECONDS_PER_MINUTE
        + second
        - offset_seconds
    )
    fraction = match["fraction"] or ""
    nanoseconds = int(fraction.ljust(_NANOSECOND_DIGITS, "0")) if fraction else 0
    return seconds, nanoseconds


def _timestamp_defect(created_at: str | None) -> TimestampDefect | None:
    """Which defect this raw value carries, or ``None`` when it is usable."""
    if created_at is None or created_at == "":
        return TimestampDefect.ABSENT
    if _parse_instant(created_at) is None:
        return TimestampDefect.MALFORMED
    return None


def issue_order_key(issue: OrderableIssue) -> tuple[int, int, int, int, int]:
    """This issue's position in the total order, as a comparable tuple.

    ``(priority rank, dated?, seconds, nanoseconds, number)``. The dated bit sits
    *between* the rank and the instant, which is exactly what "sorts last within
    its priority rank" means: an undated issue falls behind every dated issue of
    its own rank and ahead of every issue of the rank below.
    """
    seconds, nanoseconds = _NO_INSTANT
    dated = _UNDATED
    if issue.created_at:
        instant = _parse_instant(issue.created_at)
        if instant is not None:
            seconds, nanoseconds = instant
            dated = _DATED
    return (priority_rank(issue.labels), dated, seconds, nanoseconds, issue.number)


def order_issues(issues: Iterable[OrderableIssue]) -> IssueOrder:
    """Order eligible issues, and report every one that could not be dated.

    The result is a pure function of the issues given: re-ordering the input
    cannot change the output, because no component of the key is the input's own
    position. That is what makes ``gh``'s inherited sort stop mattering.
    """
    candidates = tuple(issues)
    order = tuple(sorted(candidates, key=issue_order_key))
    undated = tuple(
        UndatedIssue(number=issue.number, defect=defect)
        for issue in order
        if (defect := _timestamp_defect(issue.created_at)) is not None
    )
    return IssueOrder(order=order, undated=undated)


def head_of_order(issues: Iterable[OrderableIssue]) -> OrderableIssue | None:
    """The issue a **Pickup** binds, or ``None`` when there is nothing to bind.

    Named rather than left as ``order_issues(...).order[0]`` because "the head of
    the order" is the question every consumer of this seam actually asks (#393,
    #394, #396), and an empty Pool must answer it with an absence rather than an
    ``IndexError``.
    """
    return order_issues(issues).head


def promote_pinned(
    order: Sequence[_OrderedT], pin: int | None
) -> tuple[_OrderedT, ...]:
    """Move the pinned issue to the head, leaving everything else where it sat.

    The **Pin** (#396): ``--issue N`` bypasses the order *and nothing else*
    (ADR-0032). That last clause is why this is a separate step applied to a
    finished order rather than a fourth component of :func:`issue_order_key`.
    The key stays what §3.2 requires — a pure function of the *fetched issue
    fields*, identical on every host from identical input — while the pin stays
    what it is: one operator's instruction for one invocation, which is not a
    property of any issue and would be a lie if it were sorted like one.

    Design notes:

    * **It outranks Priority.** A **Priority** label is a standing assertion
      about the backlog; a pin is an operator naming one issue right now. If
      Priority won, ``--issue N`` would silently do nothing on exactly the
      repositories that use the label — the flag would work until it mattered.
    * **Stable.** The tail keeps §3.2's sequence, so a pinned Run resumes the
      oldest-first order the moment its named issue leaves the **Pool**. A
      promotion that re-sorted the remainder would be a second ordering
      decision competing with the one this module exists to be.
    * **A pin naming a non-member is a no-op, not an error.** Eligibility is
      settled long before sequence (§3.2), and an ordering seam that could
      raise is one a Run could die inside. Refusing the *invocation* over an
      ineligible pin belongs to :mod:`git_loopy.issue_pin` at preflight, where
      the tracker can still be asked why.
    * **Idempotent**, because an issue already at the head is promoted to where
      it is.

    Args:
        order: Issues already in §3.2 order. Any record carrying a ``number``.
        pin: The pinned issue number, or ``None`` for an unpinned invocation.

    Returns:
        The same members, with the pinned one first when it is present.
    """
    ordered = tuple(order)
    if pin is None:
        return ordered
    pinned = tuple(issue for issue in ordered if issue.number == pin)
    if not pinned:
        return ordered
    return pinned + tuple(issue for issue in ordered if issue.number != pin)
