"""``git_loopy.run_control`` — the per-Run control artifact + OS advisory lock.

Spec: `#445 <https://github.com/bradcstevens/git-loopy/issues/445>`_ §F
("Lane workspaces and Sweep") and §I ("Dashboard attach and detach").
Ticket: `#446 <https://github.com/bradcstevens/git-loopy/issues/446>`_.

A Run publishes exactly one **control artifact** beside its trace and holds
an **OS advisory lock** on it for the entire lifetime of the process. Any
other process — a later Run, a sweep, a Dashboard client — tests that lock
to learn, with certainty, whether the Run is alive:

* **lock held ⇒ alive.**
* **lock free ⇒ dead** — released by the kernel on every death, including
  ``SIGKILL`` and a lost power cable. There is no pid to go stale and no
  heartbeat to time out, so liveness needs no polling interval either.

Nothing consumes this artifact yet (deliberately — see the ticket). This
module only publishes the oracle; :func:`probe_liveness` exists so a later
consumer (Sweep, Dashboard attach) has one place to ask the question rather
than re-inventing the lock protocol.

Public surface:

* :class:`RunControlHandle` — held by the Run for its whole life;
  :meth:`~RunControlHandle.close` releases the lock (also released by the
  OS on process exit, including an uncatchable kill).
* :func:`open_run_control` — creates the artifact and attempts to acquire
  the lock. Never raises on a locking failure: a platform (or sandbox) that
  cannot lock degrades cleanly to a trace-only artifact and says so via
  :attr:`RunControlHandle.locked`.
* :func:`probe_liveness` — the second-process side: true if the Run that
  owns the artifact is still alive, false otherwise (including "no artifact
  at this path"). Never mutates the artifact — a fresh, independent file
  descriptor does the probing and is always closed before returning.
* :data:`LOCKING_SUPPORTED` — whether this platform has a locking backend
  wired up at all (POSIX ``fcntl.flock`` or Windows ``msvcrt.locking``).

Locking backend:

* POSIX: ``fcntl.flock(fd, LOCK_EX | LOCK_NB)`` on the whole file — no byte
  range needed, and released unconditionally by the kernel when every file
  descriptor referencing it closes (normal exit, uncaught exception,
  ``SIGKILL``, ``os._exit`` — anything).
* Windows: ``msvcrt.locking(fd, LK_NBLCK, 1)`` on a one-byte range — the
  nearest stdlib equivalent to ``flock``, released the same way on process
  death.
* Anything else (no ``fcntl`` and no ``msvcrt``): :data:`LOCKING_SUPPORTED`
  is ``False`` and :func:`open_run_control` still writes the artifact, but
  ``locked`` comes back ``False`` — the declared, clean degradation to
  trace-only liveness the ticket asks for.

Design notes:

* **The artifact carries no liveness-bearing pid.** It records ``run_id``,
  ``started_at`` and ``pid`` purely as forensic metadata for a human
  reading the file by hand; the *lock*, not any field inside the file, is
  what a caller must consult to answer "is this Run alive".
* **Stdlib-only.** Mirrors the constraint already enforced on
  :mod:`git_loopy.persist` (see
  ``tests/test_persist.py::test_persist_module_imports_are_constrained``)
  so the artifact-writing seam stays free of third-party dependencies.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "RunControlHandle",
    "open_run_control",
    "probe_liveness",
    "control_artifact_path",
    "LOCKING_SUPPORTED",
]

_IS_WINDOWS = sys.platform == "win32"

if not _IS_WINDOWS:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - no known stdlib platform lacks this
        fcntl = None  # type: ignore[assignment]
else:
    try:
        import msvcrt
    except ImportError:  # pragma: no cover - no known Windows build lacks this
        msvcrt = None  # type: ignore[assignment]

LOCKING_SUPPORTED: bool = (
    fcntl is not None if not _IS_WINDOWS else msvcrt is not None
)


def control_artifact_path(logs_dir: Path, stem: str) -> Path:
    """Return the control-artifact path for a Run sharing ``stem``.

    Lives beside the trace (``logs_dir / f"{stem}.jsonl"``) in the same
    directory, so it inherits that directory's ``.gitignore`` coverage for
    free — no separate ignore entry is needed.
    """
    return logs_dir / f"{stem}.control.lock"


def _try_acquire_nonblocking(fh: BinaryIO) -> bool:
    """Attempt to take the exclusive, non-blocking advisory lock on ``fh``.

    Returns ``True`` if acquired, ``False`` if another live holder has it,
    or if this platform has no locking backend at all.
    """
    if not LOCKING_SUPPORTED:
        return False
    if _IS_WINDOWS:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release(fh: BinaryIO) -> None:
    """Best-effort release of a lock previously taken by this process."""
    if not LOCKING_SUPPORTED:
        return
    try:
        if _IS_WINDOWS:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@dataclass
class RunControlHandle:
    """The Run's own control artifact, held open for the Run's lifetime.

    ``locked`` is ``True`` iff this process holds the exclusive advisory
    lock on ``path``. It is only ``False`` when this platform declined to
    implement locking (:data:`LOCKING_SUPPORTED` is ``False``) — degrading
    cleanly to trace-only liveness — since a genuine lock contest at this
    path (another live Run already holding it) can only happen if a run_id
    collides, which :func:`~git_loopy.persist.make_run_id` makes
    astronomically unlikely.
    """

    path: Path
    locked: bool
    _fh: BinaryIO | None = field(default=None, repr=False, compare=False)

    def close(self) -> None:
        """Release the lock (if held) and close the underlying descriptor.

        Idempotent. Also happens implicitly — the OS releases the lock the
        instant every descriptor on ``path`` closes, which is exactly what
        an uncatchable kill or a lost power cable does for free.
        """
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            _release(fh)
        finally:
            fh.close()

    def __enter__(self) -> "RunControlHandle":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_run_control(logs_dir: Path, stem: str, *, run_id: str, started_at: datetime) -> RunControlHandle:
    """Create the control artifact and take its lock for this process.

    Creates ``logs_dir`` if needed (the control artifact is published at
    Run start, unlike the trace / summary / diagnostics artefacts, which
    defer their own directory creation to first write). Writes a small
    JSON document — ``run_id``, ``started_at``, ``pid`` — purely as
    forensic metadata; none of it is load-bearing for liveness, which is
    answered entirely by the lock (see :func:`probe_liveness`).

    Never raises on a locking failure: if this platform has no locking
    backend at all, the artifact is still written and ``locked`` comes back
    ``False`` (the declared, clean trace-only degradation the ticket asks
    for). Only genuine I/O failures creating the file (disk full, permission
    denied) propagate.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = control_artifact_path(logs_dir, stem)
    fh = open(path, "wb")
    try:
        payload = {
            "run_id": run_id,
            "started_at": started_at.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            "pid": os.getpid(),
        }
        fh.write(json.dumps(payload).encode("utf-8"))
        fh.flush()
        locked = _try_acquire_nonblocking(fh)
    except BaseException:
        fh.close()
        raise
    return RunControlHandle(path=path, locked=locked, _fh=fh)


def probe_liveness(path: Path) -> bool:
    """Return whether the Run owning the control artifact at ``path`` is alive.

    ``True`` iff some other process currently holds the exclusive lock on
    ``path`` — i.e. a Run is alive. ``False`` covers every other case: the
    artifact does not exist (no Run ever claimed this path, or the platform
    that wrote it declared :data:`LOCKING_SUPPORTED` is ``False``), or the
    artifact exists but nothing holds its lock (the Run that published it
    has died — cleanly or via an uncatchable kill; the two are
    indistinguishable from the lock alone, which is the point: neither
    needs a pid, a heartbeat nor a polling interval to answer).

    Never mutates the artifact: this opens its own, independent read-only
    descriptor, attempts a non-blocking lock, releases it immediately if
    acquired, and closes the descriptor before returning — no write, no
    truncation, and any lock this call itself takes never outlives the
    call.
    """
    if not LOCKING_SUPPORTED:
        return False
    try:
        fh = open(path, "rb")
    except OSError:
        return False
    try:
        acquired = _try_acquire_nonblocking(fh)  # type: ignore[arg-type]
        if acquired:
            _release(fh)  # type: ignore[arg-type]
        return not acquired
    finally:
        fh.close()
