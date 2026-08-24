"""Reclaim dead-Run Lane workspaces and resolved reserved branches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from git_loopy import git as git_module
from git_loopy.gh import GhError, GitHubClient
from git_loopy.run_control import is_run_alive
from git_loopy.wrapper import checkpoint_message

__all__ = ["SweepReport", "sweep"]

_BRANCH_RE = re.compile(
    r"^git-loopy/(?P<run_id>[^/]+)(?:/(?P<stage>integrate|materialized))?"
    r"/issue-(?P<issue>\d+)$"
)


@dataclass(frozen=True)
class SweepReport:
    """What one sweep reclaimed, or would reclaim in dry-run mode."""

    worktrees: tuple[Path, ...] = ()
    branches: tuple[str, ...] = ()

    @property
    def reclaimed_anything(self) -> bool:
        return bool(self.worktrees or self.branches)


@dataclass(frozen=True)
class _Residue:
    branch: str
    run_id: str
    issue: int
    stage: bool


def _parse_residue(branch: str) -> _Residue | None:
    match = _BRANCH_RE.fullmatch(branch)
    if match is None:
        return None
    return _Residue(
        branch=branch,
        run_id=match["run_id"],
        issue=int(match["issue"]),
        stage=match["stage"] == "integrate",
    )


def _run_is_alive(
    control_dir: Path,
    run_id: str,
    liveness: Callable[[Path], bool | None],
) -> bool | None:
    states = [liveness(path) for path in control_dir.glob(f"*-{run_id}.control")]
    if any(state is True for state in states):
        return True
    if any(state is None for state in states):
        return None
    return False


def _prune_empty_parents(path: Path, roots: tuple[Path, ...]) -> None:
    """Remove empty workspace ancestors, never traversing outside a known root."""
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        current = path if path.exists() else path.parent
        while True:
            try:
                current.rmdir()
            except (FileNotFoundError, OSError):
                return
            if current == root:
                return
            current = current.parent
        return


def sweep(
    *,
    git: git_module.GitClient,
    github: GitHubClient | None,
    control_dir: Path,
    base_branch: str,
    dry_run: bool,
    liveness: Callable[[Path], bool | None] = is_run_alive,
) -> SweepReport:
    """Reclaim dead **Lane workspaces** and collect resolved reserved branches.

    Ownership comes only from the reserved branch namespace. A free (or absent)
    control artifact proves the Run dead; unavailable advisory locking preserves
    its workspaces because liveness is then unknown.
    """
    legacy_root = git.root.parent / f"{git.root.name}.worktrees"
    worktrees: list[Path] = []
    preserved: set[str] = set()
    for worktree in git_module.reserved_worktrees(git.list_worktrees()):
        residue = _parse_residue(worktree.branch or "")
        if residue is None:
            continue
        if _run_is_alive(control_dir, residue.run_id, liveness) is not False:
            continue
        if dry_run:
            worktrees.append(worktree.path)
            continue
        workspace_git = git.open_worktree(worktree.path)
        try:
            dirty = workspace_git.is_dirty() or workspace_git.has_untracked()
            if dirty:
                workspace_git.add_all()
                workspace_git.commit(checkpoint_message(residue.issue))
            git.remove_worktree(worktree.path, force=True)
        except git_module.GitError:
            preserved.add(residue.branch)
            continue
        worktrees.append(worktree.path)
        _prune_empty_parents(
            worktree.path,
            (git.common_git_dir() / "git-loopy", legacy_root),
        )

    branches: list[str] = []
    closed: dict[int, bool] = {}
    for branch in git.list_branches():
        residue = _parse_residue(branch)
        if residue is None or branch in preserved:
            continue
        if _run_is_alive(control_dir, residue.run_id, liveness) is not False:
            continue
        if residue.stage:
            collect = True
        else:
            try:
                merged = git.is_merged_into(branch, base_branch)
            except git_module.GitError:
                continue
            if merged:
                collect = True
            else:
                if github is None:
                    collect = False
                    continue
                if residue.issue not in closed:
                    try:
                        closed[residue.issue] = (
                            github.issue_view(residue.issue).state == "CLOSED"
                        )
                    except GhError:
                        closed[residue.issue] = False
                collect = closed[residue.issue]
        if not collect:
            continue
        if not dry_run:
            try:
                git.delete_branch(branch)
            except git_module.GitError:
                continue
        branches.append(branch)

    return SweepReport(worktrees=tuple(worktrees), branches=tuple(branches))
