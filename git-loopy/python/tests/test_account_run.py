"""Account policy observed at serial/Parallel Run and fake SDK boundaries."""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

import pytest
from copilot.generated.session_events import (
    AssistantMessageData, ModelCallFailureData, ModelCallFailureSource,
    SessionErrorData, SessionEventType, ToolExecutionCompleteData,
    ToolExecutionCompleteResult,
)

from git_loopy import gh, loop
from git_loopy.config import RunConfig
from git_loopy.account_control import AccountControl
from tests.test_account_control import Clock
from tests.fakes import FakeGitHubClient
from tests.test_iteration_end_to_end import _make_issue, _sdk_event, _stub_run_skill_catalog  # noqa: F401
from tests.test_loop_parallel import (
    FakeGateRunner, _NoProgressFakeSession, _ParallelFakeClient,
    _logged_events, _wire_repo,
)


def _run(tmp_path, monkeypatch, *, parallel=False, code="insufficient_quota",
         commit=False, maximum=5, session_hook=None, refs=(42,), config_overrides=None,
         gate=None):
    git = _wire_repo(tmp_path)
    tracker = FakeGitHubClient(
        repo=gh.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(ref, labels=[
            "ready-for-agent", "task-type:bugfix",
            *(["parallel-safe"] if parallel else []),
        ]) for ref in refs],
    )
    refusal = _sdk_event(
        SessionEventType.SESSION_ERROR,
        SessionErrorData(error_type="APIError", error_code=code,
                         status_code=429, message="Endpoint refused session"),
    )

    class Session(_NoProgressFakeSession):
        async def send_and_wait(self, prompt, *, timeout=60, **extra):
            if commit:
                target = git.worktree_client(Path(self._working_directory)) if self._working_directory else git
                ref = Path(self._working_directory).name.removeprefix("issue-") if self._working_directory else "42"
                target.simulate_agent_commit(subject="fix: work", body=f"Closes #{ref}")
            if session_hook:
                await session_hook(self, tracker)
            return await super().send_and_wait(prompt, timeout=timeout, **extra)

    client = _ParallelFakeClient(fake_git=git, scripted_events=[refusal])
    client._session_cls = Session
    monkeypatch.setattr(loop, "_make_git_client", lambda: git)
    monkeypatch.setattr(loop, "_make_github_client", lambda: tracker)
    monkeypatch.setattr(loop, "_make_client", lambda: client)
    monkeypatch.setattr(loop, "_make_gate_runner", lambda **kw: gate or FakeGateRunner())
    rc = asyncio.run(loop.run(RunConfig(
        max_iterations=maximum, **(config_overrides or {}),
    )))
    return rc, _logged_events(tmp_path), client, tracker, git


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("code,kind", [
    ("insufficient_quota", "quota_exhausted"),
    ("authentication_failed", "authentication_failed"),
])
@pytest.mark.parametrize("commit", [False, True])
def test_terminal_refusal_stops_admission_without_blaming_or_closing_issue(
    tmp_path, monkeypatch, parallel, code, kind, commit, capsys,
):
    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, code=code, commit=commit,
    )
    assert rc == 1
    assert len(client.created) == 1
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert end["outcome"] == "account_failure"
    assert end["account_error"]["kind"] == kind
    assert end["iterations_run"] == 1
    assert not [e for e in events if e["type"] in ("wrapper.strike", "wrapper.auto_close")]
    assert tracker.issue_view(42).state == "OPEN"
    assert len([e for e in events if e["type"] == "wrapper.commit.recorded"]) == int(commit)
    assert git.push_calls == 0, "a close-bearing account-failed commit must not auto-push to base"
    output = capsys.readouterr().out
    assert "account stopping" in output
    assert "✓ run end: account_failure" not in output


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("maximum,cause,starts,waits", [
    (1, "retry_prevented_by_cap", 1, []),
    (10, "retry_budget_exhausted", 3, [30, 60]),
])
def test_rate_run_retries_ordinary_work_with_shared_budget(
    tmp_path, monkeypatch, parallel, maximum, cause, starts, waits,
):
    clock = Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))
    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, code="rate_limit_exceeded",
        maximum=maximum,
    )
    assert rc == 1
    assert len(client.created) == starts
    assert clock.waits == waits
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert end["outcome"] == "account_failure"
    assert end["account_cause"] == cause
    assert end["iterations_run"] == starts
    assert not [e for e in events if e["type"] == "wrapper.strike"]


@pytest.mark.parametrize("evidence", ["status", "recovered", "tool", "network"])
def test_nonterminal_or_nonaccount_evidence_does_not_control_the_run(
    tmp_path, monkeypatch, evidence,
):
    async def script(session, _tracker):
        if evidence == "status":
            data = SessionErrorData(error_type="APIError", message="Request failed", status_code=429)
            event = _sdk_event(SessionEventType.SESSION_ERROR, data)
        elif evidence == "recovered":
            event = _sdk_event(SessionEventType.MODEL_CALL_FAILURE, ModelCallFailureData(
                source=ModelCallFailureSource.TOP_LEVEL, error_code="insufficient_quota",
                error_message="Refused one call", status_code=429,
            ))
        elif evidence == "tool":
            event = _sdk_event(SessionEventType.TOOL_EXECUTION_COMPLETE, ToolExecutionCompleteData(
                tool_call_id="t1", success=False,
                result=ToolExecutionCompleteResult(content="insufficient_quota: 429"),
            ))
        else:
            event = _sdk_event(SessionEventType.SESSION_ERROR, SessionErrorData(
                error_type="NetworkError", message="network error", error_code="ECONNRESET",
            ))
        session._scripted_events = [event, _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(content="The session continued", message_id="m1"),
        )]

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, maximum=1, session_hook=script,
    )
    assert rc == 0
    assert not [e for e in events if e["type"] == "wrapper.account.state"]
    assert len([e for e in events if e["type"] == "wrapper.strike"]) == 1


def test_simultaneous_lane_rate_refusals_coalesce_and_only_one_probe_runs(
    tmp_path, monkeypatch,
):
    clock, initial = Clock(), asyncio.Event()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))
    sends = 0

    async def script(session, _tracker):
        nonlocal sends
        sends += 1
        if sends == 2:
            initial.set()
        await initial.wait()

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, code="rate_limit_exceeded",
        maximum=10, session_hook=script, refs=(42, 43),
    )
    assert rc == 1
    assert sends == 4
    assert clock.waits == [30, 60]
    assert len([e for e in events if e["type"] == "wrapper.account.state"
                and e["cause"] == "probe_admitted"]) == 2
    assert not [e for e in events if e["type"] == "wrapper.strike"]


@pytest.mark.parametrize("parallel", [False, True])
def test_a_close_before_refusal_is_diagnosed_not_reopened_or_verified(
    tmp_path, monkeypatch, parallel,
):
    async def script(_session, tracker):
        tracker.issue_close(42, comment="Agent closed early")

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, session_hook=script, commit=True,
    )
    assert rc == 1
    assert tracker.issue_view(42).state == "CLOSED"
    assert not [e for e in events if e["type"] == "wrapper.auto_close"]
    diagnostics = next((tmp_path / ".git-loopy/logs").glob("*.log")).read_text()
    assert "already closed" in diagnostics and "not verified completion" in diagnostics


def test_terminal_endpoint_identity_survives_a_later_midcall_error(tmp_path, monkeypatch):
    async def script(session, _tracker):
        session._scripted_events.append(_sdk_event(
            SessionEventType.MODEL_CALL_FAILURE,
            ModelCallFailureData(
                source=ModelCallFailureSource.TOP_LEVEL,
                error_type="NetworkError", error_message="Disconnected",
            ),
        ))

    rc, events, client, tracker, git = _run(tmp_path, monkeypatch, session_hook=script)
    assert rc == 1
    assert not [e for e in events if e["type"] == "wrapper.strike"]


@pytest.mark.parametrize("parallel", [False, True])
def test_structured_terminal_exception_is_account_evidence(tmp_path, monkeypatch, parallel):
    class EndpointRefusal(Exception):
        code = "insufficient_quota"
        status_code = 429

    async def script(_session, _tracker):
        raise EndpointRefusal("Endpoint refused the session")

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, session_hook=script,
    )
    assert rc == 1 and len(client.created) == 1
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert end["outcome"] == "account_failure"
    assert end["account_error"]["error_code"] == "insufficient_quota"
    assert not [e for e in events if e["type"] == "wrapper.strike"]


@pytest.mark.parametrize("delay,cause", [(121, "wait_budget_exhausted"), ("invalid", "invalid_retry_delay")])
def test_provider_timing_on_terminal_exception_reaches_run_policy(tmp_path, monkeypatch, delay, cause):
    class EndpointRefusal(Exception):
        code = "rate_limit_exceeded"
        retry_after_seconds = delay

    async def script(_session, _tracker):
        raise EndpointRefusal("Endpoint refused the session")

    clock = Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))
    rc, events, client, tracker, git = _run(tmp_path, monkeypatch, session_hook=script)
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert rc == 1 and len(client.created) == 1
    assert end["account_cause"] == cause
    assert clock.waits == []


@pytest.mark.parametrize("message,code,stops", [
    ("Monthly premium request quota exhausted", None, True),
    ("Authentication failed: invalid credentials", None, True),
    ("Request forbidden", "forbidden", False),
    ("Permission denied opening /tmp/work", None, False),
    ("Disk quota exceeded", None, False),
])
def test_terminal_evidence_must_identify_the_account_not_just_a_failure(
    tmp_path, monkeypatch, message, code, stops,
):
    async def script(session, _tracker):
        session._scripted_events = [_sdk_event(
            SessionEventType.SESSION_ERROR, SessionErrorData(
                error_type="APIError", message=message, error_code=code, status_code=403,
            ),
        )]

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, maximum=1, session_hook=script,
    )
    assert rc == int(stops)
    assert bool([e for e in events if e["type"] == "wrapper.account.state"]) is stops


@pytest.mark.parametrize("parallel", [False, True])
def test_account_refusal_does_not_refund_an_earlier_issue_strike(tmp_path, monkeypatch, parallel):
    sends = 0

    async def script(session, _tracker):
        nonlocal sends
        sends += 1
        if sends == 1:
            session._scripted_events = []

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, session_hook=script,
    )
    strikes = [e for e in events if e["type"] == "wrapper.strike"]
    assert rc == 1 and sends == 2
    assert len(strikes) == 1 and strikes[0]["strikes"] == 1
    bindings = [e for e in events if e["type"] == "wrapper.pickup.bound"]
    assert [e["lifecycle_position"] for e in bindings] == ["fresh", "retrying"]
    ends = [e for e in events if e["type"] in ("wrapper.iteration.end", "wrapper.contribution.end")]
    if parallel:
        assert ends[-1]["summary"]["strike_reaction"] == "none"
    else:
        assert ends[-1]["summary"]["strikes"] == 1


def test_operator_stop_during_run_cooldown_keeps_primary_reason(tmp_path, monkeypatch):
    from tests.test_iteration_end_to_end import _capture_serial_loops

    built = _capture_serial_loops(monkeypatch)

    async def sleep(_seconds):
        built[0].request_stop_drain()
        await asyncio.Future()

    monkeypatch.setattr(loop, "AccountControl", partial(AccountControl, sleep=sleep))
    rc, events, client, tracker, git = _run(tmp_path, monkeypatch, code="rate_limit_exceeded")
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert rc == 1 and len(client.created) == 1
    assert end["outcome"] == "operator_stop"
    assert end["account_error"]["kind"] == "rate_limited"


def test_rate_probe_cannot_treat_no_eligible_work_as_clean_completion(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))

    async def script(_session, tracker):
        tracker.issue_close(42, comment="Agent closed early")

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, code="rate_limit_exceeded", session_hook=script,
    )
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert rc == 1 and len(client.created) == 1
    assert end["outcome"] == "account_failure"
    assert end["account_cause"] == "no_eligible_probe"


def test_account_failed_lane_is_not_adverse_measured_route_evidence(tmp_path, monkeypatch, capsys):
    from tests.test_demotion import _measured, _seed_artifact

    _seed_artifact(tmp_path, {"bugfix": _measured("cheap", "low")})
    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True,
        config_overrides={
            "routing": {"bugfix": ("cheap", "low")}, "demotion_threshold": 1,
        },
    )
    assert rc == 1
    assert client.create_calls[0]["model"] == "cheap"
    assert "made no progress on" not in capsys.readouterr().err


def test_recovery_refusal_stops_further_recovery_and_cannot_publish_commits(tmp_path, monkeypatch):
    sends = 0

    async def script(session, _tracker):
        nonlocal sends
        sends += 1
        if sends == 1:
            session._scripted_events = []

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, session_hook=script, commit=True,
        gate=FakeGateRunner(outcomes=[False, True]),
    )
    assert rc == 1 and sends == 2
    assert tracker.issue_view(42).state == "OPEN"
    assert not [e for e in events if e["type"] in ("wrapper.strike", "wrapper.auto_close")]
    end = next(e for e in events if e["type"] == "wrapper.contribution.end")
    assert end["reason"] == "account_failure"
    assert end["published"] is False


def test_rate_refused_recovery_releases_issue_for_an_ordinary_probe(tmp_path, monkeypatch):
    sends, clock = 0, Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))

    async def script(session, _tracker):
        nonlocal sends
        sends += 1
        if sends == 1:
            session._scripted_events = []
        elif sends == 3:
            session._scripted_events = [_sdk_event(
                SessionEventType.SESSION_ERROR,
                SessionErrorData(error_type="APIError", error_code="insufficient_quota", message="Spent"),
            )]

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, session_hook=script, commit=True,
        code="rate_limit_exceeded", gate=FakeGateRunner(default=False),
    )
    assert rc == 1 and sends == 3
    assert clock.waits == [30]
    assert len([e for e in events if e["type"] == "wrapper.pickup.bound"]) == 2
    assert not [e for e in events if e["type"] == "wrapper.strike"]


@pytest.mark.parametrize("green", [False, True])
def test_quota_stops_new_recovery_while_unaffected_lane_drains(tmp_path, monkeypatch, green):
    arrived, both = 0, asyncio.Event()

    async def script(session, _tracker):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await both.wait()
        if session._working_directory.endswith("issue-43"):
            session._scripted_events = []

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, refs=(42, 43),
        session_hook=script, commit=True, gate=FakeGateRunner(default=green),
    )
    assert rc == 1 and arrived == 2
    assert not [e for e in events if e["type"] == "wrapper.integration.recovery_started"]
    assert not [e for e in events if e["type"] == "wrapper.strike"]
    assert tracker.issue_view(42).state == "OPEN"
    assert tracker.issue_view(43).state == ("CLOSED" if green else "OPEN")


def test_classifier_account_refusal_admits_no_following_classifier_or_worker(tmp_path, monkeypatch):
    from tests.test_iteration_end_to_end import (
        FakeCopilotClient, _cheap_staircase, _classifier_config, _wire_classifier_run,
    )

    _unused, tracker = _wire_classifier_run(tmp_path, monkeypatch)
    refusal = _sdk_event(SessionEventType.SESSION_ERROR, SessionErrorData(
        error_type="APIError", error_code="authentication_failed", message="Expired credentials",
    ))
    client = FakeCopilotClient(scripted_events=[refusal])
    monkeypatch.setattr(loop, "_make_client", lambda: client)
    rc = asyncio.run(loop.run(_classifier_config(), staircase=_cheap_staircase()))
    events = _logged_events(tmp_path)
    assert rc == 1 and len(client.created) == 1
    assert client.create_calls[0]["model"] == "gpt-5-mini"
    assert not tracker.applied
    assert not [e for e in events if e["type"] == "wrapper.strike"]
    assert next(e for e in events if e["type"] == "wrapper.run.end")["outcome"] == "account_failure"


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
def test_probe_needs_positive_harness_interaction_even_when_it_commits(
    tmp_path, monkeypatch, parallel, confirmed,
):
    clock = Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))
    sends = 0

    async def script(session, _tracker):
        nonlocal sends
        sends += 1
        if sends == 2:
            session._scripted_events = (
                [_sdk_event(SessionEventType.ASSISTANT_MESSAGE, AssistantMessageData(
                    message_id="m1", content="Implemented and checked",
                ))] if confirmed else []
            )

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, code="rate_limit_exceeded",
        maximum=2, session_hook=script, commit=True,
    )
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    if confirmed:
        assert rc == 0
        assert end["outcome"] == "iteration_cap"
        assert tracker.issue_view(42).state == "CLOSED"
    else:
        assert rc == 1
        assert end["account_cause"] == "probe_unconfirmed"
        assert tracker.issue_view(42).state == "OPEN"
    assert sends == 2
    assert not [e for e in events if e["type"] == "wrapper.strike"]
