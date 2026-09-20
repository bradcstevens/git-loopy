"""Tests for the shared Copilot runtime construction seam."""

from __future__ import annotations

import sys
import tomllib
from functools import partial
from importlib.metadata import version
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from copilot import CopilotClient, CopilotSession, RuntimeConnection

from git_loopy.config import RunConfig
from git_loopy.copilot_client import (
    make_copilot_client,
    resolve_copilot_base_directory,
)
from git_loopy.persist import EventLogWriter
from git_loopy.session import IterationSession
from git_loopy.sinks import SinkFanout


def test_base_directory_honors_copilot_home_then_home(tmp_path: Path) -> None:
    explicit = tmp_path / "copilot-state"

    assert resolve_copilot_base_directory(
        {"COPILOT_HOME": str(explicit), "HOME": str(tmp_path / "home")}
    ) == explicit
    assert resolve_copilot_base_directory(
        {"HOME": str(tmp_path / "home")}
    ) == tmp_path / "home" / ".copilot"


def test_shared_factory_applies_runtime_base_working_directory_and_telemetry(
    tmp_path: Path,
) -> None:
    captured: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **options: object) -> None:
            captured.append(options)

    working_directory = tmp_path / "repo"
    telemetry = {"exporter": "test"}

    client = make_copilot_client(
        working_directory=working_directory,
        env={"HOME": str(tmp_path / "home")},
        telemetry_config=telemetry,
        client_type=FakeClient,
    )

    assert isinstance(client, FakeClient)
    assert captured == [
        {
            "working_directory": str(working_directory),
            "base_directory": str(tmp_path / "home" / ".copilot"),
            "telemetry": telemetry,
        }
    ]


def test_installed_sdk_matches_manifest_and_lock() -> None:
    project = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((project / "uv.lock").read_text(encoding="utf-8"))
    installed = version("github-copilot-sdk")

    assert f"github-copilot-sdk=={installed}" in manifest["project"]["dependencies"]
    sdk = next(package for package in lock["package"] if package["name"] == "github-copilot-sdk")
    assert sdk["version"] == installed


@pytest.mark.parametrize("telemetry", [None, {}, {"otlp_endpoint": "http://localhost:4318"}])
def test_shared_factory_accepts_locked_sdk_constructor(
    tmp_path: Path, telemetry: dict[str, str] | None
) -> None:
    # This local executable is never started; construction must not download a runtime.
    constructor = partial(
        CopilotClient, connection=RuntimeConnection.for_stdio(path=sys.executable)
    )
    client = make_copilot_client(
        working_directory=tmp_path,
        env={"HOME": str(tmp_path)},
        telemetry_config=telemetry,
        client_type=constructor,
    )

    assert isinstance(client, CopilotClient)


async def test_iteration_session_accepts_locked_sdk_signatures(tmp_path: Path) -> None:
    client = create_autospec(CopilotClient, instance=True, spec_set=True)
    session = create_autospec(CopilotSession, instance=True, spec_set=True)
    client.create_session.return_value = session

    with EventLogWriter(tmp_path / "events.jsonl") as event_log:
        async with IterationSession(
            client,
            config=RunConfig(),
            event_log=event_log,
            sinks=SinkFanout([]),
            run_id="sdk-contract",
            iter_num=1,
            model="claude-opus-5",
            reasoning_effort="max",
            context_tier="long_context",
            working_directory=str(tmp_path),
        ) as active:
            assert active is session
            await active.send_and_wait("Offline contract probe", timeout=120)

    client.create_session.assert_awaited_once()
    session.send_and_wait.assert_awaited_once_with("Offline contract probe", timeout=120)
    session.disconnect.assert_awaited_once_with()
