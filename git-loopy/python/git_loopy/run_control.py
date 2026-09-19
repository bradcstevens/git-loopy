"""Per-Run control artifacts and their advisory-lock liveness oracle.

The artifact is deliberately content-free: its OS-held lock is the complete
liveness record. A released lock therefore makes a preserved artifact a
readable record of a dead Run without a stale pid or a timed-out heartbeat.

Two lock mechanisms implement the one oracle, because git-loopy claims macOS,
Linux *and* native Windows and a platform that answered "unknown" would be an
absence of support dressed as parity (ADR-0058). POSIX uses ``flock``; Windows
uses ``LockFileEx`` on the artifact's first byte through the handle ``msvcrt``
hands out. They are chosen to behave identically where it matters: the holder's
lock is exclusive and outlives nothing but its process, and a probe's is
*shared*, so concurrent readers can never mistake one another for a live Run.
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

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised only off Windows.
    _msvcrt = None

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
    return _fcntl is not None or _msvcrt is not None


def control_path_for_trace(trace_path: Path) -> Path:
    """Return the per-Run control artifact beside ``trace_path``."""
    return trace_path.with_suffix(".control")


# ---------------------------------------------------------------------------
# The two lock mechanisms behind one oracle
# ---------------------------------------------------------------------------

#: The byte range both mechanisms lock. One byte at offset zero, held whether
#: or not the file has ever been written to — a content-free artifact has no
#: other range to agree on, and Windows locks past end-of-file quite happily.
_LOCK_OFFSET = 0
_LOCK_LENGTH = 1

if _msvcrt is not None:  # pragma: no cover - the Windows CI job is what runs this.
    import ctypes
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    #: What Windows reports when the range is held by somebody else, which is
    #: the answer this module wants rather than an error it should propagate.
    _ERROR_LOCK_VIOLATION = 33
    #: Unlocking a range nobody holds. Release is idempotent here exactly as it
    #: is for ``flock``, so this is not a failure either.
    _ERROR_NOT_LOCKED = 158

    class _Overlapped(ctypes.Structure):
        """The ``OVERLAPPED`` the lock APIs read the target offset out of."""

        _fields_ = (
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declared rather than inferred: a Windows ``HANDLE`` is pointer-sized, and
    # an undeclared ``ctypes`` argument is a 32-bit ``int``. The truncation
    # would only bite on a handle large enough to need the top word, which is
    # exactly the bug that survives every local smoke test.
    _kernel32.LockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    )
    _kernel32.UnlockFileEx.restype = wintypes.BOOL

    def _windows_region(handle: TextIO) -> tuple[int, "_Overlapped"]:
        overlapped = _Overlapped()
        overlapped.Offset = _LOCK_OFFSET
        return _msvcrt.get_osfhandle(handle.fileno()), overlapped

    def _windows_lock(handle: TextIO, *, exclusive: bool, wait: bool) -> bool:
        """Take the range, or report that somebody else is holding it."""
        os_handle, overlapped = _windows_region(handle)
        flags = 0
        if exclusive:
            flags |= _LOCKFILE_EXCLUSIVE_LOCK
        if not wait:
            flags |= _LOCKFILE_FAIL_IMMEDIATELY
        if _kernel32.LockFileEx(
            os_handle, flags, 0, _LOCK_LENGTH, 0, ctypes.byref(overlapped)
        ):
            return True
        code = ctypes.get_last_error()
        if code == _ERROR_LOCK_VIOLATION:
            return False
        raise ctypes.WinError(code)

    def _windows_unlock(handle: TextIO) -> None:
        os_handle, overlapped = _windows_region(handle)
        if _kernel32.UnlockFileEx(
            os_handle, 0, _LOCK_LENGTH, 0, ctypes.byref(overlapped)
        ):
            return
        code = ctypes.get_last_error()
        if code != _ERROR_NOT_LOCKED:
            raise ctypes.WinError(code)


def _lock_exclusively(handle: TextIO) -> None:
    """Hold the artifact for this Run, waiting out any current holder."""
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
    elif _msvcrt is not None:  # pragma: no cover - Windows-only.
        _windows_lock(handle, exclusive=True, wait=True)


def _release(handle: TextIO) -> None:
    """Release whatever this handle holds; safe where it holds nothing."""
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
    elif _msvcrt is not None:  # pragma: no cover - Windows-only.
        _windows_unlock(handle)


def _lock_is_free(handle: TextIO) -> bool:
    """Whether no Run holds the artifact, read through a *shared* probe.

    Shared on both mechanisms on purpose: an exclusive probe would conflict
    with another probe, and two operators listing Runs at the same moment would
    each report the other's read as a live Run.
    """
    if _fcntl is not None:
        try:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_SH | _fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return True
    # pragma: no cover below - the Windows CI job is what runs this.
    assert _msvcrt is not None  # guarded by ``advisory_locking_available``
    if not _windows_lock(handle, exclusive=False, wait=False):
        return False
    _windows_unlock(handle)
    return True


@dataclass
class RunControlArtifact:
    """One Run's lock-held control artifact.

    The file remains after :meth:`close`; liveness is a property of its lock,
    not of file existence. A host with neither lock mechanism keeps the
    trace-side artifact but intentionally exposes trace-only liveness through
    ``None``.
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
            _lock_exclusively(handle)
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
            _release(self._handle)
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
    result on a host that provides neither lock mechanism.
    """
    if not advisory_locking_available():
        return None
    try:
        handle = control_path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return False

    with handle:
        return not _lock_is_free(handle)


@contextmanager
def hold_uninstall_lock(repo_root: Path) -> Iterator[bool]:
    """Prevent a Run from becoming live while uninstall removes its state.

    A directory descriptor gives the Run and uninstall one advisory-lock target
    without writing another untracked file into the consuming repository.  Every
    way of failing to *take* that lock yields ``False`` rather than raising:
    uninstall answers an unavailable lock by refusing, and a caller cannot refuse
    on behalf of a traceback.

    Deliberately the one thing here that stays ``flock``-only. Its target is a
    *directory*, which Windows has no descriptor for; the Windows mechanism
    added for the control artifact locks a byte range in a file and cannot
    stand in. So uninstall on Windows proceeds exactly as it always has —
    uncoordinated at this seam and still refusing on the *Lane* liveness it
    reads from :func:`is_run_alive`, which is now a real answer there.
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

    The other half of :func:`hold_uninstall_lock`, so it is ``flock``-only for
    the same reason: there is no directory descriptor to lock on Windows, and a
    Run that offered no coordination is exactly what "no lock held" means here.
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
