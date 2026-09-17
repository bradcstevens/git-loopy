"""Report Skill-policy blockers without creating a Run (#516)."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from .config import RunConfig
from .copilot_client import make_copilot_client
from .git import GitClient, SubprocessGitClient
from .prompt import PromptMetadataError, load_prompt
from .settings import global_config_path, project_config_path
from .skill_catalog import (
    SdkSkillSurfaceError,
    SkillCatalogError,
    discover_skill_catalog,
)
from .skill_install import installed_catalog_dir
from .skill_policy import (
    DENY_SKILLS_ENV,
    ENABLED_SKILLS_ENV,
    MissingEnabledSkills,
    MissingRequiredSkills,
    SkillCatalog,
    SkillInventoryUnavailable,
    SkillPolicyResolutionError,
    SkillPolicySurface,
    UntrackedProjectSkills,
    attribute_subtracted_skill,
)
from .skill_run_preflight import (
    CatalogDiscoverer,
    RunSkillPolicyPreflight,
    resolve_run_skill_policy_preflight,
)

ClientFactory = Callable[[], Any]


def run_doctor(
    *,
    config: RunConfig,
    repo_root: Path,
    env: Mapping[str, str] | None = None,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    git: GitClient | None = None,
    prompt_text: str | None = None,
    installed_skills_dir: Path | None = None,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Report the shared Run Skill-policy preflight without mutating state."""
    environment = os.environ if env is None else env

    try:
        prompt = (
            prompt_text if prompt_text is not None else load_prompt(repo_root, environment)
        )
        catalog_dir = (
            installed_catalog_dir(environment)
            if installed_skills_dir is None
            else installed_skills_dir
        )
        bound_git = git or SubprocessGitClient(repo_root)
        factory = client_factory or (
            lambda: make_copilot_client(working_directory=repo_root, env=environment)
        )
        resolution = asyncio.run(
            _resolve(
                client_factory=factory,
                config=config,
                git=bound_git,
                prompt_text=prompt,
                repo_root=repo_root,
                installed_skills_dir=catalog_dir,
                discoverer=discoverer,
            )
        )
    except (
        OSError,
        PromptMetadataError,
        RuntimeError,
        SdkSkillSurfaceError,
        SkillCatalogError,
        TimeoutError,
    ) as exc:
        output_fn(f"git-loopy: doctor could not resolve the Skill policy: {exc}")
        return 1

    if not resolution.blockers:
        output_fn("Skill policy is healthy; a Run would not be blocked.")
        return 0

    for blocker in resolution.blockers:
        for name in blocker.names or ("Skill policy",):
            surface = _carrier(blocker, name, config=config, base=resolution.surface)
            output_fn(
                f"{name} | {_blocker_description(blocker)} | "
                f"{_surface_label(surface)} | "
                f"{_surface_remedy(surface, repo_root, environment)}"
            )
    return 1


async def _resolve(
    *,
    client_factory: ClientFactory,
    config: RunConfig,
    git: GitClient,
    prompt_text: str,
    repo_root: Path,
    installed_skills_dir: Path,
    discoverer: CatalogDiscoverer,
) -> RunSkillPolicyPreflight:
    """Open only a catalog-discovery session and close it before reporting."""
    with TemporaryDirectory(prefix="git-loopy-doctor-") as temporary:
        workspace = Path(temporary)
        async with AsyncExitStack() as stack:
            try:
                client: Any = await stack.enter_async_context(client_factory())
            except (OSError, RuntimeError, TimeoutError):
                client, discovery = None, _unavailable_catalog
            else:
                discovery = discoverer
            return await resolve_run_skill_policy_preflight(
                client,
                config=config,
                git=git,
                prompt_text=prompt_text,
                repo_root=repo_root,
                installed_skills_dir=installed_skills_dir,
                workspace=workspace,
                discoverer=discovery,
            )
    raise AssertionError("unreachable: the exit stack never suppresses")


async def _unavailable_catalog(_client: object, **_kwargs: object) -> SkillCatalog:
    """Route client startup failures through the shared inventory fallback.

    Reported as an unavailable inventory rather than as a resolution failure
    because that is precisely what a Copilot the Runner cannot start is. Every
    *other* failure stays outside this seam, so it can never be laundered into
    a diagnosis that sends an operator to reinstall a working Copilot.
    """
    raise RuntimeError("Copilot Skill inventory is unavailable")


def _carrier(
    blocker: SkillPolicyResolutionError,
    name: str,
    *,
    config: RunConfig,
    base: SkillPolicySurface,
) -> SkillPolicySurface:
    """Name the surface an operator corrects to clear this one Skill's row.

    Only a Required Skill missing from the effective set can have been taken
    away by a surface other than the base one, so it is the only row whose
    carrier is worth a second question.
    """
    if not isinstance(blocker, MissingRequiredSkills):
        return base
    subtracted = attribute_subtracted_skill(
        name,
        config.skill_policy,
        legacy_denied=config.deny_skills,
    )
    return base if subtracted is None else subtracted


def _surface_label(surface: SkillPolicySurface) -> str:
    """Name the surface in the operator's terms, not the resolver's."""
    if surface is SkillPolicySurface.ENVIRONMENT:
        return "environment replacement"
    if surface is SkillPolicySurface.MINIMAL:
        return "Minimal fallback"
    if surface is SkillPolicySurface.DENY_GUARD:
        return "legacy deny guard"
    if surface is SkillPolicySurface.DISABLE_OVERLAY:
        return "disable overlay"
    return f"{surface.value} policy"


def _surface_remedy(
    surface: SkillPolicySurface,
    repo_root: Path,
    env: Mapping[str, str],
) -> str:
    """Name what an operator edits to clear a blocker this surface carries."""
    if surface is SkillPolicySurface.ENVIRONMENT:
        return f"Environment: {ENABLED_SKILLS_ENV}"
    if surface is SkillPolicySurface.DENY_GUARD:
        return f"Deny guard: deny_skills or {DENY_SKILLS_ENV}"
    if surface is SkillPolicySurface.DISABLE_OVERLAY:
        return "Overlay: --disable-skill"
    if surface is SkillPolicySurface.GLOBAL:
        return f"Config: {global_config_path(env)}"
    return f"Config: {project_config_path(repo_root)}"


def _blocker_description(blocker: SkillPolicyResolutionError) -> str:
    if isinstance(blocker, MissingEnabledSkills):
        return "enabled Skill has no catalog winner"
    if isinstance(blocker, MissingRequiredSkills):
        return "Required Skill is disabled"
    if isinstance(blocker, UntrackedProjectSkills):
        return "enabled project Skill is not git-tracked"
    if isinstance(blocker, SkillInventoryUnavailable):
        return "Skill inventory is unavailable"
    raise AssertionError(f"unrecognized Skill policy blocker: {type(blocker).__name__}")
