"""Sandbox obligations at the Agent-session / pinned SDK boundary (ADR-0093)."""

from copy import deepcopy
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from copilot.generated.rpc import OptionsApi, SandboxApi
from copilot.generated.session_events import (
    PermissionRequestShell, SessionEvent, SessionEventType, ToolExecutionCompleteData,
)

from git_loopy.persist import EventLogWriter
from git_loopy.session import IterationSession, SandboxError, SandboxPolicy
from git_loopy.sinks import SinkFanout


class FakeTransport:
    """Exercise the real SDK serializers without starting a CLI or model."""

    def __init__(self):
        self.calls = []
        self.responses = {
            "session.options.update": {"success": True},
            "session.sandbox.getEnforcementStatus": {"blocked": False, "required": False},
        }

    async def request(self, method, params, **kwargs):
        self.calls.append((method, deepcopy(params)))
        response = self.responses[method]
        if isinstance(response, BaseException):
            raise response
        return response


class FakeSession:
    def __init__(self, transport):
        self.rpc = SimpleNamespace(
            options=OptionsApi(transport, "agent"),
            sandbox=SandboxApi(transport, "agent"),
        )
        self.transport = transport
        self.disconnected = False
        self.prompts = []
        self.tool_events = []
        self.aborts = 0
        self.abort_error = None
        self.wait_for_abort = False
        self.finished = asyncio.Event()

    async def send_and_wait(self, prompt):
        assert [method for method, _ in self.transport.calls] == [
            "session.options.update", "session.sandbox.getEnforcementStatus",
        ], "Sandbox must be applied and checked before the first prompt"
        self.prompts.append(prompt)
        for event in self.tool_events:
            # Like the real SDK dispatcher, callback exceptions are swallowed.
            try:
                self.on_event(event)
            except Exception:
                pass
        if self.wait_for_abort:
            await self.finished.wait()

    async def abort(self):
        self.aborts += 1
        self.finished.set()
        if self.abort_error:
            raise self.abort_error

    async def disconnect(self):
        self.disconnected = True


class FakeClient:
    def __init__(self):
        self.transport = FakeTransport()
        self.session = FakeSession(self.transport)

    async def create_session(self, **kwargs):
        self.options = kwargs
        self.session.on_event = kwargs["on_event"]
        return self.session


def agent(client, log, **kwargs):
    return IterationSession(
        client, config=SimpleNamespace(deny_tools=frozenset(), deny_skills=frozenset()),
        event_log=log, sinks=SinkFanout(()), run_id="sandbox-run", iter_num=1,
        **kwargs,
    )


async def test_forwarded_policy_and_only_callers_grants_precede_first_prompt(tmp_path):
    settings = {
        "enabled": False,
        "allowBypass": True,
        "addCurrentWorkingDirectory": False,
        "futureOperatorOption": {"preserve": [1, 2]},
        "auth": {"git": True, "gh": False},
        "userPolicy": {
            "network": {"allowedHosts": ["github.com"]},
            "filesystem": {"readonlyPaths": ["/operator/hooks"], "deniedPaths": ["/secrets"]},
        },
    }
    original = deepcopy(settings)
    client = FakeClient()
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        async with agent(
            client, log,
            sandbox_policy=SandboxPolicy(
                settings, readwrite_paths=("/issue",),
                readonly_paths=("/source/objects", "/operator/.gitconfig"),
            ),
        ) as session:
            await session.send_and_wait("work")
    assert client.transport.calls[0] == ("session.options.update", {
        "sessionId": "agent",
        "sandboxConfig": {
            "enabled": True, "allowBypass": False, "addCurrentWorkingDirectory": False,
            "futureOperatorOption": {"preserve": [1, 2]},
            "auth": {"git": True, "gh": False},
            "userPolicy": {
                "network": {"allowedHosts": ["github.com"]},
                "filesystem": {
                    "readonlyPaths": ["/operator/hooks", "/source/objects", "/operator/.gitconfig"],
                    "readwritePaths": ["/issue"],
                    "deniedPaths": ["/secrets"],
                },
            },
        },
    })
    assert settings == original
    assert client.session.disconnected


@pytest.mark.parametrize("flag", ["request_sandbox_bypass", "request_sandbox_permissive"])
async def test_sandbox_escalations_are_denied_and_recorded(tmp_path, flag):
    client = FakeClient()
    path = tmp_path / "events.jsonl"
    with EventLogWriter(path) as log:
        async with agent(client, log, sandbox_policy=SandboxPolicy({})):
            request = PermissionRequestShell(
                can_offer_session_approval=True, commands=[], full_command_text="echo test",
                has_write_file_redirection=False, intention="test",
                possible_paths=[], possible_urls=[], tool_call_id="escalation",
                **{flag: True},
            )
            decision = client.options["on_permission_request"](request, {})
            assert decision.kind == "reject"
    denied = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(e["type"], e["reason"], e["tool_call_id"]) for e in denied] == [
        ("tool.permission_denied", "sandbox_escalation_denied", "escalation"),
    ]


def execution(sandboxed):
    return SessionEvent(
        id="execution", timestamp=datetime.now(timezone.utc), parent_id=None,
        type=SessionEventType.TOOL_EXECUTION_COMPLETE,
        data=ToolExecutionCompleteData(
            success=True, tool_call_id="tool", sandboxed=sandboxed,
            parent_tool_call_id="subagent",
        ),
    )


@pytest.mark.parametrize("sandboxed", [None, False])
@pytest.mark.parametrize("abort_fails", [False, True])
async def test_unsandboxed_execution_aborts_agent_with_distinct_failure(
    tmp_path, sandboxed, abort_fails,
):
    from git_loopy.session import SandboxBreachError

    client = FakeClient()
    client.session.tool_events = [execution(sandboxed), execution(sandboxed)]
    client.session.wait_for_abort = True
    if abort_fails:
        client.session.abort_error = RuntimeError("transport failed")
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        with pytest.raises(SandboxBreachError, match="tool"):
            async with asyncio.timeout(2):
                async with agent(client, log, sandbox_policy=SandboxPolicy({})) as session:
                    await session.send_and_wait("work")
                    pytest.fail("The Agent must not continue after a Sandbox breach")
    assert client.session.aborts == 1
    assert client.session.disconnected


async def test_sandboxed_execution_can_finish(tmp_path):
    client = FakeClient()
    client.session.tool_events = [execution(True)]
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        async with agent(client, log, sandbox_policy=SandboxPolicy({})) as session:
            await session.send_and_wait("work")
    assert client.session.aborts == 0


@pytest.mark.parametrize(("method", "response"), [
    ("session.options.update", RuntimeError("update failed")),
    ("session.options.update", {"success": False}),
    ("session.sandbox.getEnforcementStatus", RuntimeError("read failed")),
    ("session.sandbox.getEnforcementStatus", {}),
    ("session.sandbox.getEnforcementStatus", {"blocked": True, "required": True}),
])
async def test_application_or_enforcement_failure_never_exposes_session(
    tmp_path, method, response,
):
    client = FakeClient()
    client.transport.responses[method] = response
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        with pytest.raises(SandboxError):
            async with agent(client, log, sandbox_policy=SandboxPolicy({})):
                pytest.fail("A failed Sandbox must not expose the Agent session")
    assert client.session.prompts == []
    assert client.session.disconnected


async def test_cancellation_during_sandbox_setup_disconnects_session(tmp_path):
    client = FakeClient()
    client.transport.responses["session.options.update"] = asyncio.CancelledError()
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        with pytest.raises(asyncio.CancelledError):
            async with agent(client, log, sandbox_policy=SandboxPolicy({})):
                pytest.fail("Cancelled Sandbox setup must not expose the Agent session")
    assert client.session.disconnected


async def test_no_policy_does_not_touch_sandbox_or_watch_tool_execution(tmp_path):
    client = FakeClient()
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        async with agent(client, log):
            client.options["on_event"](execution(None))
            await asyncio.sleep(0)
    assert client.transport.calls == []
    assert client.session.aborts == 0
    assert client.session.disconnected
