"""Tests for the reusable test fakes in ``tests/fakes.py`` (issue #46).

The loop and source suites lean on these fakes to substitute a whole seam with
one object; these tests pin the fake's *own* contract so a drifting fake cannot
quietly invalidate the suites that build on it — chiefly the
**checkpoint-exclusion** invariant that keeps the Strike rule honest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_afk.git import Commit, GitClient, GitError
from tests.fakes import FakeGitClient


def test_fake_git_client_satisfies_gitclient_protocol(tmp_path: Path) -> None:
    """The fake satisfies the ``@runtime_checkable`` ``GitClient`` structurally."""
    assert isinstance(FakeGitClient(tmp_path), GitClient)
    assert not isinstance(object(), GitClient)


def test_head_and_recent_track_the_linear_log(tmp_path: Path) -> None:
    seed = Commit(sha="s0", subject="root", body="", date="2026-01-01")
    git = FakeGitClient(tmp_path, commits=[seed])
    assert git.head_sha() == "s0"
    git.simulate_agent_commit(subject="first", sha="s1")
    b = git.simulate_agent_commit(subject="second", sha="s2")
    assert git.head_sha() == b == "s2"
    # recent_commits is newest-first and bounded by n.
    assert [c.sha for c in git.recent_commits(2)] == ["s2", "s1"]
    assert [c.sha for c in git.recent_commits(10)] == ["s2", "s1", "s0"]
    assert git.recent_commits(0) == []
    assert git.recent_commits(-1) == []


def test_commits_between_is_positional_and_excludes_checkpoint(
    tmp_path: Path,
) -> None:
    """The load-bearing invariant: a Checkpoint committed after ``head`` is read
    is positionally after ``head`` in the log, so it is excluded from the range."""
    git = FakeGitClient(
        tmp_path, commits=[Commit(sha="base", subject="b", body="")]
    )
    pre = git.head_sha()
    git.simulate_agent_commit(subject="feat", body="Closes #42", sha="agent")
    head = git.head_sha()
    # Runner Checkpoint lands AFTER head is captured.
    checkpoint = git.commit("chore(ralph): checkpoint")
    between = git.commits_between(pre, head)
    assert [c.sha for c in between] == ["agent"]
    assert checkpoint not in [c.sha for c in between]
    assert git.range_count(pre, head) == 1
    # Same pre/head → empty (no self-range).
    assert git.commits_between(head, head) == []
    assert git.range_count(head, head) == 0


def test_commit_appends_records_and_returns_new_head(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    before = git.head_sha()
    sha = git.commit("subject line\n\nbody text")
    assert sha == git.head_sha()
    assert sha != before
    assert git.commit_messages == ["subject line\n\nbody text"]
    recorded = git.recent_commits(1)[0]
    assert recorded.subject == "subject line"
    assert recorded.body == "body text"


def test_add_all_and_push_are_recorded_spies(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    git.add_all()
    git.add_all()
    git.push()
    assert git.add_all_calls == 2
    assert git.push_calls == 1


def test_injected_commit_error_is_raised_after_recording(tmp_path: Path) -> None:
    boom = GitError(["git", "commit"], 1, "nothing to commit")
    git = FakeGitClient(tmp_path, commit_error=boom)
    with pytest.raises(GitError):
        git.commit("checkpoint")
    # The message is still recorded: the loop treats a Checkpoint failure as
    # non-fatal, so the spy must witness the attempt.
    assert git.commit_messages == ["checkpoint"]


def test_injected_push_error_is_raised_after_recording(tmp_path: Path) -> None:
    boom = GitError(["git", "push"], 1, "no upstream")
    git = FakeGitClient(tmp_path, push_error=boom)
    with pytest.raises(GitError):
        git.push()
    assert git.push_calls == 1


def test_dirty_and_untracked_are_test_controlled_and_persist(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path, dirty=True, untracked=True)
    assert git.is_dirty() is True
    assert git.has_untracked() is True
    # commit does NOT clear them (a real agent re-dirties each iteration), so a
    # multi-iteration test that leaves dirty=True Checkpoints every iteration.
    git.add_all()
    git.commit("checkpoint")
    assert git.is_dirty() is True
    assert git.has_untracked() is True


def test_branch_switch_is_recorded(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    assert git.current_branch() == "main"
    git.switch("feature/x")
    assert git.current_branch() == "feature/x"
    assert git.switch_calls == ["feature/x"]


def test_commits_between_unknown_sha_raises(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    with pytest.raises(GitError):
        git.commits_between("deadbeef", git.head_sha())
