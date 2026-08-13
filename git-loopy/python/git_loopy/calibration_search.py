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
* **The gate is the oracle and cost is the discriminator.** A **Trial** carries
  exactly the three keys ADR-0027 scores it on — cleared the gate, credits, wall
  clock — and there is no fourth field for a judge, an acceptance-criteria score
  or a weighted composite to occupy. The weights would be chosen by the same
  judgment the measurement replaces.
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
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

from git_loopy.measured_routing import ProvingTask
from git_loopy.staircase import Candidate

__all__ = [
    "PROMOTION_TRIALS",
    "TrialResult",
    "TrialRunner",
    "SearchBudget",
    "WalkedRung",
    "SearchStop",
    "SearchResult",
    "search_price_staircase",
]

#: **Five Proving tasks, unanimously** (ADR-0027). Not a rate estimate and never
#: to be described as one: separating a 70%-reliable pair from a 90%-reliable one
#: needs dozens of tasks per rung, which is the arithmetic ADR-0027 already
#: rejected. Unanimity is chosen because the two errors are asymmetric — a false
#: pass merges bad work, a false fail merely overpays — so a strict bar biases
#: toward the cheap error.
PROMOTION_TRIALS = 5


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
        wall_clock_seconds: End-to-end wall clock.
        failure: The failure detail on a red Trial, so a reader can check the
            conclusion rather than take it on faith. Nothing branches on it: it
            is detail, not a fourth scoring key.
    """

    passed: bool
    credits: Decimal | None
    wall_clock_seconds: float
    failure: str | None = None


@runtime_checkable
class TrialRunner(Protocol):
    """Run one candidate pair against one **Proving task**, and report the result.

    The single injected seam this search introduces, modelled on
    :class:`~git_loopy.gate.GateRunner`: one method, a value in, a value out, and
    ``@runtime_checkable`` so production and scripted fakes satisfy it
    structurally. Everything expensive — the worktree, the agent session, the
    gate — lives behind it (#369).
    """

    def run(self, candidate: Candidate, task: ProvingTask) -> TrialResult:
        """Trial ``candidate`` against ``task`` and return its :class:`TrialResult`."""
        ...


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


@dataclass(frozen=True)
class WalkedRung:
    """One rung the search walked, and how it scored.

    ``total`` is the number of Trials actually *run*, not
    :data:`PROMOTION_TRIALS`: a rung abandoned on its first failure records the
    two it ran rather than claiming five it did not.
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
    """

    stop: SearchStop
    rungs: tuple[WalkedRung, ...]
    rungs_available: int
    proving_tasks: tuple[ProvingTask, ...]
    credits: Decimal | None = Decimal(0)
    wall_clock_seconds: float = 0.0
    winner: Candidate | None = None

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

    Returns:
        The :class:`SearchResult`, which carries a winner only when a rung went
        five of five.
    """
    tasks = _tasks_for_every_rung(proving_set)
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
        )

    if len(tasks) < PROMOTION_TRIALS:
        return stopped(SearchStop.INSUFFICIENT_PROVING_SET)

    try:
        for candidate in candidates:
            in_progress = _RungTally(candidate)
            for task in tasks:
                ceiling = _Spend.over(walked + [in_progress]).exceeded(budget)
                if ceiling is not None:
                    return stopped(ceiling)
                result = runner.run(candidate, task)
                # The single place a Trial is written down, and the first thing
                # done with it. Two accumulators would leave a window in which an
                # interrupt lands between them and the record disagrees with
                # itself about what was spent; the search's totals are folded
                # from the rungs instead (:meth:`_Spend.over`).
                in_progress.record(result)
                if not result.passed:
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
                )
    except KeyboardInterrupt:
        # Every Trial already measured is kept, including the part-walked rung: a
        # Calibration is hours long, and all-or-nothing would make an operator
        # who interrupts one pay for it twice.
        return stopped(SearchStop.INTERRUPTED)
    # Running out of rungs and out of budget in the same breath is reported as
    # running out of rungs. Both are true and only one is useful: a ceiling would
    # tell an operator to raise a limit that has nothing left to buy.
    return stopped(SearchStop.STAIRCASE_EXHAUSTED)


class _RungTally:
    """One rung's running tally, mutable while it is being walked."""

    def __init__(self, candidate: Candidate) -> None:
        self.candidate = candidate
        self.passed = 0
        self.total = 0
        self.spend = _Spend()

    def record(self, result: TrialResult) -> None:
        self.total += 1
        self.spend.add(result)
        if result.passed:
            self.passed += 1

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


def _tasks_for_every_rung(proving_set: Sequence[ProvingTask]) -> tuple[ProvingTask, ...]:
    """The :data:`PROMOTION_TRIALS` tasks *every* rung of this search measures.

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

    def add(self, result: TrialResult) -> None:
        if result.credits is None:
            self.credits = None
        else:
            self.known_credits += result.credits
            if self.credits is not None:
                self.credits += result.credits
        self.seconds += result.wall_clock_seconds

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
