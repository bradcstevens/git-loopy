"""Native Continuation command framing for the Python distribution."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

CONTINUATION_CONTRACT_VERSION = "1.0"
RECORD_FORMAT = 1
WRAPPER_CONTRACT_VERSION = "1.2"
EVENT_SCHEMA_VERSION = "1.1"

CAPABILITY_MANIFEST: dict[str, Any] = {
    "continuation_contract_versions": [CONTINUATION_CONTRACT_VERSION],
    "record_formats": [RECORD_FORMAT],
    "wrapper_contract_version": WRAPPER_CONTRACT_VERSION,
    "event_schema_version": EVENT_SCHEMA_VERSION,
    "tracker_adapters": {"github": {"operations": []}},
    "operations": {
        "capabilities": True,
        "publish": False,
        "reconcile": False,
        "record-dispatch-result": False,
        "repair-index": False,
    },
    "instruction_handlers": [],
    "instruction_modes": [],
    "evaluators": [],
    "effect_scopes": [],
    "optional_capabilities": {
        "terminal_rendering": False,
        "concurrent_dispatch": False,
    },
    "continuation_modes": {
        "default": "off",
        "off": True,
        "report": False,
        "execute-frontier": False,
    },
}


def _emit_json(value: dict[str, Any], stream: TextIO) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def _request_bytes(input_path: str | None, stdin: TextIO) -> bytes:
    if input_path is not None:
        return Path(input_path).read_bytes()
    binary = getattr(stdin, "buffer", None)
    if binary is not None:
        return binary.read()
    return stdin.read().encode("utf-8")


def _read_request(input_path: str | None, stdin: TextIO) -> dict[str, Any]:
    try:
        raw = _request_bytes(input_path, stdin)
    except OSError as exc:
        raise ValueError(f"could not read request: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request must be one UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("request must be one UTF-8 JSON object")
    return value


def run_command(
    operation: str,
    *,
    input_path: str | None = None,
    terminal: bool = False,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one public Continuation operation without entering the Run loop."""
    del terminal
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    if operation == "capabilities":
        _emit_json({"ok": True, "capabilities": CAPABILITY_MANIFEST}, stdout)
        return 0

    try:
        _read_request(input_path, stdin)
    except ValueError as exc:
        message = str(exc)
        _emit_json(
            {
                "ok": False,
                "operation": operation,
                "error": {"code": "invalid_request", "message": message},
            },
            stdout,
        )
        print(f"git-loopy continuation: {message}", file=stderr)
        return 1

    message = f"{operation} is not supported by this distribution"
    _emit_json(
        {
            "ok": False,
            "operation": operation,
            "error": {"code": "unsupported_operation", "message": message},
        },
        stdout,
    )
    print(f"git-loopy continuation: {message}", file=stderr)
    return 1
