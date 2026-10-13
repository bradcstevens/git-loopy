"""Prove a checkout's Agent profiles without installing or selecting them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import TASK_TYPE_KEYS
from .prompt import PromptMetadataError, PromptMetadataFailure, parse_required_skills
from .skill_catalog import SkillCatalogError, read_skill_metadata

_KEY = re.compile(r"([a-z][a-z-]*):[ \t]*(.*)")
_FIELDS = {"required-skills", "permitted-skills", "access"}


@dataclass(frozen=True)
class ProfileBreach:
    """An actionable refusal naming both the profile and the broken rule."""

    profile: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.profile} [{self.rule}]: {self.detail}"


def _validate_profile(path: Path, skills: Path) -> list[ProfileBreach]:
    name = path.name
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [ProfileBreach(name, "frontmatter", f"cannot read profile: {exc}")]
    if not lines or lines[0] != "---":
        return [ProfileBreach(name, "frontmatter", "missing opening '---'")]
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return [ProfileBreach(name, "frontmatter", "missing closing '---'")]

    breaches: list[ProfileBreach] = []
    fields: dict[str, list[str]] = {}
    index = 1
    while index < closing:
        line = lines[index]
        index += 1
        if not line.strip() or line.startswith("#"):
            continue
        match = _KEY.fullmatch(line)
        if match is None:
            breaches.append(ProfileBreach(name, "frontmatter", f"invalid line: {line!r}"))
            continue
        key, value = match.groups()
        block = [value]
        while index < closing and (
            not lines[index].strip() or lines[index][0].isspace()
        ):
            block.append(lines[index])
            index += 1
        if key not in _FIELDS:
            breaches.append(ProfileBreach(name, "frontmatter-key", f"unknown key {key!r}"))
        elif key in fields:
            breaches.append(ProfileBreach(name, "duplicate-key", f"repeated key {key!r}"))
        else:
            fields[key] = block

    skill_lists: dict[str, tuple[str, ...]] = {}
    for key in ("required-skills", "permitted-skills"):
        if key not in fields:
            skill_lists[key] = ()
            continue
        # Both lists use the Run instructions' existing canonical Skill-list syntax.
        text = "---\nrequired-skills:" + "\n".join(fields[key]) + "\n---\n"
        try:
            parsed = parse_required_skills(text)
        except PromptMetadataError as exc:
            rule = (
                "duplicate-skill"
                if exc.failure == PromptMetadataFailure.DUPLICATE_REQUIRED_SKILL
                else "skill-list"
            )
            breaches.append(ProfileBreach(name, rule, f"{key}: {exc}"))
        else:
            assert parsed is not None
            skill_lists[key] = parsed
    for key, names in skill_lists.items():
        for skill_name in names:
            skill = skills / skill_name
            metadata = skill / "SKILL.md"
            detail = ""
            if skills.is_symlink() or skill.is_symlink() or metadata.is_symlink():
                detail = "a symlink is not a Skill in this checkout"
            elif not metadata.is_file():
                detail = "Skill must have a regular SKILL.md file"
            else:
                try:
                    declared = read_skill_metadata(metadata).name
                except (SkillCatalogError, UnicodeError) as exc:
                    detail = str(exc)
                else:
                    if declared != skill_name:
                        detail = f"Skill declares a different name: {declared!r}"
            if detail:
                breaches.append(
                    ProfileBreach(name, "skill-reference", f"{key} {skill_name!r}: {detail}")
                )
    for overlap in sorted(
        set(skill_lists.get("required-skills", ()))
        & set(skill_lists.get("permitted-skills", ()))
    ):
        breaches.append(
            ProfileBreach(
                name, "duplicate-skill", f"{overlap!r} is both Required and Permitted"
            )
        )
    access = "\n".join(fields.get("access", [])).strip()
    if len(access) >= 2 and access[0] == access[-1] and access[0] in {"'", '"'}:
        access = access[1:-1]
    if access not in {"read-only", "full"}:
        breaches.append(ProfileBreach(name, "access", "declare read-only or full"))
    if path.stem == "review" and (
        access != "read-only"
        or "code-review" not in skill_lists.get("required-skills", ())
    ):
        breaches.append(
            ProfileBreach(name, "review", "must be read-only and require code-review")
        )
    if not "\n".join(lines[closing + 1 :]).strip():
        breaches.append(ProfileBreach(name, "body", "role body must not be empty"))
    return breaches


def validate_profiles(
    checkout: Path,
    *,
    profiles_directory: str = "profiles",
    skills_directory: str = "skills",
) -> tuple[ProfileBreach, ...]:
    """Judge a complete profile set in a checkout; never write or reach the network."""
    for directory in (profiles_directory, skills_directory):
        relative = Path(directory)
        if not directory.strip() or relative.is_absolute() or ".." in relative.parts:
            return (
                ProfileBreach(
                    directory, "profile-set", "directory must be relative inside the checkout"
                ),
            )
        path = checkout
        for part in relative.parts:
            path /= part
            if path.is_symlink():
                return (
                    ProfileBreach(directory, "symlink", f"directory traverses symlink {path}"),
                )
    root = checkout / profiles_directory
    if not root.is_dir():
        return (
            ProfileBreach(profiles_directory, "profile-set", "expected a profile directory"),
        )
    expected = {f"{key}.md" for key in TASK_TYPE_KEYS}
    try:
        entries = {entry.name: entry for entry in root.iterdir()}
    except OSError as exc:
        return (ProfileBreach(profiles_directory, "profile-set", f"cannot read: {exc}"),)
    breaches = [
        ProfileBreach(name, "profile-set", "missing Task type Markdown file")
        for name in sorted(expected - entries.keys())
    ]
    for name, path in sorted(entries.items()):
        if path.is_symlink():
            breaches.append(ProfileBreach(name, "symlink", "profile entry is a symlink"))
        elif name not in expected or not path.is_file():
            breaches.append(
                ProfileBreach(name, "profile-set", "expected one Markdown file per Task type")
            )
        else:
            breaches.extend(_validate_profile(path, checkout / skills_directory))
    return tuple(breaches)
