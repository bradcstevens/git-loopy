"""Production-seam tests for the Effective Skill policy resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.config import SkillPolicyInput, SkillPolicyInputs
from git_loopy.skill_policy import (
    collect_project_skill_tracking,
    MissingEnabledSkills,
    MissingRequiredSkills,
    SkillCatalog,
    SkillCatalogWinner,
    SkillInventoryUnavailable,
    SkillPolicyFallback,
    SkillPolicyScope,
    UntrackedProjectSkills,
    resolve_skill_policy,
)
from tests.fakes import FakeGitClient


def test_project_policy_replaces_global_policy() -> None:
    result = resolve_skill_policy(
        SkillPolicyInputs(
            project=SkillPolicyInput(present=True, names=("project",)),
            global_=SkillPolicyInput(present=True, names=("global",)),
        ),
        catalog=SkillCatalog(
            winners={
                "global": SkillCatalogWinner("global", "personal"),
                "project": SkillCatalogWinner("project", "project"),
            }
        ),
        required_skills=(),
        tracked_project_skills={"project"},
    )

    assert result.enabled == ("project",)
    assert result.base_scope is SkillPolicyScope.PROJECT
    assert dict(result.source_kinds) == {"project": "project"}


@pytest.mark.parametrize(
    (
        "inputs",
        "required",
        "fallback",
        "legacy_denied",
        "expected_enabled",
        "expected_scope",
        "expected_fallback",
    ),
    [
        pytest.param(
            SkillPolicyInputs(
                project=SkillPolicyInput(present=True),
                global_=SkillPolicyInput(present=True, names=("global",)),
            ),
            (),
            SkillPolicyFallback.MINIMAL,
            (),
            (),
            SkillPolicyScope.PROJECT,
            None,
            id="explicit-empty-project",
        ),
        pytest.param(
            SkillPolicyInputs(
                global_=SkillPolicyInput(present=True, names=("global",))
            ),
            (),
            SkillPolicyFallback.MINIMAL,
            (),
            ("global",),
            SkillPolicyScope.GLOBAL,
            None,
            id="global-inheritance",
        ),
        pytest.param(
            SkillPolicyInputs(),
            ("required",),
            SkillPolicyFallback.MINIMAL,
            (),
            ("required",),
            SkillPolicyScope.MINIMAL,
            SkillPolicyFallback.MINIMAL,
            id="minimal-fallback",
        ),
        pytest.param(
            SkillPolicyInputs(),
            ("required",),
            SkillPolicyFallback.MIGRATION,
            (),
            ("required",),
            SkillPolicyScope.MINIMAL,
            SkillPolicyFallback.MIGRATION,
            id="migration-fallback",
        ),
        pytest.param(
            SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("base",)),
                environment=SkillPolicyInput(
                    present=True, names=("environment", "disabled")
                ),
                enable_skills=frozenset({"added", "conflict"}),
                disable_skills=frozenset({"disabled", "conflict"}),
            ),
            (),
            SkillPolicyFallback.MINIMAL,
            ("added",),
            ("environment",),
            SkillPolicyScope.PROJECT,
            None,
            id="replacement-overlays-disable-wins-and-legacy-last",
        ),
    ],
)
def test_policy_precedence_matrix(
    inputs: SkillPolicyInputs,
    required: tuple[str, ...],
    fallback: SkillPolicyFallback,
    legacy_denied: tuple[str, ...],
    expected_enabled: tuple[str, ...],
    expected_scope: SkillPolicyScope,
    expected_fallback: SkillPolicyFallback | None,
) -> None:
    names = {"base", "global", "required", "environment", "disabled", "added", "conflict"}
    catalog = SkillCatalog(
        winners={
            name: SkillCatalogWinner(name, "builtin")
            for name in names
        }
    )

    result = resolve_skill_policy(
        inputs,
        catalog=catalog,
        required_skills=required,
        fallback=fallback,
        legacy_denied=legacy_denied,
    )

    assert result.enabled == expected_enabled
    assert result.base_scope is expected_scope
    assert result.fallback is expected_fallback
    assert result.legacy_denied == tuple(sorted(legacy_denied))


@pytest.mark.parametrize(
    ("inputs", "catalog", "required", "tracked", "error_type", "names"),
    [
        pytest.param(
            SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("alpha",))
            ),
            SkillCatalog(
                winners={"alpha": SkillCatalogWinner("alpha", "builtin")},
                inventory_available=False,
            ),
            (),
            (),
            SkillInventoryUnavailable,
            ("alpha",),
            id="required-inventory-unavailable",
        ),
        pytest.param(
            SkillPolicyInputs(),
            SkillCatalog(inventory_available=False),
            ("required",),
            (),
            SkillInventoryUnavailable,
            ("required",),
            id="minimal-required-winner-needs-unavailable-inventory",
        ),
        pytest.param(
            SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("missing",))
            ),
            SkillCatalog(),
            (),
            (),
            MissingEnabledSkills,
            ("missing",),
            id="enabled-name-missing-from-catalog",
        ),
        pytest.param(
            SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("alpha",))
            ),
            SkillCatalog(
                winners={
                    "alpha": SkillCatalogWinner("alpha", "builtin"),
                    "required": SkillCatalogWinner("required", "packaged"),
                }
            ),
            ("required",),
            (),
            MissingRequiredSkills,
            ("required",),
            id="required-skill-disabled",
        ),
        pytest.param(
            SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("project",))
            ),
            SkillCatalog(
                winners={
                    "project": SkillCatalogWinner("project", "project"),
                }
            ),
            (),
            (),
            UntrackedProjectSkills,
            ("project",),
            id="project-winner-untracked",
        ),
    ],
)
def test_validation_failures_are_distinct_and_typed(
    inputs: SkillPolicyInputs,
    catalog: SkillCatalog,
    required: tuple[str, ...],
    tracked: tuple[str, ...],
    error_type: type[Exception],
    names: tuple[str, ...],
) -> None:
    with pytest.raises(error_type) as exc_info:
        resolve_skill_policy(
            inputs,
            catalog=catalog,
            required_skills=required,
            tracked_project_skills=tracked,
        )

    assert exc_info.value.names == names


def test_minimal_policy_uses_packaged_winners_when_inventory_is_unavailable() -> None:
    result = resolve_skill_policy(
        SkillPolicyInputs(),
        catalog=SkillCatalog(
            winners={
                "required": SkillCatalogWinner("required", "packaged"),
            },
            inventory_available=False,
        ),
        required_skills=("required",),
    )

    assert result.enabled == ("required",)
    assert result.fallback is SkillPolicyFallback.MINIMAL


def test_project_tracking_evidence_uses_injected_git_client(tmp_path: Path) -> None:
    skill_path = tmp_path / ".copilot" / "skills" / "project"
    git = FakeGitClient(tmp_path, tracked_paths={skill_path})
    catalog = SkillCatalog(
        winners={
            "builtin": SkillCatalogWinner("builtin", "builtin"),
            "project": SkillCatalogWinner(
                "project",
                "project",
                project_path=skill_path,
            ),
            "untracked": SkillCatalogWinner(
                "untracked",
                "project",
                project_path=tmp_path / ".copilot" / "skills" / "untracked",
            ),
        }
    )

    assert collect_project_skill_tracking(catalog, git) == frozenset({"project"})


def test_effective_policy_copies_and_freezes_every_collection() -> None:
    winners = {
        "beta": SkillCatalogWinner("beta", "builtin"),
        "alpha": SkillCatalogWinner("alpha", "personal"),
    }
    catalog = SkillCatalog(winners=winners)
    result = resolve_skill_policy(
        SkillPolicyInputs(
            project=SkillPolicyInput(
                present=True,
                names=("beta", "alpha"),
            )
        ),
        catalog=catalog,
        required_skills=("beta", "alpha"),
        legacy_denied=("unused-z", "unused-a"),
    )

    winners.clear()

    assert result.enabled == ("alpha", "beta")
    assert result.required == ("alpha", "beta")
    assert result.legacy_denied == ("unused-a", "unused-z")
    assert list(result.source_kinds.items()) == [
        ("alpha", "personal"),
        ("beta", "builtin"),
    ]
    assert tuple(catalog.winners) == ("alpha", "beta")
    with pytest.raises(TypeError):
        result.source_kinds["later"] = "custom"  # type: ignore[index]
