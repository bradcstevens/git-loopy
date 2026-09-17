"""Resolve and materialize one closed-world Skill boundary for a Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import RunConfig, SkillPolicyInputs
from .prompt import resolve_required_skills
from .skill_catalog import (
    SkillCatalogError,
    SdkSkillSurfaceError,
    build_skill_catalog,
    discover_skill_catalog,
)
from .skill_exposure import SkillExposure, build_skill_exposure
from .skill_policy import (
    EffectiveSkillPolicy,
    SkillCatalog,
    SkillPolicyResolutionError,
    SkillPolicySurface,
    collect_project_skill_tracking,
    find_skill_policy_blockers,
    resolve_skill_policy,
    select_skill_policy_surface,
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


@dataclass(frozen=True)
class RunSkillPolicyPreflight:
    """The report-safe policy result shared by Run and ``git-loopy doctor``."""

    policy: EffectiveSkillPolicy | None
    catalog: SkillCatalog
    blockers: tuple[SkillPolicyResolutionError, ...]
    migration_warning: bool
    surface: SkillPolicySurface
    inputs: SkillPolicyInputs
    required_skills: tuple[str, ...]
    legacy_denied: tuple[str, ...]
    tracked_project_skills: frozenset[str]

    def blockers_for(
        self,
        inputs: SkillPolicyInputs,
    ) -> tuple[SkillPolicyResolutionError, ...]:
        """Judge a candidate policy against the facts this preflight judged.

        The one way to ask "would this policy start a Run?" without resolving a
        second, differently-informed preflight: a repair proposed by
        ``git-loopy doctor`` is only a repair if the resolver that reported the
        blockers agrees they are gone, and it must agree about the same catalog,
        the same Required Skills, and the same tracking facts to mean anything.
        """
        return find_skill_policy_blockers(
            inputs,
            catalog=self.catalog,
            required_skills=self.required_skills,
            legacy_denied=self.legacy_denied,
            tracked_project_skills=self.tracked_project_skills,
        )


def _minimal_catalog(installed_skills_dir: Path, workspace: Path) -> SkillCatalog:
    root = workspace / "minimal-catalog-root"
    root.mkdir(parents=True, exist_ok=True)
    return build_skill_catalog(
        (),
        repo_root=root,
        installed_skills_dir=installed_skills_dir,
    )


async def resolve_run_skill_preflight(
    client: Any,
    *,
    config: RunConfig,
    git: Any,
    prompt_text: str,
    repo_root: Path,
    installed_skills_dir: Path,
    workspace: Path,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
) -> RunSkillPreflight:
    """Resolve the shared policy preflight, then materialize one Run exposure."""
    resolution = await resolve_run_skill_policy_preflight(
        client,
        config=config,
        git=git,
        prompt_text=prompt_text,
        repo_root=repo_root,
        installed_skills_dir=installed_skills_dir,
        workspace=workspace,
        discoverer=discoverer,
    )
    if resolution.blockers:
        raise resolution.blockers[0]
    assert resolution.policy is not None
    exposure = build_skill_exposure(
        resolution.policy,
        resolution.catalog,
        directory=workspace / "exposure",
    )
    return RunSkillPreflight(
        exposure=exposure,
        migration_warning=resolution.migration_warning,
    )


async def resolve_run_skill_policy_preflight(
    client: Any,
    *,
    config: RunConfig,
    git: Any,
    prompt_text: str,
    repo_root: Path,
    installed_skills_dir: Path,
    workspace: Path,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
) -> RunSkillPolicyPreflight:
    """Resolve all Skill-policy blockers without materializing a Run exposure."""
    required = resolve_required_skills(prompt_text)
    workspace.mkdir(parents=True, exist_ok=True)
    inputs = config.skill_policy
    configured = (
        inputs.project.present
        or inputs.global_.present
        or inputs.environment.present
        or bool(inputs.enable_skills)
    )
    catalog = await _catalog_for_policy(
        client,
        configured=configured,
        repo_root=repo_root,
        installed_skills_dir=installed_skills_dir,
        workspace=workspace,
        discoverer=discoverer,
    )
    tracked = collect_project_skill_tracking(catalog, git)
    legacy_denied = tuple(config.deny_skills)
    blockers = find_skill_policy_blockers(
        inputs,
        catalog=catalog,
        required_skills=required.required_skills,
        legacy_denied=legacy_denied,
        tracked_project_skills=tracked,
    )
    return RunSkillPolicyPreflight(
        policy=(
            None
            if blockers
            else resolve_skill_policy(
                inputs,
                catalog=catalog,
                required_skills=required.required_skills,
                legacy_denied=legacy_denied,
                tracked_project_skills=tracked,
            )
        ),
        catalog=catalog,
        blockers=blockers,
        migration_warning=required.migration_warning,
        surface=select_skill_policy_surface(inputs),
        inputs=inputs,
        required_skills=tuple(required.required_skills),
        legacy_denied=legacy_denied,
        tracked_project_skills=tracked,
    )


async def _catalog_for_policy(
    client: Any,
    *,
    configured: bool,
    repo_root: Path,
    installed_skills_dir: Path,
    workspace: Path,
    discoverer: CatalogDiscoverer,
) -> SkillCatalog:
    """Discover the catalog a policy resolves against, with the Run fallback."""
    discovery_directory = workspace / "discovery"
    discovery_directory.mkdir(exist_ok=True)
    try:
        discovered = await discoverer(
            client,
            repo_root=repo_root,
            installed_skills_dir=installed_skills_dir,
            discovery_directory=discovery_directory,
        )
    except (
        OSError,
        RuntimeError,
        TimeoutError,
        SkillCatalogError,
        SdkSkillSurfaceError,
    ):
        minimal = _minimal_catalog(installed_skills_dir, workspace)
        discovered = SkillCatalog(
            winners=minimal.winners,
            inventory_available=False,
        )

    return discovered if configured else _minimal_catalog(installed_skills_dir, workspace)
