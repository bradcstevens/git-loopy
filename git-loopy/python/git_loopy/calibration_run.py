"""``git_loopy.calibration_run`` — ``git-loopy calibrate``, the path that spends (#372).

:mod:`git_loopy.calibratecmd` answers everything about a **Calibration** that can be
known before it costs anything, and is structurally incapable of costing anything.
This module is the other half: the single command surface that may mine a corpus,
admit it, run **Trials** and write the committed **Measured routing** artifact.

Design notes:

* **A Calibration is always an explicit act** (ADR-0027). Nothing here is reachable
  from a **Run**, from preflight, or from a roster change — an operator types
  ``git-loopy calibrate`` and confirms it, or nothing is spent. The fully-automatic
  version is deliberately dead: routing improves when somebody asks for it, and the
  honest way to buy autonomy later is a pre-authorised spend allowance nobody has
  proposed.
* **One plan, printed once, by the same renderer the dry run uses.** ``--dry-run``
  promising work the spending path would not do is the one failure a dry run must
  not have, so both call :func:`git_loopy.calibratecmd.render_plan`.
* **Every seam that costs is injected.** The corpus verifier, the **Trial** runner,
  the staircase, the tracker and the clock all arrive as arguments, which is what
  lets the whole surface be pinned without a network, a harness or an **AI Credit**.
* **The search core is untouched.** :func:`~git_loopy.calibration_search.search_price_staircase`
  decides which Trials are bought and in what order; this module decides *which Task
  types* get a search at all, and what their results mean for the artifact.
* **Progress is observed at the runner seam, not bolted into the walk.** A Trial
  completing is exactly the event the seam already carries, so
  :class:`ProgressReportingRunner` decorates it rather than adding a callback to the
  search — and a serial and a concurrent search report through the same object.
* **AI Credits throughout** (ADR-0026). No USD figure appears, and an *unknown*
  spend is never rendered or recorded as zero.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

from git_loopy.calibratecmd import (
    CalibrateCommandError,
    CalibrationSurvey,
    gather,
    render_plan,
)
from git_loopy.calibration_search import (
    DEFAULT_SEARCH_BUDGET,
    PROMOTION_TRIALS,
    SearchBudget,
    SearchResult,
    SearchStop,
    search_price_staircase,
)
from git_loopy.config import (
    TASK_TYPE_KEYS,
    TASK_TYPE_LABEL_PREFIX,
    TaskTypeError,
    validate_task_type_key,
)
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
    ProvingTask,
    Rung,
    write_measured_routing,
)
from git_loopy.persist import make_run_id
from git_loopy.proving_admission import (
    AdmissionExclusion,
    AdmittedProvingSet,
    AdmittedProvingTask,
    CandidateVerifier,
    Verdict,
)
from git_loopy.proving_set import MinedProvingSet, ProvingCandidate
from git_loopy.release_version import read_runtime_release_version
from git_loopy.staircase import Candidate, PriceStaircase, render_pair
from git_loopy.trial_concurrency import (
    InlineTrialDispatcher,
    ThreadedTrialDispatcher,
    TrialDispatcher,
    TrialRequest,
    TrialResult,
    TrialRunner,
    resolve_trial_concurrency,
)

if TYPE_CHECKING:
    from git_loopy.gh import GitHubClient
    from git_loopy.git import GitClient

__all__ = [
    "INTERRUPTED_EXIT_CODE",
    "CalibrationOutcome",
    "ProgressReportingRunner",
    "RecordDecision",
    "TaskTypeCalibration",
    "TaskTypeSkip",
    "TrialHarness",
    "TrialProgress",
    "admit_enough",
    "calibrate",
    "eligible_task_types",
    "gate_loops_for",
    "merge_records",
    "provenance_for",
    "interactive_confirm",
    "record_for",
    "render_credits",
    "render_duration",
    "render_trial_progress",
    "run_calibrate",
]


def _default_out(line: str) -> None:
    print(line)


def _default_err(line: str) -> None:
    print(line, file=sys.stderr)


@dataclass(frozen=True)
class RecordDecision:
    """What one **Task type**'s search leaves in the artifact, or why it leaves nothing.

    Exactly one of the two is set, enforced here rather than promised: a decision
    carrying both would let a caller write a record *and* report that it had not,
    and a decision carrying neither would drop a search silently.
    """

    entry: MeasuredEntry | None = None
    unwritten_reason: str | None = None

    def __post_init__(self) -> None:
        if (self.entry is None) == (self.unwritten_reason is None):
            raise ValueError(
                "a record decision is either an entry or the reason there is none"
            )


def record_for(result: SearchResult) -> RecordDecision:
    """Turn one search's :class:`~git_loopy.calibration_search.SearchResult` into a record.

    Three outcomes, and the difference between them is the whole of *"a stopped
    search must look stopped"*:

    * A :attr:`~git_loopy.calibration_search.SearchStop.WINNER` becomes a
      ``measured`` record carrying the pair **and** the evidence that chose it —
      every rung walked and the **Proving tasks** measured (ADR-0028).
    * A ceiling, an interrupt or an exhausted staircase becomes an ``incomplete``
      record: where the walk got to, what it cost, what it measured, and no pair at
      all. The incumbent keeps routing because there is nothing to supersede it —
      and where the incumbent is itself a record in this artifact,
      :func:`records_to_write` is what keeps it there.
    * A search that ran no Trial writes nothing. ``stopped_at_rung = 0`` would
      record a Calibration that never started as one that started and stopped, and
      it would overwrite whatever the Task type's incumbent record said.

    A search whose **AI Credits** the harness never reported also writes nothing.
    :class:`~git_loopy.measured_routing.MeasuredEntry` requires a ``credits`` float
    on both record states and the artifact has no spelling for *unknown*, so the
    alternatives are a zero nobody can stand behind (ADR-0026 forbids it) or the
    incumbent kept with the reason stated. See this module's follow-up note in #372.
    """
    if not result.rungs:
        return RecordDecision(
            unwritten_reason=(
                f"the search stopped as {result.stop.value!r} before its first "
                f"Trial, so no Trial ran and there is nothing to record"
            )
        )
    if result.credits is None:
        return RecordDecision(
            unwritten_reason=(
                "the harness reported no AI Credits for at least one Trial, so this "
                "search's spend is unknown — and an unknown cost is unavailable, "
                "never zero (ADR-0026). The artifact records a credit figure on "
                "every record, so the incumbent is kept rather than stamped with a "
                "number nobody can stand behind"
            )
        )
    rungs = tuple(
        Rung(
            model=rung.candidate.model,
            effort=rung.candidate.effort or "",
            passed=rung.passed,
            total=rung.total,
            credits=float(rung.credits if rung.credits is not None else 0),
        )
        for rung in result.rungs
    )
    credits = float(result.credits)
    seconds = int(result.wall_clock_seconds)
    if result.stop is SearchStop.WINNER:
        winning = result.rungs[-1]
        return RecordDecision(
            entry=MeasuredEntry(
                status=MeasuredStatus.MEASURED,
                model=winning.candidate.model,
                effort=winning.candidate.effort or "",
                trials_passed=winning.passed,
                trials_total=winning.total,
                rungs_walked=len(result.rungs),
                credits=credits,
                wall_clock_seconds=seconds,
                rungs=rungs,
                proving_tasks=result.proving_tasks,
            )
        )
    return RecordDecision(
        entry=MeasuredEntry(
            status=MeasuredStatus.INCOMPLETE,
            stopped_at_rung=result.stopped_at_rung,
            rungs_available=result.rungs_available,
            credits=credits,
            wall_clock_seconds=seconds,
            rungs=rungs,
            proving_tasks=result.proving_tasks,
        )
    )


# --------------------------------------------------------------------------- #
# Which Task types get a search at all                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaskTypeSkip:
    """A **Task type** a Calibration would not measure, and why not."""

    task_type: str
    reason: str


def eligible_task_types(
    admitted: AdmittedProvingSet, *, task_types: Sequence[str] = TASK_TYPE_KEYS
) -> tuple[tuple[str, ...], tuple[TaskTypeSkip, ...]]:
    """Split ``task_types`` into the ones a search can promote from, and the rest.

    The bar is :data:`~git_loopy.calibration_search.PROMOTION_TRIALS` **admitted**
    tasks — admitted, not mined, because only admission (#380) establishes that a
    task fails before its fix and passes after, and a rung measured against work
    that was never runnable scores the corpus rather than the pair.

    Every ineligible Task type comes back as a :class:`TaskTypeSkip` rather than
    being dropped: a Task type silently omitted from the report reads as one that
    needed nothing, which is the opposite of what a shortfall means.
    """
    grouped = admitted.by_task_type()
    eligible: list[str] = []
    skipped: list[TaskTypeSkip] = []
    for key in task_types:
        count = len(grouped.get(key, ()))
        if count >= PROMOTION_TRIALS:
            eligible.append(key)
            continue
        skipped.append(TaskTypeSkip(task_type=key, reason=_shortfall(count)))
    return tuple(eligible), tuple(skipped)


def _shortfall(admitted: int) -> str:
    """Say what is missing, in the units an operator has to go and find."""
    return (
        f"a Calibration promotes only on {PROMOTION_TRIALS} unanimous Trials, so "
        f"this Task type needs {PROMOTION_TRIALS} admitted Proving tasks and "
        f"has {admitted} — {PROMOTION_TRIALS - admitted} short"
    )


# --------------------------------------------------------------------------- #
# The walk over the eligible Task types                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaskTypeCalibration:
    """One **Task type**'s search, and what it leaves in the artifact."""

    task_type: str
    result: SearchResult
    decision: RecordDecision


@dataclass(frozen=True)
class CalibrationOutcome:
    """Everything one ``git-loopy calibrate`` invocation measured and refused."""

    calibration_id: str
    calibrated: tuple[TaskTypeCalibration, ...] = ()
    skipped: tuple[TaskTypeSkip, ...] = ()
    interrupted: bool = False

    @property
    def records(self) -> dict[str, MeasuredEntry]:
        """The entries this Calibration *produced*, keyed by **Task type**.

        Only the searches that produced one: a refused search and a search whose
        spend is unknown both keep the incumbent, and keeping it means writing
        nothing over it.

        What of these actually reaches the artifact is
        :func:`records_to_write`'s decision, because that question needs the
        artifact already on disk and this record has never seen it.
        """
        return {
            item.task_type: item.decision.entry
            for item in self.calibrated
            if item.decision.entry is not None
        }


def calibrate(
    *,
    task_types: Sequence[str],
    staircase: PriceStaircase,
    admitted: AdmittedProvingSet,
    runner: TrialRunner,
    budget: SearchBudget,
    calibration_id: str,
    dispatcher: TrialDispatcher | None = None,
    on_task_type_start: Callable[[str, int, int], None] | None = None,
    on_task_type_end: Callable[[TaskTypeCalibration], None] | None = None,
    on_skip: Callable[[TaskTypeSkip], None] | None = None,
    on_trial: Callable[[TrialProgress], None] | None = None,
) -> CalibrationOutcome:
    """Run one search per eligible **Task type**, cheapest rung first.

    Args:
        task_types: The taxonomy keys this invocation was asked for — all seven
            for a bare ``calibrate``, exactly one for ``calibrate <task-type>``.
        staircase: The **price staircase**, already ordered by
            :mod:`git_loopy.staircase`. Every search walks the same one.
        admitted: The corpus. Each search measures only its own Task type's
            stratum, because a rung handed another Task type's work is not a
            measurement of this one.
        runner: The injected :class:`~git_loopy.trial_concurrency.TrialRunner`.
            Everything this function spends is spent behind it.
        budget: The ceilings, applied **per Task type**. A shared budget would let
            an expensive walk exhaust the credits the next Task type was going to
            be measured with, and the second would read as unmeasurable when it
            was merely second in a list.
        calibration_id: Identifies this Calibration, carried into the records the
            Trials emit and into the working branches they create.
        dispatcher: How many **Trials** run at once (#381). The search defaults it
            to serial.
        on_task_type_start: Called with ``(task_type, position, total)`` before a
            search starts, so a long Calibration does not look like a hang.
        on_task_type_end: Called with each completed :class:`TaskTypeCalibration`.
        on_skip: Called with each :class:`TaskTypeSkip` as it is decided.
        on_trial: Called with each :class:`TrialProgress` as a **Trial**
            completes, through :class:`ProgressReportingRunner`.

    Returns:
        The :class:`CalibrationOutcome`. An interrupted search ends the whole
        Calibration — Ctrl-C means *stop spending*, not *stop this Task type and
        start the next one* — and whatever was measured before it is kept.
    """
    eligible, skipped = eligible_task_types(admitted, task_types=task_types)
    if on_skip is not None:
        for skip in skipped:
            on_skip(skip)
    calibrated: list[TaskTypeCalibration] = []
    interrupted = False
    for position, key in enumerate(eligible, start=1):
        if on_task_type_start is not None:
            on_task_type_start(key, position, len(eligible))
        reporting: TrialRunner = runner
        if on_trial is not None:
            reporting = ProgressReportingRunner(
                runner,
                task_type=key,
                rungs=staircase.candidates,
                report=on_trial,
            )
        try:
            result = search_price_staircase(
                candidates=staircase.candidates,
                proving_set=admitted.pins_for(key),
                budget=budget,
                runner=reporting,
                dispatcher=dispatcher,
            )
        except KeyboardInterrupt:
            # The search catches its own interrupt and returns what it measured;
            # this catches one that lands between two searches, where there is no
            # part-walked rung to keep and the only thing left to do is stop.
            interrupted = True
            break
        item = TaskTypeCalibration(
            task_type=key, result=result, decision=record_for(result)
        )
        calibrated.append(item)
        if on_task_type_end is not None:
            on_task_type_end(item)
        if result.stop is SearchStop.INTERRUPTED:
            interrupted = True
            break
    return CalibrationOutcome(
        calibration_id=calibration_id,
        calibrated=tuple(calibrated),
        skipped=tuple(skipped),
        interrupted=interrupted,
    )


# --------------------------------------------------------------------------- #
# Progress: a long search must not look like a hang                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TrialProgress:
    """One **Trial** that has just completed, and where it sits in the walk."""

    task_type: str
    candidate: Candidate
    rung_position: int
    rungs_available: int
    trial_index: int
    trials_needed: int
    task: ProvingTask
    result: TrialResult


class ProgressReportingRunner:
    """A :class:`~git_loopy.trial_concurrency.TrialRunner` that narrates itself.

    Decorating the seam rather than threading a callback through
    :func:`~git_loopy.calibration_search.search_price_staircase` keeps the walk's
    rules in one place and gives serial and concurrent searches one reporter — the
    dispatcher already funnels every Trial through this method, whichever thread
    it runs on.

    The trial counter is per **rung**: a rung's Trials are the unit an operator is
    waiting on (five of five, or dead at the first), so restarting the count when
    the candidate changes is what makes *"Trial 3 of 5"* mean anything. Guarded by
    a lock because a threaded dispatcher calls this from ``width`` threads at once.
    """

    def __init__(
        self,
        inner: TrialRunner,
        *,
        task_type: str,
        rungs: Sequence[Candidate],
        report: Callable[[TrialProgress], None],
    ) -> None:
        self._inner = inner
        self._task_type = task_type
        self._rungs = tuple(rungs)
        self._report = report
        self._lock = threading.Lock()
        self._current: Candidate | None = None
        self._index = 0

    def run(self, request: TrialRequest) -> TrialResult:
        result = self._inner.run(request)
        with self._lock:
            if request.candidate != self._current:
                self._current = request.candidate
                self._index = 0
            self._index += 1
            index = self._index
        self._report(
            TrialProgress(
                task_type=self._task_type,
                candidate=request.candidate,
                rung_position=self._position(request.candidate),
                rungs_available=len(self._rungs),
                trial_index=index,
                trials_needed=PROMOTION_TRIALS,
                task=request.task,
                result=result,
            )
        )
        return result

    def _position(self, candidate: Candidate) -> int:
        """The candidate's 1-based place on the staircase, or ``0`` if it is not on it."""
        for position, rung in enumerate(self._rungs, start=1):
            if rung == candidate:
                return position
        return 0


def render_trial_progress(progress: TrialProgress) -> str:
    """One completed **Trial** as a line that survives a pipe.

    No cursor control and no colour: a Calibration runs for hours, so its output
    is at least as likely to be redirected into a log as watched on a terminal,
    and a progress display that only works on a TTY is the same as no progress
    display for the run that needed it most.
    """
    verdict = "passed" if progress.result.passed else "failed"
    detail = (
        ""
        if progress.result.passed or not progress.result.failure
        else f" — {progress.result.failure}"
    )
    return (
        f"  rung {progress.rung_position} of {progress.rungs_available} "
        f"{render_pair(progress.candidate.model, progress.candidate.effort)}: "
        f"Trial {progress.trial_index} of {progress.trials_needed} on "
        f"#{progress.task.issue} {verdict} "
        f"({render_credits(progress.result.credits)}, "
        f"{render_duration(progress.result.wall_clock_seconds)}){detail}"
    )


def render_credits(credits: Decimal | None) -> str:
    """**AI Credits**, with *unknown* spelled as itself (ADR-0026).

    Never ``0``: a Trial the harness did not bill may well have cost something,
    and a zero here would be a figure nobody can stand behind on the one surface
    an operator uses to decide whether to keep spending.
    """
    if credits is None:
        return "unknown AI Credits"
    return f"{credits} AI Credits"


def render_duration(seconds: float) -> str:
    """A duration as an operator reads a clock."""
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# --------------------------------------------------------------------------- #
# What reaches the committed artifact                                          #
# --------------------------------------------------------------------------- #


def gate_loops_for(
    admitted: AdmittedProvingSet, *, measured: Sequence[int]
) -> tuple[str, ...]:
    """The feedback loops that actually scored this Calibration, sorted.

    Taken from the **admitted** tasks that were measured, because each one's
    ``oracle_loops`` were selected from *its own base commit's* feedback-loop
    table — and ADR-0027 is explicit that the gate a replay runs is the one that
    commit declared. Stamping this repository's loops as they stand today would
    assert that strengthening the table improved a Calibration measured against a
    frozen corpus, which is the belief the ADR's refresh rule exists to correct.
    """
    issues = set(measured)
    loops: set[str] = set()
    for task in admitted.tasks:
        if task.issue in issues:
            loops.update(task.oracle_loops)
    return tuple(sorted(loops))


def provenance_for(
    *,
    staircase: PriceStaircase,
    admitted: AdmittedProvingSet,
    outcome: CalibrationOutcome,
    cli_version: str,
    calibrated_at: str,
) -> Provenance:
    """Stamp what this Calibration ran against (ADR-0028).

    The provenance sits on the thing it justifies, so *"has the roster moved?"* is
    the live roster against this stamp rather than a second file nobody updates.
    It is **file-scoped**, which a single-**Task type** re-measure inherits: the
    file was last written by this Calibration, so this Calibration's stamp is the
    true one for the file even where an older record survives beside it.
    """
    measured = [
        task.issue for item in outcome.calibrated for task in item.result.proving_tasks
    ]
    pin = admitted.classifier_pin
    return Provenance(
        cli_version=cli_version,
        calibrated_at=calibrated_at,
        candidate_count=len(staircase.candidates),
        gate_loops=gate_loops_for(admitted, measured=measured),
        classifier_model=None if pin is None else pin.model,
        classifier_effort=None if pin is None else (pin.effort or ""),
    )


@dataclass(frozen=True)
class KeptIncumbent:
    """A record this Calibration measured and deliberately did not write."""

    task_type: str
    reason: str


def records_to_write(
    existing: MeasuredRouting, outcome: CalibrationOutcome
) -> tuple[dict[str, MeasuredEntry], tuple[KeptIncumbent, ...]]:
    """Split this Calibration's records into the ones that may land, and the rest.

    One rule, in one place: **a record carrying no Routed pair never displaces one
    that carries a pair.** The artifact holds one record per **Task type**, so an
    ``incomplete`` written over a ``measured`` does not merely re-describe the Task
    type — it takes the pair out of the tier, and the next **Run** routes on
    **Config** instead. A ceiling, an interrupt and an exhausted staircase measured
    nothing about the pair already in force, so none of them may remove it; that is
    what *"the incumbent is kept"* means everywhere else in this module, and it is
    the operator-facing promise ``calibrate`` prints on exactly this path.

    Withheld is not the same as forgotten. The stopped walk's evidence lives in the
    Calibration's own event log (#371), and the record is reported through the same
    *nothing written* channel an unknown spend uses.

    The rule is about the **pair**, not the status: where the incumbent record
    supplies none — an earlier walk that stopped, or no record at all — the newer
    stop *is* the current state, and ADR-0028 stores current state only.
    """
    writable: dict[str, MeasuredEntry] = {}
    kept: list[KeptIncumbent] = []
    for key, entry in outcome.records.items():
        incumbent = existing.entries.get(key)
        if (
            entry.routed_pair is None
            and incumbent is not None
            and (pair := incumbent.routed_pair) is not None
        ):
            kept.append(KeptIncumbent(task_type=key, reason=_kept_reason(pair)))
            continue
        writable[key] = entry
    return writable, tuple(kept)


def _kept_reason(pair: tuple[str, str]) -> str:
    """Why one stopped search left the artifact's pair where it was."""
    return (
        f"this search published no winner, and the record it would write carries "
        f"no pair — writing it would have removed the "
        f"{render_pair(pair[0], pair[1])} already in force, which nothing this "
        f"Calibration measured disagreed with"
    )


def merge_records(
    existing: MeasuredRouting,
    outcome: CalibrationOutcome,
    provenance: Provenance,
) -> MeasuredRouting:
    """Fold this Calibration's records into the artifact already on disk.

    A fold rather than a rebuild, because the artifact is one file for the whole
    taxonomy: ``calibrate docs`` that rewrote it from its own outcomes would
    silently retract every **Task type** the operator did not ask about — a
    Calibration that cost nothing and deleted evidence. Which records may land is
    :func:`records_to_write`'s rule, so the artifact and the report cannot disagree
    about what changed.
    """
    entries = dict(existing.entries)
    writable, _kept = records_to_write(existing, outcome)
    entries.update(writable)
    return MeasuredRouting(entries=entries, provenance=provenance)


# --------------------------------------------------------------------------- #
# Admission: verify only as much of the corpus as a search will draw           #
# --------------------------------------------------------------------------- #


def admit_enough(
    mined: MinedProvingSet,
    verifier: CandidateVerifier,
    *,
    task_types: Sequence[str],
    needed: int = PROMOTION_TRIALS,
    on_verdict: Callable[[ProvingCandidate, Verdict], None] | None = None,
) -> AdmittedProvingSet:
    """Admit the newest candidates of each **Task type**, stopping at ``needed``.

    Admission spends no **AI Credit** and hours of wall clock: it checks out a
    commit and runs tests, once per candidate. A repository with two hundred
    closed ``bugfix`` issues would verify all two hundred to hand five to the
    search, so the walk stops as soon as a Task type has as many as a search can
    draw.

    **Newest-first, because that is the order the search draws in**
    (:func:`~git_loopy.calibration_search.tasks_for_every_rung`). Admitting in a
    different order would verify one set of tasks and measure another — the plan
    an operator confirmed would not be the work they paid for.
    """
    grouped = mined.by_task_type()
    tasks: list[AdmittedProvingTask] = []
    exclusions: list[AdmissionExclusion] = []
    for key in task_types:
        newest_first = sorted(
            grouped.get(key, ()), key=lambda candidate: candidate.issue, reverse=True
        )
        admitted = 0
        for candidate in newest_first:
            if admitted >= needed:
                break
            verdict = verifier.verify(candidate)
            if isinstance(verdict, AdmittedProvingTask):
                tasks.append(verdict)
                admitted += 1
            else:
                exclusions.append(verdict)
            if on_verdict is not None:
                on_verdict(candidate, verdict)
    return AdmittedProvingSet(
        tasks=tuple(tasks),
        exclusions=tuple(exclusions),
        classifier_pin=mined.classifier_pin,
    )


# --------------------------------------------------------------------------- #
# `git-loopy calibrate` — the command surface                                  #
# --------------------------------------------------------------------------- #


#: How a caller runs the **Trials**: it is handed the admitted corpus and this
#: Calibration's id, and must call the body with a live
#: :class:`~git_loopy.trial_concurrency.TrialRunner`, returning whatever the body
#: returned. Inverted rather than a plain factory because the production runner
#: needs a *started* harness client and must stop it again — a lifetime, not an
#: object — and because that lifetime is asynchronous while the search is not.
TrialHarness = Callable[
    [AdmittedProvingSet, str, Callable[[TrialRunner], CalibrationOutcome]],
    CalibrationOutcome,
]

#: What ``calibrate`` exits with when the operator interrupted it. The shell's
#: SIGINT convention rather than the Wrapper contract's codes: a Calibration is
#: not a **Run**, and an operator who pressed Ctrl-C has not hit a preflight
#: failure or a usage error.
INTERRUPTED_EXIT_CODE = 130


def run_calibrate(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    task_type: str | None = None,
    assume_yes: bool = False,
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
    github: "GitHubClient | None" = None,
    git: "GitClient | None" = None,
    fetch_staircase: Callable[[Callable[[str], None]], PriceStaircase] | None = None,
    budget: SearchBudget = DEFAULT_SEARCH_BUDGET,
    verifier: CandidateVerifier | None = None,
    harness: TrialHarness | None = None,
    confirm: Callable[[str], bool] | None = None,
    calibration_id: str | None = None,
    now: Callable[[], str] | None = None,
) -> int:
    """Measure the eligible **Task types**, and write what was measured.

    The only path in this distribution that runs a **Trial**. Every step before
    the first one is a chance to stop: the plan is printed in full, and an
    interactive operator has to say yes.

    **Parallelism is not one of those steps any more** (ADR-0037). A Calibration
    used to refuse a serial run outright, because a **Routed pair** took effect
    only in **Parallel mode** and the measurement would have bought nothing. The
    pair now applies at the **Pickup** of whatever works the issue, so the
    measurement is worth the same **AI Credits** at either width and the knob is
    not read here at all.
    """
    try:
        keys = _requested_task_types(task_type)
        _root, found = gather(repo_root, err, github, git, fetch_staircase)
    except CalibrateCommandError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    root = _root

    if not found.staircase.available:
        err(f"git-loopy: error: {found.staircase.reason()}")
        return 1

    candidates = [key for key in keys if found.shortfall(key) == 0]
    if not candidates:
        err(
            "git-loopy: error: no Task type has the "
            f"{PROMOTION_TRIALS} replayable Proving tasks a Calibration promotes "
            "on, so there is nothing to measure. `git-loopy calibrate --status` "
            "lists the closed issues that would grow the corpus."
        )
        return 1

    out(
        f"Calibration plan — {len(candidates)} Task type(s): "
        + ", ".join(f"{TASK_TYPE_LABEL_PREFIX}{key}" for key in candidates)
    )
    out("")
    render_plan(found, budget=budget, env=env, out=out)
    out("")
    if not _confirmed(assume_yes, confirm, out, err):
        return 0 if assume_yes or confirm is not None else 1

    identifier = calibration_id if calibration_id is not None else make_run_id()
    out(
        f"Calibration {identifier} starting. Ctrl-C stops it and keeps what it measured."
    )
    out("")
    resolved_verifier = (
        verifier if verifier is not None else _live_verifier(root, identifier)
    )
    try:
        admitted = admit_enough(
            found.mined,
            resolved_verifier,
            task_types=candidates,
            on_verdict=lambda candidate, verdict: out(
                _render_verdict(candidate, verdict)
            ),
        )
    except KeyboardInterrupt:
        out("")
        out(
            "Interrupted during admission. Nothing was measured and nothing was written."
        )
        return INTERRUPTED_EXIT_CODE

    dispatcher = _dispatcher_for(env)
    run_harness = harness if harness is not None else _live_harness(root, found, env)
    outcome = run_harness(
        admitted,
        identifier,
        lambda runner: calibrate(
            task_types=candidates,
            staircase=found.staircase,
            admitted=admitted,
            runner=runner,
            budget=budget,
            calibration_id=identifier,
            dispatcher=dispatcher,
            on_task_type_start=lambda key, position, total: out(
                f"{TASK_TYPE_LABEL_PREFIX}{key} ({position} of {total})"
            ),
            on_task_type_end=lambda item: out(_render_task_type_end(item)),
            on_skip=lambda skip: out(
                f"{TASK_TYPE_LABEL_PREFIX}{skip.task_type}: skipped — {skip.reason}"
            ),
            on_trial=lambda progress: out(render_trial_progress(progress)),
        ),
    )

    out("")
    _write_outcome(
        root,
        found,
        admitted,
        outcome,
        out=out,
        now=now if now is not None else _utc_now,
    )
    return INTERRUPTED_EXIT_CODE if outcome.interrupted else 0


def _requested_task_types(task_type: str | None) -> tuple[str, ...]:
    """The taxonomy keys this invocation asked for.

    ``None`` is every key: a bare ``git-loopy calibrate`` measures the whole
    taxonomy. One key is validated against the closed taxonomy (ADR-0029) rather
    than searched for silently, because a typo that measured nothing would look
    exactly like a corpus that supports nothing.
    """
    if task_type is None:
        return tuple(TASK_TYPE_KEYS)
    try:
        validate_task_type_key(task_type)
    except TaskTypeError as exc:
        raise CalibrateCommandError(str(exc)) from None
    return (task_type,)


def _confirmed(
    assume_yes: bool,
    confirm: Callable[[str], bool] | None,
    out: Callable[[str], None],
    err: Callable[[str], None],
) -> bool:
    """Whether a human has asked for this spend (ADR-0027).

    Three cases and no fourth. ``--yes`` is the human asking in advance. An
    interactive terminal is asked. A non-interactive one is **refused**: an
    operator who piped ``calibrate`` into a log still typed the command, but a
    script that inherited it did not, and the difference is not observable from
    here — so the spend needs the flag that says it out loud.
    """
    if assume_yes:
        return True
    if confirm is None:
        err(
            "git-loopy: error: a Calibration spends AI Credits, so it is confirmed "
            "before it starts. This terminal is not interactive; re-run with --yes "
            "to confirm the plan above."
        )
        return False
    if confirm("Run this Calibration?"):
        return True
    out("Declined. No Trial ran, no worktree was created and no AI Credit was spent.")
    return False


def _render_verdict(candidate: ProvingCandidate, verdict: Verdict) -> str:
    """One admitted or refused **Proving task**, as it lands."""
    if isinstance(verdict, AdmittedProvingTask):
        return (
            f"  admitted #{candidate.issue} ({TASK_TYPE_LABEL_PREFIX}"
            f"{candidate.task_type}) — oracle {', '.join(verdict.oracle_loops)} "
            f"red at the base commit"
        )
    detail = f" — {verdict.detail}" if verdict.detail else ""
    return f"  refused #{candidate.issue}: {verdict.reason.value}{detail}"


def _render_task_type_end(item: TaskTypeCalibration) -> str:
    """One **Task type**'s verdict, in the vocabulary of what it changes."""
    result = item.result
    label = f"{TASK_TYPE_LABEL_PREFIX}{item.task_type}"
    if result.stop is SearchStop.WINNER and result.winner is not None:
        return (
            f"  {label}: {render_pair(result.winner.model, result.winner.effort)} "
            f"promoted on {PROMOTION_TRIALS} of {PROMOTION_TRIALS} Trials "
            f"({render_credits(result.credits)}, "
            f"{render_duration(result.wall_clock_seconds)})"
        )
    return (
        f"  {label}: no winner — {_stop_reason(result.stop)}, stopped at rung "
        f"{result.stopped_at_rung} of {result.rungs_available} "
        f"({render_credits(result.credits)}, "
        f"{render_duration(result.wall_clock_seconds)}). The incumbent is kept."
    )


def _stop_reason(stop: SearchStop) -> str:
    """Name the thing that ended the walk, in the operator's terms.

    A ceiling is named as *which* ceiling, because the two have different
    remedies: more credits, or more hours.
    """
    return {
        SearchStop.CREDIT_CEILING: "the AI Credit ceiling stopped it",
        SearchStop.WALL_CLOCK_CEILING: "the wall-clock ceiling stopped it",
        SearchStop.STAIRCASE_EXHAUSTED: "every rung was walked and none was unanimous",
        SearchStop.INTERRUPTED: "the operator interrupted it",
        SearchStop.INSUFFICIENT_PROVING_SET: "the Proving set is too small to promote on",
    }[stop]


def _write_outcome(
    root: Path,
    found: "CalibrationSurvey",
    admitted: AdmittedProvingSet,
    outcome: CalibrationOutcome,
    *,
    out: Callable[[str], None],
    now: Callable[[], str],
) -> None:
    """Fold the outcome into the artifact, or say why nothing was written.

    Written **once**, at the end, because the artifact is a tracked file: a
    Calibration that committed after every Task type would be several diffs where
    the operator asked for one, and ADR-0028's whole reason for committing it is
    that the change arrives as a pull-request diff a human can read and revert.
    """
    for item in outcome.calibrated:
        if item.decision.unwritten_reason is not None:
            out(
                f"{TASK_TYPE_LABEL_PREFIX}{item.task_type}: nothing written — "
                f"{item.decision.unwritten_reason}."
            )
    writable, kept = records_to_write(found.artifact, outcome)
    for incumbent in kept:
        out(
            f"{TASK_TYPE_LABEL_PREFIX}{incumbent.task_type}: nothing written — "
            f"{incumbent.reason}."
        )
    if outcome.interrupted:
        out("Interrupted. This Calibration is unfinished and published no winner.")
    if not writable:
        out("Nothing was written: the measured routing artifact is unchanged.")
        return
    merged = merge_records(
        found.artifact,
        outcome,
        provenance_for(
            staircase=found.staircase,
            admitted=admitted,
            outcome=outcome,
            cli_version=read_runtime_release_version(),
            calibrated_at=now(),
        ),
    )
    write_measured_routing(root, merged)
    out(f"Wrote {found.artifact_path}.")
    out(
        "  Review it as you would any other diff: `git diff` it, question it, "
        "and `git revert` the commit if you disagree."
    )


def _utc_now() -> str:
    """The Calibration's timestamp, in the artifact's own spelling."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dispatcher_for(env: Mapping[str, str]) -> TrialDispatcher:
    """How many **Trials** run at once, from the operator's own host setting (#381)."""
    resolved = resolve_trial_concurrency(env=env, ceiling=PROMOTION_TRIALS)
    if resolved.serial:
        return InlineTrialDispatcher(1)
    return ThreadedTrialDispatcher(resolved.effective)


def interactive_confirm(prompt: str) -> bool:
    """Ask on a real terminal, and take silence as no.

    ``EOFError`` is a stdin that closed under us, which is the shape a background
    job takes — and the safe reading of it is *not confirmed*.
    """
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


# --------------------------------------------------------------------------- #
# The production seams — the only things here that touch a worktree or the SDK #
# --------------------------------------------------------------------------- #


def _live_verifier(root: Path, calibration_id: str) -> CandidateVerifier:
    """The real admission replay: a worktree at a real commit, running real tests."""
    from git_loopy import git as git_module
    from git_loopy.proving_admission import ReplayVerifier

    return ReplayVerifier(
        git=git_module.SubprocessGitClient(root),
        admission_id=calibration_id,
        warn=lambda message: print(f"git-loopy: warning: {message}", file=sys.stderr),
    )


def _live_harness(
    root: Path, found: CalibrationSurvey, env: Mapping[str, str]
) -> TrialHarness:
    """The real **Trial** runner, for as long as the search needs it.

    A lifetime rather than a factory: the harness client has to be *started* and
    stopped again, and both are asynchronous while the search is not — so the
    whole walk runs inside one :func:`asyncio.run`, and
    :func:`git_loopy.trial._await` drives each session from there.

    A Calibration is **not a Run**, so this builds an event log and nothing else:
    no run-summary writer, no Iteration numbers, no **Strike** machine. The
    records the Trials write name the Calibration and are unattributable to any
    Run (#371).
    """

    def harness(
        admitted: AdmittedProvingSet,
        calibration_id: str,
        body: Callable[[TrialRunner], CalibrationOutcome],
    ) -> CalibrationOutcome:
        import asyncio

        from git_loopy import git as git_module
        from git_loopy.loop import _make_client
        from git_loopy.persist import EventLogWriter, ensure_gitignore_entry
        from git_loopy.sinks import SinkFanout
        from git_loopy.trial import ReplayTrialRunner

        ensure_gitignore_entry(root)
        log_path = root / ".git-loopy" / "logs" / f"calibration-{calibration_id}.jsonl"

        async def _main() -> CalibrationOutcome:
            client = _make_client()
            await client.start()
            try:
                with EventLogWriter(log_path) as event_log:
                    runner = ReplayTrialRunner(
                        git=git_module.SubprocessGitClient(root),
                        candidates=admitted.candidates(),
                        client=client,
                        config=_CalibrationSessionConfig(),
                        event_log=event_log,
                        sinks=SinkFanout([]),
                        calibration_id=calibration_id,
                        warn=lambda message: print(
                            f"git-loopy: warning: {message}", file=sys.stderr
                        ),
                    )
                    return body(runner)
            finally:
                await client.stop()

        return asyncio.run(_main())

    return harness


@dataclass(frozen=True)
class _CalibrationSessionConfig:
    """The session settings a **Trial** runs under.

    Deliberately the permissive defaults rather than the operator's Run config: a
    Trial measures a *pair*, and a deny-list applied here would measure the
    deny-list instead — and would measure it differently for an operator who had
    one than for an operator who did not, which is the one thing a shared
    **Measured routing** artifact must not do.
    """

    deny_tools: frozenset[str] = frozenset()
    deny_skills: frozenset[str] = frozenset()
    verbosity: int = 1
    render_reasoning: bool = False
