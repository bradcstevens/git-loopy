"""**Run discovery** — which Runs this clone may name, and which one you meant.

The one place the public Run-control commands answer "*which* Run?" (#584,
ADR-0058). `git-loopy runs` lists what it finds; the Attach and Stop commands
resolve one explicit identity out of the same listing. Sharing the module is
what keeps their domains identical: a Run either belongs to this clone or is
not addressable at all, and the answer cannot depend on which command asked.

Three rules make that safe, and each of them is a refusal to guess:

* **The domain is the clone, never the machine.** A Run publishes its control
  artifact beside its trace, which is per-*worktree*, so discovery enumerates
  this clone's registered worktrees (`git worktree list`) and stops there. An
  independent clone of the same remote is a different control domain, and a
  machine-wide scan would quietly make it one domain.
* **Liveness is the control artifact's advisory lock**
  (:mod:`git_loopy.run_control`), the same oracle a **Sweep** reads. Never a
  pid, a heartbeat, or the mere presence of a leftover file — a Run that
  finished normally leaves its artifact behind on purpose.
* **Unprovable is its own answer.** An artifact that cannot be read, or a
  trace with no artifact at all, is `unknown` and never `dead`. ADR-0058 is
  explicit that an inability to prove safety is not permission to control or
  reclaim work.

Discovery is observational: it opens files for reading, starts nothing, sends
no **Stop**, reclaims nothing, and never touches a tracker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from git_loopy import git as git_module
from git_loopy.persist import parse_run_stem
from git_loopy.run_control import (
    advisory_locking_available,
    control_path_for_trace,
    is_run_alive,
)

__all__ = [
    "AmbiguousRunTarget",
    "DiscoveredRun",
    "RunLiveness",
    "RunTargetError",
    "UnknownRunTarget",
    "UnprovableRunTarget",
    "control_directories",
    "discover_runs",
    "resolve_run",
]

#: Where a Run publishes its per-Run files, relative to the worktree it ran in.
_LOG_DIR = (".git-loopy", "logs")


class RunLiveness(Enum):
    """What this host can *prove* about a discovered Run, in its own words.

    ``UNKNOWN`` is deliberately not a softer ``DEAD``. It is what a host says
    when the control artifact could not be read at all, and it withholds the
    permission a ``DEAD`` answer grants.
    """

    LIVE = "live"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiscoveredRun:
    """One Run this clone can name, with everything explicit targeting needs."""

    run_id: str
    #: The worktree the Run published its artifacts in — its originating scope,
    #: which is what tells two Runs of one clone apart for an operator.
    scope: Path
    started_at: datetime
    liveness: RunLiveness
    trace_path: Path
    control_path: Path

    @property
    def stem(self) -> str:
        """The shared filename stem both per-Run artifacts are named for."""
        return self.trace_path.stem


class RunTargetError(RuntimeError):
    """A refusal to target a Run, phrased for the operator who asked."""


class UnknownRunTarget(RunTargetError):
    """No Run in this clone answers to that identity — including none given."""


class AmbiguousRunTarget(RunTargetError):
    """Several Runs answer to that identity, so none of them is *the* one."""

    def __init__(self, message: str, candidates: Sequence[DiscoveredRun]) -> None:
        super().__init__(message)
        self.candidates = tuple(candidates)


class UnprovableRunTarget(RunTargetError):
    """The Run was named, but this host cannot prove it is running."""

    def __init__(self, message: str, run: DiscoveredRun) -> None:
        super().__init__(message)
        self.run = run


def control_directories(git: git_module.GitClient) -> tuple[Path, ...]:
    """Every directory in this clone a Run could have published artifacts in.

    The invoking worktree first, so a Run of the operator's own worktree leads
    the listing, then every other registration ``git worktree list`` reports.
    A registration whose directory is gone (``prunable``) holds nothing to
    read and is skipped.

    A clone whose registrations cannot be read at all raises rather than
    answering with the invoking worktree alone: a listing that quietly covered
    less than the domain it claims would hide exactly the Run this seam exists
    to find, and would hide it from the Stop command too.

    Raises:
        git_module.GitError: The clone's worktrees could not be enumerated.
    """
    roots = [_normalize(git.root)]
    for worktree in git.list_worktrees():
        if worktree.prunable:
            continue
        root = _normalize(worktree.path)
        if root not in roots:
            roots.append(root)
    return tuple(root.joinpath(*_LOG_DIR) for root in roots)


def discover_runs(*, git: git_module.GitClient) -> tuple[DiscoveredRun, ...]:
    """Every Run this clone can name, newest first.

    Newest first because a listing is read from the top and the Run an operator
    is looking for is almost always the recent one — but the order is a
    *presentation*, and nothing here or downstream may promote the head of it
    into an inferred target (ADR-0058).

    Raises:
        git_module.GitError: The clone's worktrees could not be enumerated.
    """
    found: list[DiscoveredRun] = []
    for logs in control_directories(git):
        found.extend(_runs_in(logs))
    return tuple(
        sorted(found, key=lambda run: (run.started_at, run.run_id), reverse=True)
    )


def resolve_run(
    identity: str,
    runs: Iterable[DiscoveredRun],
    *,
    require_proof: bool = False,
) -> DiscoveredRun:
    """Resolve one explicit Run identity within this clone, or refuse.

    Accepts the full Run identity or any unambiguous leading part of one — a
    26-character ULID is a great deal to retype, and a prefix that names
    exactly one Run names it exactly. Every other outcome is a refusal that
    selects nothing:

    * an absent identity, because an empty prefix "matches" every Run and
      silently targeting one of them is the guess ADR-0058 forbids;
    * an identity no Run in this clone answers to;
    * an identity several answer to, reported with the candidates so the
      operator can retype a longer one;
    * and, where the caller needs proof before it acts, a Run whose liveness
      this host could not read.

    Args:
        identity: The Run identity the operator typed.
        runs: The Runs of this clone, from :func:`discover_runs`.
        require_proof: Demand proven liveness, for a caller that is about to
            control the Run rather than observe it.

    Raises:
        UnknownRunTarget: The identity was absent or matched nothing.
        AmbiguousRunTarget: The identity matched more than one Run.
        UnprovableRunTarget: ``require_proof`` and the liveness is unknown.
    """
    wanted = identity.strip().upper()
    if not wanted:
        raise UnknownRunTarget(
            "no Run identity given; name the Run to act on "
            "(`git-loopy runs` lists this clone's Runs)."
        )
    candidates = tuple(runs)
    exact = [run for run in candidates if run.run_id == wanted]
    matches = exact or [run for run in candidates if run.run_id.startswith(wanted)]
    if not matches:
        raise UnknownRunTarget(
            f"no Run of this clone is named {identity!r} "
            "(`git-loopy runs` lists this clone's Runs)."
        )
    if len(matches) > 1:
        named = ", ".join(run.run_id for run in matches)
        raise AmbiguousRunTarget(
            f"{identity!r} names {len(matches)} Runs of this clone ({named}); "
            "give the full Run identity.",
            matches,
        )
    run = matches[0]
    if require_proof and run.liveness is RunLiveness.UNKNOWN:
        raise UnprovableRunTarget(
            f"the liveness of Run {run.run_id} could not be read on this host, "
            "so it will not be acted on. Its control artifact is "
            f"{run.control_path}.",
            run,
        )
    return run


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalize(path: Path) -> Path:
    """One spelling per directory, so a worktree is never enumerated twice."""
    try:
        return path.resolve()
    except OSError:
        return path


def _runs_in(logs: Path) -> list[DiscoveredRun]:
    """Read one worktree's published Runs, tolerating a directory that is gone."""
    scope = logs.parent.parent
    try:
        entries = sorted(logs.iterdir())
    except OSError:
        return []
    stems = {
        entry.stem
        for entry in entries
        if entry.suffix in {".jsonl", ".control"}
    }
    runs: list[DiscoveredRun] = []
    for stem in sorted(stems):
        identity = parse_run_stem(stem)
        if identity is None:
            continue
        trace_path = logs / f"{stem}.jsonl"
        control_path = control_path_for_trace(trace_path)
        runs.append(
            DiscoveredRun(
                run_id=identity.run_id,
                scope=scope,
                started_at=identity.started_at,
                liveness=_liveness(control_path),
                trace_path=trace_path,
                control_path=control_path,
            )
        )
    return runs


def _liveness(control_path: Path) -> RunLiveness:
    """Read one Run's liveness from its control artifact, and nothing else.

    Every way of *not* getting an answer lands on ``UNKNOWN``: a host with no
    advisory locks, an artifact that was never published, and an artifact this
    process cannot open. The last two are distinct from a free lock — that one
    is the artifact of a Run that really has ended.
    """
    if not advisory_locking_available():
        return RunLiveness.UNKNOWN
    if not control_path.exists():
        return RunLiveness.UNKNOWN
    try:
        alive = is_run_alive(control_path)
    except OSError:
        return RunLiveness.UNKNOWN
    if alive is None:
        return RunLiveness.UNKNOWN
    return RunLiveness.LIVE if alive else RunLiveness.DEAD
