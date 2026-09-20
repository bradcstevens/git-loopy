"""Report every Run precondition without creating a Run (#516, #519), inspect
the installed Skill catalog (#518), and repair the saved Skill policy on request (#517)."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterable, Mapping

from .config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from .copilot_client import make_copilot_client
from .git import GitClient, SubprocessGitClient
from .prompt import PromptMetadataError, load_prompt
from .settings import global_config_path, project_config_path
from .skill_catalog import (
    SdkSkillSurfaceError,
    SkillCatalogError,
    discover_skill_catalog,
)
from .skill_install import (
    CatalogInstallStatus,
    SkillInstallError,
    inspect_installed_catalog,
    installed_catalog_dir,
    refresh_installed_catalog,
)
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
from .skill_source import SkillSourceError, read_skill_source_pin
from .skill_run_preflight import (
    CatalogDiscoverer,
    RunSkillPolicyPreflight,
    resolve_run_skill_policy_preflight,
)
from .run_environment_preflight import (
    RunEnvironmentPreflight,
    resolve_run_environment_preflight,
)
from .run_routing_preflight import resolve_run_routing_preflight
from .static_route import RoutePolicy
from . import settings

ClientFactory = Callable[[], Any]
ConfigWriter = Callable[[Path, Mapping[str, object]], None]
EnvironmentPreflightResolver = Callable[..., RunEnvironmentPreflight]


@dataclass(frozen=True)
class SkillPolicyRepairPlan:
    """The minimal saved-policy repair derived from shared preflight facts."""

    surface: SkillPolicySurface
    current: tuple[str, ...]
    proposed: tuple[str, ...]
    unresolved: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", tuple(sorted(set(self.current))))
        object.__setattr__(self, "proposed", tuple(sorted(set(self.proposed))))

    @property
    def additions(self) -> tuple[str, ...]:
        return tuple(name for name in self.proposed if name not in self.current)

    @property
    def removals(self) -> tuple[str, ...]:
        return tuple(name for name in self.current if name not in self.proposed)

    @property
    def has_saved_policy(self) -> bool:
        return self.surface in {
            SkillPolicySurface.PROJECT,
            SkillPolicySurface.GLOBAL,
        }


def _render_catalog_install_status(
    status: CatalogInstallStatus, *, output_fn: Callable[[str], None]
) -> None:
    """Report the local install before using it to judge any policy name."""
    if status.state == "matching":
        output_fn(
            "Skill catalog | matching | "
            f"installed revision {status.pin.revision} matches the pinned revision."
        )
        return
    if status.state == "absent":
        output_fn(
            "Skill catalog | absent | "
            f"no catalog is installed for pinned revision {status.pin.revision}; "
            "refresh with `git-loopy doctor --apply`."
        )
        return
    assert status.installed is not None
    if status.installed.revision == status.pin.revision:
        output_fn(
            "Skill catalog | drifted | "
            "installed contents no longer match the record for pinned revision "
            f"{status.pin.revision}; refresh with `git-loopy doctor --apply`."
        )
        return
    output_fn(
        "Skill catalog | drifted | "
        f"installed revision {status.installed.revision} does not match pinned "
        f"{status.pin.revision}; refresh with `git-loopy doctor --apply`."
    )


def _render_catalog_unverified(
    detail: str, *, output_fn: Callable[[str], None]
) -> None:
    """Keep failed acquisition separate from a missing-Skill verdict."""
    output_fn(f"Skill catalog | could not verify | Warning: {detail}")


def run_doctor(
    *,
    config: RunConfig,
    repo_root: Path,
    env: Mapping[str, str] | None = None,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    git: GitClient | None = None,
    prompt_text: str | None = None,
    output_fn: Callable[[str], None] = print,
    apply: bool = False,
    writer: ConfigWriter = settings.write_config_atomic,
    environment_preflight_resolver: EnvironmentPreflightResolver | None = None,
) -> int:
    """Report the Run's preflight; ``--apply`` refreshes before repairing policy."""
    environment = os.environ if env is None else env
    resolve_environment = (
        resolve_run_environment_preflight
        if environment_preflight_resolver is None
        else environment_preflight_resolver
    )
    environment_preflight = resolve_environment(
        repo_root=repo_root,
        issue_source=config.issue_source,
    )
    for check in environment_preflight.checks:
        output_fn(
            f"{check.name} | {'passed' if check.passed else 'failed'} | "
            f"{check.message}"
        )

    routing_preflight = asyncio.run(
        resolve_run_routing_preflight(
            config, environment, warn=lambda message: output_fn(f"Routing | {message}")
        )
    )
    if not routing_preflight.passed:
        output_fn(f"Routing | failed | {routing_preflight.refusal}")
        output_fn(
            "Routing scope | doctor evaluates Config and environment, not a "
            "future Run's --model/--reasoning-effort flags. To check a run-wide "
            "override here, use its GIT_LOOPY_MODEL/GIT_LOOPY_REASONING_EFFORT "
            "environment equivalent."
        )
    elif config.route_policy is not RoutePolicy.UNSELECTED:
        output_fn(
            "Routing prerequisites | passed | configuration prerequisites resolved; "
            "issue-specific eligibility and evidence are checked at Pickup."
        )

    try:
        prompt = (
            prompt_text if prompt_text is not None else load_prompt(repo_root, environment)
        )
        # The Skill root and the verdict passed on it are derived from one
        # environment, never supplied separately: a caller able to point the
        # catalog read at one directory while the pin was compared against
        # another could silently switch off the whole stale-install guard and
        # prune a policy name a refresh would have restored (#518).
        catalog_dir = installed_catalog_dir(environment)
        install_status = inspect_installed_catalog(read_skill_source_pin(), environment)
        _render_catalog_install_status(install_status, output_fn=output_fn)
        if apply:
            try:
                refreshed = refresh_installed_catalog(
                    install_status.pin, env=environment
                )
            except SkillInstallError as exc:
                _render_catalog_unverified(str(exc), output_fn=output_fn)
                return 1
            if refreshed.warning is not None:
                _render_catalog_unverified(refreshed.warning, output_fn=output_fn)
                return 1
            refreshed_status = inspect_installed_catalog(
                install_status.pin, environment
            )
            if refreshed_status.state != install_status.state:
                _render_catalog_install_status(refreshed_status, output_fn=output_fn)
            install_status = refreshed_status
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
        SkillSourceError,
        TimeoutError,
    ) as exc:
        output_fn(f"git-loopy: doctor could not resolve the Skill policy: {exc}")
        return 1

    if not resolution.blockers:
        if not routing_preflight.passed:
            output_fn("Skill policy is healthy; routing still blocks this Run.")
        elif apply:
            output_fn("Skill policy is healthy; no changes to apply.")
        elif config.route_policy is RoutePolicy.UNSELECTED:
            output_fn("Skill policy is healthy; a Run would not be blocked.")
        else:
            output_fn("Skill policy is healthy.")
        return (
            0
            if (
                environment_preflight.passed
                and routing_preflight.passed
                and install_status.state == "matching"
            )
            else 1
        )

    for blocker in resolution.blockers:
        for name in blocker.names or ("Skill policy",):
            surface = _carrier(blocker, name, resolution=resolution)
            description = _blocker_description(blocker)
            remedy = _surface_remedy(blocker, surface, repo_root, environment)
            if install_status.state != "matching" and isinstance(
                blocker, MissingEnabledSkills
            ):
                description += f"; the installed catalog is {install_status.state}"
                remedy = (
                    "Fix: refresh the pinned Skill catalog with "
                    "`git-loopy doctor --apply`; do not prune this policy name."
                )
            output_fn(
                f"{name} | {description} | {_surface_label(surface)} | {remedy}"
            )
    if not apply:
        return 1

    # The refresh above is what `--apply` offers a stale install, so reaching
    # here on a verdict that is still not `matching` means it did not take. The
    # names were judged against that install, and the rows above told the
    # operator not to prune them, so the repair is refused rather than allowed
    # to contradict the report that preceded it.
    if install_status.state != "matching":
        output_fn(
            "No repair was applied; the installed Skill catalog is "
            f"{install_status.state} and the reported names were judged against "
            "it. Refresh it before repairing the policy."
        )
        return 1

    plan = plan_skill_policy_repair(resolution)
    if not plan.has_saved_policy:
        output_fn("No saved Skill policy to repair.")
        return 1
    if plan.unresolved:
        output_fn("No repair was applied; resolve the reported blockers first.")
        return 1

    path = _policy_path(plan.surface, repo_root, environment)
    _render_repair_plan(plan, output_fn=output_fn)
    table = dict(settings.load_config_table(path))
    table["enabled_skills"] = list(plan.proposed)
    writer(path, table)
    output_fn(f"Saved repaired {plan.surface.value} Skill policy to {path}")
    return 0 if environment_preflight.passed and routing_preflight.passed else 1


def plan_skill_policy_repair(
    resolution: RunSkillPolicyPreflight,
) -> SkillPolicyRepairPlan:
    """Plan only repairs the shared resolver agrees would clear every blocker.

    The candidate is proposed from the two blocker classes a saved policy can
    own — an enabled name the catalog has no winner for, and a Required Skill
    the policy simply never listed — and is then handed straight back to
    :meth:`RunSkillPolicyPreflight.blockers_for`. Nothing is written unless the
    resolver that produced the report says the candidate is clean, which is what
    makes "a repair that leaves a blocker standing is a failure" structural
    rather than a case analysis that has to anticipate every surface.
    """
    surface = resolution.surface
    current = tuple(_saved_policy_names(surface, resolution.inputs))
    plan = SkillPolicyRepairPlan(
        surface=surface,
        current=current,
        proposed=current,
        unresolved=True,
    )
    if not plan.has_saved_policy:
        return plan

    enabled = set(current)
    for blocker in resolution.blockers:
        if isinstance(blocker, MissingEnabledSkills):
            enabled.difference_update(blocker.names)
        elif isinstance(blocker, MissingRequiredSkills):
            enabled.update(
                name for name in blocker.names if name in resolution.catalog.winners
            )

    proposed = tuple(enabled)
    candidate = _with_saved_names(resolution.inputs, surface, proposed)
    return SkillPolicyRepairPlan(
        surface=surface,
        current=current,
        proposed=proposed,
        unresolved=bool(resolution.blockers_for(candidate)),
    )


def _with_saved_names(
    inputs: SkillPolicyInputs,
    surface: SkillPolicySurface,
    names: Iterable[str],
) -> SkillPolicyInputs:
    """Return the same Run inputs with one saved scope's names replaced."""
    replacement = SkillPolicyInput(present=True, names=tuple(names))
    if surface is SkillPolicySurface.GLOBAL:
        return replace(inputs, global_=replacement)
    assert surface is SkillPolicySurface.PROJECT
    return replace(inputs, project=replacement)


def _saved_policy_names(
    surface: SkillPolicySurface,
    inputs: SkillPolicyInputs,
) -> Iterable[str]:
    if surface is SkillPolicySurface.PROJECT:
        return inputs.project.names
    if surface is SkillPolicySurface.GLOBAL:
        return inputs.global_.names
    return ()


def _policy_path(
    surface: SkillPolicySurface,
    repo_root: Path,
    env: Mapping[str, str],
) -> Path:
    if surface is SkillPolicySurface.GLOBAL:
        return global_config_path(env)
    assert surface is SkillPolicySurface.PROJECT
    return project_config_path(repo_root)


def _render_repair_plan(
    plan: SkillPolicyRepairPlan,
    *,
    output_fn: Callable[[str], None],
) -> None:
    output_fn(f"Skill policy repair for the {plan.surface.value} policy:")
    for name in plan.additions:
        output_fn(f"Add: {name}")
    for name in plan.removals:
        output_fn(f"Remove: {name}")


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
    resolution: RunSkillPolicyPreflight,
) -> SkillPolicySurface:
    """Name the surface an operator corrects to clear this one Skill's row.

    Only a Required Skill missing from the effective set can have been taken
    away by a surface other than the base one, so it is the only row whose
    carrier is worth a second question.
    """
    if not isinstance(blocker, MissingRequiredSkills):
        return resolution.surface
    subtracted = attribute_subtracted_skill(
        name,
        resolution.inputs,
        legacy_denied=resolution.legacy_denied,
    )
    return resolution.surface if subtracted is None else subtracted


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
    blocker: SkillPolicyResolutionError,
    surface: SkillPolicySurface,
    repo_root: Path,
    env: Mapping[str, str],
) -> str:
    """Name what an operator edits to clear a blocker this surface carries."""
    if isinstance(blocker, UntrackedProjectSkills):
        return (
            "Fix: git add and commit the Skill, or run `git-loopy skills edit` "
            "to disable it."
        )
    if isinstance(blocker, SkillInventoryUnavailable):
        return "Fix: restore Copilot CLI access, then re-run `git-loopy doctor`."
    if surface is SkillPolicySurface.ENVIRONMENT:
        return f"Environment: {ENABLED_SKILLS_ENV}"
    if surface is SkillPolicySurface.DENY_GUARD:
        return f"Deny guard: deny_skills or {DENY_SKILLS_ENV}"
    if surface is SkillPolicySurface.DISABLE_OVERLAY:
        return "Overlay: --disable-skill"
    if surface is SkillPolicySurface.GLOBAL:
        return f"Config: {_policy_path(surface, repo_root, env)}"
    if surface is SkillPolicySurface.PROJECT:
        return f"Config: {_policy_path(surface, repo_root, env)}"
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
