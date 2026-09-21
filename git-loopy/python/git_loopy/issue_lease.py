"""Pure Lease record inspection (ADR-0033).

Staged ahead of the ref transport and Pickup consumers: this module neither
takes a Lease nor authorizes a side effect. Expiry is not a fence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal


_MAX_INTEGER = 2**53 - 1
_RUN_ID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class LeaseRecord:
    run_id: str
    issue: int
    repository: str
    claimed_at: int
    heartbeat_at: int
    ttl_seconds: int
    host: str
    pid: int


@dataclass(frozen=True)
class LeaseInspection:
    state: Literal["absent", "live", "expired"]
    record: LeaseRecord | None
    diagnostics: tuple[str, ...] = ()


def _whole_number(value: object, minimum: int) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= value <= _MAX_INTEGER
        and value == int(value)
    )


def _parse_record(raw: str, repository: str, issue: int) -> LeaseRecord | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    run_id, record_repository, host = (
        value.get("run_id"),
        value.get("repository"),
        value.get("host"),
    )
    if (
        not isinstance(run_id, str)
        or _RUN_ID.fullmatch(run_id) is None
        or not isinstance(record_repository, str)
        or _REPOSITORY.fullmatch(record_repository) is None
        or record_repository.lower() != repository.lower()
        or not isinstance(host, str)
        or not host
    ):
        return None
    for field, minimum in (
        ("issue", 1),
        ("claimed_at", 0),
        ("heartbeat_at", 0),
        ("ttl_seconds", 1),
        ("pid", 1),
    ):
        if not _whole_number(value.get(field), minimum):
            return None
    if value["issue"] != issue or value["heartbeat_at"] < value["claimed_at"]:
        return None
    return LeaseRecord(
        run_id=run_id,
        issue=int(value["issue"]),
        repository=record_repository,
        claimed_at=int(value["claimed_at"]),
        heartbeat_at=int(value["heartbeat_at"]),
        ttl_seconds=int(value["ttl_seconds"]),
        host=host,
        pid=int(value["pid"]),
    )


def inspect_lease(
    raw: str | None,
    *,
    now: int,
    repository: str,
    issue: int,
    skew_tolerance_seconds: int = 60,
) -> LeaseInspection:
    """Inspect one ref's message with an injected Unix-seconds clock.

    ``None`` denotes an absent ref, not an unreadable fetch. The transport must
    propagate read failures, never substitute absence. A malformed message is
    expired with a diagnostic; invalid caller context instead raises ValueError.
    Diagnostics are structural signals for the eventual caller to report.
    """
    for name, value, minimum in (
        ("now", now, 0),
        ("issue", issue, 1),
        ("skew_tolerance_seconds", skew_tolerance_seconds, 0),
    ):
        if not _whole_number(value, minimum):
            raise ValueError(f"Lease inspection: invalid {name}")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Lease inspection: invalid repository")
    if raw is None:
        return LeaseInspection("absent", None)
    record = _parse_record(raw, repository, issue)
    if record is None:
        return LeaseInspection("expired", None, ("malformed_record",))
    return LeaseInspection(
        "expired" if now - record.heartbeat_at > record.ttl_seconds else "live",
        record,
        ("clock_skew",) if record.claimed_at - now > skew_tolerance_seconds else (),
    )
