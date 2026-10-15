"""Launch the pinned Planner in the engineer's own harness, outside a Run."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import settings, skill_install
from .agent_profiles import AgentProfile, load_planning_profile
from .config import resolve_iteration_model
from .skill_source import read_skill_source_pin

_AGENT_FILE = "git-loopy-planner.agent.md"
_REFUSED_ARGUMENTS = frozenset({
    "--agent", "--interactive", "--prompt", "--fleet", "--resume", "--continue",
    "--model", "--reasoning-effort", "--effort", "--context",
})
# Copilot bundles its valueless short switches before value-taking ones.
_REFUSED_SHORT_ARGUMENT = re.compile(r"^-[svh]*[ipr]")


def run_plan(args: argparse.Namespace, *, passthrough: list[str]) -> int:
    """Refresh, resolve and exec; no Run, SDK session or repository write."""
    from .cli import build_parser, resolve_config, resolve_repo_root

    def warn(message: str) -> None:
        print(f"git-loopy: warning: {message}", file=sys.stderr)

    try:
        for argument in passthrough:
            if (
                argument.partition("=")[0] in _REFUSED_ARGUMENTS
                or _REFUSED_SHORT_ARGUMENT.match(argument)
            ):
                raise ValueError(
                    f"refused Copilot pass-through argument {argument!r}: "
                    "plan owns the agent, prompt, interactive session and Routing."
                )
        outcome = skill_install.refresh_installed_catalog(env=os.environ)
        if outcome.warning:
            warn(outcome.warning)
        status = skill_install.inspect_installed_catalog(read_skill_source_pin(), os.environ)
        if status.state != "matching" or status.profiles_state != "matching":
            raise skill_install.SkillInstallError(
                f"Agent profiles at {status.profile_root} are not matching the pin "
                f"({status.profiles_state}); repair with `git-loopy doctor --apply`."
            )
        profile = load_planning_profile(status.profile_root, skills=outcome.catalog.root)
        repo = resolve_repo_root()
        tables = settings.load_configs(repo, os.environ)
        flags = build_parser().parse_args([])
        flags.model = args.model
        flags.reasoning_effort = args.reasoning_effort
        flags.context_tier = args.context_tier
        resolved = resolve_config(
            flags, os.environ, project=tables.project, global_=tables.global_,
            measured=tables.measured, measured_provisional=tables.measured_provisional,
            warn=warn,
        )
        route = resolve_iteration_model(resolved.run, ("task-type:planning",), warn=warn)
        for warning in route.gate_warnings:
            warn(str(warning))
        context_named = (
            resolved.run.context_tier_override
            or tables.project.get("context_tier") is not None
            or tables.global_.get("context_tier") is not None
        )
        _refuse_shadowing(repo)
        executable = _checked_harness(warn)
        root = _build_root(profile, skills=outcome.catalog.root)
        argv = [executable, "--add-dir", str(root), "--agent", "git-loopy-planner"]
        for flag, value in (
            ("--model", route.model),
            ("--reasoning-effort", route.reasoning_effort),
            ("--context", route.context_tier if context_named else None),
        ):
            if value is not None:
                argv.extend((flag, value))
        prompt = "/to-tickets"
        if args.planning_document:
            prompt += " " + args.planning_document
        argv.extend(("-i", prompt, *passthrough))
        if sys.platform == "win32":
            # Windows exec creates a different PID. Keep this root's owner
            # waiting instead, so concurrent launches cannot prune live files.
            return subprocess.call(argv)
        os.execv(executable, argv)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"git-loopy plan: error: {exc}", file=sys.stderr)
        return 1


def _refuse_shadowing(repo: Path) -> None:
    home = os.environ.get("COPILOT_HOME", "").strip()
    personal = Path(home) if home else Path.home() / ".copilot"
    candidates = [personal / "agents" / _AGENT_FILE]
    directory = Path.cwd()
    while True:
        candidates.extend(
            directory / source / "agents" / _AGENT_FILE for source in (".github", ".claude")
        )
        if directory == repo or directory == directory.parent:
            break
        directory = directory.parent
    for path in candidates:
        if path.exists() or path.is_symlink():
            raise RuntimeError(
                f"Planner is shadowed by {path}; remove or rename that agent before planning."
            )


def _checked_harness(warn: Callable[[str], None]) -> str:
    executable = shutil.which("copilot")
    if executable is None:
        raise RuntimeError("copilot is missing from PATH; install Copilot CLI >= 1.0.96.")
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        warn(f"could not read copilot version at {executable}: {exc}")
        return executable
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout)
    if result.returncode != 0 or match is None:
        warn(
            f"could not read copilot version at {executable} "
            f"(exit {result.returncode}): {(result.stderr or result.stdout).strip()}"
        )
    elif tuple(int(part) for part in match.groups()) < (1, 0, 96):
        raise RuntimeError(
            f"copilot {match.group(0)} at {executable} is older than the required 1.0.96; "
            "upgrade Copilot CLI before planning."
        )
    return executable


def _build_root(profile: AgentProfile, *, skills: Path) -> Path:
    parent = settings.global_dir(os.environ) / "planner"
    if parent.is_symlink():
        raise ValueError(f"Planner root directory must not be a symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    for previous in parent.iterdir():
        match = re.fullmatch(r"([1-9][0-9]*)|\.(?P<staged>[1-9][0-9]*)-.+", previous.name)
        if match is None:
            continue
        owner = int(match.group(1) or match.group("staged"))
        # This process has not exec'd yet, so its own old root can only be a
        # leftover from a previous use of the PID.
        if owner == pid or not _process_alive(owner):
            try:
                if previous.is_symlink():
                    previous.unlink()
                elif previous.is_dir():
                    shutil.rmtree(previous)
            except FileNotFoundError:
                pass  # Another launcher pruned the same dead root.
    root = parent / str(pid)
    staged = Path(tempfile.mkdtemp(prefix=f".{pid}-", dir=parent))
    try:
        agents = staged / ".github/agents"
        agents.mkdir(parents=True)
        (agents / _AGENT_FILE).write_text(
            "---\nname: Planner\nuser-invocable: true\ndisable-model-invocation: true\n"
            "skills:\n" + "".join(f"  - {name}\n" for name in profile.required)
            + "---\n\n" + profile.instructions + "\n",
            encoding="utf-8",
        )
        exposed = staged / ".github/skills"
        exposed.mkdir()
        for name in sorted(set(profile.required) | set(profile.permitted)):
            shutil.copytree(skills / name, exposed / name)
        staged.rename(root)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return root


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) on Windows terminates rather than probes a process.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
                return False
            if error == 5:  # Access denied is not proof that a process is gone.
                return True
            raise ctypes.WinError(error)
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            if state not in (0, 258):  # WAIT_OBJECT_0 / WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
            return state == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
