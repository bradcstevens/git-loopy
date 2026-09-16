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
from collections.abc import Callable
from pathlib import Path

from git_loopy.gh import GhError, SubprocessGitHubClient
from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.sweep import resolve_base_ref, sweep


def run_sweep(
    *,
    repo_root: Path,
    dry_run: bool,
    output: Callable[[str], None] = print,
) -> int:
    """Sweep ``repo_root`` and name every removal, in the order it happened.

    Args:
        repo_root: The clone to sweep.
        dry_run: Plan the sweep without performing any part of it.
        output: Where each line goes; injectable so tests read the report
            rather than captured stdout.

    Returns:
        ``0``, including for a clean repository; ``1`` when git or ``gh`` failed
        outright, which is the one case that is not a removal and so does not go
        through ``output``.
    """
    git = SubprocessGitClient(repo_root)
    github = SubprocessGitHubClient()
    try:
        report = sweep(
            git=git,
            github=github,
            control_dir=repo_root / ".git-loopy" / "logs",
            base_branch=resolve_base_ref(git),
            dry_run=dry_run,
        )
    except (GitError, GhError, OSError) as exc:
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
    return 0
