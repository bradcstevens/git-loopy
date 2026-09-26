"""``git-loopy attach <run-id>`` — observe one Run without owning it (#586).

The public command over the client foundation any launching terminal already
uses (#459, ADR-0058). It does not own a second lifecycle. It names one Run
through the shared resolver and follows that Run's trace. It never spawns a
worker, never resumes one, and never writes a Stop.

A Dashboard helper, when one is usable, draws the Run. ``q`` is Detach.
A missing helper or a Dashboard fault stays attached through the line printer.
That fallback is not Detach and not Stop. Liveness this host cannot prove is
reported as unknown rather than treated as a finished Run or a live one.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.run_discovery import (
    RunLiveness,
    RunTargetError,
    discover_runs,
    resolve_run,
)
from git_loopy.run_sidecar import observe_existing_run

__all__ = ["run_attach"]


def run_attach(
    *,
    repo_root: Path,
    run_id: str,
    output: Callable[[str], None] = print,
) -> int:
    """Attach to one explicit Run and observe it until Detach or the Run ends.

    Args:
        repo_root: Any worktree of the clone whose Runs may be named.
        run_id: The Run identity, or an unambiguous leading part of one.
        output: Where the observation's own lines go. Refusals go to stderr,
            because they are not the success result.

    Returns:
        ``0`` after a clean Detach, a finished observation, or a replay of a
        Run that had already ended. ``1`` when the Run cannot be named, or
        when its liveness cannot be proved. Never the Run's own exit code:
        this command does not own that process.
    """
    try:
        run = resolve_run(
            run_id,
            discover_runs(git=SubprocessGitClient(repo_root)),
        )
    except GitError as exc:
        _refuse(f"could not enumerate this clone's worktrees: {exc}")
        return 1
    except RunTargetError as exc:
        _refuse(str(exc))
        return 1
    if run.liveness is RunLiveness.UNKNOWN:
        _refuse(
            f"the liveness of Run {run.run_id} could not be read on this host, "
            "so Attach will not follow it as live and will not report it as "
            "ended. Its control artifact is "
            f"{run.control_path}. Attach does not resume a worker."
        )
        return 1
    return observe_existing_run(
        repository_root=repo_root,
        trace_path=run.trace_path,
        control_path=run.control_path,
        run_id=run.run_id,
        output=output,
    )


def _refuse(message: str) -> None:
    print(f"git-loopy: {message}", file=sys.stderr)
