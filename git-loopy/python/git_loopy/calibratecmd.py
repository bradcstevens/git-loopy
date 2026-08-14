"""``git_loopy.calibratecmd`` — ``git-loopy calibrate --status`` / ``--dry-run`` (#367).

Everything an operator can ask about a **Calibration** *before* it spends
anything. The spending path is a separate command surface (#372); this module
ships the two modes that answer questions and change nothing.

* ``calibrate --status``  — *what does my corpus support?* Per **Task type**: the
  current **Routed pair** and the tier that supplied it, how many replayable
  **Proving tasks** exist, and whether the live roster has moved in the one way
  that could change an answer.
* ``calibrate --dry-run`` — *what would it cost?* The candidate staircase, the
  Proving set it would measure against, both ceilings, and the maximum number of
  **Trials** it may run.

Design notes:

* **Nothing here spends.** No agent session, no worktree, no **AI Credit**, no
  label. That is not a convention: the module imports nothing that could do any
  of it, and ``tests/test_calibrate_cmd.py`` holds the import list. A command
  whose whole contract is that an operator can decline before committing has to
  be structurally incapable of committing.
* **It reports the corpus; it never grows one.** ``--status`` surfaces the closed
  issues that qualify on every rule except the ``task-type:`` label, because that
  list is what an operator needs to judge whether the corpus can be grown — and
  it applies no label and calls no **Task-type classifier**. Inference belongs to
  the classifier's own path (#377, #378), not to a surface that changes nothing.
* **A thin answer is worse than no answer** (ADR-0027). A Task type with fewer
  than :data:`~git_loopy.calibration_search.PROMOTION_TRIALS` replayable tasks is
  *refused*, naming exactly what is missing, rather than reported as a smaller
  number somebody might read as measurable.
* **Mined is not admitted.** Admission (#380) is the only pass that can establish
  that a task fails before its fix and passes after; until it lands every count
  here is an upper bound and says so in the same breath as the number.
* **Injectable, like every other command surface.** ``out`` / ``err`` sinks, an
  injected ``repo_root`` + ``env``, and injected tracker / git / staircase seams,
  so no test touches a real tracker, a real harness or the network.
* **AI Credits throughout.** ADR-0026's denomination: no USD figure appears
  anywhere in this module's output.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

from git_loopy import measured_routing, proving_set as proving_set_module, settings
from git_loopy.calibration_search import (
    DEFAULT_SEARCH_BUDGET,
    PROMOTION_TRIALS,
    SearchBudget,
    maximum_trials,
    tasks_for_every_rung,
)
from git_loopy.config import TASK_TYPE_KEYS, TASK_TYPE_LABEL_PREFIX
from git_loopy.measured_routing import MeasuredRouting, MeasuredStatus, ProvingTask
from git_loopy.proving_set import MinedProvingSet
from git_loopy.roster_drift import compare_classifier_pin, compare_roster_to_measured
from git_loopy.staircase import PriceStaircase, render_pair
from git_loopy.task_type_classifier import ClassifierPair, resolve_classifier_pair
from git_loopy.trial_concurrency import CONCURRENCY_ENV, resolve_trial_concurrency

if TYPE_CHECKING:
    from git_loopy.cli import ResolvedConfig
    from git_loopy.gh import GitHubClient
    from git_loopy.git import GitClient

__all__ = [
    "CalibrateCommandError",
    "CalibrationSurvey",
    "gather",
    "render_plan",
    "run_calibrate_status",
    "run_calibrate_dry_run",
    "survey",
]


class CalibrateCommandError(Exception):
    """A user-facing ``git-loopy calibrate`` failure.

    Carries a clean, prefix-free message; the ``run_*`` wrappers render it with
    the kit's ``git-loopy: error:`` prefix and return a non-zero exit code.
    """


def _default_out(line: str) -> None:
    print(line)


def _default_err(line: str) -> None:
    print(line, file=sys.stderr)


# ---------------------------------------------------------------------------
# The survey — one read of everything both modes report on.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationSurvey:
    """What both non-spending modes read, gathered once.

    One survey rather than two collections, because ``--status`` and
    ``--dry-run`` are two renderings of the same facts and a repository that
    answered them differently would be reporting a race rather than a state.
    """

    artifact: MeasuredRouting
    artifact_path: Path
    staircase: PriceStaircase
    mined: MinedProvingSet
    labelling: tuple[int, ...]
    classifier: ClassifierPair | None
    closed_issues_read: int
    closed_issues_complete: bool

    def candidates_for(self, task_type: str) -> tuple[ProvingTask, ...]:
        """The **Proving tasks** a search over ``task_type`` would measure, in its order.

        Read through the search's own
        :func:`~git_loopy.calibration_search.tasks_for_every_rung`, so a dry run
        cannot promise work the search would not actually draw.
        """
        pinned = [
            candidate.pin()
            for candidate in self.mined.by_task_type().get(task_type, ())
        ]
        return tasks_for_every_rung(pinned)

    def mined_count(self, task_type: str) -> int:
        """How many mined candidates this **Task type** has."""
        return len(self.mined.by_task_type().get(task_type, ()))

    def shortfall(self, task_type: str) -> int:
        """How many more replayable tasks this Task type needs to be calibratable."""
        return max(0, PROMOTION_TRIALS - self.mined_count(task_type))


def survey(
    *,
    repo_root: Path,
    github: "GitHubClient",
    git: "GitClient",
    staircase: PriceStaircase,
) -> CalibrationSurvey:
    """Read the artifact, the roster and the closed backlog, once.

    Pure of policy: it decides nothing about what the numbers *mean*, which is
    the renderers' job below. Mining reads metadata only — no checkout, no test
    run, no credit (see :mod:`git_loopy.proving_set`).
    """
    artifact_path = measured_routing.measured_routing_path(repo_root)
    artifact = measured_routing.load_measured_routing(artifact_path)
    classifier = resolve_classifier_pair(staircase)
    pin = classifier if isinstance(classifier, ClassifierPair) else None
    page = proving_set_module.closed_issues(github)
    default_branch = github.repo_view().default_branch
    return CalibrationSurvey(
        artifact=artifact,
        artifact_path=artifact_path,
        staircase=staircase,
        mined=proving_set_module.mine_proving_set(
            page.issues, git, default_branch=default_branch, classifier_pin=pin
        ),
        labelling=proving_set_module.labelling_candidates(
            page.issues, git, default_branch=default_branch
        ),
        classifier=pin,
        closed_issues_read=len(page.issues),
        closed_issues_complete=page.complete,
    )


# ---------------------------------------------------------------------------
# `calibrate --status` — what does my corpus support?
# ---------------------------------------------------------------------------


def run_calibrate_status(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
    github: "GitHubClient | None" = None,
    git: "GitClient | None" = None,
    fetch_staircase: Callable[[Callable[[str], None]], PriceStaircase] | None = None,
) -> int:
    """Report what a **Calibration** would have to work with, and spend nothing."""
    try:
        gathered = gather(repo_root, err, github, git, fetch_staircase)
    except CalibrateCommandError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    root, found = gathered
    _report_artifact(found, out)
    _report_classifier(found, out)
    _report_corpus(found, out, err)
    out("")
    resolved = _resolve_routing(root, env, err)
    for key in TASK_TYPE_KEYS:
        _report_task_type(found, key, resolved, out)
    _report_exclusions(found, out)
    _report_labelling(found, out)
    return 0


def _report_artifact(found: CalibrationSurvey, out: Callable[[str], None]) -> None:
    """State whether anything here was measured at all.

    A repository with no artifact is not a repository with a bad one, and the
    difference matters more than any other line in the report: every pair below
    it is a *recommendation*, and reading recommendations as measurements is the
    exact confusion the measured tier exists to end (ADR-0028).
    """
    provenance = found.artifact.provenance
    if provenance is None:
        absent = not found.artifact_path.exists()
        out(
            f"Measured routing: no Calibration has run — {found.artifact_path} "
            + ("does not exist." if absent else "records no Calibration.")
        )
        out(
            "  Every Routed pair below is unmeasured. The built-in defaults are "
            "recommendations, not measurements."
        )
        return
    out(
        f"Measured routing: calibrated {provenance.calibrated_at} by git-loopy "
        f"{provenance.cli_version}, over {provenance.candidate_count} candidate pairs."
    )


def _report_classifier(found: CalibrationSurvey, out: Callable[[str], None]) -> None:
    """Name the pin, because a pin change is a **Proving set** refresh trigger.

    The **Task-type classifier** runs on the cheapest rung of the live roster, so
    it moves when the roster does — and a taxonomy that drifts underneath a
    deliberately frozen corpus is what ADR-0028's refresh rule exists to catch.
    Reporting it is the only way an operator can see that it has moved.
    """
    if found.classifier is None:
        out(
            "Task-type classifier: no pair pinned — "
            f"{found.staircase.reason()}."
        )
        return
    out(
        f"Task-type classifier: "
        f"{render_pair(found.classifier.model, found.classifier.effort)} — the "
        f"cheapest rung of the live roster. A change here is a Proving set "
        f"refresh trigger."
    )
    # Through the same comparison Run preflight warns on (#370), so the report
    # an operator is sent to cannot say the taxonomy held while the Run that
    # sent them there said it moved.
    pin = compare_classifier_pin(found.artifact.provenance, found.classifier)
    if pin.diverged:
        out(f"  Pin: {pin.reason()}.")


def _report_corpus(
    found: CalibrationSurvey, out: Callable[[str], None], err: Callable[[str], None]
) -> None:
    """The corpus totals, with the word ``mined`` attached to every one of them."""
    out(
        f"Proving set: {len(found.mined.candidates)} mined candidates from "
        f"{found.closed_issues_read} closed issues. {_MINED_NOT_ADMITTED}"
    )
    if not found.closed_issues_complete:
        err(
            "git-loopy: warning: the closed-issue read hit its ceiling, so this "
            "corpus has holes in it and every count below is a floor as well as "
            "an upper bound."
        )


#: The sentence that must accompany every count this module prints. Mining reads
#: metadata and cannot tell whether a task genuinely fails before its fix and
#: passes after — only admission (#380) can — so a bare number here would read as
#: a verified corpus and be wrong in the direction that matters.
_MINED_NOT_ADMITTED = (
    "These are mined candidates, not admitted Proving tasks: admission has not "
    "run, so each count is an upper bound."
)


def _resolve_routing(
    repo_root: Path, env: Mapping[str, str], err: Callable[[str], None]
) -> "ResolvedConfig":
    """The effective config, resolved once for the whole report.

    Through :mod:`git_loopy.configcmd`'s own seam rather than a second walk of
    the precedence chain: ``config get task-type:<key>`` and ``calibrate
    --status`` must not be able to name different tiers for the same key.
    """
    from git_loopy import configcmd

    return configcmd.resolve_effective_config(
        repo_root, env, warn=lambda message: err(f"git-loopy: warning: {message}")
    )


def _report_task_type(
    found: CalibrationSurvey,
    key: str,
    resolved: "ResolvedConfig",
    out: Callable[[str], None],
) -> None:
    """One **Task type**'s block: its pair, its corpus, and the roster against it."""
    from git_loopy import configcmd

    pair, tier = configcmd.routing_report(resolved, key)
    out(f"{TASK_TYPE_LABEL_PREFIX}{key}")
    out(f"  Routed pair: {pair} ({tier})")
    mined = found.mined_count(key)
    issues = ", ".join(
        f"#{candidate.issue}"
        for candidate in found.mined.by_task_type().get(key, ())
    )
    out(f"  Proving tasks: {mined} mined candidate(s){f' — {issues}' if issues else ''}")
    shortfall = found.shortfall(key)
    if shortfall:
        out(f"  Refused: {_shortfall_reason(mined, shortfall)}")
    entry = found.artifact.entries.get(key)
    if entry is None:
        return
    if entry.status is not MeasuredStatus.MEASURED:
        out(f"  Artifact record: {entry.status.value} — this pair was never measured.")
    comparison = compare_roster_to_measured(entry, found.staircase)
    if comparison.diverged:
        out(f"  Roster: {comparison.reason()}.")


def _shortfall_reason(mined: int, shortfall: int) -> str:
    """Say what is missing, in the units the operator has to go and find.

    ADR-0027 promotes on :data:`PROMOTION_TRIALS` *unanimous* Trials, so a Task
    type with fewer tasks than that cannot produce a winner at all — measuring
    three of three and promoting on it would read the unanimity rule as
    *"everything available passed"*, a bar the ADR never set. Refusing with the
    shortfall is what turns "cannot calibrate" into something actionable.
    """
    return (
        f"a Calibration promotes only on {PROMOTION_TRIALS} unanimous Trials, so "
        f"this Task type needs {PROMOTION_TRIALS} replayable Proving tasks and "
        f"has {mined} — {shortfall} short."
    )


def _report_exclusions(found: CalibrationSurvey, out: Callable[[str], None]) -> None:
    """Every excluded closed issue, under the rule it failed.

    Reported rather than counted (#303's discipline): a number tells a loop
    engineer that the corpus is partial and nothing at all about whether it is
    representative.
    """
    if not found.mined.exclusions:
        return
    grouped: dict[str, list[int]] = {}
    for exclusion in found.mined.exclusions:
        grouped.setdefault(exclusion.reason.value, []).append(exclusion.issue)
    out("")
    out("Excluded closed issues, by the rule each failed:")
    for reason in sorted(grouped):
        numbers = ", ".join(f"#{number}" for number in sorted(grouped[reason]))
        out(f"  {reason} ({len(grouped[reason])}): {numbers}")


def _report_labelling(found: CalibrationSurvey, out: Callable[[str], None]) -> None:
    """The closed issues that would qualify if somebody labelled them.

    The corpus-growth question, and the reason it is worth its own list: the
    ``no_task_type`` exclusions above include issues that fail a *second* rule
    too, and sending an operator to label those wastes them.
    """
    if not found.labelling:
        return
    numbers = ", ".join(f"#{number}" for number in found.labelling)
    out("")
    out(
        f"Closed issues that qualify on every rule except the "
        f"{TASK_TYPE_LABEL_PREFIX[:-1]}: label ({len(found.labelling)}): {numbers}"
    )
    out(
        "  Labelling these would grow the Proving set. `calibrate` reports them "
        "and applies no label; inference is the Task-type classifier's own path."
    )


# ---------------------------------------------------------------------------
# `calibrate --dry-run` — what would it cost?
# ---------------------------------------------------------------------------


def run_calibrate_dry_run(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
    github: "GitHubClient | None" = None,
    git: "GitClient | None" = None,
    fetch_staircase: Callable[[Callable[[str], None]], PriceStaircase] | None = None,
    budget: SearchBudget = DEFAULT_SEARCH_BUDGET,
) -> int:
    """Report what a **Calibration** would do, and spend nothing doing it."""
    try:
        gathered = gather(repo_root, err, github, git, fetch_staircase)
    except CalibrateCommandError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    _root, found = gathered
    out(
        "Dry run: no Trial is run, no session is spawned, no worktree is created "
        "and no AI Credit is consumed."
    )
    out("")
    render_plan(found, budget=budget, env=env, out=out)
    return 0


def render_plan(
    found: CalibrationSurvey,
    *,
    budget: SearchBudget,
    env: Mapping[str, str],
    out: Callable[[str], None],
) -> None:
    """The plan: the staircase, both ceilings, the concurrency and the corpus.

    Public and shared, because ``--dry-run`` and the spending path (#372) must
    print the *same* plan. A dry run that promised work the spend would not do is
    the one failure a dry run can have, and two renderers is how it happens: they
    would drift a line at a time and nobody would see it, because the two outputs
    are never read side by side.
    """
    _report_staircase(found, out)
    out("")
    _report_ceilings(found, budget, out)
    out("")
    _report_concurrency(env, out)
    out("")
    _report_plan(found, out)


def _report_staircase(found: CalibrationSurvey, out: Callable[[str], None]) -> None:
    """The rungs a search would climb, in the order it would climb them."""
    if not found.staircase.available:
        out(f"Price staircase: none — {found.staircase.reason()}.")
        return
    rungs = found.staircase.candidates
    out(f"Price staircase ({len(rungs)} rungs, cheapest first):")
    for position, candidate in enumerate(rungs, start=1):
        out(
            f"  {position}. {render_pair(candidate.model, candidate.effort)} "
            f"(premium multiplier {candidate.multiplier})"
        )


def _report_ceilings(
    found: CalibrationSurvey, budget: SearchBudget, out: Callable[[str], None]
) -> None:
    """Both ceilings, per **Task type**, and the Trial count they bound.

    Wall clock is printed beside credits rather than behind them because it is
    the ceiling that actually binds (ADR-0027): the gate costs the same time on
    every rung regardless of the model under test — it is compilers and test
    suites, not tokens — so a Calibration is bounded by hours long before it is
    bounded by credits.
    """
    rungs = len(found.staircase.candidates)
    out("Ceilings, per Task type:")
    out(f"  AI Credits: {budget.credit_ceiling}")
    out(
        f"  Wall clock: {_render_duration(budget.wall_clock_ceiling_seconds)} "
        "(elapsed, so Trials that run together spend it once)"
    )
    out(
        f"  Maximum Trials: {maximum_trials(rungs_available=rungs)} "
        f"({rungs} rungs x {PROMOTION_TRIALS} Proving tasks)"
    )


def _report_concurrency(env: Mapping[str, str], out: Callable[[str], None]) -> None:
    """How many **Trials** would run at once, and which setting said so (#381).

    Printed before anything is spent because concurrency is the one plan input
    an operator's *host* decides rather than this repository — and because it
    changes what a Calibration costs in hours without changing what it costs in
    credits, which is the trade they are being asked to make.
    """
    resolved = resolve_trial_concurrency(env=env, ceiling=PROMOTION_TRIALS)
    out("Concurrency:")
    if resolved.serial:
        out("  1 Trial at a time (serial).")
        out(
            f"  Set {CONCURRENCY_ENV}=N to run N Trials at once, each in its own "
            f"worktree. Wall clock, not spend, is what limits a Calibration."
        )
        return
    out(f"  {resolved.effective} Trials at a time, each in its own worktree.")
    out(f"  From {resolved.source}.")
    if resolved.capped:
        out(
            f"  Capped from {resolved.requested}: a rung buys {PROMOTION_TRIALS} "
            f"Trials, so a wider host has nothing more to run."
        )
    out(
        "  A rung's first Trial is a probe run alone, so a rung that fails costs "
        "what it costs serially."
    )


def _report_plan(found: CalibrationSurvey, out: Callable[[str], None]) -> None:
    """Per **Task type**: the work a search would measure, or why it cannot."""
    out(f"Proving set per Task type. {_MINED_NOT_ADMITTED}")
    calibratable = 0
    for key in TASK_TYPE_KEYS:
        shortfall = found.shortfall(key)
        if shortfall:
            out(
                f"  {TASK_TYPE_LABEL_PREFIX}{key}: refused — "
                f"{_shortfall_reason(found.mined_count(key), shortfall)}"
            )
            continue
        calibratable += 1
        drawn = ", ".join(f"#{task.issue}" for task in found.candidates_for(key))
        out(
            f"  {TASK_TYPE_LABEL_PREFIX}{key}: would measure "
            f"{PROMOTION_TRIALS} Proving tasks — {drawn}"
        )
    rungs = len(found.staircase.candidates)
    out("")
    out(
        f"Calibratable Task types: {calibratable} of {len(TASK_TYPE_KEYS)} — at "
        f"most {calibratable * maximum_trials(rungs_available=rungs)} Trials in total."
    )


def _render_duration(seconds: float) -> str:
    """A ceiling as an operator reads a clock, not as a number of seconds."""
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# Shared preflight
# ---------------------------------------------------------------------------


def gather(
    repo_root: Path | None,
    err: Callable[[str], None],
    github: "GitHubClient | None",
    git: "GitClient | None",
    fetch_staircase: Callable[[Callable[[str], None]], PriceStaircase] | None,
) -> tuple[Path, CalibrationSurvey]:
    """Resolve the seams and read the survey, or refuse with a reason.

    Public because the spending path (#372) reads the *same* survey through the
    same seams: two collections could disagree about the corpus between the plan
    an operator confirmed and the search they paid for.

    ``calibrate`` is repository-scoped in a way ``config`` and ``skills`` are
    not: the **Proving set** *is* this repository's closed history and the
    measured artifact is a tracked file in it, so outside a repository there is
    nothing to report rather than a global scope to fall back on.
    """
    if repo_root is None:
        raise CalibrateCommandError(
            "calibrate reads this repository's closed history and its measured "
            "routing artifact, so it must run inside a git repository"
        )
    warn: Callable[[str], None] = lambda message: err(  # noqa: E731
        f"git-loopy: warning: {message}"
    )
    resolved_github = github if github is not None else _default_github()
    resolved_git = git if git is not None else _default_git(repo_root)
    fetch = fetch_staircase if fetch_staircase is not None else _live_staircase
    try:
        found = survey(
            repo_root=repo_root,
            github=resolved_github,
            git=resolved_git,
            staircase=fetch(warn),
        )
    except settings.SettingsError as exc:
        raise CalibrateCommandError(str(exc)) from None
    return repo_root, found


def _default_github() -> "GitHubClient":
    """The real ``gh`` adapter, built lazily so the parser stays SDK-free."""
    from git_loopy import gh as gh_module

    return gh_module.SubprocessGitHubClient()


def _default_git(repo_root: Path) -> "GitClient":
    """The real read-only git adapter. Mining touches no worktree."""
    from git_loopy import git as git_module

    return git_module.SubprocessGitClient(repo_root)


def _live_staircase(warn: Callable[[str], None]) -> PriceStaircase:
    """The **price staircase** off one live model listing (ADR-0019).

    Resolved through the shared
    :class:`~git_loopy.model_listing.LiveModelListing`, so the roster and the
    **Rate card** are two reads of one listing and no rung can be ordered by a
    different listing's prices.
    """
    import asyncio

    from git_loopy.model_listing import LiveModelListing
    from git_loopy.staircase import resolve_price_staircase

    return asyncio.run(resolve_price_staircase(LiveModelListing(), warn=warn))
