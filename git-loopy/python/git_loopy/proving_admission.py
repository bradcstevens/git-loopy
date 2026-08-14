"""``git_loopy.proving_admission`` — admitting a mined candidate by replaying it.

Mining (:mod:`git_loopy.proving_set`) selects closed issues on **metadata**: a
closing commit, a well-formed body, at least one changed test path. That last
rule is a *proxy*, and the property it stands in for — the fix's own tests **fail
before the fix and pass after** — is the only reason a replay means anything
(ADR-0027). This module is the mandatory validation pass that establishes it: it
replays every candidate with its **real historical fix** and admits only the ones
that genuinely fail before and pass after.

It is also the only defence against a **red base commit**. The gate is fail-fast
and whole-repo, so a base that is red for an unrelated reason — a flaky test, a
since-fixed lint, a toolchain that moved — fails every pair at every rung, the
search walks the whole staircase, exhausts its ceiling and reports ``incomplete``
with nothing to distinguish *"every model is too weak"* from *"this task was
never runnable."*

Design notes:

* **Admission is the Trial's replay, with the historical fix in the agent's
  place.** Both halves select the base commit's covering loops with
  :func:`~git_loopy.trial.covering_loops` and run them through
  :func:`~git_loopy.trial.run_oracle`, so what admission verified is what a
  **Trial** measures. A second implementation of either could drift, and a drift
  would mean a task admitted by one instrument and scored by another.
* **It is separate from mining because it does a different kind of work.** Mining
  reads metadata and touches no worktree; admission checks out commits and runs
  tests. It spends wall clock and never an **AI Credit**: there is no session
  here, no model, no pair.
* **A candidate's failure is its own exclusion, never a raise.** One unreachable
  worktree must not throw away an admission pass that has already replayed forty
  candidates. Every exclusion carries its reason, in the same spirit as mining's
  — a corpus a loop engineer cannot judge the representativeness of is a corpus
  they have to trust.
* **Nothing survives.** The worktree and its branch are torn down on admission,
  on exclusion, on an exception and on interruption, exactly as a **Trial**'s
  are. The operator's branch and main worktree are never addressed.
* **The admitted set is its own type.** :class:`~git_loopy.proving_set.MinedProvingSet`
  holds *candidates*; only an :class:`AdmittedProvingSet` holds **Proving tasks**,
  and it is the one object that answers both halves of a measurement — the pins a
  search draws and the candidates a Trial runner resolves them against.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from git_loopy.gate import GateError, GateResult, GateRunner, parse_feedback_loops
from git_loopy.git import GitClient, GitError
from git_loopy.measured_routing import ProvingTask
from git_loopy.proving_set import MinedProvingSet, ProvingCandidate
from git_loopy.task_type_classifier import ClassifierPair
from git_loopy.trial import (
    apply_oracle,
    covering_loops,
    make_oracle_gate_runner,
    read_agents_md,
    restore_paths,
    run_oracle,
)
from git_loopy.worktree import WorktreeSetup

__all__ = [
    "AdmissionExclusion",
    "AdmissionReason",
    "AdmittedProvingSet",
    "AdmittedProvingTask",
    "CandidateVerifier",
    "ReplayVerifier",
    "Verdict",
    "admission_branch_name",
    "admit",
    "covering_loops",
    "run_oracle",
]


class AdmissionReason(Enum):
    """Why a mined candidate did not become a **Proving task**.

    A closed vocabulary, in the order admission can reach it. Deliberately
    separate from :class:`~git_loopy.proving_set.ExclusionReason`: mining's
    reasons are all *"this could never be replayed"* and these are all *"this was
    replayed and here is what happened"*, and folding them into one enum would
    lose exactly the distinction an operator growing the corpus needs.
    """

    #: The oracle was already green at the base commit, so the task measures
    #: nothing. A pair drawing it would be promoted for having done no work.
    ALREADY_SOLVED = "already_solved"
    #: The oracle was still red with the *historical* fix applied. If the commit
    #: that actually shipped cannot satisfy these tests here, no candidate pair
    #: can — the task is unsolvable, and an unsolvable task reads as every model
    #: being too weak.
    FIX_DID_NOT_PASS = "fix_did_not_pass"
    #: The base commit could not produce a usable result at all: no worktree, no
    #: prepared environment, no loop covering the oracle, or an oracle that could
    #: not be asked. This is the red-base filter, and it is the reason ADR-0027
    #: makes admission mandatory rather than advisory.
    UNRUNNABLE = "unrunnable"


@dataclass(frozen=True)
class AdmittedProvingTask:
    """One candidate whose fail-before / pass-after property has been established.

    Attributes:
        candidate: The mined candidate, verbatim — a **Trial** replays exactly
            this, from the same base commit with the same oracle paths.
        oracle_loops: The loop names the oracle ran, selected from the *base
            commit's* feedback-loop table. Recorded because the gate a replay
            runs is the one that commit declared, so a reader can check which
            instrument admitted this task rather than assume it.
        base_failure: How the oracle failed before the fix. The evidence for
            *fail-before*: an admitted task whose recorded failure is an
            infrastructure one is an admission bug a reader can see.
        seconds: Wall clock the verification took. Admission's cost is time, and
            a **Calibration**'s wall-clock ceiling is spent on the Trials that
            follow — so this is the number that says whether refreshing a corpus
            is an afternoon or a fortnight.
    """

    candidate: ProvingCandidate
    oracle_loops: tuple[str, ...]
    base_failure: str
    seconds: float = 0.0

    @property
    def issue(self) -> int:
        return self.candidate.issue

    @property
    def task_type(self) -> str:
        return self.candidate.task_type

    def pin(self) -> ProvingTask:
        """The artifact record that identifies this task exactly (ADR-0028)."""
        return self.candidate.pin()


@dataclass(frozen=True)
class AdmissionExclusion:
    """One candidate the replay refused, and why."""

    issue: int
    reason: AdmissionReason
    detail: str | None = None


#: What verifying one candidate yields: a task, or the reason it is not one.
Verdict = AdmittedProvingTask | AdmissionExclusion


@dataclass(frozen=True)
class AdmittedProvingSet:
    """The corpus a **Calibration** is allowed to measure against.

    A distinct type from :class:`~git_loopy.proving_set.MinedProvingSet` on
    purpose: the difference between a mined candidate and an admitted task is the
    whole of this ticket, and a single type carrying both would make *"unverified
    candidates are never used"* a convention rather than a fact about what a
    caller can reach.

    Attributes:
        tasks: The admitted tasks, in the order their candidates were mined.
        exclusions: Every candidate the replay refused, with its reason.
        classifier_pin: The **Task-type classifier** pair the corpus was
            stratified by, carried through from mining. ADR-0028 refreshes a
            **Proving set** when that pin moves, and a pin dropped here would
            make the check impossible for the only set that is measured against.
    """

    tasks: tuple[AdmittedProvingTask, ...] = ()
    exclusions: tuple[AdmissionExclusion, ...] = ()
    classifier_pin: ClassifierPair | None = None

    def by_task_type(self) -> Mapping[str, tuple[AdmittedProvingTask, ...]]:
        """The admitted tasks grouped by **Task type**, which is how they are measured."""
        grouped: dict[str, list[AdmittedProvingTask]] = {}
        for task in self.tasks:
            grouped.setdefault(task.task_type, []).append(task)
        return {key: tuple(value) for key, value in grouped.items()}

    def candidates(self) -> tuple[ProvingCandidate, ...]:
        """What a :class:`~git_loopy.trial.ReplayTrialRunner` is constructed from."""
        return tuple(task.candidate for task in self.tasks)

    def pins_for(self, task_type: str) -> tuple[ProvingTask, ...]:
        """What a search measures one **Task type** against.

        The runner resolves a request's pin against the candidates it holds and
        goes red on one it does not know; that is only a guarantee if the pins
        and the candidates come from this same object.
        """
        return tuple(task.pin() for task in self.by_task_type().get(task_type, ()))


@runtime_checkable
class CandidateVerifier(Protocol):
    """The seam: replay one candidate and say whether it may be measured against.

    Production is :class:`ReplayVerifier`, which needs a real repository and runs
    real tests. Scripted in tests, exactly as the gate and worktree seams are.
    """

    def verify(
        self, candidate: ProvingCandidate
    ) -> AdmittedProvingTask | AdmissionExclusion:
        """Replay ``candidate`` with its real historical fix and return the verdict."""
        ...


def admit(
    mined: MinedProvingSet,
    verifier: CandidateVerifier,
    *,
    on_verdict: Callable[
        [ProvingCandidate, AdmittedProvingTask | AdmissionExclusion], None
    ]
    | None = None,
) -> AdmittedProvingSet:
    """Verify every mined candidate, and admit only the ones that prove themselves.

    Args:
        mined: What :func:`~git_loopy.proving_set.mine_proving_set` found. Its own
            exclusions stay where they are — they are a different vocabulary,
            answering *"why was this never a candidate?"* rather than *"what
            happened when it was replayed?"*.
        verifier: The replay.
        on_verdict: Called with each candidate and its verdict as it lands.
            Admission runs tests, so a corpus-sized pass takes real time and must
            not look like a hang (#372).

    Returns:
        The :class:`AdmittedProvingSet`: verified tasks, every refusal with its
        reason, and the classifier pin the corpus was stratified by.
    """
    tasks: list[AdmittedProvingTask] = []
    exclusions: list[AdmissionExclusion] = []
    for candidate in mined.candidates:
        verdict = verifier.verify(candidate)
        if isinstance(verdict, AdmittedProvingTask):
            tasks.append(verdict)
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
# The replay                                                                   #
# --------------------------------------------------------------------------- #

_ADMISSION_SEQUENCE = itertools.count(1)


def admission_branch_name(admission_id: str, sequence: int) -> str:
    """The working branch for one verification.

    Sequenced for the reason :func:`~git_loopy.trial.trial_branch_name` is: a
    branch a failed teardown left behind must never be a name the next
    verification tries to create.
    """
    return f"git-loopy/admission/{admission_id}/candidate-{sequence}"


class ReplayVerifier:
    """The production :class:`CandidateVerifier`: a real worktree at a real commit.

    Composes the same seams a **Trial** does and modifies none of them — the git
    client's worktree creation, :class:`~git_loopy.worktree.WorktreeSetup`, and
    the oracle gate runner pointed at a generated single-purpose table.

    Args:
        git: The operator's repository client. A worktree is added at the
            candidate's base commit and removed again; the operator's own branch
            and main worktree are never addressed.
        worktree_parent: Where verification worktrees are created. Defaults to
            the repository's *sibling* directory, never inside it (ADR-0008).
        setup: The worktree preparation seam. Defaults to
            :class:`~git_loopy.worktree.CommandWorktreeSetup`'s auto-detect,
            because a fresh worktree has the source but not the environment the
            loops need.
        oracle: The fail-to-pass instrument. Defaults to
            :func:`~git_loopy.trial.make_oracle_gate_runner`, the same one a
            Trial scores with.
        gate_timeout_seconds: The per-loop bound the oracle inherits (#374).
        admission_id: Identifies this admission pass, and namespaces its
            branches.
        slot: Which worktree this verifier uses. Serial today — one verifier, one
            slot — and the parameter is what a concurrent admission pass would
            vary, exactly as a **Trial**'s slot does.
        clock: The monotonic source the wall clock is measured from.
        warn: Optional diagnostic sink for the things that are nobody's failure,
            like a worktree that would not tear down.
    """

    def __init__(
        self,
        *,
        git: GitClient,
        worktree_parent: Path | None = None,
        setup: WorktreeSetup | None = None,
        oracle: GateRunner | None = None,
        gate_timeout_seconds: float | None = None,
        admission_id: str = "admission",
        slot: int = 0,
        clock: Callable[[], float] = time.monotonic,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._git = git
        self._worktree_parent = Path(
            worktree_parent if worktree_parent is not None else git.root.parent
        )
        self._setup = setup if setup is not None else _default_setup()
        timeout = (
            gate_timeout_seconds
            if gate_timeout_seconds is not None
            else _default_gate_timeout()
        )
        self._oracle = (
            oracle
            if oracle is not None
            else make_oracle_gate_runner(timeout_seconds=timeout)
        )
        self._admission_id = admission_id
        self._slot = slot
        self._clock = clock
        self._warn = warn
        self._leaked: list[tuple[Path | None, str | None]] = []

    # -- the seam ---------------------------------------------------------- #

    def verify(
        self, candidate: ProvingCandidate
    ) -> AdmittedProvingTask | AdmissionExclusion:
        """Replay ``candidate`` with its real historical fix, in its own worktree.

        Never raises for anything one candidate can be responsible for: a base
        commit this clone does not carry, a worktree that would not prepare, an
        oracle no loop covers, an oracle that could not be asked — each comes
        back as an exclusion with its reason, because one bad candidate must not
        end an admission pass. A :class:`BaseException` — an operator's Ctrl-C —
        *does* pass through, with the worktree torn down on the way past.
        """
        started = self._clock()
        path = self._worktree_path()
        self._reclaim(path)
        branch = admission_branch_name(self._admission_id, next(_ADMISSION_SEQUENCE))
        try:
            self._git.add_worktree(path, branch=branch, base=candidate.base_commit)
        except GitError as exc:
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                f"the worktree could not be created: {exc}",
            )

        try:
            return self._replay(candidate, path, started)
        except Exception as exc:  # noqa: BLE001 — one candidate cannot end the pass
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                f"the replay raised {type(exc).__name__}: {exc}",
            )
        finally:
            self._teardown(path, branch)

    # -- the replay -------------------------------------------------------- #

    def _replay(
        self, candidate: ProvingCandidate, path: Path, started: float
    ) -> AdmittedProvingTask | AdmissionExclusion:
        prepared = self._prepare(path)
        if prepared is not None:
            return _excluded(candidate, AdmissionReason.UNRUNNABLE, prepared)

        loops = covering_loops(
            parse_feedback_loops(read_agents_md(path)), candidate.oracle_paths
        )
        if not loops:
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                "no feedback loop at the base commit covers the oracle paths "
                f"{list(candidate.oracle_paths)}",
            )
        oracle_names = tuple(loop.name for loop in loops)

        apply_oracle(
            path, commit=candidate.oracle_commit, paths=candidate.oracle_paths
        )
        try:
            before = run_oracle(self._oracle, path, loops)
        except GateError as exc:
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                f"the oracle could not be asked at the base commit: {exc}",
            )
        if before.passed:
            return _excluded(
                candidate,
                AdmissionReason.ALREADY_SOLVED,
                f"the oracle {list(oracle_names)} was already green at "
                f"{candidate.base_commit[:12]}",
            )
        broken = _infrastructure(before)
        if broken is not None:
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                f"the oracle was red at the base commit for a reason that is not "
                f"a test result: {broken}",
            )

        restore_paths(
            path,
            commit=candidate.oracle_commit,
            paths=self._git.changed_paths(candidate.oracle_commit),
        )
        # Prepared *again*, because the fix is entitled to have shipped a
        # dependency: an environment built from the base tree that cannot build
        # the fixed one would fail the historical fix's own tests and exclude the
        # task under a reason — "the fix did not pass" — that is not what
        # happened.
        prepared = self._prepare(path)
        if prepared is not None:
            return _excluded(candidate, AdmissionReason.UNRUNNABLE, prepared)

        try:
            after = run_oracle(self._oracle, path, loops)
        except GateError as exc:
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                f"the oracle could not be asked with the fix applied: {exc}",
            )
        broken = _infrastructure(after)
        if broken is not None:
            return _excluded(
                candidate,
                AdmissionReason.UNRUNNABLE,
                f"the oracle was red with the historical fix applied for a reason "
                f"that is not a test result: {broken}",
            )
        if not after.passed:
            return _excluded(
                candidate,
                AdmissionReason.FIX_DID_NOT_PASS,
                f"the historical fix left {_why(after)} red",
            )

        return AdmittedProvingTask(
            candidate=candidate,
            oracle_loops=oracle_names,
            base_failure=_why(before),
            seconds=self._clock() - started,
        )

    def _prepare(self, path: Path) -> str | None:
        """Run the worktree setup, returning why it refused — or ``None`` on success.

        A fresh worktree has the source but not the environment the loops need.
        """
        setup = self._setup.run(path)
        if setup.passed:
            return None
        return (
            f"the worktree would not prepare: {setup.command!r} exited "
            f"{setup.returncode}"
        )

    # -- teardown ---------------------------------------------------------- #

    def _teardown(self, path: Path | None, branch: str | None) -> None:
        """Remove the worktree *and* its branch — admission leaves no breadcrumb.

        Best-effort throughout, for the reason a **Trial**'s teardown is: a
        teardown that raised would replace a verdict with a crash. What it does
        *not* do is forget: whatever would not go is recorded for
        :meth:`_reclaim`, because the slot path is the same for every candidate
        and one leaked directory would otherwise turn the whole rest of the pass
        into ``unrunnable`` exclusions that nothing was wrong with.
        """
        if path is not None:
            try:
                self._git.remove_worktree(path, force=True)
                path = None
            except Exception as exc:  # noqa: BLE001
                self._report(
                    f"the admission worktree at {path} could not be removed: {exc}"
                )
        if branch is not None:
            try:
                self._git.delete_branch(branch)
                branch = None
            except Exception as exc:  # noqa: BLE001
                self._report(
                    f"the admission branch {branch} could not be deleted: {exc}"
                )
        if path is not None or branch is not None:
            self._leaked.append((path, branch))

    def _reclaim(self, path: Path) -> None:
        """Retry every teardown that failed, before the slot is used again."""
        leaked, self._leaked = self._leaked, []
        for stale_path, stale_branch in leaked:
            self._teardown(stale_path, stale_branch)
        if path.exists():
            # Namespaced by the admission pass *and* the slot, so whatever sits
            # here can only be a verification of ours that did not clean up.
            self._report(f"reclaiming a leaked admission worktree at {path}")
            self._teardown(path, None)

    # -- helpers ----------------------------------------------------------- #

    def _worktree_path(self) -> Path:
        """This verifier's worktree, in a sibling directory outside the repo (ADR-0008)."""
        return self._worktree_parent / (
            f"{self._git.root.name}-admission-{self._admission_id}-slot-{self._slot}"
        )

    def _report(self, message: str) -> None:
        if self._warn is None:
            return
        try:
            self._warn(message)
        except Exception:  # pragma: no cover - defensive
            pass


def _excluded(
    candidate: ProvingCandidate, reason: AdmissionReason, detail: str
) -> AdmissionExclusion:
    return AdmissionExclusion(issue=candidate.issue, reason=reason, detail=detail)


def _why(result: GateResult) -> str:
    if result.failure is None:  # pragma: no cover - defensive
        return "an unnamed loop"
    return f"{result.failure.name!r} ({result.failure.summary})"


def _infrastructure(result: GateResult) -> str | None:
    """Why this red result is not a test result — or ``None`` when it is one.

    The red-base filter is the reason admission is mandatory, and *"the loop
    exited non-zero"* is not the only way a base commit is red for a reason that
    has nothing to do with the task. A loop that **timed out** never produced a
    result at all, and a loop whose **command was not found** (``127``) was never
    run. Read as a test result, the first would let a slow cold start count as
    *fail-before* — admitting a task on evidence that was a stopwatch — and the
    second would exclude a perfectly good candidate as unsolvable because a tool
    is missing from this host.

    Both are :class:`~git_loopy.gate.LoopFailure` distinctions (#374) rather than
    new ones invented here; this only refuses to collapse them.
    """
    failure = result.failure
    if failure is None:
        return None
    if failure.timed_out:
        return f"{failure.name!r} {failure.summary}"
    if failure.returncode == 127:
        return f"{failure.name!r} command was not found: {failure.command!r}"
    return None


def _default_setup() -> WorktreeSetup:
    from git_loopy.worktree import CommandWorktreeSetup

    return CommandWorktreeSetup()


def _default_gate_timeout() -> float:
    import os

    from git_loopy.gate import resolve_gate_timeout_seconds

    return resolve_gate_timeout_seconds(os.environ)
