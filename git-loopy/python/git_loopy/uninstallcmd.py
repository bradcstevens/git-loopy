"""Remove the machine-local state owned by one git-loopy installation."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from git_loopy import installation, skill_install, tui_release
from git_loopy.git import GitError, SubprocessGitClient, reserved_worktrees
from git_loopy.run_control import hold_uninstall_lock, is_run_alive
from git_loopy.settings import global_dir

_RESERVED_BRANCH = re.compile(
    r"^git-loopy/(?P<run_id>[^/]+)(?:/(?:integrate|materialized))?/issue-\d+$"
)
_WINDOWS_COMMAND_CHARACTERS = frozenset("&|<>()%^!\"")


@dataclass(frozen=True)
class _ChannelRemoval:
    """How one proven **Install channel**'s artifact comes off this machine.

    ``residue`` names what the removal deliberately does *not* reach, so an
    exit code never implies more was removed than was.
    """

    command: tuple[str, ...]
    describes: str
    residue: str = ""


@dataclass(frozen=True)
class _Removal:
    """One filesystem path this command owns and may remove.

    ``resolved`` is where the path landed when the plan was *printed*.  It is
    recorded rather than recomputed because a removal is only entitled to run
    against the filesystem the operator confirmed: a link swapped in afterwards
    redirects the same spelling somewhere they never saw.
    """

    description: str
    path: Path
    resolved: Path


def run_uninstall(
    *,
    env: Mapping[str, str] | None = None,
    executable_path: Path | None = None,
    repo_root: Path | None = None,
    all_: bool = False,
    confirm: Callable[[str], bool] | None = None,
    channel_uninstaller: Callable[[Sequence[str]], None] | None = None,
    live_lanes: Callable[[Path], Sequence[Path]] | None = None,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Remove git-loopy state after presenting its complete, bounded plan.

    The executable is only handed to a proven **Install channel**.  All other
    paths are machine-local facts, so an unprovable executable never prevents
    their removal.  A **Lane** is checked before the plan because `--all` can
    remove its Run logs, and any work currently held by a Run must win.
    """
    environ = os.environ if env is None else env
    executable = Path(sys.argv[0]) if executable_path is None else executable_path
    if all_ and repo_root is None:
        output_fn(
            "Left git-loopy unchanged: `uninstall --all` requires a git repository "
            "to identify the project scope and Run logs."
        )
        return 1

    lane_probe = live_lanes or find_live_lanes
    if repo_root is not None and _refuse_live_lanes(repo_root, lane_probe, output_fn):
        return 1

    inventory = installation.inspect_installation(
        env=environ, executable_path=executable
    )
    command = _uninstall_command(inventory)
    machine = _machine_removals(environ)
    project = _project_removals(repo_root) if all_ and repo_root is not None else []
    removals = machine + project
    refusal = _unremovable(
        machine=machine,
        project=project,
        repo_root=repo_root,
        executable=Path(inventory.executable),
    )
    if refusal is not None:
        output_fn(f"Left git-loopy unchanged: {refusal}")
        return 1

    if command is None:
        output_fn(
            f"Will leave executable unchanged: its Install channel cannot be proven "
            f"from {inventory.executable}."
        )
        instruction = _unproven_uninstall_instruction(
            Path(inventory.executable), windows=bool(environ.get("COMSPEC"))
        )
        if instruction is not None:
            output_fn("Remove that executable yourself with:")
            output_fn(f"  {instruction}")
        else:
            output_fn(
                "Its path contains characters cmd.exe would interpret, so "
                "git-loopy will not render a command for it."
            )
    else:
        output_fn(command.describes)
        if command.residue:
            output_fn(command.residue)
    for removal in removals:
        output_fn(f"Will remove {removal.description}: {removal.path}")
    if repo_root is not None and not all_:
        _report_preserved_project_scope(repo_root, output_fn)
    if not removals and command is None:
        return 1
    if confirm is None:
        output_fn("Uninstall needs confirmation; re-run from a terminal and confirm the plan.")
        return 1
    if not confirm("Remove the listed git-loopy installation state?"):
        output_fn("Left git-loopy unchanged: the removal plan was not confirmed.")
        return 1
    if repo_root is not None and _refuse_live_lanes(repo_root, lane_probe, output_fn):
        return 1
    moved = next((removal for removal in removals if _has_moved(removal)), None)
    if moved is not None:
        output_fn(
            f"Left git-loopy unchanged: {moved.path} no longer resolves to "
            f"{moved.resolved}, so the confirmed plan is not the one that would run."
        )
        return 1

    if repo_root is not None:
        with hold_uninstall_lock(repo_root) as locked:
            if not locked:
                output_fn(
                    "Left git-loopy unchanged: could not take this repository's "
                    "lifecycle lock, so a Run may have started during "
                    "confirmation. Run `git-loopy sweep` after it has stopped."
                )
                return 1
            if _refuse_live_lanes(repo_root, lane_probe, output_fn):
                return 1
            return _remove_confirmed_plan(
                command=command,
                removals=removals,
                channel_uninstaller=channel_uninstaller,
                output_fn=output_fn,
            )
    return _remove_confirmed_plan(
        command=command,
        removals=removals,
        channel_uninstaller=channel_uninstaller,
        output_fn=output_fn,
    )


def _remove_confirmed_plan(
    *,
    command: _ChannelRemoval | None,
    removals: Sequence[_Removal],
    channel_uninstaller: Callable[[Sequence[str]], None] | None,
    output_fn: Callable[[str], None],
) -> int:
    """Run the already-confirmed plan after its final liveness coordination."""
    succeeded = True
    if command is not None:
        try:
            (channel_uninstaller or _run_channel_uninstaller)(command.command)
        except (OSError, subprocess.CalledProcessError) as exc:
            output_fn(f"Could not remove the executable through its Install channel: {exc}")
            succeeded = False
    else:
        succeeded = False

    for removal in removals:
        try:
            if _has_moved(removal):
                raise OSError(f"path no longer resolves to {removal.resolved}")
            _remove_path(removal.path)
        except OSError as exc:
            output_fn(f"Could not remove {removal.description} ({removal.path}): {exc}")
            succeeded = False
    return 0 if succeeded else 1


def find_live_lanes(repo_root: Path) -> tuple[Path, ...]:
    """Return the clone's reserved worktrees whose owning Run still holds a lock.

    Liveness composes exactly as a Sweep's does (:func:`git_loopy.sweep`): any
    locked artifact means live, any *unreadable* answer means unknown, and only
    the complete absence of both proves the Run dead.  An absent artifact is
    therefore probed rather than skipped, so a host without advisory locks keeps
    its explicit unknown instead of silently reading every Lane as collectable —
    uninstall removes strictly more than a sweep does and must be at least as
    pessimistic.
    """
    git = SubprocessGitClient(repo_root)
    worktrees = git.list_worktrees()
    control_dirs = tuple(worktree.path / ".git-loopy" / "logs" for worktree in worktrees)
    live: list[Path] = []
    for worktree in reserved_worktrees(worktrees):
        match = _RESERVED_BRANCH.fullmatch(worktree.branch or "")
        if match is None:
            continue
        run_id = match["run_id"]
        states = [
            state
            for control_dir in control_dirs
            for state in _control_states(control_dir, run_id)
        ]
        if any(state is True for state in states):
            live.append(worktree.path)
        elif any(state is None for state in states):
            raise OSError(f"Lane liveness is unavailable for {worktree.path}")
    return tuple(live)


def _control_states(control_dir: Path, run_id: str) -> list[bool | None]:
    """Read every liveness answer one directory holds for one Run."""
    try:
        matches = sorted(control_dir.glob(f"*-{run_id}.control"))
    except OSError:
        return [None]
    if not matches:
        matches = [control_dir / f"{run_id}.control"]
    return [_liveness(path) for path in matches]


def _liveness(control_path: Path) -> bool | None:
    """Read one control artifact, treating an unreadable one as unknown."""
    try:
        return is_run_alive(control_path)
    except OSError:
        return None


def _refuse_live_lanes(
    repo_root: Path,
    lane_probe: Callable[[Path], Sequence[Path]],
    output_fn: Callable[[str], None],
) -> bool:
    """Report a live or unprovable **Lane** before a destructive step."""
    try:
        active_lanes = tuple(lane_probe(repo_root))
    except (GitError, OSError) as exc:
        output_fn(
            f"Left git-loopy unchanged: could not determine whether a Lane is live "
            f"in {repo_root}: {exc}. Run `git-loopy sweep` after resolving it."
        )
        return True
    if not active_lanes:
        return False
    output_fn(
        "Left git-loopy unchanged: a live Lane worktree must be finished or "
        "recovered before uninstall."
    )
    for lane in active_lanes:
        output_fn(f"Live Lane: {lane}")
    output_fn("Run `git-loopy sweep` after the Run has stopped.")
    return True


def _uninstall_command(inventory: installation.Installation) -> _ChannelRemoval | None:
    """Name what may remove this executable, and what that deliberately leaves.

    A package manager owns its artifact end to end, so removing it through the
    channel is the whole story.  The shell installer's launcher is not: it is a
    two-line shim that ``exec``s a clone the operator owns (ADR-0054), and the
    clone is theirs — so this removes exactly the shim, says so rather than
    naming a channel that is not involved, and reports the residue instead of
    letting an exit code imply it is gone.
    """
    if not inventory.install_channel.proven:
        return None
    removals = {
        "uv-tool": _ChannelRemoval(
            command=("uv", "tool", "uninstall", "git-loopy"),
            describes=(
                "Will remove executable through the uv-tool Install channel: "
                f"{inventory.executable}"
            ),
        ),
        "homebrew": _ChannelRemoval(
            command=("brew", "uninstall", "git-loopy"),
            describes=(
                "Will remove executable through the homebrew Install channel: "
                f"{inventory.executable}"
            ),
        ),
        "installer-launcher": _ChannelRemoval(
            command=("rm", "-f", inventory.executable),
            describes=(
                "Will remove the launcher the shell installer placed: "
                f"{inventory.executable}"
            ),
            residue=(
                "Will keep the clone that launcher execs: it is yours, and "
                "removing it is not something uninstall will do over your work."
            ),
        ),
    }
    return removals.get(inventory.install_channel.name)


def _unremovable(
    *,
    machine: Sequence[_Removal],
    project: Sequence[_Removal],
    repo_root: Path | None,
    executable: Path,
) -> str | None:
    """Name the first planned path this command has no standing to remove.

    Four rules, each a sentence of ADR-0054 rather than a filesystem heuristic.
    A **machine** removal that encloses the repository, or sits inside it, is
    editing a repository's contents whatever it is called — and ``--all`` widens
    the plan to three named project paths, never to the directory holding them.
    A **project** removal that lands outside the repository is not that
    repository's project scope.  A planned path that is a link standing in for a
    directory is nobody's to resolve (:func:`_redirects_a_tree`).  And nothing
    may take the executable out through a parent, because only a proven
    **Install channel** removes that.
    """
    if repo_root is not None:
        root = _resolved(repo_root)
        for removal in machine:
            if _encloses(removal.resolved, root):
                return (
                    f"removing the {removal.description} {removal.path} would "
                    f"also remove {repo_root}; uninstall never edits a "
                    "repository's contents."
                )
            if _encloses(root, removal.resolved):
                return (
                    f"the {removal.description} {removal.path} is inside "
                    f"{repo_root}; uninstall never edits a repository's "
                    "contents, so this scope must be removed by hand."
                )
        for removal in project:
            if not _encloses(root, removal.resolved):
                return (
                    f"{removal.path} resolves to {removal.resolved}, outside "
                    f"{repo_root}, so it is not this repository's project scope."
                )
    for removal in (*machine, *project):
        if _redirects_a_tree(removal):
            return (
                f"the {removal.description} {removal.path} is a symbolic link "
                f"to {removal.resolved}. Removing the link would orphan that "
                "directory and following it would delete outside this plan, so "
                "remove it yourself."
            )
    target = _resolved(executable)
    for removal in (*machine, *project):
        if _encloses(removal.resolved, target):
            return (
                f"removing {removal.path} would also remove the executable "
                f"{executable}."
            )
    return None


def _redirects_a_tree(removal: _Removal) -> bool:
    """Whether a planned path is a link standing in for a directory elsewhere.

    Neither answer available to :func:`_remove_path` is an uninstall here.
    Unlinking leaves the directory the link stood for — the very state this
    command reports as removed — while following the link deletes a tree the
    printed plan never named, in a location that is frequently the operator's
    own dotfiles repository.  A link to a *file* has no such asymmetry: the
    link is the whole artifact at that location.
    """
    return removal.path.is_symlink() and removal.path.is_dir()


def _unproven_uninstall_instruction(path: Path, *, windows: bool) -> str | None:
    """Render a path-specific recovery command without guessing a channel."""
    raw = str(path)
    if windows:
        if (
            _WINDOWS_COMMAND_CHARACTERS.intersection(raw)
            or any(ord(character) < 32 for character in raw)
        ):
            return None
        return f"del /f /q {subprocess.list2cmdline([raw])}"
    return f"rm -f -- {shlex.quote(raw)}"


def _machine_removals(env: Mapping[str, str]) -> list[_Removal]:
    """Plan paths in child-first order before their enclosing config home."""
    scope = global_dir(env)
    candidates = (
        _plan("installed Skill catalog", skill_install.installed_catalog_dir(env)),
        _plan(
            "installed Skill catalog record",
            skill_install.install_record_path(env),
        ),
        *(
            _plan("TUI helper", path)
            for path in tui_release.machine_local_helper_paths(env)
        ),
        *(
            _plan(
                "TUI helper resolved-Release record",
                tui_release.helper_release_record_path(path),
            )
            for path in tui_release.machine_local_helper_paths(env)
        ),
        _plan("global config-home", scope),
    )
    return [removal for removal in candidates if _present(removal.path)]


def _project_removals(repo_root: Path) -> list[_Removal]:
    """Plan only the two tracked project assets and the Run logs."""
    candidates = (
        _plan("project Config", repo_root / "git-loopy" / "config.toml"),
        _plan("project PROMPT.md", repo_root / "git-loopy" / "PROMPT.md"),
        _plan("Run logs", repo_root / ".git-loopy" / "logs"),
    )
    return [removal for removal in candidates if _present(removal.path)]


def _plan(description: str, path: Path) -> _Removal:
    """Record one planned path together with where it resolves right now."""
    return _Removal(description=description, path=path, resolved=_resolved(path))


def _present(path: Path) -> bool:
    """Whether a planned path is there to remove, a broken link included."""
    return path.exists() or path.is_symlink()


def _report_preserved_project_scope(
    repo_root: Path, output_fn: Callable[[str], None]
) -> None:
    """Name repository contents deliberately excluded from the default plan."""
    for removal in _project_removals(repo_root):
        output_fn(f"Will keep {removal.description}: {removal.path}")


def _run_channel_uninstaller(command: Sequence[str]) -> None:
    """Delegate deletion of the executable to the package manager that owns it."""
    subprocess.run(list(command), check=True)


def _has_moved(removal: _Removal) -> bool:
    """Whether a planned path stopped landing where the printed plan said."""
    return _resolved(removal.path) != removal.resolved


def _resolved(path: Path) -> Path:
    """Where a path actually lands, following every link on the way to it.

    Resolution is deliberately total: an unreadable link is a path whose
    destination is unknown, which the caller must be able to compare and refuse
    rather than crash on.
    """
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(path))


def _encloses(parent: Path, child: Path) -> bool:
    """Whether recursive removal of ``parent`` would include ``child``."""
    return child == parent or parent in child.parents


def _remove_path(path: Path) -> None:
    """Delete exactly one planned path without following a symbolic link."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
