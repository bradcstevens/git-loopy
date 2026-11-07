"""Nonsecret launch receipts in this clone's shared git directory."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from git_loopy.git import GitClient
from git_loopy.persist import parse_run_stem, run_stem
from git_loopy.run_discovery import RunTargetError
from git_loopy.remote_setup import AzureTarget, RemoteSetupError


@dataclass(frozen=True)
class RemoteRunReceipt:
    run_id: str
    scope: Path
    started_at: datetime
    target: AzureTarget

    def __post_init__(self) -> None:
        if (
            self.started_at.tzinfo is None or not self.scope.is_absolute()
            or parse_run_stem(run_stem(self.run_id, self.started_at)) is None
        ):
            raise ValueError("Invalid remote Run receipt.")


def record_remote_run(git: GitClient, receipt: RemoteRunReceipt) -> None:
    """Persist before allocation, including when its response may be lost."""
    directory = git.common_git_dir() / "git-loopy" / "remote-runs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{receipt.run_id}.json").open("x", encoding="utf-8") as handle:
            json.dump({
                "schema_version": 1,
                "run_id": receipt.run_id,
                "scope": str(receipt.scope),
                "started_at": receipt.started_at.isoformat(),
                "target": asdict(receipt.target),
            }, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        raise RunTargetError("Cannot save this clone's remote Run receipt; launch refused.") from None


def remote_run_receipts(git: GitClient) -> tuple[RemoteRunReceipt, ...]:
    directory = git.common_git_dir() / "git-loopy" / "remote-runs"
    try:
        try:
            paths = sorted(directory.iterdir())
        except FileNotFoundError:
            return ()
        receipts = []
        for path in paths:
            if path.suffix != ".json":
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if value["schema_version"] != 1 or value["run_id"] != path.stem:
                raise ValueError
            started_at = datetime.fromisoformat(value["started_at"])
            if started_at.tzinfo is None:
                raise ValueError
            receipts.append(RemoteRunReceipt(
                value["run_id"], Path(value["scope"]), started_at,
                AzureTarget.from_config({"remote": value["target"]}),
            ))
        return tuple(receipts)
    except (OSError, ValueError, KeyError, TypeError, RemoteSetupError):
        raise RunTargetError(
            "Cannot read this clone's remote Run receipts; refusing a partial listing."
        ) from None


def retire_remote_run(git: GitClient, receipt: RemoteRunReceipt) -> None:
    """Remove only the receipt the caller proved spent, never a changed record."""
    current = remote_run_receipts(git)
    if receipt not in current:
        raise RunTargetError("Remote Run receipt changed during Sweep; retaining it.")
    (git.common_git_dir() / "git-loopy" / "remote-runs" / f"{receipt.run_id}.json").unlink()
