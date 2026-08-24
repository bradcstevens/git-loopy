"""Tests for dead-Run workspace and branch reclamation (#455)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_loopy import sweepcmd
from git_loopy.gh import Issue, Repo
from git_loopy.git import SubprocessGitClient, integration_branch_name, lane_branch_name
from git_loopy.run_control import RunControlArtifact, advisory_locking_available
from git_loopy.sweep import sweep
from git_loopy.wrapper import checkpoint_message, is_checkpoint_message
from tests.fakes import FakeGitClient, FakeGitHubClient


def _issue(number: int, *, state: str = "OPEN") -> Issue:
    return Issue(
        number=number,
        title=f"Issue {number}",
        body="",
        labels=[],
        state=state,
        url=f"https://example.test/issues/{number}",
        created_at="2026-08-23T00:00:00Z",
    )


def test_dry_run_reports_only_dead_namespace_owned_residue(tmp_path: Path) -> None:
    """Dry run plans a dead Lane reclaim without touching either worktree."""
    git = FakeGitClient(tmp_path, branch="main")
    legacy_root = tmp_path.parent / f"{tmp_path.name}.worktrees"
    lane_path = legacy_root / "RUNDEAD" / "issue-7"
    lane = git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 7), base="main")
    lane.dirty = True
    operators_path = legacy_root / "operator-spike"
    git.add_worktree(operators_path, branch="operator/spike", base="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=True,
    )

    assert report.worktrees == (lane_path,)
    assert report.branches == (lane_branch_name("RUNDEAD", 7),)
    assert git.worktree_removes == []
    assert lane.commit_messages == []
    assert operators_path not in report.worktrees


def test_sweep_salvages_dead_dirty_lane_before_reclaiming_it(tmp_path: Path) -> None:
    """A dead dirty Lane becomes a Checkpoint before its workspace is removed."""
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNDEAD" / "issue-7"
    lane = git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 7), base="main")
    lane.dirty = True
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7)],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.worktrees == (lane_path,)
    assert report.branches == ()
    assert lane.commit_messages == [checkpoint_message(7)]
    assert git.worktree_removes == [lane_path]
    assert git.list_branches() == [lane_branch_name("RUNDEAD", 7), "main"]


def test_sweep_collects_closed_lane_branch_and_every_stage_branch(tmp_path: Path) -> None:
    """Resolution collects a Lane branch; an Integration stage is always ephemeral."""
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNLANE" / "issue-7"
    lane_branch = lane_branch_name("RUNLANE", 7)
    git.add_worktree(lane_path, branch=lane_branch, base="main")
    git.remove_worktree(lane_path)
    stage_path = tmp_path / ".git" / "git-loopy" / "RUNSTAGE" / "integrate" / "issue-8"
    stage_branch = integration_branch_name("RUNSTAGE", 8)
    git.add_worktree(stage_path, branch=stage_branch, base="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED"), _issue(8)],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.worktrees == (stage_path,)
    assert report.branches == (lane_branch, stage_branch)
    assert git.branch_deletes == [lane_branch, stage_branch]


@pytest.mark.skipif(
    not advisory_locking_available(), reason="this platform has no flock advisory locks"
)
def test_sweep_never_touches_a_workspace_for_a_lock_held_run(tmp_path: Path) -> None:
    """The control artifact lock keeps a live Run's Lane out of the sweep."""
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNLIVE" / "issue-7"
    git.add_worktree(lane_path, branch=lane_branch_name("RUNLIVE", 7), base="main")
    control = RunControlArtifact.acquire(
        tmp_path / ".git-loopy" / "logs" / "live-RUNLIVE.jsonl"
    )
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED")],
    )
    try:
        report = sweep(
            git=git,
            github=github,
            control_dir=tmp_path / ".git-loopy" / "logs",
            base_branch="main",
            dry_run=False,
        )
    finally:
        control.close()

    assert report.reclaimed_anything is False
    assert git.worktree_removes == []
    assert git.branch_deletes == []


def test_sweep_removes_an_empty_legacy_directory_after_reclaiming_its_lane(
    tmp_path: Path,
) -> None:
    """Legacy parents go only when their last namespace-owned workspace is gone."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("user.email", "test@example.com"), ("user.name", "Test User")):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    git = SubprocessGitClient(repo)
    legacy_root = tmp_path / "repo.worktrees"
    lane_path = legacy_root / "RUNDEAD" / "issue-7"
    branch = lane_branch_name("RUNDEAD", 7)
    lane = git.add_worktree(lane_path, branch=branch, base="main")
    (lane.root / "unfinished.txt").write_text("keep this\n", encoding="utf-8")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7)],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=repo / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.worktrees == (lane_path,)
    assert not legacy_root.exists()
    assert any(is_checkpoint_message(commit.message) for commit in git.commits_reachable(branch))


def test_sweep_command_is_silent_when_clean_and_reports_dry_run_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator command is quiet when clean and names each planned deletion."""
    git = FakeGitClient(tmp_path, branch="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED")],
    )
    monkeypatch.setattr(sweepcmd, "SubprocessGitClient", lambda _root: git)
    monkeypatch.setattr(sweepcmd, "SubprocessGitHubClient", lambda: github)
    output: list[str] = []

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=True, output=output.append) == 0
    assert output == []

    lane_path = tmp_path / ".git" / "git-loopy" / "RUNDEAD" / "issue-7"
    lane_branch = lane_branch_name("RUNDEAD", 7)
    git.add_worktree(lane_path, branch=lane_branch, base="main")

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=True, output=output.append) == 0
    assert output == [
        f"Would reclaim worktree: {lane_path}",
        f"Would collect branch: {lane_branch}",
    ]
