"""``git_loopy.calibration_search`` — the **Calibration** search core (#365, ADR-0027).

ADR-0027's objective is *the cheapest pair that clears the AGENTS.md gate*, and
this module is that sentence made mechanical: walk the **price staircase**
(:mod:`git_loopy.staircase`) from its cheapest rung and stop at the first that
goes five of five.

Design notes:

* **Pure over its injected inputs.** The search performs no I/O, spawns no
  session and touches no worktree. Everything that costs money sits behind the
  :class:`TrialRunner` seam, modelled directly on
  :class:`~git_loopy.gate.GateRunner`. That is what makes every rule below
  pinnable without spending an **AI Credit**.
* **The gate is the oracle and cost is the discriminator.** A **Trial** is scored
  on exactly the three keys ADR-0027 orders it by — cleared the gate, credits,
  wall clock — and there is no fourth scoring key for a judge, an
  acceptance-criteria score or a weighted composite to occupy. The weights would
  be chosen by the same judgment the measurement replaces. What a Trial
  *records* is wider than what it is scored on, and deliberately so:
  :class:`~git_loopy.trial_concurrency.TrialResult` carries the failure detail
  and :class:`~git_loopy.trial.ReplayTrialResult` the loops that ran. Nothing
  branches on either, which is the whole distinction — a fourth *field* is
  provenance a reader can check the conclusion against, where a fourth *scoring
  key* would change which pair wins.
* **A stopped search looks stopped.** :class:`SearchResult` refuses a winner on
  every stop but :attr:`~SearchStop.WINNER`, enforced at construction rather than
  promised — so the incumbent is kept by there being nothing to publish, not by a
  caller remembering not to publish it.
* **No prior enters.** ``RECOMMENDED_ROUTING`` is not imported, read or
  consulted. A hardcoded human guess as a starting position is the inferential
  prior returning as an initial condition, and the walk starts wherever
  :mod:`git_loopy.staircase` says the cheapest rung is.
* **One **Task type** per call.** The search holds no state between calls and
  knows nothing of the other Task types, so its ceilings cannot leak from one
  into the next. The Calibration-wide loop over the taxonomy belongs to the
  ``calibrate`` subcommand (#372).
* **Concurrency is a dispatcher, not a second walk** (#381). How many **Trials**
  run at once is :mod:`git_loopy.trial_concurrency`'s; *which* Trials are bought
  and in what order stays here, and is identical at every width. A serial search
  is ``InlineTrialDispatcher(1)``, which is the same code path — so *"identical
  to a serial run"* holds by there being nothing else to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterator, Sequence

from git_loopy.measured_routing import ProvingTask
from git_loopy.staircase import Candidate
from git_loopy.trial_concurrency import (
    InlineTrialDispatcher,
    TrialDispatcher,
    TrialInterrupt,
    TrialRequest,
    TrialResult,
    TrialRunner,
)

__all__ = [
    "DEFAULT_SEARCH_BUDGET",
    "PROMOTION_TRIALS",
    "TrialRequest",
    "TrialResult",
    "TrialRunner",
    "SearchBudget",
    "WalkedRung",
    "SearchStop",
    "SearchResult",
    "maximum_trials",
    "search_price_staircase",
    "tasks_for_every_rung",
]

#: **Five Proving tasks, unanimously** (ADR-0027). Not a rate estimate and never
#: to be described as one: separating a 70%-reliable pair from a 90%-reliable one
#: needs dozens of tasks per rung, which is the arithmetic ADR-0027 already
#: rejected. Unanimity is chosen because the two errors are asymmetric — a false
#: pass merges bad work, a false fail merely overpays — so a strict bar biases
#: toward the cheap error.
PROMOTION_TRIALS = 5


@dataclass(frozen=True)
class SearchBudget:
    """The two ceilings that bound a search.

    Both are needed, because the credit ceiling alone points the wrong way
    (ADR-0027): cheapest-first spends its early rungs on the cheapest pairs while
    the gate costs the same wall clock on every rung regardless of the model
    under test — it is compilers and test suites, not tokens — so credits trip
    *last* in exactly the pathological case the ceiling exists for.
    """

    credit_ceiling: Decimal
    wall_clock_ceiling_seconds: float


#: The ceilings a **Calibration** takes when nothing else says otherwise, **per
#: Task type** — one search, one staircase, one budget.
#:
#: These are **stated bounds, not measurements**, and this module will not
#: pretend otherwise: no Calibration has run here yet, so there is no observed
#: cost per **Trial** to derive them from. They are chosen from ADR-0027's own
#: arithmetic — at most eight rungs times :data:`PROMOTION_TRIALS` is forty
#: Trials for one Task type, each an agent session followed by a full five-loop
#: gate — and set where an unattended search stays inside a working session
#: rather than running overnight unsupervised.
#:
#: Hitting either is a **first-class outcome**, not a failure: the search keeps
#: the incumbent, records ``incomplete`` with the rung it reached, and names
#: which ceiling stopped it. That is why a conservative default is the safe one
#: to ship — it costs a re-run with a raised ceiling, where a generous one costs
#: credits nobody authorised.
#:
#: ``git-loopy calibrate --dry-run`` prints both, before anything is spent,
#: precisely because they are assertions an operator should get to overrule.
DEFAULT_SEARCH_BUDGET = SearchBudget(
    credit_ceiling=Decimal("100"),
    wall_clock_ceiling_seconds=4 * 60 * 60,
)


def maximum_trials(*, rungs_available: int) -> int:
    """The most **Trials** a search over ``rungs_available`` rungs can run.

    The worst case and never an estimate: a search that promotes nothing walks
    every rung, and a rung that goes five of five runs
    :data:`PROMOTION_TRIALS` of them. A rung abandoned on an early failure runs
    fewer, so the real count is bounded by this and unknowable before the walk —
    which is the whole reason ``--dry-run`` reports a maximum rather than a
    forecast.
    """
    return rungs_available * PROMOTION_TRIALS


@dataclass(frozen=True)
class WalkedRung:
    """One rung the search walked, and how it scored.

    ``total`` is the number of Trials actually *run*, not
    :data:`PROMOTION_TRIALS`: a rung abandoned on its first failure records the
    two it ran rather than claiming five it did not.

    ``wall_clock_seconds`` is the rung's **elapsed** time and not the sum of its
    Trials': under concurrency those are different numbers, and only the first is
    what a Calibration is bounded by. Trials dispatched together contribute the
    longest of them, so a serial rung — where every dispatch is one Trial — still
    reports the sum it always did.
    """

    candidate: Candidate
    passed: int
    total: int
    credits: Decimal | None
    wall_clock_seconds: float


class SearchStop(Enum):
    """Why the walk ended. Only :attr:`WINNER` publishes a pair."""

    #: A rung went five of five.
    WINNER = "winner"
    #: The search reached its wall-clock ceiling.
    WALL_CLOCK_CEILING = "wall_clock_ceiling"
    #: The search reached its **AI Credit** ceiling.
    CREDIT_CEILING = "credit_ceiling"
    #: Every rung was walked and none was unanimous. The search finished; it
    #: simply found nothing, which is a different fact from running out of budget.
    STAIRCASE_EXHAUSTED = "staircase_exhausted"
    #: The operator interrupted the walk. Whatever was measured is kept.
    INTERRUPTED = "interrupted"
    #: The **Task type**'s Proving set holds fewer than :data:`PROMOTION_TRIALS`
    #: tasks, so no rung could go five of five. Refused before the first Trial:
    #: measuring three of three and promoting on it would read the unanimity rule
    #: as *"everything available passed"*, which is a bar ADR-0027 never set.
    INSUFFICIENT_PROVING_SET = "insufficient_proving_set"


@dataclass(frozen=True)
class SearchResult:
    """What the search found, and where it stopped.

    The record's one invariant is the whole of *"a stopped search must look
    stopped"*: a winner is carried by a :attr:`~SearchStop.WINNER` result and by
    nothing else. Enforced here rather than promised in a docstring, so no caller
    can assemble a result that publishes a pair the search did not promote.

    :attr:`wall_clock_seconds` is **elapsed** time, folded from the rungs, and
    :attr:`concurrency` is the width it was measured at — both, because a rung's
    wall clock is only comparable to another rung's measured at the same width,
    and neither number means anything to a reader without the other.
    """

    stop: SearchStop
    rungs: tuple[WalkedRung, ...]
    rungs_available: int
    proving_tasks: tuple[ProvingTask, ...]
    credits: Decimal | None = Decimal(0)
    wall_clock_seconds: float = 0.0
    winner: Candidate | None = None
    concurrency: int = 1

    def __post_init__(self) -> None:
        if self.stop is SearchStop.WINNER and self.winner is None:
            raise ValueError("a 'winner' result carries the pair it promoted")
        if self.stop is not SearchStop.WINNER and self.winner is not None:
            raise ValueError(
                f"a {self.stop.value!r} search publishes no winner; "
                f"{self.winner.model} @ {self.winner.effort} was not promoted"
            )

    @property
    def stopped_at_rung(self) -> int:
        """How far up the staircase the walk got — rung *N* of :attr:`rungs_available`.

        The count of rungs that had at least one Trial run against them, which is
        the 1-based position of the last rung walked. Zero when the search was
        refused before its first Trial.
        """
        return len(self.rungs)


def search_price_staircase(
    *,
    candidates: Sequence[Candidate],
    proving_set: Sequence[ProvingTask],
    budget: SearchBudget,
    runner: TrialRunner,
    dispatcher: TrialDispatcher | None = None,
) -> SearchResult:
    """Walk ``candidates`` cheapest-first and stop at the first unanimous rung.

    Args:
        candidates: The **price staircase**, already ordered cheapest-first by
            :mod:`git_loopy.staircase`. The order is taken as given: this
            function never reorders it and never seeds itself from a prior.
        proving_set: One **Task type**'s Proving tasks. The same
            :data:`PROMOTION_TRIALS` of them are measured at *every* rung, so the
            rungs are comparable and two runs of the same search measure the same
            work.
        budget: The two ceilings that bound the walk.
        runner: The injected :class:`TrialRunner`. Every credit this search
            spends is spent behind it.
        dispatcher: How many **Trials** run at once, and what keeps them apart
            (#381). Defaults to a serial dispatcher, so a caller that says
            nothing gets the walk this search has always performed.

    Returns:
        The :class:`SearchResult`, which carries a winner only when a rung went
        five of five.
    """
    dispatcher = dispatcher if dispatcher is not None else InlineTrialDispatcher(1)
    width = dispatcher.width
    tasks = tasks_for_every_rung(proving_set)
    walked: list[_RungTally] = []
    available = len(candidates)
    in_progress: _RungTally | None = None

    def stopped(stop: SearchStop) -> SearchResult:
        """The record as it stands, including any rung the walk was part-way up."""
        rungs = walked + ([in_progress] if in_progress and in_progress.total else [])
        spend = _Spend.over(rungs)
        return SearchResult(
            stop=stop,
            rungs=tuple(rung.freeze() for rung in rungs),
            rungs_available=available,
            proving_tasks=tasks,
            credits=spend.credits,
            wall_clock_seconds=spend.seconds,
            concurrency=width,
        )

    if len(tasks) < PROMOTION_TRIALS:
        return stopped(SearchStop.INSUFFICIENT_PROVING_SET)

    try:
        for candidate in candidates:
            in_progress = _RungTally(candidate)
            for group in _dispatch_groups(tasks, width):
                ceiling = _Spend.over(walked + [in_progress]).exceeded(budget)
                if ceiling is not None:
                    return stopped(ceiling)
                results = dispatcher.dispatch(
                    runner,
                    tuple(
                        TrialRequest(candidate=candidate, task=task, slot=slot)
                        for slot, task in enumerate(group)
                    ),
                )
                # The single place a Trial is written down, and the first thing
                # done with it. Two accumulators would leave a window in which an
                # interrupt lands between them and the record disagrees with
                # itself about what was spent; the search's totals are folded
                # from the rungs instead (:meth:`_Spend.over`).
                in_progress.record(results)
                if any(not result.passed for result in results):
                    break
            rung, in_progress = in_progress, None
            if rung.total:
                walked.append(rung)
            if rung.unanimous:
                # A ceiling stops the search from spending *more*; it does not
                # retract evidence already paid for. So a rung whose fifth Trial
                # landed on a ceiling is still the winner, and the ceiling is
                # simply never reached — there is nothing left to buy.
                return SearchResult(
                    stop=SearchStop.WINNER,
                    rungs=tuple(walked_rung.freeze() for walked_rung in walked),
                    rungs_available=available,
                    proving_tasks=tasks,
                    credits=_Spend.over(walked).credits,
                    wall_clock_seconds=_Spend.over(walked).seconds,
                    winner=candidate,
                    concurrency=width,
                )
    except KeyboardInterrupt as interrupted:
        # Every Trial already measured is kept, including the part-walked rung: a
        # Calibration is hours long, and all-or-nothing would make an operator
        # who interrupts one pay for it twice. Under concurrency the Trials that
        # had already returned when the operator pressed Ctrl-C arrive on the
        # interrupt itself, which is the only way they reach the record at all.
        if isinstance(interrupted, TrialInterrupt) and in_progress is not None:
            in_progress.record(interrupted.measured)
        return stopped(SearchStop.INTERRUPTED)
    # Running out of rungs and out of budget in the same breath is reported as
    # running out of rungs. Both are true and only one is useful: a ceiling would
    # tell an operator to raise a limit that has nothing left to buy.
    return stopped(SearchStop.STAIRCASE_EXHAUSTED)


def _dispatch_groups(
    tasks: Sequence[ProvingTask], width: int
) -> Iterator[tuple[ProvingTask, ...]]:
    """The **Trials** of one rung, grouped into what is bought at a time.

    **The first Trial is a probe, run alone.** Promotion is unanimous, so one
    failure kills a rung — which makes a single Trial the cheapest possible
    evidence that a rung is dead, and cheapest-first means most rungs are. Buying
    all five at once would multiply the cost of every rung the search *expects*
    to fail by the operator's width, turning a wall-clock saving into a credit
    bill nobody asked for. Probing first keeps a rung that dies at its first
    Trial costing exactly what it costs serially, while a rung that lives still
    collapses from five Trial-times to two.

    Everything after the probe is dispatched ``width`` at a time. At ``width``
    ``1`` that degenerates to one Trial per dispatch, in order — the serial walk,
    reached by the same code rather than by a second one.
    """
    if width <= 1:
        for task in tasks:
            yield (task,)
        return
    yield (tasks[0],)
    remainder = tasks[1:]
    for start in range(0, len(remainder), width):
        yield tuple(remainder[start : start + width])


class _RungTally:
    """One rung's running tally, mutable while it is being walked."""

    def __init__(self, candidate: Candidate) -> None:
        self.candidate = candidate
        self.passed = 0
        self.total = 0
        self.spend = _Spend()

    def record(self, results: Sequence[TrialResult]) -> None:
        """Fold one dispatch's Trials into the rung.

        A whole dispatch at a time rather than a Trial at a time, because the
        elapsed time of Trials that ran together is not the sum of them
        (:meth:`_Spend.add`).
        """
        if not results:
            return
        self.total += len(results)
        self.spend.add(results)
        self.passed += sum(1 for result in results if result.passed)

    @property
    def unanimous(self) -> bool:
        """Whether this rung went five of five, which is the only promotion."""
        return self.passed == self.total == PROMOTION_TRIALS

    def freeze(self) -> WalkedRung:
        return WalkedRung(
            candidate=self.candidate,
            passed=self.passed,
            total=self.total,
            credits=self.spend.credits,
            wall_clock_seconds=self.spend.seconds,
        )


def tasks_for_every_rung(proving_set: Sequence[ProvingTask]) -> tuple[ProvingTask, ...]:
    """The :data:`PROMOTION_TRIALS` tasks *every* rung of this search measures.

    Public because ``git-loopy calibrate --dry-run`` (#367) promises an operator
    the **Proving tasks** a search *would* measure against, and a second
    selection written for the report could name different work than the search
    then measures — which is the one thing a dry run must not do.

    Drawn once, before the walk, and deterministically — highest issue number
    first. Determinism is what makes two runs of the same search measure the same
    work; drawing them **once** is what makes the rungs comparable, since a cheap
    rung handed easier tasks than the rung above it would not be a measurement at
    all. Newest-first because the **Proving set** already measures the project you
    *were*, and the most recent closed work is the least stale sample of it.
    """
    newest_first = sorted(proving_set, key=lambda task: task.issue, reverse=True)
    return tuple(newest_first[:PROMOTION_TRIALS])


class _Spend:
    """What a set of Trials consumed, in credits and wall clock.

    Credits are carried **twice**, because one number cannot do both jobs.
    :attr:`credits` is what the search *reports* and it latches to unknown: a
    Trial the harness never billed makes the total a figure nobody can stand
    behind, and ADR-0026's rule is that an unknown cost is unavailable and never
    zero. :attr:`known_credits` is what the search *enforces* its ceiling on — a
    lower bound over the Trials that were billed, so one unreported Trial early
    in a walk cannot buy unlimited spend after it. Reporting the lower bound
    would be the guess ADR-0026 forbids; enforcing on the latched total would be
    the overspend the latch exists to prevent.

    Wall clock never latches — it is measured by a clock rather than reported by
    a harness — which is why the wall-clock ceiling still bounds a search whose
    credits are entirely unknown, and one more reason ADR-0027 needed both.
    """

    def __init__(self) -> None:
        self.credits: Decimal | None = Decimal(0)
        self.known_credits: Decimal = Decimal(0)
        self.seconds: float = 0.0

    @classmethod
    def over(cls, rungs: Sequence[_RungTally]) -> _Spend:
        """The walk's totals, folded from the rungs rather than tracked beside them."""
        total = cls()
        for rung in rungs:
            if total.credits is None or rung.spend.credits is None:
                total.credits = None
            else:
                total.credits += rung.spend.credits
            total.known_credits += rung.spend.known_credits
            total.seconds += rung.spend.seconds
        return total

    def add(self, results: Sequence[TrialResult]) -> None:
        """Fold one dispatch's Trials in: credits sum, wall clock does not.

        Credits are additive because concurrency discounts nothing — five Trials
        run at once cost what five Trials cost. Wall clock is not: Trials
        dispatched together overlap, so the dispatch takes the longest of them
        and summing would report a Calibration as five times slower than the
        operator watched it be. Summing would also trip the wall-clock ceiling at
        a fifth of the time an operator asked for, which is the one ceiling
        concurrency exists to fit inside.

        A dispatch of one — every dispatch of a serial search — has a maximum
        equal to its sum, so nothing about a serial walk changes.
        """
        if not results:
            return
        for result in results:
            if result.credits is None:
                self.credits = None
            else:
                self.known_credits += result.credits
                if self.credits is not None:
                    self.credits += result.credits
        self.seconds += max(result.wall_clock_seconds for result in results)

    def exceeded(self, budget: SearchBudget) -> SearchStop | None:
        """Which ceiling this spend has reached, or ``None`` while it is affordable.

        Wall clock is tested first, and a search that has reached both ceilings
        reports that one. Not a coin toss: cheapest-first defers spend while the
        gate costs the same wall clock on every rung, so credits trip *last*
        (ADR-0027) — if both have tripped, wall clock tripped first and is the
        true cause.
        """
        if self.seconds >= budget.wall_clock_ceiling_seconds:
            return SearchStop.WALL_CLOCK_CEILING
        if self.known_credits >= budget.credit_ceiling:
            return SearchStop.CREDIT_CEILING
        return None
