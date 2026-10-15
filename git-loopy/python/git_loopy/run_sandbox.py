"""Resolve the local Run's Copilot Sandbox requirement before any Agent (#863)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from copilot import CopilotClient
from copilot.generated.rpc import PermissionDecisionReject

from .session import SandboxError


async def resolve_run_sandbox(client: CopilotClient) -> dict[str, Any] | None:
    """Read the runtime's user policy and effective managed requirement.

    A prompt-free, tool-free management session asks the same enforcement
    resolver as an Agent, including policy-helper policy (which the server's
    ``managedSettings.resolve`` deliberately does not run). It performs no work.
    Unknown policy or host support is a refusal, never an opt-out.
    """
    try:
        settings = await client.rpc.user.settings.get(timeout=30)
        sandbox = settings.settings["sandbox"].value
        if sandbox is None:
            sandbox = {}
        if not isinstance(sandbox, dict) or not isinstance(sandbox.get("enabled", False), bool):
            raise ValueError("sandbox.enabled must be a boolean")
        session = await client.create_session(
            on_permission_request=lambda *_: PermissionDecisionReject(),
            available_tools=[],
            memory={"enabled": False},
            enable_session_store=False,
        )
        try:
            enforcement = await session.rpc.sandbox.get_enforcement_status(timeout=30)
        finally:
            await session.disconnect()
        required = sandbox.get("enabled", False) or enforcement.required
        if not required and not enforcement.blocked:
            return None
        support = await client.rpc.sandbox.get_host_support(timeout=30)
        if not support.supported:
            raise SandboxError(
                f"Required Sandbox is unavailable: {support.reason or 'unsupported host'}. "
                "Install the Copilot sandbox prerequisites on a supported host and retry; "
                "the Run will not start unsandboxed."
            )
        if enforcement.blocked:
            raise SandboxError(
                "Copilot managed Sandbox enforcement is blocked. Resolve the managed "
                "policy or host prerequisites with your administrator before retrying."
            )
        return deepcopy(sandbox)
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError(
            "Could not determine the effective Copilot Sandbox policy or host support. "
            "Check Copilot settings, managed policy and runtime availability before retrying."
        ) from exc
