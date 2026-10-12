"""Assessment policy is a startup snapshot, never repository content."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .settings import global_dir


REPOSITORY_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/"
    r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,99})"
)
MILESTONE_PATTERN = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
KEYS = frozenset({
    "workflows", "app_id", "installation_id", "app_identity", "key_file",
})


class AssessmentConfigError(ValueError):
    """The service cannot start with this policy."""


@dataclass(frozen=True)
class Policy:
    workflows: tuple[str, ...]
    app_id: str
    installation_id: str
    app_identity: str
    key_file: Path
    revision: str

    @property
    def identity(self) -> str:
        return (
            f"GitHub App {self.app_identity} (app {self.app_id}, "
            f"installation {self.installation_id})"
        )


def load_policy(env: Mapping[str, str]) -> dict[str, Policy]:
    path = global_dir(env) / "assessment" / "config.toml"
    try:
        tables = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise AssessmentConfigError(f"cannot read assessment configuration: {exc}") from exc
    if not tables:
        raise AssessmentConfigError("assessment configuration has no repository tables")
    policies = {}
    for repository, table in tables.items():
        if (
            len(repository) > 140
            or re.fullmatch(REPOSITORY_PATTERN, repository) is None
            or not isinstance(table, dict)
        ):
            raise AssessmentConfigError("configuration must contain owner/name tables")
        if set(table) != KEYS:
            unknown = sorted(set(table) - KEYS)
            missing = sorted(KEYS - set(table))
            raise AssessmentConfigError(
                f"unknown keys: {unknown}; missing keys: {missing}"
            )
        workflows = table["workflows"]
        if (
            not isinstance(workflows, list) or not workflows
            or not all(isinstance(w, str) and 0 < len(w) <= 200 for w in workflows)
            or len(workflows) > 100 or len(set(workflows)) != len(workflows)
        ):
            raise AssessmentConfigError("workflows must be unique, bounded names")
        for key in ("app_id", "installation_id"):
            if not isinstance(table[key], str) or not re.fullmatch(
                r"[1-9][0-9]{0,19}", table[key],
            ):
                raise AssessmentConfigError(f"{key} must be a positive numeric string")
        for key in ("app_identity", "key_file"):
            if not isinstance(table[key], str) or not 0 < len(table[key]) <= 4096:
                raise AssessmentConfigError(f"{key} must be a bounded nonempty string")
        canonical = json.dumps(table, sort_keys=True, separators=(",", ":"))
        policies[repository] = Policy(
            workflows=tuple(workflows), app_id=table["app_id"],
            installation_id=table["installation_id"],
            app_identity=table["app_identity"], key_file=Path(table["key_file"]),
            revision=hashlib.sha256(canonical.encode()).hexdigest(),
        )
    return policies
