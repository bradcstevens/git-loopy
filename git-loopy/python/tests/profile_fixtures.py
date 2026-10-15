"""Offline installed profiles for tests that exercise setup and Run preflight."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from git_loopy import skill_install
from git_loopy.skill_source import read_skill_source_pin

RUN_REQUIRED_SKILLS = ("code-review", "diagnosing-bugs", "tdd")


def seed_profile_install(
    env: Mapping[str, str],
    required: Sequence[str] = RUN_REQUIRED_SKILLS,
) -> skill_install.InstalledCatalog:
    """Write a complete synthetic install at the production-resolved locations."""
    pin = read_skill_source_pin()
    skills = skill_install.installed_catalog_dir(env)
    profiles = skill_install.installed_profiles_dir(env)
    profiles.mkdir(parents=True, exist_ok=True)
    names = set(required) | {"code-review", "setup-agent-skills"}
    for name in names:
        skill = skills / name
        skill.mkdir(parents=True, exist_ok=True)
        path = skill / "SKILL.md"
        if not path.exists():
            path.write_text(
                f"---\nname: {name}\ndescription: A stand-in {name} Skill.\n---\n",
                encoding="utf-8",
            )
    for key in ("planning", "review", "implementation", "bugfix", "test", "docs", "chore"):
        required_names = (
            ("code-review",) if key == "review"
            else tuple(name for name in required if name != "code-review")
            if key in ("implementation", "bugfix", "test")
            else ()
        )
        sequence = "\n" + "".join(f"  - {name}\n" for name in required_names)
        if not required_names:
            sequence = " []\n"
        access = "read-only" if key == "review" else "full"
        (profiles / f"{key}.md").write_text(
            f"---\nrequired-skills:{sequence}permitted-skills: []\n"
            f"access: {access}\n---\n{key} role delta.\n",
            encoding="utf-8",
        )
    skill_install.install_record_path(env).write_text(
        json.dumps({
            "schema_version": 2,
            "repository": pin.repository,
            "revision": pin.revision,
            "sha256": skill_install.catalog_digest(skills),
            "profiles_directory": pin.profiles_directory,
            "profiles_sha256": skill_install.catalog_digest(profiles),
        }),
        encoding="utf-8",
    )
    catalog = skill_install.read_install_record(env)
    assert catalog is not None
    return catalog
