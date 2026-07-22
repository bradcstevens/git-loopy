"""Tests for the closed-world SDK Skill exposure projection."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.skill_exposure import SkillExposureError, build_skill_exposure
from git_loopy.skill_policy import (
    EffectiveSkillPolicy,
    SkillCatalog,
    SkillCatalogWinner,
    SkillPolicyScope,
)


def _write_skill(root: Path, name: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\nInstructions.\n",
        encoding="utf-8",
    )
    return skill


def test_projection_contains_only_enabled_catalog_winners(tmp_path: Path) -> None:
    source = tmp_path / "source"
    enabled = _write_skill(source, "enabled")
    _write_skill(source, "disabled")
    catalog = SkillCatalog(
        winners={
            "disabled": SkillCatalogWinner(
                "disabled", "plugin", path=source / "disabled" / "SKILL.md"
            ),
            "enabled": SkillCatalogWinner(
                "enabled", "plugin", path=enabled / "SKILL.md"
            ),
        }
    )
    policy = EffectiveSkillPolicy(
        enabled=("enabled",),
        required=(),
        legacy_denied=(),
        source_kinds={"enabled": "plugin"},
        base_scope=SkillPolicyScope.PROJECT,
    )

    exposure = build_skill_exposure(
        policy,
        catalog,
        directory=tmp_path / "exposure",
    )

    assert exposure.policy is policy
    assert exposure.catalog is catalog
    assert exposure.skill_directories == (str(tmp_path / "exposure"),)
    assert exposure.disabled_skills == ("disabled",)
    assert (tmp_path / "exposure" / "enabled" / "SKILL.md").is_file()
    assert not (tmp_path / "exposure" / "disabled").exists()


def test_projection_rejects_nonempty_directory_without_overwriting_it(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "exposure"
    directory.mkdir()
    unrelated = directory / "unrelated"
    unrelated.mkdir()
    policy = EffectiveSkillPolicy(
        enabled=(),
        required=(),
        legacy_denied=(),
        source_kinds={},
        base_scope=SkillPolicyScope.PROJECT,
    )

    with pytest.raises(SkillExposureError, match="must be empty"):
        build_skill_exposure(
            policy,
            SkillCatalog(),
            directory=directory,
        )

    assert unrelated.is_dir()


def test_projection_rejects_links_outside_enabled_skill(
    tmp_path: Path,
) -> None:
    source = tmp_path / "plugin"
    enabled = _write_skill(source, "enabled")
    disabled = _write_skill(source, "disabled")
    linked_resource = enabled / "shared.md"
    try:
        linked_resource.symlink_to(disabled / "SKILL.md")
    except OSError:
        pytest.skip("filesystem does not permit symlink creation")
    catalog = SkillCatalog(
        winners={
            "disabled": SkillCatalogWinner(
                "disabled", "plugin", path=disabled / "SKILL.md"
            ),
            "enabled": SkillCatalogWinner(
                "enabled", "plugin", path=enabled / "SKILL.md"
            ),
        }
    )
    policy = EffectiveSkillPolicy(
        enabled=("enabled",),
        required=(),
        legacy_denied=(),
        source_kinds={"enabled": "plugin"},
        base_scope=SkillPolicyScope.PROJECT,
    )

    with pytest.raises(SkillExposureError, match="escapes enabled Skill"):
        build_skill_exposure(
            policy,
            catalog,
            directory=tmp_path / "exposure",
        )

    assert not (tmp_path / "exposure" / "enabled").exists()


def test_projection_fails_closed_when_enabled_external_winner_has_no_path(
    tmp_path: Path,
) -> None:
    catalog = SkillCatalog(
        winners={
            "plugin-skill": SkillCatalogWinner("plugin-skill", "plugin"),
        }
    )
    policy = EffectiveSkillPolicy(
        enabled=("plugin-skill",),
        required=(),
        legacy_denied=(),
        source_kinds={"plugin-skill": "plugin"},
        base_scope=SkillPolicyScope.PROJECT,
    )

    with pytest.raises(SkillExposureError, match="has no exposable path"):
        build_skill_exposure(
            policy,
            catalog,
            directory=tmp_path / "exposure",
        )
