"""``git_loopy.serial_pickup`` — a serial Iteration's **Pickup** (#394, ADR-0032).

Ordering the **Pool** correctly was never enough in serial mode, because there
the code did not pick the issue at all: the whole Pool was rendered into one
prompt block and ``PROMPT.md`` told the agent to self-select by task type. List
position was a rendering hint competing against an explicit instruction to
ignore it, so an issue could be passed over indefinitely and nothing noticed.
This module is the decision that ends that — the runner binds one issue *before*
the agent session starts, and the agent is told which issue it is working.

The seam is deliberately tiny: given the Pool the read already put into Wrapper
contract §3.2 order, walk it front to back and bind the first candidate an
injected ``admit`` accepts. Everything a **Pickup** has to be able to say
afterwards — which issue, why, where it sat, and every issue it passed over —
comes back in one record.

Design notes:

* **It does not sort.** Sequence is decided at the read
  (:func:`git_loopy.sources.in_selection_order`), and a Pickup that re-derived
  it would be a second implementation of the decision
  ``conformance/issue-ordering.json`` exists to keep single. Worse, it could
  disagree with the Pool the prompt and the completion whitelist were built
  from, so "the issue the runner bound" and "the issue at the head of the
  order" would be two facts instead of one.
* **Refusal is a skip, not a crash** (§7). ``ready-for-agent`` means *ready for
  autonomous execution*, and a serial Run fires precisely when nobody is
  watching — so a candidate that cannot be taken is passed over with its reason
  and the next in order is taken. A **Lane** may raise on a routing refusal
  because a released reservation leaves the candidate for another Lane; a serial
  Iteration has no other Lane, so raising would end the Run over one bad label.
* **``admit`` is asked lazily, once per candidate.** The loop's admission
  resolves a **Routed pair**, and a Pickup that pre-admitted the whole Pool
  would pay for every issue it was never going to work.
* **An exhausted Pool is not an empty one.** ``bound`` is ``False`` in both
  cases and :attr:`SerialPickup.skipped` is what tells them apart: an empty Pool
  is "there is no work" and ends a Run cleanly, while a Pool whose every
  candidate was refused is a Run going nowhere and owes a **Strike**.
* **Pure.** No clock, no I/O, no Config, and structurally unable to reach a
  terminal — which is how §7's "no selection path blocks for input" is kept true
  against a future confirmation prompt rather than merely observed to be true
  today.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from git_loopy.issue_order import LABEL_PRIORITY
from git_loopy.sources import AfkReadyItem

__all__ = [
    "PICKUP_REASON_ORDER",
    "PICKUP_REASON_PRIORITY",
    "Admit",
    "SerialSkip",
    "SerialPickup",
    "pick_serial",
]

#: This candidate was taken because it was next in the §3.2 order.
PICKUP_REASON_ORDER: Final[str] = "order"

#: This candidate was taken because a human asserted **Priority** on it. A
#: distinct reason from :data:`PICKUP_REASON_ORDER` because the two answer
#: different operator questions: "is the backlog draining oldest-first?" and
#: "did my Priority label do anything?". #396 adds the third, ``pin``.
PICKUP_REASON_PRIORITY: Final[str] = "priority"

#: Whether one candidate may be bound. Returns the reason it may *not* be, or
#: ``None`` to accept — so the common answer is the falsy one and a caller with
#: no policy needs no callable at all.
Admit = Callable[[AfkReadyItem], "str | None"]


def _admit_everything(item: AfkReadyItem) -> str | None:
    return None


@dataclass(frozen=True)
class SerialSkip:
    """One candidate a **Pickup** passed over, and why.

    Carries the position as well as the ref because being skipped at the head
    of the order and being skipped behind ten other candidates are different
    facts about a backlog. #397 makes this a skip Event; until then it reaches
    an operator through the runner's diagnostics.
    """

    ref: int | str
    position: int
    reason: str


@dataclass(frozen=True)
class SerialPickup:
    """What one serial **Pickup** bound, and everything it passed over.

    Both halves come out of one walk, so the binding and its diagnostics cannot
    disagree about which candidates were considered — the same reason
    :class:`git_loopy.issue_order.IssueOrder` carries its undated issues rather
    than leaving a caller to re-derive them.

    Attributes:
        item: The bound **Active issue**, or ``None`` when nothing could be
            bound. ``None`` covers both an empty Pool and a Pool whose every
            candidate was refused; :attr:`skipped` distinguishes them.
        position: The bound candidate's 1-based place in the order it was
            selected from, or ``None`` when nothing was bound. This is what
            lets an operator tell "the runner took the oldest" from "the runner
            took the only one left", and it is only knowable here — afterwards
            the Pool that gave it meaning is gone.
        reason: :data:`PICKUP_REASON_ORDER` or :data:`PICKUP_REASON_PRIORITY`,
            or ``None`` when nothing was bound.
        skipped: Every candidate passed over ahead of the binding, in order.
        considered: The Pool this Pickup decided over, in the order it was
            given — the sequence :attr:`position` indexes into.
    """

    item: AfkReadyItem | None
    position: int | None
    reason: str | None
    skipped: tuple[SerialSkip, ...]
    considered: tuple[AfkReadyItem, ...]

    @property
    def bound(self) -> bool:
        """``True`` iff this Pickup produced an **Active issue**."""
        return self.item is not None


def _reason_for(item: AfkReadyItem) -> str:
    """Why this candidate was taken.

    Read off the bound item's own labels rather than the Pool's, so a skipped
    **Priority** issue never lends its reason to the successor that replaced it.
    Matching is exact, for :func:`git_loopy.issue_order.priority_rank`'s reason:
    a prefixed neighbour like ``priority:high`` is a vocabulary nobody decided.
    """
    if LABEL_PRIORITY in item.labels:
        return PICKUP_REASON_PRIORITY
    return PICKUP_REASON_ORDER


def pick_serial(
    pool: Iterable[AfkReadyItem],
    *,
    admit: Admit = _admit_everything,
) -> SerialPickup:
    """Bind the first candidate in ``pool`` that ``admit`` accepts.

    Args:
        pool: The **Pool**, already in Wrapper contract §3.2 **selection
            order**. This function does not sort it — see the module docstring.
        admit: Asked once per candidate, front to back, until one is accepted.
            Returns the reason a candidate may not be bound, or ``None`` to
            accept it. The default accepts everything, so a caller with no
            admission policy simply gets the head of the order.

    Returns:
        The binding, its position and reason, and every candidate passed over.
    """
    considered: Sequence[AfkReadyItem] = tuple(pool)
    skipped: list[SerialSkip] = []
    for position, item in enumerate(considered, start=1):
        refusal = admit(item)
        if refusal is None:
            return SerialPickup(
                item=item,
                position=position,
                reason=_reason_for(item),
                skipped=tuple(skipped),
                considered=tuple(considered),
            )
        skipped.append(SerialSkip(ref=item.ref, position=position, reason=refusal))
    return SerialPickup(
        item=None,
        position=None,
        reason=None,
        skipped=tuple(skipped),
        considered=tuple(considered),
    )
