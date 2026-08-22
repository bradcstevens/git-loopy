"""Tests for ``git_loopy.run_control`` (issue #446).

Covers every acceptance criterion from the ticket:

* A Run creates its control artifact at start and holds the lock until
  the process exits.
* A second process determines liveness from the lock alone — no pid, no
  heartbeat, no polling interval.
* The lock is released by the OS on abnormal termination, including an
  uncatchable ``SIGKILL`` (subprocess-driven).
* Two Runs in the same repository each hold their own artifact and
  neither reports the other as dead.
* A stale artifact from a dead Run is distinguishable from a live one,
  and reading liveness never mutates the artifact (mtime unchanged).
* The artifact lives beside the trace and is not tracked by git (covered
  indirectly — same directory as the event log, which ``.gitignore``
  already covers).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from git_loopy.run_control import (
    LOCKING_SUPPORTED,
    control_artifact_path,
    open_run_control,
    probe_liveness,
)

_FIXED_RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_FIXED_TS = datetime(2026, 5, 16, 3, 14, 15, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(
    not LOCKING_SUPPORTED,
    reason="advisory locking not available on this platform",
)


def test_open_run_control_creates_artifact_at_path(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".git-loopy" / "logs"
    stem = f"2026-05-16T03-14-15Z-{_FIXED_RUN_ID}"
    handle = open_run_control(logs_dir, stem, run_id=_FIXED_RUN_ID, started_at=_FIXED_TS)
    try:
        assert handle.path == control_artifact_path(logs_dir, stem)
        assert handle.path.exists()
        assert handle.path.parent == logs_dir  # beside the trace, same dir
    finally:
        handle.close()


def test_open_run_control_holds_lock_while_open(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".git-loopy" / "logs"
    stem = "stem-a"
    handle = open_run_control(logs_dir, stem, run_id=_FIXED_RUN_ID, started_at=_FIXED_TS)
    try:
        assert handle.locked is True
        assert probe_liveness(handle.path) is True
    finally:
        handle.close()


def test_close_releases_lock(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".git-loopy" / "logs"
    stem = "stem-close"
    handle = open_run_control(logs_dir, stem, run_id=_FIXED_RUN_ID, started_at=_FIXED_TS)
    assert probe_liveness(handle.path) is True
    handle.close()
    assert probe_liveness(handle.path) is False


def test_close_is_idempotent(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".git-loopy" / "logs"
    handle = open_run_control(
        logs_dir, "stem-idem", run_id=_FIXED_RUN_ID, started_at=_FIXED_TS
    )
    handle.close()
    handle.close()  # must not raise


def test_probe_liveness_false_for_missing_artifact(tmp_path: Path) -> None:
    missing = tmp_path / ".git-loopy" / "logs" / "no-such.control.lock"
    assert probe_liveness(missing) is False


def test_probe_liveness_never_mutates_artifact(tmp_path: Path) -> None:
    """Reading liveness must not bump mtime, size, or content."""
    logs_dir = tmp_path / ".git-loopy" / "logs"
    handle = open_run_control(
        logs_dir, "stem-immutable", run_id=_FIXED_RUN_ID, started_at=_FIXED_TS
    )
    try:
        before_stat = handle.path.stat()
        before_bytes = handle.path.read_bytes()
        for _ in range(3):
            probe_liveness(handle.path)
        after_stat = handle.path.stat()
        after_bytes = handle.path.read_bytes()
        assert before_stat.st_mtime == after_stat.st_mtime
        assert before_stat.st_size == after_stat.st_size
        assert before_bytes == after_bytes
    finally:
        handle.close()


def test_two_runs_each_hold_own_artifact_independently(tmp_path: Path) -> None:
    """Two Runs in the same repository: neither reports the other as dead."""
    logs_dir = tmp_path / ".git-loopy" / "logs"
    h1 = open_run_control(logs_dir, "stem-run-1", run_id=_FIXED_RUN_ID, started_at=_FIXED_TS)
    h2 = open_run_control(
        logs_dir, "stem-run-2", run_id="01BRZ3NDEKTSV4RRFFQ69G5FAV", started_at=_FIXED_TS
    )
    try:
        assert probe_liveness(h1.path) is True
        assert probe_liveness(h2.path) is True
        h1.close()
        assert probe_liveness(h1.path) is False
        assert probe_liveness(h2.path) is True  # unaffected by h1's death
    finally:
        h2.close()


def test_artifact_content_is_forensic_json_not_liveness_bearing(tmp_path: Path) -> None:
    """The file's own content (run_id/started_at/pid) is metadata only —
    liveness is answered by the lock, never by parsing this JSON."""
    logs_dir = tmp_path / ".git-loopy" / "logs"
    handle = open_run_control(
        logs_dir, "stem-content", run_id=_FIXED_RUN_ID, started_at=_FIXED_TS
    )
    try:
        payload = json.loads(handle.path.read_bytes())
        assert payload["run_id"] == _FIXED_RUN_ID
        assert payload["started_at"] == "2026-05-16T03:14:15.000Z"
        assert payload["pid"] == os.getpid()
    finally:
        handle.close()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL is POSIX-only")
def test_lock_released_on_sigkill(tmp_path: Path) -> None:
    """Acceptance criterion: kill a Run uncatchably (SIGKILL) and observe
    the lock free — verified with a real child process, since SIGKILL
    cannot be trapped or cleaned up after from inside the victim."""
    logs_dir = tmp_path / ".git-loopy" / "logs"
    logs_dir.mkdir(parents=True)
    artifact = logs_dir / "stem-kill.control.lock"

    repo_python_dir = Path(__file__).resolve().parents[1]
    child_script = tmp_path / "hold_lock.py"
    child_script.write_text(
        textwrap.dedent(
            f"""
            import sys
            import time
            from datetime import datetime, timezone
            from pathlib import Path

            sys.path.insert(0, {str(repo_python_dir)!r})
            from git_loopy.run_control import open_run_control

            handle = open_run_control(
                Path({str(logs_dir)!r}),
                "stem-kill",
                run_id={_FIXED_RUN_ID!r},
                started_at=datetime.now(timezone.utc),
            )
            print("locked", flush=True)
            time.sleep(30)
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(child_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready_line = proc.stdout.readline()
        assert ready_line.strip() == "locked"
        assert probe_liveness(artifact) is True
        proc.kill()  # SIGKILL — uncatchable, no cleanup runs in the child
        proc.wait(timeout=10)
        assert probe_liveness(artifact) is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

