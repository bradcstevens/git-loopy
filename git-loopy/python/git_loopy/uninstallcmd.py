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
class _Removal:
    """One filesystem path this command owns and may remove."""

    description: str
    path: Path


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
    removals = _machine_removals(environ)
    if all_ and repo_root is not None:
        removals.extend(_project_removals(repo_root))
    unsafe = next((removal for removal in removals if _has_symlink_ancestor(removal)), None)
    if unsafe is not None:
        output_fn(
            f"Left git-loopy unchanged: {unsafe.path} passes through a symbolic link, "
            "so it is not a removable installation path."
        )
        return 1
    protected = _preserved_paths(
        repo_root=repo_root,
        all_=all_,
        executable=Path(inventory.executable),
    )
    overlap = next(
        (
            (removal, path)
            for removal in removals
            for path in protected
            if _would_remove(removal.path, path)
        ),
        None,
    )
    if overlap is not None:
        removal, path = overlap
        output_fn(
            f"Left git-loopy unchanged: removing {removal.path} would also remove "
            f"the preserved path {path}."
        )
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
        output_fn(
            f"Will remove executable through the {inventory.install_channel.name} "
            f"Install channel: {inventory.executable}"
        )
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
    unsafe = next((removal for removal in removals if _has_symlink_ancestor(removal)), None)
    if unsafe is not None:
        output_fn(
            f"Left git-loopy unchanged: {unsafe.path} changed to pass through a "
            "symbolic link after confirmation."
        )
        return 1

    if repo_root is not None:
        with hold_uninstall_lock(repo_root) as locked:
            if not locked:
                output_fn(
                    "Left git-loopy unchanged: a Run started during confirmation. "
                    "Run `git-loopy sweep` after it has stopped."
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
    command: Sequence[str] | None,
    removals: Sequence[_Removal],
    channel_uninstaller: Callable[[Sequence[str]], None] | None,
    output_fn: Callable[[str], None],
) -> int:
    """Run the already-confirmed plan after its final liveness coordination."""
    succeeded = True
    if command is not None:
        try:
            (channel_uninstaller or _run_channel_uninstaller)(command)
        except (OSError, subprocess.CalledProcessError) as exc:
            output_fn(f"Could not remove the executable through its Install channel: {exc}")
            succeeded = False
    else:
        succeeded = False

    for removal in removals:
        try:
            if _has_symlink_ancestor(removal):
                raise OSError("path changed to pass through a symbolic link")
            _remove_path(removal.path)
        except OSError as exc:
            output_fn(f"Could not remove {removal.description} ({removal.path}): {exc}")
            succeeded = False
    return 0 if succeeded else 1


def find_live_lanes(repo_root: Path) -> tuple[Path, ...]:
    """Return the clone's reserved worktrees whose owning Run still holds a lock."""
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
            is_run_alive(control)
            for control_dir in control_dirs
            for control in control_dir.glob(f"*-{run_id}.control")
        ]
        if any(state is True for state in states):
            live.append(worktree.path)
        elif any(state is None for state in states):
            raise OSError(f"Lane liveness is unavailable for {worktree.path}")
    return tuple(live)


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


def _uninstall_command(inventory: installation.Installation) -> tuple[str, ...] | None:
    """Name the sole package manager command proven to own this executable."""
    if not inventory.install_channel.proven:
        return None
    commands = {
        "uv-tool": ("uv", "tool", "uninstall", "git-loopy"),
        "homebrew": ("brew", "uninstall", "git-loopy"),
        "installer-launcher": ("rm", "-f", inventory.executable),
    }
    return commands.get(inventory.install_channel.name)


def _preserved_paths(
    *,
    repo_root: Path | None,
    all_: bool,
    executable: Path,
) -> tuple[Path, ...]:
    """Name present paths this invocation must not remove through a parent."""
    preserved = [] if repo_root is None or all_ else [
        removal.path for removal in _project_removals(repo_root)
    ]
    preserved.append(executable)
    return tuple(preserved)


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
        _Removal(
            "installed Skill catalog", skill_install.installed_catalog_dir(env)
        ),
        _Removal(
            "installed Skill catalog record",
            skill_install.install_record_path(env),
        ),
        *(
            _Removal("TUI helper", path)
            for path in tui_release.machine_local_helper_paths(env)
        ),
        _Removal("global config-home", scope),
    )
    return [removal for removal in candidates if removal.path.exists() or removal.path.is_symlink()]


def _project_removals(repo_root: Path) -> list[_Removal]:
    """Plan only the two tracked project assets and the Run logs."""
    candidates = (
        _Removal("project Config", repo_root / "git-loopy" / "config.toml"),
        _Removal("project PROMPT.md", repo_root / "git-loopy" / "PROMPT.md"),
        _Removal("Run logs", repo_root / ".git-loopy" / "logs"),
    )
    return [removal for removal in candidates if removal.path.exists() or removal.path.is_symlink()]


def _report_preserved_project_scope(
    repo_root: Path, output_fn: Callable[[str], None]
) -> None:
    """Name repository contents deliberately excluded from the default plan."""
    for removal in _project_removals(repo_root):
        output_fn(f"Will keep {removal.description}: {removal.path}")


def _run_channel_uninstaller(command: Sequence[str]) -> None:
    """Delegate deletion of the executable to the package manager that owns it."""
    subprocess.run(list(command), check=True)


def _has_symlink_ancestor(removal: _Removal) -> bool:
    """Reject a planned path that resolves through a symbolic-link ancestor."""
    path = Path(os.path.abspath(removal.path))
    while True:
        if path.is_symlink():
            return True
        if path.parent == path:
            return False
        path = path.parent


def _would_remove(removal: Path, protected: Path) -> bool:
    """Whether recursive removal of ``removal`` would include ``protected``."""
    try:
        protected.resolve().relative_to(removal.resolve())
    except ValueError:
        return False
    return True


def _remove_path(path: Path) -> None:
    """Delete exactly one planned path without following a symbolic link."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
