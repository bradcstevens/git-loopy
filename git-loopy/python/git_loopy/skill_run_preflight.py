"""Resolve and materialize one closed-world Skill boundary for a Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import RunConfig
from .prompt import resolve_required_skills
from .skill_catalog import (
    SkillCatalogError,
    SdkSkillSurfaceError,
    build_skill_catalog,
    discover_skill_catalog,
)
from .skill_exposure import SkillExposure, build_skill_exposure
from .skill_policy import (
    SkillCatalog,
    collect_project_skill_tracking,
    resolve_skill_policy,
)

CatalogDiscoverer = Callable[..., Awaitable[SkillCatalog]]


@dataclass(frozen=True)
class RunSkillPreflight:
    """The immutable exposure and redacted audit projection for one Run."""

    exposure: SkillExposure
    migration_warning: bool

    @property
    def event_payload(self) -> dict[str, object]:
        policy = self.exposure.policy
        return {
            "base_scope": policy.base_scope.value,
            "enabled": list(policy.enabled),
            "fallback": policy.fallback.value if policy.fallback is not None else None,
            "legacy_denied": list(policy.legacy_denied),
            "migration_warning": self.migration_warning,
            "required": list(policy.required),
            "source_kinds": dict(policy.source_kinds),
        }


def _minimal_catalog(packaged_skills_dir: Path, workspace: Path) -> SkillCatalog:
    root = workspace / "minimal-catalog-root"
    root.mkdir(parents=True, exist_ok=True)
    return build_skill_catalog(
        (),
        repo_root=root,
        packaged_skills_dir=packaged_skills_dir,
    )


async def resolve_run_skill_preflight(
    client: Any,
    *,
    config: RunConfig,
    git: Any,
    prompt_text: str,
    repo_root: Path,
    packaged_skills_dir: Path,
    workspace: Path,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
) -> RunSkillPreflight:
    """Resolve Required Skills, policy, tracking, and one Run-scoped exposure."""
    required = resolve_required_skills(prompt_text)
    workspace.mkdir(parents=True, exist_ok=True)
    discovery_directory = workspace / "discovery"
    discovery_directory.mkdir()
    try:
        discovered = await discoverer(
            client,
            repo_root=repo_root,
            packaged_skills_dir=packaged_skills_dir,
            discovery_directory=discovery_directory,
        )
    except (
        OSError,
        RuntimeError,
        TimeoutError,
        SkillCatalogError,
        SdkSkillSurfaceError,
    ):
        minimal = _minimal_catalog(packaged_skills_dir, workspace)
        discovered = SkillCatalog(
            winners=minimal.winners,
            inventory_available=False,
        )

    inputs = config.skill_policy
    configured = (
        inputs.project.present
        or inputs.global_.present
        or inputs.environment.present
        or bool(inputs.enable_skills)
    )
    catalog = discovered if configured else _minimal_catalog(packaged_skills_dir, workspace)
    tracked = collect_project_skill_tracking(catalog, git)
    policy = resolve_skill_policy(
        inputs,
        catalog=catalog,
        required_skills=required.required_skills,
        legacy_denied=config.deny_skills,
        tracked_project_skills=tracked,
    )
    exposure = build_skill_exposure(
        policy,
        catalog,
        directory=workspace / "exposure",
    )
    return RunSkillPreflight(
        exposure=exposure,
        migration_warning=required.migration_warning,
    )
