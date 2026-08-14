"""``git_loopy.trial_concurrency`` — **Trials** run concurrently, in isolation (#381, ADR-0027).

ADR-0027's amendment says Trials run *"in parallel across worktrees reusing
ADR-0008's machinery, bounded by an operator concurrency setting"*, and this
module is the bound and the isolation. The **Calibration** search
(:mod:`git_loopy.calibration_search`) decides *which* Trials to buy; everything
here decides *how many run at once* and *what keeps them apart*.

Why it exists at all: wall clock, not spend, is what limits a Calibration. Five
Trials per rung across three to eight rungs and seven **Task types** is 105-280
agent sessions, each followed by a full five-loop AGENTS.md gate — and the gate
costs the same wall clock on every rung regardless of the model under test,
because it is compilers and test suites rather than tokens. A serial Calibration
is a multi-day job and the credit ceiling never fires in time to help.

Design notes:

* **The slot is the isolation, and the search hands it out.** A
  :class:`TrialRequest` carries a ``slot``, and the dispatcher guarantees no two
  Trials in flight ever hold the same one. A runner that named its own worktree
  would be asserting uniqueness rather than being given it, and *"concurrent
  Trials share no worktree and no working branch"* would be a promise instead of
  a mechanism. The production runner (#369) keys its worktree and its working
  branch off the slot.
* **Width 1 is not a second code path.** ``InlineTrialDispatcher(1)`` runs one
  Trial at a time in request order, which is exactly the walk the search
  performed before this module existed — so *"gate outcome and Consumption for a
  given Trial are identical to a serial run"* holds because there is nothing for
  a parallel run to do differently, not because two paths were compared.
* **The threads are here and nowhere else.** :class:`InlineTrialDispatcher` is
  the deterministic default and what every fixture drives, so the search core
  stays pure and offline-testable; :class:`ThreadedTrialDispatcher` is the only
  thing in the family that starts a thread for a Trial. Threads rather than
  processes because a Trial is an agent session and a gate run — subprocesses
  and sockets, with the GIL released throughout.
* **A sibling's failure is that Trial's own result.** An exception inside one
  Trial becomes a red :class:`TrialResult` carrying the failure detail, never a
  raise that takes down the Trials beside it. A Trial that could not be measured
  did not clear the gate, and its **Consumption** is *unknown* rather than zero
  (ADR-0026) — a crashed session may well have been billed.
* **An interrupt keeps what was already paid for.** :class:`TrialInterrupt`
  carries the Trials that had completed when the operator pressed Ctrl-C, so the
  search records them instead of discarding hours of measurement. Trials still
  in flight at that moment are not measured; tearing their worktrees down is the
  runner's own ``finally`` (#369), not this module's.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Protocol, Sequence, runtime_checkable

from git_loopy.measured_routing import ProvingTask
from git_loopy.staircase import Candidate

__all__ = [
    "CONCURRENCY_ENV",
    "LANE_CAP_ENV",
    "TrialResult",
    "TrialRequest",
    "TrialRunner",
    "TrialDispatcher",
    "TrialInterrupt",
    "InlineTrialDispatcher",
    "ThreadedTrialDispatcher",
    "TrialConcurrency",
    "resolve_trial_concurrency",
]

#: The **Calibration**'s own concurrency knob. Separate from the **Lane cap**
#: because a Trial is heavier than a Lane — it runs the whole AGENTS.md gate
#: rather than one contribution's — so a host that sustains six Lanes may
#: sustain fewer Trials.
CONCURRENCY_ENV = "GIT_LOOPY_CALIBRATE_CONCURRENCY"

#: The **Lane cap** this falls back to. Calibration is a Parallel-mode feature
#: (#379), so an operator who reached it has already stated what this host can
#: take; asking a second time by default would be asking twice.
LANE_CAP_ENV = "GIT_LOOPY_MAX_PARALLEL"


@dataclass(frozen=True)
class TrialResult:
    """What one **Trial** returns: the three scoring keys, plus why it went red.

    Attributes:
        passed: The gate outcome — the *only* thing that decides whether the pair
            solved the **Proving task**.
        credits: The **AI Credits** the Trial consumed, read off its
            **Consumption**, or ``None`` when the harness reported none.
            ADR-0026's rule holds: unknown is unknown, never zero. Only the
            credits cross this seam, because credits are the whole of what the
            search scores on and the whole of what the artifact records
            (:class:`~git_loopy.measured_routing.Rung`). The tally itself —
            tokens, premium requests, the cache split — stays with the Trial
            runner that owns the session (#369), which is also where it is kept
            out of the **Run**'s accounting (#371).
        wall_clock_seconds: End-to-end wall clock, measured by the Trial itself.
            Under concurrency it includes whatever contention the Trial suffered,
            which is why it is only the *third* key the search scores on.
        failure: The failure detail on a red Trial, so a reader can check the
            conclusion rather than take it on faith. Nothing branches on it: it
            is detail, not a fourth scoring key.
    """

    passed: bool
    credits: Decimal | None
    wall_clock_seconds: float
    failure: str | None = None


@dataclass(frozen=True)
class TrialRequest:
    """One **Trial** to run: a pair, a **Proving task**, and the slot it owns.

    Attributes:
        candidate: The rung under test.
        task: The Proving task this Trial replays.
        slot: The isolation slot. Distinct for every Trial *in flight*, and
            reused only once the Trial holding it has returned — which is what
            makes *"concurrent Trials share no worktree and no working branch"* a
            property of the dispatcher rather than an obligation on the runner.
            Always in ``0 .. width - 1``, so a host prepared for ``width``
            worktrees never has to hold a ``width + 1``-th.
    """

    candidate: Candidate
    task: ProvingTask
    slot: int

    def __post_init__(self) -> None:
        if self.slot < 0:
            raise ValueError(f"slot must be ≥ 0, got {self.slot}")


@runtime_checkable
class TrialRunner(Protocol):
    """Run one **Trial** and report the result.

    The single injected seam the **Calibration** search introduces, modelled on
    :class:`~git_loopy.gate.GateRunner`: one method, a value in, a value out, and
    ``@runtime_checkable`` so production and scripted fakes satisfy it
    structurally. Everything expensive — the worktree, the agent session, the
    gate — lives behind it (#369).

    A production runner is called from several threads at once, so it must hold
    no per-Trial state of its own: everything that distinguishes one concurrent
    Trial from another is on the :class:`TrialRequest` it is handed.
    """

    def run(self, request: TrialRequest) -> TrialResult:
        """Trial ``request`` and return its :class:`TrialResult`."""
        ...


class TrialInterrupt(KeyboardInterrupt):
    """Ctrl-C during a dispatch, carrying the Trials that had already completed.

    A :class:`KeyboardInterrupt` so no caller has to learn a new exception to
    stop, and a subclass so the search can keep what was measured. A Calibration
    is hours long, and discarding a completed rung because the operator
    interrupted the one above it would make them pay for it twice.
    """

    def __init__(self, measured: Sequence[TrialResult] = ()) -> None:
        super().__init__()
        self.measured: tuple[TrialResult, ...] = tuple(measured)


@runtime_checkable
class TrialDispatcher(Protocol):
    """Run a bounded set of **Trials** together and answer in request order.

    ``width`` is the operator's bound: the search never hands
    :meth:`dispatch` more than that many requests, so a dispatcher never has to
    queue and a slot is never contended.
    """

    width: int

    def dispatch(
        self, runner: TrialRunner, requests: Sequence[TrialRequest]
    ) -> tuple[TrialResult, ...]:
        """Run every request and return the results **in request order**.

        Request order rather than completion order, so the record a search
        publishes does not depend on which Trial happened to finish first.
        """
        ...


def _isolated(runner: TrialRunner, request: TrialRequest) -> TrialResult:
    """Run one Trial, turning any failure of its own into that Trial's result.

    :class:`KeyboardInterrupt` and every other :class:`BaseException` pass
    through: an operator stopping the search is not a Trial going red, and a
    dispatcher that swallowed it would make the Calibration unstoppable.
    """
    try:
        return runner.run(request)
    except Exception as exc:  # noqa: BLE001 — a sibling's fault is not this Trial's
        return TrialResult(
            passed=False,
            credits=None,
            wall_clock_seconds=0.0,
            failure=f"the Trial could not be measured: {exc!r}",
        )


class InlineTrialDispatcher:
    """Run the requests one after another, in order, on the calling thread.

    The deterministic dispatcher: it carries a ``width`` so the search groups
    Trials exactly as a concurrent run would, and then runs them serially, so
    every rule about *what is bought* is pinnable offline without a thread, a
    clock or a scheduler. ``InlineTrialDispatcher(1)`` is a serial Calibration.
    """

    def __init__(self, width: int = 1) -> None:
        if width < 1:
            raise ValueError(f"width must be ≥ 1 (1 = serial), got {width}")
        self.width = width

    def dispatch(
        self, runner: TrialRunner, requests: Sequence[TrialRequest]
    ) -> tuple[TrialResult, ...]:
        results: list[TrialResult] = []
        for request in requests:
            try:
                results.append(_isolated(runner, request))
            except KeyboardInterrupt as exc:
                raise TrialInterrupt(results) from exc
        return tuple(results)


class ThreadedTrialDispatcher:
    """Run the requests concurrently, one thread each, bounded by ``width``.

    Threads rather than processes: a Trial is an agent session followed by a
    five-loop gate, so it is subprocesses and sockets from end to end and the GIL
    is released for effectively all of it. Processes would buy nothing and cost
    the pickling of every seam the search injects.
    """

    def __init__(self, width: int) -> None:
        if width < 1:
            raise ValueError(f"width must be ≥ 1 (1 = serial), got {width}")
        self.width = width

    def dispatch(
        self, runner: TrialRunner, requests: Sequence[TrialRequest]
    ) -> tuple[TrialResult, ...]:
        if not requests:
            return ()
        pool = ThreadPoolExecutor(max_workers=self.width)
        try:
            futures = [
                pool.submit(_isolated, runner, request) for request in requests
            ]
            try:
                return tuple(future.result() for future in futures)
            except KeyboardInterrupt as exc:
                raise TrialInterrupt(_completed(futures)) from exc
        finally:
            # Deliberately not the context manager, which shuts down with
            # ``wait=True``: an interrupt that then blocked until every gate run
            # in flight had finished would be indistinguishable from not having
            # pressed Ctrl-C at all.
            pool.shutdown(wait=False, cancel_futures=True)


def _completed(futures: Sequence[Future[TrialResult]]) -> tuple[TrialResult, ...]:
    """The results of the Trials that had finished, in request order.

    A Trial still in flight is *not* waited for. Waiting would hold an operator's
    Ctrl-C for however long a gate run takes, which is the opposite of stopping;
    the worktree it holds is torn down by the runner's own ``finally`` (#369).
    """
    done: list[TrialResult] = []
    for future in futures:
        if future.done() and not future.cancelled() and future.exception() is None:
            done.append(future.result())
    return tuple(done)


@dataclass(frozen=True)
class TrialConcurrency:
    """The operator's concurrency setting, and what it actually buys.

    Attributes:
        requested: What the operator asked for.
        ceiling: The most concurrency the search can use, passed in rather than
            imported so this stays a pure value: a rung buys exactly
            ``PROMOTION_TRIALS`` Trials, and within-rung parallelism cannot spend
            a wider host than that.
        source: Which setting supplied :attr:`requested`, so a report can name it
            rather than leaving an operator to guess which knob won.
    """

    requested: int
    ceiling: int
    source: str

    @property
    def effective(self) -> int:
        """The width the search will actually run at."""
        return min(self.requested, self.ceiling)

    @property
    def serial(self) -> bool:
        """Whether this setting runs one Trial at a time."""
        return self.effective == 1

    @property
    def capped(self) -> bool:
        """Whether the request was wider than a rung can use.

        Worth reporting rather than silently honouring: an operator who set 12
        and gets 5 should be told the other seven bought nothing, because the
        answer changes once a Calibration runs several **Task types** at once
        (#372) and this is the fact that will change with it.
        """
        return self.requested > self.ceiling


def resolve_trial_concurrency(
    *, env: Mapping[str, str], ceiling: int
) -> TrialConcurrency:
    """Resolve the concurrency a **Calibration** runs its **Trials** at.

    Precedence, matching the kit's per-run-knob convention — never a persisted
    ``config.toml``, because how much a *host* can take is not a property of the
    repository:

    1. :data:`CONCURRENCY_ENV`.
    2. :data:`LANE_CAP_ENV`, the **Lane cap** an operator already set to reach
       Parallel mode at all (#379).
    3. ``1`` — serial.

    A malformed or sub-1 value at either tier **degrades to the next**, exactly
    as :func:`git_loopy.cli._resolve_parallel` degrades to serial: a stray env
    value should cost an operator concurrency, never the Calibration they asked
    for.
    """
    if ceiling < 1:
        raise ValueError(f"ceiling must be ≥ 1, got {ceiling}")
    for name in (CONCURRENCY_ENV, LANE_CAP_ENV):
        value = _positive_int(env.get(name))
        if value is not None:
            return TrialConcurrency(requested=value, ceiling=ceiling, source=name)
    return TrialConcurrency(requested=1, ceiling=ceiling, source="default")


def _positive_int(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None
