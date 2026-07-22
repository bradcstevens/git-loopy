#!/usr/bin/env python3
"""Deterministic ``gh`` process used by Continuation command scenarios."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        print(f"missing scripted GitHub environment variable: {name}", file=sys.stderr)
        raise SystemExit(98)
    return Path(value)


def _load_script(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        print("scripted GitHub fixture must be an array", file=sys.stderr)
        raise SystemExit(98)
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = " ".join(arguments)
    log_path = _path("GIT_LOOPY_SCRIPTED_GITHUB_LOG")
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(command + "\n")

    script = _load_script(_path("GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"))
    state_path = _path("GIT_LOOPY_SCRIPTED_GITHUB_STATE")
    index = int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0
    if index >= len(script):
        print(f"unlisted GitHub call: {command}", file=sys.stderr)
        return 98

    step = script[index]
    expected_command = step.get("command")
    if command != expected_command:
        print(
            f"expected GitHub call {expected_command!r}, got {command!r}",
            file=sys.stderr,
        )
        return 98

    if "expected_stdin_json" in step:
        try:
            actual_stdin = json.load(sys.stdin)
        except json.JSONDecodeError:
            print("GitHub call stdin was not valid JSON", file=sys.stderr)
            return 98
        if actual_stdin != step["expected_stdin_json"]:
            print("GitHub call stdin did not match fixture", file=sys.stderr)
            return 98
    elif "expected_stdin" in step:
        if sys.stdin.read() != step["expected_stdin"]:
            print("GitHub call stdin did not match fixture", file=sys.stderr)
            return 98

    state_path.write_text(str(index + 1), encoding="utf-8")
    if "stdout_json" in step:
        sys.stdout.write(
            json.dumps(step["stdout_json"], ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
    else:
        sys.stdout.write(str(step.get("stdout", "")))
    sys.stderr.write(str(step.get("stderr", "")))
    return int(step["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
