"""Shared teardown for bounded commands started in a private process session."""

from __future__ import annotations

import os
import signal
import subprocess


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _release_after_failed_drain(
    process: subprocess.Popen[str], grace_seconds: float
) -> None:
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
    try:
        process.wait(timeout=grace_seconds)
    except (subprocess.TimeoutExpired, OSError):
        pass


def kill_and_release(
    process: subprocess.Popen[str], *, grace_seconds: float = 5.0
) -> str:
    """Kill a command and drain its pipes, with bounded cleanup as well.

    On POSIX the caller must start a new session so the kill covers children
    holding the same pipes. Elsewhere only the direct child can be killed.
    A descendant escaping the session can keep those pipes open indefinitely;
    cap the drain and close our ends rather than hanging the Run on cleanup.
    """
    _kill_process_group(process)
    try:
        stdout, stderr = process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _release_after_failed_drain(process, grace_seconds)
        return ""
    return (stdout or "") + (stderr or "")
