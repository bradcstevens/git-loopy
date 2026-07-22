from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.skill_policy import (
    SkillCatalog,
    SkillCatalogWinner,
    SkillInventoryUnavailable,
)
from git_loopy.skill_run_preflight import resolve_run_skill_preflight
from tests.fakes import FakeGitClient


def _write_skill(root: Path, name: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n",
        encoding="utf-8",
    )
    return skill


@pytest.mark.asyncio
async def test_configured_run_resolves_and_materializes_one_frozen_exposure(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    packaged = tmp_path / "packaged"
    project_skill = _write_skill(repo / ".copilot" / "skills", "project-skill")
    _write_skill(packaged, "required")
    discovered = SkillCatalog(
        winners={
            "project-skill": SkillCatalogWinner(
                "project-skill",
                "project",
                path=project_skill / "SKILL.md",
                project_path=project_skill,
            ),
            "required": SkillCatalogWinner(
                "required",
                "packaged",
                path=packaged / "required" / "SKILL.md",
            ),
        }
    )
    calls: list[tuple[object, Path]] = []

    async def discoverer(client: object, **kwargs: object) -> SkillCatalog:
        calls.append((client, Path(str(kwargs["discovery_directory"]))))
        return discovered

    client = object()
    result = await resolve_run_skill_preflight(
        client,
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                project=SkillPolicyInput(
                    present=True,
                    names=("required", "project-skill"),
                )
            )
        ),
        git=FakeGitClient(repo, tracked_paths=(project_skill,)),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        repo_root=repo,
        packaged_skills_dir=packaged,
        workspace=tmp_path / "workspace",
        discoverer=discoverer,
    )

    assert calls == [(client, tmp_path / "workspace" / "discovery")]
    assert result.exposure.policy.enabled == ("project-skill", "required")
    assert result.exposure.policy.required == ("required",)
    assert tuple(sorted(path.name for path in result.exposure.directory.iterdir())) == (
        "project-skill",
        "required",
    )
    assert result.event_payload == {
        "base_scope": "project",
        "enabled": ["project-skill", "required"],
        "fallback": None,
        "legacy_denied": [],
        "migration_warning": False,
        "required": ["required"],
        "source_kinds": {
            "project-skill": "project",
            "required": "packaged",
        },
    }
    assert str(tmp_path) not in repr(result.event_payload)


@pytest.mark.asyncio
async def test_unconfigured_run_uses_only_packaged_minimal_winners(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    packaged = tmp_path / "packaged"
    required = _write_skill(packaged, "required")
    personal = _write_skill(tmp_path / "personal", "personal")
    project = _write_skill(repo / ".copilot" / "skills", "opportunistic")

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return SkillCatalog(
            winners={
                "required": SkillCatalogWinner(
                    "required", "personal", path=required / "SKILL.md"
                ),
                "personal": SkillCatalogWinner(
                    "personal", "personal", path=personal / "SKILL.md"
                ),
                "opportunistic": SkillCatalogWinner(
                    "opportunistic",
                    "project",
                    path=project / "SKILL.md",
                    project_path=project,
                ),
            }
        )

    result = await resolve_run_skill_preflight(
        object(),
        config=RunConfig(),
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        repo_root=repo,
        packaged_skills_dir=packaged,
        workspace=tmp_path / "workspace",
        discoverer=discoverer,
    )

    assert result.exposure.policy.enabled == ("required",)
    assert dict(result.exposure.policy.source_kinds) == {"required": "packaged"}
    assert [path.name for path in result.exposure.directory.iterdir()] == ["required"]


@pytest.mark.asyncio
async def test_unavailable_inventory_fails_only_an_explicit_policy(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    packaged = tmp_path / "packaged"
    _write_skill(packaged, "required")

    async def unavailable(_client: object, **_kwargs: object) -> SkillCatalog:
        raise RuntimeError("inventory unavailable")

    with pytest.raises(SkillInventoryUnavailable) as raised:
        await resolve_run_skill_preflight(
            object(),
            config=RunConfig(
                skill_policy=SkillPolicyInputs(
                    global_=SkillPolicyInput(present=True, names=("required",))
                )
            ),
            git=FakeGitClient(repo),
            prompt_text="---\nrequired-skills:\n  - required\n---\n",
            repo_root=repo,
            packaged_skills_dir=packaged,
            workspace=tmp_path / "configured-workspace",
            discoverer=unavailable,
        )

    assert raised.value.names == ("required",)

    fallback = await resolve_run_skill_preflight(
        object(),
        config=RunConfig(),
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        repo_root=repo,
        packaged_skills_dir=packaged,
        workspace=tmp_path / "fallback-workspace",
        discoverer=unavailable,
    )

    assert fallback.exposure.policy.enabled == ("required",)
    assert fallback.exposure.policy.fallback == "minimal"
