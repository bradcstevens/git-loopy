"""Local Run Sandbox preflight at the pinned runtime RPC boundary (#863)."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from copilot.generated.rpc import ServerRpc, SandboxApi

from git_loopy.run_sandbox import resolve_run_sandbox
from git_loopy.session import SandboxError


class Runtime:
    def __init__(self, *, enabled=False, managed=False, supported=True):
        self.calls = []
        self.disconnected = False
        self.settings = {
            "enabled": enabled,
            "allowBypass": True,
            "auth": {"git": True},
            "userPolicy": {"filesystem": {"readonlyPaths": ["/operator/hooks"]}},
        }
        self.responses = {
            "user.settings.get": {"settings": {"sandbox": {
                "value": self.settings, "default": {}, "isDefault": False,
            }}},
            "session.sandbox.getEnforcementStatus": {
                "required": managed, "blocked": False,
            },
            "sandbox.getHostSupport": {
                "supported": supported, "capabilities": [],
                **({} if supported else {"reason": "bubblewrap is missing"}),
            },
        }
        self.rpc = ServerRpc(self)

    async def request(self, method, params, **kwargs):
        self.calls.append((method, deepcopy(params)))
        response = self.responses[method]
        if isinstance(response, Exception):
            raise response
        return response

    async def create_session(self, **kwargs):
        assert kwargs["available_tools"] == []
        assert kwargs["memory"] == {"enabled": False}
        return SimpleNamespace(
            rpc=SimpleNamespace(sandbox=SandboxApi(self, "preflight")),
            disconnect=self.disconnect,
        )

    async def disconnect(self):
        self.disconnected = True


@pytest.mark.parametrize(
    ("enabled", "managed", "required"),
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
async def test_local_run_follows_user_or_managed_sandbox(enabled, managed, required):
    runtime = Runtime(enabled=enabled, managed=managed)
    result = await resolve_run_sandbox(runtime)
    assert (result is not None) is required
    assert runtime.disconnected
    assert ("sandbox.getHostSupport" in [call[0] for call in runtime.calls]) is required
    if required:
        assert result == runtime.settings


async def test_required_unsupported_sandbox_refuses_with_host_remedy():
    runtime = Runtime(managed=True, supported=False)
    with pytest.raises(SandboxError, match="bubblewrap is missing.*Copilot.*sandbox"):
        await resolve_run_sandbox(runtime)
    assert runtime.disconnected


async def test_disabled_sandbox_does_not_require_host_support():
    runtime = Runtime(enabled=False, supported=False)
    assert await resolve_run_sandbox(runtime) is None
    assert all(method != "sandbox.getHostSupport" for method, _ in runtime.calls)


async def test_remote_sandbox_is_required_even_when_operator_disables_it():
    runtime = Runtime(enabled=False)
    assert await resolve_run_sandbox(runtime, residence="remote") == runtime.settings
    with pytest.raises(SandboxError, match="Required Sandbox is unavailable"):
        await resolve_run_sandbox(Runtime(enabled=False, supported=False), residence="remote")


@pytest.mark.parametrize("method", [
    "user.settings.get", "session.sandbox.getEnforcementStatus", "sandbox.getHostSupport",
])
async def test_unknown_policy_or_host_support_is_never_an_opt_out(method):
    runtime = Runtime(enabled=True)
    runtime.responses[method] = RuntimeError("runtime unavailable")
    with pytest.raises(SandboxError, match="Could not determine.*Sandbox"):
        await resolve_run_sandbox(runtime)
    if method != "user.settings.get":
        assert runtime.disconnected


async def test_blocked_managed_enforcement_refuses_even_when_backend_is_available():
    runtime = Runtime()
    runtime.responses["session.sandbox.getEnforcementStatus"]["blocked"] = True
    with pytest.raises(SandboxError, match="managed Sandbox enforcement is blocked"):
        await resolve_run_sandbox(runtime)
