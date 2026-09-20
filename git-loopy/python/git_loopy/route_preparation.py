"""Preparing **Routing proposals** ahead of the Pickups that bind them (#566).

The nonbinding half of ADR-0057's "proposal, binding, and reuse". A running
Runner prepares a proposal for each candidate it has *already established* as
eligible, so that the **Pickup** which later reaches one finds an assessment
already made instead of buying it on the critical path.

What this deliberately is not:

- **Not a dispatcher.** It decides nothing about which issue is worked, in what
  order, or whether it may be. The **Pool** order and the admission rules are
  untouched, and a prepared proposal that never gets picked up simply expires.
- **Not a Lease.** Preparing costs a candidate nothing and reserves nothing;
  two Runs may prepare the same issue and neither has claimed it.
- **Not authority.** A proposal is an input to a **Pickup**'s own fresh
  validation, never a substitute for it. That is why this module hands over a
  :class:`~git_loopy.dynamic_route.RoutingProposal` and not a route: the router
  still re-reads both live sources, still compares the relevant input identity,
  and still reassesses what moved.
- **Not a service.** It runs only while a Run is running, on that Run's own
  event loop, and stops when the Run does.

The bounds that keep it from becoming any of those live here: the configured
**Route selector** concurrency, one attempt per candidate per Run, and a latch
that ends preparation outright the moment the routing allowance or the
assessment deadline is spent — because after exhaustion no further routing call
may be admitted at all, and a desk that kept trying would spin for the rest of
the Run.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Iterable, Protocol, Sequence

from .dynamic_route import RoutingProposal, RoutingUnavailableReason

__all__ = [
    "HALTING_REASONS",
    "PreparationOutcome",
    "PreparedRoute",
    "RoutePreparation",
]


class PreparationOutcome(Enum):
    """What preparation reached for one eligible candidate.

    Four answers rather than a boolean, because "no proposal" has three very
    different causes and an operator is owed which: an operator's own **Static
    route** made the selector unnecessary, an earlier Run's decision is already
    there to revalidate for free, or routing could not propose at all.
    """

    #: A nonbinding proposal exists and is waiting for this issue's Pickup.
    PROPOSED = "proposed"
    #: A **Static route** applies, so no **Route selector** call was bought.
    STATIC = "static"
    #: A **Reusable route** is already available; its Pickup revalidates free.
    REUSABLE = "reusable"
    #: Preparation could not propose. The Pickup still decides for itself.
    UNAVAILABLE = "unavailable"


#: Refusals that end preparation for the whole Run rather than for one
#: candidate. Both mean the Run's authorized routing budget is gone, and
#: ADR-0057 admits no further routing calls after that — so continuing to
#: prepare would be a loop that spends nothing and never ends.
HALTING_REASONS = frozenset(
    {
        RoutingUnavailableReason.QUOTA_EXHAUSTED,
        RoutingUnavailableReason.DEADLINE_EXHAUSTED,
    }
)


@dataclass(frozen=True)
class PreparedRoute:
    """One candidate's preparation outcome, as the desk records it.

    Attributes:
        ref: The issue this describes.
        outcome: See :class:`PreparationOutcome`.
        proposal: The nonbinding proposal, present only for
            :attr:`PreparationOutcome.PROPOSED`.
        reason: Why routing could not propose, for
            :attr:`PreparationOutcome.UNAVAILABLE`.
        detail: A short, issue-safe phrase for a reader. Never a route.
    """

    ref: int | str
    outcome: PreparationOutcome
    proposal: RoutingProposal | None = None
    reason: RoutingUnavailableReason | None = None
    detail: str | None = None

    @property
    def halting(self) -> bool:
        """Does this outcome end preparation for the rest of the Run?"""
        return self.reason in HALTING_REASONS


class _Candidate(Protocol):
    """The one thing preparation needs of a **Pool** candidate: its identity."""

    @property
    def ref(self) -> int | str: ...  # pragma: no cover - structural only


_Prepare = Callable[[_Candidate], Awaitable[PreparedRoute]]


class RoutePreparation:
    """The Run-scoped desk that prepares proposals for eligible candidates.

    Args:
        prepare: What preparing one candidate means — classify it, settle
            whether an operator already routed it, and only then assess.
            Injected rather than assembled here because every one of those
            steps is the Run's own collaborator, and a desk that reached for
            them would be a second **Pickup** rather than a bounded helper.
        concurrency: The operator's configured **Route selector** concurrency.
            The same bound the admission ledger enforces, applied here too so
            the desk does not queue work the ledger will only serialize.
        clock: Reads the instant a proposal's validity is judged against.
        on_prepared: Told about every outcome, in the order they were reached.
            The Run's record of preparation, which is what makes proposal state
            visible without making it look like a binding.
        diag: The Run's diagnostics logger, or ``None``.
    """

    def __init__(
        self,
        *,
        prepare: _Prepare,
        concurrency: int,
        clock: Callable[[], datetime] | None = None,
        on_prepared: Callable[[PreparedRoute], None] | None = None,
        diag: logging.Logger | None = None,
    ) -> None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int):
            raise ValueError("selector concurrency must be an integer")
        if concurrency < 1:
            raise ValueError("selector concurrency must be at least 1")
        self._prepare = prepare
        self._concurrency = concurrency
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_prepared = on_prepared
        self._diag = diag
        self._prepared: dict[int | str, PreparedRoute] = {}
        self._settled: set[int | str] = set()
        self._in_progress: set[int | str] = set()
        self._tasks: dict[int | str, asyncio.Task[PreparedRoute | None]] = {}
        self._pickups: set[int | str] = set()
        self._priority = asyncio.Condition()
        self._limit = asyncio.Semaphore(concurrency)
        self._halted = False

    @property
    def halted(self) -> bool:
        """Has the Run's routing allowance or deadline ended preparation?"""
        return self._halted

    def outstanding(self) -> tuple[int | str, ...]:
        """The refs currently holding a prepared proposal, in preparation order."""
        return tuple(
            ref
            for ref, entry in self._prepared.items()
            if entry.outcome is PreparationOutcome.PROPOSED
        )

    def unsettled(self, refs: Iterable[int | str]) -> tuple[int | str, ...]:
        """Which of ``refs`` this Run has not yet tried to prepare.

        Asked *before* a caller pays to re-read a candidate authoritatively.
        The **Rolling** driver comes round many times a second and the desk
        prepares each candidate once per Run, so without this the second turn
        onwards would buy a tracker read per cached candidate to discover there
        was nothing to do.
        """
        return tuple(
            ref
            for ref in refs
            if ref not in self._settled and ref not in self._in_progress
        )

    async def prepare_ahead(
        self, candidates: Sequence[_Candidate]
    ) -> tuple[PreparedRoute, ...]:
        """Prepare ``candidates``, next Pickup first, within the bounds.

        ``candidates`` arrives in the **Pool**'s own order, so its head *is*
        the next Pickup. That one is prepared alone and to completion before
        any other starts: preparing ahead is worth nothing if the issue about
        to be worked is queued behind speculation. The rest then run together
        under the configured concurrency.

        A candidate this Run already settled — prepared, statically routed,
        revalidatable, or refused — is not prepared again. One attempt per
        candidate per Run is what keeps this from being a loop that re-buys the
        same assessment every time the driver comes round.
        """
        if self._halted:
            return ()
        pending = [
            candidate
            for candidate in candidates
            if candidate.ref not in self._settled
            and candidate.ref not in self._in_progress
        ]
        if not pending:
            return ()
        head_done = asyncio.Event()

        async def bounded(candidate: _Candidate, *, head: bool) -> PreparedRoute | None:
            try:
                if not head:
                    await head_done.wait()
                async with self._priority:
                    await self._priority.wait_for(
                        lambda: not self._pickups or candidate.ref in self._pickups
                    )
                async with self._limit:
                    if self._halted:
                        return None
                    return await self._prepare_one(candidate)
            except asyncio.CancelledError:
                self._cancelled(candidate.ref)
                raise

        tasks: dict[int | str, asyncio.Task[PreparedRoute | None]] = {}
        for candidate in pending:
            if candidate.ref in self._in_progress:
                continue
            self._in_progress.add(candidate.ref)
            task = asyncio.create_task(bounded(candidate, head=not tasks))
            if not tasks:
                # A task cancelled before its first step runs no coroutine cleanup.
                task.add_done_callback(lambda _task: head_done.set())
            tasks[candidate.ref] = task
            self._tasks[candidate.ref] = task
        try:
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
            return tuple(result for result in results if isinstance(result, PreparedRoute))
        finally:
            for ref, task in tasks.items():
                if task.cancelled():
                    self._cancelled(ref)
                self._in_progress.discard(ref)
                self._tasks.pop(ref, None)

    async def prioritize(self, ref: int | str) -> None:
        """Free routing slots for Pickup, joining only its own preparation.

        Completed proposals survive. Interrupted assessments are explicitly
        unavailable, not reusable results; their eventual Pickup may assess
        within the remaining allowance.
        """
        async with self._priority:
            self._pickups.add(ref)
            self._priority.notify_all()
        interrupted = tuple(
            task for other, task in self._tasks.items() if other not in self._pickups
        )
        for task in interrupted:
            task.cancel()
        await asyncio.gather(
            *interrupted,
            return_exceptions=True,
        )
        own = self._tasks.get(ref)
        if own is not None and not own.cancelled():
            await asyncio.shield(own)

    async def finish_pickup(self, ref: int | str) -> None:
        """Resume preparation after this authoritative route has settled."""
        async with self._priority:
            self._pickups.discard(ref)
            self._priority.notify_all()

    def _cancelled(self, ref: int | str) -> None:
        if ref in self._settled:
            return
        self._settled.add(ref)
        if self._on_prepared is not None:
            self._on_prepared(
                PreparedRoute(
                    ref=ref,
                    outcome=PreparationOutcome.UNAVAILABLE,
                    detail="preparation cancelled; Pickup must validate its own route",
                )
            )

    def take(self, ref: int | str) -> RoutingProposal | None:
        """Hand this issue's proposal to its **Pickup**, once and only if live.

        Popped rather than read, because a proposal is bound at most once: a
        second Pickup of the same issue is a new attempt with a new attempt
        history, and the proposal made before that ending could not describe
        it.

        A proposal whose validity window has closed is dropped instead of
        handed over. Refusing it here costs a fresh assessment the Pickup was
        always able to make, while handing it over would be work started under
        a stale result — which is the one outcome ADR-0057 rules out flatly.
        """
        entry = self._prepared.pop(ref, None)
        if entry is None or entry.proposal is None:
            return None
        if self._clock() > entry.proposal.valid_until:
            if self._diag is not None:
                self._diag.info(
                    "prepared route for issue #%s expired before its Pickup; "
                    "assessing again",
                    ref,
                )
            return None
        return entry.proposal

    def forget(self, refs: Iterable[int | str]) -> None:
        """Drop what this Run knows about ``refs`` so they may be prepared again.

        The seam a Pickup ending uses: an issue that has just been worked has a
        new attempt history, so anything prepared for it describes inputs that
        no longer exist. Dropping it is not the same as preparing it again —
        the next pass decides that, under the same bounds.
        """
        for ref in refs:
            self._prepared.pop(ref, None)
            self._settled.discard(ref)
            self._pickups.discard(ref)

    async def _prepare_one(self, candidate: _Candidate) -> PreparedRoute | None:
        ref = candidate.ref
        try:
            outcome = await self._prepare(candidate)
        except Exception as exc:  # noqa: BLE001 - preparation never fails a Run
            # Preparing ahead is an optimisation on top of a **Pickup** that
            # still works without it, so no way of failing to prepare may
            # reach the Run. The candidate stays eligible and its Pickup
            # assesses for itself.
            if self._diag is not None:
                self._diag.warning(
                    "route preparation for issue #%s failed unexpectedly: %s: %s",
                    ref,
                    type(exc).__name__,
                    exc,
                )
            outcome = PreparedRoute(
                ref=ref,
                outcome=PreparationOutcome.UNAVAILABLE,
                reason=RoutingUnavailableReason.SELECTOR_UNAVAILABLE,
                detail="preparation failed; see Run diagnostics",
            )
        self._settled.add(ref)
        if outcome.outcome is PreparationOutcome.PROPOSED:
            self._prepared[ref] = outcome
        if outcome.halting:
            self._halted = True
            if self._diag is not None:
                self._diag.info(
                    "route preparation stopped for this Run: %s",
                    outcome.reason.value if outcome.reason is not None else "exhausted",
                )
        if self._on_prepared is not None:
            self._on_prepared(outcome)
        return outcome
