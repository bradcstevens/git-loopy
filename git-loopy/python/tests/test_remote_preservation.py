"""Shared guest preservation against real repositories and a fake GitHub boundary."""

import json
import subprocess
from datetime import datetime, timezone

import pytest

from git_loopy.git import SubprocessGitClient, lane_branch_name
from git_loopy.persist import make_run_id, run_stem
from git_loopy.run_control import RunControlArtifact
from git_loopy.wrapper import CHECKPOINT_COMMENT_MARKER, is_checkpoint_message


def git(root, *args):
    location = [f"--git-dir={root}"] if root.name.endswith(".git") else ["-C", str(root)]
    return subprocess.check_output(["git", *location, *args], text=True).strip()


class GitHub:
    def __init__(self):
        self.comments = {}

    def issue_comments(self, number):
        return self.comments.get(number, ())

    def issue_comment(self, number, body):
        self.comments.setdefault(number, []).append(body)


@pytest.fixture
def guest(tmp_path):
    origin, root = tmp_path / "origin.git", tmp_path / "guest"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / ".gitignore").write_text(".git-loopy/\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    git(root, "push", "-q", "origin", "HEAD")
    identity = make_run_id()
    trace = root / ".git-loopy" / "logs" / f"{run_stem(identity, datetime.now(timezone.utc))}.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text(json.dumps({"type": "wrapper.run.start", "run_id": identity}) + "\n")
    with RunControlArtifact.acquire(trace):
        pass
    return root, origin, identity, trace


def test_teardown_salvages_issue_clone_and_preserves_all_branches_before_halt(guest, tmp_path):
    from git_loopy.remote_teardown import teardown_run

    root, origin, identity, _ = guest
    source = SubprocessGitClient(root)
    branch = lane_branch_name(identity, 7)
    clone = source.add_issue_clone(tmp_path / "lane", branch=branch, base="HEAD")
    (clone.root / "unfinished").write_text("do not lose this")
    git(root, "checkout", "-qb", "operator-work")
    (root / "ahead").write_text("unpublished work")
    git(root, "add", ".")
    git(root, "commit", "-qm", "ahead")
    ahead = git(root, "rev-parse", "HEAD")
    github = GitHub()
    halted = []

    def halt():
        salvaged = git(clone.root, "rev-parse", "HEAD")
        assert is_checkpoint_message(git(clone.root, "show", "-s", "--format=%B"))
        assert git(origin, "rev-parse", f"refs/heads/git-loopy-checkpoints/issue-7/{salvaged}") == salvaged
        assert git(origin, "rev-parse", f"refs/heads/git-loopy-checkpoints/run-{identity}/{ahead}") == ahead
        assert github.comments[7][0].startswith(CHECKPOINT_COMMENT_MARKER)
        assert "unfinished" in git(origin, "ls-tree", "--name-only", salvaged)
        halted.append(True)

    teardown_run(root, identity, github=github, halt=halt)
    teardown_run(root, identity, github=github, halt=halt)
    assert halted == [True, True]
    assert len(github.comments[7]) == 1
    assert clone.root.exists()  # preservation itself never removes local work


@pytest.mark.parametrize("defect", [
    "live", "unknown", "ambiguous", "origin-offline", "comment-fails",
    "incomplete-clone", "dirty-during-push", "conflict",
])
def test_teardown_refuses_unproven_or_unpreserved_work(guest, tmp_path, defect):
    from git_loopy.remote_teardown import teardown_run
    from git_loopy.run_discovery import RunTargetError
    from git_loopy.git import GitError

    root, origin, identity, trace = guest
    git(root, "checkout", "-qb", lane_branch_name(identity, 7))
    (root / "work").write_text("unfinished work")
    github = GitHub()
    halted = []
    lock = None
    if defect == "live":
        lock = RunControlArtifact.acquire(trace)
    elif defect == "unknown":
        trace.with_suffix(".control").unlink()
    elif defect == "ambiguous":
        duplicate = trace.with_name(f"2026-01-01T00-00-00Z-{identity}.jsonl")
        duplicate.write_text(trace.read_text())
        duplicate.with_suffix(".control").touch()
    elif defect == "origin-offline":
        origin.rename(tmp_path / "unreachable.git")
    elif defect == "comment-fails":
        def fail(*args):
            raise RunTargetError("GitHub unavailable")
        github.issue_comment = fail
    elif defect == "incomplete-clone":
        git(root, "config", "--add", "git-loopy.issueClone", json.dumps({
            "path": str(tmp_path / "missing"), "branch": lane_branch_name(identity, 8),
            "preparing": False,
        }))
    elif defect == "dirty-during-push":
        def changed(number, comment):
            (root / "more-work").write_text("an orphan child is still writing")
        github.issue_comment = changed
    elif defect == "conflict":
        git(root, "add", ".")
        git(root, "commit", "-qm", "ours")
        git(root, "checkout", "-qb", "other", "HEAD~")
        (root / "work").write_text("other version")
        git(root, "add", ".")
        git(root, "commit", "-qm", "theirs")
        subprocess.run(["git", "-C", str(root), "merge", lane_branch_name(identity, 7)],
                       capture_output=True, check=False)
    try:
        with pytest.raises((RunTargetError, GitError, OSError)):
            teardown_run(root, identity, github=github, halt=lambda: halted.append(True))
    finally:
        if lock:
            lock.close()
    assert halted == []
    assert (root / "work").exists()
