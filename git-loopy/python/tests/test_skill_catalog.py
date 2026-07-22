"""Production-seam tests for normalized Skill catalog discovery."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_loopy.skill_catalog import (
    SdkSkillSurfaceError,
    build_skill_catalog,
    discover_skill_catalog,
    validate_sdk_skill_surface,
)


def _write_skill(root: Path, directory: str, *, name: str, description: str) -> Path:
    skill_dir = root / directory
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n"
        "Instructions that catalog discovery must not need.\n",
        encoding="utf-8",
    )
    return skill_md


def test_catalog_resolves_project_then_copilot_then_packaged_winners(
    tmp_path: Path,
) -> None:
    project_skills = tmp_path / "repo" / ".copilot" / "skills"
    packaged_skills = tmp_path / "packaged"
    project_path = _write_skill(
        project_skills,
        "alpha-dir",
        name="alpha",
        description="Project alpha",
    )
    _write_skill(
        packaged_skills,
        "beta",
        name="beta",
        description="Packaged beta",
    )
    _write_skill(
        packaged_skills,
        "gamma",
        name="gamma",
        description="Packaged gamma",
    )
    copilot_skills = [
        SimpleNamespace(
            name="alpha",
            description="Personal alpha",
            enabled=False,
            source="personal-copilot",
            user_invocable=True,
            path="/Users/operator/.copilot/skills/alpha/SKILL.md",
            plugin_name=None,
        ),
        SimpleNamespace(
            name="beta",
            description="Plugin beta",
            enabled=True,
            source="plugin",
            user_invocable=False,
            path="/Users/operator/.copilot/plugins/example/skills/beta/SKILL.md",
            plugin_name="example",
        ),
    ]

    catalog = build_skill_catalog(
        copilot_skills,
        repo_root=tmp_path / "repo",
        packaged_skills_dir=packaged_skills,
    )

    assert tuple(catalog.winners) == ("alpha", "beta", "gamma")
    assert catalog.winners["alpha"].description == "Project alpha"
    assert catalog.winners["alpha"].source_kind == "project"
    assert catalog.winners["alpha"].copilot_enabled is False
    assert catalog.winners["alpha"].path == project_path
    assert catalog.winners["alpha"].project_path == project_path.parent
    assert catalog.winners["beta"].description == "Plugin beta"
    assert catalog.winners["beta"].source_kind == "plugin"
    assert catalog.winners["beta"].plugin_name == "example"
    assert catalog.winners["gamma"].source_kind == "packaged"
    assert catalog.winners["gamma"].copilot_enabled is None


def test_pinned_sdk_skill_surface_guard_rejects_source_enum_drift() -> None:
    class DriftedSkillSource(StrEnum):
        PROJECT = "project"

    with pytest.raises(SdkSkillSurfaceError, match="SkillSource"):
        validate_sdk_skill_surface(source_type=DriftedSkillSource)


def test_pinned_sdk_skill_surface_guard_accepts_locked_sdk() -> None:
    validate_sdk_skill_surface()


def test_pinned_sdk_skill_surface_guard_rejects_missing_disabled_option() -> None:
    class DriftedClient:
        async def create_session(self) -> object:
            return object()

    with pytest.raises(SdkSkillSurfaceError, match="disabled_skills"):
        validate_sdk_skill_surface(client_type=DriftedClient)


@pytest.mark.asyncio
async def test_discovery_uses_typed_metadata_rpc_without_starting_agent_work(
    tmp_path: Path,
) -> None:
    packaged_skills = tmp_path / "packaged"
    _write_skill(
        packaged_skills,
        "fallback",
        name="fallback",
        description="Fallback",
    )

    class FakeSkillsApi:
        def __init__(self) -> None:
            self.list_calls = 0

        async def list(self) -> SimpleNamespace:
            self.list_calls += 1
            return SimpleNamespace(skills=[])

    class FakeSession:
        def __init__(self) -> None:
            self.rpc = SimpleNamespace(skills=FakeSkillsApi())
            self.disconnect_calls = 0

        async def disconnect(self) -> None:
            self.disconnect_calls += 1

    class FakeClient:
        def __init__(self) -> None:
            self.session = FakeSession()
            self.create_calls: list[dict[str, object]] = []

        async def create_session(self, **options: object) -> FakeSession:
            self.create_calls.append(options)
            return self.session

    client = FakeClient()
    discovery_directory = tmp_path / "isolated"
    discovery_directory.mkdir()

    catalog = await discover_skill_catalog(
        client,
        repo_root=tmp_path,
        packaged_skills_dir=packaged_skills,
        discovery_directory=discovery_directory,
        validate_surface=False,
    )

    assert tuple(catalog.winners) == ("fallback",)
    assert client.create_calls == [
        {
            "working_directory": str(discovery_directory),
            "enable_skills": True,
            "enable_config_discovery": True,
            "skip_custom_instructions": True,
        }
    ]
    assert client.session.rpc.skills.list_calls == 1
    assert client.session.disconnect_calls == 1
