"""Python adapter for the Runner-family Sandbox obligations (ADR-0093)."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from typing import cast

import pytest
from copilot import CopilotClient
from copilot.generated.session_events import PermissionRequestShell

from git_loopy.persist import EventLogWriter
from git_loopy.run_sandbox import resolve_run_sandbox, sandbox_state
from git_loopy.session import SandboxBreachError, SandboxError, SandboxPolicy
from tests.test_run_sandbox import Runtime
from tests.test_session_sandbox import FakeClient, agent, execution


SANDBOX = json.loads(
    (Path(__file__).parents[2] / "conformance/sandbox.json").read_text(encoding="utf-8")
)


def sandbox_cases(group):
    return [
        case for case in SANDBOX[group]
        if "python" in case.get("distributions", SANDBOX["distributions"])
    ]


@pytest.mark.parametrize("case", sandbox_cases("policy_cases"), ids=lambda case: case["id"])
async def test_policy_is_forwarded_before_first_prompt(tmp_path, case):
    settings = deepcopy(case["settings"])
    client = FakeClient()
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        async with agent(client, log, sandbox_policy=SandboxPolicy(
            settings,
            readwrite_paths=tuple(case["readwrite_paths"]),
            readonly_paths=tuple(case["readonly_paths"]),
        )) as session:
            await session.send_and_wait("work")
    assert client.transport.calls[0][1]["sandboxConfig"] == case["expected"]
    assert settings == case["settings"]
    assert client.session.disconnected


@pytest.mark.parametrize("case", sandbox_cases("permission_cases"), ids=lambda case: case["id"])
async def test_every_bypass_is_denied(tmp_path, case):
    client = FakeClient()
    path = tmp_path / "events.jsonl"
    with EventLogWriter(path) as log:
        async with agent(client, log, sandbox_policy=SandboxPolicy(
            {"allowBypass": case["allow_bypass"]},
        )):
            decision = client.options["on_permission_request"](PermissionRequestShell(
                can_offer_session_approval=True, commands=[], full_command_text="echo test",
                has_write_file_redirection=False, intention="test",
                possible_paths=[], possible_urls=[], tool_call_id=case["id"],
                request_sandbox_bypass=case["request_bypass"],
                request_sandbox_permissive=case["request_permissive"],
            ), {})
    assert decision.kind == case["expected"]["decision"]
    events = [
        event for line in path.read_text().splitlines()
        if (event := json.loads(line))["type"] == "tool.permission_denied"
    ]
    assert [event["reason"] for event in events] == case["expected"]["denials"]
    assert all(
        event["type"] == "tool.permission_denied" and event["tool_call_id"] == case["id"]
        for event in events
    )


@pytest.mark.parametrize("case", sandbox_cases("preflight_cases"), ids=lambda case: case["id"])
async def test_required_but_unenforceable_sandbox_is_refused(case, monkeypatch):
    runtime = Runtime(
        enabled=case["enabled"], managed=case["managed"], supported=case["supported"],
    )
    runtime.responses["session.sandbox.getEnforcementStatus"]["blocked"] = case["blocked"]
    if case["unavailable"] is not None:
        method = {
            "settings": "user.settings.get",
            "enforcement": "session.sandbox.getEnforcementStatus",
            "host_support": "sandbox.getHostSupport",
        }[case["unavailable"]]
        request = runtime.request

        async def unavailable(name, params, **kwargs):
            if name == method:
                raise RuntimeError("runtime unavailable")
            return await request(name, params, **kwargs)

        monkeypatch.setattr(runtime, "request", unavailable)
    # The transport fake implements only the SDK surface used by preflight.
    client = cast(CopilotClient, runtime)
    expected = case["expected"]
    if expected["refused"]:
        with pytest.raises(SandboxError, match=expected["error_contains"]):
            await resolve_run_sandbox(client, residence=case["residence"])
    else:
        settings = await resolve_run_sandbox(client, residence=case["residence"])
        assert sandbox_state(settings, residence=case["residence"]) == expected["state"]
        assert settings == (None if expected["state"] == "off" else runtime.settings)
        assert (
            "sandbox.getHostSupport" in [method for method, _ in runtime.calls]
        ) is expected["host_checked"]
    if case["unavailable"] != "settings":
        assert runtime.disconnected


@pytest.mark.parametrize("case", sandbox_cases("execution_cases"), ids=lambda case: case["id"])
async def test_tool_execution_requires_sandbox_attestation(tmp_path, case):
    client = FakeClient()
    client.session.tool_events = [execution(case["sandboxed"])]
    expected = case["expected"]
    with EventLogWriter(tmp_path / "events.jsonl") as log:
        async with asyncio.timeout(2):
            if expected["breach"]:
                client.session.wait_for_abort = True
                with pytest.raises(SandboxBreachError):
                    async with agent(client, log, sandbox_policy=SandboxPolicy({})) as session:
                        await session.send_and_wait("work")
            else:
                async with agent(client, log, sandbox_policy=SandboxPolicy({})) as session:
                    await session.send_and_wait("work")
    assert client.session.aborts == expected["aborts"]
    assert client.session.disconnected
