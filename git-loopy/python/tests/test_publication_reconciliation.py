"""Real-Git publication death and restart at the Run boundary (#419)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import loop as loop_module
from git_loopy.config import RunConfig
from git_loopy.git import SubprocessGitClient
from git_loopy.issue_lease import LeaseTransport
from git_loopy.lease_lifecycle import LeaseLifecycle
from git_loopy.run_control import RunControlArtifact
from tests import test_loop_parallel as lp
from tests.fakes import FakeGateRunner, FakeGitHubClient
from tests.test_loop_parallel import (  # noqa: F401
    _declare_two_local_lane_slots,
    _stub_run_skill_catalog,
)


def git(root, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=root, check=check, capture_output=True, text=True,
    ).stdout.strip()


def repository(root):
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Publication test")
    git(root, "config", "user.email", "publication@example.test")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(".git-loopy/\n")
    (root / "work").write_text("base\n")
    (root / "git-loopy").mkdir(exist_ok=True)
    (root / "git-loopy" / "prompt.md").write_text("do the work")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return SubprocessGitClient(root)


class Tracker(FakeGitHubClient):
    """A tracker service surviving the publisher, not an in-process close log."""

    def __init__(self, root, *, release=False):
        super().__init__(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[lp._make_issue(42, labels=["ready-for-agent", "parallel-safe"]
                                  + (["v1.2.4"] if release else []))],
        )
        self.path = root / ".git-loopy" / "tracker.json"
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text('{"state": "OPEN", "closures": 0}')

    def issue_view(self, number):
        return replace(super().issue_view(number), state=json.loads(self.path.read_text())["state"])

    def issue_list(self, label, state="open"):
        self._issues[42] = self.issue_view(42)
        return super().issue_list(label, state)

    def issue_close(self, number, comment):
        state = json.loads(self.path.read_text())
        assert state["state"] == "OPEN", "duplicate tracker closure"
        self.path.write_text(json.dumps({"state": "CLOSED", "closures": state["closures"] + 1}))
        super().issue_close(number, comment)


class Session(lp._ParallelFakeSession):
    message = "Implement issue\n\nCloses #42"

    async def send_and_wait(self, prompt, *, timeout=60, **extra):
        path = Path(self._working_directory)
        (path / "contribution").write_text("verified contribution\n")
        lane = SubprocessGitClient(path)
        lane.add_all()
        lane.commit(self.message)


class Client(lp._ParallelFakeClient):
    _session_cls = Session


def wire(root, monkeypatch):
    client = Client(fake_git=None, scripted_events=[])
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[],
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: SubprocessGitClient(root))
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    monkeypatch.setattr(loop_module, "_make_client", lambda: client)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda **kw: FakeGateRunner())
    return tracker, client


def run(**overrides):
    config = RunConfig(
        model="claude-opus-4.8-max", issue_source="github",
        max_iterations=1, verbosity=0, render_reasoning=False,
        prevent_idle_sleep=False,
    )
    return asyncio.run(loop_module.run(replace(config, **overrides)))


def diagnostics(root):
    return "\n".join(p.read_text() for p in (root / ".git-loopy/logs").glob("*.log"))


def test_unrecorded_unfinished_merge_refuses_startup_without_touching_base(
    tmp_path, monkeypatch,
):
    repository(tmp_path)
    git(tmp_path, "checkout", "-b", "other")
    (tmp_path / "work").write_text("other\n")
    git(tmp_path, "commit", "-am", "other")
    git(tmp_path, "checkout", "main")
    (tmp_path / "work").write_text("main\n")
    git(tmp_path, "commit", "-am", "main")
    git(tmp_path, "merge", "other", check=False)
    before = (tmp_path / "work").read_bytes(), git(tmp_path, "ls-files", "--stage")
    tracker, client = wire(tmp_path, monkeypatch)

    assert run() == 1

    assert "Publication reconciliation" in diagnostics(tmp_path)
    assert "unfinished base merge" in diagnostics(tmp_path)
    assert (tmp_path / "work").read_bytes() == before[0]
    assert git(tmp_path, "ls-files", "--stage") == before[1]
    assert (tmp_path / ".git/MERGE_HEAD").exists()
    assert not client.created
    assert not tracker.issue_close_calls


def crash_publication(root, monkeypatch, *, boundary="merge", tracker=None):
    """Die after Git's write, before its caller can persist an acknowledgement."""
    real_run = git_module._run

    def command(args, **kwargs):
        if boundary == "before-merge" and args[0] == "merge" and Path(kwargs["cwd"]) == root:
            os._exit(77)
        result = real_run(args, **kwargs)
        on_base = Path(kwargs["cwd"]) == root
        if on_base and (
            (boundary == "merge" and args[0] == "merge")
            or (boundary == "release" and args[0] == "commit"
                and any("chore(release):" in arg for arg in args))
            or (boundary == "push" and args[0] == "push"
                and any(arg.endswith(":refs/heads/main") for arg in args))
        ):
            os._exit(77)
        return result

    pid = os.fork()
    if pid == 0:
        monkeypatch.setattr(git_module, "_run", command)
        if boundary == "close":
            original_close = tracker.issue_close

            def close(number, comment):
                original_close(number, comment)
                os._exit(77)

            monkeypatch.setattr(tracker, "issue_close", close)
        try:
            os._exit(run())
        except BaseException:
            os._exit(99)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 77, diagnostics(root)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_restart_reconciles_merged_publication_without_another_agent(
    tmp_path, monkeypatch,
):
    source = repository(tmp_path)
    _, client = wire(tmp_path, monkeypatch)
    tracker = Tracker(tmp_path)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    crash_publication(tmp_path, monkeypatch)
    published = source.head_sha()
    assert tracker.issue_view(42).state == "OPEN"

    assert run() == 0

    assert tracker.issue_view(42).state == "CLOSED"
    assert source.head_sha() == published
    assert not client.created
    assert "Publication reconciliation completed" in diagnostics(tmp_path)
    assert run() == 0
    assert json.loads(tracker.path.read_text())["closures"] == 1
    assert not client.created


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
@pytest.mark.parametrize("boundary", ["release", "push", "close"])
def test_restart_proves_final_release_and_upstream_without_repeating_work(
    tmp_path, monkeypatch, boundary,
):
    lp._wire_release_distribution(tmp_path)
    source = repository(tmp_path)
    upstream = tmp_path.parent / f"{tmp_path.name}.git"
    subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
    git(tmp_path, "remote", "add", "origin", str(upstream))
    git(tmp_path, "push", "-u", "origin", "main")
    _, client = wire(tmp_path, monkeypatch)
    tracker = Tracker(tmp_path, release=True)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    publisher_pid = os.getpid()
    authority = []

    def leases(config, git, *, run_id, diag, **kwargs):
        # The old Run's TTL has elapsed at restart. All ref I/O and CAS remain real.
        lifecycle = LeaseLifecycle(
            LeaseTransport(git, remote="origin", repository="x/y"),
            run_id=run_id, host="test", pid=os.getpid(),
            clock=lambda: 2000 if os.getpid() == publisher_pid else 1000,
            heartbeats=None,
        )
        authority.append(lifecycle)
        return lifecycle

    monkeypatch.setattr(loop_module, "_make_lease_lifecycle", leases)
    crash_publication(tmp_path, monkeypatch, boundary=boundary, tracker=tracker)
    final = source.head_sha()
    assert (tmp_path / "VERSION").read_text().strip() == "1.2.4-alpha.1"
    assert tracker.issue_view(42).state == ("CLOSED" if boundary == "close" else "OPEN")

    assert run() == 0

    assert source.head_sha() == final
    assert source.probe_remote_ref("origin", "refs/heads/main") == final
    assert tracker.issue_view(42).state == "CLOSED"
    assert not client.created
    assert authority
    assert run() == 0
    assert json.loads(tracker.path.read_text())["closures"] == 1
    assert (tmp_path / "VERSION").read_text().strip() == "1.2.4-alpha.1"


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    """A dead Run's actual merge and retained publication record."""
    def make(*, remote=True, release=False, boundary="merge"):
        if release:
            lp._wire_release_distribution(tmp_path)
        source = repository(tmp_path)
        upstream = tmp_path.parent / f"{tmp_path.name}.git"
        if remote:
            subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
            git(tmp_path, "remote", "add", "origin", str(upstream))
            git(tmp_path, "push", "-u", "origin", "main")
        _, client = wire(tmp_path, monkeypatch)
        tracker = Tracker(tmp_path, release=release)
        monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
        crash_publication(tmp_path, monkeypatch, boundary=boundary, tracker=tracker)
        return source, tracker, client, upstream
    return make


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
@pytest.mark.parametrize("case,reason", [
    ("later-local", "later unrecorded local commits"),
    ("dirty", "dirty base"),
    ("staged", "dirty base"),
    ("untracked", "dirty base"),
    ("upstream-changed", "upstream changed"),
    ("upstream-removed", "upstream changed"),
    ("remote-unreadable", "git subprocess failed"),
    ("branch-changed", "base branch changed"),
    ("corrupt", "unreadable pending record"),
    ("incomplete", "corrupt or incomplete pending record"),
    ("contradictory", "contradicts publication evidence"),
    ("release-unresolved", "final Release-line publication remains unresolved"),
    ("before-merge", "verified stage is not contained"),
    ("tracker-changed", "tracker repository changed"),
    ("release-tree-changed", "final Release-line publication is unresolved"),
])
def test_startup_refuses_ambiguous_publication_before_sweep_or_dispatch(
    tmp_path, monkeypatch, interrupted, case, reason,
):
    source, tracker, client, upstream = interrupted(
        release=case in {"release-unresolved", "release-tree-changed"},
        boundary=(
            "before-merge" if case == "before-merge"
            else "release" if case == "release-tree-changed" else "merge"
        ),
    )
    record = tmp_path / ".git-loopy/publication.json"
    if case in {"later-local", "dirty", "staged"}:
        (tmp_path / "work").write_text("operator edit\n")
        if case == "later-local":
            git(tmp_path, "commit", "-am", "later unrecorded work")
        elif case == "staged":
            git(tmp_path, "add", "work")
    elif case == "untracked":
        (tmp_path / "operator").write_text("keep me")
    elif case == "upstream-changed":
        git(tmp_path, "remote", "set-url", "origin", str(upstream) + ".other")
    elif case == "upstream-removed":
        git(tmp_path, "branch", "--unset-upstream")
    elif case == "remote-unreadable":
        upstream.rename(upstream.with_suffix(".offline"))
    elif case == "branch-changed":
        git(tmp_path, "checkout", "-b", "different-base")
    elif case == "corrupt":
        record.write_text("{")
    elif case == "tracker-changed":
        monkeypatch.setattr(
            tracker, "repo_view",
            lambda: gh_module.Repo(owner="different", name="tracker", default_branch="main"),
        )
    elif case in {"incomplete", "contradictory", "release-tree-changed"}:
        data = json.loads(record.read_text())
        if case == "incomplete":
            del data["stage"]
        elif case == "contradictory":
            data["final"] = data["before"]
        else:
            data["release_tree"] = source.commit_identity(data["before"])[0]
        record.write_text(json.dumps(data))
    before = source.head_sha(), git(tmp_path, "ls-files", "--stage"), (tmp_path / "work").read_bytes()
    residue = git(tmp_path, "worktree", "list", "--porcelain"), git(tmp_path, "for-each-ref")
    contents = record.read_bytes()

    assert run() == 1

    assert reason in diagnostics(tmp_path)
    assert not client.created
    assert tracker.issue_view(42).state == "OPEN"
    assert before == (source.head_sha(), git(tmp_path, "ls-files", "--stage"), (tmp_path / "work").read_bytes())
    assert residue == (git(tmp_path, "worktree", "list", "--porcelain"), git(tmp_path, "for-each-ref"))
    assert contents == record.read_bytes()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_remote_descendant_is_proof_without_republishing(tmp_path, monkeypatch, interrupted):
    source, tracker, client, upstream = interrupted(boundary="push")
    final = source.head_sha()
    other = tmp_path.parent / f"{tmp_path.name}-other"
    subprocess.run(["git", "clone", "-b", "main", str(upstream), str(other)],
                   check=True, capture_output=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.test")
    git(other, "commit", "--allow-empty", "-m", "newer remote")
    git(other, "push", "origin", "main")
    newer = git(other, "rev-parse", "HEAD")
    # A rejection hook makes an unnecessary recovery push observable.
    hook = upstream / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    assert run() == 0

    assert tracker.issue_view(42).state == "CLOSED"
    assert source.head_sha() == final
    assert source.probe_remote_ref("origin", "refs/heads/main") == newer
    assert not client.created
    assert run() == 0
    assert json.loads(tracker.path.read_text())["closures"] == 1
    assert not client.created


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_pending_publication_owned_by_a_live_run_is_not_reconciled(tmp_path, interrupted):
    source, tracker, client, _ = interrupted(remote=False)
    pending = json.loads((tmp_path / ".git-loopy/publication.json").read_text())
    trace = next((tmp_path / ".git-loopy/logs").glob(f"*{pending['run_id']}.jsonl"))
    owner = RunControlArtifact.acquire(trace)
    before = source.head_sha()
    try:
        assert run() == 1
    finally:
        owner.close()
    assert "originating Run is live or its liveness is unknown" in diagnostics(tmp_path)
    assert tracker.issue_view(42).state == "OPEN"
    assert source.head_sha() == before
    assert not client.created


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_failed_recovery_push_is_not_retried_and_keeps_issue_open(
    tmp_path, monkeypatch, interrupted,
):
    source, tracker, client, upstream = interrupted()
    before = source.head_sha()
    hook = upstream / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\necho 'publication refused by policy' >&2\nexit 1\n")
    hook.chmod(0o755)
    assert run() == 1
    assert "pre-receive hook declined" in diagnostics(tmp_path)
    hook.unlink()

    assert run() == 1

    assert "recovery push already attempted" in diagnostics(tmp_path)
    assert tracker.issue_view(42).state == "OPEN"
    assert source.head_sha() == before
    assert source.probe_remote_ref("origin", "refs/heads/main") != before
    assert not client.created


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
@pytest.mark.parametrize("authority", ["missing", "unexpired", "unavailable", "fence-lost"])
def test_reconciliation_requires_fresh_lease_and_individual_fence(
    tmp_path, monkeypatch, interrupted, authority,
):
    publisher_pid = os.getpid()

    def lifecycle(source, run_id, clock):
        return LeaseLifecycle(
            LeaseTransport(source, remote="origin", repository="x/y"),
            run_id=run_id, host="test", pid=os.getpid(), clock=lambda: clock,
            heartbeats=None,
        )

    def leases(config, git, *, run_id, **kwargs):
        return lifecycle(git, run_id, 2000 if os.getpid() == publisher_pid else 1000)

    monkeypatch.setattr(loop_module, "_make_lease_lifecycle", leases)
    source, tracker, client, upstream = interrupted(
        boundary="push" if authority == "fence-lost" else "merge",
    )
    before = source.head_sha()
    if authority == "missing":
        monkeypatch.setattr(loop_module, "_make_lease_lifecycle", lambda *a, **k: None)
    elif authority == "unexpired":
        monkeypatch.setattr(
            loop_module, "_make_lease_lifecycle",
            lambda config, git, *, run_id, **k: lifecycle(git, run_id, 1100),
        )
    elif authority == "unavailable":
        hook = upstream / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho 'permission denied' >&2\nexit 1\n")
        hook.chmod(0o755)
    else:
        original_view = tracker.issue_view

        def view(number):
            rival = lifecycle(source, "rival", 4000)
            assert rival.take(number).granted
            return original_view(number)

        monkeypatch.setattr(tracker, "issue_view", view)

    assert run() == 1

    assert "Lease" in diagnostics(tmp_path)
    assert json.loads(tracker.path.read_text())["state"] == "OPEN"
    assert source.head_sha() == before
    assert not client.created


def test_clean_preupgrade_start_does_not_infer_closure_from_history(tmp_path, monkeypatch):
    repository(tmp_path)
    git(tmp_path, "commit", "--allow-empty", "-m", "historical work\n\nCloses #42")
    tracker, client = wire(tmp_path, monkeypatch)
    before = git(tmp_path, "rev-parse", "HEAD")

    assert run() == 0

    assert not tracker.issue_view_calls
    assert not tracker.issue_close_calls
    assert not client.created
    assert git(tmp_path, "rev-parse", "HEAD") == before


def test_pending_failed_publication_stops_same_run_before_fresh_execution(tmp_path, monkeypatch):
    repository(tmp_path)
    upstream = tmp_path.parent / f"{tmp_path.name}.git"
    subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
    git(tmp_path, "remote", "add", "origin", str(upstream))
    git(tmp_path, "push", "-u", "origin", "main")
    hook = upstream / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _, client = wire(tmp_path, monkeypatch)
    tracker = Tracker(tmp_path)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)

    assert run(max_iterations=3) == 1

    assert len(client.created) == 1
    assert tracker.issue_view(42).state == "OPEN"
    assert (tmp_path / ".git-loopy/publication.json").exists()
    assert "Publication reconciliation required" in diagnostics(tmp_path)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_reconciliation_survives_a_second_death_after_recovery_push(
    tmp_path, monkeypatch, interrupted,
):
    source, tracker, client, upstream = interrupted()
    final = source.head_sha()
    crash_publication(tmp_path, monkeypatch, boundary="push", tracker=tracker)
    assert source.probe_remote_ref("origin", "refs/heads/main") == final
    assert tracker.issue_view(42).state == "OPEN"
    assert json.loads((tmp_path / ".git-loopy/publication.json").read_text())["recovery_push_attempted"]
    hook = upstream / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    assert run() == 0

    assert tracker.issue_view(42).state == "CLOSED"
    assert not client.created
    assert json.loads(tracker.path.read_text())["closures"] == 1


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_recovery_push_names_only_exact_final_sha_without_force(
    tmp_path, monkeypatch, interrupted,
):
    source, tracker, client, _ = interrupted()
    final = source.head_sha()
    real_run = git_module._run
    pushes = []

    def command(args, **kwargs):
        if args[0] == "push":
            pushes.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(git_module, "_run", command)
    assert run() == 0
    assert pushes == [["push", "--", "origin", f"{final}:refs/heads/main"]]
    assert tracker.issue_view(42).state == "CLOSED"
    assert not client.created


@pytest.mark.skipif(not hasattr(os, "fork"), reason="process-death proof uses fork")
def test_reconciled_partial_progress_does_not_invent_closure_intent(
    tmp_path, monkeypatch, interrupted,
):
    monkeypatch.setattr(Session, "message", "Partial progress, not completion")
    source, tracker, client, _ = interrupted(remote=False)
    before = source.head_sha()
    # The tracker has withdrawn this issue from new work; it remains open.
    monkeypatch.setattr(tracker, "issue_list", lambda *a, **k: gh_module.IssueListPage(
        issues=(), complete=True,
    ))

    assert run() == 0

    assert tracker.issue_view(42).state == "OPEN"
    assert not (tmp_path / ".git-loopy/publication.json").exists()
    assert source.head_sha() == before
    assert not client.created


def test_dirty_base_before_publication_never_falls_back_to_an_agent(tmp_path, monkeypatch):
    source = repository(tmp_path)
    _, client = wire(tmp_path, monkeypatch)
    tracker = Tracker(tmp_path)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    (tmp_path / "work").write_text("operator edit\n")
    before = source.head_sha(), git(tmp_path, "ls-files", "--stage")

    assert run(max_iterations=2) == 1

    assert len(client.created) == 1
    assert tracker.issue_view(42).state == "OPEN"
    assert "base is not clean" in diagnostics(tmp_path)
    assert (tmp_path / "work").read_text() == "operator edit\n"
    assert before == (source.head_sha(), git(tmp_path, "ls-files", "--stage"))
