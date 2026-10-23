"""The clone's remote Sweep: receipts are retired only after Azure proves absence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from git_loopy.git import GitClient
from git_loopy.remote_runs import RemoteRunReceipt, remote_run_receipts, retire_remote_run
from git_loopy.run_discovery import DiscoveredRun, RunLiveness, RunTargetError, resolve_run


@dataclass(frozen=True)
class RemoteSweepResult:
    actions: tuple[str, ...] = ()
    retire: bool = False


class RemoteSweepProvider(Protocol):
    def sweep(
        self, receipt: RemoteRunReceipt, *, dry_run: bool, retry: bool,
    ) -> RemoteSweepResult: ...


def sweep_remote(
    *, git: GitClient, provider: RemoteSweepProvider, dry_run: bool,
    retry: str | None = None, output: Callable[[str], None] = print,
) -> bool:
    """Best-effort per receipt, but never work from a partial or ambiguous listing.

    An explicit retry targets exactly one receipt, with the same prefix rules
    as ``runs``. It is the only path allowed to resume a Stopped microVM.
    """
    receipts = remote_run_receipts(git)
    if retry is not None:
        selected = resolve_run(retry, (
            DiscoveredRun(
                r.run_id, r.scope, r.started_at, RunLiveness.UNKNOWN,
                r.scope / r.run_id, r.scope / r.run_id, "remote",
            ) for r in receipts
        ))
        receipts = tuple(r for r in receipts if r.run_id == selected.run_id)
    ok = True
    for receipt in receipts:
        try:
            result = provider.sweep(receipt, dry_run=dry_run, retry=retry is not None)
            for action in result.actions:
                output(("Would " if dry_run else "") + action)
            if result.retire:
                if not dry_run:
                    retire_remote_run(git, receipt)
                output(
                    ("Would " if dry_run else "")
                    + f"retire remote Run receipt: {receipt.run_id}"
                )
        except RunTargetError as exc:
            # RunTargetError carries the provider's redacted, operator-facing
            # refusal, including the exact retry command when one is needed.
            output(f"Remote Run {receipt.run_id} retained: {exc}")
            ok = False
        except (OSError, ImportError):
            # Neither provider diagnostics nor guest output may leak secrets.
            output(
                f"Remote Run {receipt.run_id} retained: reclamation is unconfirmed; "
                "check Azure CLI access and guest preservation."
            )
            ok = False
    return ok
