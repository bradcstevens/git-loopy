"""Run idle-sleep policy and suspension evidence at the issue's public seams."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from git_loopy import cli, settings
from git_loopy.config import RunConfig
from git_loopy.run_sidecar import (
    DetachedRunSpec,
    decode_detached_run_spec,
    encode_detached_run_spec,
)


@pytest.mark.parametrize(
    ("env", "project", "global_", "expected"),
    [
        ({}, {}, {}, True),
        ({}, {}, {"prevent_idle_sleep": False}, False),
        ({}, {"prevent_idle_sleep": False}, {"prevent_idle_sleep": True}, False),
        (
            {"GIT_LOOPY_PREVENT_IDLE_SLEEP": "off"},
            {"prevent_idle_sleep": True}, {}, False,
        ),
        (
            {"GIT_LOOPY_PREVENT_IDLE_SLEEP": "true"},
            {"prevent_idle_sleep": False}, {}, True,
        ),
    ],
)
def test_idle_sleep_config_precedence_and_detached_round_trip(
    env: dict[str, str],
    project: dict[str, object],
    global_: dict[str, object],
    expected: bool,
) -> None:
    config = cli.resolve_config(
        cli.build_parser().parse_args([]), env, project=project, global_=global_
    ).run
    assert config.prevent_idle_sleep is expected
    spec = DetachedRunSpec(
        config=config, run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
    )
    assert decode_detached_run_spec(
        encode_detached_run_spec(spec)
    ).config.prevent_idle_sleep is expected


@pytest.mark.parametrize("value", ["maybe", "", "2"])
def test_invalid_idle_sleep_environment_is_not_a_silent_opt_out(value: str) -> None:
    with pytest.raises(settings.SettingsError, match="PREVENT_IDLE_SLEEP"):
        cli.resolve_config(
            cli.build_parser().parse_args([]),
            {"GIT_LOOPY_PREVENT_IDLE_SLEEP": value}, project={}, global_={},
        )


def test_run_config_defaults_to_preventing_idle_sleep() -> None:
    assert RunConfig().prevent_idle_sleep is True


def test_idle_sleep_config_command_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GIT_LOOPY_PREVENT_IDLE_SLEEP", raising=False)
    assert cli.main(["config", "set", "--global", "prevent_idle_sleep", "off"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "get", "prevent_idle_sleep"]) == 0
    assert capsys.readouterr().out.strip() == "false"
    assert cli.main(["config", "set", "prevent_idle_sleep", "maybe"]) == 1
    assert "boolean" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["false", 0, None])
def test_idle_sleep_persisted_config_requires_a_boolean(value: object) -> None:
    with pytest.raises(settings.SettingsError, match="prevent_idle_sleep"):
        cli.resolve_config(
            cli.build_parser().parse_args([]), {},
            project={"prevent_idle_sleep": value}, global_={},
        )


@pytest.mark.parametrize(
    ("platform", "enabled", "local", "state"),
    [
        ("linux", True, True, "unsupported"),
        ("win32", True, True, "unsupported"),
        ("darwin", False, True, "disabled"),
        ("darwin", True, False, "remote"),
    ],
)
def test_inapplicable_runs_never_acquire_an_assertion(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    platform: str, enabled: bool, local: bool, state: str,
) -> None:
    from git_loopy.host_sleep import IdleSleepAssertion

    monkeypatch.setattr(sys, "platform", platform)
    with caplog.at_level(logging.INFO):
        assertion = IdleSleepAssertion(enabled=enabled, local=local)
        assertion.start(logging.getLogger("test"))
        assertion.close()
    assert f"idle-sleep assertion: {state}" in caplog.text


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS assertion proof")
@pytest.mark.parametrize("ending", ["normal", "exception", "kill"])
def test_native_assertion_is_process_owned_and_released_on_every_exit(
    ending: str,
) -> None:
    code = """
import logging, os, sys
from git_loopy.host_sleep import IdleSleepAssertion
assertion = IdleSleepAssertion(enabled=True, local=True)
assertion.start(logging.getLogger("probe"))
print(os.getpid(), flush=True)
try:
    sys.stdin.readline()
    if sys.argv[1] == "exception":
        raise RuntimeError("crash")
finally:
    assertion.close()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).parent.parent)}
    child = subprocess.Popen(
        [sys.executable, "-c", code, ending], env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # The system view, not a mock of IOKit, proves both type and owner.
        import select

        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 10)[0], "assertion startup stalled"
        assert child.stdout.readline().strip() == str(child.pid)
        report = subprocess.check_output(["pmset", "-g", "assertions"], text=True)
        owned = [line for line in report.splitlines() if f"pid {child.pid}(" in line]
        assert len(owned) == 1, report
        assert "PreventUserIdleSystemSleep" in owned[0]
        assert "git-loopy Run" in owned[0]
        if ending == "kill":
            child.kill()
        _, stderr = child.communicate("\n" if ending != "kill" else None, timeout=10)
        assert child.returncode == (0 if ending == "normal" else 1 if ending == "exception" else -9), stderr
        deadline = time.monotonic() + 5
        while True:
            report = subprocess.check_output(["pmset", "-g", "assertions"], text=True)
            if f"pid {child.pid}(" not in report:
                break
            assert time.monotonic() < deadline, report
            time.sleep(0.05)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
