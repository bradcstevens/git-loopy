"""Discover and normalize the metadata-only Skill catalog."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Iterable

from .skill_policy import SkillCatalog, SkillCatalogWinner, is_canonical_skill_name

_SOURCE_KINDS = {
    "project": "project",
    "inherited": "inherited",
    "personal-copilot": "personal",
    "personal-agents": "personal",
    "plugin": "plugin",
    "custom": "custom",
    "builtin": "builtin",
}
_SDK_SKILL_SOURCES = frozenset(_SOURCE_KINDS)
_SDK_SKILL_FIELDS = frozenset(
    {
        "name",
        "description",
        "enabled",
        "source",
        "user_invocable",
        "path",
        "plugin_name",
    }
)


class SkillCatalogError(ValueError):
    """Raised when catalog metadata cannot be normalized safely."""


class SdkSkillSurfaceError(RuntimeError):
    """Raised when the pinned SDK's Skill-discovery contract has drifted."""


@dataclass(frozen=True)
class _SkillMetadata:
    name: str
    description: str
    user_invocable: bool
    path: Path


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise SkillCatalogError(f"invalid quoted Skill metadata: {value!r}") from exc
        if not isinstance(parsed, str):
            raise SkillCatalogError(f"Skill metadata must be text: {value!r}")
        return parsed
    return value


def _read_metadata(skill_md: Path) -> _SkillMetadata:
    """Read only a Skill's leading metadata block, never its instructions."""
    try:
        with skill_md.open(encoding="utf-8") as stream:
            if stream.readline().rstrip("\r\n") != "---":
                raise SkillCatalogError(f"{skill_md} has no Skill metadata")
            frontmatter: list[str] = []
            for line in stream:
                stripped = line.rstrip("\r\n")
                if stripped == "---":
                    break
                frontmatter.append(stripped)
            else:
                raise SkillCatalogError(f"{skill_md} has unclosed Skill metadata")
    except OSError as exc:
        raise SkillCatalogError(f"cannot read Skill metadata at {skill_md}: {exc}") from exc

    values: dict[str, str] = {}
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        index += 1
        if not line or line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if key not in {"name", "description", "user-invocable"}:
            continue
        if raw in {">", "|", ">-", "|-"}:
            parts: list[str] = []
            while index < len(frontmatter):
                continuation = frontmatter[index]
                if continuation and not continuation.startswith((" ", "\t")):
                    break
                index += 1
                parts.append(continuation.strip())
            raw = " ".join(part for part in parts if part)
        values[key] = _scalar(raw)

    name = values.get("name", "")
    if not is_canonical_skill_name(name):
        raise SkillCatalogError(f"{skill_md} has invalid canonical Skill name {name!r}")
    description = values.get("description", "").strip()
    invocable = values.get("user-invocable", "true").strip().lower()
    if invocable not in {"true", "false"}:
        raise SkillCatalogError(
            f"{skill_md} has invalid user-invocable value {invocable!r}"
        )
    return _SkillMetadata(
        name=name,
        description=description,
        user_invocable=invocable == "true",
        path=skill_md,
    )


def _filesystem_skills(root: Path) -> dict[str, _SkillMetadata]:
    if not root.is_dir():
        return {}
    result: dict[str, _SkillMetadata] = {}
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        skill_md = child / "SKILL.md"
        if not child.is_dir() or not skill_md.is_file():
            continue
        metadata = _read_metadata(skill_md)
        if metadata.name in result:
            raise SkillCatalogError(
                f"duplicate canonical Skill name {metadata.name!r} under {root}"
            )
        result[metadata.name] = metadata
    return result


def _source_value(source: Any) -> str:
    value = getattr(source, "value", source)
    if not isinstance(value, str) or value not in _SOURCE_KINDS:
        raise SkillCatalogError(f"unsupported Copilot Skill source: {value!r}")
    return value


def _copilot_winners(skills: Iterable[Any]) -> dict[str, SkillCatalogWinner]:
    winners: dict[str, SkillCatalogWinner] = {}
    for skill in skills:
        name = skill.name
        if not is_canonical_skill_name(name):
            raise SkillCatalogError(f"invalid Copilot Skill name: {name!r}")
        if name in winners:
            raise SkillCatalogError(f"duplicate Copilot Skill winner: {name!r}")
        source = _source_value(skill.source)
        raw_path = getattr(skill, "path", None)
        winners[name] = SkillCatalogWinner(
            name=name,
            source_kind=_SOURCE_KINDS[source],
            description=skill.description,
            copilot_enabled=skill.enabled,
            user_invocable=skill.user_invocable,
            plugin_name=getattr(skill, "plugin_name", None),
            path=Path(raw_path) if raw_path else None,
        )
    return winners


def validate_sdk_skill_surface(
    *,
    client_type: type[Any] | None = None,
    skill_type: type[Any] | None = None,
    source_type: type[Any] | None = None,
    skills_api_type: type[Any] | None = None,
    skill_list_type: type[Any] | None = None,
) -> None:
    """Fail deliberately if the exact pinned discovery seam changes."""
    if any(
        value is None
        for value in (
            client_type,
            skill_type,
            source_type,
            skills_api_type,
            skill_list_type,
        )
    ):
        from copilot import CopilotClient
        from copilot.generated.rpc import Skill, SkillList, SkillsApi, SkillSource

        client_type = client_type or CopilotClient
        skill_type = skill_type or Skill
        source_type = source_type or SkillSource
        skills_api_type = skills_api_type or SkillsApi
        skill_list_type = skill_list_type or SkillList

    assert client_type is not None
    assert skill_type is not None
    assert source_type is not None
    assert skills_api_type is not None
    assert skill_list_type is not None

    create_parameters = inspect.signature(client_type.create_session).parameters
    for option in ("disabled_skills", "skill_directories"):
        if option not in create_parameters:
            raise SdkSkillSurfaceError(
                f"CopilotClient.create_session no longer exposes {option}"
            )
    if not hasattr(skills_api_type, "list"):
        raise SdkSkillSurfaceError("typed SkillsApi.list discovery RPC is unavailable")
    source_values = frozenset(member.value for member in source_type)
    if source_values != _SDK_SKILL_SOURCES:
        raise SdkSkillSurfaceError(
            "Copilot SkillSource values drifted: "
            f"expected {sorted(_SDK_SKILL_SOURCES)}, got {sorted(source_values)}"
        )
    skill_fields = frozenset(field.name for field in fields(skill_type))
    missing_fields = _SDK_SKILL_FIELDS.difference(skill_fields)
    if missing_fields:
        raise SdkSkillSurfaceError(
            f"Copilot Skill response fields drifted: missing {sorted(missing_fields)}"
        )
    list_fields = frozenset(field.name for field in fields(skill_list_type))
    if "skills" not in list_fields:
        raise SdkSkillSurfaceError("Copilot SkillList response no longer contains skills")


def build_skill_catalog(
    copilot_skills: Iterable[Any],
    *,
    repo_root: Path,
    packaged_skills_dir: Path,
) -> SkillCatalog:
    """Resolve project, Copilot, and packaged metadata into stable winners."""
    copilot = _copilot_winners(copilot_skills)
    project = _filesystem_skills(repo_root / ".copilot" / "skills")
    packaged = _filesystem_skills(packaged_skills_dir)
    winners = dict(copilot)

    for name, metadata in packaged.items():
        winners.setdefault(
            name,
            SkillCatalogWinner(
                name=name,
                source_kind="packaged",
                description=metadata.description,
                user_invocable=metadata.user_invocable,
                path=metadata.path,
            ),
        )
    for name, metadata in project.items():
        copilot_winner = copilot.get(name)
        winners[name] = SkillCatalogWinner(
            name=name,
            source_kind="project",
            description=metadata.description,
            copilot_enabled=(
                copilot_winner.copilot_enabled if copilot_winner is not None else None
            ),
            user_invocable=(
                copilot_winner.user_invocable
                if copilot_winner is not None
                else metadata.user_invocable
            ),
            path=metadata.path,
            project_path=metadata.path.parent,
        )
    return SkillCatalog(winners=winners)


async def discover_skill_catalog(
    client: Any,
    *,
    repo_root: Path,
    packaged_skills_dir: Path,
    discovery_directory: Path,
    validate_surface: bool = True,
) -> SkillCatalog:
    """Discover Copilot metadata through its typed RPC and resolve all winners."""
    if validate_surface:
        validate_sdk_skill_surface()
    isolated_directory = discovery_directory.resolve()
    if isolated_directory == repo_root.resolve():
        raise SkillCatalogError(
            "Copilot catalog discovery requires an isolated working directory"
        )
    session = await client.create_session(
        working_directory=str(isolated_directory),
        enable_skills=True,
        enable_config_discovery=True,
        skip_custom_instructions=True,
    )
    try:
        result = await session.rpc.skills.list()
        return build_skill_catalog(
            result.skills,
            repo_root=repo_root,
            packaged_skills_dir=packaged_skills_dir,
        )
    finally:
        await session.disconnect()
