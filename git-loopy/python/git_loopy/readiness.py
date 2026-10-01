"""``git_loopy.readiness`` — a candidate's **Readiness** verdict (#438, ADR-0047).

**Readiness** is a fact about the tracker's dependency graph, not about how an
issue was authored (that is **Eligibility**, decided once at collection). A
candidate carrying an open native ``blocked_by`` dependency is not admissible
at **Pickup**: the runner passes it over and tries the next candidate. See
[ADR-0047](../../../docs/adr/0047-a-blocked-issue-is-not-pickup-admissible.md)
and Wrapper contract §3.3.1.

This module is the **pure, I/O-free readiness seam** the contract requires: it
turns one ``blockedBy`` connection read into a verdict, and nothing else. It is
driven directly by ``conformance/issue-readiness.json`` — every case that
fixture pins is a case this module decides, not a case the GitHub adapter
(:mod:`git_loopy.gh`) reproduces its own copy of.

Design notes:

* **One hop, never traversed.** :func:`decide_readiness` takes exactly the
  connection the candidate's own ``blockedBy`` read returned; it has no way to
  ask for a second hop, which is what keeps traversal a non-goal structurally
  rather than merely by convention.
* **An open blocker outranks an unreadable node.** When the connection is both
  incomplete *and* it already found an open blocker, the proven fact wins:
  reporting ``readiness_unprovable`` would withhold a blocker the read is
  holding. See the fixture's ``a-read-open-blocker-outranks-an-unreadable-node``
  case.
* **``blocked_by_open_dependency`` and ``readiness_unprovable`` are different
  facts.** The first reports an assertion that was read — an open blocker
  exists, and the verdict names it. The second reports that no assertion could
  be read at all — there may be no blocker whatsoever. Collapsing them would
  have the runner assert the very thing it just failed to establish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "POOL_CLASS_ADMITTED",
    "POOL_CLASS_UNRESOLVED",
    "POOL_CLASS_WAITING",
    "POOL_CLASSES",
    "SKIP_BLOCKED_BY_OPEN_DEPENDENCY",
    "SKIP_READINESS_UNPROVABLE",
    "READINESS_VERDICTS",
    "BlockerNode",
    "BlockedByRead",
    "Readiness",
    "blocked_skip_reason",
    "blockers_from_skip_reason",
    "decide_readiness",
]

#: At least one ``blocked_by`` dependency was read and is open.
SKIP_BLOCKED_BY_OPEN_DEPENDENCY: Final[str] = "blocked_by_open_dependency"

#: The ``blockedBy`` connection was incomplete, or a node came back unreadable.
SKIP_READINESS_UNPROVABLE: Final[str] = "readiness_unprovable"

#: A candidate this verdict admits: it has no place in an unbound-Pool count.
POOL_CLASS_ADMITTED: Final[str] = "admitted"

#: A candidate refused because it waits on something outside the Run. A Pool
#: of nothing else is entitled to ``all_blocked`` (Wrapper contract §3.3.1).
POOL_CLASS_WAITING: Final[str] = "waiting"

#: A candidate whose read did not complete, so it proves nothing. One of these
#: outranks every refusal and makes an unbound Pool ``preflight_failed`` (#542).
POOL_CLASS_UNRESOLVED: Final[str] = "unresolved"

#: The class each verdict takes in the unbound-Pool rule
#: (:func:`git_loopy.sources.unbound_pool_outcome`). Closed.
POOL_CLASSES: Final[tuple[str, ...]] = (
    POOL_CLASS_ADMITTED,
    POOL_CLASS_WAITING,
    POOL_CLASS_UNRESOLVED,
)

#: The unbound-Pool class each blocked verdict's ``skip_reason`` takes. A new
#: refusal kind needs an entry here and nothing in a caller (#693).
_POOL_CLASS_BY_SKIP_REASON: Final[dict[str, str]] = {
    SKIP_BLOCKED_BY_OPEN_DEPENDENCY: POOL_CLASS_WAITING,
    SKIP_READINESS_UNPROVABLE: POOL_CLASS_UNRESOLVED,
}

#: Separates a refusal's kind from the blockers it names, and one blocker from
#: the next, in a ``wrapper.pickup.skipped`` reason.
_REASON_DETAIL: Final[str] = ": "
_BLOCKER_SEPARATOR: Final[str] = ", "


def blocked_skip_reason(skip_reason: str, blockers: tuple[str, ...]) -> str:
    """The ``wrapper.pickup.skipped`` reason for a candidate waiting on blockers.

    ``<skip_reason>: <owner/repo#N>, <owner/repo#N>``. The one producer of that
    shape, so :func:`blockers_from_skip_reason` -- and the Unbound-Run notice
    that reads it (#642) -- cannot drift from what a Pickup writes.
    """
    return f"{skip_reason}{_REASON_DETAIL}{_BLOCKER_SEPARATOR.join(blockers)}"


def blockers_from_skip_reason(reason: str) -> tuple[str, ...]:
    """The open blockers a skip reason names, or ``()`` when it names none.

    The inverse of :func:`blocked_skip_reason`, for a refusal whose kind is
    :data:`SKIP_BLOCKED_BY_OPEN_DEPENDENCY`; every other reason names none.
    """
    prefix = f"{SKIP_BLOCKED_BY_OPEN_DEPENDENCY}{_REASON_DETAIL.rstrip()}"
    if not reason.startswith(prefix):
        return ()
    detail = reason[len(prefix):]
    return tuple(part.strip() for part in detail.split(",") if part.strip())


#: Every verdict a readiness read may reach. Closed, and pinned by
#: ``issue-readiness.json``'s own ``verdicts`` list.
READINESS_VERDICTS: Final[tuple[str, ...]] = ("ready", "blocked")


@dataclass(frozen=True)
class BlockerNode:
    """One node the candidate's ``blockedBy`` connection returned.

    Attributes:
        ref: The blocker's full ``owner/repo#number`` (or bare ``#number``
            equivalent) — carried through unchanged so a cross-repository
            blocker's reason names the repository it lives in (§3.3.1).
        state: ``"open"`` or ``"closed"``, or ``None`` when the node came back
            unreadable (the state GitHub returns for a node the token cannot
            see).
        readable: ``False`` when GraphQL returned the node's count but not its
            body — a blocker in a repository the token cannot see. Defaults to
            ``True``, so an ordinary node needs no extra field set.
    """

    ref: str
    state: str | None
    readable: bool = True


@dataclass(frozen=True)
class BlockedByRead:
    """One candidate's ``blockedBy`` read result.

    A read that could not produce a connection has ``total_count=None`` rather
    than fabricating a count/node mismatch. Otherwise the fields preserve the
    connection GraphQL returned.

    Attributes:
        total_count: The connection's ``totalCount`` — the number of edges the
            candidate asserts, whether or not every node came back. ``None``
            means the connection could not be read.
        nodes: The nodes GraphQL actually returned. May be shorter than
            ``total_count`` (an incomplete page) and may contain unreadable
            nodes (a blocker the token cannot see); see :attr:`BlockerNode`.
    """

    total_count: int | None
    nodes: tuple[BlockerNode, ...] = ()

    @classmethod
    def unprovable(cls) -> "BlockedByRead":
        """Return a result for a connection that could not be read (#438 finding 4)."""
        return cls(total_count=None)


@dataclass(frozen=True)
class Readiness:
    """The verdict one :class:`BlockedByRead` decides — a closed type, not
    three independent primitives (#438 finding 3).

    Earlier revisions carried ``verdict``, ``admissible``, and ``skip_reason``
    as three independently-settable fields, which permitted contradictory
    states no caller ever meant to construct — ``verdict="ready"`` with
    ``admissible=False``, or ``"blocked"`` with ``skip_reason=None``. This
    dataclass keeps ``verdict`` and ``skip_reason`` as the only state that
    varies, derives ``admissible`` from ``verdict`` as a read-only property so
    it can never independently disagree, and validates the
    verdict/skip_reason/blockers pairing in ``__post_init__`` so a
    contradictory instance cannot exist even via direct construction. The two
    valid shapes are reached only through :meth:`ready` and :meth:`blocked` —
    the closed constructors :func:`decide_readiness` itself is pinned to.

    The verdict also answers the two questions a caller used to derive from
    its ``blockers`` (#693): :attr:`refusal_reason` is the reason a Pickup skip
    or a Rolling Run end's ``refusals`` entry carries, and :attr:`pool_class`
    is the class the unbound-Pool rule counts it under. Every caller asks the
    verdict, so a new refusal kind cannot be misclassified at one of them.

    Attributes:
        verdict: ``"ready"`` or ``"blocked"`` — one of
            :data:`READINESS_VERDICTS`.
        skip_reason: One of :data:`SKIP_BLOCKED_BY_OPEN_DEPENDENCY` /
            :data:`SKIP_READINESS_UNPROVABLE` when ``verdict`` is
            ``"blocked"``, else ``None``.
        blockers: The open blockers the read established, in the order the
            connection returned them, and never empty for
            ``blocked_by_open_dependency``. Empty when ``verdict`` is
            ``"ready"``, and also empty for ``readiness_unprovable`` — that reason reports
            that no assertion could be read, so there is nothing proven to
            name.
    """

    verdict: str
    skip_reason: str | None = None
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse the states three independent fields used to permit.

        ``ready`` never carries a reason or a blocker list; ``blocked``
        always carries one of the two closed reasons, and ``blockers`` is
        populated for :data:`SKIP_BLOCKED_BY_OPEN_DEPENDENCY` and only for it
        — the one reason that names a proven fact rather than reporting that
        nothing could be proven.
        """
        if self.verdict not in READINESS_VERDICTS:
            raise ValueError(f"not a closed Readiness verdict: {self.verdict!r}")
        if self.verdict == "ready":
            if self.skip_reason is not None or self.blockers:
                raise ValueError(
                    "a ready Readiness may carry no skip_reason and no blockers"
                )
            return
        if self.skip_reason not in _POOL_CLASS_BY_SKIP_REASON:
            raise ValueError(
                f"a blocked Readiness must name a closed skip_reason, got "
                f"{self.skip_reason!r}"
            )
        if self.blockers and self.skip_reason != SKIP_BLOCKED_BY_OPEN_DEPENDENCY:
            raise ValueError(
                f"{self.skip_reason!r} proves nothing to name; blockers must be empty"
            )
        if not self.blockers and self.skip_reason == SKIP_BLOCKED_BY_OPEN_DEPENDENCY:
            raise ValueError(
                f"{self.skip_reason!r} asserts an open blocker; it must name one"
            )

    @property
    def admissible(self) -> bool:
        """Whether the candidate may be bound at **Pickup**.

        Derived from ``verdict`` — never its own field — so it cannot
        independently disagree with the verdict it describes.
        """
        return self.verdict == "ready"

    @property
    def refusal_reason(self) -> str | None:
        """The reason a refusal of this candidate records, or ``None`` if admitted.

        The ``wrapper.pickup.skipped`` reason at a serial **Pickup**, and the
        same string in a Rolling Run end's ``refusals``. An open blocker names
        its blockers through :func:`blocked_skip_reason`; a verdict that proves
        nothing names only its kind.
        """
        if self.skip_reason is None:
            return None
        if self.blockers:
            return blocked_skip_reason(self.skip_reason, self.blockers)
        return self.skip_reason

    @property
    def pool_class(self) -> str:
        """This verdict's class in the unbound-Pool rule: one of :data:`POOL_CLASSES`.

        :data:`POOL_CLASS_WAITING` and :data:`POOL_CLASS_UNRESOLVED` feed
        :func:`git_loopy.sources.unbound_pool_outcome`'s ``waiting`` and
        ``unresolved`` counts; :data:`POOL_CLASS_ADMITTED` feeds neither.
        """
        if self.skip_reason is None:
            return POOL_CLASS_ADMITTED
        return _POOL_CLASS_BY_SKIP_REASON[self.skip_reason]

    @classmethod
    def ready(cls) -> "Readiness":
        """The one admissible verdict: no reason, no blockers to name."""
        return cls(verdict="ready")

    @classmethod
    def blocked(cls, skip_reason: str, blockers: tuple[str, ...] = ()) -> "Readiness":
        """The one inadmissible verdict, naming why and (if provable) whom."""
        return cls(verdict="blocked", skip_reason=skip_reason, blockers=blockers)


def decide_readiness(read: BlockedByRead) -> Readiness:
    """Decide one candidate's **Readiness** from its ``blockedBy`` read.

    Pure: no clock, no I/O, no ``gh``. The whole decision is the ``read`` it is
    given, which is what lets ``conformance/issue-readiness.json`` drive it
    directly rather than through a GitHub-shaped adapter.

    Order of decision, matching the fixture:

    1. Any node the read positively found **open** makes the candidate
       **blocked**, reason :data:`SKIP_BLOCKED_BY_OPEN_DEPENDENCY`, naming
       every open blocker found — checked *first* so a proven open blocker
       outranks an incomplete or unreadable read (see the module docstring).
    2. Otherwise, an incomplete connection (fewer nodes than ``total_count``)
       or an unreadable node means readiness was never proven: **blocked**,
       reason :data:`SKIP_READINESS_UNPROVABLE`, naming no blockers — there is
       nothing proven to name.
    3. Otherwise every node was read, none is open: **ready**.

    Args:
        read: The candidate's ``blockedBy`` connection, one hop, already read.

    Returns:
        The verdict, whether it is admissible, and why not.
    """
    open_blockers = tuple(node.ref for node in read.nodes if node.state == "open")
    if open_blockers:
        return Readiness.blocked(SKIP_BLOCKED_BY_OPEN_DEPENDENCY, open_blockers)
    incomplete = read.total_count is None or len(read.nodes) < read.total_count
    unreadable = any(not node.readable for node in read.nodes)
    if incomplete or unreadable:
        return Readiness.blocked(SKIP_READINESS_UNPROVABLE)
    return Readiness.ready()
