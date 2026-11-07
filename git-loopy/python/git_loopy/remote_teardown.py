"""Guest-only preservation shared by remote Sweep and the future supervisor.

No Azure identity is needed here. Unlike the supervisor's bounded-failure
policy, this backstop never halts after a failed preservation.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Protocol, Sequence

from git_loopy.git import GitError, RESERVED_BRANCH_RE, SubprocessGitClient
from git_loopy.remote_control import GUEST_REPOSITORY, inspect_run
from git_loopy.run_discovery import RunLiveness, RunTargetError, discover_runs
from git_loopy.wrapper import CHECKPOINT_COMMENT_MARKER, checkpoint_message


class Breadcrumbs(Protocol):
    def issue_comments(self, number: int) -> Sequence[str]: ...
    def issue_comment(self, number: int, comment: str) -> None: ...


def preserve_work(
    repository: Path, run_id: str, *, github: Breadcrumbs,
) -> tuple[str, ...]:
    """Salvage first, then preserve branch tips and issue Breadcrumbs.

    Preserve a conservative superset of local Sweep's retention: *all* heads,
    including Checkpoints and branches ahead of origin. Do not collect branches
    or remove workspaces. A partially successful attempt is safe to retry.
    """
    source = SubprocessGitClient(repository)
    origin = source.remote_url("origin")
    if not origin:
        raise RunTargetError("Preservation requires origin.")
    try:
        advertised = subprocess.run(
            ["git", "-C", str(repository), "ls-remote", "--symref", "origin", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise RunTargetError("Origin's default branch is unprovable.") from None
    base = next((
        line.split()[1] for line in advertised.splitlines()
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD")
    ), None)
    if base is None:
        raise RunTargetError("Origin's default branch is unprovable.")
    # Remote Runs start on this branch. Use its local tip, as local Sweep does,
    # including Integration that completed before the guest lost its connection.
    base_tip = source.branch_tip(base).sha
    worktrees = source.list_worktrees()
    clones = source.list_issue_clones()
    # list_issue_clones deliberately skips unreadable registrations for local
    # reclamation. A whole VM cannot be discarded on a partial inventory.
    registered = subprocess.run(
        ["git", "-C", str(repository), "config", "--null", "--get-all", "git-loopy.issueClone"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if registered.returncode not in (0, 1):
        raise RunTargetError("Cannot enumerate Issue clones.")
    paths = {
        Path(json.loads(row)["path"])
        for row in set(filter(None, registered.stdout.split("\0")))
    }
    if paths != {clone.path for clone in clones}:
        raise RunTargetError("Incomplete Issue clone inventory; refusing Teardown.")
    clients = {source.root.resolve(): source}
    for worktree in [*worktrees, *clones]:
        if worktree.prunable:
            continue
        if worktree.preparing or worktree.path.is_symlink():
            raise RunTargetError("Unproven workspace; refusing Teardown.")
        clients[worktree.path.resolve()] = source.open_worktree(worktree.path)
    for git in clients.values():
        conflicts = subprocess.run(
            ["git", "-C", str(git.root), "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        if conflicts:
            raise RunTargetError("Unresolved merge; refusing to Checkpoint conflict markers.")
        branch = git.current_branch()
        issue = _branch_issue(branch or "")
        if git.is_dirty() or git.has_untracked():
            git.add_all()
            git.commit(checkpoint_message(issue))
    # Linked worktrees share refs: every Salvage must finish before any
    # client's tips or stability snapshot is read.
    tips: dict[tuple[int | None, str], SubprocessGitClient] = {}
    heads: dict[Path, dict[str, str]] = {}
    for git in clients.values():
        # Detached work is also work, even though no branch carries it.
        if git.current_branch() is None:
            tips[(None, git.head_sha())] = git
        for name in git.list_branches():
            sha = git.branch_tip(name).sha
            owner = _branch_issue(name)
            if owner is not None and git.is_merged_into(sha, base_tip):
                owner = None  # Resolved work gets a Run ref, not an unfinished-work comment.
            tips[(owner, sha)] = git
        heads[git.root] = _heads(git)
    preserved = []
    for (issue, sha), git in tips.items():
        if issue is None:
            continue
        ref = f"refs/heads/git-loopy-checkpoints/issue-{issue}/{sha}"
        existing = git.probe_remote_ref(origin, ref)
        if existing != sha:
            if existing is not None or not git.push_ref(origin, ref, sha, None):
                raise RunTargetError("Preservation ref differs; refusing to overwrite it.")
        if git.probe_remote_ref(origin, ref) != sha:
            raise RunTargetError("Preservation push is not confirmed.")
        marker = f"{CHECKPOINT_COMMENT_MARKER}\nCheckpoint preserved at `{ref}`"
        if not any(comment.startswith(marker) for comment in github.issue_comments(issue)):
            github.issue_comment(
                issue,
                f"{marker} on remote `origin` (commit `{sha}`).\n\n"
                "Unfinished work, not published onto base. Inspect explicitly:\n\n"
                f"```sh\ngit fetch origin {ref}\ngit show FETCH_HEAD\n```",
            )
        preserved.append(ref)
    unattributed = {sha: git for (issue, sha), git in tips.items() if issue is None}
    if unattributed:
        preserved.append(_preserve_run(source, origin, run_id, unattributed))
    for git in clients.values():
        if git.is_dirty() or git.has_untracked() or _heads(git) != heads[git.root]:
            raise RunTargetError("Work changed during preservation; refusing guest halt.")
    return tuple(preserved)


def _preserve_run(
    source: SubprocessGitClient, origin: str, run_id: str,
    tips: dict[str, SubprocessGitClient],
) -> str:
    """One append-only carrier retains every tip, including earlier attempts.

    Fetch objects only: neither the guest's heads nor its index may move.
    A retry with no new work reuses the remote carrier. Otherwise its successor
    parents both the old carrier and today's tips, so even vanished local work
    remains reachable. Compare-and-swap refuses competing updates; read-back
    must confirm the candidate before the guest can halt.
    """
    def objects(*args: str, input: str | None = None) -> str:
        return subprocess.run(
            ["git", "-C", str(source.root), *args],
            input=input, capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()

    def fetch(remote: str, sha: str) -> None:
        objects("fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", remote, sha)

    for sha, git in tips.items():
        if git.root != source.root:
            fetch(str(git.root), sha)
    ref = f"refs/heads/git-loopy-checkpoints/run-{run_id}"
    previous = source.probe_remote_ref(origin, ref)
    if previous is not None:
        fetch(origin, previous)
    if previous is not None and all(source.is_merged_into(sha, previous) for sha in tips):
        carrier = previous
    else:
        parents = ([previous] if previous is not None else []) + sorted(tips.keys() - {previous})
        tree = objects("hash-object", "-t", "tree", "-w", "--stdin", input="")
        carrier = objects(
            "commit-tree", tree,
            *(arg for parent in parents for arg in ("-p", parent)),
            "-m", f"Preserved unattributed work for Run {run_id}",
        )
        if not source.push_ref(origin, ref, carrier, previous):
            raise RunTargetError("Run preservation ref changed; retry without overwriting it.")
    if source.probe_remote_ref(origin, ref) != carrier:
        raise RunTargetError("Run preservation push is not confirmed.")
    return ref


def _heads(git: SubprocessGitClient) -> dict[str, str]:
    return {name: git.branch_tip(name).sha for name in ["HEAD", *git.list_branches()]}


def _branch_issue(branch: str) -> int | None:
    match = RESERVED_BRANCH_RE.fullmatch(branch)
    return int(match["issue"]) if match and match["issue"] else None


def teardown_run(
    repository: Path, run_id: str, *, github: Breadcrumbs,
    halt: Callable[[], None] | None = None,
) -> tuple[str, ...]:
    """Re-prove guest death and fence its lock through preservation and halt.

    ``halt=None`` performs preservation alone. The laptop can then arm the
    deletion delay before invoking this same operation with a guest halt.
    """
    import fcntl  # The remote guest is Linux; fail closed elsewhere.

    snapshot = inspect_run(repository, run_id)
    if snapshot.run.liveness is not RunLiveness.DEAD:
        raise RunTargetError("Only a proven-dead remote Run can be preserved.")
    if snapshot.run.control_path.is_symlink():
        raise RunTargetError("Unprovable Run control artifact.")
    with snapshot.run.control_path.open("r") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RunTargetError("Run lock changed; refusing Teardown.") from None
        others = discover_runs(git=SubprocessGitClient(repository))
        if any(
            run.control_path != snapshot.run.control_path and run.liveness is not RunLiveness.DEAD
            for run in others
        ):
            raise RunTargetError("Another Run is live or unprovable; refusing guest halt.")
        refs = preserve_work(repository, run_id, github=github)
        if halt is not None:
            halt()
        return refs


def main() -> int:
    from git_loopy.gh import GhError, SubprocessGitHubClient
    from git_loopy.remote_guest import guest_environment
    from git_loopy.route_publication import RouteDeliveryError

    try:
        request = json.loads(base64.b64decode(sys.argv[1], validate=True))
        state = GUEST_REPOSITORY.parent
        document = json.loads((state / "launch.json").read_text())
        # Execution API processes do not inherit the detached worker's home or
        # proxy-placeholder authentication. Reconstitute only the guest env.
        env = guest_environment(state / "home", document["feeds"])
        os.environ.clear()
        os.environ.update(env)
        os.chdir(GUEST_REPOSITORY)
        action = request["action"]
        if action not in ("preserve", "halt"):
            raise RunTargetError("Unknown Teardown action.")
        refs = teardown_run(
            GUEST_REPOSITORY, request["run_id"], github=SubprocessGitHubClient(),
            halt=(
                lambda: subprocess.run(["/sbin/poweroff", "-f"], check=True, timeout=30)
            ) if action == "halt" else None,
        )
        print(json.dumps({
            "schema_version": 1, "run_id": request["run_id"],
            "preserved": True, "refs": refs,
        }))
        return 0
    except (RunTargetError, GitError, GhError, RouteDeliveryError, OSError, ValueError, KeyError,
            TypeError, IndexError, subprocess.SubprocessError):
        print("Remote preservation failed; the guest was not halted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
