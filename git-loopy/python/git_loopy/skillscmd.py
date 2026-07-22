"""Operator-facing Skill catalog management commands."""

from __future__ import annotations

import asyncio
import os
import sys
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Awaitable, Callable, Iterable, Mapping

from . import settings
from .copilot_client import make_copilot_client
from .prompt import PromptMetadataError, load_prompt, resolve_required_skills
from .skill_catalog import (
    SdkSkillSurfaceError,
    SkillCatalogError,
    discover_skill_catalog,
)
from .skill_policy import SkillCatalog

ClientFactory = Callable[[], Any]
CatalogDiscoverer = Callable[..., Awaitable[SkillCatalog]]


def _packaged_skills_dir() -> Path:
    return Path(str(files("git_loopy") / "skills"))


def _configured_names(
    repo_root: Path,
    env: Mapping[str, str],
    required_skills: Iterable[str],
) -> tuple[str, ...]:
    if "GIT_LOOPY_ENABLED_SKILLS" in env:
        return tuple(
            item
            for raw in env.get("GIT_LOOPY_ENABLED_SKILLS", "").split(",")
            if (item := raw.strip())
        )
    tables = settings.load_configs(repo_root, env)
    project = settings.table_optional_str_list(
        tables.project, "enabled_skills", scope="project"
    )
    if project is not None:
        return tuple(project)
    global_ = settings.table_optional_str_list(
        tables.global_, "enabled_skills", scope="global"
    )
    return tuple(global_ if global_ is not None else required_skills)


async def _load_catalog(
    *,
    client_factory: ClientFactory,
    discoverer: CatalogDiscoverer,
    repo_root: Path,
    packaged_skills_dir: Path,
    discovery_directory: Path,
) -> SkillCatalog:
    client = client_factory()
    async with client:
        return await discoverer(
            client,
            repo_root=repo_root,
            packaged_skills_dir=packaged_skills_dir,
            discovery_directory=discovery_directory,
        )


def _copilot_state(enabled: bool | None) -> str:
    if enabled is None:
        return "unavailable"
    return "enabled" if enabled else "disabled"


def _source_label(source_kind: str, plugin_name: str | None) -> str:
    if source_kind == "plugin" and plugin_name:
        return f"plugin:{plugin_name}"
    return source_kind


def run_skills_list(
    *,
    repo_root: Path,
    env: Mapping[str, str] | None = None,
    output_fn: Callable[[str], None] = print,
    error_fn: Callable[[str], None] | None = None,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    enabled_skills: Iterable[str] | None = None,
    required_skills: Iterable[str] | None = None,
    packaged_skills_dir: Path | None = None,
) -> int:
    """Print one stable, non-mutating view of normalized Skill catalog winners."""
    environment = os.environ if env is None else env
    errors = (
        (lambda message: print(message, file=sys.stderr))
        if error_fn is None
        else error_fn
    )
    if required_skills is None:
        try:
            required_skills = resolve_required_skills(
                load_prompt(repo_root, environment)
            ).required_skills
        except (OSError, PromptMetadataError) as exc:
            errors(
                "git-loopy: unable to resolve Required Skills: "
                f"{type(exc).__name__}: {exc}"
            )
            return 1
    required = frozenset(required_skills)
    if enabled_skills is None:
        try:
            enabled_skills = _configured_names(repo_root, environment, required)
        except settings.SettingsError as exc:
            errors(f"git-loopy: unable to resolve Skill policy: {exc}")
            return 1
    enabled = frozenset(enabled_skills)
    packaged = packaged_skills_dir or _packaged_skills_dir()

    try:
        with TemporaryDirectory(prefix="git-loopy-skill-catalog-") as temporary:
            discovery_directory = Path(temporary)
            factory = client_factory or (
                lambda: make_copilot_client(
                    working_directory=discovery_directory,
                    env=environment,
                )
            )
            catalog = asyncio.run(
                _load_catalog(
                    client_factory=factory,
                    discoverer=discoverer,
                    repo_root=repo_root,
                    packaged_skills_dir=packaged,
                    discovery_directory=discovery_directory,
                )
            )
    except (
        OSError,
        RuntimeError,
        TimeoutError,
        SkillCatalogError,
        SdkSkillSurfaceError,
    ) as exc:
        errors(
            "git-loopy: unable to discover Skill inventory: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    output_fn("GIT-LOOPY\tCOPILOT\tREQUIRED\tSOURCE\tNAME\tDESCRIPTION")
    for name, winner in catalog.winners.items():
        description = " ".join(winner.description.split())
        output_fn(
            "\t".join(
                (
                    "enabled" if name in enabled else "disabled",
                    _copilot_state(winner.copilot_enabled),
                    "yes" if name in required else "no",
                    _source_label(winner.source_kind, winner.plugin_name),
                    name,
                    description,
                )
            )
        )
    return 0
