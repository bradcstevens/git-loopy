"""Tests for the detached-Run sidecar seam behind TTY startup (#459)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


@pytest.mark.parametrize("tier", ["default", "long_context"])
def test_a_detached_run_preserves_explicit_work_tier_authority(tier) -> None:
    from git_loopy import cli, run_sidecar

    config = cli.resolve_config(
        cli.build_parser().parse_args(["--context-tier", tier]),
        {},
        project={"route_policy": "dynamic"},
        global_={},
    ).run
    spec = run_sidecar.DetachedRunSpec(
        config=config, run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
    )

    decoded = run_sidecar.decode_detached_run_spec(run_sidecar.encode_detached_run_spec(spec))

    assert decoded.config.context_tier == tier
    assert decoded.config.context_tier_override is True
    assert decoded.config.routing_suppressed is False


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


# ---------------------------------------------------------------------------
# The client reports the worker's result, not its own success at watching
# (#583, ADR-0058)
# ---------------------------------------------------------------------------


def _finished_worker(code: int) -> "subprocess.Popen[Any]":
    """A real worker process that has already exited with ``code``."""
    child = subprocess.Popen([sys.executable, "-c", f"raise SystemExit({code})"])
    child.wait()
    return child


def test_the_client_reports_a_worker_blocked_before_it_traced_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A blocked startup must not reach the operator as a clean exit 0 (#583).

    Run preflight runs inside the detached worker *before* the first Event, so a
    precondition failure — an unauthenticated ``gh``, a missing ``copilot`` —
    leaves the trace empty and writes the blocker only to the per-Run
    diagnostics file. The client that follows that trace used to render nothing
    and return 0, so a saved setup looked like Run readiness. ADR-0058 requires
    the opposite: the confirmed setup stays, no issue work starts, and the
    non-zero result names the blocker and its remedy.
    """
    from git_loopy import run_sidecar

    trace_path = tmp_path / "run.trace.jsonl"  # never created: nothing ran
    diagnostics_path = tmp_path / "run.log"
    diagnostics_path.write_text(
        "2026-09-19 12:00:00 ERROR gh is not authenticated. "
        "Run `gh auth login` and retry\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    rc = run_sidecar.run_terminal_client(
        repository_root=tmp_path,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(1),
        release_version="",
        warn=warnings.append,
        diagnostics_path=diagnostics_path,
    )

    assert rc == 1
    reported = capsys.readouterr().err + "\n".join(warnings)
    assert "gh auth login" in reported
    assert str(diagnostics_path) in reported


def test_the_client_returns_zero_for_a_worker_that_ended_cleanly(
    tmp_path: Path,
) -> None:
    """A Run that finished is still a success, and says so exactly once."""
    from git_loopy import run_sidecar

    trace_path = tmp_path / "run.trace.jsonl"
    trace_path.write_text(
        '{"type":"wrapper.run.end","ts":"2026-09-19T00:00:00.000Z",'
        '"run_id":"r","outcome":"empty_pool"}\n',
        encoding="utf-8",
    )
    warnings: list[str] = []

    rc = run_sidecar.run_terminal_client(
        repository_root=tmp_path,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(0),
        release_version="",
        warn=warnings.append,
        diagnostics_path=tmp_path / "run.log",
    )

    assert rc == 0


@pytest.mark.skipif(
    not advisory_locking_available(),
    reason="this platform has no flock advisory locks",
)
def test_the_client_never_invents_a_failure_for_a_worker_still_running(
    tmp_path: Path,
) -> None:
    """A client observes; it does not own the Run's lifetime (ADR-0058).

    A Dashboard that exits of its own accord while the Run works — a Detach — has
    nothing to report but its own clean return. Waiting on that worker, or
    reading a failure into it, would make disconnecting indistinguishable from
    stopping work, which is the confusion ADR-0058 separates. The Dashboard is
    the only double here: the worker is a real process holding the real control
    artifact, and the client really execs the helper.
    """
    from git_loopy import run_sidecar, tui_release

    trace_path = tmp_path / "run.trace.jsonl"
    control_path = run_sidecar.control_path_for_trace(trace_path)
    helper = tmp_path / "fake-dashboard.py"
    helper.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
    helper.chmod(0o755)
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        "import pathlib, sys, time\n"
        "from git_loopy.run_control import RunControlArtifact\n"
        # Bound, not discarded: the artifact's lock lives on its open handle.
        "artifact = RunControlArtifact.acquire(pathlib.Path(sys.argv[1]))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )

    worker = subprocess.Popen([sys.executable, str(worker_script), str(trace_path)])
    original_resolve = tui_release.resolve_runtime_helper
    tui_release.resolve_runtime_helper = (  # type: ignore[assignment]
        lambda *_args, **_kwargs: helper
    )
    try:
        started = time.monotonic()
        rc = run_sidecar.run_terminal_client(
            repository_root=tmp_path,
            config=_config(),
            trace_path=trace_path,
            control_path=control_path,
            child=worker,
            release_version="0.10.0",
            warn=lambda _message: None,
            diagnostics_path=tmp_path / "run.log",
        )
        elapsed = time.monotonic() - started
    finally:
        tui_release.resolve_runtime_helper = original_resolve  # type: ignore[assignment]
        still_running = worker.poll() is None
        worker.kill()
        worker.wait()

    assert rc == 0
    assert still_running, "the client ended the Run it was only observing"
    assert elapsed < 30, "the client blocked on a worker it does not own"


def test_the_client_reports_a_worker_that_failed_after_it_traced_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Run that recorded work and then failed is reported, not re-narrated.

    The trace already said what happened, so echoing the diagnostics file over
    the top would bury it. Only the exit status crosses back.
    """
    from git_loopy import run_sidecar

    trace_path = tmp_path / "run.trace.jsonl"
    trace_path.write_text(
        '{"type":"wrapper.run.end","ts":"2026-09-19T00:00:00.000Z",'
        '"run_id":"r","outcome":"stuck"}\n',
        encoding="utf-8",
    )
    diagnostics_path = tmp_path / "run.log"
    diagnostics_path.write_text("2026-09-19 ERROR internal detail\n", encoding="utf-8")

    rc = run_sidecar.run_terminal_client(
        repository_root=tmp_path,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(1),
        release_version="",
        warn=lambda _message: None,
        diagnostics_path=diagnostics_path,
    )

    assert rc == 1
    assert "internal detail" not in capsys.readouterr().err


def test_the_python_launch_leaves_the_viewing_machines_clock_to_the_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denver_viewer: None,
) -> None:
    """The Orchestrator states no offset and scrubs no environment (#597).

    This is the defect from the other side. The helper used to default to a
    zero offset and no launcher ever corrected it, so every operator read UTC
    no matter where they stood; the helper now resolves the *viewing* machine
    instead. That repair only holds while the launch keeps out of the way, and
    it can be undone from here in two ways that no other test would notice:

    * passing ``--utc-offset-minutes`` would pin every viewer to one clock —
      the Run host's, or whatever the Orchestrator happened to compute; and
    * handing the child a constructed environment would strip ``TZ``, so a
      helper on a machine that knows perfectly well where it is would fall
      back to labelled UTC.

    Both are asserted at the real process boundary, against a helper that
    records what it was actually given, because ``_helper_args`` returning the
    right list proves nothing about the environment the exec inherits.
    """
    from git_loopy import run_sidecar, tui_release

    trace_path = tmp_path / "run.trace.jsonl"
    control_path = run_sidecar.control_path_for_trace(trace_path)
    argv_path = tmp_path / "helper.argv"
    zone_path = tmp_path / "helper.zone"
    zone_directory_path = tmp_path / "helper.zone-directory"
    zone_directory = tmp_path / "zoneinfo"
    monkeypatch.setenv("TZDIR", str(zone_directory))

    helper = tmp_path / "recording-dashboard.py"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"open({str(zone_path)!r}, 'w').write(os.environ.get('TZ', '<unset>'))\n"
        f"open({str(zone_directory_path)!r}, 'w').write(os.environ.get('TZDIR', '<unset>'))\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)

    worker_script = tmp_path / "worker.py"
    worker_script.write_text(
        "import pathlib, sys, time\n"
        "from git_loopy.run_control import RunControlArtifact\n"
        "artifact = RunControlArtifact.acquire(pathlib.Path(sys.argv[1]))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )

    # The viewing machine this Run is launched from. A helper that receives it
    # can resolve the operator's own zone; one that does not, cannot.
    environment = dict(os.environ, TZ="America/Denver")
    worker = subprocess.Popen(
        [sys.executable, str(worker_script), str(trace_path)], env=environment
    )
    monkeypatch.setattr(
        tui_release, "resolve_runtime_helper", lambda *_args, **_kwargs: helper
    )
    try:
        run_sidecar.run_terminal_client(
            repository_root=tmp_path,
            config=_config(),
            trace_path=trace_path,
            control_path=control_path,
            child=worker,
            release_version="0.10.0",
            warn=lambda _message: None,
            diagnostics_path=tmp_path / "run.log",
        )
    finally:
        worker.kill()
        worker.wait()

    assert argv_path.exists(), "the client never reached the helper"
    arguments = json.loads(argv_path.read_text(encoding="utf-8"))
    assert "--utc-offset-minutes" not in arguments, (
        "the Python launch pinned the Dashboard's clock; the viewing machine's "
        "own zone is the default and the Orchestrator must not override it"
    )
    assert zone_path.read_text(encoding="utf-8") == "America/Denver", (
        "the helper was handed a scrubbed environment, so it cannot resolve "
        "the zone of the machine an operator is actually looking at"
    )
    assert zone_directory_path.read_text(encoding="utf-8") == str(zone_directory)


# ---------------------------------------------------------------------------
# An Unbound Run says why it ended after the client returns (#642)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_ambient_gh_repository(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve the Run's repository from each test's clone, not this host's gh."""
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path_factory.mktemp("gh-config")))
    for name in ("GH_REPO", "GH_HOST", "GIT_LOOPY_TUI_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)


def _unbound_fixture_case(case_id: str) -> dict[str, Any]:
    fixture = json.loads(
        (Path(__file__).parents[2] / "conformance" / "unbound-run-notice.json").read_text(
            encoding="utf-8"
        )
    )
    return next(case for case in fixture["cases"] if case["id"] == case_id)


def _write_trace(trace_path: Path, events: list[dict[str, Any]]) -> None:
    trace_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def _clone_of(root: Path, repository: str) -> Path:
    """A clone whose ``origin`` names ``repository``, as a Lease reads it."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin",
         f"https://github.com/{repository}.git"],
        check=True,
    )
    return root


def test_the_line_printer_client_says_why_an_unbound_run_ended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exit status alone read as a crash; the notice names the root blocker.

    The client resolves the Run's repository from ``origin``, so a blocker that
    is itself a Pool member is left out of the roots, exactly as the fixture
    case with that repository says.
    """
    from git_loopy import run_sidecar

    case = _unbound_fixture_case("all_blocked_chain_names_only_the_roots_outside_the_pool")
    root = _clone_of(tmp_path / "clone", case["repository"])
    trace_path = tmp_path / "run.trace.jsonl"
    _write_trace(trace_path, case["events"])

    rc = run_sidecar.run_terminal_client(
        repository_root=root,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(1),
        release_version="",
        warn=lambda _message: None,
        diagnostics_path=tmp_path / "run.log",
    )

    assert rc == 1, "the notice explains the exit; it does not change it"
    err = capsys.readouterr().err
    for line in case["notice"]:
        assert f"git-loopy: {line}" in err
    assert err.count("No workable issues") == 1


def test_a_clone_with_no_resolvable_repository_names_every_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No repository proves no blocker is a member, so none is dropped."""
    from git_loopy import run_sidecar

    case = _unbound_fixture_case("all_blocked_without_a_repository_names_every_blocker")
    trace_path = tmp_path / "run.trace.jsonl"
    _write_trace(trace_path, case["events"])

    run_sidecar.run_terminal_client(
        repository_root=tmp_path,  # not a clone: no origin to resolve
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(1),
        release_version="",
        warn=lambda _message: None,
        diagnostics_path=tmp_path / "run.log",
    )

    err = capsys.readouterr().err
    for line in case["notice"]:
        assert f"git-loopy: {line}" in err


@pytest.mark.skipif(
    not advisory_locking_available(),
    reason="this platform has no flock advisory locks",
)
def test_the_dashboard_client_says_why_an_unbound_run_ended(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported defect: the Dashboard closed and the shell came back silent.

    The helper leaving with 0 is the Dashboard closing, which used to be the
    last thing the operator saw. The notice now follows it onto the terminal
    the operator gets back, and the helper is told the repository so its own
    held notice names the same roots.
    """
    from git_loopy import run_sidecar, tui_release

    case = _unbound_fixture_case("empty_pool_on_github_says_nothing_is_labelled")
    root = _clone_of(tmp_path / "clone", case["repository"])
    trace_path = tmp_path / "run.trace.jsonl"
    _write_trace(trace_path, case["events"])
    argv_path = tmp_path / "helper-argv.json"
    helper = tmp_path / "fake-dashboard.py"
    helper.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], "
        "'repository': os.environ.get('GIT_LOOPY_TUI_REPOSITORY'), "
        "'tz': os.environ.get('TZ', '<unset>')}))\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    monkeypatch.setattr(
        tui_release, "resolve_runtime_helper", lambda *_args, **_kwargs: helper
    )
    monkeypatch.setattr(run_sidecar, "wait_for_run_control", lambda *_args: True)

    rc = run_sidecar.run_terminal_client(
        repository_root=root,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(0),
        release_version="0.11.0",
        warn=lambda _message: None,
        diagnostics_path=tmp_path / "run.log",
    )

    assert rc == 0
    handed = json.loads(argv_path.read_text(encoding="utf-8"))
    assert "--repository" not in handed["argv"], (
        "an option an older helper does not know makes it exit 2 (ADR-0052)"
    )
    assert handed["repository"] == case["repository"]
    assert handed["tz"] == os.environ.get("TZ", "<unset>"), "the environment was scrubbed"
    err = capsys.readouterr().err
    for line in case["notice"]:
        assert f"git-loopy: {line}" in err


def test_a_run_that_bound_work_ends_without_an_unbound_run_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from git_loopy import run_sidecar

    case = _unbound_fixture_case("a_run_that_bound_work_then_ran_out_is_not_an_unbound_run")
    trace_path = tmp_path / "run.trace.jsonl"
    _write_trace(trace_path, case["events"])

    run_sidecar.run_terminal_client(
        repository_root=tmp_path,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(1),
        release_version="",
        warn=lambda _message: None,
        diagnostics_path=tmp_path / "run.log",
    )

    assert "No workable issues" not in capsys.readouterr().err


def test_a_fork_clone_reads_the_repository_gh_reads_not_origin(tmp_path: Path) -> None:
    """``gh`` reads the upstream a fork came from, so the notice must too."""
    from git_loopy import run_sidecar

    root = _clone_of(tmp_path / "clone", "someone/git-loopy")
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "upstream",
         "https://github.com/bradcstevens/git-loopy.git"],
        check=True,
    )
    assert run_sidecar._run_repository(root) == "bradcstevens/git-loopy"

    subprocess.run(
        ["git", "-C", str(root), "config", "remote.origin.gh-resolved", "base"],
        check=True,
    )
    assert run_sidecar._run_repository(root) == "someone/git-loopy"


def test_a_stray_inherited_repository_never_reaches_the_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The channel carries exactly what this client resolved, or nothing."""
    from git_loopy import run_sidecar

    monkeypatch.setenv(run_sidecar.HELPER_REPOSITORY_ENV, "stray/value")
    assert run_sidecar.HELPER_REPOSITORY_ENV not in run_sidecar._helper_environment(None)
    resolved = run_sidecar._helper_environment("o/r")
    assert resolved[run_sidecar.HELPER_REPOSITORY_ENV] == "o/r"


def test_gh_signed_in_hosts_are_read_from_its_own_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import run_sidecar

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config = tmp_path / "gh"
    config.mkdir()
    (config / "hosts.yml").write_text(
        "github.com:\n    user: someone\nghe.example.com:\n    user: other\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_CONFIG_DIR", str(config))
    assert run_sidecar._gh_signed_in_hosts() == ("ghe.example.com", "github.com")


def test_an_undecodable_remote_resolves_no_repository_and_the_client_still_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resolving the repository is best effort; it must never cost the Run's result."""
    from git_loopy import run_sidecar

    root = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    config = root / ".git" / "config"
    config.write_bytes(
        config.read_bytes() + b'[remote "origin"]\n\turl = /srv/r\xe9po.git\n'
    )
    assert run_sidecar._run_repository(root) is None

    case = _unbound_fixture_case("all_blocked_without_a_repository_names_every_blocker")
    trace_path = tmp_path / "run.trace.jsonl"
    _write_trace(trace_path, case["events"])
    rc = run_sidecar.run_terminal_client(
        repository_root=root,
        config=_config(),
        trace_path=trace_path,
        control_path=run_sidecar.control_path_for_trace(trace_path),
        child=_finished_worker(1),
        release_version="",
        warn=lambda _message: None,
        diagnostics_path=tmp_path / "run.log",
    )
    assert rc == 1
    assert case["notice"][0] in capsys.readouterr().err


def test_an_undecodable_gh_hosts_file_falls_back_to_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import run_sidecar

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config = tmp_path / "gh"
    config.mkdir()
    (config / "hosts.yml").write_bytes(b"gh\xe9.example.com:\n    user: x\n")
    monkeypatch.setenv("GH_CONFIG_DIR", str(config))
    assert run_sidecar._gh_signed_in_hosts(), "an unreadable hosts file resolves to some host"
