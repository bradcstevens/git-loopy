"""Remote Run launch through its provider seam, without Azure credentials."""

import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from git_loopy.run_residence import RunLaunch
from tests.test_azure_target import (
    FakeAzureTransport, JsonResponse, OfflineCredential, TARGET,
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE,
    ).strip()


@pytest.fixture
def repository(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "config", "user.name", "Remote test")
    git(origin, "config", "user.email", "remote@example.test")
    (origin / ".devcontainer").mkdir()
    (origin / ".devcontainer" / "devcontainer.json").write_text(
        '{"image":"mcr.microsoft.com/azurelinux/base/core:3.0"}'
    )
    git(origin, "add", ".")
    git(origin, "commit", "-m", "base")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), str(checkout))
    git(checkout, "config", "user.name", "Remote test")
    git(checkout, "config", "user.email", "remote@example.test")
    git(checkout, "remote", "set-url", "origin", "https://github.com/example/project.git")
    git(checkout, "config", f"url.{origin}.insteadOf", "https://github.com/example/project.git")
    return checkout


class FakeProvider:
    def __init__(self):
        self.launches = []

    def launch(self, plan):
        self.launches.append(plan)
        return "fake-microvm"


def test_ahead_default_branch_refuses_before_creating_microvm(repository):
    from git_loopy.remote_launch import RemoteLaunchError, launch_remote

    (repository / "unpublished").write_text("not on origin")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "unpublished")
    provider = FakeProvider()
    with pytest.raises(RemoteLaunchError, match="ahead of origin"):
        launch_remote(
            RunLaunch(repository, ()), provider=provider, sandbox_settings={},
            env={}, output=lambda _: None,
        )
    assert provider.launches == []


def test_launch_forwards_only_run_inputs_and_sandbox_policy(repository, tmp_path):
    from git_loopy.remote_launch import launch_remote
    from git_loopy.run_sidecar import decode_detached_run_spec

    provider = FakeProvider()
    output = []
    policy = {"enabled": False, "allowBypass": True, "allowDevToolAccess": False}
    launch_remote(
        RunLaunch(repository, ("7", "--execution-host", "local")),
        provider=provider, sandbox_settings=policy,
        env={
            "HOME": str(tmp_path / "isolated"),
            "GH_TOKEN": "SECRET_GITHUB",
            "AZURE_CLIENT_SECRET": "SECRET_AZURE",
            "OTEL_EXPORTER_OTLP_HEADERS": "SECRET_TELEMETRY",
        },
        output=output.append,
    )
    assert len(provider.launches) == 1
    plan = provider.launches[0]
    document = json.loads(plan.guest_payload())
    assert document["sandbox"] == policy
    assert document["residence"] == "remote"
    assert document["origin"] == "https://github.com/example/project.git"
    config = decode_detached_run_spec(document["run_spec"]).config
    assert config.max_iterations == 7
    assert config.execution_host == "local"
    assert "SECRET_" not in plan.guest_payload()
    assert any("metered to the Azure subscription" in line for line in output)
    assert not any("estimate" in line.lower() or "$" in line for line in output)


@pytest.mark.parametrize("refusal", ["devcontainer", "github-actions"])
def test_other_launch_refusals_precede_any_microvm(repository, refusal):
    from git_loopy.remote_launch import RemoteLaunchError, launch_remote

    arguments = ()
    if refusal == "devcontainer":
        (repository / ".devcontainer" / "devcontainer.json").unlink()
    else:
        arguments = ("--execution-host", "github-actions")
    provider = FakeProvider()
    with pytest.raises(RemoteLaunchError, match=refusal):
        launch_remote(
            RunLaunch(repository, arguments), provider=provider, sandbox_settings={},
            env={}, output=lambda _: None,
        )
    assert provider.launches == []


class LaunchTransport(FakeAzureTransport):
    def __init__(self):
        super().__init__()
        self.created = None
        self.guest_files = []
        self.commands = []
        self.deleted = []

    def send(self, request, **kwargs):
        path = urlsplit(request.url).path
        if request.method == "PUT" and path.endswith("/diskimages"):
            return JsonResponse(request, {"id": "disk-one", "status": {"state": "Ready"}}, 200)
        if request.method == "PUT" and path.endswith("/sandboxes"):
            self.created = json.loads(request.body)
            return JsonResponse(request, {"id": "vm-one", "state": "Running"}, 201)
        if request.method == "GET" and path.endswith("/sandboxes/vm-one"):
            return JsonResponse(request, {
                "id": "vm-one", "state": "Running", "lifecycle": self.created["lifecycle"],
            }, 200)
        if request.method == "PUT" and path.endswith("/files"):
            self.guest_files.append(request.body)
            return JsonResponse(request, {}, 200)
        if request.method == "POST" and path.endswith("/executeShellCommand"):
            self.commands.append(json.loads(request.body))
            return JsonResponse(request, {
                "exitCode": 0,
                "stdout": '{"pid": 42, "detached": true, "sandbox": "remote"}',
                "stderr": "",
            }, 200)
        if request.method == "DELETE":
            self.deleted.append(path)
            return JsonResponse(request, {}, 200)
        return super().send(request, **kwargs)


def test_real_provider_creates_with_suspend_disabled_and_no_guest_credential(repository):
    from git_loopy.azure_remote import AzureRemoteProvider
    from git_loopy.remote_image import RemoteImage
    from git_loopy.remote_launch import launch_remote

    @contextmanager
    def image(plan, **kwargs):
        yield RemoteImage("example.azurecr.io/image:pinned", "registry-user", "SECRET_REGISTRY")

    transport = LaunchTransport()
    provider = AzureRemoteProvider(
        {"remote": {**asdict(TARGET), "registry": "example"}},
        {"GH_TOKEN": "SECRET_HOST", "AZURE_CLIENT_SECRET": "SECRET_AZURE"},
        credential=OfflineCredential(), transport=transport, image_builder=image,
    )
    assert launch_remote(
        RunLaunch(repository, ()), provider=provider,
        sandbox_settings={"enabled": False}, env={}, output=lambda _: None,
    ) == "vm-one"
    assert transport.created["lifecycle"]["autoSuspendPolicy"]["enabled"] is False
    assert transport.created["lifecycle"]["autoDeletePolicy"]["enabled"] is False
    assert transport.created["skipEgressProxy"] is False
    assert transport.deleted == []
    assert len(transport.commands) == 1
    guest_inputs = json.dumps({
        "environment": transport.created.get("environment", {}),
        "files": [value.decode() if isinstance(value, bytes) else value for value in transport.guest_files],
        "argv": transport.commands,
    })
    assert "SECRET_" not in guest_inputs
    assert "offline-azure-credential" not in guest_inputs
    assert "registry-user" not in guest_inputs
    from git_loopy.git import SubprocessGitClient
    from git_loopy.remote_runs import remote_run_receipts

    receipt, = remote_run_receipts(SubprocessGitClient(repository))
    assert receipt.run_id == transport.created["labels"]["git-loopy-launch"]
    assert receipt.target == TARGET
    assert receipt.scope == repository


def test_fake_provider_worker_survives_its_launching_client_exiting(tmp_path):
    """A separate fake execution client uses the production detach operation."""
    marker = tmp_path / "after-client-exit.json"
    worker = (
        "import json, os, time; from pathlib import Path; time.sleep(.4); "
        f"Path({str(marker)!r}).write_text(json.dumps("
        "{'pid': os.getpid(), 'sid': os.getsid(0), 'parent': os.getppid()}))"
    )
    parent_code = (
        "import os, sys; from pathlib import Path; "
        "from git_loopy.remote_guest import detach_remote_worker; "
        f"print(detach_remote_worker([sys.executable, '-c', {worker!r}], "
        f"directory=Path({str(tmp_path)!r}), env=os.environ, "
        f"log=Path({str(tmp_path / 'worker.log')!r})))"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True,
    )
    output, _ = parent.communicate(timeout=10)
    assert parent.returncode == 0
    pid = int(output.strip())
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        evidence = json.loads(marker.read_text())
        assert evidence["pid"] == pid
        assert evidence["sid"] == pid
        assert evidence["parent"] != parent.pid
    finally:
        if not marker.exists():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_remote_guest_cannot_turn_off_required_sandbox(repository, monkeypatch):
    from git_loopy import cli
    from git_loopy.remote_guest import run_guest
    from git_loopy.remote_launch import launch_remote
    from git_loopy.run_sandbox import resolve_run_sandbox, sandbox_state
    from git_loopy.run_start_disclosures import run_start_disclosures
    from tests.test_run_sandbox import Runtime

    provider = FakeProvider()
    launch_remote(
        RunLaunch(repository, ()), provider=provider,
        sandbox_settings={"enabled": False}, env={}, output=lambda _: None,
    )
    observed = []

    async def drive(config, *, residence, run_id, started_at):
        resolved = await resolve_run_sandbox(Runtime(enabled=False), residence=residence)
        observed.append(sandbox_state(resolved, residence=residence))
        return 0

    monkeypatch.setattr(cli, "_drive_line_printer", drive)
    assert run_guest(json.loads(provider.launches[0].guest_payload())) == 0
    assert observed == ["remote"]
    disclosure = run_start_disclosures(
        host_placement="local", repository_visibility="PUBLIC", inside_ci=False,
        host_yield="unsupported", remote_residence=True,
    )["host_metering"]
    assert disclosure["state"] == "metered"
    assert "Azure subscription" in disclosure["message"]


def test_image_build_uses_committed_toolchain_and_preserves_corporate_feeds(
    repository, monkeypatch,
):
    from git_loopy.remote_image import PackageFeeds, build_remote_image
    from git_loopy.remote_launch import launch_remote

    provider = FakeProvider()
    launch_remote(
        RunLaunch(repository, ()), provider=provider,
        sandbox_settings={}, env={}, output=lambda _: None,
    )
    plan = provider.launches[0]
    (repository / ".env").write_text("SECRET_UNTRACKED")
    monkeypatch.setenv("GH_TOKEN", "SECRET_HOST")
    calls = []
    original = subprocess.run

    def tools(argv, **kwargs):
        if argv[0] == "git":
            return original(argv, **kwargs)
        calls.append(argv)
        env = kwargs.get("env")
        if argv[0] in {"devcontainer", "docker"}:
            assert "SECRET_" not in json.dumps(dict(env))
            assert env["UV_DEFAULT_INDEX"] == "https://packagefeedproxy.microsoft.io/pypi/simple/"
            assert env["npm_config_registry"] == "https://packagefeedproxy.microsoft.io/npm/"
            assert Path(env["HOME"], ".nuget/NuGet/NuGet.Config").is_file()
        if argv[0] == "devcontainer":
            workspace = Path(argv[argv.index("--workspace-folder") + 1])
            assert (workspace / ".devcontainer/devcontainer.json").exists()
            assert not (workspace / ".env").exists()
        if argv[:2] == ["docker", "build"]:
            layer = Path(argv[-1])
            with zipfile.ZipFile(next(layer.glob("*.whl"))) as wheel:
                assert wheel.read("git_loopy/VERSION").decode().strip() == plan.version
                assert "git_loopy/remote_guest.py" in wheel.namelist()
                assert "git_loopy/remote_control.py" in wheel.namelist()
            script = (layer / "prerequisites.sh").read_text()
            for requirement in ("slirp4netns", "libslirp", "bubblewrap", "iptables", "extended/x86_64"):
                assert requirement in script
        output = ""
        if argv[:3] == ["az", "acr", "login"]:
            output = json.dumps({"accessToken": "SECRET_REGISTRY", "loginServer": "example.azurecr.io"})
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", tools)
    with build_remote_image(
        plan, registry="example", subscription=TARGET.subscription,
        feeds=PackageFeeds.from_environment({}),
    ) as image:
        assert image.registry_token == "SECRET_REGISTRY"
        assert "SECRET_REGISTRY" not in repr(image)
    assert any(call[:4] == ["az", "acr", "repository", "delete"] for call in calls)
    assert sum(call[:3] == ["docker", "image", "rm"] for call in calls) == 2


def test_public_remote_command_uses_remote_residence_without_changing_plain_runs(
    repository, monkeypatch, tmp_path,
):
    from git_loopy import azure_remote, cli, remote_launch
    from git_loopy.run_discovery import RunTargetError
    from git_loopy.run_residence import residence_provider

    class Provider(FakeProvider):
        def launch(self, plan):
            assert residence_provider("remote").name == "remote"
            return super().launch(plan)

    provider = Provider()

    async def policy():
        return {"enabled": False}

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "isolated-config"))
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: repository)
    monkeypatch.setattr(azure_remote, "AzureRemoteProvider", lambda *args: provider)
    monkeypatch.setattr(remote_launch, "read_operator_sandbox", policy)
    assert cli.main(["remote", "4", "--execution-host", "local"]) == 0
    assert provider.launches[0].config.max_iterations == 4
    assert residence_provider().name == "local"
    with pytest.raises(RunTargetError, match="not registered"):
        residence_provider("remote")


@pytest.mark.parametrize("failure", ["suspend-enabled", "unconfirmed-launch"])
def test_provider_retains_possibly_running_vm_but_reclaims_prelaunch_failure(
    repository, failure,
):
    from git_loopy.azure_remote import AzureRemoteProvider
    from git_loopy.remote_image import RemoteImage
    from git_loopy.remote_launch import RemoteLaunchError, launch_remote

    class FailingTransport(LaunchTransport):
        def send(self, request, **kwargs):
            path = urlsplit(request.url).path
            if failure == "suspend-enabled" and path.endswith("/sandboxes/vm-one"):
                if request.method == "GET":
                    return JsonResponse(request, {
                        "id": "vm-one", "state": "Running",
                        "lifecycle": {"autoSuspendPolicy": {"enabled": True}},
                    }, 200)
            if failure == "unconfirmed-launch" and path.endswith("/executeShellCommand"):
                return JsonResponse(request, {"error": {"message": "SECRET_RESPONSE"}}, 503)
            return super().send(request, **kwargs)

    @contextmanager
    def image(plan, **kwargs):
        yield RemoteImage("example.azurecr.io/image:pinned", "registry-user", "SECRET_REGISTRY")

    transport = FailingTransport()
    provider = AzureRemoteProvider(
        {"remote": {**asdict(TARGET), "registry": "example"}}, {},
        credential=OfflineCredential(), transport=transport, image_builder=image,
    )
    with pytest.raises(RemoteLaunchError) as error:
        launch_remote(
            RunLaunch(repository, ()), provider=provider,
            sandbox_settings={}, env={}, output=lambda _: None,
        )
    assert "SECRET_" not in str(error.value)
    if failure == "suspend-enabled":
        assert len(transport.deleted) == 2
        assert transport.commands == []
    else:
        assert transport.deleted == []
        assert "retained" in str(error.value)
        from git_loopy.git import SubprocessGitClient
        from git_loopy.remote_runs import remote_run_receipts

        receipt, = remote_run_receipts(SubprocessGitClient(repository))
        assert receipt.run_id == transport.created["labels"]["git-loopy-launch"]


def test_forwarded_policy_is_saved_at_the_runtime_settings_path(tmp_path, monkeypatch):
    from git_loopy.copilot_client import resolve_copilot_base_directory
    from git_loopy.remote_guest import configure_guest_home
    from git_loopy.remote_image import PackageFeeds

    monkeypatch.setenv("GH_TOKEN", "SECRET_HOST")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "SECRET_AZURE")
    policy = {"enabled": False, "allowBypass": True, "auth": {"git": True, "gh": True}}
    env = configure_guest_home(
        tmp_path / "guest", sandbox=policy, feeds=PackageFeeds.from_environment({}).environment(),
    )
    settings = resolve_copilot_base_directory(env) / "settings.json"
    assert json.loads(settings.read_text()) == {"sandbox": policy}
    assert not (settings.parent / "config.json").exists()
    assert "SECRET_" not in json.dumps(env)
    assert env["COPILOT_SKIP_CLI_DOWNLOAD"] == "1"
    assert env["PIP_INDEX_URL"] == "https://packagefeedproxy.microsoft.io/pypi/simple/"
    assert env["npm_config_registry"] == "https://packagefeedproxy.microsoft.io/npm/"


def test_failed_image_cleanup_still_reclaims_the_imported_disk(repository):
    from git_loopy.azure_remote import AzureRemoteProvider
    from git_loopy.remote_image import RemoteImage
    from git_loopy.remote_launch import RemoteLaunchError, launch_remote

    @contextmanager
    def image(plan, **kwargs):
        yield RemoteImage("example.azurecr.io/image:pinned", "registry-user", "SECRET_REGISTRY")
        raise RemoteLaunchError("Registry cleanup failed.")

    transport = LaunchTransport()
    provider = AzureRemoteProvider(
        {"remote": {**asdict(TARGET), "registry": "example"}}, {},
        credential=OfflineCredential(), transport=transport, image_builder=image,
    )
    with pytest.raises(RemoteLaunchError, match="Registry cleanup"):
        launch_remote(
            RunLaunch(repository, ()), provider=provider,
            sandbox_settings={}, env={}, output=lambda _: None,
        )
    assert transport.created is None
    assert len(transport.deleted) == 1
    assert transport.deleted[0].endswith("/diskimages/disk-one")
