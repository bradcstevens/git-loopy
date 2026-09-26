"""A client Stop request, and nothing else, beside a Run's control artifact.

The control artifact itself stays content-free: its lock is the liveness
oracle (ADR-0058). The one verb that crosses from a client into the Run is a
durable file in the sibling directory ``<control-path>.stops``. The Run reads
that directory and latches the same two-stage Wind-down a Stop from the
launching terminal enters. The client never emits the Wind-down record.

One file is one logical request. Its name is the request identity. Writing
that name again is redelivery, not a second Stop. A different name is a
deliberate further Stop: the first asks for drain, any later one asks for
cancel, and a third adds no harder stage. A file that is not a complete Stop
— another verb, a partial write, a client that died mid-write — does not
cross. A client that dies after the file is linked does not withdraw it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "STOP_VERB",
    "StopAcknowledgment",
    "StopRequest",
    "StopSubmission",
    "apply_client_stops",
    "await_stop_acknowledgment",
    "latched_operator_stage",
    "read_stop_requests",
    "stops_directory",
    "submit_stop",
    "watch_client_stops",
]

#: The only verb a client may send across the boundary.
STOP_VERB = "stop"

#: How often a live Run looks for a client Stop. Short enough that a drain
#: latches while an agent session is still in flight, long enough that an idle
#: Run is not a busy loop.
POLL_INTERVAL = 0.05

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STAGE_RANK = {"drain": 0, "cancel": 1}
_OPERATOR_STOP = "operator_stop"
_STOP_REQUESTED = "wrapper.stop.requested"


class StopRequestError(ValueError):
    """A client named a Stop the Run cannot accept as a request identity."""


@dataclass(frozen=True)
class StopRequest:
    """One complete Stop a client has already made durable."""

    request_id: str
    seq: int


@dataclass(frozen=True)
class StopSubmission:
    """What ``submit_stop`` handed the Run, and which stage that identity asks for."""

    request_id: str
    stage: str
    redelivered: bool


@dataclass(frozen=True)
class StopAcknowledgment:
    """Whether the Run has announced the stage a client asked for.

    ``acknowledged`` means the trace carries that stage or a stronger one.
    ``unconfirmed`` means the bound elapsed without that announcement. It is
    not success and not a claim that the Run stopped (ADR-0058).
    """

    status: str
    stage: str | None


class StopTarget(Protocol):
    """The Run entry points a client Stop must reach, and no others."""

    def request_stop_drain(self) -> None: ...

    def request_stop_cancel(self) -> None: ...


def stops_directory(control_path: Path) -> Path:
    """The directory of Stop requests for the Run that owns ``control_path``."""
    return Path(str(control_path) + ".stops")


def submit_stop(control_path: Path, request_id: str, *, seq: int | None = None) -> StopSubmission:
    """Make one logical Stop durable, or report that this identity already was.

    Returns only after the request is linked into the directory, so a client
    that dies after this returns cannot take the Stop with it. Resending
    ``request_id`` does not allocate a new stage.
    """
    _require_request_id(request_id)
    directory = stops_directory(control_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / request_id
    if _complete_stop(target) is not None:
        return _submission(control_path, request_id, redelivered=True)

    temporary = directory / f".{request_id}.{os.getpid()}.tmp"
    payload = {"verb": STOP_VERB, "seq": time.time_ns() if seq is None else seq}
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return _submission(control_path, request_id, redelivered=True)
        except OSError:
            if _complete_stop(target) is not None:
                return _submission(control_path, request_id, redelivered=True)
            os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    _fsync_directory(directory)
    return _submission(control_path, request_id, redelivered=False)


def read_stop_requests(control_path: Path) -> tuple[StopRequest, ...]:
    """Complete Stop requests, first-to-drain order. Anything else is absent."""
    directory = stops_directory(control_path)
    if not directory.is_dir():
        return ()
    found: list[StopRequest] = []
    for path in directory.iterdir():
        if not _REQUEST_ID.fullmatch(path.name):
            continue
        request = _complete_stop(path)
        if request is not None:
            found.append(request)
    found.sort(key=lambda request: (request.seq, request.request_id))
    return tuple(found)


def apply_client_stops(run: StopTarget, control_path: Path) -> str | None:
    """Latch the Wind-down the requests have reached, through the Run's own entry.

    Idempotent. A request the Run has already latched is the Run's existing
    no-second-record rule, not a second announcement from here.
    """
    reached = _reached_stage(read_stop_requests(control_path))
    if reached is None:
        return None
    run.request_stop_drain()
    if reached == "cancel":
        run.request_stop_cancel()
    return reached


def latched_operator_stage(trace_path: Path) -> str | None:
    """The strongest operator-Stop stage the trace has announced, if any."""
    if not trace_path.is_file():
        return None
    strongest: str | None = None
    try:
        text = trace_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stage = _operator_stage(line)
        if stage is None:
            continue
        if strongest is None or _STAGE_RANK[stage] > _STAGE_RANK[strongest]:
            strongest = stage
    return strongest


def await_stop_acknowledgment(
    trace_path: Path,
    *,
    stage: str,
    timeout: float,
    poll_interval: float = POLL_INTERVAL,
) -> StopAcknowledgment:
    """Wait until the Run announces ``stage`` or stronger, or the bound elapses.

    A stronger announcement satisfies a weaker request: a Stop that escalated
    an already-latched Strike drain is still the stage the client asked for,
    reached. Elapsing the bound returns ``unconfirmed`` and does not claim the
    Run stopped.
    """
    if stage not in _STAGE_RANK:
        raise StopRequestError(f"unknown Stop stage: {stage!r}")
    if timeout < 0:
        raise StopRequestError("acknowledgment timeout cannot be negative")
    deadline = time.monotonic() + timeout
    while True:
        latched = latched_operator_stage(trace_path)
        if latched is not None and _STAGE_RANK[latched] >= _STAGE_RANK[stage]:
            return StopAcknowledgment(status="acknowledged", stage=latched)
        if time.monotonic() >= deadline:
            return StopAcknowledgment(status="unconfirmed", stage=latched)
        time.sleep(poll_interval)


async def watch_client_stops(
    run: StopTarget,
    control_path: Path,
    diag: logging.Logger,
) -> None:
    """Apply client Stops for as long as the Run is driving.

    A request the Run cannot read this tick stays where it is. The next tick
    tries again. Cancelling this task does not withdraw a request already
    written, and it does not itself Stop the Run.
    """
    while True:
        try:
            apply_client_stops(run, control_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad request must not end the Run
            diag.warning("client Stop request could not be applied: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)


def _submission(control_path: Path, request_id: str, *, redelivered: bool) -> StopSubmission:
    requests = read_stop_requests(control_path)
    index = next(
        (position for position, request in enumerate(requests) if request.request_id == request_id),
        None,
    )
    if index is None:
        raise StopRequestError(f"Stop request {request_id!r} was not durable")
    stage = "drain" if index == 0 else "cancel"
    return StopSubmission(request_id=request_id, stage=stage, redelivered=redelivered)


def _reached_stage(requests: tuple[StopRequest, ...]) -> str | None:
    if not requests:
        return None
    return "cancel" if len(requests) >= 2 else "drain"


def _complete_stop(path: Path) -> StopRequest | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict) or payload.get("verb") != STOP_VERB:
        return None
    seq = payload.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return None
    return StopRequest(request_id=path.name, seq=seq)


def _operator_stage(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    if event.get("type") != _STOP_REQUESTED or event.get("cause") != _OPERATOR_STOP:
        return None
    stage = event.get("stage")
    if stage not in _STAGE_RANK:
        return None
    return stage


def _require_request_id(request_id: str) -> None:
    if not _REQUEST_ID.fullmatch(request_id):
        raise StopRequestError(
            "a Stop request identity must be 1-128 letters, digits, '_' or '-'"
        )


def _fsync_directory(directory: Path) -> None:
    """Make the linked name durable. A platform that cannot fsync a directory
    still has the file itself fsynced; process death is not power loss.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)
