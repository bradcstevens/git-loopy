"""Behavioral tests for the official optional SWE-bench Verified adapter (#564)."""

from __future__ import annotations

import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from datetime import datetime, timezone
from decimal import Decimal

import pytest

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
        "reasoning_effort": "high",
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
            conditions="reasoning_effort=high; submission_date=2026-09-01",
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
        "reasoning_effort": "high",
        "resolved": 72.4,
        "date": "2026-09-01",
        "mini-swe-agent_version": "2.4.1"
      },
      {
        "agent": "mini-SWE-agent",
        "name": "Old release",
        "reasoning_effort": "high",
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


def test_optional_decoder_recursion_is_a_source_failure(monkeypatch):
    async def fetch(_method, _url, _headers):
        return '<script id="leaderboard-data">[]</script>'

    def decode(_payload):
        raise RecursionError("fixture: JSON decoder nesting limit")

    monkeypatch.setattr(swe_bench.json, "loads", decode)
    with pytest.raises(swe_bench.SWEbenchSourceError, match="source unavailable"):
        asyncio.run(swe_bench.SWEbenchVerifiedSource(
            associations={"Exact model": "work-model@high"}, fetch=fetch,
        ).fetch())


def test_optional_transport_cancellation_does_not_wait_for_a_slow_body(monkeypatch):
    stop = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            try:
                for _ in range(60):
                    if stop.wait(0.05):
                        break
                    self.wfile.write(b"x" * 1024)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    connect = asyncio.open_connection

    async def local_connection(_host, _port, *, ssl):
        assert ssl.check_hostname
        return await connect("127.0.0.1", server.server_port)

    monkeypatch.setattr(swe_bench.asyncio, "open_connection", local_connection)

    async def read():
        source = swe_bench.SWEbenchVerifiedSource(associations={})
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(source.fetch(), timeout=0.1)

    try:
        started = time.monotonic()
        asyncio.run(read())
        assert time.monotonic() - started < 1
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("framing", ["length", "chunked", "error", "truncated"])
def test_optional_transport_decodes_complete_http_responses(monkeypatch, framing):
    requests = []
    connect = asyncio.open_connection
    body = b'<script id="leaderboard-data">[{"name":"Verified","results":[]}]</script>'

    async def serve(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        if framing == "chunked":
            response = (
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                + f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
            )
        else:
            status = "503 Unavailable" if framing == "error" else "200 OK"
            length = len(body) + (10 if framing == "truncated" else 0)
            response = (
                f"HTTP/1.1 {status}\r\nContent-Length: {length}\r\n\r\n".encode() + body
            )
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def read():
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            async def local_connection(host, port, *, ssl):
                assert (host, port) == ("www.swebench.com", 443)
                assert ssl.check_hostname
                return await connect("127.0.0.1", server.sockets[0].getsockname()[1])

            monkeypatch.setattr(swe_bench.asyncio, "open_connection", local_connection)
            source = swe_bench.SWEbenchVerifiedSource(associations={})
            if framing in {"error", "truncated"}:
                with pytest.raises(swe_bench.SWEbenchSourceError):
                    await source.fetch()
            else:
                assert (await source.fetch()).records == ()

    asyncio.run(read())
    assert requests == [
        b"GET / HTTP/1.1\r\nHost: www.swebench.com\r\n"
        b"Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ]


@pytest.mark.parametrize(
    "patch",
    [
        {"resolved": -1},
        {"resolved": 101},
        {"resolved": "NaN"},
        {"reasoning_effort": "low"},
        {"reasoning_effort": None},
        {"warning": "Submission has invalid evaluation results"},
        {"date": "unknown"},
    ],
)
def test_invalid_or_differently_configured_scores_are_not_supporting_evidence(patch):
    async def fetch(_method, _url, _headers):
        row = {
            "agent": "mini-SWE-agent",
            "name": "Exact model",
            "reasoning_effort": "high",
            "resolved": 72.4,
            "date": "2026-09-01",
            "mini-swe-agent_version": "2.4.1",
            **patch,
        }
        return '<script id="leaderboard-data">' + json.dumps([
            {"name": "Verified", "results": [row]},
        ]) + "</script>"

    result = asyncio.run(swe_bench.SWEbenchVerifiedSource(
        associations={"Exact model": "work-model@high"}, fetch=fetch,
    ).fetch())

    assert result.available is False
    assert result.records == ()
