"""``git_loopy.demotion`` — a measured pair that stops working loses its entry (#366, ADR-0030).

**Demotion** is what buys **Measured routing** the right to be cheap. Promotion
requires measurement; demotion requires only experience — which is why a
five-**Trial** search is affordable at all. The search does not have to be right;
it has to be cheap and **reversible**, and this module is the reverse gear.

Demotion is valid without a counterfactual because it is an *absolute* threshold
rather than a comparison: nothing here claims another pair would have done
better, only that this one is failing.

Design notes:

* **The signal is per pair, and it is not the Strike counter.** ADR-0027
  originally specified consecutive **Strikes**, which cannot be implemented:
  :class:`~git_loopy.wrapper.NMTStrikeStateMachine` is one Run-scoped counter
  every **Lane** shares and *any* Lane's progress resets, so a good pair's commit
  erases what a bad pair accumulated. Worse, the limit that ends a Run is small,
  so a threshold at or above it never fires and one below it fires on noise — the
  usable range is empty. The Strike counter keeps its existing job (ending a Run
  that is going nowhere) entirely unchanged.

* **The source is the finalized Contribution, not the Run summary.** ADR-0030
  says *"the record already knows which pair worked which issue and whether it
  made progress"* and names ``.git-loopy/runs/*.json``. **That is false for a
  Lane.** ``loop.py`` gives a Lane session ``event_observer=self._cost_meter``
  rather than the rollup, and Lane contributions emit no
  ``wrapper.iteration.start``/``.end`` at all (#219/#306), so the Run summary
  holds no Lane rows — in the *only* mode Demotion applies to. The facts the
  decision needs are on
  :class:`~git_loopy.rolling_scheduler.Contribution` instead: ``model`` and
  ``reasoning_effort`` bound once at **Pickup**, and ``reason`` set at
  finalization, where ``published`` is the only progress. Same two facts, same
  quiescent moment, and they actually exist. See ADR-0030's amendment.

* **Demotion applies after the Run**, never mid-Run. No Lane is running, so
  there is no race between concurrent Lanes over one shared tracked file, and no
  need to invent a mid-Run mechanism for committing an arbitrary tracked file.
  The change arrives as a reviewable, revertible commit — the property ADR-0028
  committed the artifact to obtain.

* **It steps up, into the unmeasured.** The obvious fallback — the next-cheapest
  pair the **Calibration** already measured — does not exist: cheapest-first
  stops at the first pass, so every measured rung sits *below* the winner and
  failed, and nothing above it was ever trialled. Stepping up is also the cheap
  error (ADR-0027). The result is recorded ``provisional`` so an unmeasured pair
  can never read as a measured one.

* **It notifies; it never searches.** ADR-0028's notify-don't-act rule, applied
  unchanged and for the same reason: an implicit trigger converts an unattended
  Run into a benchmark suite. Nothing here imports a **Trial**, the search or the
  dispatcher, so that holds by where the code sits rather than by care.

* **Pure decision, injected I/O.** Everything down to :func:`apply_demotions` is
  a pure function of values, so the whole rule is pinnable without a repository,
  a Run or a worktree. :func:`demote_after_run` is the one seam that touches
  disk, and it takes its writer and its committer as arguments.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

from git_loopy.config import RunConfig
from git_loopy.git import GitClient, GitError

from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    ProvisionalReason,
    load_measured_routing,
    measured_routing_path,
    write_measured_routing,
)
from git_loopy.rolling_scheduler import REASON_PUBLISHED, Contribution
from git_loopy.roster_preflight import RECALIBRATE_HINT
from git_loopy.routing_scope import routing_in_force
from git_loopy.settings import SettingsError
from git_loopy.staircase import PriceStaircase, render_pair

__all__ = [
    "Pair",
    "Demotion",
    "DemotionRefusal",
    "RefusedDemotion",
    "DemotionPlan",
    "tally_no_progress",
    "plan_demotions",
    "apply_demotions",
    "render_demotion",
    "render_refusal",
    "demote_after_run",
]

#: A **Routed pair**: the model and the reasoning effort that ran together. The
#: effort is half the key, because it is half of what routes — and is exactly
#: what the harness-reported ``consumption.model`` cannot express.
Pair = tuple[str, str | None]


def tally_no_progress(contributions: Iterable[Contribution]) -> dict[Pair, int]:
    """Count each **Routed pair**'s no-progress **Lane contributions**.

    The per-pair signal ADR-0030 substitutes for the shared **Strike** counter.
    ``published`` is the only Parallel progress there is; ``unchanged_branch``,
    ``checkpoint_failed`` and ``serial_fallback`` are each a contribution that
    reached a terminal disposition without publishing, which is precisely the
    experience Demotion acts on.

    Two rows are deliberately skipped rather than bucketed. A contribution that
    is still **open** carries ``reason is None``, which means "has not finished"
    rather than "did not publish" — counting it would demote on work that was
    still running. And one that resolved **no model** belongs to no pair, so it
    is evidence about nothing this module can act on.

    Args:
        contributions: The Run's finalized contributions, in any order —
            typically :attr:`~git_loopy.rolling_scheduler.RollingScheduler.finalized`.

    Returns:
        ``{pair: count}``, holding only pairs with at least one no-progress
        contribution. A pair that published everything is **absent** rather than
        present with a ``0``, so the mapping reads as the list of pairs with
        something to answer for.
    """
    tally: Counter[Pair] = Counter()
    for contribution in contributions:
        if contribution.reason is None or contribution.reason == REASON_PUBLISHED:
            continue
        if contribution.model is None:
            continue
        tally[(contribution.model, contribution.reasoning_effort)] += 1
    return dict(tally)


class DemotionRefusal(Enum):
    """Why a pair that crossed the threshold was *not* demoted.

    A closed vocabulary rather than prose, in the same spirit as
    :class:`~git_loopy.measured_routing.ProvisionalReason` and
    :class:`~git_loopy.staircase.StaircaseRefusal`: each of these is a fact an
    operator should hear, and a test should be able to assert on, rather than a
    sentence a reader has to trust.

    Every member describes a pair that **did** fail enough to be demoted. A pair
    below the threshold is not a refusal — nothing was refused, the rule simply
    did not fire — so it produces no member here and no notification.
    """

    #: The Run resolved no price staircase, so there is no "up" to step to.
    NO_STAIRCASE = "no_staircase"
    #: The failing pair is not on the current staircase at all — the roster moved
    #: under the artifact, which is the more urgent news.
    PAIR_OFF_STAIRCASE = "pair_off_staircase"
    #: The failing pair is already the most expensive rung the roster offers.
    TOP_OF_STAIRCASE = "top_of_staircase"
    #: The entry is already a ``provisional`` one a previous Demotion installed.
    ALREADY_PROVISIONAL = "already_provisional"


@dataclass(frozen=True)
class Demotion:
    """One **Task type**'s entry stepping up, and the evidence that moved it."""

    task_type: str
    demoted: Pair
    replacement: Pair
    no_progress: int


@dataclass(frozen=True)
class RefusedDemotion:
    """A pair that failed enough to be demoted, and could not be."""

    task_type: str
    pair: Pair
    no_progress: int
    reason: DemotionRefusal


@dataclass(frozen=True)
class DemotionPlan:
    """What this Run's experience says to change, and what it can only report."""

    demotions: tuple[Demotion, ...] = ()
    refusals: tuple[RefusedDemotion, ...] = ()

    def __bool__(self) -> bool:
        """Whether there is anything at all to act on or say."""
        return bool(self.demotions or self.refusals)


def plan_demotions(
    *,
    entries: Mapping[str, MeasuredEntry],
    in_force: Mapping[str, Pair],
    tally: Mapping[Pair, int],
    staircase: PriceStaircase,
    threshold: int,
) -> DemotionPlan:
    """Decide which measured entries this Run's experience demotes.

    Pure, and deliberately so: the whole rule is a function of four values, so it
    is pinnable without a repository, a Run, a worktree or an **AI Credit**.

    Four gates, in order, and each exists for a different reason:

    1. **The entry must be a ``measured`` one.** An ``incomplete`` search
       published no winner and a ``demoted`` record has no pair, so neither has
       anything in force to lose. A ``provisional`` entry *does* route, but it is
       already the unmeasured fallback a previous Demotion installed — stepping
       it again would let successive bad streaks walk a Task type to the top of
       the staircase unattended, spending real credits the whole way on a pair
       nobody measured, while its outstanding "re-calibrate" notification went
       unanswered. One step per Calibration is what keeps Demotion reversible.
    2. **The entry's pair must be the one actually in force.** A hand-written
       ``[routing]`` entry beats the measured tier forever (ADR-0028), so where
       one exists it is the operator's pair that worked those issues and the
       shadowed measured entry is not what failed. This is how *"a hand-written
       routing entry is never demoted"* holds without inspecting Config tiers
       here: the pair in force is compared, not the tier that supplied it.
    3. **The count must reach the threshold.** Absolute, not comparative —
       nothing is claimed about which pair is better, only that this one is
       failing.
    4. **The staircase must offer a rung above it.** Otherwise there is a
       failing pair and nothing to do about it, which is a refusal an operator
       hears rather than a silence.

    Args:
        entries: The artifact's records, keyed by **Task type**.
        in_force: The **Routed pair** actually resolved for each Task type this
            Run — :attr:`~git_loopy.config.RunConfig.routing`, after the whole
            precedence chain has been walked.
        tally: :func:`tally_no_progress`'s answer for this Run.
        staircase: The Run's price staircase, or a refused one.
        threshold: How many no-progress contributions demote a pair.

    Returns:
        The demotions to apply and the refusals to report. Both may be empty,
        which is the ordinary outcome of a Run that went fine.
    """
    demotions: list[Demotion] = []
    refusals: list[RefusedDemotion] = []
    for task_type in sorted(entries):
        entry = entries[task_type]
        pair = entry.routed_pair
        if pair is None:
            continue
        if in_force.get(task_type) != pair:
            continue
        count = tally.get(pair, 0)
        if count < threshold:
            continue
        if entry.status is not MeasuredStatus.MEASURED:
            refusals.append(
                RefusedDemotion(
                    task_type=task_type,
                    pair=pair,
                    no_progress=count,
                    reason=DemotionRefusal.ALREADY_PROVISIONAL,
                )
            )
            continue
        replacement, refusal = _next_rung_up(pair, staircase)
        if replacement is None:
            assert refusal is not None
            refusals.append(
                RefusedDemotion(
                    task_type=task_type,
                    pair=pair,
                    no_progress=count,
                    reason=refusal,
                )
            )
            continue
        demotions.append(
            Demotion(
                task_type=task_type,
                demoted=pair,
                replacement=replacement,
                no_progress=count,
            )
        )
    return DemotionPlan(demotions=tuple(demotions), refusals=tuple(refusals))


def _next_rung_up(
    pair: Pair, staircase: PriceStaircase
) -> tuple[Pair | None, DemotionRefusal | None]:
    """The pair one rung more expensive than ``pair``, or why there is none.

    The staircase is ordered by ascending expected spend, so "up" is simply the
    next element — the same ordering the **Calibration** walked, read backwards.
    Using any other ordering would step into a pair whose price relationship to
    the failing one is unknown, which is the thing the staircase's "never a
    partial ordering" rule exists to prevent.
    """
    if not staircase.available:
        return None, DemotionRefusal.NO_STAIRCASE
    rungs = [(candidate.model, candidate.effort) for candidate in staircase.candidates]
    if pair not in rungs:
        return None, DemotionRefusal.PAIR_OFF_STAIRCASE
    index = rungs.index(pair)
    if index + 1 >= len(rungs):
        return None, DemotionRefusal.TOP_OF_STAIRCASE
    return rungs[index + 1], None


def apply_demotions(artifact: MeasuredRouting, plan: DemotionPlan) -> MeasuredRouting:
    """Fold a plan's demotions into the artifact, leaving everything else alone.

    Each demoted **Task type**'s record is replaced outright by a ``provisional``
    one. Replaced, not amended: the outgoing record's rungs and **Proving tasks**
    are the evidence for the pair that just *failed*, and carrying them across
    would present another pair's **Trial** results as though they had measured
    the replacement — the exact confusion this state exists to prevent
    (ADR-0030). :class:`~git_loopy.measured_routing.MeasuredEntry`'s own
    invariant refuses such a record, so this is enforced rather than intended.

    ``[provenance]`` is carried through untouched. It records what the
    **Calibration** ran against, and a Demotion trialled nothing — restamping it
    would claim a measurement this Run did not make, and would silence the
    roster-drift notification that stamp exists to make possible.

    Args:
        artifact: The artifact as loaded at Run start.
        plan: :func:`plan_demotions`'s answer. An empty one is the ordinary case.

    Returns:
        A new artifact, or ``artifact`` itself when the plan demotes nothing —
        so a Run that changed nothing has nothing to write.
    """
    if not plan.demotions:
        return artifact
    entries = dict(artifact.entries)
    for item in plan.demotions:
        model, effort = item.replacement
        replaced_model, replaced_effort = item.demoted
        entries[item.task_type] = MeasuredEntry(
            status=MeasuredStatus.PROVISIONAL,
            model=model,
            effort=effort,
            replaced_model=replaced_model,
            replaced_effort=replaced_effort,
            replaced_after_no_progress=item.no_progress,
            reason=ProvisionalReason.DEMOTION,
        )
    return MeasuredRouting(entries=entries, provenance=artifact.provenance)


def render_demotion(item: Demotion) -> str:
    """One demotion as an operator reads it, in the staircase's own spelling."""
    return (
        f"{item.task_type}: {render_pair(*item.demoted)} made no progress on "
        f"{item.no_progress} contribution(s) this Run, so it steps up to "
        f"{render_pair(*item.replacement)} — which nothing has measured"
    )


def render_refusal(item: RefusedDemotion) -> str:
    """Why a failing pair was left in force, phrased for the operator who has it."""
    pair = render_pair(*item.pair)
    head = (
        f"{item.task_type}: {pair} made no progress on {item.no_progress} "
        f"contribution(s) this Run, and was left in force because "
    )
    if item.reason is DemotionRefusal.NO_STAIRCASE:
        return head + (
            "this Run resolved no price staircase, so there is no measured "
            "ordering to step up through"
        )
    if item.reason is DemotionRefusal.PAIR_OFF_STAIRCASE:
        return head + (
            "the live roster no longer offers that pair at all — the roster has "
            "moved under the artifact"
        )
    if item.reason is DemotionRefusal.TOP_OF_STAIRCASE:
        return head + "it is already the most expensive pair the roster offers"
    return head + (
        "it is already a provisional pair a previous Demotion installed, and "
        "stepping it again would walk this Task type up the staircase unmeasured"
    )


def demote_after_run(
    *,
    repo_root: Path | None,
    config: RunConfig,
    staircase: PriceStaircase,
    contributions: Iterable[Contribution],
    git: GitClient,
    warn: Callable[[str], None],
) -> DemotionPlan:
    """Count, decide, rewrite and commit — once, after the **Run** has ended.

    The whole of Demotion's contact with the world. It is called from
    :func:`git_loopy.loop.run` at the quiescent point where every **Lane** has
    finalized and nothing is in flight, which is what dissolves the two problems
    a mid-Run demotion would have had: no concurrent Lane can race it over one
    shared tracked file, and no mid-Run mechanism for committing an arbitrary
    tracked file has to be invented (ADR-0030).

    **Nothing here starts a search.** ADR-0028's notify-don't-act rule applies
    unchanged: an implicit trigger would convert an unattended Run into a
    benchmark suite. The module imports no **Trial**, no search and no
    dispatcher, so that holds structurally rather than by care.

    **Every failure is swallowed**, with one exception's worth of nuance. A
    Demotion that cannot be committed is still written to disk — the decision was
    correct, and the next Run should honour it — but the git failure is reported
    and the Run's exit code is untouched. Rewriting routing is never a
    precondition for the work the Run already did.

    Args:
        repo_root: The repository, or ``None`` off one — the artifact is a
            tracked file, so off-repo there is nothing to rewrite.
        config: The Run's resolved config, read for ``parallel`` (through
            :func:`~git_loopy.routing_scope.routing_in_force`, the family's one
            routing-scope answer), ``routing`` and ``demotion_threshold``.
        staircase: The Run's price staircase, or a refused one.
        contributions: The finalized **Lane contributions** — in Parallel mode,
            :attr:`~git_loopy.rolling_scheduler.RollingScheduler.finalized`.
        git: The repository's git client, used for exactly one path-scoped
            commit.
        warn: The Run's non-fatal warning sink.

    Returns:
        What was decided, so a caller can assert on it without parsing prose.
    """
    if repo_root is None or not routing_in_force(config.parallel):
        return DemotionPlan()
    try:
        artifact = load_measured_routing(measured_routing_path(repo_root))
    except SettingsError as exc:
        # The Run's own config resolution already loaded and owns failing on this
        # file; re-reporting it fatally here would make a routing correction a
        # precondition for finishing a Run that has already done its work.
        warn(str(exc))
        return DemotionPlan()
    if not artifact.entries:
        return DemotionPlan()
    plan = plan_demotions(
        entries=artifact.entries,
        in_force=config.routing,
        tally=tally_no_progress(contributions),
        staircase=staircase,
        threshold=config.demotion_threshold,
    )
    if not plan:
        return plan
    for refusal in plan.refusals:
        warn(f"Demotion: {render_refusal(refusal)}.")
    if not plan.demotions:
        warn(RECALIBRATE_HINT)
        return plan
    for item in plan.demotions:
        warn(f"Demotion: {render_demotion(item)}.")
    warn(RECALIBRATE_HINT)
    path = measured_routing_path(repo_root)
    try:
        write_measured_routing(repo_root, apply_demotions(artifact, plan))
    except OSError as exc:
        warn(f"Demotion: the measured routing artifact could not be written ({exc}).")
        return plan
    try:
        git.commit_paths(_commit_message(plan), [path])
    except GitError as exc:
        warn(
            f"Demotion: {path.name} was rewritten but could not be committed "
            f"({exc}); commit it yourself, or `git checkout` it to undo."
        )
    return plan


def _commit_message(plan: DemotionPlan) -> str:
    """The commit an operator reviews, saying what moved and on what evidence.

    Machine-authored and addressed to a reader of ``git log``: ADR-0028 keeps the
    ledger in git rather than in the artifact, so this message is where a past
    Demotion explains itself.
    """
    subject = (
        f"chore(routing): demote {plan.demotions[0].task_type}"
        if len(plan.demotions) == 1
        else f"chore(routing): demote {len(plan.demotions)} task types"
    )
    body = "\n".join(f"- {render_demotion(item)}." for item in plan.demotions)
    return (
        f"{subject}\n\n"
        f"{body}\n\n"
        f"Written by Demotion (ADR-0030) at the end of a Run: a measured pair "
        f"that stopped making progress on real work steps up the price "
        f"staircase. The replacement is recorded 'provisional' because nothing "
        f"has measured it. Re-calibrate to replace it with evidence, or revert "
        f"this commit to put the measured pair back."
    )
