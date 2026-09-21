"""Behavioral tests for the official optional SWE-bench Verified adapter (#564)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from git_loopy import swe_bench


def test_official_source_admits_only_exact_same_harness_rows() -> None:
    """SWE-bench supporting evidence is comparable, mapped, and never inferred."""
    calls: list[tuple[str, str, dict[str, str]]] = []

    async def fetch(method: str, url: str, headers: dict[str, str]) -> str:
        calls.append((method, url, headers))
        return """\
<script type="application/json" id="leaderboard-data">
[
  {
    "name": "Verified",
    "results": [
      {
        "agent": "mini-SWE-agent",
        "name": "GPT Test (20260901)",
        "reasoning_effort": "high",
        "resolved": 72.4,
        "date": "2026-09-01",
        "mini-swe-agent_version": "2.4.1"
      },
      {
        "agent": "mini-SWE-agent",
        "name": "Older harness",
        "resolved": 91,
        "date": "2026-09-02",
        "mini-swe-agent_version": "2.3.0"
      },
      {
        "agent": "A bespoke coding system",
        "name": "Not comparable",
        "resolved": 99,
        "date": "2026-09-03",
        "mini-swe-agent_version": "2.4.1"
      }
    ]
  }
]
</script>
"""

    source = swe_bench.SWEbenchVerifiedSource(
        associations={
            "GPT Test (20260901)": "gpt-test@high",
            "Older harness": "older@high",
            "Not comparable": "bespoke@high",
        },
        harness_version="2.4.1",
        fetch=fetch,
        clock=lambda: datetime(2026, 9, 18, 21, tzinfo=timezone.utc),
    )

    result = asyncio.run(source.fetch())

    assert calls == [("GET", swe_bench.SWE_BENCH_VERIFIED_URL, {})]
    assert result.source_identity == swe_bench.SWE_BENCH_VERIFIED_URL
    assert result.retrieved_at == datetime(2026, 9, 18, 21, tzinfo=timezone.utc)
    assert result.available is True
    assert result.records == (
        swe_bench.SWEbenchVerifiedRecord(
            source_identity=swe_bench.SWE_BENCH_VERIFIED_URL,
            source_model_identity="GPT Test (20260901)",
            associated_copilot_model="gpt-test",
            associated_copilot_effort="high",
            association_provenance="swe_bench_associations:GPT Test (20260901)",
            resolved=Decimal("72.4"),
            benchmark_version="SWE-bench Verified",
            harness="mini-SWE-agent",
            harness_version="2.4.1",
            conditions="reasoning_effort=high; evaluated_on=2026-09-01",
        ),
    )


def test_official_source_reports_no_result_when_mapped_rows_span_harness_versions() -> (
    None
):
    """A source never chooses one mini-SWE-agent release as a hidden baseline."""

    async def fetch(_method: str, _url: str, _headers: dict[str, str]) -> str:
        return """\
<script type="application/json" id="leaderboard-data">
[
  {
    "name": "Verified",
    "results": [
      {
        "agent": "mini-SWE-agent",
        "name": "New release",
        "resolved": 72.4,
        "date": "2026-09-01",
        "mini-swe-agent_version": "2.4.1"
      },
      {
        "agent": "mini-SWE-agent",
        "name": "Old release",
        "resolved": 91,
        "date": "2026-09-02",
        "mini-swe-agent_version": "2.3.0"
      }
    ]
  }
]
</script>
"""

    result = asyncio.run(
        swe_bench.SWEbenchVerifiedSource(
            associations={
                "New release": "new@high",
                "Old release": "old@high",
            },
            fetch=fetch,
        ).fetch()
    )

    assert result.available is False
    assert result.records == ()
