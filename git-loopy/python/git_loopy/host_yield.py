"""The local Run's inherited CPU scheduling policy."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Literal


_APPLIED_PID = "_GIT_LOOPY_HOST_YIELD_PID"

#: What a Run may disclose about the **Host yield policy** it applied (§12).
#: Defined here, beside the code that decides it, so the Event payload and the
#: decision cannot name two different vocabularies.
HostYieldState = Literal["utility", "none", "unsupported"]


class HostYieldError(RuntimeError):
    """The requested Host yield policy could not be applied."""


def platform_declares_a_policy() -> bool:
    """Whether this platform has a **Host yield policy** at all (§4).

    macOS is the only one today. Named once because four decisions turn on it —
    the Config default, the launch clamp, the line-printer re-exec, and the
    disclosure — and a fifth that re-spelled the check would drift from them
    silently.
    """
    return sys.platform == "darwin"


def default_host_yield() -> str:
    return "utility" if platform_declares_a_policy() else "none"


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
    if not platform_declares_a_policy() or policy == "none":
        return argv
    executable = shutil.which("taskpolicy")
    if executable is None:
        raise HostYieldError(
            "host_yield='utility' requires macOS taskpolicy on PATH; restore it "
            "or explicitly opt out with GIT_LOOPY_HOST_YIELD=none"
        )
    return [executable, "-c", "utility", *argv]


def host_yield_disclosure(policy: str) -> HostYieldState:
    """Which **Host yield policy** this Run actually applied (#817).

    The Run-start disclosure, and deliberately not the resolved Config value.
    A host with no declared policy discloses ``unsupported`` rather than the
    ``none`` its Config resolves to: ``none`` is an operator's opt-out, and a
    platform that never offered the choice has not opted out of anything.
    Reading the two as one would tell an operator their clamp was declined on
    a host that could never have applied it.
    """
    policy = parse_host_yield(policy)
    if not platform_declares_a_policy():
        return "unsupported"
    return "utility" if policy == "utility" else "none"


def enter_line_printer_policy(policy: str, argv: list[str]) -> None:
    """Replace this process, preserving its PID and inherited stdio."""
    applied_pid = os.environ.pop(_APPLIED_PID, None)
    if not platform_declares_a_policy() or policy == "none":
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
