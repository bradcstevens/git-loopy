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
    _capture_parallel_loops, _logged_events, _wire_repo,
)


def _run(tmp_path, monkeypatch, *, parallel=False, code="insufficient_quota",
         commit=False, maximum=5, session_hook=None, refs=(42,), config_overrides=None,
         gate=None, client_type=_ParallelFakeClient, run_timeout=None):
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

    client = client_type(fake_git=git, scripted_events=[refusal])
    client._session_cls = Session
    monkeypatch.setattr(loop, "_make_git_client", lambda: git)
    monkeypatch.setattr(loop, "_make_github_client", lambda: tracker)
    monkeypatch.setattr(loop, "_make_client", lambda: client)
    monkeypatch.setattr(loop, "_make_gate_runner", lambda **kw: gate or FakeGateRunner())
    async def scenario():
        run = loop.run(RunConfig(max_iterations=maximum, **(config_overrides or {})))
        return await asyncio.wait_for(run, run_timeout) if run_timeout else await run

    rc = asyncio.run(scenario())
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


@pytest.mark.parametrize("parallel,salvage_failure", [(False, False), (True, False), (True, True)])
def test_a_close_before_refusal_is_diagnosed_not_reopened_or_verified(
    tmp_path, monkeypatch, parallel, salvage_failure,
):
    from git_loopy.git import GitError

    async def script(session, tracker):
        tracker.issue_close(42, comment="Agent closed early")
        if salvage_failure:
            target = session._fake_git.worktree_client(Path(session._working_directory))
            target.dirty = True
            target.commit_error = GitError(["commit"], 1, "fixture salvage failed")

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


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("commit", [False, True])
def test_recovered_call_error_cannot_mask_a_later_terminal_account_exception(
    tmp_path, monkeypatch, parallel, commit,
):
    class EndpointRefusal(Exception):
        code = "insufficient_quota"

    async def script(session, _tracker):
        session._on_event(_sdk_event(
            SessionEventType.MODEL_CALL_FAILURE,
            ModelCallFailureData(
                source=ModelCallFailureSource.TOP_LEVEL,
                error_type="NetworkError", error_message="network error",
            ),
        ))
        raise EndpointRefusal("Endpoint refused the session")

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, commit=commit, session_hook=script,
    )
    assert rc == 1 and len(client.created) == 1
    assert not [e for e in events if e["type"] in ("wrapper.strike", "wrapper.auto_close")]
    assert tracker.issue_view(42).state == "OPEN"
    assert git.push_calls == 0
    assert len([e for e in events if e["type"] == "wrapper.commit.recorded"]) == int(commit)


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


@pytest.mark.parametrize("parallel", [False, True])
def test_one_provider_minimum_is_not_charged_again_at_lane_finalization(tmp_path, monkeypatch, parallel):
    class RateRefusal(Exception):
        code = "rate_limit_exceeded"
        retry_after_seconds = 120

    sends, clock = 0, Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))

    async def script(_session, _tracker):
        nonlocal sends
        sends += 1
        if sends == 1:
            raise RateRefusal("Rate refused")

    class Client(_ParallelFakeClient):
        async def create_session(self, **kwargs):
            session = await super().create_session(**kwargs)

            async def disconnect():
                if len(self.created) == 1:
                    clock.now += 10

            session.disconnect = disconnect
            return session

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, session_hook=script, maximum=2,
        client_type=Client,
    )
    assert rc == 1 and sends == 2
    assert clock.waits == [110]
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert end["account_error"]["kind"] == "quota_exhausted"
    assert end["account_cause"] == "terminal_refusal"


@pytest.mark.parametrize("message,code,error_type,stops", [
    ("Monthly premium request quota exhausted", None, "APIError", True),
    ("Authentication failed: invalid credentials", None, "APIError", True),
    ("Request forbidden", "forbidden", "APIError", False),
    ("Permission denied opening /tmp/work", None, "APIError", False),
    ("Disk quota exceeded", None, "APIError", False),
    ("Disk quota exceeded", None, "DiskQuotaExceeded", False),
    ("Filesystem allocation failed", "disk_quota_exceeded", "APIError", False),
])
def test_terminal_evidence_must_identify_the_account_not_just_a_failure(
    tmp_path, monkeypatch, message, code, error_type, stops,
):
    async def script(session, _tracker):
        session._scripted_events = [_sdk_event(
            SessionEventType.SESSION_ERROR, SessionErrorData(
                error_type=error_type, message=message, error_code=code, status_code=403,
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


@pytest.mark.parametrize("repair_after", [1, 3])
def test_first_stop_drains_lane_through_bounded_recovery(tmp_path, monkeypatch, repair_after):
    built = _capture_parallel_loops(monkeypatch)
    gate = FakeGateRunner(default=False)
    sends = 0

    async def script(session, _tracker):
        nonlocal sends
        sends += 1
        session._scripted_events = []
        if sends == 1:
            built[0].request_stop_drain()
        elif sends == repair_after + 1:
            # A gate-only retry cannot repair the stage: a Recovery session
            # must actually run before the gate can turn green.
            gate._outcomes.append(True)

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, commit=True, session_hook=script, gate=gate,
    )
    assert len(client.created) == repair_after + 1
    assert sends == repair_after + 1
    assert len([e for e in events if e["type"] == "wrapper.integration.recovery_started"]) == repair_after
    (contribution,) = [e for e in events if e["type"] == "wrapper.contribution.end"]
    assert contribution["published"] is True
    assert contribution["reason"] != "account_failure"
    assert tracker.issue_view(42).state == "CLOSED"
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert rc == 1 and end["outcome"] == "operator_stop"
    assert "account_cause" not in end and "account_error" not in end
    assert not [e for e in events if e["type"] == "wrapper.account.state"]


def test_first_stop_after_lane_dispatch_still_creates_and_publishes_session(tmp_path, monkeypatch):
    built = _capture_parallel_loops(monkeypatch)
    real_policy = loop._Loop._agent_sandbox_policy

    def stop_before_session(self, issue_clone):
        assert self is built[0]._serial
        built[0].request_stop_drain()
        return real_policy(self, issue_clone)

    monkeypatch.setattr(loop._Loop, "_agent_sandbox_policy", stop_before_session)

    async def script(session, _tracker):
        session._scripted_events = []

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, commit=True, session_hook=script,
    )
    assert len(client.created) == 1
    assert len(client.created[0].send_and_wait_calls) == 1
    (contribution,) = [e for e in events if e["type"] == "wrapper.contribution.end"]
    assert contribution["published"] is True
    assert contribution["reason"] != "account_failure"
    assert tracker.issue_view(42).state == "CLOSED"
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert rc == 1 and end["outcome"] == "operator_stop"
    assert "account_cause" not in end and "account_error" not in end
    assert not [e for e in events if e["type"] == "wrapper.account.state"]


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


@pytest.mark.parametrize("work", ["committed", "dirty", "checkpoint_failed"])
def test_account_refused_recovery_preserves_repairs_before_stage_cleanup(tmp_path, monkeypatch, work):
    from git_loopy.git import GitError
    from git_loopy.wrapper import is_checkpoint_message

    sends, repair = 0, None

    async def script(session, _tracker):
        nonlocal sends, repair
        sends += 1
        if sends == 1:
            session._scripted_events = []
            return
        target = session._fake_git.worktree_client(Path(session._working_directory))
        repair = target.simulate_agent_commit(subject="fix: recovery-only repair")
        if work != "committed":
            target.dirty = True
        if work == "checkpoint_failed":
            target.commit_error = GitError(["commit"], 1, "fixture salvage refused")

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, session_hook=script, commit=True,
        gate=FakeGateRunner(default=False),
    )
    path, branch, _base = next(row for row in git.worktree_adds if "/integrate/" in row[1])
    assert rc == 1 and sends == 2
    assert branch not in git.branch_deletes
    tip = git.branch_tip(branch)
    if work == "dirty":
        assert is_checkpoint_message(tip.message)
    else:
        assert tip.sha == repair
    assert (path in git.active_worktrees) is (work == "checkpoint_failed")
    assert tracker.issue_view(42).state == "OPEN"
    assert not [e for e in events if e["type"] in ("wrapper.strike", "wrapper.auto_close")]


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


def test_account_stop_does_not_prevent_an_unaffected_gate_only_rerun(tmp_path, monkeypatch):
    from git_loopy.gate import GateResult, LoopFailure

    arrived, both = 0, asyncio.Event()

    async def script(session, _tracker):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await both.wait()
        if session._working_directory.endswith("issue-43"):
            session._scripted_events = []

    gate = FakeGateRunner(by_issue={43: [
        GateResult.red(
            ("test",), LoopFailure("test", "pytest", None, "", timed_out=True, timeout_seconds=10),
        ),
        GateResult.green(("test",)),
    ]})
    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=True, refs=(42, 43),
        session_hook=script, commit=True, gate=gate,
    )
    assert rc == 1 and arrived == 2
    assert len([path for path in gate.calls if path.name == "issue-43"]) == 2
    assert not [e for e in events if e["type"] == "wrapper.integration.recovery_started"]
    assert tracker.issue_view(42).state == "OPEN"
    assert tracker.issue_view(43).state == "CLOSED"


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
def test_probe_setup_failure_stops_unconfirmed_without_another_admission(tmp_path, monkeypatch, parallel):
    from types import SimpleNamespace

    from copilot.generated.rpc import OptionsApi, SandboxApi
    from git_loopy.run_sandbox import resolve_run_sandbox
    from tests.test_run_sandbox import Runtime

    class Client(_ParallelFakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.runtime = Runtime(enabled=True)
            self.rpc = self.runtime.rpc
            self.updates = 0

        async def request(self, method, params, **kwargs):
            if method == "session.options.update":
                self.updates += 1
                if self.updates > 1:
                    raise RuntimeError("Sandbox transport failed before send")
                return {"success": True}
            assert method == "session.sandbox.getEnforcementStatus"
            return {"blocked": False, "required": False}

        async def create_session(self, **kwargs):
            if kwargs.get("available_tools") == []:
                return await self.runtime.create_session(**kwargs)
            session = await super().create_session(**kwargs)
            session.rpc = SimpleNamespace(
                options=OptionsApi(self, session.session_id),
                sandbox=SandboxApi(self, session.session_id),
            )
            return session

    clock = Clock()
    monkeypatch.setattr(loop, "resolve_run_sandbox", resolve_run_sandbox)
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))
    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, parallel=parallel, code="rate_limit_exceeded",
        maximum=3, client_type=Client, run_timeout=5,
    )
    assert rc == 1 and len(client.created) == 2
    assert sum(len(session.send_and_wait_calls) for session in client.created) == 1
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert end["outcome"] == "account_failure"
    assert end["account_cause"] == "probe_unconfirmed"
    assert end["iterations_run"] == 2
    assert not [e for e in events if e["type"] == "wrapper.strike"]
    if parallel:
        contributions = [e for e in events if e["type"] == "wrapper.contribution.end"]
        assert contributions[-1]["reason"] == "account_failure"


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


@pytest.mark.parametrize("probe_work", ["checkpoint", "progress", "completion", "unverified"])
def test_account_health_does_not_authorize_old_close_bearing_commits(tmp_path, monkeypatch, probe_work):
    sends, clock = 0, Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))

    async def script(session, tracker):
        nonlocal sends
        sends += 1
        git = session._fake_git
        if sends == 1:
            git.simulate_agent_commit(subject="fix: incomplete work", body="Closes #42")
            return
        session._scripted_events = [_sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(message_id="healthy", content="Account interaction succeeded"),
        )]
        if probe_work == "checkpoint":
            git.dirty = True
        else:
            git.simulate_agent_commit(
                subject="fix: subsequent work",
                body="Closes #42" if probe_work in ("completion", "unverified") else "",
            )
        if probe_work == "unverified":
            def refuse_close(*args, **kwargs):
                raise RuntimeError("Tracker could not verify completion")
            tracker.issue_close = refuse_close

    rc, events, client, tracker, git = _run(
        tmp_path, monkeypatch, code="rate_limit_exceeded", maximum=2, session_hook=script,
    )
    assert rc == 0 and sends == 2
    assert any(e.get("cause") == "probe_succeeded" for e in events)
    verified = probe_work == "completion"
    assert git.push_calls == int(verified)
    assert tracker.issue_view(42).state == ("CLOSED" if verified else "OPEN")
    assert bool([e for e in events if e["type"] == "wrapper.auto_close"]) is verified


@pytest.mark.parametrize("code,materializes,later_rate", [
    ("insufficient_quota", True, False), ("rate_limit_exceeded", False, False),
    ("insufficient_quota", True, True),
])
def test_remote_account_evidence_survives_other_errors_and_failed_materialization(
    tmp_path, monkeypatch, code, materializes, later_rate,
):
    from dataclasses import replace

    from git_loopy.git import GitError
    from git_loopy.session_outcome import SessionError
    from tests.test_loop_parallel import _RemoteBranchExecutionHost, _wire_two_lane_rolling

    git, tracker, client, config = _wire_two_lane_rolling(tmp_path, monkeypatch)
    clock = Clock()
    monkeypatch.setattr(loop, "AccountControl", partial(
        AccountControl, clock=clock, sleep=clock.sleep,
    ))

    class Host(_RemoteBranchExecutionHost):
        calls = 0

        async def run_contribution(self, request):
            self.calls += 1
            outcome = await super().run_contribution(request)
            if not materializes:
                def unavailable(remote, ref):
                    raise GitError(["ls-remote"], 1, "transport unavailable")
                self.git.probe_remote_ref = unavailable
            terminal = {
                "ts": "2026-10-20T00:00:00Z", "type": "session.error",
                "error_type": "APIError", "error_code": code, "message": "Endpoint refused",
            }
            later = {
                "ts": "2026-10-20T00:00:01Z", "type": "model.call_failure",
                "error_type": "NetworkError", "message": "network error",
            }
            if later_rate:
                later = {
                    **terminal, "ts": "2026-10-20T00:00:01Z",
                    "error_code": "rate_limit_exceeded",
                }
            return replace(
                outcome, events=(terminal, later),
                ending=replace(outcome.ending, error=SessionError.from_event(later)),
            )

    host = Host(git)
    real_loop = loop._ParallelLoop
    monkeypatch.setattr(loop, "_ParallelLoop", lambda **kw: real_loop(
        **{**kw, "execution_host": host},
    ))
    rc = asyncio.run(loop.run(replace(config, max_iterations=1)))
    events = _logged_events(tmp_path)
    end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert rc == 1 and host.calls == 1
    assert end["iterations_run"] == 1
    assert end["account_cause"] == ("terminal_refusal" if materializes else "retry_prevented_by_cap")
    assert clock.waits == []
    assert not [e for e in events if e["type"] in ("wrapper.strike", "wrapper.auto_close")]
    assert all(e["reason"] == "account_failure" for e in events if e["type"] == "wrapper.contribution.end")
    assert tracker.issue_view(42).state == "OPEN"
