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
    "SKIP_BLOCKED_BY_OPEN_DEPENDENCY",
    "SKIP_READINESS_UNPROVABLE",
    "READINESS_VERDICTS",
    "BlockerNode",
    "BlockedByRead",
    "Readiness",
    "decide_readiness",
]

#: At least one ``blocked_by`` dependency was read and is open.
SKIP_BLOCKED_BY_OPEN_DEPENDENCY: Final[str] = "blocked_by_open_dependency"

#: The ``blockedBy`` connection was incomplete, or a node came back unreadable.
SKIP_READINESS_UNPROVABLE: Final[str] = "readiness_unprovable"

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
    """One candidate's ``blockedBy`` connection, exactly as GraphQL returned it.

    Attributes:
        total_count: The connection's ``totalCount`` — the number of edges the
            candidate asserts, whether or not every node came back.
        nodes: The nodes GraphQL actually returned. May be shorter than
            ``total_count`` (an incomplete page) and may contain unreadable
            nodes (a blocker the token cannot see); see :attr:`BlockerNode`.
    """

    total_count: int
    nodes: tuple[BlockerNode, ...] = ()


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

    Attributes:
        verdict: ``"ready"`` or ``"blocked"`` — one of
            :data:`READINESS_VERDICTS`.
        skip_reason: One of :data:`SKIP_BLOCKED_BY_OPEN_DEPENDENCY` /
            :data:`SKIP_READINESS_UNPROVABLE` when ``verdict`` is
            ``"blocked"``, else ``None``.
        blockers: The open blockers the read established, in the order the
            connection returned them. Empty when ``verdict`` is ``"ready"``,
            and also empty for ``readiness_unprovable`` — that reason reports
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
        only ever populated for :data:`SKIP_BLOCKED_BY_OPEN_DEPENDENCY` — the
        one reason that names a proven fact rather than reporting that
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
        if self.skip_reason not in (
            SKIP_BLOCKED_BY_OPEN_DEPENDENCY,
            SKIP_READINESS_UNPROVABLE,
        ):
            raise ValueError(
                f"a blocked Readiness must name a closed skip_reason, got "
                f"{self.skip_reason!r}"
            )
        if self.blockers and self.skip_reason != SKIP_BLOCKED_BY_OPEN_DEPENDENCY:
            raise ValueError(
                f"{self.skip_reason!r} proves nothing to name; blockers must be empty"
            )

    @property
    def admissible(self) -> bool:
        """Whether the candidate may be bound at **Pickup**.

        Derived from ``verdict`` — never its own field — so it cannot
        independently disagree with the verdict it describes.
        """
        return self.verdict == "ready"

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
    incomplete = len(read.nodes) < read.total_count
    unreadable = any(not node.readable for node in read.nodes)
    if incomplete or unreadable:
        return Readiness.blocked(SKIP_READINESS_UNPROVABLE)
    return Readiness.ready()
