"""PROTOTYPE: inspect the issue #223 Skill-policy state model.

Run from the repository root:

    uv run --project git-loopy/python python \
      git-loopy/python/git_loopy/prototypes/skill_policy_223.py --all
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    project: tuple[str, ...] | None
    global_: tuple[str, ...] | None
    environment: tuple[str, ...] | None
    enable: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()
    legacy_deny: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    catalog: tuple[str, ...] = ()


CASES = (
    Case(
        "project replaces global",
        ("project",),
        ("global",),
        None,
        catalog=("project", "global"),
    ),
    Case(
        "explicit-empty project remains authoritative",
        (),
        ("global",),
        None,
        catalog=("global",),
    ),
    Case(
        "environment replaces base before overlays",
        ("project",),
        ("global",),
        ("environment",),
        enable=("added", "conflict"),
        disable=("conflict",),
        catalog=("environment", "added", "conflict"),
    ),
    Case(
        "legacy deny is the final subtraction",
        None,
        None,
        None,
        enable=("required",),
        legacy_deny=("required",),
        required=("required",),
        catalog=("required",),
    ),
    Case(
        "missing catalog names fail before Required validation",
        ("missing",),
        None,
        None,
        required=("required",),
        catalog=("required",),
    ),
)


def project(case: Case) -> str:
    if case.project is not None:
        scope = "project"
        enabled = set(case.project)
    elif case.global_ is not None:
        scope = "global"
        enabled = set(case.global_)
    else:
        scope = "minimal"
        enabled = set(case.required)

    if case.environment is not None:
        enabled = set(case.environment)

    enabled.update(case.enable)
    enabled.difference_update(case.disable)
    enabled.difference_update(case.legacy_deny)

    missing_catalog = enabled.difference(case.catalog)
    missing_required = set(case.required).difference(enabled)
    if missing_catalog:
        result = f"FAIL missing_enabled={sorted(missing_catalog)}"
    elif missing_required:
        result = f"FAIL missing_required={sorted(missing_required)}"
    else:
        result = f"OK enabled={sorted(enabled)}"
    return f"{case.name}\n  base_scope={scope}\n  {result}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--case", type=int, choices=range(1, len(CASES) + 1))
    args = parser.parse_args()

    if args.all:
        selected = CASES
    elif args.case:
        selected = (CASES[args.case - 1],)
    else:
        print("Choose a case:")
        for index, case in enumerate(CASES, 1):
            print(f"  {index}. {case.name}")
        selected = (CASES[int(input("> ")) - 1],)

    print("\n\n".join(project(case) for case in selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
