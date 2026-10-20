"""Azure target configuration for Run residence (ADR-0093), not a Lane host.

Setup has no VM lifecycle and does not register a RunResidence. The remote
residence will consume this target when its launch/transport is implemented;
the default LocalResidence and its discovery remain untouched.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, cast

from git_loopy import settings


class RemoteSetupError(ValueError):
    """An actionable refusal that contains no provider response or credential."""


@dataclass(frozen=True)
class AzureTarget:
    """Only resource identifiers; credential_policy references a policy sandbox.

    Azure attaches egress policies to sandboxes, not sandbox groups. An
    operator-owned template sandbox in the target group holds the policy;
    setup stores its ID, never the policy or its credential values.
    """

    subscription: str
    resource_group: str
    sandbox_group: str
    credential_policy: str

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            self.subscription,
        ):
            raise RemoteSetupError("--subscription must be an Azure subscription UUID.")
        for name in ("resource_group", "sandbox_group", "credential_policy"):
            value = getattr(self, name)
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,89}", value)
                or value.endswith(".")
                or value.lower().startswith(("github_pat_", "ghp_", "gho_", "ghs_", "ghu_"))
            ):
                raise RemoteSetupError(
                    f"--{name.replace('_', '-')} must be a resource name, not a URL or credential."
                )

    @classmethod
    def from_config(cls, table: Mapping[str, object]) -> AzureTarget:
        raw = table.get("remote")
        fields = ("subscription", "resource_group", "sandbox_group", "credential_policy")
        if not isinstance(raw, dict):
            raise RemoteSetupError("Run `git-loopy remote setup` to record an Azure target.")
        section = cast(dict[str, object], raw)
        values: dict[str, str] = {}
        for key in fields:
            value = section.get(key)
            if not isinstance(value, str):
                raise RemoteSetupError("Run `git-loopy remote setup` to record an Azure target.")
            values[key] = value
        return cls(**values)


class SandboxProvider(Protocol):
    """Read-only Azure validation; never return or request a credential value."""

    def validate_target(self, target: AzureTarget) -> None: ...


def run_setup(
    target: AzureTarget,
    *,
    repo_root: Path,
    provider: SandboxProvider,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
    ask: Callable[[str], str] = input,
) -> int:
    """Validate before replacing the project target, preserving unrelated Config."""
    if err is None:
        def err(message: str) -> None:
            print(message, file=sys.stderr)
    try:
        path = settings.project_config_path(repo_root)
        table = settings.load_config_table(path)
        provider.validate_target(target)
        _offer_devcontainer(repo_root, ask=ask, out=out)
        previous = table.get("remote")
        table["remote"] = {
            **(previous if isinstance(previous, dict) else {}), **asdict(target),
        }
        settings.write_config_atomic(path, table, normalize_enabled_skills=False)
    except RemoteSetupError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    except (OSError, settings.SettingsError):
        # Parser/OS errors may contain arbitrary Config text. Do not echo it.
        err("git-loopy: error: Cannot save remote setup; check project Config and file permissions.")
        return 1
    out("Azure target validated and saved. Plain Runs still reside locally.")
    return 0


def _offer_devcontainer(
    root: Path, *, ask: Callable[[str], str], out: Callable[[str], None]
) -> None:
    directory = root / ".devcontainer"
    single_file = root / ".devcontainer.json"
    if any(path.exists() or path.is_symlink() for path in (directory, single_file)):
        return
    try:
        consent = ask("No .devcontainer found. Generate a minimal .devcontainer? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        consent = ""
    if consent.strip().lower() not in {"y", "yes"}:
        out("No .devcontainer written; one is required before a remote Run can start.")
        return
    # Exclusive creation also refuses a directory/symlink created while asking.
    directory.mkdir()
    with (directory / "devcontainer.json").open("x", encoding="utf-8") as handle:
        json.dump(
            {"name": "git-loopy remote", "image": "mcr.microsoft.com/devcontainers/base:ubuntu"},
            handle, indent=2,
        )
        handle.write("\n")
    out("Created .devcontainer/devcontainer.json; customize it for the repository toolchain.")
