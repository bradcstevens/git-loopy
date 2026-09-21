"""Pure Lease record inspection (ADR-0033).

Staged ahead of the ref transport and Pickup consumers: this module neither
takes a Lease nor authorizes a side effect. Expiry is not a fence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


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


def inspect_lease(
    raw: str | None,
    *,
    now: int,
    repository: str,
    issue: int,
) -> LeaseInspection:
    """Inspect a fetched record without reading the clock or contacting origin."""
    record = LeaseRecord(**json.loads(raw))
    return LeaseInspection("live", record)
