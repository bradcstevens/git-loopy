"""``git_loopy.attempt_lifecycle`` — how many Strikes one issue may take (#412, ADR-0070).

A Run that cannot make progress on an issue re-picked the same issue on the
same pair, every **Iteration**, until something stopped it. Nothing anywhere
held the one fact that would have stopped it: *this Run has already tried this
issue and it did not work*. This module is that fact.

Since ADR-0070 the fact is a count. Every **Session outcome** charges the issue
it belongs to one **Strike**, and an issue that has taken ``max_strikes`` of
them is **skipped** for the rest of the Run. The monotonic per-issue, per-Run
lifecycle — **fresh → retrying → skipped** — is a projection of that count, so
there is no per-ending table to drift: no Strikes is fresh, at least one short
of the limit is retrying, and the limit is skipped.

It is one of the ledgers one ending turns, and they are separate because the
dials are: :mod:`git_loopy.escalation` decides *whether the pair changes*, and
this module decides *whether the issue is worked at all*. A crash charges this
one and not that one; every routed issue's first stall moves both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from git_loopy.config import RoutingLifecyclePosition
from git_loopy.session_outcome import SessionOutcome

__all__ = ["AttemptState", "AttemptLedger", "DEFAULT_MAX_STRIKES"]


class AttemptState(Enum):
    """Where one issue sits in this Run's attempt lifecycle.

    Ordered, and the order is the whole machine: an issue only ever moves
    forward along it.
    """

    FRESH = "fresh"
    RETRYING = "retrying"
    SKIPPED = "skipped"


#: The default Strike limit, matching ``max_nmt_strikes``'s own default so a
#: ledger built without a Config disposes of an issue as a default Run would.
DEFAULT_MAX_STRIKES = 3


@dataclass
class AttemptLedger:
    """Which issues this Run has already tried, and how many Strikes each took.

    Attributes:
        max_strikes: How many Strikes one issue may take before this Run skips
            it. Must be at least 1. Mirrors ``max_nmt_strikes``.
    """

    max_strikes: int = DEFAULT_MAX_STRIKES
    _strikes: dict[int | str, int] = field(default_factory=dict, repr=False)
    _defeats: dict[int | str, SessionOutcome] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        if self.max_strikes < 1:
            raise ValueError(
                f"max_strikes must be ≥ 1 (got {self.max_strikes!r}); "
                "an issue could never be worked otherwise."
            )

    def strikes(self, ref: int | str) -> int:
        """How many Strikes ``ref`` has taken this Run. An unworked issue has none."""
        return self._strikes.get(ref, 0)

    def state(self, ref: int | str) -> AttemptState:
        """Where ``ref`` sits now, projected from its Strike count."""
        strikes = self.strikes(ref)
        if strikes == 0:
            return AttemptState.FRESH
        if strikes >= self.max_strikes:
            return AttemptState.SKIPPED
        return AttemptState.RETRYING

    def defeated_by(self, ref: int | str) -> SessionOutcome | None:
        """The ending that charged ``ref``'s last Strike, or ``None``.

        Kept here rather than by the **Pickup** that reports it, because the
        ledger is the only thing that knows *which* of several endings was the
        one too many. A skip that could only say "already attempted" would leave
        an operator unable to tell a Run stalling on hard work from a Run whose
        harness is falling over.
        """
        return self._defeats.get(ref)

    def skipped(self, ref: int | str) -> bool:
        """Whether ``ref`` is out of Strikes and no **Pickup** may bind it again.

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
        """Charge ``ref`` one Strike for one ending; answer where it now sits.

        Monotonic by construction: the count only ever grows, and it stops at
        ``max_strikes``, so no ending — including the absence of one — can put
        a skipped issue back into contention. That is deliberate rather than
        convenient: an issue that advanced *once* under a Run that cannot finish
        it is the ordinary shape of a Run grinding, not evidence the Run has
        recovered, and only a fresh Run withdraws the claim.

        Every ending charges the same single Strike (ADR-0070). Which ending it
        was decides what the **Escalation rung** and the **Attempt evidence**
        ledgers do, not how many tries the issue has left.

        Args:
            ref: The issue the ending belongs to.
            outcome: That session's **Session outcome**, or ``None`` where the
                Iteration advanced its issue and so reached no ending at all.
                An absence charges nothing — the ledger counts *failures to
                advance*, and work that lands is the opposite of one.

        Returns:
            Where ``ref`` now sits.
        """
        if outcome is None or self.skipped(ref):
            return self.state(ref)
        self._strikes[ref] = self.strikes(ref) + 1
        if self.skipped(ref):
            self._defeats[ref] = outcome
        return self.state(ref)
