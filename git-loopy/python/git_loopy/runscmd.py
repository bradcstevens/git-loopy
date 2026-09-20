"""``git-loopy runs`` — list this clone's Runs (#584, ADR-0058).

The public answer to "what is running here, and what did it leave behind". It
reports; it does not act. Every Run it names carries the identity Attach and
Stop are targeted by, the worktree it was started in, and the liveness this
host could actually prove — including ``unknown``, which is printed as the
distinct answer it is rather than rounded down to ``dead``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.run_discovery import DiscoveredRun, discover_runs

__all__ = ["run_runs"]

_HEADINGS = ("RUN", "STATE", "STARTED", "SCOPE")


def run_runs(
    *,
    repo_root: Path,
    output: Callable[[str], None] = print,
) -> int:
    """List the Runs of ``repo_root``'s clone, newest first.

    Args:
        repo_root: Any worktree of the clone whose Runs to list.
        output: Where each line goes; injectable so tests read the report
            rather than captured stdout.

    Returns:
        ``0`` for any answer, including an empty one — a clone with no Runs is
        a fact, not a failure. ``1`` only when the clone's worktrees could not
        be enumerated at all, because a listing that silently covered fewer
        worktrees than it claims is worse than no listing.
    """
    try:
        runs = discover_runs(git=SubprocessGitClient(repo_root))
    except GitError as exc:
        print(
            f"git-loopy: could not enumerate this clone's worktrees: {exc}",
            file=sys.stderr,
        )
        return 1
    if not runs:
        output("No Runs have been recorded in this clone.")
        return 0
    for line in _table(runs):
        output(line)
    return 0


def _table(runs: tuple[DiscoveredRun, ...]) -> list[str]:
    """Render one column-aligned row per Run, under a stable heading."""
    rows = [_HEADINGS, *(_row(run) for run in runs)]
    widths = [max(len(row[column]) for row in rows) for column in range(len(_HEADINGS))]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    ]


def _row(run: DiscoveredRun) -> tuple[str, str, str, str]:
    return (
        run.run_id,
        run.liveness.value,
        run.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        str(run.scope),
    )
