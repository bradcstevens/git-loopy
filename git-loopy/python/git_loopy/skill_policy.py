"""Resolve closed-world Skill-policy inputs into one immutable Run policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping

from .config import SkillPolicyInputs

if TYPE_CHECKING:
    from .git import GitClient


class SkillPolicyScope(StrEnum):
    """The persisted or fallback scope selected before Run overlays."""

    PROJECT = "project"
    GLOBAL = "global"
    MINIMAL = "minimal"


class SkillPolicyFallback(StrEnum):
    """Why the Minimal Skill policy was selected."""

    MINIMAL = "minimal"
    MIGRATION = "migration"


class SkillPolicyResolutionError(ValueError):
    """Base class for actionable Effective Skill policy failures."""

    problem = "Skill policy resolution failed"

    def __init__(self, names: Iterable[str] = ()) -> None:
        self.names = tuple(sorted(set(names)))
        detail = f": {', '.join(self.names)}" if self.names else ""
        super().__init__(f"{self.problem}{detail}")


class SkillInventoryUnavailable(SkillPolicyResolutionError):
    """Raised when explicit policy input requires unavailable inventory."""

    problem = "Skill inventory is unavailable for explicit policy names"


class MissingEnabledSkills(SkillPolicyResolutionError):
    """Raised when enabled names have no catalog winner."""

    problem = "Enabled Skills are missing from the catalog"


class MissingRequiredSkills(SkillPolicyResolutionError):
    """Raised when Required Skills are absent from the effective set."""

    problem = "Required Skills are disabled"


class UntrackedProjectSkills(SkillPolicyResolutionError):
    """Raised when enabled project winners are not versioned."""

    problem = "Enabled project Skills are not git-tracked"


@dataclass(frozen=True)
class SkillCatalogWinner:
    """The normalized winning catalog entry for one canonical Skill name."""

    name: str
    source_kind: str
    project_path: Path | None = None


@dataclass(frozen=True)
class SkillCatalog:
    """Normalized catalog winners plus external-inventory availability."""

    winners: Mapping[str, SkillCatalogWinner] = field(default_factory=dict)
    inventory_available: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "winners",
            MappingProxyType(dict(sorted(self.winners.items()))),
        )


@dataclass(frozen=True)
class EffectiveSkillPolicy:
    """The deeply immutable capability boundary shared by an entire Run."""

    enabled: tuple[str, ...]
    required: tuple[str, ...]
    legacy_denied: tuple[str, ...]
    source_kinds: Mapping[str, str]
    base_scope: SkillPolicyScope
    fallback: SkillPolicyFallback | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", tuple(sorted(set(self.enabled))))
        object.__setattr__(self, "required", tuple(sorted(set(self.required))))
        object.__setattr__(
            self,
            "legacy_denied",
            tuple(sorted(set(self.legacy_denied))),
        )
        object.__setattr__(
            self,
            "source_kinds",
            MappingProxyType(dict(sorted(self.source_kinds.items()))),
        )


def collect_project_skill_tracking(
    catalog: SkillCatalog,
    git: GitClient,
) -> frozenset[str]:
    """Return project winners whose complete Skill path is git-tracked."""
    return frozenset(
        winner.name
        for winner in catalog.winners.values()
        if winner.source_kind == "project"
        and winner.project_path is not None
        and git.is_tracked(winner.project_path)
    )


def resolve_skill_policy(
    inputs: SkillPolicyInputs,
    *,
    catalog: SkillCatalog,
    required_skills: Iterable[str],
    fallback: SkillPolicyFallback = SkillPolicyFallback.MINIMAL,
    legacy_denied: Iterable[str] = (),
    tracked_project_skills: Iterable[str] = (),
) -> EffectiveSkillPolicy:
    """Resolve the selected configured scope into an Effective Skill policy."""
    required = frozenset(required_skills)
    if inputs.project.present:
        scope = SkillPolicyScope.PROJECT
        enabled = set(inputs.project.names)
        fallback_state = None
    elif inputs.global_.present:
        scope = SkillPolicyScope.GLOBAL
        enabled = set(inputs.global_.names)
        fallback_state = None
    else:
        scope = SkillPolicyScope.MINIMAL
        enabled = set(required)
        fallback_state = fallback

    if inputs.environment.present:
        enabled = set(inputs.environment.names)
    enabled.update(inputs.enable_skills)
    enabled.difference_update(inputs.disable_skills)
    legacy_denied_names = frozenset(legacy_denied)
    enabled.difference_update(legacy_denied_names)

    explicit_policy = (
        inputs.project.present
        or inputs.global_.present
        or inputs.environment.present
        or bool(inputs.enable_skills)
    )
    if explicit_policy and not catalog.inventory_available:
        raise SkillInventoryUnavailable(enabled)

    missing_enabled = enabled.difference(catalog.winners)
    if missing_enabled and not catalog.inventory_available:
        raise SkillInventoryUnavailable(missing_enabled)
    if missing_enabled:
        raise MissingEnabledSkills(missing_enabled)

    missing_required = required.difference(enabled)
    if missing_required:
        raise MissingRequiredSkills(missing_required)

    tracked = frozenset(tracked_project_skills)
    untracked_project = {
        name
        for name in enabled
        if catalog.winners[name].source_kind == "project" and name not in tracked
    }
    if untracked_project:
        raise UntrackedProjectSkills(untracked_project)

    source_kinds = {
        name: catalog.winners[name].source_kind
        for name in enabled
    }
    return EffectiveSkillPolicy(
        enabled=tuple(enabled),
        required=tuple(required),
        legacy_denied=tuple(legacy_denied_names),
        source_kinds=source_kinds,
        base_scope=scope,
        fallback=fallback_state,
    )
