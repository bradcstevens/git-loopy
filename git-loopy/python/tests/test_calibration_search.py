"""``git_loopy.calibration_search`` tests — the search core (#365, ADR-0027).

The search is ADR-0027's objective made mechanical: *the cheapest pair that
clears the bar*, walked from the cheapest rung and stopped at the first that goes
five of five. So these tests ask only what that sentence commits to — where the
walk starts, when it stops, what it publishes when it does not finish, and that
nothing but the gate, the credits and the wall clock ever scores a **Trial**.

Every staircase here is **synthetic and declared in the test itself**, following
ADR-0019's correction to ``effort-gate.json``: a vendor catalogue change must not
be able to silently invalidate a behavioural test. Scripted **Trial** results are
the only fake these tests need — the search performs no I/O, spawns no session
and touches no worktree.
"""

from __future__ import annotations

import ast
import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import pytest

from git_loopy import calibration_search
from git_loopy.calibration_search import (
    DEFAULT_SEARCH_BUDGET,
    PROMOTION_TRIALS,
    SearchBudget,
    SearchResult,
    SearchStop,
    TrialRequest,
    TrialResult,
    TrialRunner,
    maximum_trials,
    search_price_staircase,
)
from git_loopy.measured_routing import ProvingTask
from git_loopy.staircase import Candidate
from git_loopy.trial_concurrency import InlineTrialDispatcher


class _ScriptedTrialRunner:
    """A **Trial runner** answering from a script keyed by candidate pair.

    The one fake the search needs. It records every call, so a test can assert on
    the Trials that were *not* run — which is how rung abandonment and the
    dominated rungs above the winner are pinned.
    """

    def __init__(self, script: dict[tuple[str, str | None], list[TrialResult]]) -> None:
        self._script = {key: list(value) for key, value in script.items()}
        self.calls: list[TrialRequest] = []

    def run(self, request: TrialRequest) -> TrialResult:
        self.calls.append(request)
        candidate = request.candidate
        return self._script[(candidate.model, candidate.effort)].pop(0)

    def trials_for(self, candidate: Candidate) -> int:
        """How many Trials this pair was actually sent."""
        return sum(1 for call in self.calls if call.candidate == candidate)


def _candidate(model: str, multiplier: float, effort: str | None = "medium") -> Candidate:
    return Candidate(model=model, effort=effort, multiplier=multiplier)


def _proving_set(count: int = PROMOTION_TRIALS) -> tuple[ProvingTask, ...]:
    """A Proving set of ``count`` tasks, numbered so their order is checkable."""
    return tuple(
        ProvingTask(
            issue=100 + index, base_commit=f"base{index}", oracle_commit=f"fix{index}"
        )
        for index in range(count)
    )


def _passed(credits: float = 1.0, seconds: float = 10.0) -> TrialResult:
    return TrialResult(
        passed=True, credits=Decimal(str(credits)), wall_clock_seconds=seconds
    )


def _failed(
    credits: float = 1.0, seconds: float = 10.0, failure: str = "gate red"
) -> TrialResult:
    return TrialResult(
        passed=False,
        credits=Decimal(str(credits)),
        wall_clock_seconds=seconds,
        failure=failure,
    )


def _budget(credits: float = 1_000.0, seconds: float = 100_000.0) -> SearchBudget:
    return SearchBudget(
        credit_ceiling=Decimal(str(credits)), wall_clock_ceiling_seconds=seconds
    )


def test_the_walk_stops_at_the_first_rung_that_goes_five_of_five() -> None:
    """Cheapest-first, and the first unanimous rung is the winner.

    The whole objective in one case: the cheapest rung fails, the next goes five
    of five and wins, and the rung above it — which *would* have passed — is
    never trialled, because everything above the first pass is dominated by the
    objective itself.
    """
    cheap = _candidate("cheap-1", 0.25)
    middle = _candidate("middle-1", 1.0)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {
            ("cheap-1", "medium"): [_failed()],
            ("middle-1", "medium"): [_passed()] * PROMOTION_TRIALS,
            ("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(cheap, middle, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
    )

    assert result.stop is SearchStop.WINNER
    assert result.winner == middle
    assert [rung.candidate for rung in result.rungs] == [cheap, middle]
    assert runner.trials_for(dear) == 0


def test_a_failure_abandons_its_rung_and_the_tally_records_what_ran() -> None:
    """A rung that fails once is abandoned; its remaining Trials are not run.

    A rung that has already failed cannot be promoted, so finishing it is pure
    waste. The tally is of the Trials actually *run* — three, one of them red —
    rather than five it never sent, because a record that claims five would be
    reporting work nobody paid for.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {
            ("cheap-1", "medium"): [_passed(), _passed(), _failed(failure="pytest red")],
            ("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
    )

    assert runner.trials_for(cheap) == 3
    abandoned = result.rungs[0]
    assert (abandoned.candidate, abandoned.passed, abandoned.total) == (cheap, 2, 3)
    assert result.winner == dear


def test_a_staircase_walked_to_the_top_with_no_pass_publishes_no_winner() -> None:
    """Every rung failed, so the search stops without a pair — never with a guess.

    Cheapest-first has a pathological case where nothing cheap passes, and the
    honest answer at the top of the staircase is *no winner*. Recording the most
    nearly-passing rung would be a ranking, which is the thing ADR-0027 replaced
    with a search.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {("cheap-1", "medium"): [_failed()], ("dear-1", "medium"): [_failed()]}
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
    )

    assert result.stop is SearchStop.STAIRCASE_EXHAUSTED
    assert result.winner is None
    assert result.stopped_at_rung == 2
    assert result.rungs_available == 2


def test_the_credit_ceiling_stops_the_search_and_names_where_it_stopped() -> None:
    """Reaching the **AI Credit** ceiling stops the walk and publishes no winner.

    The incumbent is kept by publishing nothing: the record says where it
    stopped and how many rungs were available, so an operator can tell *"nothing
    cheap passes"* from *"this ran out of money before it found out"*.
    """
    rungs = tuple(_candidate(f"model-{index}", float(index)) for index in range(1, 5))
    runner = _ScriptedTrialRunner(
        {(rung.model, rung.effort): [_failed(credits=6.0)] for rung in rungs}
    )

    result = search_price_staircase(
        candidates=rungs,
        proving_set=_proving_set(),
        budget=_budget(credits=10.0),
        runner=runner,
    )

    assert result.stop is SearchStop.CREDIT_CEILING
    assert result.winner is None
    assert result.stopped_at_rung == 2
    assert result.rungs_available == 4
    assert result.credits == Decimal("12.0")
    assert runner.trials_for(rungs[2]) == 0


def test_the_wall_clock_ceiling_stops_the_search_and_is_named_as_the_cause() -> None:
    """The wall-clock ceiling stops the walk the same way, and says it was the one.

    Two ceilings exist because the credit one points the wrong way (ADR-0027):
    cheapest-first defers spend while the gate costs the same wall clock on every
    rung. A record that only said *"incomplete"* would leave an operator unable
    to tell which ceiling to raise.
    """
    rungs = tuple(_candidate(f"model-{index}", float(index)) for index in range(1, 5))
    runner = _ScriptedTrialRunner(
        {
            (rung.model, rung.effort): [_failed(credits=0.01, seconds=600.0)]
            for rung in rungs
        }
    )

    result = search_price_staircase(
        candidates=rungs,
        proving_set=_proving_set(),
        budget=_budget(credits=1_000.0, seconds=900.0),
        runner=runner,
    )

    assert result.stop is SearchStop.WALL_CLOCK_CEILING
    assert result.winner is None
    assert result.stopped_at_rung == 2
    assert result.rungs_available == 4
    assert result.wall_clock_seconds == 1_200.0


def test_an_unknown_consumption_leaves_the_wall_clock_ceiling_to_bind() -> None:
    """A Trial the harness never billed makes the credit total unknown, not zero.

    ADR-0026's rule reaches the search: reading an unreported Consumption as zero
    would let a walk cross its own credit ceiling while reporting that it had
    spent nothing. So the total latches to unknown, the credit ceiling can no
    longer trip — and the wall-clock ceiling, which is measured by a clock rather
    than reported by a harness, still bounds the search.
    """
    rungs = tuple(_candidate(f"model-{index}", float(index)) for index in range(1, 4))
    unbilled = TrialResult(passed=False, credits=None, wall_clock_seconds=600.0)
    runner = _ScriptedTrialRunner(
        {(rung.model, rung.effort): [unbilled] for rung in rungs}
    )

    result = search_price_staircase(
        candidates=rungs,
        proving_set=_proving_set(),
        budget=_budget(credits=0.5, seconds=900.0),
        runner=runner,
    )

    assert result.credits is None
    assert result.stop is SearchStop.WALL_CLOCK_CEILING
    assert result.rungs[0].credits is None


def test_an_interrupted_search_keeps_every_trial_it_measured() -> None:
    """Ctrl-C keeps what was measured and records the search as unfinished.

    A Calibration is hours long, so all-or-nothing would make an operator who
    interrupts one pay for it twice. The partially-walked rung is kept as the
    partial thing it is — two of its five Trials run — and no winner is
    published, because an interrupted rung was never unanimous.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)

    class _InterruptingRunner(_ScriptedTrialRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            if request.candidate == dear and self.trials_for(dear) == 2:
                self.calls.append(request)
                raise KeyboardInterrupt
            return super().run(request)

    runner = _InterruptingRunner(
        {
            ("cheap-1", "medium"): [_failed()],
            ("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
    )

    assert result.stop is SearchStop.INTERRUPTED
    assert result.winner is None
    assert [rung.candidate for rung in result.rungs] == [cheap, dear]
    assert (result.rungs[1].passed, result.rungs[1].total) == (2, 2)


def test_a_proving_set_thinner_than_five_promotes_nothing_and_spends_nothing() -> None:
    """Fewer than five Proving tasks is refused, not quietly measured.

    Three of three is not five of five, and a search that ran it would publish a
    winner promoted on a bar ADR-0027 never set — the unanimity rule read as
    *"everything available passed"* instead of *"five passed"*. Refusing costs
    nothing and a thin answer is worse than no answer, so no Trial is run at all.
    """
    runner = _ScriptedTrialRunner({})

    result = search_price_staircase(
        candidates=(_candidate("cheap-1", 0.25),),
        proving_set=_proving_set(count=PROMOTION_TRIALS - 2),
        budget=_budget(),
        runner=runner,
    )

    assert result.stop is SearchStop.INSUFFICIENT_PROVING_SET
    assert result.winner is None
    assert result.rungs == ()
    assert runner.calls == []


def test_no_stopped_search_can_be_made_to_carry_a_winner() -> None:
    """A stopped search must *look* stopped, and the record enforces it.

    Every stop other than a five-of-five promotion refuses a pair at
    construction, so the rule survives a later caller that assembles a
    :class:`SearchResult` itself — the incumbent is kept by there being nothing
    to publish, rather than by a caller remembering not to publish it.
    """
    incumbent = _candidate("cheap-1", 0.25)
    stopped_without_a_winner = [
        stop for stop in SearchStop if stop is not SearchStop.WINNER
    ]
    assert stopped_without_a_winner, "the enum must hold more than a winner"

    for stop in stopped_without_a_winner:
        with pytest.raises(ValueError, match="publishes no winner"):
            SearchResult(
                stop=stop,
                rungs=(),
                rungs_available=1,
                proving_tasks=_proving_set(),
                winner=incumbent,
            )

    with pytest.raises(ValueError, match="carries the pair it promoted"):
        SearchResult(
            stop=SearchStop.WINNER,
            rungs=(),
            rungs_available=1,
            proving_tasks=_proving_set(),
        )


def test_every_rung_measures_the_same_five_tasks_and_two_runs_measure_the_same_five() -> None:
    """The draw is made once and deterministically, so rungs and runs compare.

    Two properties in one case because they are one decision. Drawing **once**
    is what makes the rungs comparable: a cheap rung handed easier tasks than the
    rung above it is not a measurement. Drawing **deterministically** is what
    makes two runs of the same search measure the same work, so a re-measurement
    is a re-measurement rather than a fresh sample.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    oversized = _proving_set(count=PROMOTION_TRIALS + 4)

    def _run() -> tuple[list[ProvingTask], list[ProvingTask]]:
        runner = _ScriptedTrialRunner(
            {
                ("cheap-1", "medium"): [_passed()] * (PROMOTION_TRIALS - 1) + [_failed()],
                ("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS,
            }
        )
        result = search_price_staircase(
            candidates=(cheap, dear),
            proving_set=oversized,
            budget=_budget(),
            runner=runner,
        )
        assert result.stop is SearchStop.WINNER
        return (
            [call.task for call in runner.calls if call.candidate == cheap],
            [call.task for call in runner.calls if call.candidate == dear],
        )

    first_cheap, first_dear = _run()
    second_cheap, second_dear = _run()

    assert len(first_cheap) == PROMOTION_TRIALS
    assert first_cheap == first_dear
    assert (first_cheap, first_dear) == (second_cheap, second_dear)


def test_the_draw_is_the_newest_tasks_and_a_reordered_set_draws_the_same_five() -> None:
    """The draw is by issue number, so a re-mined set in another order is stable.

    Mining order is an accident of how the tracker answered, and a draw that
    inherited it would let two mining passes over the same corpus measure
    different work. Newest-first because the **Proving set** already measures the
    project you *were*; the most recent closed work is the least stale sample.
    """
    candidate = _candidate("cheap-1", 0.25)
    ordered = _proving_set(count=PROMOTION_TRIALS + 3)
    shuffled = tuple(reversed(ordered[2:])) + ordered[:2]

    def _drawn(proving_set: tuple[ProvingTask, ...]) -> tuple[ProvingTask, ...]:
        runner = _ScriptedTrialRunner(
            {("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS}
        )
        result = search_price_staircase(
            candidates=(candidate,),
            proving_set=proving_set,
            budget=_budget(),
            runner=runner,
        )
        return result.proving_tasks

    newest = tuple(sorted(ordered, key=lambda task: task.issue, reverse=True))[
        :PROMOTION_TRIALS
    ]
    assert _drawn(ordered) == newest
    assert _drawn(shuffled) == newest


def test_one_task_types_search_carries_nothing_into_the_next() -> None:
    """Each **Task type** is searched independently, including its budget.

    The hazard a shared accumulator would create is silent and directional: the
    first Task type calibrates normally and every one after it stops early
    against a ceiling it never spent. So a second search over the same budget and
    the same runner gets the whole budget, and reaches the same rung.
    """
    rungs = tuple(_candidate(f"model-{index}", float(index)) for index in range(1, 5))
    budget = _budget(credits=10.0)

    def _search() -> SearchResult:
        runner = _ScriptedTrialRunner(
            {(rung.model, rung.effort): [_failed(credits=6.0)] for rung in rungs}
        )
        return search_price_staircase(
            candidates=rungs,
            proving_set=_proving_set(),
            budget=budget,
            runner=runner,
        )

    first = _search()
    second = _search()

    assert first == second
    assert second.stop is SearchStop.CREDIT_CEILING
    assert second.stopped_at_rung == 2


def test_a_trial_reports_only_the_three_scoring_keys_and_its_failure_detail() -> None:
    """Nothing but the gate, the credits and the wall clock can reach the score.

    ADR-0027 scores a **Trial** lexicographically on exactly those three and
    forbids a judge, an acceptance-criteria score and a weighted composite. The
    strongest available pin is that no fourth *scoring* field exists to be
    weighted: ``failure`` is detail a reader checks the conclusion with, and
    nothing in the search branches on it.
    """
    fields = {field.name for field in dataclasses.fields(TrialResult)}

    assert fields == {"passed", "credits", "wall_clock_seconds", "failure"}


def test_the_search_reaches_no_io_and_no_prior() -> None:
    """The search core is pure over its injected inputs, and its imports say so.

    Three claims a reader cannot check by walking the loop. **Purity**:
    everything expensive sits behind :class:`TrialRunner`, so a module that
    imported a worktree, a git client, a subprocess or the harness would have
    grown a second way to spend — and the search would stop being
    fixture-testable offline. **Determinism**: the threads that make **Trials**
    concurrent live behind the dispatcher seam (#381), so a search that imported
    one would have made *which Trials are bought* depend on which thread got
    there first. **No prior**: ``RECOMMENDED_ROUTING`` is a hardcoded human
    guess, and a search that consulted it would have the inferential prior back
    as an initial condition (ADR-0027). The same guard
    :mod:`git_loopy.staircase` carries, for the same reason, and checked the same
    way — over the names the module actually *reaches*, so prose about the prior
    is not mistaken for a use of it.
    """
    tree = ast.parse(Path(calibration_search.__file__).read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "calibration_search.py uses absolute imports only"
            assert node.module is not None, "from-import with no module name"
            imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)

    forbidden_modules = {
        "os",
        "subprocess",
        "shutil",
        "pathlib",
        "socket",
        "urllib",
        "urllib.request",
        "httpx",
        "requests",
        "copilot",
        "threading",
        "concurrent.futures",
        "git_loopy.git",
        "git_loopy.gh",
        "git_loopy.worktree",
        "git_loopy.session",
        "git_loopy.copilot_client",
        "git_loopy.model_listing",
        "git_loopy.settings",
    }
    leaked = imported_modules & forbidden_modules
    assert not leaked, f"the search core reaches I/O: {leaked}"

    assert "RECOMMENDED_ROUTING" not in imported_names
    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "RECOMMENDED_ROUTING" not in referenced


def test_the_scripted_runner_satisfies_the_trial_runner_seam_structurally() -> None:
    """``TrialRunner`` is satisfied by shape, exactly as ``GateRunner`` is.

    The seam is ``@runtime_checkable`` so production (#369) and a scripted fake
    are interchangeable without either inheriting from the other — which is what
    lets every rule in this module be pinned without spending an **AI Credit**.
    """
    assert isinstance(_ScriptedTrialRunner({}), TrialRunner)


def test_a_known_charge_after_an_unknown_one_still_reaches_the_credit_ceiling() -> None:
    """Latching the *reported* total must not unbind the ceiling from what is known.

    An unknown Consumption makes the published figure one nobody can stand behind
    (ADR-0026), and that is all it makes unknown: the Trials that *were* billed
    are still money spent. Enforcing the ceiling on the reported total alone
    would let one unreported Trial early in a walk buy unlimited spend after it —
    the overspend the latch exists to prevent, arriving through the latch.
    """
    rungs = tuple(_candidate(f"model-{index}", float(index)) for index in range(1, 5))
    unbilled = TrialResult(passed=False, credits=None, wall_clock_seconds=10.0)
    runner = _ScriptedTrialRunner(
        {
            ("model-1", "medium"): [unbilled],
            ("model-2", "medium"): [_failed(credits=10.0)],
            ("model-3", "medium"): [_passed()] * PROMOTION_TRIALS,
            ("model-4", "medium"): [_passed()] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=rungs,
        proving_set=_proving_set(),
        budget=_budget(credits=10.0),
        runner=runner,
    )

    assert result.stop is SearchStop.CREDIT_CEILING
    assert result.stopped_at_rung == 2
    assert result.credits is None, "the reported total stays unknown, never a guess"
    assert runner.trials_for(rungs[2]) == 0


def test_a_rung_that_goes_five_of_five_wins_even_if_its_last_trial_reaches_a_ceiling() -> None:
    """A winner bought within budget is a winner, not a search that ran out.

    A ceiling stops the search from spending *more*; it does not retract evidence
    already paid for. Discarding a unanimous rung because its fifth Trial landed
    on the ceiling would keep the incumbent while having bought the very answer
    that replaces it — and the operator would pay again next time to learn it.
    """
    winner = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {
            ("cheap-1", "medium"): [_passed(credits=2.0)] * PROMOTION_TRIALS,
            ("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(winner, dear),
        proving_set=_proving_set(),
        budget=_budget(credits=10.0),
        runner=runner,
    )

    assert result.stop is SearchStop.WINNER
    assert result.winner == winner
    assert result.credits == Decimal("10.0")
    assert runner.trials_for(dear) == 0


def test_the_last_rung_of_the_staircase_reports_an_exhausted_walk_not_a_ceiling() -> None:
    """Running out of rungs and out of budget at once is reported as out of rungs.

    Both are true, and only one is useful: ``credit_ceiling`` would tell an
    operator to raise a ceiling that has nothing left to buy, while
    ``staircase_exhausted`` says the honest thing — every pair the roster offers
    was trialled and none of them passed.
    """
    rungs = tuple(_candidate(f"model-{index}", float(index)) for index in range(1, 3))
    runner = _ScriptedTrialRunner(
        {(rung.model, rung.effort): [_failed(credits=5.0)] for rung in rungs}
    )

    result = search_price_staircase(
        candidates=rungs,
        proving_set=_proving_set(),
        budget=_budget(credits=10.0),
        runner=runner,
    )

    assert result.stop is SearchStop.STAIRCASE_EXHAUSTED
    assert result.stopped_at_rung == result.rungs_available == 2
    assert result.credits == Decimal("10.0")


# --------------------------------------------------------------------------- #
# The declared ceilings and the plan arithmetic (#367)                          #
# --------------------------------------------------------------------------- #


def test_the_default_budget_bounds_a_search_on_both_axes() -> None:
    """Both ceilings are real bounds, not placeholders a search would ignore.

    A zero ceiling refuses before the first Trial and an infinite one is no
    ceiling at all; either would make ``calibrate --dry-run`` print a number
    that misdescribes what the search would actually do.
    """
    assert DEFAULT_SEARCH_BUDGET.credit_ceiling > 0
    assert DEFAULT_SEARCH_BUDGET.wall_clock_ceiling_seconds > 0
    assert DEFAULT_SEARCH_BUDGET.credit_ceiling < Decimal("Infinity")
    assert DEFAULT_SEARCH_BUDGET.wall_clock_ceiling_seconds != float("inf")


def test_the_default_credit_ceiling_is_decimal_money() -> None:
    """**AI Credits** are Decimal everywhere (ADR-0026); a float ceiling would drift."""
    assert isinstance(DEFAULT_SEARCH_BUDGET.credit_ceiling, Decimal)


def test_the_default_budget_is_the_budget_the_search_actually_takes() -> None:
    """Declared where it is *used*, so the printed plan and the walk cannot differ.

    ``calibrate --dry-run`` promises an operator two numbers before they spend.
    A default that the search would not accept is a promise about a different
    search.
    """
    cheap = _candidate("synth-cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("synth-cheap-1", "medium"): [_passed()] * 5})

    result = search_price_staircase(
        candidates=[cheap],
        proving_set=_proving_set(),
        budget=DEFAULT_SEARCH_BUDGET,
        runner=runner,
    )

    assert result.stop is SearchStop.WINNER


def test_the_maximum_trial_count_is_every_rung_walked_unanimously() -> None:
    """The number ``--dry-run`` prints: the worst case, never an estimate.

    A search that finds no winner walks every rung, and a rung that goes five of
    five runs five Trials — so the ceiling on Trials is the staircase's height
    times :data:`PROMOTION_TRIALS`. Anything smaller would be a guess about
    where the walk stops, which is the thing the search exists to discover.
    """
    assert maximum_trials(rungs_available=8) == 8 * PROMOTION_TRIALS


def test_a_staircase_with_no_rungs_plans_no_trials() -> None:
    """A refused staircase costs nothing, and the plan says so as a number."""
    assert maximum_trials(rungs_available=0) == 0


# ---------------------------------------------------------------------------
# Concurrency (#381)
# ---------------------------------------------------------------------------


class _RecordingDispatcher:
    """An inline dispatcher that remembers what was bought at a time.

    Wraps rather than replaces :class:`InlineTrialDispatcher`, so the grouping
    these tests assert on is the grouping the production dispatcher is handed.
    """

    def __init__(self, width: int) -> None:
        self._inner = InlineTrialDispatcher(width)
        self.width = width
        self.dispatches: list[tuple[TrialRequest, ...]] = []

    def dispatch(
        self, runner: TrialRunner, requests: Sequence[TrialRequest]
    ) -> tuple[TrialResult, ...]:
        self.dispatches.append(tuple(requests))
        return self._inner.dispatch(runner, requests)

    @property
    def sizes(self) -> list[int]:
        return [len(dispatch) for dispatch in self.dispatches]


def test_a_rung_probes_with_one_trial_before_buying_the_rest() -> None:
    """The first Trial of a rung runs alone, and the other four run together.

    Promotion is unanimous, so a single red Trial kills a rung — which makes one
    Trial the cheapest possible evidence that a rung is dead, and cheapest-first
    means most rungs are. The probe is what keeps concurrency a wall-clock saving
    rather than a credit bill: a rung that lives still collapses from five
    Trial-times to two.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})
    dispatcher = _RecordingDispatcher(width=PROMOTION_TRIALS)

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=dispatcher,
    )

    assert result.stop is SearchStop.WINNER
    assert dispatcher.sizes == [1, 4]


def test_a_rung_that_dies_at_its_probe_costs_exactly_what_it_costs_serially() -> None:
    """Concurrency buys nothing it does not need, on the rungs it expects to fail.

    The whole point of probing: without it, a width of five would spend five
    Trials to learn what one Trial already said, on every rung below the winner.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {
            ("cheap-1", "medium"): [_failed()],
            ("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    assert result.stop is SearchStop.WINNER
    assert runner.trials_for(cheap) == 1
    assert (result.rungs[0].passed, result.rungs[0].total) == (0, 1)


def test_the_remainder_is_bought_at_the_operators_width() -> None:
    """A narrower host buys the four post-probe Trials in narrower dispatches.

    The bound is the operator's, because the useful ceiling is the host's and
    this project cannot pick it.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})
    dispatcher = _RecordingDispatcher(width=2)

    search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=dispatcher,
    )

    assert dispatcher.sizes == [1, 2, 2]


def test_a_serial_dispatcher_buys_one_trial_at_a_time_in_order() -> None:
    """Width 1 is the walk the search always performed, reached by the same code.

    Which is *why* a gate outcome is identical to a serial run: there is no
    second path for a parallel search to take.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})
    dispatcher = _RecordingDispatcher(width=1)

    search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=dispatcher,
    )

    assert dispatcher.sizes == [1, 1, 1, 1, 1]


def test_no_two_trials_in_one_dispatch_share_a_slot() -> None:
    """Distinct slots are what make distinct worktrees structural.

    The runner (#369) keys its worktree and working branch off the slot it is
    handed, so *"concurrent Trials share no worktree and no working branch"* is
    a property of what the search issues rather than a convention the runner has
    to honour. Slots stay inside the operator's width, so a host prepared for
    ``width`` worktrees never has to hold one more.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})
    dispatcher = _RecordingDispatcher(width=PROMOTION_TRIALS)

    search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=dispatcher,
    )

    for dispatch in dispatcher.dispatches:
        slots = [request.slot for request in dispatch]
        assert len(set(slots)) == len(slots)
        assert max(slots) < dispatcher.width


def test_a_dispatched_trial_carries_the_pair_and_the_task_it_replays() -> None:
    """The request is the whole of what distinguishes one concurrent Trial.

    A production runner is called from several threads at once, so anything it
    had to remember between calls would be shared state between Trials that are
    supposed to share none.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})
    tasks = _proving_set()

    search_price_staircase(
        candidates=(cheap,),
        proving_set=tasks,
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    assert [call.candidate for call in runner.calls] == [cheap] * PROMOTION_TRIALS
    assert {call.task for call in runner.calls} == set(tasks)


def test_wall_clock_is_elapsed_time_and_not_the_sum_of_overlapping_trials() -> None:
    """Trials that ran together took the longest of them, not all of them added up.

    The defect concurrency exposes, and the reason this is not a reporting
    detail: a Calibration that summed overlapping Trials would report itself five
    times slower than the operator watched it be — and would trip its own
    wall-clock ceiling at a fifth of the time they authorised, which is the one
    ceiling concurrency exists to fit inside.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner(
        {("cheap-1", "medium"): [_passed(seconds=600.0)] * PROMOTION_TRIALS}
    )

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    # The probe, then four Trials that overlapped: two Trial-times, not five.
    assert result.wall_clock_seconds == 1200.0
    assert result.rungs[0].wall_clock_seconds == 1200.0


def test_a_serial_search_still_reports_the_sum_it_always_did() -> None:
    """Because a dispatch of one has a maximum equal to its sum.

    So the elapsed rule is not a change to the serial walk; it is the same
    number reached by a statement that also holds when Trials overlap.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner(
        {("cheap-1", "medium"): [_passed(seconds=600.0)] * PROMOTION_TRIALS}
    )

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
    )

    assert result.wall_clock_seconds == 3000.0


def test_concurrency_discounts_no_credit() -> None:
    """Five Trials run at once cost what five Trials cost.

    Wall clock is what overlapping changes; **AI Credits** are not, and a search
    that folded them the same way would under-report its own spend.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner(
        {("cheap-1", "medium"): [_passed(credits=2.0)] * PROMOTION_TRIALS}
    )

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    assert result.credits == Decimal("10")


def test_both_ceilings_still_bound_a_concurrent_search() -> None:
    """The bound is on the Calibration as a whole, however wide it ran.

    Checked before every dispatch rather than before every Trial, so a search
    can overshoot by at most one dispatch — the same statement as before, with
    "Trial" widened to "what is bought at a time".
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {
            ("cheap-1", "medium"): [_failed(credits=60.0)],
            ("dear-1", "medium"): [_passed(credits=1.0)] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(credits=50.0),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    assert result.stop is SearchStop.CREDIT_CEILING
    assert result.winner is None
    assert runner.trials_for(dear) == 0


def test_the_result_records_the_width_it_was_measured_at() -> None:
    """A rung's wall clock is only comparable to another measured the same way.

    Contention makes a Trial's wall clock noisier under concurrency, and wall
    clock is the third key the search scores on — so the record has to say what
    width produced it or a reader cannot tell noise from a result.
    """
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(3),
    )

    assert result.concurrency == 3


def test_a_serial_result_records_a_width_of_one() -> None:
    """The default is serial, and the record says so rather than saying nothing."""
    cheap = _candidate("cheap-1", 0.25)
    runner = _ScriptedTrialRunner({("cheap-1", "medium"): [_passed()] * PROMOTION_TRIALS})

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
    )

    assert result.concurrency == 1


@pytest.mark.parametrize("width", range(1, PROMOTION_TRIALS + 1))
def test_the_gate_outcome_and_the_tally_are_identical_at_every_width(width: int) -> None:
    """The same script promotes the same pair on the same evidence, however wide.

    The acceptance criterion stated as a walk: a rung that dies at its probe and
    a rung that goes five of five are the two shapes cheapest-first actually
    produces, and neither depends on how many Trials were in flight.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)
    runner = _ScriptedTrialRunner(
        {
            ("cheap-1", "medium"): [_failed(credits=0.5)],
            ("dear-1", "medium"): [_passed(credits=2.0)] * PROMOTION_TRIALS,
        }
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(width),
    )

    assert result.stop is SearchStop.WINNER
    assert result.winner == dear
    assert [(rung.passed, rung.total) for rung in result.rungs] == [(0, 1), (5, 5)]
    assert result.credits == Decimal("10.5")


def test_an_interrupt_mid_dispatch_keeps_the_trials_that_had_completed() -> None:
    """Ctrl-C during a concurrent dispatch keeps what was already paid for.

    Under concurrency the Trials that had returned when the operator interrupted
    arrive on the interrupt itself, which is the only way they reach the record —
    a search that let them fall on the floor would make an operator who stops one
    pay for it twice, which is the fault the serial path was already written to
    avoid.
    """
    cheap = _candidate("cheap-1", 0.25)

    class _InterruptingRunner(_ScriptedTrialRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            if self.trials_for(cheap) == 3:
                self.calls.append(request)
                raise KeyboardInterrupt
            return super().run(request)

    runner = _InterruptingRunner(
        {("cheap-1", "medium"): [_passed(seconds=60.0)] * PROMOTION_TRIALS}
    )

    result = search_price_staircase(
        candidates=(cheap,),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    assert result.stop is SearchStop.INTERRUPTED
    assert result.winner is None
    # The probe, plus the two Trials of the second dispatch that had returned.
    assert (result.rungs[0].passed, result.rungs[0].total) == (3, 3)


def test_a_trial_that_could_not_be_measured_fails_its_rung_rather_than_the_search() -> None:
    """A Trial that explodes is a red Trial, and the search keeps its record.

    A raise would lose the rungs already measured, which is hours of a
    Calibration thrown away over one unreachable worktree — and would leave a
    ``calibrate`` run with no record of what it had already spent.
    """
    cheap = _candidate("cheap-1", 0.25)
    dear = _candidate("dear-1", 4.0)

    class _ExplodingOnCheap(_ScriptedTrialRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            if request.candidate == cheap:
                self.calls.append(request)
                raise RuntimeError("worktree vanished")
            return super().run(request)

    runner = _ExplodingOnCheap(
        {("dear-1", "medium"): [_passed()] * PROMOTION_TRIALS}
    )

    result = search_price_staircase(
        candidates=(cheap, dear),
        proving_set=_proving_set(),
        budget=_budget(),
        runner=runner,
        dispatcher=InlineTrialDispatcher(PROMOTION_TRIALS),
    )

    assert result.stop is SearchStop.WINNER
    assert result.winner == dear
    assert (result.rungs[0].passed, result.rungs[0].total) == (0, 1)
    # ADR-0026: the crashed Trial's Consumption is unknown, so the search's is.
    assert result.credits is None
