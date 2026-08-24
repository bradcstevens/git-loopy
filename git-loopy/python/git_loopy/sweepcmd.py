"""Explicit operator command for reclaiming dead-Run residue."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from git_loopy.gh import GhError, SubprocessGitHubClient
from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.sweep import sweep


def run_sweep(
    *,
    repo_root: Path,
    dry_run: bool,
    output: Callable[[str], None] = print,
) -> int:
    """Sweep this repository and print only the work reclaimed or planned."""
    git = SubprocessGitClient(repo_root)
    github = SubprocessGitHubClient()
    try:
        base_branch = github.repo_view().default_branch
        report = sweep(
            git=git,
            github=github,
            control_dir=repo_root / ".git-loopy" / "logs",
            base_branch=base_branch,
            dry_run=dry_run,
        )
    except (GitError, GhError) as exc:
        print(f"git-loopy: sweep failed: {exc}", file=sys.stderr)
        return 1
    worktree_verb = "Would reclaim" if dry_run else "Reclaimed"
    branch_verb = "Would collect" if dry_run else "Collected"
    for worktree in report.worktrees:
        output(f"{worktree_verb} worktree: {worktree}")
    for branch in report.branches:
        output(f"{branch_verb} branch: {branch}")
    return 0
