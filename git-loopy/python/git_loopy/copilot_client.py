"""Shared construction for management and long-lived Copilot clients."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .telemetry import otel as telemetry

_DEFAULT_TELEMETRY = object()


def _construct_client(
    constructor: Callable[..., Any],
    options: Mapping[str, object],
) -> Any:
    return constructor(**options)


def resolve_copilot_base_directory(
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the one Copilot data root shared by every client."""
    environment = os.environ if env is None else env
    configured = environment.get("COPILOT_HOME")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    home = environment.get("HOME")
    base = Path(home).expanduser() if home and home.strip() else Path.home()
    return (base / ".copilot").resolve()


def make_copilot_client(
    *,
    working_directory: Path | None = None,
    env: Mapping[str, str] | None = None,
    telemetry_config: object = _DEFAULT_TELEMETRY,
    client_type: Callable[..., Any] | None = None,
) -> Any:
    """Build a Copilot client with the shared runtime identity and telemetry."""
    if client_type is None:
        from copilot import CopilotClient

        constructor: Callable[..., Any] = CopilotClient
    else:
        constructor = client_type
    if telemetry_config is _DEFAULT_TELEMETRY:
        telemetry_config = telemetry.build_sdk_telemetry_config()
    directory = (working_directory or Path.cwd()).resolve()
    return _construct_client(
        constructor,
        {
            "working_directory": str(directory),
            "base_directory": str(resolve_copilot_base_directory(env)),
            "telemetry": telemetry_config,
        },
    )
