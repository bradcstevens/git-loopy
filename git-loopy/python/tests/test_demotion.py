"""Tests for :mod:`git_loopy.demotion` — the per-pair demotion decision (#366).

Covers ADR-0030: a **Measured routing** entry whose **Routed pair** keeps failing
to make progress on real work is replaced by the next pair *up* the price
staircase, counted per pair, applied after the **Run**, and recorded as
``provisional`` so an unmeasured pair can never read as a measured one.

Every test drives the public surface over real
:class:`~git_loopy.rolling_scheduler.Contribution` rows and a real
:class:`~git_loopy.staircase.PriceStaircase` — the two things the decision reads.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_loopy import demotion
from git_loopy import loop as loop_module
from git_loopy.config import RunConfig
from git_loopy.demotion import Pair
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
    ProvingTask,
    ProvisionalReason,
    Rung,
    load_measured_routing,
    measured_routing_path,
    write_measured_routing,
)
from git_loopy.rolling_scheduler import (
    REASON_CHECKPOINT_FAILED,
    REASON_PUBLISHED,
    REASON_SERIAL_FALLBACK,
    REASON_UNCHANGED_BRANCH,
    Contribution,
)
from git_loopy.git import GitError
from git_loopy.roster_preflight import RECALIBRATE_HINT
from git_loopy.routing_scope import SERIAL_PARALLELISM
from git_loopy.staircase import Candidate, PriceStaircase, StaircaseRefusal


def _contribution(
    ref: int,
    *,
    model: str | None,
    effort: str | None,
    reason: str,
) -> Contribution:
    """One finalized **Lane contribution**, as the scheduler leaves it."""
    return Contribution(
        contribution_id=f"c{ref}",
        ref=ref,
        lane_id="lane-0",
        model=model,
        reasoning_effort=effort,
        published=reason == REASON_PUBLISHED,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The count: per pair, from the contributions the Run finalized.
# ---------------------------------------------------------------------------


def test_only_unpublished_contributions_count_against_their_pair() -> None:
    """``published`` is the only Parallel progress; each other reason is a fail.

    The count is *per pair* rather than per Run, which is the whole of what makes
    it a usable signal where the shared **Strike** counter is not: a good pair
    committing work cannot erase what a bad pair accumulated, because they are
    counted in different buckets.
    """
    tally = demotion.tally_no_progress(
        [
            _contribution(1, model="cheap", effort="low", reason=REASON_PUBLISHED),
            _contribution(2, model="cheap", effort="low", reason=REASON_UNCHANGED_BRANCH),
            _contribution(3, model="cheap", effort="low", reason=REASON_CHECKPOINT_FAILED),
            _contribution(4, model="cheap", effort="low", reason=REASON_SERIAL_FALLBACK),
            _contribution(5, model="fancy", effort="high", reason=REASON_PUBLISHED),
        ]
    )

    assert tally == {("cheap", "low"): 3}


def test_a_pair_that_published_everything_is_absent_rather_than_zero() -> None:
    """Nothing to answer for is an absence, not a row reading ``0``."""
    tally = demotion.tally_no_progress(
        [_contribution(1, model="cheap", effort="low", reason=REASON_PUBLISHED)]
    )

    assert tally == {}


def test_effort_is_half_of_the_key() -> None:
    """One model at two efforts is two pairs, because a pair is what routes.

    The record's ``consumption.model`` — the harness-reported model, carrying no
    effort at all — cannot express this, which is why the count is taken from the
    pair bound at **Pickup** instead.
    """
    tally = demotion.tally_no_progress(
        [
            _contribution(1, model="cheap", effort="low", reason=REASON_SERIAL_FALLBACK),
            _contribution(2, model="cheap", effort="high", reason=REASON_SERIAL_FALLBACK),
        ]
    )

    assert tally == {("cheap", "low"): 1, ("cheap", "high"): 1}


def test_a_contribution_that_resolved_no_model_counts_against_nothing() -> None:
    """No pair, no bucket — an unrouted contribution is evidence about no pair."""
    tally = demotion.tally_no_progress(
        [_contribution(1, model=None, effort=None, reason=REASON_SERIAL_FALLBACK)]
    )

    assert tally == {}


def test_an_open_contribution_is_not_counted() -> None:
    """Only a *finalized* contribution has an outcome to count.

    An open row's ``reason`` is ``None``, which is not "did not publish" — it is
    "has not finished". Counting it would let a Run in flight demote on work that
    was still running.
    """
    still_open = Contribution(
        contribution_id="c1", ref=1, lane_id="lane-0", model="cheap",
        reasoning_effort="low",
    )

    assert demotion.tally_no_progress([still_open]) == {}


# ---------------------------------------------------------------------------
# The plan: which entries the count actually demotes, and to what.
# ---------------------------------------------------------------------------


def _staircase(*rungs: Pair) -> PriceStaircase:
    """A staircase over the given pairs, cheapest first, priced in that order."""
    return PriceStaircase(
        candidates=tuple(
            Candidate(model=model, effort=effort, multiplier=float(index + 1))
            for index, (model, effort) in enumerate(rungs)
        )
    )


def _measured(model: str, effort: str) -> MeasuredEntry:
    """A minimal but valid ``measured`` record for ``model @ effort``."""
    return MeasuredEntry(
        status=MeasuredStatus.MEASURED,
        model=model,
        effort=effort,
        trials_passed=5,
        trials_total=5,
        rungs_walked=1,
        credits=1.0,
        wall_clock_seconds=60,
        rungs=(Rung(model=model, effort=effort, passed=5, total=5, credits=1.0),),
        proving_tasks=(ProvingTask(issue=1, base_commit="a" * 40, oracle_commit="b" * 40),),
    )


_STAIRCASE = _staircase(("cheap", "low"), ("mid", "medium"), ("fancy", "high"))


def test_a_pair_below_the_threshold_is_left_alone() -> None:
    """Two failures where three are required is not a threshold being crossed."""
    plan = demotion.plan_demotions(
        entries={"bugfix": _measured("cheap", "low")},
        in_force={"bugfix": ("cheap", "low")},
        tally={("cheap", "low"): 2},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan.demotions == ()


def test_a_pair_at_the_threshold_steps_up_one_rung() -> None:
    """The next pair *up* the price staircase — never the next measured one.

    Cheapest-first search stops at the first pass, so every measured rung sits
    below the winner and failed; the set of measured pairs above it is empty by
    construction. Stepping up is therefore into the unmeasured, which is what the
    ``provisional`` status exists to say out loud.
    """
    plan = demotion.plan_demotions(
        entries={"bugfix": _measured("cheap", "low")},
        in_force={"bugfix": ("cheap", "low")},
        tally={("cheap", "low"): 3},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan.demotions == (
        demotion.Demotion(
            task_type="bugfix",
            demoted=("cheap", "low"),
            replacement=("mid", "medium"),
            no_progress=3,
        ),
    )


def test_a_hand_written_routing_entry_is_never_demoted() -> None:
    """The operator's decision, and this system does not overrule those.

    Structural rather than a status check: a hand-written ``[routing]`` entry
    beats the measured tier forever, so the pair that actually worked those
    issues was the operator's. The measured entry it shadows was never in force,
    so nothing that happened this Run is evidence about it.
    """
    plan = demotion.plan_demotions(
        entries={"bugfix": _measured("cheap", "low")},
        in_force={"bugfix": ("fancy", "high")},
        tally={("cheap", "low"): 99},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan.demotions == ()


def test_a_pair_at_the_top_of_the_staircase_is_reported_not_demoted() -> None:
    """Nothing above it to step to, and silence would hide a failing pair.

    The refusal is the notification: an entry that keeps failing at the most
    expensive rung the roster offers is exactly the case where re-calibrating —
    or reconsidering the work — is the only move left.
    """
    plan = demotion.plan_demotions(
        entries={"bugfix": _measured("fancy", "high")},
        in_force={"bugfix": ("fancy", "high")},
        tally={("fancy", "high"): 5},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan.demotions == ()
    assert [refusal.reason for refusal in plan.refusals] == [
        demotion.DemotionRefusal.TOP_OF_STAIRCASE
    ]


def test_a_pair_the_roster_no_longer_offers_is_reported_not_demoted() -> None:
    """No rung, no successor — and the roster having moved is the real news."""
    plan = demotion.plan_demotions(
        entries={"bugfix": _measured("retired", "low")},
        in_force={"bugfix": ("retired", "low")},
        tally={("retired", "low"): 5},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan.demotions == ()
    assert [refusal.reason for refusal in plan.refusals] == [
        demotion.DemotionRefusal.PAIR_OFF_STAIRCASE
    ]


def test_without_a_staircase_nothing_is_demoted() -> None:
    """A refused staircase carries no rungs, so there is no "up" to step to.

    Demoting into an invented ordering would put an unmeasured pair in force on
    an ordering that is itself unmeasured — the compounding the staircase's own
    "never a partial one" rule exists to prevent.
    """
    plan = demotion.plan_demotions(
        entries={"bugfix": _measured("cheap", "low")},
        in_force={"bugfix": ("cheap", "low")},
        tally={("cheap", "low"): 5},
        staircase=PriceStaircase(refusal=StaircaseRefusal.NO_RATE_CARD),
        threshold=3,
    )

    assert plan.demotions == ()
    assert [refusal.reason for refusal in plan.refusals] == [
        demotion.DemotionRefusal.NO_STAIRCASE
    ]


def test_a_provisional_entry_is_not_stepped_up_again() -> None:
    """One step per **Calibration**, which is what keeps Demotion reversible.

    A ``provisional`` entry is already the unmeasured fallback a previous
    Demotion installed, and its notification to re-calibrate is still
    outstanding. Stepping it again would let successive bad streaks walk a
    **Task type** to the top of the staircase unattended, spending real **AI
    Credits** the whole way on a pair nobody ever measured.
    """
    provisional = MeasuredEntry(
        status=MeasuredStatus.PROVISIONAL,
        model="mid",
        effort="medium",
        replaced_model="cheap",
        replaced_effort="low",
        replaced_after_no_progress=3,
        reason=ProvisionalReason.DEMOTION,
    )
    plan = demotion.plan_demotions(
        entries={"bugfix": provisional},
        in_force={"bugfix": ("mid", "medium")},
        tally={("mid", "medium"): 9},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan.demotions == ()
    assert [refusal.reason for refusal in plan.refusals] == [
        demotion.DemotionRefusal.ALREADY_PROVISIONAL
    ]


def test_an_incomplete_entry_supplies_no_pair_and_so_demotes_nothing() -> None:
    """An unfinished search published no winner; there is nothing in force."""
    incomplete = MeasuredEntry(
        status=MeasuredStatus.INCOMPLETE,
        stopped_at_rung=2,
        rungs_available=3,
        credits=4.0,
        wall_clock_seconds=90,
    )
    plan = demotion.plan_demotions(
        entries={"bugfix": incomplete},
        in_force={},
        tally={("cheap", "low"): 9},
        staircase=_STAIRCASE,
        threshold=3,
    )

    assert plan == demotion.DemotionPlan()


# ---------------------------------------------------------------------------
# Applying the plan: the artifact the Run leaves behind.
# ---------------------------------------------------------------------------


def test_a_demotion_writes_a_provisional_record_carrying_its_evidence() -> None:
    """The pair now in force, the pair it replaced, and the count that moved it.

    Never ``measured``: nothing trialled the replacement, and the whole point of
    the fourth status is that an unmeasured pair must *look* unmeasured (#376).
    """
    artifact = MeasuredRouting(entries={"bugfix": _measured("cheap", "low")})
    plan = demotion.DemotionPlan(
        demotions=(
            demotion.Demotion(
                task_type="bugfix",
                demoted=("cheap", "low"),
                replacement=("mid", "medium"),
                no_progress=4,
            ),
        )
    )

    updated = demotion.apply_demotions(artifact, plan)

    entry = updated.entries["bugfix"]
    assert entry.status is MeasuredStatus.PROVISIONAL
    assert entry.routed_pair == ("mid", "medium")
    assert (entry.replaced_model, entry.replaced_effort) == ("cheap", "low")
    assert entry.replaced_after_no_progress == 4
    assert entry.reason is ProvisionalReason.DEMOTION
    assert updated.provisional_keys == frozenset({"bugfix"})


def test_a_demotion_discards_the_replaced_pairs_evidence() -> None:
    """The rungs belong to the pair that was demoted, not the one stepping in.

    Carrying them across is the exact confusion ``provisional`` exists to
    prevent: another pair's **Trial** results read as though they measured this
    one. The record's own invariant refuses it, so this is a fact about the
    writer rather than a hope about it.
    """
    artifact = MeasuredRouting(entries={"bugfix": _measured("cheap", "low")})
    plan = demotion.DemotionPlan(
        demotions=(
            demotion.Demotion(
                task_type="bugfix",
                demoted=("cheap", "low"),
                replacement=("mid", "medium"),
                no_progress=3,
            ),
        )
    )

    entry = demotion.apply_demotions(artifact, plan).entries["bugfix"]

    assert entry.rungs == ()
    assert entry.proving_tasks == ()
    assert entry.trials_passed is None


def test_task_types_the_plan_does_not_name_survive_untouched() -> None:
    """Demotion rewrites the entries it demoted and nothing else."""
    untouched = _measured("fancy", "high")
    artifact = MeasuredRouting(
        entries={"bugfix": _measured("cheap", "low"), "docs": untouched}
    )
    plan = demotion.DemotionPlan(
        demotions=(
            demotion.Demotion(
                task_type="bugfix",
                demoted=("cheap", "low"),
                replacement=("mid", "medium"),
                no_progress=3,
            ),
        )
    )

    updated = demotion.apply_demotions(artifact, plan)

    assert updated.entries["docs"] == untouched


def test_the_calibrations_provenance_survives_a_demotion() -> None:
    """Demotion measured nothing, so it restamps nothing.

    ``[provenance]`` records what the **Calibration** ran against — its roster,
    its **Rate card**, its classifier pin. A Demotion trialled no pair, so
    overwriting it would claim a measurement this Run did not make, and would
    silence the roster-drift notification that stamp exists to make possible.
    """
    provenance = Provenance(
        cli_version="9.9.9",
        calibrated_at="2026-01-01T00:00:00Z",
        candidate_count=3,
        gate_loops=("bash -n git-loopy/shell/git-loopy.sh",),
    )
    artifact = MeasuredRouting(
        entries={"bugfix": _measured("cheap", "low")}, provenance=provenance
    )
    plan = demotion.DemotionPlan(
        demotions=(
            demotion.Demotion(
                task_type="bugfix",
                demoted=("cheap", "low"),
                replacement=("mid", "medium"),
                no_progress=3,
            ),
        )
    )

    assert demotion.apply_demotions(artifact, plan).provenance == provenance


def test_an_empty_plan_returns_the_artifact_unchanged() -> None:
    """The ordinary Run changes nothing, and must not rewrite the file to say so."""
    artifact = MeasuredRouting(entries={"bugfix": _measured("cheap", "low")})

    assert demotion.apply_demotions(artifact, demotion.DemotionPlan()) == artifact


def test_the_written_record_survives_a_round_trip_through_the_artifact(
    tmp_path: Path,
) -> None:
    """What Demotion writes, the loader reads back — including the count.

    The end-to-end pin on the schema slice: a ``provisional`` record built here
    is one :func:`~git_loopy.measured_routing.dump_measured_routing` can write and
    :func:`~git_loopy.measured_routing.load_measured_routing` can read, so the two
    halves of the seam cannot disagree about what a Demotion says.
    """
    artifact = MeasuredRouting(
        entries={"bugfix": _measured("cheap", "low")},
        provenance=Provenance(
            cli_version="9.9.9",
            calibrated_at="2026-01-01T00:00:00Z",
            candidate_count=3,
            gate_loops=("bash -n git-loopy/shell/git-loopy.sh",),
        ),
    )
    plan = demotion.DemotionPlan(
        demotions=(
            demotion.Demotion(
                task_type="bugfix",
                demoted=("cheap", "low"),
                replacement=("mid", "medium"),
                no_progress=7,
            ),
        )
    )
    updated = demotion.apply_demotions(artifact, plan)

    write_measured_routing(tmp_path, updated)
    round_tripped = load_measured_routing(measured_routing_path(tmp_path))

    assert round_tripped == updated
    assert round_tripped.entries["bugfix"].replaced_after_no_progress == 7


# ---------------------------------------------------------------------------
# The threshold: a free parameter, so it is configurable (ADR-0030).
# ---------------------------------------------------------------------------


def test_the_threshold_defaults_to_a_value_the_strike_limit_could_never_hold() -> None:
    """Unlike the **Strike** limit, this one is bounded by nothing structural.

    ADR-0030's *"Why the stated rule cannot work"* turns on the Strike limit
    ending the Run at 3, which leaves the per-Run range empty. This counter is
    per pair and does not end anything, so the same number is a perfectly usable
    threshold here — three no-progress contributions by one pair is a signal, and
    one is noise.
    """
    assert RunConfig().demotion_threshold == 3


def test_a_threshold_below_one_is_refused() -> None:
    """A threshold of ``0`` would demote every pair that worked at all."""
    with pytest.raises(ValueError, match="demotion_threshold"):
        RunConfig(demotion_threshold=0)



# ---------------------------------------------------------------------------
# `demote_after_run` — the one seam that touches disk, at the quiescent point.
# ---------------------------------------------------------------------------


class _RecordingGit:
    """A git client that records what it was asked to commit, and nothing else."""

    def __init__(self, root: Path, *, fail: bool = False) -> None:
        self.root = root
        self.commits: list[tuple[str, list[str]]] = []
        self._fail = fail

    def commit_paths(self, message: str, paths) -> str:
        if self._fail:
            raise GitError("commit refused", 1, "nothing to commit")
        self.commits.append((message, [str(path) for path in paths]))
        return "0" * 40


def _seed_artifact(root: Path, entries: dict[str, MeasuredEntry]) -> None:
    """Write an artifact carrying ``entries`` and a plausible provenance."""
    write_measured_routing(
        root,
        MeasuredRouting(
            entries=entries,
            provenance=Provenance(
                cli_version="9.9.9",
                calibrated_at="2026-01-01T00:00:00Z",
                candidate_count=3,
                gate_loops=("bash -n git-loopy/shell/git-loopy.sh",),
            ),
        ),
    )


def _failing(pair: Pair, count: int) -> list[Contribution]:
    return [
        _contribution(
            index, model=pair[0], effort=pair[1], reason=REASON_UNCHANGED_BRANCH
        )
        for index in range(count)
    ]


def _run_demotion(
    root: Path,
    *,
    contributions: list[Contribution],
    parallel: int = 4,
    routing: dict[str, tuple[str, str | None]] | None = None,
    staircase: PriceStaircase = _STAIRCASE,
    git: _RecordingGit | None = None,
    warnings: list[str] | None = None,
) -> demotion.DemotionPlan:
    return demotion.demote_after_run(
        repo_root=root,
        config=RunConfig(
            parallel=parallel, routing=routing or {"bugfix": ("cheap", "low")}
        ),
        staircase=staircase,
        contributions=contributions,
        git=git if git is not None else _RecordingGit(root),
        warn=(warnings if warnings is not None else []).append,
    )


def _config(*, parallel: int) -> RunConfig:
    return RunConfig(parallel=parallel, routing={"bugfix": ("cheap", "low")})


def test_a_demotion_rewrites_and_commits_the_artifact_once(tmp_path: Path) -> None:
    """One write, one commit, naming only the artifact (ADR-0028, ADR-0030).

    The commit *is* the review: it lands after the Run, as a reviewable and
    revertible change, which is the property that made moving Demotion out of the
    Run worth doing.
    """
    _seed_artifact(tmp_path, {"bugfix": _measured("cheap", "low")})
    git = _RecordingGit(tmp_path)

    plan = _run_demotion(
        tmp_path, contributions=_failing(("cheap", "low"), 3), git=git
    )

    assert len(plan.demotions) == 1
    entry = load_measured_routing(measured_routing_path(tmp_path)).entries["bugfix"]
    assert entry.status is MeasuredStatus.PROVISIONAL
    assert entry.routed_pair == ("mid", "medium")
    assert entry.replaced_after_no_progress == 3
    assert len(git.commits) == 1
    message, paths = git.commits[0]
    assert paths == [str(measured_routing_path(tmp_path))]
    assert "bugfix" in message


def test_nothing_demotes_in_serial_mode(tmp_path: Path) -> None:
    """Nothing routes there, so nothing measured is in force to be wrong.

    Read through :func:`~git_loopy.routing_scope.routing_in_force` rather than
    comparing ``parallel`` here, so the scope rule has one author across the
    **Calibration**'s refusal, the reporting surfaces and this.
    """
    _seed_artifact(tmp_path, {"bugfix": _measured("cheap", "low")})
    git = _RecordingGit(tmp_path)

    plan = _run_demotion(
        tmp_path,
        contributions=_failing(("cheap", "low"), 99),
        parallel=SERIAL_PARALLELISM,
        git=git,
    )

    assert plan == demotion.DemotionPlan()
    assert git.commits == []
    entry = load_measured_routing(measured_routing_path(tmp_path)).entries["bugfix"]
    assert entry.status is MeasuredStatus.MEASURED


def test_a_run_that_demotes_nothing_writes_nothing(tmp_path: Path) -> None:
    """The ordinary Run must leave the tracked file — and the log — untouched."""
    _seed_artifact(tmp_path, {"bugfix": _measured("cheap", "low")})
    before = measured_routing_path(tmp_path).read_text(encoding="utf-8")
    git = _RecordingGit(tmp_path)

    plan = _run_demotion(
        tmp_path, contributions=_failing(("cheap", "low"), 1), git=git
    )

    assert plan == demotion.DemotionPlan()
    assert measured_routing_path(tmp_path).read_text(encoding="utf-8") == before
    assert git.commits == []


def test_a_repository_that_never_calibrated_pays_nothing(tmp_path: Path) -> None:
    """No artifact, no measured tier, nothing to demote — and no error either."""
    git = _RecordingGit(tmp_path)

    plan = _run_demotion(
        tmp_path, contributions=_failing(("cheap", "low"), 99), git=git
    )

    assert plan == demotion.DemotionPlan()
    assert git.commits == []
    assert not measured_routing_path(tmp_path).exists()


def test_a_demotion_notifies_that_the_task_type_needs_recalibrating(
    tmp_path: Path,
) -> None:
    """ADR-0028's notify-don't-act rule, applied unchanged.

    Demotion installs a pair *nobody measured*, so the artifact is now weaker
    evidence than it was. Saying so — and naming the command — is the whole of
    what it is allowed to do about that.
    """
    _seed_artifact(tmp_path, {"bugfix": _measured("cheap", "low")})
    warnings: list[str] = []

    _run_demotion(
        tmp_path, contributions=_failing(("cheap", "low"), 3), warnings=warnings
    )

    spoken = " ".join(warnings)
    assert "bugfix" in spoken
    assert "cheap" in spoken and "mid" in spoken
    assert "3" in spoken
    assert RECALIBRATE_HINT in warnings


def test_a_refusal_is_reported_even_though_nothing_changed(tmp_path: Path) -> None:
    """A pair failing at the top of the staircase is the case most worth hearing."""
    _seed_artifact(tmp_path, {"bugfix": _measured("fancy", "high")})
    warnings: list[str] = []

    plan = _run_demotion(
        tmp_path,
        contributions=_failing(("fancy", "high"), 4),
        routing={"bugfix": ("fancy", "high")},
        warnings=warnings,
    )

    assert plan.demotions == ()
    assert plan.refusals != ()
    assert any("fancy" in line for line in warnings)


def test_a_failed_commit_leaves_the_run_alone(tmp_path: Path) -> None:
    """Demotion is not a precondition for the work the Run already did.

    The artifact is still rewritten on disk — the decision was correct and the
    next Run should honour it — but a git failure at the very end of a Run is
    reported and swallowed, exactly as ``_maybe_push`` treats one.
    """
    _seed_artifact(tmp_path, {"bugfix": _measured("cheap", "low")})
    warnings: list[str] = []

    plan = _run_demotion(
        tmp_path,
        contributions=_failing(("cheap", "low"), 3),
        git=_RecordingGit(tmp_path, fail=True),
        warnings=warnings,
    )

    assert len(plan.demotions) == 1
    assert any("commit" in line for line in warnings)


def test_demotion_off_a_repository_does_nothing(tmp_path: Path) -> None:
    """The artifact is a tracked file, so off-repo there is nothing to rewrite."""
    assert (
        demotion.demote_after_run(
            repo_root=None,
            config=RunConfig(parallel=4, routing={"bugfix": ("cheap", "low")}),
            staircase=_STAIRCASE,
            contributions=_failing(("cheap", "low"), 9),
            git=_RecordingGit(tmp_path),
            warn=[].append,
        )
        == demotion.DemotionPlan()
    )


# ---------------------------------------------------------------------------
# The Run-end wiring: where the count comes from, and when it is taken.
# ---------------------------------------------------------------------------


def test_the_parallel_loop_hands_demotion_the_finalized_contributions() -> None:
    """The seam that replaces ADR-0030's *"persisted run record"* premise.

    ADR-0030 says the count comes from ``.git-loopy/runs/*.json``. It cannot: a
    **Lane** session is given ``event_observer=self._cost_meter`` rather than the
    rollup, and Lane contributions emit no ``wrapper.iteration.start``/``.end``
    at all (#219/#306), so that file holds no Lane rows in the *only* mode
    Demotion applies to. The facts are on the finalized
    :class:`~git_loopy.rolling_scheduler.Contribution` instead — the pair bound
    at **Pickup**, and the terminal reason set at finalization.
    """
    finalized = tuple(_failing(("cheap", "low"), 2))

    class _Scheduled:
        _scheduler = SimpleNamespace(finalized=finalized)

    fget = loop_module._ParallelLoop.finalized_contributions.fget
    assert fget(_Scheduled()) == finalized


def test_a_run_that_never_built_a_scheduler_counts_nothing() -> None:
    """A Parallel Run that refused to go rolling finalized no contribution.

    ``()`` rather than a raise: Demotion runs at the very end of every Run,
    including one that fell back before a scheduler existed, and a Run must not
    fail at its last step over having had nothing to demote.
    """

    class _Unscheduled:
        _scheduler = None

    fget = loop_module._ParallelLoop.finalized_contributions.fget
    assert fget(_Unscheduled()) == ()


def test_the_serial_loop_finalizes_no_contribution_to_demote() -> None:
    """Serial says ``()`` in code, not by having no attribute at all.

    :func:`git_loopy.loop.run` reads the same name off whichever loop it built,
    and a ``getattr`` default would make *"serial demotes nothing"* an accident
    of spelling — a later rename would silently switch Demotion off in Parallel
    too, and the tally would just be empty rather than the Run being wrong in a
    way anything notices. There is a second, independent refusal downstream
    (:func:`~git_loopy.demotion.demote_after_run` checks
    :func:`~git_loopy.routing_scope.routing_in_force`); this is the first.
    """

    class _Serial:
        pass

    fget = loop_module._Loop.finalized_contributions.fget
    assert fget(_Serial()) == ()


def test_a_run_hands_its_contributions_to_demotion_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter passes the loop's rows straight through, unfiltered.

    Every rule about *which* rows matter lives in
    :func:`~git_loopy.demotion.tally_no_progress`; the loop's job is only to say
    which Run they came from. A loop that pre-filtered would put half the rule in
    a module that cannot be unit-tested without an event loop.
    """
    finalized = tuple(_failing(("cheap", "low"), 3))
    seen: dict[str, object] = {}

    def _record(**kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(demotion, "demote_after_run", _record)
    config = _config(parallel=4)
    staircase = _STAIRCASE

    loop_module._demote_after_run(
        config,
        SimpleNamespace(root=Path("/repo")),
        SimpleNamespace(finalized_contributions=finalized),
        staircase,
        logging.getLogger("test"),
    )

    assert seen["contributions"] == finalized
    assert seen["config"] is config
    assert seen["staircase"] is staircase
    assert seen["repo_root"] == Path("/repo")


def test_a_run_that_finalized_nothing_never_reaches_demotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rows means no read of the artifact and no ``git`` invocation at all.

    The overwhelmingly common Run — one that published everything, or picked up
    nothing — must not pay a file read and a ``git status`` for a decision whose
    input is empty.
    """
    called = False

    def _record(**kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(demotion, "demote_after_run", _record)

    loop_module._demote_after_run(
        _config(parallel=4),
        SimpleNamespace(root=Path("/repo")),
        SimpleNamespace(finalized_contributions=()),
        _STAIRCASE,
        logging.getLogger("test"),
    )

    assert called is False


def test_a_demotion_that_explodes_cannot_take_the_run_with_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rewriting a routing table is never a precondition for finished work.

    Demotion runs in :func:`~git_loopy.loop.run`'s ``finally``, *after* the exit
    code is decided, on advisory data. A Run that fixed three issues and pushed
    them must not report failure because a TOML file could not be rewritten, so
    the adapter catches everything — including the failure modes
    :func:`~git_loopy.demotion.demote_after_run` does not itself expect.
    """

    def _explode(**kwargs: object) -> None:
        raise RuntimeError("the artifact is a directory")

    monkeypatch.setattr(demotion, "demote_after_run", _explode)

    with caplog.at_level(logging.WARNING):
        loop_module._demote_after_run(
            _config(parallel=4),
            SimpleNamespace(root=Path("/repo")),
            SimpleNamespace(finalized_contributions=_failing(("cheap", "low"), 3)),
            _STAIRCASE,
            logging.getLogger("git-loopy.test"),
        )

    assert "the artifact is a directory" in caplog.text


def test_a_run_with_no_staircase_still_reaches_demotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` becomes a *refused* staircase, not a skipped Demotion.

    The distinction is the whole notify-don't-act rule. A Run whose listing could
    not be read still knows a pair stopped making progress, and still owes the
    operator that sentence; what it cannot do is choose a replacement. Skipping
    the call would swap a reported refusal for silence.
    """
    seen: dict[str, object] = {}
    monkeypatch.setattr(demotion, "demote_after_run", lambda **kw: seen.update(kw))

    loop_module._demote_after_run(
        _config(parallel=4),
        SimpleNamespace(root=Path("/repo")),
        SimpleNamespace(finalized_contributions=_failing(("cheap", "low"), 3)),
        None,
        logging.getLogger("test"),
    )

    staircase = seen["staircase"]
    assert isinstance(staircase, PriceStaircase)
    assert staircase.candidates == ()


# ---------------------------------------------------------------------------
# The structural guards: what Demotion cannot reach, by where the code sits.
# ---------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    """Every module name this file imports, including inside a function body.

    Parsed rather than grepped, on the precedent
    ``test_no_unattended_path_can_start_a_calibration`` set: this module
    *discusses* a **Calibration** at length and a docstring cross-reference is
    not a dependency, while an ``ast`` walk catches the lazy import inside a
    function that is where a late-arriving one would most plausibly hide.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    "forbidden",
    [
        "git_loopy.trial",
        "git_loopy.trial_concurrency",
        "git_loopy.calibration_search",
        "git_loopy.calibration_run",
        "git_loopy.staircase.search_price_staircase",
        "git_loopy.proving_admission",
        "git_loopy.worktree",
        "git_loopy.session",
    ],
)
def test_demotion_notifies_and_cannot_start_a_calibration(forbidden: str) -> None:
    """*"Demotion notifies; it never starts a Calibration"* holds structurally.

    This is the criterion the whole feature's safety rests on, and it is the one
    a plausible future change breaks by accident: a demoted **Task type** wants
    re-measuring, its record says so, and the shortest path from *"says so"* to
    *"is so"* is one call. That call would turn an unattended overnight Run into
    a spending event — a thing #372 made explicitly operator-confirmed, and which
    the **Calibration** glossary entry promises *"never starts itself, not on a
    first **Run**, not at preflight, not when the roster moves."*

    A module that never imports the walk, the **Trial** runner, the dispatcher or
    the corpus cannot reach any of them by any argument. The staircase is
    imported for its *ordering* — ``Candidate``, ``PriceStaircase`` — and
    ``search_price_staircase``, the one thing in it that spends, is named
    separately and refused.
    """
    source = Path(demotion.__file__)
    assert forbidden not in _imported_modules(source)


def test_demotion_touches_no_strike_machine() -> None:
    """The **Strike** counter is untouched, per #366's second criterion.

    Demotion counts what the Strike machine also counts, and the tempting
    economy is to read one from the other. They answer different questions over
    different windows: Strikes end a **Run** after consecutive no-progress
    Iterations against *one issue*, while Demotion judges a **Routed pair**
    across every issue it worked. Sharing a counter would make a **Task type**'s
    routing depend on which issue happened to be picked up last — and, in the
    other direction, would let a routing decision end a Run.

    Asserted on the name rather than on ``git_loopy.wrapper`` wholesale, because
    the machine shares that module with the **Wrapper contract** itself; a
    docstring may name it, and this module's does, but no executable line may.
    """
    source = Path(demotion.__file__)
    assert "git_loopy.wrapper.NMTStrikeStateMachine" not in _imported_modules(source)

    tree = ast.parse(source.read_text(encoding="utf-8"))
    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "NMTStrikeStateMachine" not in referenced
    assert "record_strike" not in referenced
