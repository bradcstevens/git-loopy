"""Prepare a whole remote Run before crossing the microVM provider seam."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, NoReturn, Protocol

from git_loopy.config import RunConfig
from git_loopy.persist import make_run_id
from git_loopy.release_version import read_runtime_release_version
from git_loopy.run_discovery import DiscoveredRun, RunLiveness, RunTargetError
from git_loopy.run_residence import AttachChannel, RunLaunch
from git_loopy.remote_runs import RemoteRunReceipt, remote_run_receipts

if TYPE_CHECKING:
    from git_loopy.git import GitClient
    from git_loopy.remote_control import RemoteSnapshot


class RemoteLaunchError(RunTargetError):
    """An actionable remote-launch refusal, without provider diagnostics."""


@dataclass(frozen=True)
class RemotePlan:
    """Only explicit, noncredential Run inputs cross into the guest."""

    source: Path
    origin: str
    branch: str
    revision: str
    config: RunConfig
    sandbox: Mapping[str, object]
    version: str
    launch_id: str

    def guest_payload(self) -> str:
        from git_loopy.run_sidecar import DetachedRunSpec, encode_detached_run_spec

        return json.dumps({
            "schema_version": 1,
            "residence": "remote",
            "origin": self.origin,
            "branch": self.branch,
            "revision": self.revision,
            "version": self.version,
            "sandbox": dict(self.sandbox),
            "run_spec": encode_detached_run_spec(DetachedRunSpec(
                config=self.config, run_id=self.launch_id,
                started_at_epoch_ms=int(time.time() * 1000),
            )),
        }, sort_keys=True)


class RemoteLaunchProvider(Protocol):
    def launch(self, plan: RemotePlan) -> str: ...


class RemoteProvider(RemoteLaunchProvider, Protocol):
    def inspect(
        self, receipt: RemoteRunReceipt, *, offset: int = 0, request_id: str | None = None,
    ) -> RemoteSnapshot | None: ...


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        raise RemoteLaunchError(
            "Cannot verify origin's default branch; check Git access and retry."
        ) from None
    return result.stdout.strip()


def launch_remote(
    request: RunLaunch,
    *,
    provider: RemoteLaunchProvider,
    sandbox_settings: Mapping[str, object] | None = None,
    env: Mapping[str, str],
    output: Callable[[str], None] = print,
) -> str:
    """Refuse unpublished default-branch work before allocating any compute."""
    from git_loopy import cli, settings

    root = request.target
    if not any(path.is_file() for path in (
        root / ".devcontainer" / "devcontainer.json", root / ".devcontainer.json",
    )):
        raise RemoteLaunchError(
            "A remote Run requires a .devcontainer; run `git-loopy remote setup`."
        )
    tables = settings.load_configs(root, env)
    parser = cli.build_parser()
    args = parser.parse_args(list(request.run_arguments))
    if args.issue_pin is not None and len(args.issue_pin) > 1:
        parser.error("--issue may be given at most once.")
    config = cli.resolve_config(
        args, env, project=tables.project, global_=tables.global_,
        measured=tables.measured, measured_provisional=tables.measured_provisional,
    ).run
    if config.execution_host != "local":
        raise RemoteLaunchError(
            "A remote Run cannot use the github-actions Lane Execution host; select local."
        )
    advertised = _git(root, "ls-remote", "--symref", "origin", "HEAD")
    default = next((
        line.split()[1] for line in advertised.splitlines()
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD")
    ), None)
    if default is None:
        raise RemoteLaunchError("Origin does not advertise a default branch.")
    _git(root, "fetch", "--no-tags", "origin", default)
    local_only = _git(root, "rev-list", default, "--not", "FETCH_HEAD")
    if local_only:
        raise RemoteLaunchError(
            "The local default branch is ahead of origin; publish it before a remote Run."
        )
    origin = _git(root, "config", "--get", "remote.origin.url")
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        origin,
    )
    if match is None:
        raise RemoteLaunchError(
            "Remote Runs require a credential-free github.com origin URL."
        )
    revision = _git(root, "rev-parse", "FETCH_HEAD")
    if request.base_revision not in {"HEAD", revision}:
        raise RemoteLaunchError("A remote Run must start at origin's default branch.")
    tracked = _git(root, "ls-tree", "-r", "--name-only", revision).splitlines()
    if not {".devcontainer/devcontainer.json", ".devcontainer.json"}.intersection(tracked):
        raise RemoteLaunchError("Publish the .devcontainer to origin before a remote Run.")
    plan = RemotePlan(
        source=root, origin=f"https://github.com/{match[1]}",
        branch=default.removeprefix("refs/heads/"), revision=revision,
        config=config, sandbox=(
            asyncio.run(read_operator_sandbox())
            if sandbox_settings is None else dict(sandbox_settings)
        ),
        version=read_runtime_release_version(), launch_id=make_run_id(),
    )
    output(
        f"Launching remote Run {plan.launch_id}. "
        "Remote Run compute is metered to the Azure subscription."
    )
    sandbox_id = provider.launch(plan)
    output(
        f"Remote Run {plan.launch_id} launched detached in Azure sandbox {sandbox_id}; "
        "Sandbox is remote (always on)."
    )
    return sandbox_id


class RemoteResidence:
    """Whole-Run clients address only launches recorded by this clone."""

    name = "remote"

    def __init__(self, provider: RemoteProvider, env: Mapping[str, str]) -> None:
        self._provider = provider
        self._env = env
        self._receipts: dict[str, RemoteRunReceipt] = {}
        self._control_root: Path | None = None

    def launch(self, request: RunLaunch) -> int:
        launch_remote(request, provider=self._provider, env=self._env)
        return 0

    def discover(self, *, git: GitClient) -> tuple[DiscoveredRun, ...]:
        from git_loopy.remote_control import GUEST_REPOSITORY

        found = []
        self._control_root = git.common_git_dir()
        self._receipts = {receipt.run_id: receipt for receipt in remote_run_receipts(git)}
        for receipt in self._receipts.values():
            try:
                snapshot = self._provider.inspect(receipt)
            except ImportError:
                print(
                    f"git-loopy: Remote Run {receipt.run_id} liveness is unknown; "
                    "install the [remote] extra through your configured package feed.",
                    file=sys.stderr,
                )
                snapshot = None
            except RunTargetError as exc:
                print(
                    f"git-loopy: Remote Run {receipt.run_id} liveness is unknown: {exc}",
                    file=sys.stderr,
                )
                snapshot = None
            if snapshot is None:
                trace = GUEST_REPOSITORY / ".git-loopy" / "logs" / f"{receipt.run_id}.jsonl"
                found.append(DiscoveredRun(
                    receipt.run_id, receipt.scope, receipt.started_at, RunLiveness.UNKNOWN,
                    trace, trace.with_suffix(".control"), "remote",
                ))
            else:
                found.append(replace(snapshot.run, scope=receipt.scope, residence="remote"))
        return tuple(found)

    def open_attach(self, run: DiscoveredRun, *, repo_root: Path) -> AttachChannel:
        from git_loopy.remote_client import RemoteAttachChannel

        return RemoteAttachChannel(self._provider, self._receipt(run), repo_root)

    def request_stop(
        self, run: DiscoveredRun, *, request_id: str, timeout: float,
        output: Callable[[str], None],
    ) -> int:
        from git_loopy.remote_client import request_remote_stop

        return request_remote_stop(
            self._provider, self._receipt(run),
            request_id=request_id, timeout=timeout, output=output,
            outbox=self._stop_outbox(run),
        )

    def _receipt(self, run: DiscoveredRun) -> RemoteRunReceipt:
        try:
            return self._receipts[run.run_id]
        except KeyError:
            raise RunTargetError("Remote Run was not discovered in this clone.") from None

    def _stop_outbox(self, run: DiscoveredRun) -> Path:
        if self._control_root is None:
            raise RunTargetError("Remote Run was not discovered in this clone.")
        return self._control_root / "git-loopy" / "remote-stops" / run.run_id

    def teardown(self, run: DiscoveredRun) -> NoReturn:
        raise RemoteLaunchError(
            "Remote teardown is not available yet; preserve salvage before deleting the VM."
        )


async def read_operator_sandbox() -> dict[str, object]:
    """Read only the operator's sandbox block, never copy the Copilot home."""
    from git_loopy.copilot_client import make_copilot_client

    try:
        async with make_copilot_client() as client:
            response = await client.rpc.user.settings.get(timeout=30)
            value = response.settings["sandbox"].value
            if value is None:
                return {}
            if not isinstance(value, dict):
                raise ValueError
            return json.loads(json.dumps(value))
    except Exception:
        raise RemoteLaunchError(
            "Cannot read the operator's Copilot sandbox policy; check the local runtime."
        ) from None


def run_remote_command(arguments: list[str]) -> int:
    from git_loopy import cli, settings
    from git_loopy.azure_remote import AzureRemoteProvider
    from git_loopy.remote_setup import RemoteSetupError
    from git_loopy.run_residence import launch_run, register_residence
    from git_loopy.git import SubprocessGitClient
    from git_loopy.remote_sweep import sweep_remote

    parser = cli.build_parser()
    parser.prog = "git-loopy remote"
    parser.description = (
        "Launch a detached Azure Run. Configure its target with `git-loopy remote setup`."
    )
    args = parser.parse_args(arguments)
    if args.version:
        return cli.main(["--version"])
    try:
        root = cli.resolve_repo_root()
        tables = settings.load_configs(root, os.environ)
        provider = AzureRemoteProvider(tables.project, os.environ)
        sweep_remote(git=SubprocessGitClient(root), provider=provider, dry_run=False)
        with register_residence(RemoteResidence(provider, os.environ)):
            return launch_run(RunLaunch(root, tuple(arguments)), residence="remote")
    except ImportError:
        print(
            "git-loopy: Install the [remote] extra through your configured package feed.",
            file=sys.stderr,
        )
        return 1
    except (RunTargetError, RemoteSetupError, settings.SettingsError, RuntimeError) as exc:
        print(f"git-loopy: {exc}", file=sys.stderr)
        return 1
    return 0
