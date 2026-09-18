"""Per-Run control artifacts and their advisory-lock liveness oracle.

The artifact is deliberately content-free: its OS-held lock is the complete
liveness record. A released lock therefore makes a preserved artifact a
readable record of a dead Run without a stale pid or a timed-out heartbeat.
"""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Iterator, TextIO

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on platforms without flock.
    _fcntl = None

__all__ = [
    "RunControlArtifact",
    "advisory_locking_available",
    "control_path_for_trace",
    "hold_uninstall_lock",
    "is_run_alive",
]


def advisory_locking_available() -> bool:
    """Whether this distribution can make the control artifact authoritative."""
    return _fcntl is not None


def control_path_for_trace(trace_path: Path) -> Path:
    """Return the per-Run control artifact beside ``trace_path``."""
    return trace_path.with_suffix(".control")


@dataclass
class RunControlArtifact:
    """One Run's lock-held control artifact.

    The file remains after :meth:`close`; liveness is a property of its lock,
    not of file existence. Platforms without ``flock`` keep the trace-side
    artifact but intentionally expose trace-only liveness through ``None``.
    """

    path: Path
    _handle: TextIO
    _repository_fd: int | None = None

    @classmethod
    def acquire(cls, trace_path: Path) -> "RunControlArtifact":
        """Create and exclusively lock the artifact alongside ``trace_path``."""
        path = control_path_for_trace(trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
        try:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            repository_fd = _hold_run_lock(trace_path)
        except OSError:
            handle.close()
            raise
        return cls(path=path, _handle=handle, _repository_fd=repository_fd)

    def close(self) -> None:
        """Release the lock and close the descriptor; safe to call repeatedly."""
        if self._handle.closed:
            return
        try:
            if _fcntl is not None:
                _fcntl.flock(self._handle.fileno(), _fcntl.LOCK_UN)
        finally:
            self._handle.close()
            _release_repository_lock(self._repository_fd)
            self._repository_fd = None

    def __enter__(self) -> "RunControlArtifact":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def is_run_alive(control_path: Path) -> bool | None:
    """Read liveness from ``control_path`` without changing the artifact.

    ``True`` means another process holds its advisory lock; ``False`` means the
    lock is free (or the artifact is absent). ``None`` is an explicit trace-only
    result on a platform that does not provide ``flock``.
    """
    if _fcntl is None:
        return None
    try:
        handle = control_path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return False

    with handle:
        try:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH | _fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return True
            raise
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return False


@contextmanager
def hold_uninstall_lock(repo_root: Path) -> Iterator[bool]:
    """Prevent a Run from becoming live while uninstall removes its state.

    A directory descriptor gives the Run and uninstall one advisory-lock target
    without writing another untracked file into the consuming repository.
    """
    if _fcntl is None:
        yield True
        return
    fd = os.open(repo_root, os.O_RDONLY)
    acquired = False
    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            yield False
            return
        acquired = True
        yield True
    finally:
        if acquired:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)


def _hold_run_lock(trace_path: Path) -> int | None:
    """Hold the repository's shared lifecycle lock for a normal Run."""
    if _fcntl is None:
        return None
    root = _repository_root_for_trace(trace_path)
    if root is None:
        return None
    fd = os.open(root, os.O_RDONLY)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_SH)
    except OSError:
        os.close(fd)
        raise
    return fd


def _release_repository_lock(fd: int | None) -> None:
    """Release a shared Run lifecycle lock if this artifact held one."""
    if fd is None:
        return
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _repository_root_for_trace(trace_path: Path) -> Path | None:
    """Recover the repository root only from the standard Run-log location."""
    logs = trace_path.parent
    control = logs.parent
    if logs.name != "logs" or control.name != ".git-loopy":
        return None
    return control.parent
