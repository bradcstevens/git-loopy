"""Tests for dead-Run workspace and branch reclamation (#455)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from git_loopy import sweepcmd
from git_loopy.gh import Issue, Repo
from git_loopy.git import SubprocessGitClient, integration_branch_name, lane_branch_name
from git_loopy.run_control import RunControlArtifact, advisory_locking_available
from git_loopy.sweep import SweepReport, sweep
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
    """A dry run plans a dead Lane's reclaim and touches neither worktree.

    The two worktrees sit side by side in the legacy directory, which is the
    whole point: only the one on a **Reserved branch namespace** branch is
    git-loopy's, and location says nothing about either.
    """
    git = FakeGitClient(tmp_path, branch="main")
    legacy_root = tmp_path.parent / f"{tmp_path.name}.worktrees"
    lane_path = legacy_root / "RUNDEAD" / "issue-7"
    lane_branch = lane_branch_name("RUNDEAD", 7)
    lane = git.add_worktree(lane_path, branch=lane_branch, base="main")
    lane.dirty = True
    operators_path = legacy_root / "operator-spike"
    operators = git.add_worktree(operators_path, branch="operator/spike", base="main")
    operators.dirty = True
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
    assert git.worktree_removes == []
    assert lane.commit_messages == []
    assert operators.commit_messages == []
    assert operators_path not in report.worktrees
    assert "operator/spike" not in report.branches


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


def test_sweep_never_collects_the_branch_it_just_salvaged_work_onto(
    tmp_path: Path,
) -> None:
    """**Salvage** destroys nothing, so the same sweep must not delete its commit.

    Collecting a Lane branch by issue-closed is premised on the branch holding
    nothing that is not already resolved — a premise a salvage commit made
    moments earlier contradicts, because that commit is by definition the only
    copy of work nobody has seen. Collection therefore cannot judge the branch
    on state this sweep itself created.
    """
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNDEAD" / "issue-7"
    lane_branch = lane_branch_name("RUNDEAD", 7)
    lane = git.add_worktree(lane_path, branch=lane_branch, base="main")
    lane.dirty = True
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert lane.commit_messages == [checkpoint_message(7)]
    assert report.worktrees == (lane_path,)
    assert report.branches == ()
    assert git.branch_deletes == []
    assert lane_branch in git.list_branches()


def test_sweep_dry_run_plans_the_outcome_the_real_sweep_produces(tmp_path: Path) -> None:
    """A plan that over-reports a deletion is worse than no plan at all.

    Salvage is the one step that changes the answer to a later question: a dirty
    workspace's branch is merged until the Checkpoint lands on it. A dry run
    that skips salvage and then reads merged-ness would promise to delete a
    branch the real sweep keeps.
    """
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNDEAD" / "issue-7"
    lane = git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 7), base="main")
    lane.dirty = True
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED")],
    )

    planned = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=True,
    )

    assert lane.commit_messages == []
    assert git.worktree_removes == []
    assert planned.worktrees == (lane_path,)
    assert planned.branches == ()


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


def _real_repo(tmp_path: Path) -> SubprocessGitClient:
    """A one-commit clone on ``main``, for the claims only real git can settle."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for key, value in (("user.email", "test@example.com"), ("user.name", "Test User")):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return SubprocessGitClient(repo)


def test_sweep_removes_an_empty_legacy_directory_after_reclaiming_its_lane(
    tmp_path: Path,
) -> None:
    """Legacy parents go only when their last namespace-owned workspace is gone."""
    git = _real_repo(tmp_path)
    repo = git.root
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


def test_sweep_reclaims_a_workspace_whose_directory_is_already_gone(
    tmp_path: Path,
) -> None:
    """A registration outliving its directory is residue, not a live workspace.

    The hard kill this whole actor exists for can also take the directory and
    leave git's bookkeeping — and a stale registration is worse than an ordinary
    leftover, because nothing can be opened in it. Treating that as a failure
    withholds its branch from collection forever, so the sweep would accumulate
    exactly the residue it was written to remove. There is nothing to salvage:
    a directory that is gone holds no work.
    """
    git = _real_repo(tmp_path)
    lane_path = git.common_git_dir() / "git-loopy" / "RUNGONE" / "issue-9"
    branch = lane_branch_name("RUNGONE", 9)
    git.add_worktree(lane_path, branch=branch, base="main")
    shutil.rmtree(lane_path)
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9)],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=git.root / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.worktrees == (lane_path,)
    assert report.branches == (branch,)
    assert [worktree.path for worktree in git.list_worktrees()] == [git.root]
    assert branch not in git.list_branches()


def test_a_salvaged_branch_survives_every_later_sweep(tmp_path: Path) -> None:
    """A **Checkpoint** tip is work nobody has seen, and a closed issue cannot see it.

    Collecting by issue-closed is premised on the branch holding work that was
    superseded — committed once, then landed by some other route. A Checkpoint
    is the opposite: work its author never chose to commit, rescued by a
    reclaiming actor. The issue closing says nothing about it, so it survives.

    This is what makes **Salvage** worth performing at all. Without it the
    first sweep commits the rescue and the *second* deletes it, and the same
    hole swallows the salvage ADR-0050's exit paths (#452) leave behind — a
    Checkpoint written by a Run that this sweep never saw.
    """
    git = _real_repo(tmp_path)
    lane_path = git.common_git_dir() / "git-loopy" / "RUNGONE" / "issue-9"
    branch = lane_branch_name("RUNGONE", 9)
    git.add_worktree(lane_path, branch=branch, base="main")
    (lane_path / "precious.txt").write_text("work nobody has seen\n")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    def run_sweep() -> SweepReport:
        return sweep(
            git=git,
            github=github,
            control_dir=git.root / ".git-loopy" / "logs",
            base_branch="main",
            dry_run=False,
        )

    first = run_sweep()
    second = run_sweep()

    assert first.worktrees == (lane_path,)
    assert branch in git.list_branches()
    assert "precious.txt" in git.changed_paths(git.commits_between("main", branch)[0].sha)
    assert not second.reclaimed_anything


def test_sweep_never_touches_a_run_whose_control_artifact_is_in_another_worktree(
    tmp_path: Path,
) -> None:
    """Liveness spans the clone, because the branch namespace does.

    Control artifacts are per-*worktree* but reserved branches are per-*clone*,
    so a sweep run from one worktree enumerates workspaces belonging to a Run
    that published its lock in another. Reading only the sweeping worktree's own
    directory declares that Run dead and force-removes a live workspace.
    """
    git = _real_repo(tmp_path)
    elsewhere = git.root.parent / "linked"
    git.add_worktree(elsewhere, branch="operator-checkout", base="main")
    lane_path = git.common_git_dir() / "git-loopy" / "RUNLIVE" / "issue-9"
    git.add_worktree(lane_path, branch=lane_branch_name("RUNLIVE", 9), base="main")
    held = RunControlArtifact.acquire(
        elsewhere / ".git-loopy" / "logs" / "20260823-RUNLIVE.jsonl"
    )
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    try:
        report = sweep(
            git=git,
            github=github,
            control_dir=git.root / ".git-loopy" / "logs",
            base_branch="main",
            dry_run=False,
        )
    finally:
        held.close()

    assert not report.reclaimed_anything
    assert lane_path.is_dir()


def test_sweep_never_commits_to_the_worktree_it_is_running_in(tmp_path: Path) -> None:
    """The clone you are standing in is never residue, whatever it is checked out on.

    An operator who checks a dead Run's Lane branch out to look at it puts their
    own working tree on a reserved branch. Reclamation would then stage and
    commit *their* uncommitted work before git refused to remove the main
    worktree — and the refusal is swallowed, so nothing would say so.

    The client is deliberately built on an *aliased* path, because git reports
    its own resolved one — exactly what a macOS ``/tmp`` (a link to
    ``/private/tmp``) hands you. Identity here is a question about the
    directory, and comparing the two spellings answers it wrongly.
    """
    git = _real_repo(tmp_path)
    branch = lane_branch_name("RUNDEAD", 9)
    subprocess.run(["git", "switch", "-c", branch], cwd=git.root, check=True, capture_output=True)
    (git.root / "operator-wip.txt").write_text("uncommitted and unasked for\n")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    alias = tmp_path / "alias"
    alias.symlink_to(git.root)

    report = sweep(
        git=SubprocessGitClient(alias),
        github=github,
        control_dir=git.root / ".git-loopy" / "logs",
        base_branch=branch,
        dry_run=False,
    )

    assert report.worktrees == ()
    assert git.is_dirty() or git.has_untracked()
    assert not is_checkpoint_message(git.branch_tip(branch).message)


def test_sweep_never_opens_a_workspace_registered_through_a_symlink(
    tmp_path: Path,
) -> None:
    """A registration is a claim about a path, and a symlink makes it a lie.

    Salvage opens the registered path and commits what it finds there. If that
    path is a link into an unrelated checkout, the Checkpoint lands in someone
    else's repository — so an unverifiable registration is left entirely alone.
    """
    git = _real_repo(tmp_path)
    lane_path = git.common_git_dir() / "git-loopy" / "RUNGONE" / "issue-9"
    branch = lane_branch_name("RUNGONE", 9)
    git.add_worktree(lane_path, branch=branch, base="main")
    victim = _real_repo(tmp_path / "victim").root
    (victim / "theirs.txt").write_text("not ours\n")
    shutil.rmtree(lane_path)
    lane_path.symlink_to(victim)
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=git.root / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.worktrees == ()
    assert report.branches == ()
    assert (victim / "theirs.txt").read_text() == "not ours\n"
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=victim, capture_output=True, text=True
    ).stdout.strip() == "?? theirs.txt"


def test_sweep_dry_run_does_not_report_a_symlinked_registered_workspace(
    tmp_path: Path,
) -> None:
    """A plan preserves the workspace and branch an actual sweep must preserve.

    Git's registration can point at a symlink, but opening or removing that path
    is not safe. A dry run must not report a deletion that execution refuses.
    """
    git = _real_repo(tmp_path)
    lane_path = git.common_git_dir() / "git-loopy" / "RUNGONE" / "issue-9"
    branch = lane_branch_name("RUNGONE", 9)
    git.add_worktree(lane_path, branch=branch, base="main")
    victim = _real_repo(tmp_path / "victim").root
    shutil.rmtree(lane_path)
    lane_path.symlink_to(victim)
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=git.root / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=True,
    )

    assert not report.reclaimed_anything
    assert branch in git.list_branches()
    assert victim.is_dir()


def test_sweep_does_not_salvage_a_dead_integration_stage(tmp_path: Path) -> None:
    """A stage holds a half-finished merge, and its branch is always collected.

    Salvaging one would commit conflict markers to a branch this same sweep
    then deletes by definition — work that is not work, rescued onto a carrier
    that cannot keep it.
    """
    git = _real_repo(tmp_path)
    stage_path = git.common_git_dir() / "git-loopy" / "RUNGONE" / "integrate" / "issue-9"
    branch = integration_branch_name("RUNGONE", 9)
    git.add_worktree(stage_path, branch=branch, base="main")
    (stage_path / "half-merged.txt").write_text("<<<<<<< HEAD\n")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"), issues=[_issue(9)]
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=git.root / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.worktrees == (stage_path,)
    assert report.branches == (branch,)


def test_sweep_treats_an_unreadable_control_artifact_as_unknown(
    tmp_path: Path,
) -> None:
    """A sweep is best-effort cleanup and must never take a Run down with it.

    Liveness reads the filesystem, so it can fail for reasons that say nothing
    about the Run — a permission, a directory where a file should be. Every one
    of them is *unknown*, which preserves; none of them is an exception the
    start-of-Run hook has to survive.
    """
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNDEAD" / "issue-9"
    git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 9), base="main")
    control_dir = tmp_path / ".git-loopy" / "logs"
    control_dir.mkdir(parents=True)
    (control_dir / "20260823-RUNDEAD.control").mkdir()
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=control_dir,
        base_branch="main",
        dry_run=False,
    )

    assert not report.reclaimed_anything


def test_sweep_treats_a_missing_artifact_as_unknown_without_advisory_locks(
    tmp_path: Path,
) -> None:
    """A platform without advisory locks cannot prove an unrecorded Run dead.

    ``is_run_alive`` reports ``None`` before it tries to open an artifact on
    those platforms. The liveness composition must ask it even when no prior
    Run left an artifact behind; otherwise the empty match set accidentally
    becomes ``False`` and turns unknown into a destructive answer.
    """
    git = FakeGitClient(tmp_path, branch="main")
    lane_path = tmp_path / ".git" / "git-loopy" / "RUNUNKNOWN" / "issue-9"
    git.add_worktree(lane_path, branch=lane_branch_name("RUNUNKNOWN", 9), base="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(9, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
        liveness=lambda _path: None,
    )

    assert not report.reclaimed_anything
    assert git.worktree_removes == []
    assert git.branch_deletes == []


def test_sweep_never_follows_or_removes_a_symlink(tmp_path: Path) -> None:
    """A link must not walk the reaper out of the subtree it was aimed at.

    The directory pass is the one part of a sweep that reasons about the disk
    rather than about a branch, so a symlink in a workspace root is the one way
    it could reach a directory that is nobody's residue.
    """
    git = FakeGitClient(tmp_path, branch="main")
    root = tmp_path / ".git" / "git-loopy"
    root.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "empty").mkdir(parents=True)
    (root / "link").symlink_to(elsewhere)
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"), issues=[]
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.directories == ()
    assert (elsewhere / "empty").is_dir()
    assert (root / "link").is_symlink()


def test_sweep_dry_run_plans_every_directory_the_real_sweep_removes(
    tmp_path: Path,
) -> None:
    """The plan must account for the directories that reclaiming itself empties.

    Most of a workspace root's residue is parents that are only empty *once*
    their worktree is gone, so a plan that reads the disk as it stands names
    almost none of what the sweep is about to do. Removals are modelled instead
    of performed, which is what lets the same pass answer both questions.
    """
    git = _real_repo(tmp_path)
    lane_path = git.common_git_dir() / "git-loopy" / "RUNDEAD" / "issue-7"
    git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 7), base="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7, state="CLOSED")],
    )
    kwargs = dict(
        git=git,
        github=github,
        control_dir=git.root / ".git-loopy" / "logs",
        base_branch="main",
    )

    planned = sweep(**kwargs, dry_run=True)
    performed = sweep(**kwargs, dry_run=False)

    assert planned == performed
    assert lane_path.parent.parent in performed.directories
    assert not (git.common_git_dir() / "git-loopy").exists()


def test_sweep_collects_against_the_branch_the_run_publishes_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merged-ness is judged against the checked-out base, not the repo default.

    A **Lane** branch is cut from the branch the Run is on (ADR-0008's natural
    base) and Integration publishes back to it, so a Run working a release line
    resolves every branch against that line. Reading the repository's default
    branch instead asks git about a ref the Lane was never merged into.
    """
    git = FakeGitClient(tmp_path, branch="release/2.x")
    lane_branch = lane_branch_name("RUNDEAD", 7)
    git.add_worktree(
        tmp_path / ".git" / "git-loopy" / "RUNDEAD" / "issue-7",
        branch=lane_branch,
        base="release/2.x",
    )
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(7)],
    )
    monkeypatch.setattr(sweepcmd, "SubprocessGitClient", lambda _root: git)
    monkeypatch.setattr(sweepcmd, "SubprocessGitHubClient", lambda: github)
    output: list[str] = []

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=False, output=output.append) == 0

    assert git.branch_deletes == [lane_branch]
    assert github.issue_view_calls == []


def test_sweep_reaps_workspace_skeletons_no_worktree_owns(tmp_path: Path) -> None:
    """Empty directories outlive the worktrees that made them, so a sweep reaps them.

    ``git worktree remove`` takes the ``issue-<N>`` leaf and leaves the run and
    ``integrate/`` parents standing, so every Run donates permanent empty
    directories that no reclamation keyed to a *live* worktree can ever see —
    a census found nine run skeletons and not one file. Emptiness is the whole
    rule: a directory that still holds anything is refused, which is why an
    operator's own worktree sharing the legacy directory survives even though
    the classification above it never looked at it.
    """
    git = FakeGitClient(tmp_path, branch="main")
    legacy_root = tmp_path.parent / f"{tmp_path.name}.worktrees"
    skeletons = [legacy_root / run / "integrate" for run in ("RUN1", "RUN2")]
    for skeleton in skeletons:
        skeleton.mkdir(parents=True)
    internal_skeleton = tmp_path / ".git" / "git-loopy" / "RUN3" / "integrate"
    internal_skeleton.mkdir(parents=True)
    operators_own = legacy_root / "git-loopy-438"
    operators_own.mkdir(parents=True)
    (operators_own / "in-flight.txt").write_text("mine\n", encoding="utf-8")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"), issues=[]
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert [skeleton.exists() for skeleton in skeletons] == [False, False]
    assert not (legacy_root / "RUN1").exists()
    assert not (tmp_path / ".git" / "git-loopy").exists()
    assert (operators_own / "in-flight.txt").read_text(encoding="utf-8") == "mine\n"
    assert legacy_root.exists()
    assert report.reclaimed_anything is True
    assert set(report.directories) == {
        legacy_root / "RUN1",
        skeletons[0],
        legacy_root / "RUN2",
        skeletons[1],
        tmp_path / ".git" / "git-loopy",
        tmp_path / ".git" / "git-loopy" / "RUN3",
        internal_skeleton,
    }


def test_sweep_collects_an_unmerged_lane_branch_only_once_its_issue_closes(
    tmp_path: Path,
) -> None:
    """Resolution, not merged-ness, is what makes a Lane branch collectable.

    Merged-ness alone was measured and collects less than half the residue while
    keeping branches that are provably worthless: every unmerged Lane branch in
    the census belonged to a **closed** issue, which merged-ness structurally
    cannot see. An unmerged branch for an issue still open is the one case where
    the branch may still be the only copy of the work, so it stays.
    """
    git = FakeGitClient(tmp_path, branch="main")
    for number in (7, 8):
        path = tmp_path / ".git" / "git-loopy" / "RUNDEAD" / f"issue-{number}"
        lane = git.add_worktree(path, branch=lane_branch_name("RUNDEAD", number), base="main")
        lane.commit(f"unmerged work for #{number}")
        git.remove_worktree(path)
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

    assert report.branches == (lane_branch_name("RUNDEAD", 7),)
    assert git.branch_deletes == [lane_branch_name("RUNDEAD", 7)]
    assert lane_branch_name("RUNDEAD", 8) in git.list_branches()


def test_sweep_leaves_an_operators_own_dirty_worktree_entirely_alone(
    tmp_path: Path,
) -> None:
    """A live sweep must be as blind to a foreign worktree as a dry run is.

    An operator's ``git worktree add ../git-loopy-<N>`` lands in the very
    directory git-loopy's own worktrees once used, and a census caught one being
    committed to by a live session mid-sweep. Ownership therefore comes from the
    **Reserved branch namespace** alone: an unreserved branch is never salvaged,
    never removed, and never reported.
    """
    git = FakeGitClient(tmp_path, branch="main")
    legacy_root = tmp_path.parent / f"{tmp_path.name}.worktrees"
    operators_path = legacy_root / "git-loopy-438"
    operators = git.add_worktree(operators_path, branch="git-loopy-438", base="main")
    operators.dirty = True
    operators_path.mkdir(parents=True)
    (operators_path / "in-flight.txt").write_text("mine\n", encoding="utf-8")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(438, state="CLOSED")],
    )

    report = sweep(
        git=git,
        github=github,
        control_dir=tmp_path / ".git-loopy" / "logs",
        base_branch="main",
        dry_run=False,
    )

    assert report.reclaimed_anything is False
    assert git.worktree_removes == []
    assert git.branch_deletes == []
    assert operators.commit_messages == []
    assert (operators_path / "in-flight.txt").read_text(encoding="utf-8") == "mine\n"


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


def test_sweep_dry_run_names_the_directories_it_leaves_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run must name every removal, and perform none of them.

    A directory is the one kind of residue an operator cannot preview any other
    way — a branch survives in ``git branch`` and a worktree in ``git worktree
    list``, but an empty skeleton is only ever visible to the sweep's own
    classification, so withholding it from the plan hides the bulk of what a
    first sweep does.
    """
    git = FakeGitClient(tmp_path, branch="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"), issues=[]
    )
    monkeypatch.setattr(sweepcmd, "SubprocessGitClient", lambda _root: git)
    monkeypatch.setattr(sweepcmd, "SubprocessGitHubClient", lambda: github)
    skeleton = tmp_path / ".git" / "git-loopy" / "RUN1" / "integrate"
    skeleton.mkdir(parents=True)
    output: list[str] = []

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=True, output=output.append) == 0

    assert output == [
        f"Would reclaim directory: {skeleton}",
        f"Would reclaim directory: {skeleton.parent}",
        f"Would reclaim directory: {skeleton.parent.parent}",
    ]
    assert skeleton.exists()
