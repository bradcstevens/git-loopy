"""``git_loopy.attempt_lifecycle`` — how many attempts one issue gets (#412).

A Run that cannot make progress on an issue re-picked the same issue, on the
same pair, every **Iteration**, until the **Strike** ceiling aborted the whole
Run. Nothing anywhere held the one fact that would have stopped it: *this Run
has already tried this issue and it did not work*. This module is that fact.

One monotonic per-issue, per-Run state — **fresh → retrying → skipped** — moved
by the same **Session outcome** the **Escalation rung**'s ledger reads. The two
ledgers answer the two dials one ending turns, and they are separate because the
dials are: :mod:`git_loopy.escalation` decides *whether the pair changes*, and
this module decides *whether the issue advances at all*. A crash moves this one
and not that one; every routed issue's first stall moves both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from git_loopy.config import RoutingLifecyclePosition
from git_loopy.session_outcome import SessionOutcome

__all__ = ["AttemptState", "AttemptLedger"]


class AttemptState(Enum):
    """Where one issue sits in this Run's attempt lifecycle.

    Ordered, and the order is the whole machine: an issue only ever moves
    forward along it.
    """

    FRESH = "fresh"
    RETRYING = "retrying"
    SKIPPED = "skipped"


#: The states in lifecycle order, so "one step forward" is an index rather than
#: a table of pairs that could disagree with the enum.
_ORDER: tuple[AttemptState, ...] = (
    AttemptState.FRESH,
    AttemptState.RETRYING,
    AttemptState.SKIPPED,
)

#: The endings that buy the issue one more attempt before defeating it. Both are
#: endings a *second* attempt could plausibly answer differently — a stall
#: because the **Escalation rung** changes the pair under it, a crash because the
#: harness is what failed and the harness is not the work. Every other member of
#: the closed :class:`~git_loopy.session_outcome.SessionOutcome` vocabulary
#: defeats the issue outright, stated as the complement rather than as a second
#: list so a sixth ending cannot be silently retried by omission.
_RETRYABLE_ENDINGS: frozenset[SessionOutcome] = frozenset(
    {SessionOutcome.NO_PROGRESS, SessionOutcome.CRASH}
)


@dataclass
class AttemptLedger:
    """Which issues this Run has already tried, and how far each one got."""

    _states: dict[int | str, AttemptState] = field(default_factory=dict, repr=False)
    _defeats: dict[int | str, SessionOutcome] = field(
        default_factory=dict, repr=False
    )

    def state(self, ref: int | str) -> AttemptState:
        """Where ``ref`` sits now. An issue nobody has worked is fresh."""
        return self._states.get(ref, AttemptState.FRESH)

    def defeated_by(self, ref: int | str) -> SessionOutcome | None:
        """The ending that took ``ref`` out of contention, or ``None``.

        Kept here rather than by the **Pickup** that reports it, because the
        ledger is the only thing that knows *which* of several endings was the
        one too many. A skip that could only say "already attempted" would leave
        an operator unable to tell a Run stalling on hard work from a Run whose
        harness is falling over.
        """
        return self._defeats.get(ref)

    def skipped(self, ref: int | str) -> bool:
        """Whether ``ref`` is defeated and no **Pickup** may bind it again.

        The predicate rather than a comparison at the call site, for
        :meth:`~git_loopy.escalation.EscalationLedger.owed`'s reason: a filter
        that read the state and decided what it meant would be a second copy of
        the disposition, free to disagree with this one.
        """
        return self.state(ref) is AttemptState.SKIPPED

    def lifecycle_position(self, ref: int | str) -> RoutingLifecyclePosition:
        """``ref``'s state as the **Routing resolution** vocabulary spells it.

        The one projection onto
        :class:`~git_loopy.config.RoutingLifecyclePosition`, so every **Pickup**
        reports the position from the ledger that owns it. The routing
        vocabulary has two members to this one's three and needs no third: a
        skipped issue is never bound, so it never resolves a pair to state a
        position on. Everything past ``FRESH`` is therefore ``RETRYING`` —
        which is also what makes a **same-pair crash retry** report a retry, a
        fact a position derived from the **Escalation rung**'s ledger could not
        reach (contract §14).
        """
        if self.state(ref) is AttemptState.FRESH:
            return RoutingLifecyclePosition.FRESH
        return RoutingLifecyclePosition.RETRYING

    def observe(
        self, ref: int | str, outcome: SessionOutcome | None
    ) -> AttemptState:
        """Move ``ref`` for one ending; answer where it now sits.

        Monotonic by construction: the state only ever indexes forward along
        :data:`_ORDER`, clamped at the end of it, so there is no clearing rule to
        get wrong and no ending — including the absence of one — that can put a
        defeated issue back into contention. That is deliberate rather than
        convenient: an issue that advanced *once* under a Run that cannot finish
        it is the ordinary shape of a Run grinding, not evidence the Run has
        recovered, and only a fresh Run withdraws the claim.

        Args:
            ref: The issue the ending belongs to.
            outcome: That session's **Session outcome**, or ``None`` where the
                Iteration advanced its issue and so reached no ending at all.
                An absence spends no attempt — the ledger counts *failures to
                advance*, and work that lands is the opposite of one.

        Returns:
            Where ``ref`` now sits.
        """
        current = self.state(ref)
        if outcome is None:
            return current
        if outcome in _RETRYABLE_ENDINGS:
            moved = _ORDER[min(_ORDER.index(current) + 1, len(_ORDER) - 1)]
        else:
            moved = AttemptState.SKIPPED
        self._states[ref] = moved
        if moved is AttemptState.SKIPPED and ref not in self._defeats:
            self._defeats[ref] = outcome
        return moved
