"""The start-of-Run sweep, gated on the control artifact's lock (#455, #445 §F).

A hard kill and a lost power cable leave a **Lane workspace** behind that no
in-process handler ever gets to run for. The actor that covers them cannot be
inside the dead Run at all — it has to be the *next* Run, and the only thing it
can trust is the control artifact's advisory lock: lock free ⇒ that Run is dead
⇒ its workspaces are reclaimable.

These tests drive the real ``loop.run`` rather than :func:`git_loopy.sweep.sweep`
directly, because every claim here is about a Run: that it sweeps at all, that a
Run holding its lock is left alone by its neighbour, and that none of the work
reaches the wire or the durable record.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy.config import RunConfig
from git_loopy.git import lane_branch_name
from git_loopy.run_control import RunControlArtifact, advisory_locking_available
from git_loopy.skill_catalog import build_skill_catalog
from tests.fakes import FakeGitClient, FakeGitHubClient
from tests.test_iteration_end_to_end import FakeCopilotClient


@pytest.fixture(autouse=True)
def _stub_run_skill_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Skill catalog is not this seam; discovering it needs a live session."""

    async def discover(_client: object, **kwargs: object):
        return build_skill_catalog(
            (),
            repo_root=Path(str(kwargs["repo_root"])),
            installed_skills_dir=Path(str(kwargs["installed_skills_dir"])),
        )

    monkeypatch.setattr(loop_module, "_discover_skill_catalog", discover)


@pytest.fixture
def empty_pool_run(tmp_path, monkeypatch):
    """Build one Run against an empty Pool over a prepared :class:`FakeGitClient`.

    An empty Pool is the smallest Run that still reaches every Run-level seam,
    so the sweep runs without a scripted agent session confusing the trace with
    work of its own. The factory takes the branch the clone is checked out on,
    because that is the base a Lane branch was cut from.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    monkeypatch.setattr(
        loop_module, "_make_client", lambda: FakeCopilotClient(scripted_events=[])
    )

    def make(branch: str = "main"):
        git = FakeGitClient(tmp_path, branch=branch)
        monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
        monkeypatch.setattr(
            loop_module,
            "_make_github_client",
            lambda: FakeGitHubClient(
                repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
                issues=[],
            ),
        )

        def drive() -> int:
            return asyncio.run(
                loop_module.run(RunConfig(issue_source="github", max_iterations=1))
            )

        return git, drive

    return make


def test_a_run_reclaims_the_residue_of_a_run_whose_lock_is_free(empty_pool_run) -> None:
    """A Run with no lock on its control artifact cannot still own a workspace."""
    git, drive = empty_pool_run()
    lane_path = git.root / ".git" / "git-loopy" / "RUNDEAD" / "issue-7"
    lane = git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 7), base="main")
    lane.dirty = True

    assert drive() == 0

    assert git.worktree_removes == [lane_path]


def test_a_run_sweeps_against_the_branch_it_publishes_to(empty_pool_run) -> None:
    """A Run off the repository's default branch still resolves its own residue.

    Nothing about a Run reads the repository default: Lanes are cut from the
    checked-out branch and Integration publishes back to it. Asking GitHub for
    the default branch would both cost a round trip on the startup path and
    resolve every Lane branch against a ref it was never merged into.
    """
    git, drive = empty_pool_run(branch="release/2.x")
    lane_branch = lane_branch_name("RUNDEAD", 7)
    git.add_worktree(
        git.root / ".git" / "git-loopy" / "RUNDEAD" / "issue-7",
        branch=lane_branch,
        base="release/2.x",
    )

    assert drive() == 0

    assert git.branch_deletes == [lane_branch]


@pytest.mark.skipif(
    not advisory_locking_available(), reason="this platform has no flock advisory locks"
)
def test_a_run_never_touches_the_residue_of_a_run_still_holding_its_lock(
    empty_pool_run,
) -> None:
    """Two Runs share a clone, so a neighbour's live workspace must survive.

    This is the case the whole lock gate exists for: without it a second Run
    would reclaim the first Run's workspace out from under a live agent session.
    """
    git, drive = empty_pool_run()
    lane_path = git.root / ".git" / "git-loopy" / "RUNLIVE" / "issue-7"
    git.add_worktree(lane_path, branch=lane_branch_name("RUNLIVE", 7), base="main")
    control = RunControlArtifact.acquire(
        git.root / ".git-loopy" / "logs" / "2026-08-23T00-00-00Z-RUNLIVE.jsonl"
    )

    try:
        assert drive() == 0
    finally:
        control.close()

    assert git.worktree_removes == []
    assert git.branch_deletes == []


def test_a_sweep_never_reaches_the_wire_or_the_durable_record(empty_pool_run) -> None:
    """Reclaiming someone else's residue is not this Run's work.

    A **Salvage** Event would light a **Queue** row for an issue this Run never
    worked, and a Summary row would put a **Strike**'s worth of accounting
    behind a directory removal. The sweep is therefore Event-free and
    Summary-free by construction, which is what keeps a swept issue out of every
    progress judgement made about the Run that swept it.
    """
    git, drive = empty_pool_run()
    lane_path = git.root / ".git" / "git-loopy" / "RUNDEAD" / "issue-7"
    lane = git.add_worktree(lane_path, branch=lane_branch_name("RUNDEAD", 7), base="main")
    lane.dirty = True

    assert drive() == 0

    events = [
        json.loads(line)
        for log in sorted((git.root / ".git-loopy" / "logs").glob("*.jsonl"))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert git.worktree_removes == [lane_path]
    assert [event for event in events if event.get("issue") == 7] == []
    assert [event for event in events if "checkpoint" in event["type"]] == []
    summary = json.loads(
        next((git.root / ".git-loopy" / "runs").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert [row["issues"] for row in summary["iterations"]] == [[]]
    assert [row["commits"] for row in summary["iterations"]] == [0]
    assert [row["strikes"] for row in summary["iterations"]] == [0]
