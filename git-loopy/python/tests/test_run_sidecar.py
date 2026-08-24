"""Tests for the detached-Run sidecar seam behind TTY startup (#459)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from git_loopy.config import RunConfig
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.run_control import RunControlArtifact, advisory_locking_available
from git_loopy.staircase import Candidate, PriceStaircase


def _config() -> RunConfig:
    return RunConfig(
        model="claude-opus-5",
        reasoning_effort="high",
        issue_source="github",
    )


def _card() -> RateCard:
    return RateCard(
        models={
            "claude-opus-5": ModelRate(
                model="claude-opus-5",
                multiplier=1.5,
                prices=ModelPrices(
                    batch_size=1_000_000,
                    input_price=1.0,
                    output_price=4.0,
                    cache_read_price=0.1,
                    cache_write_price=1.25,
                    max_prompt_tokens=128_000,
                ),
            )
        }
    )


def _staircase() -> PriceStaircase:
    return PriceStaircase(candidates=(Candidate("claude-opus-5", "high", 1.5),))


def test_detached_run_spec_round_trips_complex_run_inputs() -> None:
    from git_loopy import run_sidecar

    spec = run_sidecar.DetachedRunSpec(
        config=_config(),
        run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
        rate_card=_card(),
        staircase=_staircase(),
    )

    decoded = run_sidecar.decode_detached_run_spec(
        run_sidecar.encode_detached_run_spec(spec)
    )

    assert decoded == spec


def test_spawn_detached_child_uses_the_internal_child_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from git_loopy import run_sidecar

    recorded: dict[str, Any] = {}

    class _FakeProcess:
        pid = 1234

    def fake_popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(run_sidecar.subprocess, "Popen", fake_popen)

    spec = run_sidecar.DetachedRunSpec(
        config=_config(),
        run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
    )
    diagnostics_path = tmp_path / "detached.log"

    process = run_sidecar.spawn_detached_child(
        spec,
        diagnostics_path=diagnostics_path,
        cwd=tmp_path,
    )

    assert isinstance(process, _FakeProcess)
    assert recorded["argv"][1:3] == ["-m", "git_loopy.run_child"]
    assert recorded["argv"][0] == run_sidecar.sys.executable
    assert recorded["kwargs"]["cwd"] == str(tmp_path)
    assert recorded["kwargs"]["stdin"] is run_sidecar.subprocess.DEVNULL
    assert recorded["kwargs"]["stderr"] is run_sidecar.subprocess.STDOUT
    assert recorded["kwargs"]["start_new_session"] is (run_sidecar.os.name == "posix")
    assert Path(recorded["kwargs"]["stdout"].name) == diagnostics_path


def test_detached_child_main_uses_the_supplied_run_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_loopy import cli as cli_module
    from git_loopy import run_child, run_sidecar

    calls: list[dict[str, Any]] = []

    async def fake_drive_line_printer(
        config: RunConfig,
        *,
        rate_card: RateCard | None = None,
        staircase: PriceStaircase | None = None,
        run_id: str | None = None,
        started_at: datetime | None = None,
        mirror_diagnostics_to_stderr: bool = True,
    ) -> int:
        calls.append(
            {
                "config": config,
                "rate_card": rate_card,
                "staircase": staircase,
                "run_id": run_id,
                "started_at": started_at,
                "mirror_diagnostics_to_stderr": mirror_diagnostics_to_stderr,
            }
        )
        return 19

    monkeypatch.setattr(cli_module, "_drive_line_printer", fake_drive_line_printer)

    spec = run_sidecar.DetachedRunSpec(
        config=_config(),
        run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
        rate_card=_card(),
        staircase=_staircase(),
    )

    exit_code = run_child.main([run_sidecar.encode_detached_run_spec(spec)])

    assert exit_code == 19
    assert calls == [
        {
            "config": spec.config,
            "rate_card": spec.rate_card,
            "staircase": spec.staircase,
            "run_id": spec.run_id,
            "started_at": datetime.fromtimestamp(
                spec.started_at_epoch_ms / 1000, tz=timezone.utc
            ),
            "mirror_diagnostics_to_stderr": False,
        }
    ]


@pytest.mark.skipif(
    not advisory_locking_available(),
    reason="this platform has no flock advisory locks",
)
def test_trace_follower_waits_past_temporary_eof_while_the_run_is_live(
    tmp_path: Path,
) -> None:
    from git_loopy import run_sidecar

    trace_path = tmp_path / "run.trace.jsonl"
    control = RunControlArtifact.acquire(trace_path)
    try:
        follower = run_sidecar.TraceFollower(trace_path, control.path)
        trace_path.write_text(
            '{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}\n',
            encoding="utf-8",
        )

        first = follower.poll()
        assert first.lines == [
            '{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}'
        ]
        assert first.finished is False

        second = follower.poll()
        assert second.lines == []
        assert second.finished is False
    finally:
        control.close()


@pytest.mark.skipif(
    not advisory_locking_available(),
    reason="this platform has no flock advisory locks",
)
def test_trace_follower_finishes_on_run_end_or_lock_release(tmp_path: Path) -> None:
    from git_loopy import run_sidecar

    trace_path = tmp_path / "run.trace.jsonl"
    control = RunControlArtifact.acquire(trace_path)
    follower = run_sidecar.TraceFollower(trace_path, control.path)
    trace_path.write_text(
        "\n".join(
            [
                '{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}',
                '{"type":"wrapper.run.end","ts":"2026-05-16T00:00:01.000Z"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        first = follower.poll()
        assert first.lines[-1] == (
            '{"type":"wrapper.run.end","ts":"2026-05-16T00:00:01.000Z"}'
        )
        assert first.finished is True
    finally:
        control.close()

    trace_path = tmp_path / "released.trace.jsonl"
    control = RunControlArtifact.acquire(trace_path)
    follower = run_sidecar.TraceFollower(trace_path, control.path)
    try:
        assert follower.poll().finished is False
    finally:
        control.close()
    assert follower.poll().finished is True
