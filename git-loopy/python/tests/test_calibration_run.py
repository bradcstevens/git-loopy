"""``git_loopy.calibration_run`` tests — the path that spends (#372, ADR-0027).

Everything before spending is :mod:`git_loopy.calibratecmd`'s (#367) and is tested
next door. This module is the other half: the one command that may run a **Trial**,
and therefore the one command that has to be provably impossible to reach by
accident. So these tests ask three kinds of question:

* **Does it spend only when asked?** Serial refusal, the printed plan, the
  confirmation, and the structural pin that no other code path starts a Calibration.
* **Does it record what happened?** A winner, a ceiling, an interrupt and an
  exhausted staircase are four different facts, and the committed artifact says
  which one it was.
* **Does it stay offline?** Every seam that costs — the corpus admission, the Trial
  runner, the staircase, the tracker — is injected, so nothing here reaches a
  network, a harness or a real repository beyond a ``tmp_path`` git tree.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import pytest

from git_loopy import calibration_run
from git_loopy.calibration_run import (
    CalibrationOutcome,
    RecordDecision,
    TaskTypeCalibration,
)
from git_loopy.calibration_search import (
    PROMOTION_TRIALS,
    SearchBudget,
    SearchResult,
    SearchStop,
    WalkedRung,
)
from git_loopy.gh import Issue, Repo
from git_loopy.git import SubprocessGitClient
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
    ProvingTask,
    Rung,
    load_measured_routing,
    measured_routing_path,
)
from git_loopy.proving_admission import AdmittedProvingSet, AdmittedProvingTask
from git_loopy.proving_set import ProvingCandidate
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.staircase import Candidate, PriceStaircase, build_price_staircase
from git_loopy.trial_concurrency import TrialInterrupt, TrialRequest, TrialResult
from tests.fakes import FakeGitHubClient

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH; calibrate's Proving-set read cannot be exercised",
)


# --------------------------------------------------------------------------- #
# Fixtures shared by every slice                                               #
# --------------------------------------------------------------------------- #


CHEAP = Candidate(model="synth-cheap-1", effort="low", multiplier=0.25)
DEAR = Candidate(model="synth-dear-1", effort="high", multiplier=4.0)


def _pins(count: int = PROMOTION_TRIALS) -> tuple[ProvingTask, ...]:
    return tuple(
        ProvingTask(
            issue=100 + index, base_commit=f"base{index}", oracle_commit=f"fix{index}"
        )
        for index in range(count)
    )


def _rung(
    candidate: Candidate,
    *,
    passed: int,
    total: int,
    credits: Decimal | None = Decimal("1.5"),
    seconds: float = 600.0,
) -> WalkedRung:
    return WalkedRung(
        candidate=candidate,
        passed=passed,
        total=total,
        credits=credits,
        wall_clock_seconds=seconds,
    )


def _result(
    stop: SearchStop,
    *,
    rungs: Sequence[WalkedRung] = (),
    winner: Candidate | None = None,
    credits: Decimal | None = Decimal("3.0"),
    seconds: float = 1200.0,
    available: int = 2,
) -> SearchResult:
    return SearchResult(
        stop=stop,
        rungs=tuple(rungs),
        rungs_available=available,
        proving_tasks=_pins(),
        credits=credits,
        wall_clock_seconds=seconds,
        winner=winner,
    )


# --------------------------------------------------------------------------- #
# One search's result becomes one record — or nothing at all                   #
# --------------------------------------------------------------------------- #


def test_a_winning_search_becomes_a_measured_record_carrying_its_own_evidence() -> None:
    """ADR-0028: the distillate must stand on its own.

    The winning pair alone would be an assertion — the same thing the measured
    tier exists to replace — so the record carries every rung the walk climbed and
    the **Proving tasks** it measured, which is what a later reader checks the
    conclusion against.
    """
    result = _result(
        SearchStop.WINNER,
        rungs=[
            _rung(CHEAP, passed=1, total=2, credits=Decimal("0.5")),
            _rung(DEAR, passed=5, total=5, credits=Decimal("2.5")),
        ],
        winner=DEAR,
    )

    decision = calibration_run.record_for(result)

    assert decision.unwritten_reason is None
    entry = decision.entry
    assert entry is not None
    assert entry.status is MeasuredStatus.MEASURED
    assert (entry.model, entry.effort) == ("synth-dear-1", "high")
    assert (entry.trials_passed, entry.trials_total) == (5, 5)
    assert entry.rungs_walked == 2
    assert entry.credits == pytest.approx(3.0)
    assert entry.wall_clock_seconds == 1200
    assert [rung.model for rung in entry.rungs] == ["synth-cheap-1", "synth-dear-1"]
    assert entry.proving_tasks == _pins()


def test_a_ceiling_writes_incomplete_with_the_rung_it_reached_and_no_winner() -> None:
    """A stopped search must look stopped, in the artifact as in the search.

    ``incomplete`` carries where the walk got to and what it cost, and carries no
    pair at all — so the incumbent keeps routing by there being nothing to
    supersede it rather than by a caller remembering not to.
    """
    result = _result(
        SearchStop.CREDIT_CEILING,
        rungs=[_rung(CHEAP, passed=2, total=3)],
        available=7,
    )

    entry = calibration_run.record_for(result).entry

    assert entry is not None
    assert entry.status is MeasuredStatus.INCOMPLETE
    assert entry.routed_pair is None
    assert (entry.stopped_at_rung, entry.rungs_available) == (1, 7)
    assert entry.credits == pytest.approx(3.0)
    assert [rung.model for rung in entry.rungs] == ["synth-cheap-1"]


def test_an_interrupted_search_keeps_what_it_measured_and_publishes_no_winner() -> None:
    """A Calibration is hours long, so all-or-nothing would charge twice."""
    result = _result(
        SearchStop.INTERRUPTED, rungs=[_rung(CHEAP, passed=3, total=3)], available=4
    )

    entry = calibration_run.record_for(result).entry

    assert entry is not None
    assert entry.status is MeasuredStatus.INCOMPLETE
    assert entry.routed_pair is None
    assert entry.proving_tasks == _pins()


def test_an_exhausted_staircase_is_recorded_as_incomplete_rather_than_dropped() -> None:
    """*Nothing was cheap enough* is a finding, and the artifact keeps it.

    A search that walked every rung and promoted none is not the same fact as a
    search nobody ran; recording it is what stops the next operator paying for the
    identical walk to learn the identical thing.
    """
    result = _result(
        SearchStop.STAIRCASE_EXHAUSTED,
        rungs=[_rung(CHEAP, passed=1, total=2), _rung(DEAR, passed=4, total=5)],
        available=2,
    )

    entry = calibration_run.record_for(result).entry

    assert entry is not None
    assert entry.status is MeasuredStatus.INCOMPLETE
    assert (entry.stopped_at_rung, entry.rungs_available) == (2, 2)


def test_a_search_refused_before_its_first_trial_writes_nothing_at_all() -> None:
    """A refusal measured nothing, so it has nothing to record.

    An ``incomplete`` record stamped ``stopped_at_rung = 0`` would put a
    Calibration that never started into the artifact as one that started and
    stopped — and it would replace whatever the Task type's incumbent record said.
    """
    result = _result(
        SearchStop.INSUFFICIENT_PROVING_SET, credits=Decimal(0), seconds=0.0
    )

    decision = calibration_run.record_for(result)

    assert decision.entry is None
    assert decision.unwritten_reason is not None
    assert "no Trial ran" in decision.unwritten_reason


def test_a_search_whose_spend_is_unknown_writes_nothing_rather_than_a_zero() -> None:
    """ADR-0026: an unknown cost is unavailable, and never zero.

    The artifact has no spelling for *unknown* — ``credits`` is a required float on
    both record states — so the only honest move left is to keep the incumbent and
    say why. Writing ``0.0`` would put a figure nobody can stand behind beside a
    pair that routes real work.
    """
    result = _result(
        SearchStop.WINNER,
        rungs=[_rung(DEAR, passed=5, total=5, credits=None)],
        winner=DEAR,
        credits=None,
    )

    decision = calibration_run.record_for(result)

    assert decision.entry is None
    assert decision.unwritten_reason is not None
    assert "unknown" in decision.unwritten_reason


# --------------------------------------------------------------------------- #
# Which Task types get a search at all                                         #
# --------------------------------------------------------------------------- #


def _candidate(issue: int, task_type: str) -> ProvingCandidate:
    return ProvingCandidate(
        issue=issue,
        task_type=task_type,
        base_commit=f"base{issue}",
        oracle_commit=f"fix{issue}",
        oracle_paths=(f"tests/test_{issue}.py",),
        task_text="## What to build\n\nThe thing.\n",
    )


def _admitted(counts: dict[str, int]) -> AdmittedProvingSet:
    tasks: list[AdmittedProvingTask] = []
    issue = 100
    for task_type, count in counts.items():
        for _ in range(count):
            tasks.append(
                AdmittedProvingTask(
                    candidate=_candidate(issue, task_type),
                    oracle_loops=("python",),
                    base_failure="oracle red at base",
                )
            )
            issue += 1
    return AdmittedProvingSet(tasks=tuple(tasks))


def test_a_task_type_short_of_five_admitted_tasks_is_skipped_with_its_shortfall() -> (
    None
):
    """Refused before the first Trial, and told exactly what is missing.

    Promotion is unanimous over :data:`PROMOTION_TRIALS` tasks (ADR-0027), so a
    Task type with fewer cannot produce a winner at all — measuring three of three
    and promoting would read unanimity as *"everything available passed"*, a bar
    the ADR never set. Naming the shortfall is what turns "cannot calibrate" into
    something an operator can act on.
    """
    eligible, skipped = calibration_run.eligible_task_types(
        _admitted({"bugfix": PROMOTION_TRIALS, "docs": 2}),
        task_types=("bugfix", "docs"),
    )

    assert eligible == ("bugfix",)
    assert [skip.task_type for skip in skipped] == ["docs"]
    assert "3 short" in skipped[0].reason
    assert "admitted" in skipped[0].reason


def test_a_task_type_with_no_corpus_at_all_is_skipped_rather_than_omitted() -> None:
    """A Task type silently dropped reads as one that needed nothing."""
    _eligible, skipped = calibration_run.eligible_task_types(
        _admitted({"bugfix": PROMOTION_TRIALS}), task_types=("bugfix", "chore")
    )

    assert [skip.task_type for skip in skipped] == ["chore"]
    assert "has 0" in skipped[0].reason


# --------------------------------------------------------------------------- #
# The walk over the eligible Task types                                        #
# --------------------------------------------------------------------------- #


class _ScriptedRunner:
    """A :class:`~git_loopy.trial_concurrency.TrialRunner` that spends nothing.

    Verdicts are keyed by ``(model, effort)`` so a rung either goes five of five or
    dies on its probe, which is the only distinction the search branches on.
    """

    def __init__(self, verdicts: dict[tuple[str, str | None], bool]) -> None:
        self.verdicts = verdicts
        self.requests: list[TrialRequest] = []

    def run(self, request: TrialRequest) -> TrialResult:
        self.requests.append(request)
        passed = self.verdicts.get(
            (request.candidate.model, request.candidate.effort), False
        )
        return TrialResult(
            passed=passed,
            credits=Decimal("0.5"),
            wall_clock_seconds=60.0,
            failure=None if passed else "gate red",
        )


def _staircase_of(*candidates: Candidate) -> PriceStaircase:
    return PriceStaircase(candidates=tuple(candidates))


def test_every_eligible_task_type_gets_its_own_search_and_its_own_ceilings() -> None:
    """One search per **Task type**, so no ceiling leaks from one into the next.

    A Calibration that shared a budget across the taxonomy would let an expensive
    ``bugfix`` walk exhaust the credits ``docs`` was going to be measured with, and
    the second Task type would read as *unmeasurable* when it was merely second in
    a list.
    """
    runner = _ScriptedRunner({(DEAR.model, DEAR.effort): True})

    outcome = calibration_run.calibrate(
        task_types=("bugfix", "docs"),
        staircase=_staircase_of(CHEAP, DEAR),
        admitted=_admitted({"bugfix": PROMOTION_TRIALS, "docs": PROMOTION_TRIALS}),
        runner=runner,
        budget=SearchBudget(
            credit_ceiling=Decimal("1000"), wall_clock_ceiling_seconds=1e9
        ),
        calibration_id="cal01",
    )

    assert [item.task_type for item in outcome.calibrated] == ["bugfix", "docs"]
    for item in outcome.calibrated:
        assert item.result.stop is SearchStop.WINNER
        assert item.result.winner == DEAR


def test_a_skipped_task_type_does_not_stop_the_eligible_ones_running() -> None:
    """The one criterion a per-Task-type loop can get wrong in a way nobody sees."""
    runner = _ScriptedRunner({(CHEAP.model, CHEAP.effort): True})

    outcome = calibration_run.calibrate(
        task_types=("docs", "bugfix"),
        staircase=_staircase_of(CHEAP),
        admitted=_admitted({"docs": 1, "bugfix": PROMOTION_TRIALS}),
        runner=runner,
        budget=SearchBudget(
            credit_ceiling=Decimal("1000"), wall_clock_ceiling_seconds=1e9
        ),
        calibration_id="cal01",
    )

    assert [skip.task_type for skip in outcome.skipped] == ["docs"]
    assert [item.task_type for item in outcome.calibrated] == ["bugfix"]


def test_a_search_measures_only_its_own_task_types_proving_tasks() -> None:
    """The corpus is stratified, and the strata are what makes a rung comparable."""
    runner = _ScriptedRunner({(CHEAP.model, CHEAP.effort): True})
    admitted = _admitted({"bugfix": PROMOTION_TRIALS, "docs": PROMOTION_TRIALS})

    outcome = calibration_run.calibrate(
        task_types=("bugfix",),
        staircase=_staircase_of(CHEAP),
        admitted=admitted,
        runner=runner,
        budget=SearchBudget(
            credit_ceiling=Decimal("1000"), wall_clock_ceiling_seconds=1e9
        ),
        calibration_id="cal01",
    )

    bugfix_issues = {task.issue for task in admitted.pins_for("bugfix")}
    assert {request.task.issue for request in runner.requests} == bugfix_issues
    assert set(outcome.calibrated[0].result.proving_tasks) == set(
        admitted.pins_for("bugfix")
    )


def test_an_interrupted_task_type_ends_the_whole_calibration() -> None:
    """Ctrl-C means *stop spending*, not *stop this Task type and start the next*."""

    class _Interrupting:
        def run(self, request: TrialRequest) -> TrialResult:
            raise TrialInterrupt(measured=())

    outcome = calibration_run.calibrate(
        task_types=("bugfix", "docs"),
        staircase=_staircase_of(CHEAP),
        admitted=_admitted({"bugfix": PROMOTION_TRIALS, "docs": PROMOTION_TRIALS}),
        runner=_Interrupting(),
        budget=SearchBudget(
            credit_ceiling=Decimal("1000"), wall_clock_ceiling_seconds=1e9
        ),
        calibration_id="cal01",
    )

    assert outcome.interrupted
    assert [item.task_type for item in outcome.calibrated] == ["bugfix"]
    assert outcome.calibrated[0].result.stop is SearchStop.INTERRUPTED


# --------------------------------------------------------------------------- #
# Progress: a long search must not look like a hang                            #
# --------------------------------------------------------------------------- #


def test_progress_is_reported_as_each_trial_completes_with_its_place_in_the_walk() -> (
    None
):
    """Reported at the runner seam, which is where a Trial completing already is.

    A callback threaded through the search would be a second way to observe the
    walk, and the two could disagree; decorating the seam means a serial and a
    concurrent search report through the same object.
    """
    seen: list[calibration_run.TrialProgress] = []
    runner = _ScriptedRunner({(DEAR.model, DEAR.effort): True})

    calibration_run.calibrate(
        task_types=("bugfix",),
        staircase=_staircase_of(CHEAP, DEAR),
        admitted=_admitted({"bugfix": PROMOTION_TRIALS}),
        runner=runner,
        budget=SearchBudget(
            credit_ceiling=Decimal("1000"), wall_clock_ceiling_seconds=1e9
        ),
        calibration_id="cal01",
        on_trial=seen.append,
    )

    # The cheap rung dies on its probe; the dear rung goes five of five.
    assert [(item.rung_position, item.trial_index) for item in seen] == [
        (1, 1),
        (2, 1),
        (2, 2),
        (2, 3),
        (2, 4),
        (2, 5),
    ]
    assert {item.rungs_available for item in seen} == {2}
    assert {item.task_type for item in seen} == {"bugfix"}
    assert seen[0].result.passed is False


def test_a_progress_line_survives_a_pipe() -> None:
    """Non-interactive is the case that matters: a Calibration runs for hours.

    Plain lines with no cursor control, so ``git-loopy calibrate | tee`` is a
    readable log rather than a file full of escape sequences.
    """
    progress = calibration_run.TrialProgress(
        task_type="bugfix",
        candidate=DEAR,
        rung_position=2,
        rungs_available=7,
        trial_index=3,
        trials_needed=PROMOTION_TRIALS,
        task=ProvingTask(issue=101, base_commit="base", oracle_commit="fix"),
        result=TrialResult(
            passed=True, credits=Decimal("0.75"), wall_clock_seconds=90.0, failure=None
        ),
    )

    line = calibration_run.render_trial_progress(progress)

    assert "\r" not in line and "\x1b" not in line
    assert "rung 2 of 7" in line
    assert "Trial 3 of 5" in line
    assert "#101" in line
    assert "passed" in line
    assert "0.75 AI Credits" in line
    assert "$" not in line


def test_a_trial_the_harness_never_billed_reports_unknown_and_never_zero() -> None:
    """ADR-0026, on the surface an operator actually watches."""
    progress = calibration_run.TrialProgress(
        task_type="docs",
        candidate=CHEAP,
        rung_position=1,
        rungs_available=1,
        trial_index=1,
        trials_needed=PROMOTION_TRIALS,
        task=ProvingTask(issue=7, base_commit="base", oracle_commit="fix"),
        result=TrialResult(
            passed=False, credits=None, wall_clock_seconds=5.0, failure="gate red"
        ),
    )

    line = calibration_run.render_trial_progress(progress)

    assert "unknown AI Credits" in line
    assert "0 AI Credits" not in line
    assert "gate red" in line


# --------------------------------------------------------------------------- #
# What reaches the committed artifact                                          #
# --------------------------------------------------------------------------- #


def _measured_entry(model: str) -> MeasuredEntry:
    return MeasuredEntry(
        status=MeasuredStatus.MEASURED,
        model=model,
        effort="low",
        trials_passed=5,
        trials_total=5,
        rungs_walked=1,
        credits=1.0,
        wall_clock_seconds=60,
        rungs=(Rung(model=model, effort="low", passed=5, total=5, credits=1.0),),
        proving_tasks=_pins(),
    )


def test_a_single_task_type_calibration_leaves_every_other_record_alone() -> None:
    """``calibrate docs`` re-measures ``docs``, and nothing else.

    The artifact is one file for the whole taxonomy, so a writer that rebuilt it
    from this invocation's outcomes would silently retract every Task type the
    operator did not ask about — a Calibration that cost nothing and deleted
    evidence.
    """
    existing = MeasuredRouting(
        entries={"bugfix": _measured_entry("incumbent-model")},
        provenance=Provenance(
            cli_version="0.0.1",
            calibrated_at="2020-01-01T00:00:00Z",
            candidate_count=2,
            gate_loops=("python",),
        ),
    )
    outcome = CalibrationOutcome(
        calibration_id="cal01",
        calibrated=(
            TaskTypeCalibration(
                task_type="docs",
                result=_result(
                    SearchStop.WINNER,
                    rungs=[_rung(CHEAP, passed=5, total=5)],
                    winner=CHEAP,
                ),
                decision=RecordDecision(entry=_measured_entry("new-model")),
            ),
        ),
    )

    merged = calibration_run.merge_records(existing, outcome, _provenance())

    assert set(merged.entries) == {"bugfix", "docs"}
    assert merged.entries["bugfix"].model == "incumbent-model"
    assert merged.entries["docs"].model == "new-model"
    assert merged.provenance is not None
    assert merged.provenance.cli_version == "9.9.9"


def test_a_search_that_wrote_nothing_leaves_the_incumbent_record_standing() -> None:
    """Keeping the incumbent means writing nothing over it, not writing a blank."""
    existing = MeasuredRouting(entries={"docs": _measured_entry("incumbent-model")})
    outcome = CalibrationOutcome(
        calibration_id="cal01",
        calibrated=(
            TaskTypeCalibration(
                task_type="docs",
                result=_result(SearchStop.INSUFFICIENT_PROVING_SET),
                decision=RecordDecision(unwritten_reason="nothing measured"),
            ),
        ),
    )

    merged = calibration_run.merge_records(existing, outcome, _provenance())

    assert merged.entries["docs"].model == "incumbent-model"


def _provenance() -> Provenance:
    return Provenance(
        cli_version="9.9.9",
        calibrated_at="2026-08-14T00:00:00Z",
        candidate_count=2,
        gate_loops=("python",),
    )


def test_provenance_stamps_the_gate_the_trials_were_actually_scored_by() -> None:
    """Not this repository's gate today — the gate each base commit declared.

    ADR-0027 is explicit that strengthening the feedback-loop table does not
    improve a Calibration measured against a frozen **Proving set**; only a
    refresh propagates it. Stamping today's loops would assert the opposite in the
    one field a reader consults to check what scored the walk.
    """
    admitted = AdmittedProvingSet(
        tasks=(
            AdmittedProvingTask(
                candidate=_candidate(101, "bugfix"),
                oracle_loops=("Python suite",),
                base_failure="red",
            ),
            AdmittedProvingTask(
                candidate=_candidate(102, "bugfix"),
                oracle_loops=("Shell syntax", "Python suite"),
                base_failure="red",
            ),
        )
    )

    loops = calibration_run.gate_loops_for(admitted, measured=(101,))

    assert loops == ("Python suite",)


# --------------------------------------------------------------------------- #
# `git-loopy calibrate` — the command surface                                  #
# --------------------------------------------------------------------------- #


WELL_FORMED_BODY = (
    "## What to build\n\nThe thing.\n\n## Acceptance criteria\n\n- [ ] It works.\n"
)


@dataclass
class _FakeModel:
    id: str
    supported_reasoning_efforts: Sequence[str] = field(default_factory=tuple)
    billing: object = None


def _live_staircase() -> PriceStaircase:
    return build_price_staircase(
        [
            _FakeModel(id="synth-cheap-1", supported_reasoning_efforts=("low",)),
            _FakeModel(id="synth-dear-1", supported_reasoning_efforts=("high",)),
        ],
        RateCard(
            models={
                model: ModelRate(
                    model=model,
                    multiplier=multiplier,
                    prices=ModelPrices(batch_size=1_000_000, input_price=1.0),
                )
                for model, multiplier in (
                    ("synth-cheap-1", 0.25),
                    ("synth-dear-1", 4.0),
                )
            }
        ),
    )


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for key, value in (
        ("user.email", "tester@example.com"),
        ("user.name", "Tester"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        subprocess.run(
            ["git", "-C", str(path), "config", key, value],
            check=True,
            capture_output=True,
            text=True,
        )


def _commit(path: Path, message: str, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(
        ["git", "-C", str(path), "add", "-A"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
        text=True,
    )


def _corpus(
    tmp_path: Path, numbers: Sequence[int] = (10, 11, 12, 13, 14)
) -> FakeGitHubClient:
    """A repository and tracker with exactly enough ``bugfix`` work to calibrate."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 0\n"})
    for number in numbers:
        _commit(
            tmp_path,
            f"fix: issue {number}\n\nCloses #{number}",
            {
                "src.py": f"value = {number}\n",
                f"tests/test_{number}.py": "assert True\n",
            },
        )
    return FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[
            Issue(
                number=number,
                title=f"issue {number}",
                body=WELL_FORMED_BODY,
                labels=["task-type:bugfix"],
                state="CLOSED",
                url=f"https://example.invalid/{number}",
            )
            for number in numbers
        ],
    )


class _Sink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _AdmitEverything:
    """A :class:`~git_loopy.proving_admission.CandidateVerifier` that runs no test."""

    def __init__(self) -> None:
        self.verified: list[int] = []

    def verify(self, candidate: ProvingCandidate) -> AdmittedProvingTask:
        self.verified.append(candidate.issue)
        return AdmittedProvingTask(
            candidate=candidate,
            oracle_loops=("Python suite",),
            base_failure="oracle red",
        )


def _run(
    tmp_path: Path,
    github: FakeGitHubClient,
    *,
    runner: object | None = None,
    verifier: object | None = None,
    task_type: str | None = None,
    parallel: int | None = 4,
    assume_yes: bool = True,
    confirm: object | None = None,
    budget: SearchBudget | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, _Sink, _Sink, object]:
    out, err = _Sink(), _Sink()
    resolved_runner = (
        runner
        if runner is not None
        else _ScriptedRunner({("synth-cheap-1", "low"): True})
    )
    code = calibration_run.run_calibrate(
        repo_root=tmp_path,
        env=env if env is not None else {"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        task_type=task_type,
        parallel=parallel,
        assume_yes=assume_yes,
        confirm=confirm,
        out=out,
        err=err,
        github=github,
        git=SubprocessGitClient(tmp_path),
        fetch_staircase=lambda _warn: _live_staircase(),
        budget=budget
        or SearchBudget(credit_ceiling=Decimal("1000"), wall_clock_ceiling_seconds=1e9),
        verifier=verifier if verifier is not None else _AdmitEverything(),
        harness=lambda _admitted, _cid, body: body(resolved_runner),
        calibration_id="CAL0000000000000000000000",
        now=lambda: "2026-08-14T00:00:00Z",
    )
    return code, out, err, resolved_runner


def test_calibrate_refuses_to_spend_when_the_resolved_run_is_serial(
    tmp_path: Path,
) -> None:
    """Nothing a Calibration measured would take effect, so it must not be bought.

    The refusal comes from :mod:`git_loopy.routing_scope` rather than a second
    comparison here, so ``calibrate`` and ``config get`` cannot disagree about
    whether routing is in force.
    """
    github = _corpus(tmp_path)

    code, out, err, runner = _run(tmp_path, github, parallel=1)

    assert code == 1
    assert "routing is scoped to Parallel mode" in err.text
    assert "--parallel" in err.text
    assert runner.requests == []
    assert out.lines == []


def test_the_whole_plan_is_printed_before_the_first_trial(tmp_path: Path) -> None:
    """Everything an operator needs to decline: the staircase, both ceilings, the work.

    Printed by :func:`git_loopy.calibratecmd.render_plan` — the *same* renderer
    ``--dry-run`` uses — so the plan an operator confirms is the plan the dry run
    promised.
    """
    github = _corpus(tmp_path)

    code, out, _err, runner = _run(
        tmp_path, github, assume_yes=False, confirm=lambda _p: False
    )

    assert code == 0
    assert "Price staircase (2 rungs, cheapest first)" in out.text
    assert "AI Credits: 1000" in out.text
    assert "Wall clock:" in out.text
    assert "Maximum Trials: 10" in out.text
    assert "would measure 5 Proving tasks" in out.text
    assert "task-type:bugfix" in out.text
    assert runner.requests == []


def test_declining_the_confirmation_spends_nothing(tmp_path: Path) -> None:
    """An operator must be able to say no after reading the plan."""
    github = _corpus(tmp_path)
    verifier = _AdmitEverything()

    code, out, _err, runner = _run(
        tmp_path, github, verifier=verifier, assume_yes=False, confirm=lambda _p: False
    )

    assert code == 0
    assert "Declined" in out.text
    assert runner.requests == []
    assert verifier.verified == []
    assert not measured_routing_path(tmp_path).exists()


def test_a_non_interactive_terminal_refuses_without_an_explicit_yes(
    tmp_path: Path,
) -> None:
    """A human typed ``calibrate``; a script that inherited it did not.

    The two are indistinguishable from here, so the spend needs the flag that
    says it out loud rather than a default that guesses.
    """
    github = _corpus(tmp_path)

    code, _out, err, runner = _run(tmp_path, github, assume_yes=False, confirm=None)

    assert code == 1
    assert "--yes" in err.text
    assert runner.requests == []


def test_a_confirmed_calibration_writes_the_winner_and_the_evidence_that_chose_it(
    tmp_path: Path,
) -> None:
    """The whole point: a measured pair arrives as a diff a human can read."""
    github = _corpus(tmp_path)

    code, out, _err, _runner = _run(tmp_path, github)

    assert code == 0
    artifact = load_measured_routing(measured_routing_path(tmp_path))
    entry = artifact.entries["bugfix"]
    assert entry.status is MeasuredStatus.MEASURED
    assert (entry.model, entry.effort) == ("synth-cheap-1", "low")
    assert entry.trials_passed == entry.trials_total == PROMOTION_TRIALS
    assert {task.issue for task in entry.proving_tasks} == {10, 11, 12, 13, 14}
    assert artifact.provenance is not None
    assert artifact.provenance.candidate_count == 2
    assert artifact.provenance.gate_loops == ("Python suite",)
    assert "git revert" in out.text


def test_a_ceiling_keeps_the_incumbent_and_names_which_ceiling_stopped_it(
    tmp_path: Path,
) -> None:
    """The two ceilings have different remedies, so the record names which one."""
    github = _corpus(tmp_path)

    code, out, _err, _runner = _run(
        tmp_path,
        github,
        runner=_ScriptedRunner({}),
        budget=SearchBudget(
            credit_ceiling=Decimal("0.4"), wall_clock_ceiling_seconds=1e9
        ),
    )

    assert code == 0
    assert "the AI Credit ceiling stopped it" in out.text
    assert "The incumbent is kept" in out.text
    entry = load_measured_routing(measured_routing_path(tmp_path)).entries["bugfix"]
    assert entry.status is MeasuredStatus.INCOMPLETE
    assert entry.routed_pair is None
    assert (entry.stopped_at_rung, entry.rungs_available) == (1, 2)


def test_an_interrupted_calibration_keeps_what_it_measured_and_publishes_no_winner(
    tmp_path: Path,
) -> None:
    """Ctrl-C leaves an unfinished Calibration, recorded as one."""
    github = _corpus(tmp_path)

    class _Interrupting:
        def run(self, request: TrialRequest) -> TrialResult:
            raise TrialInterrupt(measured=())

        requests: list[TrialRequest] = []

    code, out, _err, _runner = _run(tmp_path, github, runner=_Interrupting())

    assert code == calibration_run.INTERRUPTED_EXIT_CODE
    assert "unfinished" in out.text
    assert "no winner" in out.text
    assert not measured_routing_path(tmp_path).exists()


def test_a_task_type_argument_calibrates_exactly_that_one(tmp_path: Path) -> None:
    """``calibrate docs`` re-measures ``docs`` without paying for all seven."""
    github = _corpus(tmp_path)

    code, out, err, _runner = _run(tmp_path, github, task_type="docs")

    assert code == 1
    assert "task-type:bugfix" not in out.text
    assert "nothing to measure" in err.text


def test_an_unknown_task_type_is_refused_against_the_closed_taxonomy(
    tmp_path: Path,
) -> None:
    """A typo that measured nothing would look like a corpus that supports nothing."""
    github = _corpus(tmp_path)

    code, _out, err, runner = _run(tmp_path, github, task_type="refactor")

    assert code == 1
    assert "refactor" in err.text
    assert runner.requests == []


def test_progress_is_reported_as_rungs_are_walked_and_trials_complete(
    tmp_path: Path,
) -> None:
    """A search that runs for hours must not be indistinguishable from a hang."""
    github = _corpus(tmp_path)

    _code, out, _err, _runner = _run(tmp_path, github)

    assert "rung 1 of 2" in out.text
    assert "Trial 5 of 5" in out.text
    assert "admitted #14" in out.text
    assert "\r" not in out.text
    assert "\x1b" not in out.text


def test_the_spending_path_reports_credits_and_never_a_usd_figure(
    tmp_path: Path,
) -> None:
    """ADR-0026's denomination, on the surface that actually spends."""
    github = _corpus(tmp_path)

    _code, out, err, _runner = _run(tmp_path, github)

    for text in (out.text, err.text):
        assert "$" not in text
        assert "USD" not in text
    assert "AI Credits" in out.text


def test_admission_stops_once_a_task_type_has_as_many_tasks_as_a_search_draws(
    tmp_path: Path,
) -> None:
    """Admission's cost is hours, and a search draws five however many exist."""
    github = _corpus(tmp_path, numbers=(10, 11, 12, 13, 14, 15, 16, 17))
    verifier = _AdmitEverything()

    _code, _out, _err, _runner = _run(tmp_path, github, verifier=verifier)

    assert verifier.verified == [17, 16, 15, 14, 13]


# --------------------------------------------------------------------------- #
# CLI wiring and the structural pins                                           #
# --------------------------------------------------------------------------- #


def test_a_bare_calibrate_dispatches_to_the_spending_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one wiring that must be right, because it is the one that costs."""
    from git_loopy import cli

    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        calibration_run, "run_calibrate", lambda **kwargs: seen.append(kwargs) or 0
    )
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)

    assert cli.main(["calibrate", "docs", "--yes", "--parallel", "3"]) == 0
    assert seen[0]["task_type"] == "docs"
    assert seen[0]["assume_yes"] is True
    assert seen[0]["parallel"] == 3


def test_a_task_type_argument_is_refused_beside_a_reporting_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reports are per-repository; narrowing them would answer a third question.

    A usage error rather than a silent narrowing, because ``calibrate docs
    --dry-run`` reads as *"what would calibrating docs cost?"* and the report it
    would actually print covers all seven.
    """
    from git_loopy import cli

    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)

    assert cli.main(["calibrate", "docs", "--dry-run"]) == 2


def test_only_the_calibrate_subcommand_can_start_a_calibration() -> None:
    """*A Calibration is always an explicit act* (ADR-0027), as a fact about imports.

    A vendor shipping a model on a Tuesday must not convert an unattended
    overnight **Run** into a benchmark suite, so the requirement is not that no
    Run *does* start one — it is that no Run *can*. Every module that could
    reach the spending path is enumerated here; a preflight, a roster drift check
    or a first-Run wizard that imported it would fail this.
    """
    import ast

    package = Path(calibration_run.__file__).parent
    importers: set[str] = set()
    for module in sorted(package.rglob("*.py")):
        if module.name == "calibration_run.py":
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module] + [
                    f"{node.module}.{alias.name}" for alias in node.names
                ]
            if any(name.endswith("calibration_run") for name in names):
                importers.add(module.relative_to(package).as_posix())

    assert importers == {"cli.py"}


def test_nothing_but_the_spending_path_reaches_the_search_or_the_trial_runner() -> None:
    """The two seams that actually buy a **Trial**, and who is allowed to hold them.

    ``search_price_staircase`` decides which Trials to buy and
    ``ReplayTrialRunner`` is what runs one. A module holding either is a module
    that can spend, whatever it does with it — so the list of them is the list of
    ways a Calibration can begin.
    """
    import ast

    package = Path(calibration_run.__file__).parent
    holders: set[str] = set()
    for module in sorted(package.rglob("*.py")):
        source = module.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = {alias.name for alias in node.names}
                if imported & {"search_price_staircase", "ReplayTrialRunner"}:
                    holders.add(module.relative_to(package).as_posix())

    assert holders == {"calibration_run.py"}


def test_the_orchestrator_creates_no_worktree_branch_or_temporary_state(
    tmp_path: Path,
) -> None:
    """Nothing survives, because nothing at this layer is ever created.

    Teardown belongs to the two seams that check a commit out — the **Trial**
    runner and the admission replay, each of which tears down in a ``finally``
    that catches :class:`BaseException`. This pins the other half: the surface
    that drives them adds no worktree, branch or scratch directory of its own, so
    there is no third thing for an interruption to leak.
    """
    github = _corpus(tmp_path)

    code, _out, _err, _runner = _run(tmp_path, github)

    assert code == 0
    worktrees = subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert worktrees.count("worktree ") == 1
    branches = subprocess.run(
        ["git", "-C", str(tmp_path), "branch", "--list", "git-loopy/*"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert branches.strip() == ""
    assert list(tmp_path.parent.glob("*git-loopy-trial*")) == []


def test_the_orchestrator_holds_no_worktree_seam_of_its_own() -> None:
    """Structural, because "creates nothing" is not something a case can exhaust."""
    import ast

    source = Path(calibration_run.__file__).read_text(encoding="utf-8")
    calls = {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
    }

    assert "add_worktree" not in calls
    assert "remove_worktree" not in calls
    assert "TemporaryDirectory" not in source
