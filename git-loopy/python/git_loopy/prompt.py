"""Machine-readable Required-Skill metadata for Run instructions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files

__all__ = [
    "PromptMetadataError",
    "PromptMetadataFailure",
    "RequiredSkills",
    "minimal_skill_policy",
    "packaged_required_skills",
    "parse_required_skills",
    "resolve_required_skills",
]

_SKILL_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_YAML_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_FRONTMATTER_KEY = re.compile(r"([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)")


class PromptMetadataFailure(str, Enum):
    """Stable failure categories suitable for Run preflight diagnostics."""

    MALFORMED_FRONTMATTER = "malformed_frontmatter"
    NON_STRING_REQUIRED_SKILL = "non_string_required_skill"
    DUPLICATE_REQUIRED_SKILL = "duplicate_required_skill"
    INVALID_REQUIRED_SKILL_NAME = "invalid_required_skill_name"
    MISSING_REQUIRED_SKILLS = "missing_required_skills"


class PromptMetadataError(ValueError):
    """A typed prompt-metadata validation failure."""

    def __init__(self, failure: PromptMetadataFailure, message: str) -> None:
        super().__init__(message)
        self.failure = failure


@dataclass(frozen=True)
class RequiredSkills:
    """Required Skills resolved for active Run instructions."""

    required_skills: tuple[str, ...]
    migration_warning: bool


def _malformed(message: str) -> PromptMetadataError:
    return PromptMetadataError(
        PromptMetadataFailure.MALFORMED_FRONTMATTER,
        message,
    )


def _parse_required_skill(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    elif value.startswith(("'", '"')):
        raise _malformed("Required Skill string has an unmatched quote")

    if (
        value.lower() in {"null", "true", "false", "~"}
        or _YAML_NUMBER.fullmatch(value)
        or value.startswith(("[", "{"))
    ):
        raise PromptMetadataError(
            PromptMetadataFailure.NON_STRING_REQUIRED_SKILL,
            f"Required Skill entries must be strings, got {value!r}",
        )
    if not _SKILL_NAME.fullmatch(value):
        raise PromptMetadataError(
            PromptMetadataFailure.INVALID_REQUIRED_SKILL_NAME,
            f"Required Skill names must be canonical, got {value!r}",
        )
    return value


def parse_required_skills(prompt_text: str) -> tuple[str, ...] | None:
    """Parse a leading ``required-skills`` frontmatter sequence when present."""
    lines = prompt_text.splitlines()
    if not lines or lines[0] != "---":
        return None

    try:
        closing_index = lines.index("---", 1)
    except ValueError as exc:
        raise PromptMetadataError(
            PromptMetadataFailure.MALFORMED_FRONTMATTER,
            "prompt frontmatter is missing its closing '---'",
        ) from exc

    frontmatter = lines[1:closing_index]
    required_skills: tuple[str, ...] | None = None
    required_key_seen = False
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        if not line or line.startswith("#"):
            index += 1
            continue
        if line[0].isspace():
            raise _malformed(f"unexpected indented frontmatter line: {line!r}")

        key_match = _FRONTMATTER_KEY.fullmatch(line)
        if key_match is None:
            raise _malformed(f"invalid frontmatter line: {line!r}")
        key, inline_value = key_match.groups()
        index += 1

        if key != "required-skills":
            while index < len(frontmatter) and (
                not frontmatter[index] or frontmatter[index][0].isspace()
            ):
                index += 1
            continue
        if required_key_seen:
            raise _malformed("required-skills may be declared only once")
        required_key_seen = True
        if inline_value == "[]":
            required_skills = ()
            continue
        if inline_value:
            raise _malformed("required-skills must be a block sequence or []")

        names: list[str] = []
        while index < len(frontmatter) and frontmatter[index].startswith("  - "):
            name = _parse_required_skill(
                frontmatter[index].removeprefix("  - ").strip()
            )
            if name in names:
                raise PromptMetadataError(
                    PromptMetadataFailure.DUPLICATE_REQUIRED_SKILL,
                    f"Required Skill {name!r} is listed more than once",
                )
            names.append(name)
            index += 1
        if not names:
            raise _malformed("required-skills must contain a sequence or explicit []")
        while index < len(frontmatter) and (
            not frontmatter[index] or frontmatter[index].startswith("#")
        ):
            index += 1
        if index < len(frontmatter) and frontmatter[index][0].isspace():
            raise _malformed(
                f"invalid required-skills sequence line: {frontmatter[index]!r}"
            )
        required_skills = tuple(sorted(names))

    return required_skills


def resolve_required_skills(
    prompt_text: str,
    *,
    packaged_required_skills: tuple[str, ...] | None = None,
) -> RequiredSkills:
    """Resolve active instructions, inheriting packaged requirements when absent."""
    parsed = parse_required_skills(prompt_text)
    if parsed is not None:
        return RequiredSkills(required_skills=parsed, migration_warning=False)
    fallback = (
        _read_packaged_required_skills()
        if packaged_required_skills is None
        else packaged_required_skills
    )
    return RequiredSkills(
        required_skills=tuple(sorted(set(fallback))),
        migration_warning=True,
    )


def _read_packaged_required_skills() -> tuple[str, ...]:
    prompt_text = (files("git_loopy") / "PROMPT.md").read_text(encoding="utf-8")
    required_skills = parse_required_skills(prompt_text)
    if required_skills is None:
        raise PromptMetadataError(
            PromptMetadataFailure.MISSING_REQUIRED_SKILLS,
            "packaged PROMPT.md must declare required-skills",
        )
    return required_skills


def packaged_required_skills() -> tuple[str, ...]:
    """Read the Required Skills declared by the packaged Run instructions."""
    return _read_packaged_required_skills()


def minimal_skill_policy() -> tuple[str, ...]:
    """Return the deterministic Minimal Skill policy from the packaged contract."""
    return packaged_required_skills()
