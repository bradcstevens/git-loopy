"""Verified, optional SWE-bench evidence for a Dynamic routing assessment.

The official leaderboard is a public HTML document with a deliberately narrow
machine-readable payload. This adapter reads only that payload and never
receives issue or repository material.
"""

from __future__ import annotations

import asyncio
import http.client
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Awaitable, Callable, Mapping
from urllib.parse import urlsplit

__all__ = [
    "SWE_BENCH_VERIFIED_URL",
    "SWEbenchSourceError",
    "SWEbenchVerifiedRecord",
    "SWEbenchVerifiedResult",
    "SWEbenchVerifiedSource",
]

SWE_BENCH_VERIFIED_URL = "https://www.swebench.com/"

_Fetch = Callable[[str, str, dict[str, str]], Awaitable[object]]


class SWEbenchSourceError(RuntimeError):
    """The optional public source could not be read or decoded."""


@dataclass(frozen=True)
class SWEbenchVerifiedRecord:
    """One exactly associated same-harness official leaderboard observation."""

    source_identity: str
    source_model_identity: str
    associated_copilot_model: str
    associated_copilot_effort: str | None
    association_provenance: str
    resolved: Decimal
    benchmark_version: str
    harness: str
    harness_version: str
    conditions: str


@dataclass(frozen=True)
class SWEbenchVerifiedResult:
    """A current source read, including a truthful no-comparable-rows result."""

    source_identity: str
    retrieved_at: datetime
    records: tuple[SWEbenchVerifiedRecord, ...]

    @property
    def available(self) -> bool:
        return bool(self.records)


class SWEbenchVerifiedSource:
    """Read official same-harness Verified rows through exact operator mappings."""

    def __init__(
        self,
        *,
        associations: Mapping[str, str],
        harness_version: str | None = None,
        fetch: _Fetch | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if harness_version is not None and (
            not isinstance(harness_version, str) or not harness_version
        ):
            raise ValueError(
                "a SWE-bench mini-SWE-agent harness version must be non-empty"
            )
        self._associations = _parse_associations(associations)
        self._harness_version = harness_version
        self._fetch = fetch or _stdlib_fetch
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def fetch(self) -> SWEbenchVerifiedResult:
        """Retrieve only current public leaderboard rows from the official source."""
        try:
            payload = await self._fetch("GET", SWE_BENCH_VERIFIED_URL, {})
            records = _project_records(
                _leaderboard_data(payload), self._associations, self._harness_version,
            )
        except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
            raise SWEbenchSourceError("SWE-bench source unavailable") from exc
        retrieved_at = self._clock()
        if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
            raise ValueError("retrieval clock must return an aware datetime")
        return SWEbenchVerifiedResult(
            source_identity=SWE_BENCH_VERIFIED_URL,
            retrieved_at=retrieved_at,
            records=records,
        )


async def _stdlib_fetch(method: str, url: str, headers: dict[str, str]) -> object:
    def request() -> bytes:
        if method != "GET" or url != SWE_BENCH_VERIFIED_URL or headers:
            raise ValueError("SWE-bench request target is invalid")
        target = urlsplit(SWE_BENCH_VERIFIED_URL)
        connection = http.client.HTTPSConnection(target.netloc, timeout=30)
        try:
            connection.request("GET", target.path or "/")
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise ValueError("SWE-bench returned an unsuccessful response")
            body = response.read(10_000_001)
        finally:
            connection.close()
        if len(body) > 10_000_000:
            raise ValueError("SWE-bench response is too large")
        return body

    return await asyncio.to_thread(request)


def _parse_associations(associations: Mapping[str, str]) -> dict[str, tuple[str, str | None]]:
    parsed: dict[str, tuple[str, str | None]] = {}
    for source_identity, configuration in associations.items():
        if not isinstance(source_identity, str) or not source_identity:
            raise ValueError("SWE-bench associations require exact non-empty model identities")
        if not isinstance(configuration, str) or not configuration.strip():
            raise ValueError("SWE-bench associations require non-empty configurations")
        model, separator, effort = configuration.rpartition("@")
        if not separator:
            model, effort = configuration, ""
        if not model.strip():
            raise ValueError("SWE-bench association model is empty")
        parsed[source_identity] = (model.strip(), effort.strip() or None)
    return parsed


def _leaderboard_data(payload: object) -> list[object]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if not isinstance(payload, str):
        raise ValueError("SWE-bench response must be HTML text")
    parser = _LeaderboardScript()
    parser.feed(payload)
    parser.close()
    if parser.data is None:
        raise ValueError("SWE-bench response is missing leaderboard data")
    parsed = json.loads(parser.data)
    if not isinstance(parsed, list):
        raise ValueError("SWE-bench leaderboard data must be a list")
    return parsed


class _LeaderboardScript(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._inside = False
        self._parts: list[str] = []
        self.data: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "leaderboard-data":
            self._inside = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inside:
            self.data = "".join(self._parts)
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self._parts.append(data)


def _project_records(
    leaderboards: list[object],
    associations: Mapping[str, tuple[str, str | None]],
    harness_version: str | None,
) -> tuple[SWEbenchVerifiedRecord, ...]:
    records: list[SWEbenchVerifiedRecord] = []
    for leaderboard in leaderboards:
        if not isinstance(leaderboard, Mapping) or leaderboard.get("name") != "Verified":
            continue
        results = leaderboard.get("results")
        if not isinstance(results, list):
            continue
        for row in results:
            record = _project_row(row, associations)
            if record is not None:
                records.append(record)
    versions = {record.harness_version for record in records}
    if harness_version is not None:
        return tuple(record for record in records if record.harness_version == harness_version)
    return tuple(records) if len(versions) == 1 else ()


def _project_row(
    row: object,
    associations: Mapping[str, tuple[str, str | None]],
) -> SWEbenchVerifiedRecord | None:
    if not isinstance(row, Mapping):
        return None
    identity = row.get("name")
    if (
        row.get("agent") != "mini-SWE-agent" or row.get("warning")
        or not isinstance(identity, str)
        or not identity
    ):
        return None
    association = associations.get(identity)
    if association is None:
        return None
    score = _decimal(row.get("resolved"))
    submitted_on = row.get("date")
    effort = row.get("reasoning_effort")
    if (
        score is None or not 0 <= score <= 100
        or not isinstance(submitted_on, str)
        or effort != association[1]
    ):
        return None
    try:
        date.fromisoformat(submitted_on)
    except ValueError:
        return None
    row_harness_version = row.get("mini-swe-agent_version")
    if not isinstance(row_harness_version, str) or not row_harness_version:
        return None
    conditions = [
        f"reasoning_effort={effort if effort is not None else 'unspecified'}",
        f"submission_date={submitted_on}",
    ]
    folder = row.get("folder")
    if isinstance(folder, str) and folder:
        conditions.append(f"submission={folder}")
    return SWEbenchVerifiedRecord(
        source_identity=SWE_BENCH_VERIFIED_URL,
        source_model_identity=identity,
        associated_copilot_model=association[0],
        associated_copilot_effort=association[1],
        association_provenance=f"swe_bench_associations:{identity}",
        resolved=score,
        benchmark_version="SWE-bench Verified",
        harness="mini-SWE-agent",
        harness_version=row_harness_version,
        conditions="; ".join(conditions),
    )


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None
