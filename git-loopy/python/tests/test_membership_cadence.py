"""Mid-work Membership at the Run seam, with quiet SDK sessions and fake time."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from git_loopy import loop as loop_module
from git_loopy import gh as gh_module
from git_loopy.config import RunConfig
from git_loopy.interactive.state import LiveRunState
from tests.fakes import FakeGateRunner, FakeGitHubClient
from tests.test_loop_parallel import (
    _ParallelFakeClient,
    _ParallelFakeSession,
    _logged_events,
    _make_issue,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
    _usage_event,
    _wire_repo,
)

# Reuse the SDK discovery fixture, not a second catalog implementation.
pytestmark = pytest.mark.usefixtures("_stub_run_skill_catalog")

_CASES = json.loads(
    (Path(__file__).parents[2] / "conformance/event-schema.json").read_text()
)["membership_cadence_cases"]


def _cases(mode: str) -> list[dict]:
    return [
        case for case in _CASES
        if case["mode"] == mode and "python" in case["distributions"]
    ]


def _membership(path: Path) -> list[dict]:
    return [
        event for event in _logged_events(path)
        if event["type"] == "wrapper.pool.refreshed"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases("serial"), ids=lambda c: c["id"])
async def test_quiet_serial_iteration_sees_new_work_before_it_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: dict,
) -> None:
    now = [0.0]
    monkeypatch.setattr(loop_module, "_membership_clock", lambda: now[0], raising=False)
    monkeypatch.setattr(loop_module, "_MEMBERSHIP_SERVICE_INTERVAL", 0.001, raising=False)
    git = _wire_repo(tmp_path)
    gh = FakeGitHubClient(issues=[_make_issue(42, labels=["ready-for-agent"])])
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: gh)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda **kw: FakeGateRunner())
    checked = []

    class QuietSession(_ParallelFakeSession):
        async def send_and_wait(self, prompt, **kwargs):
            gh.seed_issue(_make_issue(43, labels=["ready-for-agent"]))
            reads = len(gh.issue_list_calls)
            now[0] = case["times"][0]
            await asyncio.sleep(0.02)
            assert len(gh.issue_list_calls) == reads
            now[0] = case["times"][1]
            await asyncio.sleep(0.02)
            sightings = _membership(tmp_path)
            assert [event["issues"] for event in sightings] == case["expected_membership"]
            assert sightings[0]["iter"] is None
            assert not any(
                event["type"] == "wrapper.iteration.end"
                for event in _logged_events(tmp_path)
            )
            assert 43 not in gh.issue_view_calls
            checked.append(True)
            return await super().send_and_wait(prompt, **kwargs)

    client = _ParallelFakeClient(fake_git=git, scripted_events=[], serial_closes=True)
    client._session_cls = QuietSession
    monkeypatch.setattr(loop_module, "_make_client", lambda: client)
    assert await loop_module.run(RunConfig(
        model="claude-opus-4.8-max", issue_source="github",
        max_iterations=1, verbosity=0,
    )) == 0
    assert len(client.created) == 1
    # SDK exceptions are contained by the Runner; never let a swallowed assertion
    # inside a scripted session turn an unexercised check into a green test.
    assert checked == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases("lanes"), ids=lambda c: c["id"])
async def test_full_lanes_share_visibility_and_reuse_an_equivalent_scheduler_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: dict,
) -> None:
    now = [0.0]
    monkeypatch.setattr(loop_module, "_membership_clock", lambda: now[0])
    monkeypatch.setattr(loop_module, "_MEMBERSHIP_SERVICE_INTERVAL", 0.001)
    monkeypatch.setattr(
        loop_module.execution_host_module, "local_execution_host_capacity", lambda: 2
    )
    git = _wire_repo(tmp_path)

    class Tracker(FakeGitHubClient):
        def issue_list(self, *args, **kwargs):
            if not self.issue_list_calls:
                now[0] = case["times"][0]  # Existing read+Event services the cadence.
            return super().issue_list(*args, **kwargs)

    gh = Tracker(issues=[
        _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
        _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
    ])
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: gh)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda **kw: FakeGateRunner())
    started = []
    checked = []
    release = asyncio.Event()

    class QuietLane(_ParallelFakeSession):
        async def send_and_wait(self, prompt, **kwargs):
            started.append(self)
            if len(started) == 1:
                await release.wait()
            else:
                try:
                    gh.seed_issue(_make_issue(44, labels=["ready-for-agent"]))
                    gh.seed_issue(_make_issue(
                        45, labels=["ready-for-agent", "parallel-safe"]
                    ))
                    gh.seed_issue(_make_issue(
                        46, labels=["ready-for-agent"], body="not executable"
                    ))
                    reads = len(gh.issue_list_calls)
                    now[0] = case["times"][1]
                    await asyncio.sleep(0.02)
                    assert len(gh.issue_list_calls) == reads
                    # Miss several intervals. One opportunity, one read, no burst.
                    now[0] = case["times"][2]
                    await asyncio.sleep(0.02)
                    assert len(gh.issue_list_calls) == reads + 1
                    assert [e["issues"] for e in _membership(tmp_path)] == (
                        case["expected_membership"][:2]
                    )
                    assert len(client.created) == 2
                    assert not any(n in gh.issue_view_calls for n in (44, 45, 46))
                    assert not any(
                        e["type"] == "wrapper.contribution.end"
                        for e in _logged_events(tmp_path)
                    )
                    now[0] = case["times"][3]
                    await asyncio.sleep(0.02)
                    assert _membership(tmp_path)[-1]["issues"] == (
                        case["expected_membership"][2]
                    )
                    assert len(_membership(tmp_path)) == 3  # unchanged is observable
                    for number in (42, 43, 44, 45):
                        gh.seed_issue(replace(
                            _make_issue(number, labels=["ready-for-agent"]),
                            state="CLOSED",
                        ))
                    now[0] = case["times"][4]
                    await asyncio.sleep(0.02)
                    assert [e["issues"] for e in _membership(tmp_path)] == (
                        case["expected_membership"]
                    )
                    assert len(gh.issue_list_calls) == reads + 3
                    checked.append(True)
                finally:
                    release.set()
            return await super().send_and_wait(prompt, **kwargs)

    client = _ParallelFakeClient(fake_git=git, scripted_events=[
        _usage_event("claude-opus-4.8-max")
    ])
    client._session_cls = QuietLane
    monkeypatch.setattr(loop_module, "_make_client", lambda: client)
    assert await loop_module.run(RunConfig(
        model="claude-opus-4.8-max", issue_source="github",
        max_iterations=2, verbosity=0,
    )) == 0
    assert checked == [True]
    assert len(started) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases("failure"), ids=lambda c: c["id"])
async def test_failed_visibility_is_diagnosed_and_callbacks_cannot_retry_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: dict,
) -> None:
    failure = case["failure"]
    now = [0.0]
    monkeypatch.setattr(loop_module, "_membership_clock", lambda: now[0])
    monkeypatch.setattr(loop_module, "_MEMBERSHIP_SERVICE_INTERVAL", 0.001)
    git = _wire_repo(tmp_path)

    class Tracker(FakeGitHubClient):
        unexpected = False

        def issue_list(self, *args, **kwargs):
            if self.unexpected:
                self.issue_list_calls.append(("ready-for-agent", "open"))
                raise RuntimeError("visibility transport failed")
            return super().issue_list(*args, **kwargs)

    gh = Tracker(issues=[_make_issue(42, labels=["ready-for-agent"])])
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: gh)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda **kw: FakeGateRunner())
    checked = []

    class Session(_ParallelFakeSession):
        async def send_and_wait(self, prompt, **kwargs):
            gh.seed_issue(_make_issue(43, labels=["ready-for-agent"]))
            reads = len(gh.issue_list_calls)
            if failure == "incomplete":
                gh.issue_list_complete = False
            elif failure == "failed":
                gh.issue_list_error = gh_module.GhError(
                    ["gh", "issue", "list"], 1, "tracker unavailable"
                )
            else:
                gh.unexpected = True
            now[0] = case["times"][0]
            await asyncio.sleep(0.02)
            assert len(gh.issue_list_calls) == reads + 1
            assert _membership(tmp_path) == []
            gh.issue_list_error = None
            gh.issue_list_complete = True
            gh.unexpected = False
            now[0] = case["times"][1]
            for _ in range(4):
                if self._on_event:
                    self._on_event(_usage_event("claude-opus-4.8-max"))
                await asyncio.sleep(0.005)
            assert len(gh.issue_list_calls) == reads + 1
            assert _membership(tmp_path) == []
            now[0] = case["times"][2]
            await asyncio.sleep(0.02)
            assert [e["issues"] for e in _membership(tmp_path)] == (
                case["expected_membership"]
            )
            checked.append(True)
            return await super().send_and_wait(prompt, **kwargs)

    client = _ParallelFakeClient(fake_git=git, scripted_events=[], serial_closes=True)
    client._session_cls = Session
    monkeypatch.setattr(loop_module, "_make_client", lambda: client)
    assert await loop_module.run(RunConfig(
        model="claude-opus-4.8-max", issue_source="github",
        max_iterations=1, verbosity=0,
    )) == 0
    assert checked == [True]
    diagnostics = "\n".join(
        path.read_text() for path in (tmp_path / ".git-loopy" / "logs").glob("*.log")
    )
    assert case["diagnostic"] in diagnostics


@pytest.mark.parametrize("case", _cases("consumer"), ids=lambda c: c["id"])
def test_membership_only_rows_wait_for_authority_to_retire(case: dict) -> None:
    state = LiveRunState(monotonic=lambda: 0.0, wall_clock=lambda: None)
    for step in case["steps"]:
        state.render(step["event"])
        assert {
            str(ref): entry.status for ref, entry in state.ledger.items()
        } == step["expected_statuses"]
        assert list(state.ledger) == [43, 42]
        assert state.active_ref is None


def test_cadence_cases_oblige_python_without_changing_dormant_baselines() -> None:
    assert {case["mode"] for case in _CASES} == {
        "serial", "lanes", "failure", "consumer",
    }
    assert all(case["distributions"] == ["python"] for case in _CASES)


@pytest.mark.asyncio
async def test_quiet_local_markdown_work_emits_no_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(loop_module, "_membership_clock", lambda: now[0])
    monkeypatch.setattr(loop_module, "_MEMBERSHIP_SERVICE_INTERVAL", 0.001)
    git = _wire_repo(tmp_path)
    ready = tmp_path / "prds" / "feature" / "001-ready.md"
    ready.parent.mkdir(parents=True)
    ready.write_text(
        "# Ready\n\n## What to build\nthing\n\n## Acceptance criteria\n- works\n"
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: git)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda **kw: FakeGateRunner())
    checked = []

    class Session(_ParallelFakeSession):
        async def send_and_wait(self, prompt, **kwargs):
            now[0] = 180.0
            await asyncio.sleep(0.02)
            assert _membership(tmp_path) == []
            checked.append(True)
            return await super().send_and_wait(prompt, **kwargs)

    client = _ParallelFakeClient(fake_git=git, scripted_events=[])
    client._session_cls = Session
    monkeypatch.setattr(loop_module, "_make_client", lambda: client)
    assert await loop_module.run(RunConfig(
        model="claude-opus-4.8-max", issue_source="prds",
        max_iterations=1, verbosity=0,
    )) == 0
    assert checked == [True]
    assert _membership(tmp_path) == []
