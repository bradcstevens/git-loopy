"""Materialize one closed-world Skill catalog for SDK sessions."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .skill_policy import EffectiveSkillPolicy, SkillCatalog


class SkillExposureError(ValueError):
    """Raised when an exact isolated Skill projection cannot be built."""


def _validated_source(source: Path, name: str) -> Path:
    try:
        root = source.resolve(strict=True)
    except OSError as exc:
        raise SkillExposureError(
            f"Enabled Skill {name!r} source is unavailable: {source}"
        ) from exc
    for candidate in root.rglob("*"):
        if not candidate.is_symlink():
            continue
        try:
            target = candidate.resolve(strict=True)
            target.relative_to(root)
        except (OSError, ValueError) as exc:
            raise SkillExposureError(
                f"Link in enabled Skill {name!r} escapes enabled Skill source"
            ) from exc
    return root


@dataclass(frozen=True)
class SkillExposure:
    """Run-scoped SDK inputs for one Effective Skill policy."""

    policy: EffectiveSkillPolicy
    catalog: SkillCatalog
    directory: Path

    @property
    def skill_directories(self) -> tuple[str, ...]:
        return (str(self.directory),)

    @property
    def disabled_skills(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.catalog.winners).difference(self.policy.enabled)))


def build_skill_exposure(
    policy: EffectiveSkillPolicy,
    catalog: SkillCatalog,
    *,
    directory: Path,
) -> SkillExposure:
    """Copy enabled filesystem winners into an isolated SDK Skill root."""
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise SkillExposureError(f"Skill exposure directory must be empty: {directory}")
    for name in policy.enabled:
        winner = catalog.winners[name]
        if winner.path is None:
            if winner.source_kind != "builtin":
                raise SkillExposureError(
                    f"Enabled Skill {name!r} has no exposable path"
                )
            continue
        source = winner.path.parent if winner.path.name == "SKILL.md" else winner.path
        shutil.copytree(
            _validated_source(source, name),
            directory / name,
            symlinks=False,
        )
    return SkillExposure(policy=policy, catalog=catalog, directory=directory)
