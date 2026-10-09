"""Upstream durability is part of Parallel publication, before tracker closure."""

from __future__ import annotations

import asyncio

import pytest

from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import loop as loop_module
from tests import test_loop_parallel as lp
from tests.fakes import FakeGateRunner, FakeGitHubClient
from tests.test_loop_parallel import (  # noqa: F401 - autouse Run fixtures
    _declare_two_local_lane_slots,
    _stub_run_skill_catalog,
)


def _wire(root, monkeypatch, *, release=False):
    git = lp._wire_repo(root)
    if release:
        lp._wire_release_distribution(root)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
    gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            lp._make_issue(
                42,
                labels=["ready-for-agent", "parallel-safe"]
                + (["v1.2.4"] if release else []),
            )
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: gh)
    client = lp._ParallelFakeClient(fake_git=git, scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: client)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    return git, gh, client


def _run():
    return asyncio.run(loop_module.run(lp.RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        max_iterations=1,
        verbosity=0,
        render_reasoning=False,
    )))


@pytest.mark.parametrize("release", [False, True])
def test_parallel_only_run_pushes_current_base_before_close(
    tmp_path, monkeypatch, release
):
    git, gh, client = _wire(tmp_path, monkeypatch, release=release)
    git.configured_upstream = ("origin", "refs/heads/main")
    remote_head = git.head_sha()
    order = []
    original_push, original_close = git.push, gh.issue_close

    def push(*, upstream=None):
        nonlocal remote_head
        assert upstream == git.configured_upstream
        original_push(upstream=upstream)
        remote_head = git.head_sha()
        order.append("push")

    def close(number, comment):
        assert remote_head == git.head_sha()
        order.append("close")
        original_close(number, comment)

    monkeypatch.setattr(git, "push", push)
    monkeypatch.setattr(gh, "issue_close", close)
    assert _run() == 0
    assert order == ["push", "close"]
    assert git.push_calls == 1
    assert len(client.created) == 1
    events = lp._logged_events(tmp_path)
    assert not any(e["type"] == "wrapper.iteration.start" for e in events)
    push_event = next(e for e in events if e["type"] == "wrapper.push.recorded")
    published = next(e for e in events if e["type"] == "wrapper.integration.published")
    closed = next(e for e in events if e["type"] == "wrapper.auto_close")
    assert events.index(push_event) < events.index(published) < events.index(closed)
    for key in ("contribution_id", "issue", "lane_id", "iter"):
        assert push_event[key] == published[key]
    assert push_event["iter"] is None
    assert not any(e["type"] == "wrapper.integration.push_failed" for e in events)
    if release:
        assert git.commit_paths_calls
        assert git.commit_paths_calls[0][0].startswith("chore(release):")


@pytest.mark.parametrize("reason", [
    "authentication failed", "remote unreachable", "non-fast-forward rejected",
])
def test_rejected_publication_keeps_local_work_and_issue_open(
    tmp_path, monkeypatch, reason
):
    git, gh, _client = _wire(tmp_path, monkeypatch)
    before = git.head_sha()
    git.configured_upstream = ("origin", "refs/heads/main")
    git.push_error = git_module.GitError(["git", "push"], 1, reason)
    _run()
    assert git.head_sha() != before
    assert git.push_calls == 1
    assert gh.issue_close_calls == []
    assert gh.issue_view(42).state == "OPEN"
    assert lp._lane_branch_deletes(git) == []
    diagnostics = lp._diag_log(tmp_path)
    assert "integration #42: publication push failed:" in diagnostics
    assert reason in diagnostics
    events = lp._logged_events(tmp_path)
    failed = next(e for e in events if e["type"] == "wrapper.integration.push_failed")
    end = next(e for e in events if e["type"] == "wrapper.contribution.end")
    assert reason in failed["message"]
    assert failed["issue"] == 42 and failed["iter"] is None
    assert failed["contribution_id"] == end["contribution_id"]
    assert end["published"] is False
    assert not any(e["type"] in {
        "wrapper.integration.published", "wrapper.auto_close", "wrapper.push.recorded",
    } for e in events)


@pytest.mark.parametrize("green_attempt", [1, 2, 3])
def test_recovery_publication_push_failure_is_terminal(
    tmp_path, monkeypatch, green_attempt
):
    git, gh, client = _wire(tmp_path, monkeypatch, release=True)
    before = git.head_sha()
    git.configured_upstream = ("origin", "refs/heads/main")
    git.push_error = git_module.GitError(["git", "push"], 1, "push rejected")
    gate = FakeGateRunner(outcomes=[False] * green_attempt + [True])
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: gate)

    assert _run() == 0

    assert git.head_sha() != before
    assert len(git.merge_calls) == 1
    assert git.push_calls == 1
    assert len(client.created) == 1 + green_attempt
    assert len(git.commit_paths_calls) == 1
    assert git.commit_paths_calls[0][0].startswith("chore(release):")
    assert gh.issue_close_calls == []
    assert gh.issue_view(42).state == "OPEN"
    assert gh.issue_comment_calls == []
    assert lp._lane_branch_deletes(git) == []

    events = lp._logged_events(tmp_path)
    recovery = [
        e for e in events if e["type"] == "wrapper.integration.recovery_started"
    ]
    assert [e["attempt"] for e in recovery] == list(range(1, green_attempt + 1))
    failures = [
        e for e in events if e["type"] == "wrapper.integration.push_failed"
    ]
    assert len(failures) == 1
    ends = [e for e in events if e["type"] == "wrapper.contribution.end"]
    assert len(ends) == 1
    assert ends[0]["published"] is False
    assert failures[0]["contribution_id"] == ends[0]["contribution_id"]
    assert events.index(recovery[-1]) < events.index(failures[0]) < events.index(ends[0])
    assert not any(e["type"] in {
        "wrapper.integration.published", "wrapper.auto_close",
        "wrapper.push.recorded", "wrapper.release.advanced",
        "wrapper.iteration.start",
    } for e in events)


def test_local_publication_does_not_attempt_a_push_or_escalate(
    tmp_path, monkeypatch
):
    git, gh, _client = _wire(tmp_path, monkeypatch)
    git.push_error = git_module.GitError(["git", "push"], 1, "must not push")
    assert _run() == 0
    assert git.push_calls == 0
    assert [n for n, _ in gh.issue_close_calls] == [42]
    assert "publication push failed" not in lp._diag_log(tmp_path)
    events = lp._logged_events(tmp_path)
    assert any(e["type"] == "wrapper.integration.published" for e in events)
    assert not any(e["type"] in {
        "wrapper.integration.push_failed", "wrapper.push.recorded",
    } for e in events)


def test_unreadable_upstream_is_not_local_publication(tmp_path, monkeypatch):
    git, gh, _client = _wire(tmp_path, monkeypatch)

    def unreadable():
        raise git_module.GitError(["git", "for-each-ref"], 1, "unreadable upstream")

    monkeypatch.setattr(git, "upstream", unreadable)
    _run()
    assert git.push_calls == 0
    assert gh.issue_close_calls == []
    failed = next(e for e in lp._logged_events(tmp_path)
                  if e["type"] == "wrapper.integration.push_failed")
    assert "unreadable upstream" in failed["message"]


def test_lease_lost_after_local_merge_cannot_push_or_close(tmp_path, monkeypatch):
    git, gh, _client = _wire(tmp_path, monkeypatch)
    git.remote_urls = {"origin": "git@github.com:x/y.git"}
    git.configured_upstream = ("origin", "refs/heads/main")
    before = git.head_sha()
    original_merge = git.merge

    def steal_after_merge(branch):
        original_merge(branch)
        lp._rival_lease(git, 42, repository="x/y")

    monkeypatch.setattr(git, "merge", steal_after_merge)
    _run()
    assert git.head_sha() != before
    assert git.push_calls == 0
    assert gh.issue_close_calls == []
    failed = next(e for e in lp._logged_events(tmp_path)
                  if e["type"] == "wrapper.integration.push_failed")
    assert "Lease" in failed["message"]
