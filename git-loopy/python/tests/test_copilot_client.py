"""Tests for the shared Copilot runtime construction seam."""

from __future__ import annotations

from pathlib import Path

from git_loopy.copilot_client import (
    make_copilot_client,
    resolve_copilot_base_directory,
)


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
