"""Per-Run control artifacts and their advisory-lock liveness oracle.

The artifact is deliberately content-free: its OS-held lock is the complete
liveness record. A released lock therefore makes a preserved artifact a
readable record of a dead Run without a stale pid or a timed-out heartbeat.
"""

from __future__ import annotations

import errno
import os
import time
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

#: How long a starting Run offers to wait for an uninstall to finish before it
#: gives up on the lifecycle lock and starts anyway. Short, because a Run that
#: prints nothing is the failure this bound exists to prevent.
_RUN_LOCK_TIMEOUT_SECONDS = 2.0
_RUN_LOCK_POLL_SECONDS = 0.05

#: Errnos that mean this filesystem cannot hold a directory lock at all, rather
#: than that somebody else is holding one. ``ENOTSUP`` is ``EOPNOTSUPP`` on most
#: platforms and is listed defensively for the ones where it is not.
_UNSUPPORTED_LOCK_ERRNOS = frozenset(
    {errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EINVAL, errno.ENOSYS}
)


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
    without writing another untracked file into the consuming repository.  Every
    way of failing to *take* that lock yields ``False`` rather than raising:
    uninstall answers an unavailable lock by refusing, and a caller cannot refuse
    on behalf of a traceback.
    """
    if _fcntl is None:
        yield True
        return
    try:
        fd = os.open(repo_root, os.O_RDONLY)
    except OSError:
        yield False
        return
    acquired = False
    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            yield _locks_unsupported(exc)
            return
        acquired = True
        yield True
    finally:
        if acquired:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)


def _locks_unsupported(exc: OSError) -> bool:
    """Whether this failure says the *mechanism* is absent, not that it is held.

    A host with no ``flock`` at all proceeds uncoordinated, so a mount whose
    ``flock`` cannot work must proceed the same way.  Reporting it as contention
    would refuse every uninstall in that repository forever, and send the
    operator to a `git-loopy sweep` that can never clear it.
    """
    return exc.errno in _UNSUPPORTED_LOCK_ERRNOS


def _hold_run_lock(trace_path: Path) -> int | None:
    """Hold the repository's shared lifecycle lock for a normal Run.

    The lock exists so ``uninstall`` can see a Run that is starting, which makes
    it coordination a Run *offers* rather than one it depends on.  So every way
    of not getting it — a root that cannot be opened, a filesystem with no
    directory locks, or an uninstall currently holding it exclusively — answers
    "no lock held" instead of propagating.  Failing a Run's start for any of
    those would trade a rare coordination gap for an outage on the one path
    every Run takes, and a Run wedged behind a package manager with no output
    would be worse still.
    """
    if _fcntl is None:
        return None
    root = _repository_root_for_trace(trace_path)
    if root is None:
        return None
    try:
        fd = os.open(root, os.O_RDONLY)
    except OSError:
        return None
    if _flock_shared(fd):
        return fd
    os.close(fd)
    return None


def _flock_shared(fd: int) -> bool:
    """Take a shared lock without ever waiting on an exclusive holder forever."""
    assert _fcntl is not None
    deadline = time.monotonic() + _RUN_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_SH | _fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(_RUN_LOCK_POLL_SECONDS)
            continue
        return True


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
