"""``git_loopy.rolling_pool`` — the Rolling dispatch candidate cache.

**Rolling dispatch** (PRD #219, ADR-0020) refills reusable **Lanes**
continuously, which means it needs to know what work is available *right now*
without paying a full authoritative **Pool** collection on every scheduler
decision. A serial **Iteration** can afford
:meth:`~git_loopy.sources.IssueSource.collect_afk_ready` — it runs once per
Iteration and renders the whole Pool into a prompt. A rolling scheduler that
did the same on every refill turn would spend most of a Run in ``gh``.

This module owns the resulting split, so nothing else has to know about it:

* **Membership is cheap and continuous.**
  :meth:`~git_loopy.sources.RollingIssueSource.shallow_membership` is one
  paginated list call with no per-issue enrichment.
* **Authority is expensive and paid once, at reservation.**
  :meth:`~git_loopy.sources.RollingIssueSource.pickup` re-reads one candidate
  immediately before its **Lane** is reserved and is the only thing a Lane may
  dispatch from.

Design notes:

* **The scheduler expresses one number: refillable capacity.** #219 §2.7
  forbids polling while Lane capacity is full, **Integration** backpressure
  blocks refill, serial ownership is latched, or the Run is draining. Those are
  four different scheduler states, but they collapse to the same instruction —
  *do not look for more work* — so :meth:`RollingPool.service` takes
  ``refillable`` and treats zero as "no demand". There is deliberately **no**
  ``on_integration_complete`` trigger (§2.6): Integration completing matters
  only if it opened capacity the cache cannot satisfy, which the next
  ``service`` call already says.
* **The pool never sleeps and never reads the wall clock.** Backoff is a
  comparison against an injected ``clock``, so a test advances time by hand and
  the scheduler stays free to interleave. Jitter is likewise injected.
* **Emptiness is a claim, not an absence.** #219 §2.13 forbids concluding the
  Pool is empty from incomplete pagination, a failed refresh, or unresolved
  candidates. :meth:`RollingPool.confirm_empty` is therefore a separate,
  deliberate question with a boolean answer, not a side effect of the cache
  happening to be empty.
* **Stale and unavailable are different outcomes.** A closed or relabelled
  issue is dropped; a candidate whose read failed is **quarantined** — it keeps
  its FIFO position, stops blocking the candidates behind it, is retried when a
  later complete refresh still lists it, and blocks an empty claim while it
  remains unresolved.
* **stdlib + ``git_loopy.sources`` only.** Same constraint the sources seam
  carries: no SDK, no Rich, no peer-of-loop imports.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable

from git_loopy.sources import (
    AfkReadyItem,
    LABEL_PARALLEL_SAFE,
    MembershipSnapshot,
    PICKUP_UNAVAILABLE,
    PICKUP_VALIDATED,
    PoolCandidate,
    RollingIssueSource,
)

__all__ = [
    "PoolTake",
    "RefreshBackoff",
    "RollingPool",
    "is_parallel_safe",
]


def is_parallel_safe(candidate: PoolCandidate) -> bool:
    """Return ``True`` iff a human asserted this candidate is **Parallel-safe**.

    The default eligibility predicate. Eligibility is read off the label a human
    applied and is *never* inferred from issue content, dependencies, or code
    overlap (#219 non-goals, CONTEXT.md "Parallel-safe"). An ``int`` ref is also
    required: a local-markdown path or a pull request is not Lane work.
    """
    return isinstance(candidate.ref, int) and LABEL_PARALLEL_SAFE in candidate.labels


@dataclass(frozen=True)
class RefreshBackoff:
    """Bounded exponential backoff for demand-gated membership refresh.

    Attributes:
        initial: Interval after the first refresh that left demand unmet.
        maximum: Ceiling the interval doubles toward. Bounded so a long-idle
            Run still notices new work within a predictable window.
        multiplier: Growth factor applied per consecutive unmet-demand refresh.
    """

    initial: float = 2.0
    maximum: float = 60.0
    multiplier: float = 2.0

    def next_interval(self, previous: float) -> float:
        """Return the interval following ``previous`` (``0`` means "first")."""
        if previous <= 0:
            return self.initial
        return min(previous * self.multiplier, self.maximum)


@dataclass
class _CachedCandidate:
    """A cache entry: the shallow record plus its resolution state."""

    candidate: PoolCandidate
    quarantined: bool = False


@dataclass(frozen=True)
class PoolTake:
    """One walk of the cache, and where in it the walk stopped (#397).

    ``take`` used to return a bare item, which threw away the one fact a
    **Lane Pickup** record cannot reconstruct later: the candidate's place in
    the order it was chosen from. The Pool *is* the order (Wrapper contract
    §3.2), and by the time the Lane binds, the taken candidate has left the
    cache and the sequence that gave its position meaning is gone.

    It carries the same three facts as
    :class:`git_loopy.serial_pickup.SerialPickup` — what was taken, where it
    sat, and how long the order was — so both kinds of Pickup produce one
    vocabulary rather than two.

    Attributes:
        item: The validated, enriched candidate, or ``None`` when nothing in
            the cache currently resolves.
        position: The taken candidate's 1-based place in the cache as it stood
            at the start of the walk, or ``None`` when nothing was taken.
        considered: How many candidates that cache held. Reported even for a
            walk that took nothing, because an exhausted cache and an empty one
            are different Run states and only this number tells them apart.
    """

    item: AfkReadyItem | None
    position: int | None
    considered: int


@dataclass
class RollingPool:
    """The Rolling dispatch candidate cache and its refresh policy.

    Holds a stable FIFO of shallow candidates, decides when membership is worth
    re-reading, and turns a candidate into a dispatchable
    :class:`~git_loopy.sources.AfkReadyItem` only through an authoritative
    pickup.

    Args:
        diag: Diagnostics logger.
        source: The :class:`~git_loopy.sources.RollingIssueSource` seam.
        clock: Monotonic seconds source. Injected so backoff is deterministic.
        jitter: Maps a computed backoff interval to the interval actually
            waited. Defaults to full jitter over the lower half of the window,
            which de-synchronises concurrent Runs without ever waiting longer
            than the bound.
        eligible: Eligibility predicate. Defaults to :func:`is_parallel_safe`;
            a scheduler composes its Run-scoped worked-issue guard into it.
        backoff: The bounded exponential backoff policy.
    """

    diag: logging.Logger
    source: RollingIssueSource
    clock: Callable[[], float] = field(default=lambda: 0.0)
    jitter: Callable[[float], float] | None = None
    eligible: Callable[[PoolCandidate], bool] = is_parallel_safe
    backoff: RefreshBackoff = field(default_factory=RefreshBackoff)

    _entries: list[_CachedCandidate] = field(default_factory=list, init=False)
    _refreshing: bool = field(default=False, init=False)
    _demand_unmet: bool = field(default=False, init=False)
    _interval: float = field(default=0.0, init=False)
    _next_refresh_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if self.jitter is None:
            self.jitter = lambda interval: random.uniform(interval / 2.0, interval)

    # -- read-only views ---------------------------------------------------- #

    @property
    def candidate_refs(self) -> tuple[int | str, ...]:
        """The cached candidate refs in stable FIFO order."""
        return tuple(entry.candidate.ref for entry in self._entries)

    @property
    def available_count(self) -> int:
        """How many cached candidates could currently be handed to a **Lane**.

        The scheduler's demand signal (:meth:`service`) and its emptiness
        question (:meth:`confirm_empty`) both read this, and so does #304's
        serial-fallback report — "how many eligible **Parallel-safe** issues
        did this Run find" is exactly this number.
        """
        return sum(
            1
            for entry in self._entries
            if not entry.quarantined and self.eligible(entry.candidate)
        )

    @property
    def unavailable_count(self) -> int:
        """How many cached candidates are quarantined (#219 §2.11).

        Distinct from :attr:`available_count`'s complement: a quarantined
        candidate *is* an eligible **Parallel-safe** issue whose authoritative
        read failed, so a Run that reported it as absent would send the
        operator off to label work that is already labelled.
        """
        return sum(1 for entry in self._entries if entry.quarantined)

    def candidate(self, ref: int | str) -> PoolCandidate:
        """Return the cached shallow record for ``ref``.

        Raises:
            KeyError: If ``ref`` is not cached.
        """
        for entry in self._entries:
            if entry.candidate.ref == ref:
                return entry.candidate
        raise KeyError(ref)

    # -- refresh ------------------------------------------------------------ #

    def start(self) -> None:
        """Perform the one unconditional refresh at Run startup (#219 §2.1)."""
        self._refresh()

    def service(self, *, refillable: int) -> None:
        """Refresh membership if unmet demand and the backoff window allow it.

        Args:
            refillable: How many **Lanes** the scheduler could fill right now,
                already reduced by Lane capacity, **Integration** backpressure,
                the serial-demand latch, and any cap/abort drain. Zero means no
                demand, and no demand means no refresh — that single number is
                how #219 §2.6-2.7 are enforced.
        """
        unmet = refillable > self.available_count
        if not unmet:
            # Demand met (or absent). Forget the wait: if demand reappears it is
            # new evidence, not a continuation of the old unanswered question.
            self._demand_unmet = False
            self._interval = 0.0
            return
        if not self._demand_unmet:
            # §2.5: demand newly appeared — look now rather than serving out a
            # window opened by an older, already-answered question.
            self._demand_unmet = True
            self._interval = 0.0
            self._next_refresh_at = self.clock()
        if self.clock() < self._next_refresh_at:
            return
        self._refresh()

    # -- reservation -------------------------------------------------------- #

    def take(self) -> PoolTake:
        """Validate candidates in FIFO order and return the first dispatchable one.

        This is the only path from a cached candidate to Lane work, and it
        always pays the authoritative read first (#219 §2.10) — the shallow
        membership that put the candidate in the cache is never authority to
        start a **Lane contribution**.

        Walking the FIFO applies #219 §2.11 as it goes: a **stale** candidate is
        removed on the spot, with no full refresh; an **unavailable** one is
        quarantined, keeps its position, and stops being retried until a later
        complete refresh still lists it, so one unreachable issue can never
        head-of-line-block the candidates behind it.

        Returns:
            The walk (#397): the validated, enriched item — removed from the
            cache, since a reserved candidate must not be offered to a second
            **Lane** — together with where it sat in the order and how long that
            order was. A walk that resolves nothing reports the order it looked
            at anyway; see :class:`PoolTake`.
        """
        walked = list(self._entries)
        for position, entry in enumerate(walked, start=1):
            if entry.quarantined or not self.eligible(entry.candidate):
                continue
            pickup = self.source.pickup(entry.candidate.ref)
            if pickup.outcome == PICKUP_VALIDATED and pickup.item is not None:
                self._entries.remove(entry)
                return PoolTake(
                    item=pickup.item, position=position, considered=len(walked)
                )
            if pickup.outcome == PICKUP_UNAVAILABLE:
                entry.quarantined = True
                self.diag.warning(
                    "candidate %s could not be validated; quarantined until "
                    "a later refresh still lists it",
                    entry.candidate.ref,
                )
                continue
            self._entries.remove(entry)
        return PoolTake(item=None, position=None, considered=len(walked))

    # -- termination -------------------------------------------------------- #

    def confirm_empty(self) -> bool:
        """Ask, authoritatively, whether the **Pool** is genuinely exhausted.

        Called once at full pipeline quiescence with an empty cache (#219
        §2.14). Deliberately a question rather than a property: a cache that
        *looks* empty may simply have failed to read, and #219 §2.13 forbids an
        incomplete snapshot, a failed refresh, or an unresolved candidate from
        establishing final emptiness. Only a complete refresh that finds nothing
        eligible and leaves nothing quarantined may end a Run cleanly as empty.

        Ignores the backoff window — quiescence is its own trigger, and a wait
        armed for the unmet-demand question must not answer this one.

        Returns:
            ``True`` only if the fresh snapshot is complete, no eligible
            candidate remains, and nothing is quarantined.
        """
        snapshot = self._refresh_now()
        if not snapshot.complete:
            self.diag.warning(
                "final Pool refresh was incomplete; not claiming an empty Pool"
            )
            return False
        if any(entry.quarantined for entry in self._entries):
            self.diag.warning(
                "unresolved candidates remain (%s); not claiming an empty Pool",
                ", ".join(
                    str(e.candidate.ref) for e in self._entries if e.quarantined
                ),
            )
            return False
        return self.available_count == 0

    def _refresh_now(self) -> MembershipSnapshot:
        """Force one refresh regardless of the backoff window."""
        self._next_refresh_at = self.clock()
        return self._refresh()

    def _refresh(self) -> MembershipSnapshot:
        """Read membership once, reconcile it, and re-arm the backoff window.

        Re-entrant calls are coalesced (#219 §2.3): while a read is in flight a
        second request is a no-op rather than a second round-trip, so a burst of
        Lanes finishing together asks the source once.
        """
        if self._refreshing:
            return MembershipSnapshot(candidates=(), complete=False)
        self._refreshing = True
        try:
            snapshot = self.source.shallow_membership()
        finally:
            self._refreshing = False

        changed = False
        if snapshot.complete:
            before = self.candidate_refs
            self._reconcile(snapshot)
            changed = self.candidate_refs != before
        else:
            self.diag.warning(
                "membership refresh incomplete; retaining last complete snapshot"
            )

        # §2.5: a membership change is fresh evidence, so the next unmet-demand
        # look happens immediately; otherwise the window widens toward its bound.
        self._interval = 0.0 if changed else self.backoff.next_interval(self._interval)
        self._next_refresh_at = self.clock() + (
            self._jitter(self._interval) if self._interval > 0 else 0.0
        )
        return snapshot

    def _jitter(self, interval: float) -> float:
        assert self.jitter is not None  # set in __post_init__
        return self.jitter(interval)

    def _reconcile(self, snapshot: MembershipSnapshot) -> None:
        """Fold one complete snapshot into the cache without moving survivors.

        #219 §2.9: remove missing or ineligible candidates, update survivors in
        place, append newcomers in the order the snapshot gave them, retain
        quarantined candidates' FIFO positions. Order is the **Queue**'s order —
        a candidate that keeps its place keeps its place.

        That snapshot order is now the Wrapper contract §3.2 **selection
        order**, which is the whole of #393: the first refresh seeds an ordered
        cache and :meth:`take` was already walking it front to back. Nothing
        re-sorts here, deliberately. A later refresh appends a newcomer behind
        the candidates already queued even when it is older or carries
        **Priority**, because reordering a cache that Lanes are walking would
        break the position guarantee this method exists to hold.
        """
        observed = {
            c.ref: c for c in snapshot.candidates if self.eligible(c)
        }
        survivors: list[_CachedCandidate] = []
        for entry in self._entries:
            fresh = observed.pop(entry.candidate.ref, None)
            if fresh is None:
                continue
            # Still listed by an authoritative read: worth validating again.
            survivors.append(_CachedCandidate(candidate=fresh, quarantined=False))
        survivors.extend(
            _CachedCandidate(candidate=c)
            for c in snapshot.candidates
            if c.ref in observed
        )
        self._entries = survivors
