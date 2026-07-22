"""Public Python command adapter for the Continuation scenario fixture."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli


CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
FIXTURE = json.loads(
    (CONFORMANCE_DIR / "continuation-scenarios.json").read_text(encoding="utf-8")
)


def _expected_stdout(value: Any) -> Any:
    if (
        isinstance(value, dict)
        and value == {"$fixture": "capability_manifest"}
    ):
        return FIXTURE["distribution_capability_manifests"]["python"]
    if isinstance(value, dict):
        return {key: _expected_stdout(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expected_stdout(item) for item in value]
    return value


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

    github_log = tmp_path / "github-calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$GIT_LOOPY_SCRIPTED_GITHUB_LOG\"\n"
        "exit 97\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_LOG", str(github_log))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
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
        assert json.loads(captured.out) == _expected_stdout(expected["stdout"])
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
    script_path = tmp_path / "github-script.json"
    script_path.write_text(
        json.dumps(workflow["github_script"], separators=(",", ":")),
        encoding="utf-8",
    )
    state_path = tmp_path / "github-script-state"
    github_log = tmp_path / "github-calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

command = " ".join(sys.argv[1:])
log_path = Path(os.environ["GIT_LOOPY_SCRIPTED_GITHUB_LOG"])
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(command + "\\n")
script = json.loads(
    Path(os.environ["GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"]).read_text(encoding="utf-8")
)
state_path = Path(os.environ["GIT_LOOPY_SCRIPTED_GITHUB_STATE"])
index = int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0
if index >= len(script):
    print(f"unlisted GitHub call: {command}", file=sys.stderr)
    raise SystemExit(98)
step = script[index]
if command != step["command"]:
    print(f"expected GitHub call {step['command']!r}, got {command!r}", file=sys.stderr)
    raise SystemExit(98)
if "expected_stdin_json" in step:
    actual_stdin = json.load(sys.stdin)
    if actual_stdin != step["expected_stdin_json"]:
        print("GitHub call stdin did not match fixture", file=sys.stderr)
        raise SystemExit(98)
state_path.write_text(str(index + 1), encoding="utf-8")
if "stdout_json" in step:
    print(json.dumps(step["stdout_json"], separators=(",", ":")))
else:
    sys.stdout.write(step.get("stdout", ""))
raise SystemExit(step["exit_code"])
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_LOG", str(github_log))
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT", str(script_path))
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_STATE", str(state_path))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

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

    assert int(state_path.read_text(encoding="utf-8")) == len(
        workflow["github_script"]
    )
    assert github_log.read_text(encoding="utf-8").splitlines() == workflow[
        "expected_github_calls"
    ]
