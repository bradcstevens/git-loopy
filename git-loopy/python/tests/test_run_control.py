"""Cross-process liveness tests for a Run's control artifact."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from git_loopy.run_control import (
    RunControlArtifact,
    advisory_locking_available,
    hold_uninstall_lock,
    is_run_alive,
)

pytestmark = pytest.mark.skipif(
    not advisory_locking_available(),
    reason="this platform has no flock advisory locks",
)


def _probe_liveness(path: Path) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path

        from git_loopy.run_control import is_run_alive

        print(is_run_alive(Path({str(path)!r})))
        """
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_control_artifact_liveness_is_an_advisory_lock_not_process_metadata(
    tmp_path: Path,
) -> None:
    """A live Run reads alive; the same stale artifact reads dead after release."""
    trace_path = tmp_path / "logs" / "run.jsonl"
    control = RunControlArtifact.acquire(trace_path)

    assert control.path == tmp_path / "logs" / "run.control"
    assert control.path.exists()
    assert _probe_liveness(control.path).stdout == "True\n"

    before = control.path.stat()
    assert is_run_alive(control.path) is True
    after = control.path.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)

    control.close()

    assert control.path.exists()
    assert _probe_liveness(control.path).stdout == "False\n"


def test_each_run_holds_only_its_own_control_artifact(tmp_path: Path) -> None:
    """Two live Runs do not mistake the other's independently held lock for death."""
    first = RunControlArtifact.acquire(tmp_path / "first.jsonl")
    second = RunControlArtifact.acquire(tmp_path / "second.jsonl")
    try:
        assert first.path != second.path
        assert is_run_alive(first.path) is True
        assert is_run_alive(second.path) is True
    finally:
        first.close()
        second.close()


def test_liveness_readers_do_not_make_a_stale_artifact_read_alive(
    tmp_path: Path,
) -> None:
    """Shared probes coexist, so a reader never impersonates a live Run."""
    control = RunControlArtifact.acquire(tmp_path / "run.jsonl")
    control.close()
    script = textwrap.dedent(
        f"""
        import time
        from fcntl import LOCK_SH, flock

        handle = open({str(control.path)!r}, "r")
        flock(handle.fileno(), LOCK_SH)
        print("ready", flush=True)
        time.sleep(60)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        assert process.stdout.readline() == "ready\n"
        assert is_run_alive(control.path) is False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_sigkill_releases_the_control_lock_but_preserves_the_artifact(
    tmp_path: Path,
) -> None:
    """The OS, rather than a pid or heartbeat, makes a killed Run read dead."""
    trace_path = tmp_path / "logs" / "run.jsonl"
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path

        from git_loopy.run_control import RunControlArtifact

        control = RunControlArtifact.acquire(Path({str(trace_path)!r}))
        print(control.path, flush=True)
        sys.stdout.flush()
        __import__("time").sleep(60)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    control_path = Path(process.stdout.readline().strip())
    try:
        assert control_path.exists()
        assert is_run_alive(control_path) is True
        process.kill()
        process.wait(timeout=5)
        assert control_path.exists()
        assert is_run_alive(control_path) is False
    finally:
        if process.poll() is None:
            os.kill(process.pid, 9)
            process.wait(timeout=5)


def test_a_run_starts_even_when_the_repository_lock_cannot_be_taken(
    tmp_path: Path,
) -> None:
    """Uninstall coordination is best-effort; it may never fail a Run's start."""
    repo = tmp_path / "repo"
    (repo / ".git-loopy" / "logs").mkdir(parents=True)
    repo.chmod(0o311)
    try:
        control = RunControlArtifact.acquire(repo / ".git-loopy" / "logs" / "run.jsonl")
    finally:
        repo.chmod(0o755)
    try:
        assert is_run_alive(control.path) is True
    finally:
        control.close()


def test_a_run_does_not_block_behind_an_uninstall_holding_the_repository(
    tmp_path: Path,
) -> None:
    """A Run wedged with no output is worse than one that coordinates nothing."""
    repo = tmp_path / "repo"
    (repo / ".git-loopy" / "logs").mkdir(parents=True)
    with hold_uninstall_lock(repo) as locked:
        assert locked is True
        control = RunControlArtifact.acquire(
            repo / ".git-loopy" / "logs" / "run.jsonl"
        )
        control.close()


def test_an_unopenable_repository_refuses_the_uninstall_lock(tmp_path: Path) -> None:
    """A lock it cannot even attempt is reported, not raised at the caller."""
    with hold_uninstall_lock(tmp_path / "absent") as locked:
        assert locked is False


def test_a_filesystem_without_locks_is_not_mistaken_for_a_running_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount that cannot flock is a host without locks, not a busy repository."""
    import fcntl

    from git_loopy import run_control

    def unsupported(_fd: int, _operation: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(run_control._fcntl, "flock", unsupported)

    with hold_uninstall_lock(tmp_path) as locked:
        assert locked is True

    assert fcntl is run_control._fcntl


def test_a_busy_repository_still_refuses_the_uninstall_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contention keeps its distinct answer once unsupported locks are excused."""
    from git_loopy import run_control

    def contended(_fd: int, _operation: int) -> None:
        raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

    monkeypatch.setattr(run_control._fcntl, "flock", contended)

    with hold_uninstall_lock(tmp_path) as locked:
        assert locked is False
