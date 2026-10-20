"""A Lane's Checkpoint is recoverable without publishing its unfinished work."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from git_loopy import git as git_module
from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy.execution_host import ContributionRequest, ContributionSuccess, IsolationGrade, Placement
from git_loopy.session_outcome import SessionOutcome, SessionOutcomeRecord, SessionTermination
from git_loopy.wrapper import checkpoint_message
from tests import test_loop_parallel as lp
from tests.fakes import FakeGateRunner
from tests.test_git import _init_repo
from tests.test_loop_parallel import (  # noqa: F401 - autouse Run fixtures
    _declare_two_local_lane_slots,
    _stub_run_skill_catalog,
)


def _wire(tmp_path, monkeypatch, client_cls=lp._NoProgressFakeClient):
    gh = lp._wire_rolling_run(
        tmp_path, monkeypatch, [lp._make_issue(42, labels=lp._PARALLEL_SAFE)],
        client_cls=client_cls,
    )
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".git-loopy/\n")
    git = git_module.SubprocessGitClient(tmp_path)
    git.add_all()
    base = git.commit("initial")
    origin = tmp_path.parent / f"{tmp_path.name}.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", str(origin)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "push", "-q", "-u", "origin", "main"],
        check=True,
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
    return git, gh, base


@pytest.mark.parametrize("agent_commits,dirty", [(False, True), (True, True), (True, False)])
@pytest.mark.parametrize("push_fails", [False, True])
def test_lane_publishes_only_agent_commits(tmp_path, monkeypatch, agent_commits, dirty, push_fails):
    agent_sha = None

    class Session(lp._NoProgressFakeSession):
        async def send_and_wait(self, prompt, *, timeout=60.0, **extra):
            nonlocal agent_sha
            assert self._working_directory is not None
            path = Path(self._working_directory)
            if agent_commits:
                (path / "finished.txt").write_text("finished\n")
                lane = git_module.SubprocessGitClient(path)
                lane.add_all()
                agent_sha = lane.commit("Implement contribution\n\nCloses #42")
            if dirty:
                (path / "unfinished.txt").write_text("unfinished\n")
            raise TimeoutError("session timed out")

    class Client(lp._NoProgressFakeClient):
        _session_cls = Session

    git, gh, base = _wire(tmp_path, monkeypatch, Client)
    if push_fails:
        original_push = git.push_ref

        def reject_checkpoint(remote, ref, sha, expected, **kwargs):
            if ref.startswith("refs/heads/git-loopy-checkpoints/"):
                raise git_module.GitError(["push"], 1, "fixture push rejected")
            return original_push(remote, ref, sha, expected, **kwargs)

        monkeypatch.setattr(git, "push_ref", reject_checkpoint)

    assert asyncio.run(loop_module.run(lp._pinned_config(42, max_iterations=1))) == 0

    assert (git.head_sha() != base) is agent_commits
    assert git.probe_remote_ref("origin", "refs/heads/main") == git.head_sha()
    assert not (tmp_path / "unfinished.txt").exists()
    assert (tmp_path / "finished.txt").exists() is agent_commits
    assert gh.issue_view(42).state == ("CLOSED" if agent_commits else "OPEN")
    (end,) = [e for e in lp._logged_events(tmp_path)
              if e["type"] == "wrapper.contribution.end"]
    assert end["published"] is agent_commits
    assert end["ending"] == "timeout"
    assert end["issues"][0]["status"] == ("closed" if agent_commits else "no-progress")
    landed = git.commits_between(base, git.head_sha())
    assert not any("GitLoopy-Checkpoint:" in c.message for c in landed)
    if agent_sha:
        assert agent_sha in {c.sha for c in landed}
    checkpoints = [e for e in lp._logged_events(tmp_path)
                   if e["type"] == "wrapper.checkpoint.recorded"]
    if not dirty:
        assert not checkpoints
        assert "Checkpoint preserved" not in lp._diag_log(tmp_path)
        return
    (checkpoint,) = checkpoints
    ref = f"refs/heads/git-loopy-checkpoints/issue-42/{checkpoint['sha']}"
    assert ref in lp._diag_log(tmp_path)
    lane_branch = git_module.lane_branch_name(lp._run_id(tmp_path), 42)
    assert git.branch_tip(lane_branch).sha == checkpoint["sha"]
    if push_fails:
        assert git.probe_remote_ref("origin", ref) is None
        assert "fixture push rejected" in lp._diag_log(tmp_path)
        assert not any(ref in body for _, body in gh.issue_comment_calls)
    else:
        assert git.probe_remote_ref("origin", ref) == checkpoint["sha"]
        assert any(ref in body for _, body in gh.issue_comment_calls)
        git.delete_branch(lane_branch)
        assert git.probe_remote_ref("origin", ref) == checkpoint["sha"]


def test_retry_is_told_the_checkpoint_but_is_cut_from_base(tmp_path, monkeypatch):
    prompts = []
    branches = []

    class Session(lp._NoProgressFakeSession):
        async def send_and_wait(self, prompt, *, timeout=60.0, **extra):
            assert self._working_directory is not None
            path = Path(self._working_directory)
            lane = git_module.SubprocessGitClient(path)
            assert lane.head_sha() == base
            assert not (path / "unfinished.txt").exists()
            prompts.append(prompt)
            branches.append(lane.current_branch())
            (path / "unfinished.txt").write_text(f"attempt {len(prompts)}\n")
            raise TimeoutError("session timed out")

    class Client(lp._NoProgressFakeClient):
        _session_cls = Session

    git, gh, base = _wire(tmp_path, monkeypatch, Client)
    original_view = gh.issue_view

    def view_with_posted_comments(number):
        issue = original_view(number)
        return replace(issue, comments=tuple(
            gh_module.Comment(body=body, author="runner", created_at=f"2026-10-{i + 1:02}")
            for i, (ref, body) in enumerate(gh.issue_comment_calls) if ref == number
        ))

    monkeypatch.setattr(gh, "issue_view", view_with_posted_comments)
    assert asyncio.run(loop_module.run(lp._pinned_config(42, max_iterations=2))) == 0
    assert len(prompts) == len(set(branches)) == 2
    assert "git-loopy-checkpoints/" not in prompts[0]
    assert "git-loopy-checkpoints/issue-42/" in prompts[1]
    assert "The next attempt starts from base" in prompts[1]
    assert git.head_sha() == base
    assert gh.issue_view(42).state == "OPEN"


@pytest.mark.parametrize("agent_commits", [False, True])
def test_materialized_host_checkpoint_is_not_published(tmp_path, monkeypatch, agent_commits):
    git, gh, base = _wire(tmp_path, monkeypatch)
    checkpoint_sha = None

    class Host:
        placement: Placement = "github-actions"
        isolation_grade: IsolationGrade = "workspace separation only"
        capacity = 2

        async def run_contribution(self, request: ContributionRequest) -> ContributionSuccess:
            nonlocal checkpoint_sha
            branch = "git-loopy/remote/issue-42"
            path = tmp_path.parent / f"{tmp_path.name}.host"
            child = git.add_issue_clone(path, branch=branch, base=request.base_revision)
            if agent_commits:
                (path / "finished.txt").write_text("finished\n")
                child.add_all()
                child.commit("Implement contribution\n\nCloses #42")
            else:
                (path / "earlier.txt").write_text("earlier unfinished work\n")
                child.add_all()
                child.commit(checkpoint_message(42))
            (path / "unfinished.txt").write_text("unfinished\n")
            child.add_all()
            checkpoint_sha = child.commit(checkpoint_message(42))
            remote_ref = f"refs/heads/{branch}"
            assert child.push_ref("origin", remote_ref, checkpoint_sha, None)
            assert git.reclaim_issue_clone(path, branch=branch)
            return ContributionSuccess(
                branch=None, remote="origin", ref=remote_ref, sha=checkpoint_sha, events=(),
                placement=self.placement, isolation_grade=self.isolation_grade,
                # Older hosts counted their Checkpoint as progress.
                ending=SessionOutcomeRecord(
                    outcome=SessionOutcome.TIMEOUT, progressed=True,
                    termination=SessionTermination.TIMED_OUT,
                ),
            )

    original_loop = loop_module._ParallelLoop

    def with_remote_host(*args: Any, **kwargs: Any):
        kwargs["execution_host"] = Host()
        return original_loop(*args, **kwargs)

    monkeypatch.setattr(loop_module, "_ParallelLoop", with_remote_host)
    assert asyncio.run(loop_module.run(lp._pinned_config(42, max_iterations=1))) == 0
    assert (tmp_path / "finished.txt").exists() is agent_commits
    assert not (tmp_path / "unfinished.txt").exists()
    assert gh.issue_view(42).state == ("CLOSED" if agent_commits else "OPEN")
    assert not any(
        "GitLoopy-Checkpoint:" in c.message for c in git.commits_between(base, git.head_sha())
    )
    ref = f"refs/heads/git-loopy-checkpoints/issue-42/{checkpoint_sha}"
    assert git.probe_remote_ref("origin", ref) == checkpoint_sha
    (end,) = [e for e in lp._logged_events(tmp_path)
              if e["type"] == "wrapper.contribution.end"]
    assert end["published"] is agent_commits
    assert end["ending"] == "timeout"


def test_recovery_cannot_reintroduce_the_preserved_checkpoint(tmp_path, monkeypatch):
    lane_branch = None

    class Session(lp._NoProgressFakeSession):
        async def send_and_wait(self, prompt, *, timeout=60.0, **extra):
            nonlocal lane_branch
            assert self._working_directory is not None
            path = Path(self._working_directory)
            child = git_module.SubprocessGitClient(path)
            if "/integrate/" in str(path):
                assert lane_branch is not None
                assert "Revision " in prompt
                child.merge(lane_branch)
                return
            lane_branch = child.current_branch()
            (path / "finished.txt").write_text("finished\n")
            child.add_all()
            child.commit("Implement contribution\n\nCloses #42")
            (path / "unfinished.txt").write_text("unfinished\n")

    class Client(lp._NoProgressFakeClient):
        _session_cls = Session

    git, gh, base = _wire(tmp_path, monkeypatch, Client)
    monkeypatch.setattr(loop_module, "_make_gate_runner",
                        lambda: FakeGateRunner(outcomes=[False, True]))
    assert asyncio.run(loop_module.run(lp._pinned_config(42, max_iterations=1))) == 0
    assert git.head_sha() == base
    assert gh.issue_view(42).state == "OPEN"
    assert "refusing to publish a Checkpoint" in lp._diag_log(tmp_path)
