"""Tests for the detached-Run sidecar seam behind TTY startup (#459)."""

from __future__ import annotations

from dataclasses import replace as dataclasses_replace
from datetime import datetime, timezone
from decimal import Decimal
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


def test_every_run_config_field_survives_the_detached_encoding() -> None:
    """The child's Run is configured by *all* of this Run's Config, not most of it.

    Structural rather than field-by-field on purpose. The detached child is a
    second process that rebuilds :class:`RunConfig` from this payload, so a
    field the payload forgets is not a missing key — it is that field's
    **default** silently taking effect on the path a TTY operator actually
    runs, while the parent's own object still says otherwise. A round-trip
    assertion over a config whose fields are mostly defaults cannot see that:
    the dropped field compares equal to the default that replaced it. Asking
    the dataclass what its fields are is the only version of this test that
    keeps working when the next field is added.
    """
    import dataclasses

    from git_loopy import run_sidecar

    carried = set(run_sidecar._config_to_payload(RunConfig()))
    declared = {field.name for field in dataclasses.fields(RunConfig)}

    assert declared - carried == set(), (
        "these RunConfig fields are dropped when a detached Run is encoded, so "
        "the child process runs on their defaults instead"
    )


def test_a_detached_run_carries_the_selected_route_policy() -> None:
    """A **Route policy** the operator selected reaches the process that runs (#560).

    The one field whose loss is silent *and* consequential: a child that
    rebuilt :attr:`RoutePolicy.UNSELECTED` would skip the Static route
    preflight entirely and then let the legacy roster gate drop the very
    effort the operator selected — running a different route than the one
    named, and reporting it as success (ADR-0057).
    """
    from git_loopy import run_sidecar
    from git_loopy.static_route import RoutePolicy

    spec = run_sidecar.DetachedRunSpec(
        config=dataclasses_replace(_config(), route_policy=RoutePolicy.STATIC),
        run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
    )

    decoded = run_sidecar.decode_detached_run_spec(
        run_sidecar.encode_detached_run_spec(spec)
    )

    assert decoded.config.route_policy is RoutePolicy.STATIC


def test_a_detached_dynamic_run_carries_its_bounds_exactly() -> None:
    """A routing allowance is spent by the child, so it has to arrive exact (#561).

    The allowance is the one value JSON would quietly change: a
    :class:`~decimal.Decimal` through a float is a different number of credits
    than the operator authorized, and the direction of the error is not
    knowable in advance. The bounds travel beside the policy because a child
    that rebuilt them as ``None`` would refuse the very Run its parent's
    preflight had already admitted.

    The API key is deliberately *not* here and cannot be: it is read from the
    environment at the point of use, so there is nothing in this payload —
    which is written to a control artifact on disk — to leak.
    """
    from git_loopy import run_sidecar
    from git_loopy.static_route import RoutePolicy

    spec = run_sidecar.DetachedRunSpec(
        config=dataclasses_replace(
            _config(),
            route_policy=RoutePolicy.DYNAMIC,
            routing_deadline_seconds=45.5,
            routing_credit_allowance=Decimal("0.1234567890123456789"),
            selector_concurrency=3,
            route_associations={"aa/opus-4.8": "claude-opus-4.8@max"},
            swe_bench_associations={"GPT Test (20260901)": "gpt-test@high"},
        ),
        run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
    )

    encoded = run_sidecar.encode_detached_run_spec(spec)
    decoded = run_sidecar.decode_detached_run_spec(encoded)

    assert decoded.config.route_policy is RoutePolicy.DYNAMIC
    assert decoded.config.routing_deadline_seconds == 45.5
    assert decoded.config.routing_credit_allowance == Decimal(
        "0.1234567890123456789"
    )
    assert decoded.config.selector_concurrency == 3
    assert dict(decoded.config.route_associations) == {
        "aa/opus-4.8": "claude-opus-4.8@max"
    }
    assert dict(decoded.config.swe_bench_associations) == {
        "GPT Test (20260901)": "gpt-test@high"
    }


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
