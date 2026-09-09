"""Report Skill-policy blockers without creating a Run (#516)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from .config import RunConfig
from .copilot_client import make_copilot_client
from .git import GitClient, SubprocessGitClient
from .prompt import PromptMetadataError, load_prompt
from .settings import global_config_path, project_config_path
from .skill_catalog import discover_skill_catalog
from .skill_install import installed_catalog_dir
from .skill_policy import (
    MissingEnabledSkills,
    MissingRequiredSkills,
    SkillCatalog,
    SkillInventoryUnavailable,
    SkillPolicyResolutionError,
    SkillPolicyScope,
    UntrackedProjectSkills,
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
    policy_scope = _policy_scope(config)
    config_path = _policy_config_path(policy_scope, repo_root, environment)

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
    except (OSError, PromptMetadataError, RuntimeError, TimeoutError) as exc:
        output_fn(f"git-loopy: doctor could not resolve the Skill policy: {exc}")
        return 1

    if not resolution.blockers:
        output_fn("Skill policy is healthy; a Run would not be blocked.")
        return 0

    for blocker in resolution.blockers:
        for name in blocker.names or ("Skill policy",):
            output_fn(
                f"{name} | {_blocker_description(blocker)} | "
                f"{_scope_label(policy_scope)} | Config: {config_path}"
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
        try:
            async with client_factory() as client:
                return await resolve_run_skill_policy_preflight(
                    client,
                    config=config,
                    git=git,
                    prompt_text=prompt_text,
                    repo_root=repo_root,
                    installed_skills_dir=installed_skills_dir,
                    workspace=workspace,
                    discoverer=discoverer,
                )
        except (OSError, RuntimeError, TimeoutError):
            return await resolve_run_skill_policy_preflight(
                None,
                config=config,
                git=git,
                prompt_text=prompt_text,
                repo_root=repo_root,
                installed_skills_dir=installed_skills_dir,
                workspace=workspace,
                discoverer=_unavailable_catalog,
            )


async def _unavailable_catalog(_client: object, **_kwargs: object) -> SkillCatalog:
    """Route client startup failures through the shared inventory fallback."""
    raise RuntimeError("Copilot Skill inventory is unavailable")


def _policy_scope(config: RunConfig) -> SkillPolicyScope:
    inputs = config.skill_policy
    if inputs.project.present:
        return SkillPolicyScope.PROJECT
    if inputs.global_.present:
        return SkillPolicyScope.GLOBAL
    return SkillPolicyScope.MINIMAL


def _policy_config_path(
    scope: SkillPolicyScope,
    repo_root: Path,
    env: Mapping[str, str],
) -> Path:
    if scope is SkillPolicyScope.GLOBAL:
        return global_config_path(env)
    return project_config_path(repo_root)


def _scope_label(scope: SkillPolicyScope) -> str:
    if scope is SkillPolicyScope.MINIMAL:
        return "Minimal fallback"
    return f"{scope.value} policy"


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
