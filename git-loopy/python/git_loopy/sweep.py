"""**Sweep** — reclaim another Run's residue (#455, #445 §F).

The two reclamation actors that operate on residue *nobody is holding*: the
start-of-Run sweep a Run performs for its dead predecessors, and the explicit
``git-loopy sweep`` command for the case where nothing is running at all. The
actors that reclaim a Run's *own* **Lane workspaces** live in
:mod:`git_loopy.loop`; this module never runs inside the Run that owns what it
is looking at.

Three rules make that safe:

* **Liveness is the control artifact's advisory lock** (:mod:`git_loopy.run_control`)
  and nothing else — lock free ⇒ that Run is dead ⇒ its workspaces are
  reclaimable. No stale pid, no heartbeat timeout, and no code the dead Run had
  to reach. It is what covers the hard kill and the lost power cable. Liveness
  that cannot be read at all (a platform without ``flock``) is *unknown*, never
  dead, so those workspaces are preserved.
* **Ownership comes from the Reserved branch namespace, never from a path.**
  The legacy sibling directory interleaves git-loopy's worktrees with the
  operator's own, so a location can never decide what may be deleted.
* **Emptiness owns the directories.** Only an empty directory is removed, which
  is what lets the same pass be pointed at a directory git-loopy shares.

Nothing here is contract-visible: a sweep emits no **Event** — not even a
Checkpoint one for the **Salvage** it performs — produces no **Strike**, and
never appears as work in a Run's **Summary**. A Run that swept an issue's
residue did not work that issue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from git_loopy import git as git_module
from git_loopy.gh import GhError, GitHubClient
from git_loopy.run_control import is_run_alive
from git_loopy.wrapper import checkpoint_message, is_checkpoint_message

__all__ = ["SweepReport", "resolve_base_ref", "sweep"]

_BRANCH_RE = re.compile(
    r"^git-loopy/(?P<run_id>[^/]+)(?:/(?P<stage>integrate|materialized))?"
    r"/issue-(?P<issue>\d+)$"
)


def resolve_base_ref(git: git_module.GitClient) -> str:
    """The base a sweep resolves Lane branches against.

    The branch the repository is checked out on, because that is the base a
    **Lane** branch was cut from (ADR-0008) and the one **Integration**
    publishes back to. The repository's *default* branch is a different fact
    and can name a ref no Lane was ever merged into — a Run working a release
    line would then read every one of its own merged branches as unmerged.
    Falls back to the head SHA, then to the literal ``HEAD``, so a detached
    checkout still yields a ref ``git merge-base`` can resolve.
    """
    try:
        branch = git.current_branch()
    except git_module.GitError:
        branch = None
    if branch:
        return branch
    try:
        return git.head_sha()
    except git_module.GitError:
        return "HEAD"


@dataclass(frozen=True)
class SweepReport:
    """What one sweep reclaimed, or would reclaim in dry-run mode.

    Every field is a removal, which is what lets the caller stay silent on a
    clean machine by asking one question: :attr:`reclaimed_anything`.
    """

    worktrees: tuple[Path, ...] = ()
    branches: tuple[str, ...] = ()
    directories: tuple[Path, ...] = ()

    @property
    def reclaimed_anything(self) -> bool:
        return bool(self.worktrees or self.branches or self.directories)


@dataclass(frozen=True)
class _Residue:
    """What a reserved branch name says about the thing it names."""

    branch: str
    run_id: str
    issue: int
    stage: bool


def _parse_residue(branch: str) -> _Residue | None:
    """Read a reserved branch name, or ``None`` if it is not one of ours.

    Only ``stage`` is a judgement: an **Integration stage** branch exists for
    exactly one merge attempt in a private worktree and so is always
    collectable, while a *materialized* branch is a contribution fetched from a
    remote **Execution host** — real work, resolved exactly like the **Lane**
    branch a local contribution would have produced.
    """
    match = _BRANCH_RE.fullmatch(branch)
    if match is None:
        return None
    return _Residue(
        branch=branch,
        run_id=match["run_id"],
        issue=int(match["issue"]),
        stage=match["stage"] == "integrate",
    )


def _control_dirs(control_dir: Path, worktrees: list[git_module.Worktree]) -> list[Path]:
    """Every directory in this clone that a Run could have published a lock in.

    A control artifact is written beside its Run's trace, which is per-*worktree*
    — but the **Reserved branch namespace** it is claiming against is per-*clone*.
    A sweep from one worktree therefore enumerates workspaces belonging to a Run
    that locked its artifact in another, and reading only its own directory would
    declare that live Run dead and force-remove the workspace it is working in.
    """
    suffix = control_dir.name
    parent = control_dir.parent.name
    dirs = [control_dir]
    for worktree in worktrees:
        candidate = worktree.path / parent / suffix
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _liveness_by_run(
    control_dirs: list[Path], liveness: Callable[[Path], bool | None]
) -> Callable[[str], bool | None]:
    """A memoized *is this Run alive* oracle keyed by ``run_id``.

    A Run's control artifact is named for its trace, so a ``run_id`` is matched
    across every directory of :func:`_control_dirs` rather than at one known
    path. The three answers are deliberately distinct, and the composition is
    deliberately pessimistic: any locked artifact means alive, any *unreadable*
    liveness means unknown, and only the complete absence of both means dead.
    Memoizing makes the answer stable for the length of one sweep, so a Run
    cannot be read dead when its workspace is judged and alive when its branch
    is.

    Liveness reads the filesystem, so it can fail for reasons that say nothing
    about the Run. Every such failure is *unknown*, which preserves — and none
    of them escapes, because a sweep is best-effort cleanup and must never take
    down the Run that invoked it.
    """
    cache: dict[str, bool | None] = {}

    def read(path: Path) -> bool | None:
        try:
            return liveness(path)
        except OSError:
            return None

    def alive(run_id: str) -> bool | None:
        if run_id not in cache:
            states: list[bool | None] = []
            for control_dir in control_dirs:
                try:
                    matches = sorted(control_dir.glob(f"*-{run_id}.control"))
                except OSError:
                    states.append(None)
                    continue
                states.extend(read(path) for path in matches)
            if any(state is True for state in states):
                cache[run_id] = True
            elif any(state is None for state in states):
                cache[run_id] = None
            else:
                cache[run_id] = False
        return cache[run_id]

    return alive


def _reap_empty_directories(
    path: Path, *, dry_run: bool, reaped: list[Path], reclaimed: frozenset[Path]
) -> bool:
    """Reap every empty directory at or below ``path``, deepest first.

    ``git worktree remove`` takes only the leaf it was given, so each Run leaves
    its run and ``integrate/`` directories standing forever — residue that is
    real but that no worktree-keyed rule can reach, because there is no longer a
    worktree to key on.

    Emptiness is the entire classification, which is what makes this safe to
    point at a directory git-loopy shares with an operator: a directory holding
    anything at all is refused, so the pass cannot destroy work even where the
    reasoning above it never looked. That is why it may be aimed at the root
    itself — the root goes when, and only when, it is genuinely empty.

    ``reclaimed`` names the workspaces this sweep took, which are read as gone
    whether or not they are. Almost every directory here is empty only *because*
    a workspace was reclaimed, so a dry run that trusted the disk would name
    hardly any of what it is about to do; modelling the removal instead makes
    the plan and the outcome the same list.

    A symlink is never followed and never removed, at the root as well as
    below it, so a link cannot walk this pass out of the subtree it was aimed
    at and into a directory that is nobody's residue.

    Returns whether ``path`` itself was reaped, or in a dry run would be.
    """
    if path.is_symlink():
        return False
    try:
        entries = sorted(path.iterdir())
    except OSError:
        return False
    empty = True
    for entry in entries:
        if entry in reclaimed:
            continue
        if not entry.is_dir() or not _reap_empty_directories(
            entry, dry_run=dry_run, reaped=reaped, reclaimed=reclaimed
        ):
            empty = False
    if not empty:
        return False
    if not dry_run:
        try:
            path.rmdir()
        except OSError:
            return False
    reaped.append(path)
    return True


def _holds_unseen_work(
    git: git_module.GitClient, worktree: git_module.Worktree, *, stage: bool
) -> bool:
    """Whether ``worktree`` holds work that is not yet on its branch.

    Three registrations answer ``False`` without being opened, because none of
    them holds anything **Salvage** could rescue:

    * A **prunable** registration, whose directory is gone. Treating it as a
      failure instead would withhold its branch from collection forever, leaving
      the sweep to accumulate the very residue it exists to remove.
    * An **Integration stage**, which holds a half-finished merge. Its branch is
      collectable by definition, so a Checkpoint of conflict markers would be
      rescued onto a carrier that cannot keep it.
    * A registration reached through a **symlink**. A registration is a claim
      about a path, and a link makes that claim unverifiable — salvage would
      stage and commit whatever checkout the link actually points at.
    """
    if worktree.prunable or stage or worktree.path.is_symlink():
        return False
    workspace = git.open_worktree(worktree.path)
    return workspace.is_dirty() or workspace.has_untracked()


def _same_directory(left: Path, right: Path) -> bool:
    """Whether two paths name the same directory, however each is spelled.

    Git always reports its own resolved paths while a client carries whatever
    the caller passed, and on macOS those differ for everything under ``/tmp``.
    An identity question about a directory must not be answered by comparing
    two spellings of it.
    """
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _reclaim_dead_workspaces(
    *,
    git: git_module.GitClient,
    worktrees: list[git_module.Worktree],
    dry_run: bool,
    liveness: Callable[[str], bool | None],
) -> tuple[list[Path], set[str]]:
    """**Salvage** and reclaim every workspace whose Run is provably dead.

    The clone the sweep is standing in is never a candidate, whatever it is
    checked out on. An operator who checks a dead Run's Lane branch out to look
    at it puts their own working tree on a reserved branch, and reclamation
    would stage and commit *their* uncommitted work before git refused to remove
    the main worktree — a refusal this loop swallows, so nothing would say so.

    Returns the reclaimed paths and the branches collection must not look at:

    * The branch of a workspace that had to be **preserved**, salvage or removal
      having failed. The workspace is the reason the branch still matters, and
      git refuses to delete a branch a worktree still has checked out anyway.
    * The branch this pass salvaged onto, *which a dry run reports identically*.
      A real sweep needs no help here — the Checkpoint it just wrote is at the
      tip for :func:`_tip_is_a_checkpoint` to read — but a dry run wrote
      nothing, so without modelling the salvage it would plan to collect a
      branch the real sweep keeps. Dirtiness is therefore read in both modes.

    A Checkpoint left by an *earlier* actor needs no entry here at all; that is
    what reading the tip is for, and it is what protects the salvage ADR-0050's
    exit paths (#452) leave behind, from a Run this sweep never saw.
    """
    reclaimed: list[Path] = []
    withheld: set[str] = set()
    for worktree in git_module.reserved_worktrees(worktrees):
        residue = _parse_residue(worktree.branch or "")
        if residue is None or _same_directory(worktree.path, git.root):
            continue
        if liveness(residue.run_id) is not False:
            continue
        try:
            salvage = _holds_unseen_work(git, worktree, stage=residue.stage)
            if salvage:
                withheld.add(residue.branch)
            if not dry_run:
                if salvage:
                    workspace_git = git.open_worktree(worktree.path)
                    workspace_git.add_all()
                    workspace_git.commit(checkpoint_message(residue.issue))
                git.remove_worktree(worktree.path, force=True)
        except git_module.GitError:
            withheld.add(residue.branch)
            continue
        reclaimed.append(worktree.path)
    return reclaimed, withheld


def _tip_is_a_checkpoint(git: git_module.GitClient, branch: str) -> bool:
    """Whether ``branch`` ends in a **Checkpoint** of work nobody has seen.

    A Checkpoint is work its author never chose to commit, rescued by a
    reclaiming actor (ADR-0004) — so when one is the *last* thing that happened
    on a branch, nothing has since looked at it. A Checkpoint further back is a
    routine mid-Run one that later agent commits built on, which is ordinary
    work and says nothing about whether the branch is still wanted.

    An unreadable history answers ``True``: a branch that cannot be examined is
    never proven collectable.
    """
    try:
        tip = git.branch_tip(branch)
    except git_module.GitError:
        return True
    return is_checkpoint_message(tip.message)


def _is_resolved(
    residue: _Residue,
    *,
    git: git_module.GitClient,
    github: GitHubClient | None,
    base_branch: str,
    closed: dict[int, bool],
) -> bool:
    """Whether ``residue``'s branch has been resolved and may be collected.

    An **Integration stage** branch is ephemeral by construction — its whole
    life is one merge attempt in a private worktree — so it is always resolved.
    A **Lane** branch is resolved when it is merged into base *or* when its
    issue is closed. Merged-ness alone was measured and collects less than half
    the residue while keeping branches that are provably worthless: every
    unmerged Lane branch in the census belonged to a closed issue, which
    merged-ness structurally cannot see.

    Every unanswerable question resolves to *no*. A git failure, an
    unreachable GitHub and an offline sweep with no client at all all leave an
    unmerged branch standing, because it may be the only copy of its work.

    A closed issue is evidence about *the issue*, not about a **Checkpoint**
    sitting at the branch's tip — the census counted branches whose work was
    committed once and landed by some other route, which is precisely what a
    Checkpoint is not. So the closed-issue path defers to
    :func:`_tip_is_a_checkpoint`, while merged-ness does not: a merged branch's
    Checkpoint is already in base, and collecting it loses nothing.
    """
    if residue.stage:
        return True
    try:
        if git.is_merged_into(residue.branch, base_branch):
            return True
    except git_module.GitError:
        return False
    if github is None:
        return False
    if residue.issue not in closed:
        try:
            closed[residue.issue] = github.issue_view(residue.issue).state == "CLOSED"
        except GhError:
            closed[residue.issue] = False
    if not closed[residue.issue]:
        return False
    return not _tip_is_a_checkpoint(git, residue.branch)


def _collect_resolved_branches(
    *,
    git: git_module.GitClient,
    github: GitHubClient | None,
    base_branch: str,
    withheld: set[str],
    dry_run: bool,
    liveness: Callable[[str], bool | None],
) -> list[str]:
    """Delete every reserved branch of a dead Run that is resolved.

    ``withheld`` names the branches the workspace pass has taken off the table,
    which is checked before anything is asked about the branch itself: a branch
    holding unseen work is not a question about resolution.
    """
    collected: list[str] = []
    closed: dict[int, bool] = {}
    for branch in git.list_branches():
        residue = _parse_residue(branch)
        if residue is None or branch in withheld:
            continue
        if liveness(residue.run_id) is not False:
            continue
        if not _is_resolved(
            residue, git=git, github=github, base_branch=base_branch, closed=closed
        ):
            continue
        if not dry_run:
            try:
                git.delete_branch(branch)
            except git_module.GitError:
                continue
        collected.append(branch)
    return collected


def _list_worktrees(git: git_module.GitClient) -> list[git_module.Worktree]:
    """Every worktree of this clone, or none at all if git cannot say.

    Read once per sweep and shared by the liveness search and the workspace
    pass, so the two cannot disagree about what exists.
    """
    try:
        return git.list_worktrees()
    except git_module.GitError:
        return []


def sweep(
    *,
    git: git_module.GitClient,
    github: GitHubClient | None,
    control_dir: Path,
    base_branch: str,
    dry_run: bool,
    liveness: Callable[[Path], bool | None] = is_run_alive,
) -> SweepReport:
    """Reclaim the residue of every Run that can be proven dead.

    Three passes in the only order that is safe: workspaces first, because it
    is the workspace pass that discovers which branches hold work nobody has
    seen; branches second; and empty directories last, so the parents of
    everything just reclaimed are reaped in the same sweep.

    Args:
        git: The clone to sweep. Its worktree listing supplies the candidates
            and its branch listing the reserved namespace.
        github: Resolves whether an unmerged Lane branch's issue has closed, or
            ``None`` offline — which only ever collects *less*.
        control_dir: This clone's own per-Run control artifacts. Its shape also
            names where to look in every *other* registered worktree
            (:func:`_control_dirs`), because the namespace being swept spans the
            whole clone. A Run whose artifact is unlocked or absent everywhere
            is dead, and only its residue is touched.
        base_branch: The branch this clone publishes to
            (:func:`resolve_base_ref`), which Lane branches are resolved
            against.
        dry_run: Report the identical plan and perform none of it.
        liveness: The lock oracle, injectable for tests.

    Returns:
        The :class:`SweepReport` of what was removed, or would be.
    """
    worktrees = _list_worktrees(git)
    run_liveness = _liveness_by_run(_control_dirs(control_dir, worktrees), liveness)
    reclaimed_paths, withheld = _reclaim_dead_workspaces(
        git=git, worktrees=worktrees, dry_run=dry_run, liveness=run_liveness
    )
    branches = _collect_resolved_branches(
        git=git,
        github=github,
        base_branch=base_branch,
        withheld=withheld,
        dry_run=dry_run,
        liveness=run_liveness,
    )
    directories: list[Path] = []
    reclaimed = frozenset(reclaimed_paths)
    for root in (
        git.common_git_dir() / "git-loopy",
        git.root.parent / f"{git.root.name}.worktrees",
    ):
        _reap_empty_directories(
            root, dry_run=dry_run, reaped=directories, reclaimed=reclaimed
        )

    return SweepReport(
        worktrees=tuple(reclaimed_paths),
        branches=tuple(branches),
        directories=tuple(directories),
    )
