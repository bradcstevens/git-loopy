"""Credential-free guest bootstrap and detached remote Orchestrator."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from git_loopy.remote_launch import RemoteLaunchError
from git_loopy.run_sidecar import decode_detached_run_spec, popen_detached


def guest_environment(home: Path, feeds: Mapping[str, str]) -> dict[str, str]:
    """No launching environment is copied; auth values are public placeholders."""
    env = {
        "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
        "COPILOT_HOME": str(home / ".copilot"),
        "PATH": "/opt/git-loopy/bin:/opt/copilot/bin:/usr/local/bin:/usr/bin:/bin",
        "COPILOT_CLI_PATH": "/opt/copilot/bin/copilot",
        "COPILOT_SKIP_CLI_DOWNLOAD": "1",
        "GH_TOKEN": "git-loopy-egress-proxy-placeholder",
        "COPILOT_GITHUB_TOKEN": "git-loopy-egress-proxy-placeholder",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key in ("UV_DEFAULT_INDEX", "PIP_INDEX_URL", "npm_config_registry", "NUGET_SOURCE"):
        env[key] = feeds[key]
    env.update(PIP_CONFIG_FILE="/dev/null", PIP_EXTRA_INDEX_URL="", UV_NO_CONFIG="true")
    # The platform's proxy transport/CA, never the client's environment.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        value = os.environ.get(key)
        if value:
            parsed = urlsplit(value)
            if parsed.username or parsed.password:
                raise RemoteLaunchError("The guest proxy transport must not contain credentials.")
            env[key] = value
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def configure_guest_home(
    home: Path, *, sandbox: Mapping[str, object], feeds: Mapping[str, str],
) -> dict[str, str]:
    from git_loopy.copilot_client import resolve_copilot_base_directory
    from git_loopy.remote_image import PackageFeeds

    home.mkdir(parents=True, exist_ok=True)
    PackageFeeds.from_environment(feeds).write_home(home)
    env = guest_environment(home, feeds)
    copilot = resolve_copilot_base_directory(env)
    copilot.mkdir(exist_ok=True)
    (copilot / "settings.json").write_text(json.dumps({"sandbox": dict(sandbox)}))
    return env


def detach_remote_worker(
    command: list[str], *, directory: Path, env: Mapping[str, str], log: Path,
) -> int:
    """The execution request owns no child pipe, session or parent-death signal."""
    with log.open("ab", buffering=0) as output:
        child = popen_detached(
            command, cwd=directory, env=dict(env), stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, close_fds=True,
        )
    time.sleep(0.1)
    if child.poll() is not None:
        raise RemoteLaunchError("The remote Orchestrator exited during launch; inspect its guest log.")
    return child.pid


def prepare_guest(document: dict, *, state: Path) -> int:
    """Initialize TUN after boot, clone origin, then leave the client behind."""
    state.mkdir(parents=True, exist_ok=True)
    home = state / "home"
    env = configure_guest_home(home, sandbox=document["sandbox"], feeds=document["feeds"])
    tun = Path("/dev/net/tun")
    tun.parent.mkdir(exist_ok=True)
    if not tun.exists():
        import stat

        os.mknod(tun, stat.S_IFCHR | 0o600, os.makedev(10, 200))
    os.chmod(tun, 0o600)
    repository = state / "repository"
    subprocess.run(
        ["git", "clone", "--branch", document["branch"], "--",
         document["origin"], str(repository)],
        env=env, check=True, capture_output=True, timeout=300,
    )
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-B",
         document["branch"], document["revision"]],
        env=env, check=True, capture_output=True, timeout=60,
    )
    for key, value in (
        ("user.name", "GitHub Copilot"),
        ("user.email", "223556219+Copilot@users.noreply.github.com"),
    ):
        subprocess.run(
            ["git", "-C", str(repository), "config", key, value],
            env=env, check=True, capture_output=True, timeout=30,
        )
    launch = state / "launch.json"
    launch.write_text(json.dumps(document))
    pid = detach_remote_worker(
        [sys.executable, "-m", "git_loopy.remote_guest", "run", str(launch)],
        directory=repository, env=env, log=state / "orchestrator.log",
    )
    run_id = decode_detached_run_spec(document["run_spec"]).run_id
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        for trace in (repository / ".git-loopy" / "logs").glob(f"*-{run_id}.jsonl"):
            lines = trace.read_text().splitlines(keepends=True)
            for line in lines:
                if not line.endswith("\n"):
                    continue
                event = json.loads(line)
                if event.get("type") == "wrapper.sandbox.resolved":
                    if event.get("state") != "remote":
                        raise RemoteLaunchError("The guest did not require its remote Sandbox.")
                    return pid
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise RemoteLaunchError("The remote Run exited before Sandbox preflight completed.") from None
        time.sleep(.25)
    raise RemoteLaunchError("Remote Sandbox preflight was not confirmed before the launch deadline.")


def run_guest(document: dict) -> int:
    from git_loopy.cli import _drive_line_printer
    from git_loopy.release_version import read_runtime_release_version

    if document["residence"] != "remote":
        raise RemoteLaunchError("The guest must run with remote residence.")
    if document["version"] != read_runtime_release_version():
        raise RemoteLaunchError("The remote installation differs from the launching version.")
    spec = decode_detached_run_spec(document["run_spec"])
    if spec.config.execution_host != "local":
        raise RemoteLaunchError("Remote Lanes require the local Execution host.")
    return asyncio.run(_drive_line_printer(
        spec.config, residence="remote", run_id=spec.run_id,
        started_at=datetime.fromtimestamp(spec.started_at_epoch_ms / 1000, timezone.utc),
    ))


def main() -> int:
    try:
        action, path = sys.argv[1:]
        document = json.loads(Path(path).read_text())
        if action == "prepare":
            pid = prepare_guest(document, state=Path(path).parent)
            print(json.dumps({"pid": pid, "detached": True, "sandbox": "remote"}))
            return 0
        if action == "run":
            return run_guest(document)
        raise RemoteLaunchError("Unknown remote guest action.")
    except (RemoteLaunchError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("Remote guest launch failed; inspect the guest prerequisites and Run inputs.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
