from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.prompt import PromptMetadataError
from git_loopy.skill_policy import (
    SkillCatalog,
    SkillCatalogWinner,
    SkillInventoryUnavailable,
    MissingRequiredSkills,
)
from git_loopy.skill_install import SkillInstallError, installed_catalog_dir, installed_profiles_dir
from git_loopy.skill_run_preflight import resolve_run_skill_preflight
from tests.fakes import FakeGitClient
from tests.profile_fixtures import seed_profile_install

_PROFILE_CONFORMANCE = json.loads(
    (Path(__file__).resolve().parents[2] / "conformance/agent-profiles.json").read_text(),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration", ["[]", "tdd", "\n  - tdd"])
async def test_run_refuses_retired_required_skills_in_custom_instructions(
    tmp_path: Path, declaration: str,
) -> None:
    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return SkillCatalog(winners={})

    with pytest.raises(PromptMetadataError, match="Remove.*required-skills"):
        await resolve_run_skill_preflight(
            object(),
            config=RunConfig(),
            git=FakeGitClient(tmp_path),
            prompt_text=f"---\nrequired-skills: {declaration}\n---\n# Custom\n",
            repo_root=tmp_path,
            installed_skills_dir=tmp_path / "skills",
            workspace=tmp_path / "workspace",
            discoverer=discoverer,
        )


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
    packaged = seed_profile_install(os.environ, ("code-review",)).root
    project_skill = _write_skill(repo / ".copilot" / "skills", "project-skill")
    discovered = SkillCatalog(
        winners={
            "project-skill": SkillCatalogWinner(
                "project-skill",
                "project",
                path=project_skill / "SKILL.md",
                project_path=project_skill,
            ),
            "code-review": SkillCatalogWinner(
                "code-review",
                "packaged",
                path=packaged / "code-review" / "SKILL.md",
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
                    names=("code-review", "project-skill"),
                )
            )
        ),
        git=FakeGitClient(repo, tracked_paths=(project_skill,)),
        prompt_text="# Custom Run instructions\n",
        repo_root=repo,
        installed_skills_dir=packaged,
        workspace=tmp_path / "workspace",
        discoverer=discoverer,
    )

    assert calls == [(client, tmp_path / "workspace" / "discovery")]
    assert result.exposure.policy.enabled == ("code-review", "project-skill")
    assert result.exposure.policy.required == ("code-review",)
    assert tuple(sorted(path.name for path in result.exposure.directory.iterdir())) == (
        "code-review",
        "project-skill",
    )
    assert result.event_payload == {
        "base_scope": "project",
        "enabled": ["code-review", "project-skill"],
        "fallback": None,
        "legacy_denied": [],
        "migration_warning": False,
        "required": ["code-review"],
        "source_kinds": {
            "project-skill": "project",
            "code-review": "packaged",
        },
    }
    assert str(tmp_path) not in repr(result.event_payload)


@pytest.mark.asyncio
async def test_unconfigured_run_uses_only_packaged_minimal_winners(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    packaged = seed_profile_install(os.environ, ("code-review",)).root
    required = packaged / "code-review"
    personal = _write_skill(tmp_path / "personal", "personal")
    project = _write_skill(repo / ".copilot" / "skills", "opportunistic")

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return SkillCatalog(
            winners={
                "code-review": SkillCatalogWinner(
                    "code-review", "personal", path=required / "SKILL.md"
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
        prompt_text="# Custom Run instructions\n",
        repo_root=repo,
        installed_skills_dir=packaged,
        workspace=tmp_path / "workspace",
        discoverer=discoverer,
    )

    assert result.exposure.policy.enabled == ("code-review",)
    assert dict(result.exposure.policy.source_kinds) == {"code-review": "packaged"}
    assert [path.name for path in result.exposure.directory.iterdir()] == ["code-review"]


@pytest.mark.asyncio
async def test_unavailable_inventory_fails_only_an_explicit_policy(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    packaged = seed_profile_install(os.environ, ("code-review",)).root

    async def unavailable(_client: object, **_kwargs: object) -> SkillCatalog:
        raise RuntimeError("inventory unavailable")

    with pytest.raises(SkillInventoryUnavailable) as raised:
        await resolve_run_skill_preflight(
            object(),
            config=RunConfig(
                skill_policy=SkillPolicyInputs(
                    global_=SkillPolicyInput(present=True, names=("code-review",))
                )
            ),
            git=FakeGitClient(repo),
            prompt_text="# Custom Run instructions\n",
            repo_root=repo,
            installed_skills_dir=packaged,
            workspace=tmp_path / "configured-workspace",
            discoverer=unavailable,
        )

    assert raised.value.names == ("code-review",)

    fallback = await resolve_run_skill_preflight(
        object(),
        config=RunConfig(),
        git=FakeGitClient(repo),
        prompt_text="# Custom Run instructions\n",
        repo_root=repo,
        installed_skills_dir=packaged,
        workspace=tmp_path / "fallback-workspace",
        discoverer=unavailable,
    )

    assert fallback.exposure.policy.enabled == ("code-review",)
    assert fallback.exposure.policy.fallback == "minimal"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _PROFILE_CONFORMANCE["preflight_cases"], ids=lambda c: c["id"])
async def test_preflight_requires_the_union_of_all_in_run_profiles(
    tmp_path: Path, case: dict,
) -> None:
    from git_loopy.skill_catalog import build_skill_catalog

    root = installed_catalog_dir(os.environ)

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return build_skill_catalog((), repo_root=tmp_path, installed_skills_dir=root)

    required = tuple(case["required"])
    missing = case["missing"]
    config = RunConfig() if case["enabled"] is None else RunConfig(
        skill_policy=SkillPolicyInputs(
            project=SkillPolicyInput(
                present=True, names=tuple(case["enabled"]),
            ),
        ),
    )
    request = resolve_run_skill_preflight(
        object(), config=config, git=FakeGitClient(tmp_path),
        prompt_text="# Custom\n", repo_root=tmp_path,
        installed_skills_dir=root, workspace=tmp_path / "workspace",
        discoverer=discoverer,
    )
    if missing is not None:
        with pytest.raises(MissingRequiredSkills) as error:
            await request
        assert error.value.names == (missing,)
    else:
        result = await request
        assert result.exposure.policy.required == required
        assert result.exposure.policy.enabled == required


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "drifted"])
async def test_preflight_refuses_an_unmatched_profile_root_before_discovery(
    tmp_path: Path, damage: str,
) -> None:
    root = installed_profiles_dir(os.environ)
    if damage == "missing":
        root.rename(root.with_name("saved-profiles"))
    else:
        (root / "docs.md").write_text("edited\n", encoding="utf-8")

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        pytest.fail("Discovery ran before the profile refusal")

    with pytest.raises(SkillInstallError, match="profiles.*matching"):
        await resolve_run_skill_preflight(
            object(), config=RunConfig(), git=FakeGitClient(tmp_path),
            prompt_text="# Custom\n", repo_root=tmp_path,
            installed_skills_dir=installed_catalog_dir(os.environ),
            workspace=tmp_path / "workspace", discoverer=discoverer,
        )
