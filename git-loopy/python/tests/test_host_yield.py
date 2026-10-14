"""Load-free Config and process-boundary proofs for the Host yield policy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from git_loopy import cli, settings


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", "utility"), ("linux", "none"), ("win32", "none")],
)
def test_host_yield_defaults_to_utility_only_on_macos(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
) -> None:
    monkeypatch.setattr("sys.platform", platform)
    config = cli.resolve_config(
        cli.build_parser().parse_args([]), {}, project={}, global_={}
    ).run
    assert config.host_yield == expected


@pytest.mark.parametrize(
    ("env", "project", "global_", "expected"),
    [
        ({}, {}, {"host_yield": "none"}, "none"),
        ({}, {"host_yield": "none"}, {"host_yield": "utility"}, "none"),
        (
            {"GIT_LOOPY_HOST_YIELD": "none"},
            {"host_yield": "utility"},
            {"host_yield": "utility"},
            "none",
        ),
        (
            {"GIT_LOOPY_HOST_YIELD": "utility"},
            {"host_yield": "none"},
            {},
            "utility",
        ),
    ],
)
def test_host_yield_resolves_env_over_project_over_global(
    env: dict[str, str],
    project: dict[str, object],
    global_: dict[str, object],
    expected: str,
) -> None:
    config = cli.resolve_config(
        cli.build_parser().parse_args([]), env, project=project, global_=global_
    ).run
    assert config.host_yield == expected


@pytest.mark.parametrize("value", ["nice", "background", "", "utilty", 1, False])
@pytest.mark.parametrize("source", ["environment", "project", "global"])
def test_unknown_host_yield_is_an_actionable_config_refusal(
    source: str, value: object
) -> None:
    env = {"GIT_LOOPY_HOST_YIELD": str(value)} if source == "environment" else {}
    project = {"host_yield": value} if source == "project" else {}
    global_ = {"host_yield": value} if source == "global" else {}
    with pytest.raises(settings.SettingsError, match=r"host_yield.*utility.*none"):
        cli.resolve_config(
            cli.build_parser().parse_args([]), env, project=project, global_=global_
        )


def test_host_yield_config_command_round_trips_and_refuses_invalid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GIT_LOOPY_HOST_YIELD", raising=False)
    assert cli.main(["config", "set", "--global", "host_yield", "none"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "get", "host_yield"]) == 0
    assert capsys.readouterr().out.strip() == "none"
    assert cli.main(["config", "set", "--global", "host_yield", "background"]) == 1
    assert "utility" in capsys.readouterr().err


@pytest.mark.parametrize("policy", ["none", "utility"])
def test_detached_worker_receives_the_resolved_host_yield_policy(policy: str) -> None:
    from git_loopy import run_sidecar
    from git_loopy.config import RunConfig

    spec = run_sidecar.DetachedRunSpec(
        config=RunConfig(host_yield=policy),
        run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
        started_at_epoch_ms=1_780_000_123_456,
    )
    decoded = run_sidecar.decode_detached_run_spec(
        run_sidecar.encode_detached_run_spec(spec)
    )
    assert decoded.config.host_yield == policy


_RUNTIME_PROBE = """\
import json
import os
import subprocess
import sys
from pathlib import Path

from git_loopy.gate import AgentsMdGateRunner

def priority(pid):
    return int(subprocess.check_output(["ps", "-o", "pri=", "-p", str(pid)], text=True))

tool = subprocess.check_output(
    [sys.executable, "-c",
     "import os, subprocess; print(subprocess.check_output("
     "['ps', '-o', 'pri=', '-p', str(os.getpid())], text=True).strip())"],
    text=True,
)
gate = Path(os.environ["HOST_YIELD_PROBE"]).parent / "gate"
gate.mkdir(exist_ok=True)
(gate / "AGENTS.md").write_text(
    "## Feedback loops\\n\\n| Loop | Command |\\n| --- | --- |\\n"
    "| Priority | ps -o pri= -p $$ > priority |\\n"
)
result = AgentsMdGateRunner().run(gate)
assert result.passed, result
Path(os.environ["HOST_YIELD_PROBE"]).write_text(json.dumps({
    "driver": priority(os.getppid()),
    "runtime": priority(os.getpid()),
    "tool": int(tool),
    "gate": int((gate / "priority").read_text()),
}))
"""


@pytest.fixture
def process_probe(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """A real SDK launches a local stand-in runtime, with no network or CPU load."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("git", "ps", "taskpolicy", "sh"):
        executable = shutil.which(name)
        if executable is not None:
            (tools / name).symlink_to(executable)
    runtime = tmp_path / "runtime"
    runtime.write_text(f"#!{sys.executable}\n{_RUNTIME_PROBE}", encoding="utf-8")
    runtime.chmod(0o755)
    output = tmp_path / "priority.json"
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("GIT_LOOPY_", "_GIT_LOOPY_"))
    }
    env.update({
        "PATH": str(tools),
        "PYTHONPATH": str(Path(cli.__file__).resolve().parent.parent),
        "COPILOT_CLI_PATH": str(runtime),
        "GIT_LOOPY_ROUTE_POLICY": "static",
        "HOST_YIELD_PROBE": str(output),
    })
    return repo, env, output


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS QoS evidence")
@pytest.mark.parametrize("source", ["default", "project", "environment"])
def test_line_printer_clamps_its_driver_runtime_tool_and_gate(
    process_probe: tuple[Path, dict[str, str], Path],
    source: str,
) -> None:
    repo, env, output = process_probe
    expected = _set_probe_policy(repo, env, source)
    result = subprocess.run(
        [sys.executable, "-m", "git_loopy.cli", "1"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=30,
    )
    # The probe exits rather than serving the SDK, and the empty repo cannot Run.
    assert result.returncode != 0
    assert output.exists(), result.stderr
    assert json.loads(output.read_text()) == {
        "driver": expected, "runtime": expected, "tool": expected, "gate": expected,
    }


def _set_probe_policy(repo: Path, env: dict[str, str], source: str) -> int:
    if source == "default":
        return 20
    config_dir = repo / "git-loopy"
    config_dir.mkdir()
    config_dir.joinpath("config.toml").write_text(
        'host_yield = "none"\n' if source == "project" else 'host_yield = "utility"\n'
    )
    if source == "environment":
        env["GIT_LOOPY_HOST_YIELD"] = "none"
    # Opt-out must need no taskpolicy, even inside an already clamped gate.
    (Path(env["PATH"]) / "taskpolicy").unlink()
    return int(subprocess.check_output(
        ["ps", "-o", "pri=", "-p", str(os.getpid())], text=True
    ))


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS QoS evidence")
@pytest.mark.parametrize("source", ["default", "project", "environment"])
def test_detached_worker_is_clamped_without_clamping_its_dashboard_parent(
    process_probe: tuple[Path, dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    from git_loopy import run_sidecar

    repo, env, output = process_probe
    expected = _set_probe_policy(repo, env, source)
    tables = settings.load_configs(repo, env)
    config = cli.resolve_config(
        cli.build_parser().parse_args([]), env,
        project=tables.project, global_=tables.global_,
    ).run
    before = subprocess.check_output(
        ["ps", "-o", "pri=", "-p", str(os.getpid())], text=True
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    diagnostics = repo / "diagnostics.log"
    worker = run_sidecar.spawn_detached_child(
        run_sidecar.DetachedRunSpec(
            config=config,
            run_id="01K3CQ7VJ1GWQ9H8Q6SE2V1D5A",
            started_at_epoch_ms=1_780_000_123_456,
        ),
        diagnostics_path=diagnostics,
        cwd=repo,
    )
    try:
        assert worker.wait(timeout=30) != 0
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
    assert output.exists(), diagnostics.read_text()
    assert json.loads(output.read_text()) == {
        "driver": expected, "runtime": expected, "tool": expected, "gate": expected,
    }
    after = subprocess.check_output(
        ["ps", "-o", "pri=", "-p", str(os.getpid())], text=True
    )
    assert after == before


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS QoS evidence")
def test_missing_taskpolicy_refuses_instead_of_starting_unclamped_work(
    process_probe: tuple[Path, dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_loopy import run_sidecar
    from git_loopy.config import RunConfig
    from git_loopy.host_yield import HostYieldError

    repo, env, output = process_probe
    (Path(env["PATH"]) / "taskpolicy").unlink()
    result = subprocess.run(
        [sys.executable, "-m", "git_loopy.cli", "1"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "taskpolicy on PATH" in result.stderr
    assert "GIT_LOOPY_HOST_YIELD=none" in result.stderr
    monkeypatch.setenv("PATH", env["PATH"])
    with pytest.raises(HostYieldError, match="GIT_LOOPY_HOST_YIELD=none"):
        run_sidecar.spawn_detached_child(
            run_sidecar.DetachedRunSpec(
                config=RunConfig(), run_id="unused", started_at_epoch_ms=0,
            ),
            diagnostics_path=repo / "diagnostics.log",
            cwd=repo,
        )
    assert not output.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS re-exec evidence")
def test_line_printer_emits_config_warnings_once_across_reexec(
    process_probe: tuple[Path, dict[str, str], Path],
) -> None:
    repo, env, _output = process_probe
    env.update({
        "GIT_LOOPY_ROUTE_POLICY": "unselected",
        "GIT_LOOPY_MODEL": "unknown-host-yield-test-model",
        "GIT_LOOPY_PRICING_FILE": "retired-prices.toml",
    })
    result = subprocess.run(
        [sys.executable, "-m", "git_loopy.cli", "1"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert result.stderr.count("GIT_LOOPY_PRICING_FILE is set") == 1
    assert result.stderr.count("unknown-host-yield-test-model") == 1
