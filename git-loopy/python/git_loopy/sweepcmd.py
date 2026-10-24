"""``git-loopy sweep`` — the explicit operator command (#455, #445 §F).

The reclamation actor for the case no Run can cover: nothing is running at all.
The other two actors are a Run reclaiming its own **Lane workspaces** and a Run
sweeping its dead predecessors' — both need a Run.

Silence is the interface. A sweep that reclaimed nothing prints nothing, so a
clean machine says nothing and any output at all is news; ``--dry-run`` reports
the identical plan and performs none of it.
"""

from __future__ import annotations

import sys
import os
from collections.abc import Callable
from pathlib import Path

from git_loopy.gh import GhError, SubprocessGitHubClient
from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.sweep import resolve_base_ref, sweep
from git_loopy.run_discovery import RunTargetError


def run_sweep(
    *,
    repo_root: Path,
    dry_run: bool,
    retry_remote: str | None = None,
    output: Callable[[str], None] = print,
) -> int:
    """Sweep ``repo_root`` and name every removal, in the order it happened.

    Args:
        repo_root: The clone to sweep.
        dry_run: Plan the sweep without performing any part of it.
        retry_remote: Target only this remote Run, permitting an explicit resume.
        output: Where each line goes; injectable so tests read the report
            rather than captured stdout.

    Returns:
        ``0``, including for a clean repository; ``1`` on a local failure or
        unconfirmed remote reclamation. Remote failures retain their receipts.
    """
    git = SubprocessGitClient(repo_root)
    github = SubprocessGitHubClient()
    try:
        from git_loopy.azure_remote import AzureRemoteProvider
        from git_loopy.remote_sweep import sweep_remote

        remote_ok = sweep_remote(
            git=git, provider=AzureRemoteProvider({}, os.environ),
            dry_run=dry_run, retry=retry_remote, output=output,
        )
        if retry_remote is not None:
            return 0 if remote_ok else 1
        report = sweep(
            git=git,
            github=github,
            control_dir=repo_root / ".git-loopy" / "logs",
            base_branch=resolve_base_ref(git),
            dry_run=dry_run,
        )
    except (GitError, GhError, OSError, RunTargetError) as exc:
        print(f"git-loopy: sweep failed: {exc}", file=sys.stderr)
        return 1
    reclaim_verb = "Would reclaim" if dry_run else "Reclaimed"
    collect_verb = "Would collect" if dry_run else "Collected"
    for worktree in report.worktrees:
        output(f"{reclaim_verb} worktree: {worktree}")
    for branch in report.branches:
        output(f"{collect_verb} branch: {branch}")
    for directory in report.directories:
        output(f"{reclaim_verb} directory: {directory}")
    for stem in report.runs:
        output(f"{reclaim_verb} Run logs: {stem}")
    return 0 if remote_ok else 1
