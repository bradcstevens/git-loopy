"""Tests for ``git_loopy.proving_admission`` — admission (#380, ADR-0027).

Mining selects on metadata; admission is the **mandatory validation pass** that
replays each candidate with its real historical fix and admits it only when the
oracle **fails before and passes after**.

Offline throughout: real temporary git repositories for the replay half (matching
how :mod:`git_loopy.git`, :mod:`git_loopy.worktree` and :mod:`git_loopy.trial`
are already tested), trivial POSIX commands for the real gate runner, and no
tracker, network or credential anywhere.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
import pytest

from git_loopy.gate import GateError, GateResult, LoopFailure
from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.proving_admission import (
    AdmissionExclusion,
    AdmissionReason,
    AdmittedProvingSet,
    AdmittedProvingTask,
    ReplayVerifier,
    admit,
)
from git_loopy.proving_set import MinedProvingSet, ProvingCandidate
from git_loopy.task_type_classifier import ClassifierPair
from git_loopy.worktree import SetupResult

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH; admission cannot create a worktree",
)


# --------------------------------------------------------------------------- #
# The fold: mined candidates in, an admitted set out                           #
# --------------------------------------------------------------------------- #


def _candidate(issue: int, *, task_type: str = "bugfix") -> ProvingCandidate:
    return ProvingCandidate(
        issue=issue,
        task_type=task_type,
        base_commit=f"base{issue:040d}"[:40],
        oracle_commit=f"orac{issue:040d}"[:40],
        oracle_paths=("tests/check.sh",),
        task_text="## What to build\n\nThe answer must be 42.\n",
    )


def _admitted(candidate: ProvingCandidate) -> AdmittedProvingTask:
    return AdmittedProvingTask(
        candidate=candidate,
        oracle_loops=("Tests",),
        base_failure="'Tests' (exit 1)",
        seconds=1.0,
    )


class _ScriptedVerifier:
    """A :class:`CandidateVerifier` whose verdict per issue is written down."""

    def __init__(self, verdicts: dict[int, object]) -> None:
        self._verdicts = verdicts
        self.seen: list[int] = []

    def verify(self, candidate: ProvingCandidate) -> object:
        self.seen.append(candidate.issue)
        return self._verdicts[candidate.issue]


def test_only_a_verified_candidate_is_admitted() -> None:
    """The whole ticket in one case: a candidate is admitted, or it is excluded.

    Mining's rules are each *necessary and none sufficient* — "the fixing commit
    touched a test path" does not mean that test fails before the fix and passes
    after (ADR-0027). Admission is what turns that proxy into a verified property
    of every task in the set.
    """
    good, bad = _candidate(11), _candidate(12)
    verifier = _ScriptedVerifier(
        {
            11: _admitted(good),
            12: AdmissionExclusion(
                issue=12, reason=AdmissionReason.ALREADY_SOLVED, detail=None
            ),
        }
    )

    admitted = admit(MinedProvingSet(candidates=(good, bad)), verifier)

    assert [task.candidate.issue for task in admitted.tasks] == [11]
    assert [exclusion.issue for exclusion in admitted.exclusions] == [12]


def test_every_candidate_is_replayed_before_admission() -> None:
    """No candidate reaches the set without having been through the verifier."""
    candidates = tuple(_candidate(issue) for issue in (5, 6, 7))
    verifier = _ScriptedVerifier({c.issue: _admitted(c) for c in candidates})

    admitted = admit(MinedProvingSet(candidates=candidates), verifier)

    assert verifier.seen == [5, 6, 7]
    assert len(admitted.tasks) == 3


def test_every_exclusion_carries_its_reason() -> None:
    """Reported, never counted — #303's discipline, inherited from mining.

    A count tells a loop engineer the corpus is partial and nothing about whether
    it is representative.
    """
    already, unrunnable, unfixed = _candidate(1), _candidate(2), _candidate(3)
    verifier = _ScriptedVerifier(
        {
            1: AdmissionExclusion(issue=1, reason=AdmissionReason.ALREADY_SOLVED),
            2: AdmissionExclusion(
                issue=2, reason=AdmissionReason.UNRUNNABLE, detail="no covering loop"
            ),
            3: AdmissionExclusion(issue=3, reason=AdmissionReason.FIX_DID_NOT_PASS),
        }
    )

    admitted = admit(
        MinedProvingSet(candidates=(already, unrunnable, unfixed)), verifier
    )

    assert admitted.tasks == ()
    assert [exclusion.reason for exclusion in admitted.exclusions] == [
        AdmissionReason.ALREADY_SOLVED,
        AdmissionReason.UNRUNNABLE,
        AdmissionReason.FIX_DID_NOT_PASS,
    ]
    assert admitted.exclusions[1].detail == "no covering loop"


def test_the_classifier_pin_survives_admission() -> None:
    """The **Task type** taxonomy the corpus was stratified by travels with it.

    ADR-0028 refreshes a **Proving set** when the classifier pin moves, and a
    pin that mining recorded but admission dropped would make that check
    impossible for the only set a **Calibration** actually measures against.
    """
    pin = ClassifierPair(model="cheap-model", effort="low")
    candidate = _candidate(9)
    verifier = _ScriptedVerifier({9: _admitted(candidate)})

    admitted = admit(
        MinedProvingSet(candidates=(candidate,), classifier_pin=pin), verifier
    )

    assert admitted.classifier_pin == pin


def test_a_task_type_yields_only_its_own_admitted_pins() -> None:
    """A Calibration measures one **Task type** at a time, against admitted pins only."""
    bugfix, docs = _candidate(21), _candidate(22, task_type="docs")
    verifier = _ScriptedVerifier({21: _admitted(bugfix), 22: _admitted(docs)})

    admitted = admit(MinedProvingSet(candidates=(bugfix, docs)), verifier)

    assert [task.candidate.issue for task in admitted.by_task_type()["docs"]] == [22]
    assert [pin.issue for pin in admitted.pins_for("bugfix")] == [21]
    assert admitted.pins_for("chore") == ()


def test_the_pins_a_search_measures_are_the_pins_a_trial_resolves() -> None:
    """One object answers both halves, so the two cannot disagree.

    :class:`~git_loopy.trial.ReplayTrialRunner` resolves a request's pin against
    the candidates it was built with and goes red on a pin it does not know. That
    is only a real guarantee if the pins the search draws and the candidates the
    runner holds come from the same admitted set.
    """
    candidate = _candidate(31)
    verifier = _ScriptedVerifier({31: _admitted(candidate)})

    admitted = admit(MinedProvingSet(candidates=(candidate,)), verifier)

    assert admitted.candidates() == (candidate,)
    assert admitted.pins_for("bugfix") == (candidate.pin(),)


def test_a_verdict_is_reported_as_it_lands() -> None:
    """Admission runs tests, so a long pass must not look like a hang (#372)."""
    candidates = tuple(_candidate(issue) for issue in (41, 42))
    verifier = _ScriptedVerifier(
        {
            41: _admitted(candidates[0]),
            42: AdmissionExclusion(issue=42, reason=AdmissionReason.ALREADY_SOLVED),
        }
    )
    seen: list[tuple[int, bool]] = []

    admit(
        MinedProvingSet(candidates=candidates),
        verifier,
        on_verdict=lambda candidate, verdict: seen.append(
            (candidate.issue, isinstance(verdict, AdmittedProvingTask))
        ),
    )

    assert seen == [(41, True), (42, False)]


# --------------------------------------------------------------------------- #
# A repository carrying one replayable fix                                     #
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _write(repo: Path, relative: str, body: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    if relative.endswith(".sh"):
        path.chmod(0o755)


@dataclass(frozen=True)
class _History:
    """A repository carrying one fix, and the two commits that pin it."""

    repo: Path
    base_commit: str
    oracle_commit: str


def _history(
    tmp_path: Path,
    *,
    fix_source: str = "42",
    test_passes_at_base: bool = False,
    table: str | None = None,
) -> _History:
    """A repo whose second commit is a fix with its own test change.

    ``fix_source`` and ``test_passes_at_base`` are the two knobs admission exists
    to tell apart: a fix whose own test was already green before it landed, and a
    fix whose own test is still red after it landed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
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
        _git(repo, "config", key, value)

    _write(repo, "src/answer.txt", "42" if test_passes_at_base else "41")
    _write(
        repo,
        "AGENTS.md",
        table
        if table is not None
        else (
            "# Agents\n\n"
            "## Feedback loops\n\n"
            "| Loop | Command |\n"
            "| --- | --- |\n"
            "| Tests | sh tests/*.sh |\n"
            "| Docs | test -f README.md |\n"
        ),
    )
    _write(repo, "README.md", "readme\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base_commit = _git(repo, "rev-parse", "HEAD").strip()

    _write(repo, "src/answer.txt", fix_source)
    _write(
        repo,
        "tests/check-answer.sh",
        '#!/bin/sh\ntest "$(cat src/answer.txt)" = "42"\n',
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: the answer is 42\n\nCloses #7")
    oracle_commit = _git(repo, "rev-parse", "HEAD").strip()
    return _History(repo=repo, base_commit=base_commit, oracle_commit=oracle_commit)


def _replayable(history: _History, *, issue: int = 7) -> ProvingCandidate:
    return ProvingCandidate(
        issue=issue,
        task_type="bugfix",
        base_commit=history.base_commit,
        oracle_commit=history.oracle_commit,
        oracle_paths=("tests/check-answer.sh",),
        task_text="## What to build\n\nThe answer must be 42.\n",
    )


class _PassingSetup:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def run(self, worktree: Path) -> SetupResult:
        self.calls.append(worktree)
        return SetupResult(command=None)


class _RefusingSetup:
    def run(self, worktree: Path) -> SetupResult:
        return SetupResult(
            command="uv sync",
            returncode=2,
            output_tail="no network",
        )


def _verifier(
    history: _History,
    tmp_path: Path,
    *,
    setup: object | None = None,
    oracle: object | None = None,
) -> ReplayVerifier:
    return ReplayVerifier(
        git=SubprocessGitClient(history.repo),
        worktree_parent=tmp_path / "admission",
        setup=setup if setup is not None else _PassingSetup(),
        oracle=oracle,
        admission_id="adm01",
    )


# --------------------------------------------------------------------------- #
# The replay: fail before, pass after                                          #
# --------------------------------------------------------------------------- #


def test_a_task_that_fails_before_the_fix_and_passes_after_is_admitted(
    tmp_path: Path,
) -> None:
    """The property the whole replay rests on, established rather than assumed.

    The oracle runs twice against a real worktree at a real historical commit:
    red with the fix's test present and the fix absent, green once the historical
    fix is applied. Nothing else admits a task.
    """
    history = _history(tmp_path)
    candidate = _replayable(history)

    verdict = _verifier(history, tmp_path).verify(candidate)

    assert isinstance(verdict, AdmittedProvingTask)
    assert verdict.candidate == candidate
    assert verdict.oracle_loops == ("Tests",)
    assert verdict.base_failure


def test_a_task_already_green_at_the_base_commit_is_excluded_as_already_solved(
    tmp_path: Path,
) -> None:
    """An already-solved task measures nothing, and would promote whoever drew it."""
    history = _history(tmp_path, test_passes_at_base=True)

    verdict = _verifier(history, tmp_path).verify(_replayable(history))

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.ALREADY_SOLVED
    assert verdict.issue == 7


def test_a_fix_that_leaves_its_own_tests_red_is_excluded(tmp_path: Path) -> None:
    """The historical fix is the control: if *it* cannot pass, no pair can.

    A task admitted here would be unsolvable, and an unsolvable task fails every
    pair at every rung — which reads as every model being too weak.
    """
    history = _history(tmp_path, fix_source="43")

    verdict = _verifier(history, tmp_path).verify(_replayable(history))

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.FIX_DID_NOT_PASS
    assert verdict.detail is not None


def test_the_historical_fix_is_applied_whole_and_not_just_its_tests(
    tmp_path: Path,
) -> None:
    """Admission applies the *fix*; a **Trial** applies only its tests.

    The two replays differ in exactly one place, and this is it — so the source
    change the agent is meant to reinvent must be on disk for the second oracle
    run and absent from the first.
    """
    history = _history(tmp_path)
    seen: list[str] = []

    class _Watching:
        def run(self, worktree: Path) -> GateResult:
            seen.append((worktree / "src/answer.txt").read_text(encoding="utf-8"))
            return (
                GateResult.green(("Tests",))
                if len(seen) > 1
                else GateResult.red(
                    ("Tests",),
                    LoopFailure(
                        name="Tests", command="sh", returncode=1, output_tail="red"
                    ),
                )
            )

    verdict = _verifier(history, tmp_path, oracle=_Watching()).verify(
        _replayable(history)
    )

    assert isinstance(verdict, AdmittedProvingTask)
    assert [text.strip() for text in seen] == ["41", "42"]


def test_an_oracle_no_loop_covers_is_unrunnable(tmp_path: Path) -> None:
    """A base commit whose table cannot score the fix's tests scores nothing."""
    history = _history(
        tmp_path,
        table=(
            "# Agents\n\n"
            "## Feedback loops\n\n"
            "| Loop | Command |\n"
            "| --- | --- |\n"
            "| Docs | test -f README.md |\n"
        ),
    )

    verdict = _verifier(history, tmp_path).verify(_replayable(history))

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "covers" in (verdict.detail or "")


def test_a_worktree_that_will_not_prepare_is_unrunnable(tmp_path: Path) -> None:
    """A fresh worktree has the source but not the environment the loops need."""
    history = _history(tmp_path)

    verdict = _verifier(history, tmp_path, setup=_RefusingSetup()).verify(
        _replayable(history)
    )

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "prepare" in (verdict.detail or "")


def test_the_fixed_tree_is_prepared_before_its_own_tests_run(tmp_path: Path) -> None:
    """A fix is entitled to have shipped a dependency.

    The environment is built from the base tree, and a fix that adds one would
    fail its own tests in it — excluding a perfectly good candidate under a
    reason ("the fix did not pass") that is not what happened. So the tree is
    prepared again once the historical fix is on disk, and a refusal *there* is
    the host's problem rather than the task's.
    """
    history = _history(tmp_path)

    class _RefusingTheSecondTime:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, worktree: Path) -> SetupResult:
            self.calls += 1
            if self.calls == 1:
                return SetupResult(command=None)
            return SetupResult(command="uv sync", returncode=2, output_tail="no wheel")

    setup = _RefusingTheSecondTime()
    verdict = _verifier(history, tmp_path, setup=setup).verify(_replayable(history))

    assert setup.calls == 2
    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "prepare" in (verdict.detail or "")


class _Scripted:
    """An oracle returning written-down results, one per call."""

    def __init__(self, *results: GateResult) -> None:
        self._results = list(results)

    def run(self, worktree: Path) -> GateResult:
        return self._results.pop(0)


def _timed_out(name: str = "Tests") -> GateResult:
    return GateResult.red(
        (name,),
        LoopFailure(
            name=name,
            command="sh tests/*.sh",
            returncode=None,
            output_tail="",
            timed_out=True,
            timeout_seconds=900.0,
        ),
    )


def _not_found(name: str = "Tests") -> GateResult:
    return GateResult.red(
        (name,),
        LoopFailure(
            name=name,
            command="cargo test",
            returncode=127,
            output_tail="cargo: not found",
        ),
    )


def test_a_base_commit_red_on_a_timeout_is_unrunnable_not_fail_before(
    tmp_path: Path,
) -> None:
    """*Fail-before* has to be a test result, or admission proves nothing.

    A loop that exceeded its bound never produced a result at all (#374 keeps
    that distinct from an exit code on purpose). Counting it as the red half of
    fail-before would admit a task on the evidence of a stopwatch — and a slow
    cold start would then look exactly like a task no model has solved.
    """
    history = _history(tmp_path)

    verdict = _verifier(
        history,
        tmp_path,
        oracle=_Scripted(_timed_out(), GateResult.green(("Tests",))),
    ).verify(_replayable(history))

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "not a test result" in (verdict.detail or "")


def test_a_missing_tool_after_the_fix_is_unrunnable_not_an_unsolvable_task(
    tmp_path: Path,
) -> None:
    """``127`` is a host without the toolchain, not a fix that failed its tests.

    Excluded either way — but under ``fix_did_not_pass`` a loop engineer reads
    *"this repository's history is unreplayable"*, when what happened is that
    ``cargo`` is not installed.
    """
    history = _history(tmp_path)

    verdict = _verifier(
        history,
        tmp_path,
        oracle=_Scripted(
            GateResult.red(
                ("Tests",),
                LoopFailure(
                    name="Tests", command="sh", returncode=1, output_tail="red"
                ),
            ),
            _not_found(),
        ),
    ).verify(_replayable(history))

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "not a test result" in (verdict.detail or "")


def test_a_leaked_worktree_does_not_exclude_every_candidate_after_it(
    tmp_path: Path,
) -> None:
    """One failed teardown must not turn the rest of the pass into exclusions.

    Every candidate reuses the slot's path, and ``git worktree add`` refuses one
    that exists — so a single removal that did not take would read as an
    unrunnable *corpus* rather than as the one stale directory it is. The
    teardown that failed is retried before the slot is used again.
    """
    history = _history(tmp_path)
    verifier = _verifier(history, tmp_path)
    refuse = {"now": True}
    real_remove = verifier._git.remove_worktree

    def _sometimes(path: Path, *, force: bool = False) -> None:
        if refuse["now"]:
            raise GitError("the worktree is busy")
        real_remove(path, force=force)

    verifier._git.remove_worktree = _sometimes  # type: ignore[method-assign]
    first = verifier.verify(_replayable(history))
    refuse["now"] = False

    second = verifier.verify(_replayable(history, issue=8))

    assert isinstance(first, AdmittedProvingTask)
    assert isinstance(second, AdmittedProvingTask), second
    assert _worktrees(history.repo) == [str(history.repo)]
    assert _branches(history.repo) == ["main"]


def test_an_oracle_that_cannot_run_at_all_is_unrunnable_not_fail_to_pass(
    tmp_path: Path,
) -> None:
    """A red *base* is the whole point of admission, and a runner that raised is one.

    A :class:`~git_loopy.gate.GateError` is not "the oracle failed" — it is "the
    oracle could not be asked". Reading it as a failure would admit a task whose
    fail-before was infrastructure, and a red base commit fails every pair at
    every rung (ADR-0027).
    """
    history = _history(tmp_path)

    class _Broken:
        def run(self, worktree: Path) -> GateResult:
            raise GateError("no AGENTS.md table to run")

    verdict = _verifier(history, tmp_path, oracle=_Broken()).verify(
        _replayable(history)
    )

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "could not be asked" in (verdict.detail or "")


def test_a_base_commit_this_clone_does_not_carry_is_unrunnable(
    tmp_path: Path,
) -> None:
    """Nothing to check out is not a candidate's failure to prove — it is unrunnable."""
    history = _history(tmp_path)
    candidate = ProvingCandidate(
        issue=7,
        task_type="bugfix",
        base_commit="0" * 40,
        oracle_commit=history.oracle_commit,
        oracle_paths=("tests/check-answer.sh",),
        task_text="body",
    )

    verdict = _verifier(history, tmp_path).verify(candidate)

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "worktree could not be created" in (verdict.detail or "")


def test_an_unexpected_failure_is_that_candidates_exclusion_not_a_raise(
    tmp_path: Path,
) -> None:
    """One bad candidate must not throw away the whole admission pass."""
    history = _history(tmp_path)

    class _Exploding:
        def run(self, worktree: Path) -> GateResult:
            raise ValueError("boom")

    verdict = _verifier(history, tmp_path, oracle=_Exploding()).verify(
        _replayable(history)
    )

    assert isinstance(verdict, AdmissionExclusion)
    assert verdict.reason is AdmissionReason.UNRUNNABLE
    assert "ValueError" in (verdict.detail or "")


# --------------------------------------------------------------------------- #
# Isolation: nothing of the operator's is touched, and nothing survives        #
# --------------------------------------------------------------------------- #


def _branches(repo: Path) -> list[str]:
    return sorted(
        line.strip()
        for line in _git(repo, "branch", "--list", "--format=%(refname:short)")
        .strip()
        .splitlines()
        if line.strip()
    )


def _worktrees(repo: Path) -> list[str]:
    return [
        line.split(" ", 1)[1]
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]


@pytest.mark.parametrize(
    "kind",
    ["admitted", "excluded", "raised"],
)
def test_nothing_of_the_operators_survives_a_verification(
    tmp_path: Path, kind: str
) -> None:
    """Success, exclusion and exception alike leave no worktree and no branch.

    A **Calibration** mints one of these per candidate, so a leak is not a
    curiosity — it is an operator's disk and a branch list nobody asked for.
    """
    history = _history(tmp_path, fix_source="43" if kind == "excluded" else "42")
    before_branches = _branches(history.repo)
    before_head = _git(history.repo, "rev-parse", "HEAD").strip()
    before_tree = _git(history.repo, "status", "--porcelain")

    class _Exploding:
        def run(self, worktree: Path) -> GateResult:
            raise ValueError("boom")

    _verifier(
        history,
        tmp_path,
        oracle=_Exploding() if kind == "raised" else None,
    ).verify(_replayable(history))

    assert _branches(history.repo) == before_branches
    assert _worktrees(history.repo) == [str(history.repo)]
    assert _git(history.repo, "rev-parse", "HEAD").strip() == before_head
    assert _git(history.repo, "status", "--porcelain") == before_tree
    assert not list((tmp_path / "admission").glob("*"))


def test_an_interrupted_verification_still_tears_its_worktree_down(
    tmp_path: Path,
) -> None:
    """Ctrl-C stops admission; it does not leave manual cleanup behind."""
    history = _history(tmp_path)

    class _Interrupting:
        def run(self, worktree: Path) -> GateResult:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _verifier(history, tmp_path, oracle=_Interrupting()).verify(
            _replayable(history)
        )

    assert _worktrees(history.repo) == [str(history.repo)]
    assert _branches(history.repo) == ["main"]


def test_admission_runs_in_its_own_worktree_beside_the_repository(
    tmp_path: Path,
) -> None:
    """ADR-0008's rule: a private worktree, sibling to the repo, never inside it."""
    history = _history(tmp_path)
    setup = _PassingSetup()

    _verifier(history, tmp_path, setup=setup).verify(_replayable(history))

    assert setup.calls
    worktree = setup.calls[0]
    assert set(setup.calls) == {worktree}
    assert worktree != history.repo
    assert history.repo not in worktree.parents


# --------------------------------------------------------------------------- #
# The two halves cannot drift apart                                            #
# --------------------------------------------------------------------------- #


def test_admission_scores_with_the_trials_own_instrument(tmp_path: Path) -> None:
    """Admitted by one instrument and scored by another would prove nothing.

    Both halves select loops with :func:`~git_loopy.trial.covering_loops` over
    the *base commit's* table and run them through
    :func:`~git_loopy.trial.run_oracle`, so what admission verified is what a
    **Trial** measures.
    """
    from git_loopy import proving_admission, trial

    source = Path(proving_admission.__file__).read_text(encoding="utf-8")

    assert "covering_loops" in source
    assert "run_oracle" in source
    assert proving_admission.covering_loops is trial.covering_loops
    assert proving_admission.run_oracle is trial.run_oracle


def test_no_module_wires_mining_into_measurement_without_admission() -> None:
    """Structural, because "unverified candidates are never used" is not exhaustible.

    Mining produces *candidates*; only admission produces **Proving tasks**. The
    guard fires on the shape of the mistake rather than on a behaviour: a module
    that calls :func:`~git_loopy.proving_set.mine_proving_set` and then builds a
    search or a **Trial** runner out of what it got, without going through
    :func:`~git_loopy.proving_admission.admit`, is measuring against unverified
    candidates. Vacuous today — ``calibratecmd`` mines and measures nothing — and
    exactly what the spending path (#372) must not do.
    """
    import ast

    from git_loopy import proving_admission

    package = Path(proving_admission.__file__).parent
    measurement = {"ReplayTrialRunner", "run_calibration_search", "search_task_type"}
    offenders: list[str] = []
    for module in sorted(package.glob("*.py")):
        if module.name == "proving_admission.py":
            continue
        called: set[str] = set()
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name is not None:
                called.add(name)
        if "mine_proving_set" in called and called & measurement:
            if "admit" not in called:
                offenders.append(module.name)

    assert not offenders, f"{offenders} measure against unverified candidates"


def test_admission_reaches_no_tracker_and_no_model() -> None:
    """A replay reads two commits and a worktree — never the tracker, never a model.

    Not a claim that the *host* is offline: the worktree setup seam runs whatever
    this project installs with, exactly as a **Lane** and a **Trial** do. The
    claim is narrower and is the one that matters here — admission asks nobody
    anything, so it cannot spend an **AI Credit** and cannot depend on a tracker
    that has since been edited.
    """
    import ast

    from git_loopy import proving_admission

    seen: set[str] = set()
    for node in ast.walk(
        ast.parse(Path(proving_admission.__file__).read_text(encoding="utf-8"))
    ):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "proving_admission must use absolute imports only"
            assert node.module is not None
            seen.add(node.module)
            seen.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden = {
        "git_loopy.gh",
        "git_loopy.copilot_client",
        "git_loopy.session",
        "git_loopy.model_listing",
        "urllib",
        "urllib.request",
        "requests",
        "httpx",
    }
    assert not (seen & forbidden), f"proving_admission reaches for {seen & forbidden}"


def test_admission_runs_no_agent_session() -> None:
    """The fix is the historical one. Admission spends wall clock, never credits."""
    from git_loopy import proving_admission

    source = Path(proving_admission.__file__).read_text(encoding="utf-8")

    assert "IterationSession" not in source
    assert "create_session" not in source


def test_the_admitted_set_is_a_distinct_type_from_the_mined_one() -> None:
    """Two vocabularies, so a mined candidate cannot be passed off as a task."""
    assert not isinstance(MinedProvingSet(), AdmittedProvingSet)
    assert AdmittedProvingSet().tasks == ()
    assert AdmittedProvingSet().exclusions == ()
