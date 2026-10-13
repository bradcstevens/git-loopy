"""The local Run's inherited CPU scheduling policy."""

from __future__ import annotations

import os
import shutil
import sys


_APPLIED_PID = "_GIT_LOOPY_HOST_YIELD_PID"


class HostYieldError(RuntimeError):
    """The requested Host yield policy could not be applied."""


def default_host_yield() -> str:
    return "utility" if sys.platform == "darwin" else "none"


def parse_host_yield(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() in {"utility", "none"}:
        return value.strip().lower()
    raise ValueError(
        f"host_yield must be 'utility' or 'none' (got {value!r}); "
        "set host_yield = \"none\" or GIT_LOOPY_HOST_YIELD=none to opt out"
    )


def host_yield_command(argv: list[str], policy: str) -> list[str]:
    """Clamp at launch: taskpolicy's -p mode does not apply a QoS clamp."""
    policy = parse_host_yield(policy)
    if sys.platform != "darwin" or policy == "none":
        return argv
    executable = shutil.which("taskpolicy")
    if executable is None:
        raise HostYieldError(
            "host_yield='utility' requires macOS taskpolicy on PATH; restore it "
            "or explicitly opt out with GIT_LOOPY_HOST_YIELD=none"
        )
    return [executable, "-c", "utility", *argv]


def enter_line_printer_policy(policy: str, argv: list[str]) -> None:
    """Replace this process, preserving its PID and inherited stdio."""
    applied_pid = os.environ.pop(_APPLIED_PID, None)
    if sys.platform != "darwin" or policy == "none":
        return
    # exec preserves this PID; an inherited marker cannot exempt a new Run.
    if applied_pid == str(os.getpid()):
        return
    command = host_yield_command(
        [sys.executable, "-m", "git_loopy.cli", *argv], policy
    )
    env = {**os.environ, _APPLIED_PID: str(os.getpid())}
    try:
        os.execvpe(command[0], command, env)
    except OSError as exc:
        raise HostYieldError(
            f"could not apply host_yield='utility': {exc}; restore taskpolicy "
            "or explicitly opt out with GIT_LOOPY_HOST_YIELD=none"
        ) from exc
