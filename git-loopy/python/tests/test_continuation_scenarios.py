"""Public Python command adapter for the Continuation scenario fixture."""

from __future__ import annotations

import base64
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli


CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
SCRIPTED_GITHUB = Path(__file__).with_name("scripted_github.py")
FIXTURE = json.loads(
    (CONFORMANCE_DIR / "continuation-scenarios.json").read_text(encoding="utf-8")
)


def _install_scripted_github(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    script_path = tmp_path / "github-script.json"
    script_path.write_text(
        json.dumps(script, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    state_path = tmp_path / "github-script-state"
    log_path = tmp_path / "github-calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\nexec "
        f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPTED_GITHUB))} \"$@\"\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_LOG", str(log_path))
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT", str(script_path))
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_STATE", str(state_path))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    return state_path, log_path, fake_gh


def _consumed_steps(state_path: Path) -> int:
    return int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0


def test_python_scripted_github_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = FIXTURE["github_transport_probe"]
    state_path, log_path, fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        probe["github_script"],
    )

    for invocation in probe["invocations"]:
        stdin = invocation.get("stdin")
        if "stdin_json" in invocation:
            stdin = json.dumps(invocation["stdin_json"], separators=(",", ":"))
        completed = subprocess.run(
            [str(fake_gh), *invocation["arguments"]],
            input=stdin or "",
            text=True,
            capture_output=True,
            check=False,
            env=os.environ.copy(),
        )
        expected = invocation["expected"]
        assert completed.returncode == expected["exit_code"]
        if "stdout_json" in expected:
            assert json.loads(completed.stdout) == expected["stdout_json"]
        else:
            assert completed.stdout == expected["stdout"]
        assert expected["stderr_contains"].lower() in completed.stderr.lower()

    assert _consumed_steps(state_path) == len(probe["github_script"])
    assert log_path.read_text(encoding="utf-8").splitlines() == probe[
        "expected_github_calls"
    ]


@pytest.mark.parametrize(
    "scenario",
    [
        scenario
        for scenario in FIXTURE["scenarios"]
        if "python" in scenario.get("distributions", ["python"])
    ],
    ids=lambda scenario: scenario["id"],
)
def test_python_native_continuation_scenario(
    scenario: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = list(scenario["arguments"])
    request = scenario["request"]
    stdin = ""
    if request is not None:
        content = request.get("raw")
        if content is None and "json" in request:
            content = json.dumps(request["json"], separators=(",", ":"))
        if request["source"] == "file":
            input_path = tmp_path / "request.json"
            if "base64" in request:
                input_path.write_bytes(base64.b64decode(request["base64"]))
            else:
                input_path.write_text(content, encoding="utf-8")
            arguments = [
                str(input_path) if value == "$INPUT_FILE" else value
                for value in arguments
            ]
        else:
            stdin = content

    state_path, github_log, _fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        scenario["github_script"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    try:
        exit_code = cli.main(arguments)
    except SystemExit as exc:
        if exc.code is None:
            exit_code = 0
        elif isinstance(exc.code, int):
            exit_code = exc.code
        else:
            exit_code = 1

    captured = capsys.readouterr()
    expected = scenario["expected"]
    assert exit_code == expected["exit_code"], captured.err
    if expected["stdout"] is None:
        assert captured.out == ""
    else:
        assert json.loads(captured.out) == expected["stdout"]
        assert len(captured.out.splitlines()) == 1
    if expected["stderr_contains"] is None:
        assert captured.err == ""
    else:
        assert expected["stderr_contains"].lower() in captured.err.lower()
    github_calls = (
        github_log.read_text(encoding="utf-8").splitlines()
        if github_log.exists()
        else []
    )
    assert github_calls == expected["github_calls"]
    assert _consumed_steps(state_path) == len(scenario["github_script"])


@pytest.mark.parametrize(
    "workflow",
    [
        workflow
        for workflow in FIXTURE["workflows"]
        if "python" in workflow["distributions"]
    ],
    ids=lambda workflow: workflow["id"],
)
def test_python_native_continuation_workflow(
    workflow: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path, github_log, _fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        workflow["github_script"],
    )

    for command in workflow["commands"]:
        request = command["request"]
        stdin = json.dumps(request["json"], separators=(",", ":"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        try:
            exit_code = cli.main(list(command["arguments"]))
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1

        captured = capsys.readouterr()
        expected = command["expected"]
        assert exit_code == expected["exit_code"], captured.err
        assert json.loads(captured.out) == expected["stdout"]
        assert len(captured.out.splitlines()) == 1
        if expected["stderr_contains"] is None:
            assert captured.err == ""
        else:
            assert expected["stderr_contains"].lower() in captured.err.lower()

    assert _consumed_steps(state_path) == len(workflow["github_script"])
    assert github_log.read_text(encoding="utf-8").splitlines() == workflow[
        "expected_github_calls"
    ]
