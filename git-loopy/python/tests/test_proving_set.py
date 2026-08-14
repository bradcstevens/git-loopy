"""Tests for ``git_loopy.proving_set`` (issue #362, ADR-0027).

Mining is metadata-only, but the metadata it reads is git's — so the suite drives
the real :class:`~git_loopy.git.SubprocessGitClient` against real ``tmp_path``
repositories with synthetic history, exactly as ``test_git.py`` and the worktree
suite do. Every test is offline and needs no credentials: the tracker side is
plain :class:`~git_loopy.gh.Issue` records, and no ``gh`` process is spawned.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from git_loopy.gh import Issue
from git_loopy.git import SubprocessGitClient
from git_loopy.measured_routing import ProvingTask
from git_loopy.sources import EXCLUSION_REASONS
from git_loopy.task_type_classifier import ClassifierPair
from git_loopy.proving_set import (
    ExclusionReason,
    ProvingExclusion,
    closed_issues,
    is_test_path,
    mine_proving_set,
)
from tests.fakes import FakeGitHubClient

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH; the Proving set's git reads cannot be exercised",
)


# --------------------------------------------------------------------------- #
# Synthetic history                                                            #
# --------------------------------------------------------------------------- #


#: A body the AFK-ready discriminator accepts — both required sections.
WELL_FORMED_BODY = (
    "## What to build\n\nThe thing.\n\n## Acceptance criteria\n\n- [ ] It works.\n"
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


def _commit(path: Path, message: str, files: dict[str, str]) -> str:
    """Write ``files``, commit them under ``message``, return the full SHA."""
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
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _issue(
    number: int,
    *,
    labels: list[str] | None = None,
    body: str = WELL_FORMED_BODY,
    state: str = "CLOSED",
) -> Issue:
    return Issue(
        number=number,
        title=f"issue {number}",
        body=body,
        labels=["ready-for-agent", *(labels or [])],
        state=state,
        url=f"https://example.invalid/{number}",
    )


# --------------------------------------------------------------------------- #
# A qualifying issue becomes a candidate                                       #
# --------------------------------------------------------------------------- #


def test_a_qualifying_closed_issue_yields_a_replayable_candidate(
    tmp_path: Path,
) -> None:
    """Base is the commit *before* the fix; the oracle is that fix's tests only."""
    _init_repo(tmp_path)
    base = _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    fix = _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"src.py": "value = 2\n", "tests/test_x.py": "assert True\n"},
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    [candidate] = mined.candidates
    assert candidate.issue == 7
    assert candidate.task_type == "bugfix"
    assert candidate.base_commit == base
    assert candidate.oracle_commit == fix
    assert candidate.task_text == WELL_FORMED_BODY
    assert candidate.oracle_paths == ("tests/test_x.py",)
    assert mined.exclusions == ()


# --------------------------------------------------------------------------- #
# Exclusions, each with the specific rule it failed                            #
# --------------------------------------------------------------------------- #


def test_an_unlabelled_issue_is_excluded_and_never_classified(
    tmp_path: Path,
) -> None:
    """Mining reads labels; an issue without one leaves with its reason named."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    mined = mine_proving_set(
        [_issue(7)], SubprocessGitClient(tmp_path), default_branch="main"
    )
    assert mined.candidates == ()
    assert mined.exclusions == (
        ProvingExclusion(issue=7, reason=ExclusionReason.NO_TASK_TYPE),
    )


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            "## Acceptance criteria\n\n- [ ] It works.\n",
            ExclusionReason.MISSING_WHAT_TO_BUILD,
        ),
        ("## What to build\n\nThe thing.\n", ExclusionReason.MISSING_ACCEPTANCE_CRITERIA),
        ("Just some prose.\n", ExclusionReason.MISSING_BOTH_SECTIONS),
    ],
)
def test_a_malformed_body_is_excluded_by_the_section_it_lacks(
    tmp_path: Path, body: str, reason: ExclusionReason
) -> None:
    """The **Proving set** holds only well-formed work, per the AFK discriminator."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"], body=body)],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    assert mined.exclusions == (ProvingExclusion(issue=7, reason=reason),)


def test_an_issue_no_commit_closes_is_excluded(tmp_path: Path) -> None:
    """Without a closing commit there is no fix to replay and no base to start at."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(tmp_path, "chore: unrelated", {"tests/test_x.py": "assert True\n"})
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    assert mined.exclusions == (
        ProvingExclusion(issue=7, reason=ExclusionReason.NO_CLOSING_COMMIT),
    )


def test_two_closing_commits_are_ambiguous_and_the_reason_names_them(
    tmp_path: Path,
) -> None:
    """Which commit is *the* fix has no answer, so the candidate has no oracle."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    first = _commit(
        tmp_path, "fix(x): part one\n\nCloses #7", {"tests/test_x.py": "assert True\n"}
    )
    second = _commit(
        tmp_path, "fix(x): part two\n\nFixes #7", {"tests/test_y.py": "assert True\n"}
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    [exclusion] = mined.exclusions
    assert exclusion.reason is ExclusionReason.AMBIGUOUS_CLOSING_COMMITS
    assert exclusion.detail is not None
    assert first in exclusion.detail
    assert second in exclusion.detail


def test_a_fix_that_shipped_no_test_change_is_excluded(tmp_path: Path) -> None:
    """An oracle that cannot fail cannot score anything (ADR-0027)."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"src.py": "value = 2\n", "docs/notes.md": "words\n"},
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    assert mined.exclusions == (
        ProvingExclusion(issue=7, reason=ExclusionReason.NO_TEST_CHANGE),
    )


def test_a_root_fixing_commit_has_no_base_to_restore(tmp_path: Path) -> None:
    """The base commit is the fixing commit's parent; a root commit has none."""
    _init_repo(tmp_path)
    root = _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    [exclusion] = mined.exclusions
    assert exclusion.reason is ExclusionReason.NO_PARENT_COMMIT
    assert exclusion.detail == root


def test_a_fix_the_default_branch_cannot_reach_is_not_a_closing_commit(
    tmp_path: Path,
) -> None:
    """What did not ship cannot be the commit that shipped."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-q", "-b", "side"],
        check=True,
        capture_output=True,
        text=True,
    )
    _commit(
        tmp_path,
        "fix(x): never merged\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    assert mined.exclusions == (
        ProvingExclusion(issue=7, reason=ExclusionReason.NO_CLOSING_COMMIT),
    )


def test_an_open_issue_is_ignored_rather_than_excluded(tmp_path: Path) -> None:
    """It was never a candidate, and reporting it would bury the real exclusions."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"], state="OPEN")],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert mined.candidates == ()
    assert mined.exclusions == ()


# --------------------------------------------------------------------------- #
# The mined set                                                                #
# --------------------------------------------------------------------------- #


def test_candidates_are_grouped_by_task_type(tmp_path: Path) -> None:
    """**Calibration** measures per Task type, so the corpus is stratified by it."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path, "fix(a)\n\nCloses #1", {"tests/test_a.py": "assert True\n"}
    )
    _commit(
        tmp_path, "fix(b)\n\nCloses #2", {"tests/test_b.py": "assert True\n"}
    )
    _commit(
        tmp_path, "docs(c)\n\nCloses #3", {"tests/test_c.py": "assert True\n"}
    )
    mined = mine_proving_set(
        [
            _issue(1, labels=["task-type:bugfix"]),
            _issue(2, labels=["task-type:bugfix"]),
            _issue(3, labels=["task-type:docs"]),
        ],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    grouped = mined.by_task_type()
    assert sorted(grouped) == ["bugfix", "docs"]
    assert [c.issue for c in grouped["bugfix"]] == [1, 2]
    assert [c.issue for c in grouped["docs"]] == [3]


def test_a_candidate_pins_itself_by_issue_base_and_oracle(tmp_path: Path) -> None:
    """The artifact's own identity triple, so a later Calibration re-measures it."""
    _init_repo(tmp_path)
    base = _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    fix = _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    [candidate] = mined.candidates
    assert candidate.pin() == ProvingTask(
        issue=7, base_commit=base, oracle_commit=fix
    )


# --------------------------------------------------------------------------- #
# The tracker read                                                             #
# --------------------------------------------------------------------------- #


def test_closed_issues_reads_the_whole_closed_backlog_unfiltered() -> None:
    """Mining must see the unlabelled issues to be able to report them."""
    gh = FakeGitHubClient(
        issues=[
            _issue(1, labels=["task-type:bugfix"]),
            _issue(2, state="OPEN"),
            _issue(3),
        ]
    )
    page = closed_issues(gh)
    assert [issue.number for issue in page.issues] == [1, 3]
    assert gh.issue_list_calls == [("", "closed")]
    assert page.complete is True


def test_closed_issues_passes_an_incomplete_read_through() -> None:
    """A truncated backlog is a corpus with holes, and the caller has to know."""
    gh = FakeGitHubClient(
        issues=[_issue(1, labels=["task-type:bugfix"])], issue_list_complete=False
    )
    assert closed_issues(gh).complete is False


# --------------------------------------------------------------------------- #
# What counts as a test path                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "git-loopy/python/tests/test_gate.py",
        "git-loopy/shell/tests/test-orchestrator.sh",
        "git-loopy/powershell/tests/test-continuation.ps1",
        "pkg/thing_test.go",
        "src/components/Button.test.tsx",
        "src/models/user_spec.rb",
        "spec/api.spec.ts",
        "git-loopy/python/tests/conftest.py",
    ],
)
def test_conventional_test_paths_are_recognised(path: str) -> None:
    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "git-loopy/tui/src/app.rs",
        "docs/tests.md",
        "git-loopy/python/git_loopy/verification.py",
        "contest/entry.py",
        "src/latest.py",
    ],
)
def test_paths_that_only_look_like_tests_are_not(path: str) -> None:
    """The rule is a proxy, so it errs toward excluding rather than inventing."""
    assert is_test_path(path) is False


# --------------------------------------------------------------------------- #
# The classifier pin (ADR-0028: refresh when the pin changes)                  #
# --------------------------------------------------------------------------- #


def test_the_mined_set_records_the_classifier_pair_pinned_at_the_time(
    tmp_path: Path,
) -> None:
    """The taxonomy is only as frozen as the pair that wrote it (ADR-0029)."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    pin = ClassifierPair(model="gpt-5-mini", effort="low")
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
        classifier_pin=pin,
    )
    assert mined.classifier_pin == pin


def test_an_unpinned_mining_pass_records_no_classifier_pair(tmp_path: Path) -> None:
    """No pin is ``None``, never a plausible-looking default nobody chose."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    mined = mine_proving_set(
        [], SubprocessGitClient(tmp_path), default_branch="main"
    )
    assert mined.classifier_pin is None


# --------------------------------------------------------------------------- #
# Labels are read, never inferred                                              #
# --------------------------------------------------------------------------- #


def test_the_label_decides_the_task_type_even_when_the_prose_disagrees(
    tmp_path: Path,
) -> None:
    """A body that reads like docs work labelled ``bugfix`` is mined as bugfix.

    The structural guard below says mining *cannot* classify; this says it does
    not do so by any other route either — nothing re-reads the prose looking for
    a better answer than the label an operator (or #378's writeback) already gave.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {"tests/test_x.py": "assert True\n"},
    )
    body = (
        "## What to build\n\nRewrite the README and the docs index; this is "
        "documentation work, pure prose, no code.\n\n"
        "## Acceptance criteria\n\n- [ ] The docs read well.\n"
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"], body=body)],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    [candidate] = mined.candidates
    assert candidate.task_type == "bugfix"


def test_mining_cannot_reach_anything_that_classifies_or_spends() -> None:
    """A label an operator applied and one #378 wrote back are the same label.

    Mining is told to treat them identically, and the only way to *guarantee*
    that is to give it no way to tell them apart: it may read the label
    (:func:`~git_loopy.task_type_classifier.labelled_task_type`) and may not
    reach the classifier itself, the proposer seam, or any client that spends an
    **AI Credit**. Asserted structurally rather than promised, because the
    difference is invisible in an output that would look correct either way.
    """
    import ast

    from git_loopy import proving_set as proving_set_module

    tree = ast.parse(
        Path(proving_set_module.__file__).read_text(encoding="utf-8")
    )
    #: Every identifier the module actually *uses* — imported name, bare
    #: reference or attribute access — so ``task_type_classifier.classify_task_type``
    #: is caught as surely as a direct import of it. Docstrings are strings and
    #: never appear here, which is why the guard can be this blunt.
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            used.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    forbidden = {
        "classify_task_type",
        "classifier_prompt",
        "parse_task_type_proposal",
        "TaskTypeProposer",
        "resolve_classifier_pair",
        "CopilotClient",
        "copilot",
    }
    assert not (used & forbidden), sorted(used & forbidden)


# --------------------------------------------------------------------------- #
# The oracle is the fix's tests, and never the fix                             #
# --------------------------------------------------------------------------- #


def test_the_oracle_carries_no_part_of_the_fix(tmp_path: Path) -> None:
    """Passing must mean solving the problem, not matching a diff (ADR-0027)."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src/thing.py": "value = 1\n"})
    _commit(
        tmp_path,
        "fix(x): the fix\n\nCloses #7",
        {
            "src/thing.py": "value = 2\n",
            "src/helper.py": "def help_():\n    return 2\n",
            "docs/thing.md": "words\n",
            "tests/test_thing.py": "assert True\n",
            "tests/helpers/test_helper.py": "assert True\n",
        },
    )
    mined = mine_proving_set(
        [_issue(7, labels=["task-type:bugfix"])],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    [candidate] = mined.candidates
    assert sorted(candidate.oracle_paths) == [
        "tests/helpers/test_helper.py",
        "tests/test_thing.py",
    ]


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #


def test_a_mixed_backlog_reports_every_exclusion_with_its_own_reason(
    tmp_path: Path,
) -> None:
    """Four issues fail four different rules, and the report says which each was."""
    _init_repo(tmp_path)
    _commit(tmp_path, "groundwork", {"src.py": "value = 1\n"})
    _commit(
        tmp_path, "fix(a)\n\nCloses #1", {"tests/test_a.py": "assert True\n"}
    )
    _commit(tmp_path, "fix(b)\n\nCloses #2", {"src.py": "value = 2\n"})
    mined = mine_proving_set(
        [
            _issue(1, labels=["task-type:bugfix"]),
            _issue(2, labels=["task-type:bugfix"]),
            _issue(3, labels=["task-type:docs"]),
            _issue(4, labels=["task-type:docs"], body="nothing structured\n"),
        ],
        SubprocessGitClient(tmp_path),
        default_branch="main",
    )
    assert [c.issue for c in mined.candidates] == [1]
    assert [(e.issue, e.reason) for e in mined.exclusions] == [
        (2, ExclusionReason.NO_TEST_CHANGE),
        (3, ExclusionReason.NO_CLOSING_COMMIT),
        (4, ExclusionReason.MISSING_BOTH_SECTIONS),
    ]


def test_the_body_reasons_are_the_pool_exclusion_vocabulary() -> None:
    """One spelling of "well-formed work", shared with Pool collection (#303)."""
    assert {
        reason.value
        for reason in (
            ExclusionReason.MISSING_WHAT_TO_BUILD,
            ExclusionReason.MISSING_ACCEPTANCE_CRITERIA,
            ExclusionReason.MISSING_BOTH_SECTIONS,
        )
    } == set(EXCLUSION_REASONS)
