"""``git_loopy.rolling_scheduler`` — the Rolling dispatch state machine.

**Rolling dispatch** (PRD #219, ADR-0020) retires the **Wave** barrier. Instead
of grouping **Lanes** into a cohort, waiting for the slowest, and integrating
the batch, a Run owns reusable Lane slots and refills each one the moment it
frees. This module is the decision core of that scheduler: which Lanes may be
reserved right now, what each Lane-boundary outcome means, and when the Run has
genuinely drained.

It is deliberately **pure**: no ``asyncio``, no worktrees, no ``gh``, no SDK, no
wall clock. Every transition is a plain method call with a plain return value,
which is what makes #219's testing §1 ("assert invariants after every
transition") achievable at all. The async orchestrator in
:mod:`git_loopy.loop` performs the I/O and reports outcomes back here.

Design notes:

* **One refill decision, not a loop of decisions.** #219 §1.3 requires that a
  scheduler turn reserve *every* currently refillable Lane, bounded by effective
  Lane concurrency, eligible validated candidates, available ``max_iterations``
  units, **Integration** backpressure, the serial-demand latch, and any
  cap/abort drain. :meth:`RollingScheduler.reserve` is that whole decision, so
  no caller reconstructs the bounds and none of them can be forgotten
  independently.
* **A reservation is provisional until a session starts.** §3.2-3.4: worktree
  creation and setup may fail, and a failure there must leave no trace — no
  **Lane contribution**, no consumed cap unit, no **Strike**, and the candidate
  still eligible. The cap unit and the contribution identity are therefore
  minted at :meth:`start_session`, not at :meth:`reserve`. But a provisional
  reservation still holds its Lane and still reserves a cap unit *against
  further reservations*, so concurrent setup cannot oversubscribe either.
* **The worked guard latches at session start and never releases.** §1.7: once
  an issue's agent session has started it may never take a second Lane in this
  Run, through parking, Integration, recovery, closure failure, and serial
  fallback alike. Before session start there is no guard — only the reservation
  itself, which is what makes §3.3's "leave the candidate eligible" true.
* **Admission is a consequence of finishing, not a separate question.** §3.9 and
  §4.2-4.3: a changed durable branch is offered, and it either fits the H=2
  backlog (admitted; its Lane frees) or it does not (parked; its Lane is
  retained). :meth:`finish_work` returns which happened, and
  :meth:`finalize` returns whatever freeing an H slot admitted from the parked
  FIFO, so the ordering rules live in one place.
* **Terminal is terminal exactly once.** §7.6: every terminal unpublished
  contribution adds exactly one Strike and no intermediate phase adds any. The
  scheduler records the reaction on the finalized row rather than ticking the
  Strike machine itself, because that machine is shared with serial
  **Iterations** and belongs to the composed :class:`~git_loopy.loop._Loop`.
* **stdlib + the two Rolling seams only.** No SDK, no Rich, no peer-of-loop
  imports — the same constraint :mod:`git_loopy.rolling_pool` carries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from git_loopy.rolling_pool import RollingPool
from git_loopy.sources import AfkReadyItem, PoolCandidate

__all__ = [
    "ADMITTED",
    "Contribution",
    "INTEGRATION_HIGH_WATER",
    "PARKED",
    "PHASE_DRAINING_FOR_ABORT",
    "PHASE_DRAINING_FOR_SERIAL",
    "PHASE_ROLLING",
    "PHASE_ROLLING_REFILL_TURN",
    "PHASE_SERIAL_OWNERSHIP",
    "REASON_CHECKPOINT_FAILED",
    "REASON_PUBLISHED",
    "REASON_SERIAL_FALLBACK",
    "REASON_UNCHANGED_BRANCH",
    "Reservation",
    "RollingScheduler",
    "STRIKE_ADD",
    "STRIKE_RESET",
    "TERMINAL",
]

# Terminal dispositions of a **Lane contribution**, mirroring
# :data:`git_loopy.events.CONTRIBUTION_TERMINAL_REASONS`. ``published`` is the
# only Parallel progress; each of the others adds exactly one **Strike**.
REASON_PUBLISHED = "published"
REASON_UNCHANGED_BRANCH = "unchanged_branch"
REASON_CHECKPOINT_FAILED = "checkpoint_failed"
REASON_SERIAL_FALLBACK = "serial_fallback"

# What a finalized contribution does to the shared Strike machine (#219 §7.4,
# §7.6). The scheduler records the reaction; :class:`~git_loopy.loop._Loop`
# still owns the machine, because serial **Iterations** tick the same one.
STRIKE_RESET = "reset"
STRIKE_ADD = "+1"

# What finishing Lane work did with the contribution (#219 §3.8-3.9, §4.2-4.3).
ADMITTED = "admitted"
PARKED = "parked"
TERMINAL = "terminal"

# #219 §4.1: the **Integration backlog** high-water is exactly two
# admitted-but-not-landed contributions — one current Integrator owner and one
# FIFO waiter. Fixed by ADR-0020, not operator-configurable.
INTEGRATION_HIGH_WATER = 2

# The controlling scheduler phases #219 §9 requires the Dashboard to expose.
# ``rolling_refill_turn`` is the one §5.9 grants after a serial **Iteration**:
# one full refill decision under all normal bounds, before any remaining
# validated serial demand may relatch.
PHASE_ROLLING = "rolling"
PHASE_DRAINING_FOR_SERIAL = "draining_for_serial"
PHASE_SERIAL_OWNERSHIP = "serial_ownership"
PHASE_ROLLING_REFILL_TURN = "rolling_refill_turn"
PHASE_DRAINING_FOR_ABORT = "draining_for_abort"


def _ref_sort_key(ref: int | str) -> tuple[int, int, str]:
    """A total order over issue refs, ints ascending first.

    #219 §4.3's admission tie-break is "ascending issue number". Lane work is
    only ever created for ``int`` refs, but keeping the key total-orderable
    over a mixed set means the sort can never raise.
    """
    if isinstance(ref, int):
        return (0, ref, "")
    return (1, 0, str(ref))


@dataclass(frozen=True)
class Reservation:
    """A provisional claim on one **Lane** slot for one validated issue.

    Provisional means #219 §3.2: it holds the Lane and reserves a cap unit
    against further reservations, but has not created a **Lane contribution**,
    consumed a ``max_iterations`` unit, or touched **Strike**. It becomes a
    contribution at :meth:`RollingScheduler.start_session` and evaporates
    without trace at :meth:`RollingScheduler.release`.

    Attributes:
        lane_id: The reusable Lane slot this reservation holds.
        item: The authoritatively validated, enriched item — the product of the
            :meth:`~git_loopy.sources.RollingIssueSource.pickup` that #219
            §2.10 requires immediately before reservation.
    """

    lane_id: str
    item: AfkReadyItem


@dataclass
class Contribution:
    """One issue's end-to-end Parallel lifecycle (#219 "Lane contribution").

    Opened when the agent session starts and closed when the contribution
    reaches a terminal disposition. It **outlives the Lane slot it started in**:
    admission to **Integration** frees that Lane for refill while this
    contribution is still open, which is exactly why Parallel accounting is
    keyed by :attr:`contribution_id` and not by :attr:`lane_id`.

    Attributes:
        contribution_id: Stable, Run-unique identity.
        ref: The issue this contribution owns.
        lane_id: The reusable Lane it *started* in. Never changes, even after
            that Lane has been handed to someone else.
        model: The model resolved once at pickup and reused for recovery (#147,
            #148).
        reasoning_effort: The effort resolved alongside :attr:`model`.
        published: ``True`` only after green publication *and* verified closure.
        reason: The terminal disposition, one of the ``REASON_*`` constants.
            ``None`` while the contribution is still open.
        strike_reaction: :data:`STRIKE_RESET` or :data:`STRIKE_ADD`, recorded
            once at finalization. ``None`` while open.
    """

    contribution_id: str
    ref: int | str
    lane_id: str
    model: str | None = None
    reasoning_effort: str | None = None
    published: bool = False
    reason: str | None = None
    strike_reaction: str | None = None


@dataclass
class RollingScheduler:
    """The Rolling dispatch state machine (#219 §1, §3).

    Args:
        diag: Diagnostics logger.
        pool: The **Pool** candidate cache and pickup seam.
        lane_cap: The configured **Lane cap** — a strict upper bound for the
            whole Run, never mutated (#219 §6).
        max_iterations: The Run's iteration cap; ``0`` means unbounded.
    """

    diag: logging.Logger
    pool: RollingPool
    lane_cap: int
    max_iterations: int = 0

    _lanes_held: dict[str, object] = field(default_factory=dict, init=False)
    _units_spent: int = field(default=0, init=False)
    _finalized: list[Contribution] = field(default_factory=list, init=False)
    _open: dict[str, Contribution] = field(default_factory=dict, init=False)
    _admitted: list[Contribution] = field(default_factory=list, init=False)
    _parked: list[tuple[int, Contribution]] = field(default_factory=list, init=False)
    _turn: int = field(default=0, init=False)
    _serial_latched: bool = field(default=False, init=False)
    _serial_requests: list[tuple[int | str | None, str]] = field(
        default_factory=list, init=False
    )
    _abort_latched: bool = field(default=False, init=False)
    _phase: str = field(default=PHASE_ROLLING, init=False)
    _worked: set[int | str] = field(default_factory=set, init=False)
    _in_setup: set[int | str] = field(default_factory=set, init=False)
    _next_contribution: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        # Compose the Run-scoped worked-issue guard into the Pool's eligibility
        # predicate (#219 §2.15). The guard deliberately does not live in the
        # cache: it is Run state this scheduler owns, and a cache could only
        # ever approximate it.
        inner = self.pool.eligible
        self.pool.eligible = lambda c: inner(c) and self._unclaimed(c)

    def _unclaimed(self, candidate: PoolCandidate) -> bool:
        """Whether this Run has neither worked ``candidate`` nor reserved it.

        Two distinct claims, because they have different lifetimes.
        :attr:`_worked` is #219 §1.7's monotonic guard: it latches at agent
        session start and never releases. :attr:`_in_setup` covers the window a
        reservation is still provisional — the candidate is gone from the cache
        but a membership refresh would re-list it, and §3.3 makes it eligible
        again only if that setup *fails*.
        """
        return candidate.ref not in self._worked and candidate.ref not in self._in_setup

    @property
    def effective_limit(self) -> int:
        """The current effective Lane concurrency.

        #219 §6 starts a Run at ``min(configured Lane cap, 3)``. Adaptation
        under **Integration**, 429, AI-credit, and host pressure is slice 6's;
        until then this is the static-safe value the reaction table also
        prescribes whenever a required signal is unavailable.
        """
        return min(self.lane_cap, 3)

    @property
    def remaining_units(self) -> int | None:
        """``max_iterations`` units left to spend, or ``None`` when unbounded.

        A provisional :class:`Reservation` has not *spent* a unit (#219 §3.2) —
        only :meth:`start_session` does — so this counts sessions actually
        started. :attr:`refillable` is what withholds units from concurrent
        setup, per §1.3.
        """
        if self.max_iterations == 0:
            return None
        return max(0, self.max_iterations - self._units_spent)

    @property
    def finalized(self) -> tuple[Contribution, ...]:
        """The finalized **Lane contribution** rows, in finalization order."""
        return tuple(self._finalized)

    @property
    def open_count(self) -> int:
        """How many **Lane contributions** are still open (#219 §7.15).

        A contribution stays open through parking, FIFO wait, **Integration**,
        closure reconciliation, and auto-resolution — so this is deliberately
        *not* the number of busy Lanes.
        """
        return len(self._open)

    @property
    def admitted(self) -> tuple[Contribution, ...]:
        """The **Integration backlog**: admitted-but-not-landed contributions.

        Never longer than :data:`INTEGRATION_HIGH_WATER` — one current
        Integrator owner plus one FIFO waiter (#219 §4.1).
        """
        return tuple(self._admitted)

    @property
    def parked(self) -> tuple[Contribution, ...]:
        """Finished contributions waiting for admission, in admission order.

        A parked contribution still owns its **Lane** slot (#219 §4.3), so
        parking is backpressure the refill decision feels directly.
        """
        return tuple(c for _turn, c in self._parked)

    @property
    def refillable(self) -> int:
        """How many **Lanes** this scheduler could fill right now (#219 §1.3).

        The single number #219 §2.7 wants: every bound already applied, so a
        zero means "do not look for more work" whatever the reason.
        """
        if self._serial_latched and self._phase != PHASE_ROLLING_REFILL_TURN:
            # §5.3: serial ownership latched, so no new reservation. The one
            # refill turn §5.9 grants after a serial Iteration is the deliberate
            # exception — it runs before any remaining demand may relatch.
            return 0
        if self._abort_latched:
            # §7.7: drain-confirmed abort stops refill but cancels nothing.
            return 0
        free_lanes = self.effective_limit - len(self._lanes_held)
        remaining = self.remaining_units
        if remaining is not None:
            # A provisional reservation has not spent its unit yet but must not
            # let a second reservation spend it either (#219 §1.3).
            free_lanes = min(free_lanes, remaining - len(self._lanes_held))
        return max(0, free_lanes)

    @property
    def phase(self) -> str:
        """The controlling scheduler phase (#219 §9 "Serial handoff")."""
        if self._phase != PHASE_ROLLING:
            return self._phase
        if self._serial_latched:
            return PHASE_DRAINING_FOR_SERIAL
        if self._abort_latched:
            return PHASE_DRAINING_FOR_ABORT
        return PHASE_ROLLING

    @property
    def quiescent(self) -> bool:
        """Whether the whole Parallel pipeline has drained (#219 §5.5, §7.16).

        Full quiescence means no provisional setup, no active Lane work, no
        parked contribution, an empty **Integration** backlog, and no open
        contribution at all — the precondition for granting serial ownership,
        and for a ``wrapper.run.end`` to be valid.
        """
        return not (
            self._lanes_held or self._open or self._admitted or self._parked
        )

    def serial_turn(self) -> bool:
        """Grant one serial **Iteration** exclusive ownership of base, if ready.

        #219 §5.5-5.6: serial ownership requires *full* Parallel quiescence, so
        this is a question with a boolean answer rather than a state the caller
        sets. A ``True`` moves the scheduler into
        :data:`PHASE_SERIAL_OWNERSHIP` until :meth:`serial_finished`.
        """
        if not self._serial_latched or not self.quiescent:
            return False
        self._phase = PHASE_SERIAL_OWNERSHIP
        self._serial_requests.clear()
        self._serial_latched = False
        return True

    def serial_finished(self) -> None:
        """Hand base back to Rolling dispatch for exactly one refill turn.

        #219 §5.9-5.10: after the serial Iteration the scheduler refreshes and
        gets *one* full refill decision, reserving every currently refillable
        Lane under all normal bounds. Only after that decision may remaining
        validated serial demand relatch — which is what keeps neither serial
        nor Parallel-safe work starving the other.
        """
        self._phase = PHASE_ROLLING_REFILL_TURN

    def start(self) -> None:
        """Perform the Run-startup **Pool** refresh (#219 §2.1)."""
        self.pool.start()

    def reserve(self) -> tuple[Reservation, ...]:
        """Reserve every currently refillable **Lane** in one decision (#219 §1.3)."""
        self.pool.service(refillable=self.refillable)
        reservations: list[Reservation] = []
        while self.refillable > 0:
            item = self.pool.take()
            if item is None:
                break
            lane_id = self._free_lane()
            reservation = Reservation(lane_id=lane_id, item=item)
            self._lanes_held[lane_id] = reservation
            self._in_setup.add(item.ref)
            reservations.append(reservation)
        if self._phase == PHASE_ROLLING_REFILL_TURN:
            # §5.10: the granted turn is spent, so remaining serial demand may
            # relatch from here on.
            self._phase = PHASE_ROLLING
        return tuple(reservations)

    def release(self, reservation: Reservation) -> None:
        """Release a provisional reservation whose setup failed (#219 §3.3).

        Frees the **Lane** and leaves no trace: no **Lane contribution**, no
        Summary row, no ``max_iterations`` unit, and no **Strike**. The
        candidate stays eligible for a later validated pickup — the next
        complete membership refresh re-lists it, because it is still open and
        still carries both labels.
        """
        self._lanes_held.pop(reservation.lane_id, None)
        self._in_setup.discard(reservation.item.ref)

    def start_session(
        self,
        reservation: Reservation,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> Contribution:
        """Turn a provisional reservation into an open **Lane contribution**.

        The agent session starting is the single moment #219 §3.4 makes
        everything real at once: one ``max_iterations`` unit is spent (§7.9), a
        stable ``contribution_id`` is minted (§7.2), the issue latches into the
        Run-scoped worked guard for good (§1.7), and the resolved model/effort
        pair binds for both this Lane's work and its later recovery (#148).
        """
        self._next_contribution += 1
        contribution = Contribution(
            contribution_id=f"c{self._next_contribution}",
            ref=reservation.item.ref,
            lane_id=reservation.lane_id,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        self._units_spent += 1
        self._in_setup.discard(contribution.ref)
        self._worked.add(contribution.ref)
        self._open[contribution.contribution_id] = contribution
        self._lanes_held[reservation.lane_id] = contribution
        return contribution

    def finish_work(
        self, contribution: Contribution, *, changed: bool, checkpoint_ok: bool = True
    ) -> str:
        """Resolve the Lane-work boundary for ``contribution`` (#219 §3.7-3.11).

        Called once the Lane's agent session has ended, its commits are
        accounted, and its per-Lane **Checkpoint** outcome is known — never
        before, because §3.7 forbids tearing down or admitting any state while
        that outcome is still unknown.

        Args:
            changed: Whether the Lane branch carries durable work. An unchanged
                branch has nothing to integrate (§3.8); a Checkpoint-only
                branch counts as changed (§3.9).
            checkpoint_ok: Whether the per-Lane Checkpoint succeeded. A failure
                preserves the dirty branch and worktree and may never admit
                incomplete state (§3.10).

        Returns:
            :data:`ADMITTED` when the branch entered the **Integration**
            backlog and its Lane is free again, :data:`PARKED` when H was full
            and the Lane is retained, or :data:`TERMINAL` when the contribution
            is finalized here.
        """
        if not checkpoint_ok:
            self._finalize(contribution, reason=REASON_CHECKPOINT_FAILED)
            return TERMINAL
        if not changed:
            self._finalize(contribution, reason=REASON_UNCHANGED_BRANCH)
            return TERMINAL
        if len(self._admitted) < INTEGRATION_HIGH_WATER:
            self._admitted.append(contribution)
            self._release_lane(contribution)
            return ADMITTED
        self._parked.append((self._turn, contribution))
        self._parked.sort(key=lambda entry: (entry[0], _ref_sort_key(entry[1].ref)))
        return PARKED

    def finalize(
        self,
        contribution: Contribution,
        *,
        published: bool,
        reason: str | None = None,
    ) -> tuple[Contribution, ...]:
        """Close an admitted contribution and drain what its H slot released.

        The one place a **Lane contribution** reaches a terminal disposition
        after **Integration** (#219 §4.12-4.14). ``published`` is true only
        once green publication *and* verified runner-driven closure have both
        completed — a publication that is still pending closure is explicitly
        not a contribution end (§4.13), so the Integrator must not call this
        until closure verifies.

        Freeing the H slot is what admits the next contribution, so the FIFO
        ordering rules live here rather than in any caller: the admitted
        backlog drains in admission order, and contributions that parked in the
        same scheduler turn tie-break by ascending issue number (§4.3-4.4).

        Args:
            published: Whether this is Parallel progress. Resets the shared
                consecutive-Strike count and cancels a pending abort drain
                (§7.4, §7.7).
            reason: The terminal disposition for an unpublished contribution.
                Defaults to :data:`REASON_SERIAL_FALLBACK`, the disposition
                §4.14 gives recovery exhaustion — the only way an *admitted*
                contribution can end unpublished.

        Returns:
            The contributions newly admitted from the parked FIFO.
        """
        if contribution in self._admitted:
            self._admitted.remove(contribution)
        terminal = REASON_PUBLISHED if published else (reason or REASON_SERIAL_FALLBACK)
        self._finalize(contribution, reason=terminal)
        if published:
            # §7.7: a green publication during an abort drain cancels it.
            self._abort_latched = False
        elif terminal == REASON_SERIAL_FALLBACK:
            # §5.2: a K<=3 Integration fallback requests serial service
            # immediately, from already-validated Run-ledger state.
            self.request_serial(ref=contribution.ref, reason=REASON_SERIAL_FALLBACK)
        self._turn += 1
        return self._drain_parked()

    def _drain_parked(self) -> tuple[Contribution, ...]:
        """Admit parked contributions in FIFO order while H has capacity."""
        newly: list[Contribution] = []
        while self._parked and len(self._admitted) < INTEGRATION_HIGH_WATER:
            _turn, contribution = self._parked.pop(0)
            self._admitted.append(contribution)
            self._release_lane(contribution)
            newly.append(contribution)
        return tuple(newly)

    def request_serial(self, *, ref: int | str | None, reason: str) -> None:
        """Latch validated serial demand (#219 §5.1-5.3).

        Once latched, no new reservation or refill happens — but nothing in
        flight is cancelled (§5.4). Existing reservations complete setup and
        either open a contribution or release; the pipeline then drains to full
        quiescence before serial ownership is granted.
        """
        self._serial_latched = True
        self._serial_requests.append((ref, reason))

    @property
    def serial_latched(self) -> bool:
        """Whether validated serial demand has stopped refill (#219 §5.3)."""
        return self._serial_latched

    def strike_limit_reached(self) -> None:
        """Latch the drain-confirmed abort (#219 §7.7).

        Stops new reservations and refill, but cancels nothing: every started
        contribution and **Integration** operation finishes, and a later green
        publication that resets **Strike** cancels the pending abort outright.
        The Run exits stuck only at full quiescence with the limit still
        reached, which is why this is a latch rather than an immediate exit.
        """
        self._abort_latched = True

    @property
    def abort_latched(self) -> bool:
        """Whether a drain-confirmed abort is pending (#219 §7.7)."""
        return self._abort_latched

    def confirm_empty(self) -> bool:
        """Ask, authoritatively, whether the **Pool** is exhausted (#219 §7.17).

        Only meaningful at full quiescence, and deliberately delegated to
        :meth:`~git_loopy.rolling_pool.RollingPool.confirm_empty` rather than
        re-derived here: an incomplete snapshot, a failed refresh, or an
        unresolved candidate may never establish emptiness, and that rule
        belongs with the cache that knows which of those happened.
        """
        return self.pool.confirm_empty()

    def _finalize(self, contribution: Contribution, *, reason: str) -> None:
        """Close a contribution exactly once and record its Strike reaction."""
        contribution.published = reason == REASON_PUBLISHED
        contribution.reason = reason
        contribution.strike_reaction = (
            STRIKE_RESET if contribution.published else STRIKE_ADD
        )
        self._open.pop(contribution.contribution_id, None)
        self._release_lane(contribution)
        self._finalized.append(contribution)

    def _release_lane(self, contribution: Contribution) -> None:
        for lane_id, holder in list(self._lanes_held.items()):
            if holder is contribution:
                del self._lanes_held[lane_id]

    def _free_lane(self) -> str:
        """The lowest-numbered unheld Lane slot, so assignment is deterministic."""
        for n in range(1, self.lane_cap + 1):
            lane_id = f"L{n}"
            if lane_id not in self._lanes_held:
                return lane_id
        raise RuntimeError("no free Lane slot")  # pragma: no cover - guarded by caller
