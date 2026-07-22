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
        return FIXTURE["capability_manifest"]
    if isinstance(value, dict):
        return {key: _expected_stdout(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expected_stdout(item) for item in value]
    return value


@pytest.mark.parametrize(
    "scenario",
    FIXTURE["scenarios"],
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
    assert exit_code == expected["exit_code"]
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
