#!/usr/bin/env python3
"""Regenerate the wheel-vendored workflow skill catalog from the canonical source.

git-loopy's authoring workflow ships as agent skills. The repo-root
``.copilot/skills/`` is the single human-edited canonical source of truth; the
copies under ``git-loopy/python/git_loopy/skills/`` are *generated* vendored
copies that travel inside the built wheel so ``git-loopy init`` can scaffold the
whole catalog from a checkout-free install (PRD #121; extends ADR-0006).

The vendored catalog is exactly ``subdirs(.copilot/skills/) - SKILL_DENYLIST``:
every canonical skill except the three optional tool/vendor integrations, which
are cleanly severable and obtainable via ``/find-skills`` or a manual copy.

This is the committed, explicit sync command (run via the repo's ``uv``
toolchain) -- deliberately **not** a pre-commit hook, so regeneration stays
reviewable::

    uv run --project git-loopy/python python git-loopy/python/scripts/sync_skills.py

Run it after editing (or adding/removing) a canonical skill, then commit the
regenerated tree. Adding a skill needs no allowlist edit -- drop it under
``.copilot/skills/`` and re-run; it auto-ships and auto-scaffolds. Excluding a
new integration is a one-line :data:`SKILL_DENYLIST` edit.

The byte-identical guard in ``tests/test_packaged_skills.py`` imports this
module's :data:`SKILL_DENYLIST` and :func:`catalog_skill_names` (so the sync and
the guard can never disagree) and fails CI if the vendored copies drift, if a
denied skill leaks in, or if the built wheel is missing a catalog skill.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

# The one place the exclusion set is defined. Imported by the guard test so the
# sync and the guard can never disagree about which skills are vendored. These
# are optional tool/vendor integrations, cleanly severable from the core
# loop-engineering catalog (PRD #121).
SKILL_DENYLIST: frozenset[str] = frozenset(
    {
        "azure-mcaps-resource-deployment",
        "microsoft-docs",
        "microsoft-foundry",
        "playwright-cli",
    }
)

# scripts/ -> python/ -> git-loopy/ -> <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_SKILLS_DIR = _REPO_ROOT / ".copilot" / "skills"
VENDORED_SKILLS_DIR = Path(__file__).resolve().parents[1] / "git_loopy" / "skills"

# Named in the guard's drift message so a forgotten sync is self-correcting.
SYNC_COMMAND = (
    "uv run --project git-loopy/python python git-loopy/python/scripts/sync_skills.py"
)


class SyncResult(NamedTuple):
    """What a sync did (or, from :func:`classify`, would do)."""

    added: list[str]
    updated: list[str]
    removed: list[str]
    unchanged: list[str]

    @property
    def changed(self) -> bool:
        """True when the vendored tree was (or would be) mutated."""
        return bool(self.added or self.updated or self.removed)


def catalog_skill_names(canonical_dir: Path = CANONICAL_SKILLS_DIR) -> list[str]:
    """Sorted catalog: every canonical skill subdirectory minus the denylist."""
    return sorted(
        child.name
        for child in canonical_dir.iterdir()
        if child.is_dir() and child.name not in SKILL_DENYLIST
    )


def dirs_equal(left: Path, right: Path) -> bool:
    """True when two directory trees are byte-identical (recursive, deep)."""
    cmp = filecmp.dircmp(str(left), str(right))
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    # dircmp's default shallow compare only stats files; force a deep,
    # content-level comparison so a byte drift with a matching size is caught.
    _, mismatch, errors = filecmp.cmpfiles(
        str(left), str(right), cmp.common_files, shallow=False
    )
    if mismatch or errors:
        return False
    return all(dirs_equal(left / sub, right / sub) for sub in cmp.common_dirs)


def classify(
    canonical_dir: Path = CANONICAL_SKILLS_DIR,
    vendored_dir: Path = VENDORED_SKILLS_DIR,
) -> SyncResult:
    """Diff the vendored tree against the catalog **without** writing anything."""
    desired = catalog_skill_names(canonical_dir)
    existing = (
        sorted(child.name for child in vendored_dir.iterdir() if child.is_dir())
        if vendored_dir.is_dir()
        else []
    )
    removed = [name for name in existing if name not in desired]
    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for name in desired:
        dst = vendored_dir / name
        if not dst.exists():
            added.append(name)
        elif not dirs_equal(canonical_dir / name, dst):
            updated.append(name)
        else:
            unchanged.append(name)
    return SyncResult(added, updated, removed, unchanged)


def sync(
    canonical_dir: Path = CANONICAL_SKILLS_DIR,
    vendored_dir: Path = VENDORED_SKILLS_DIR,
) -> SyncResult:
    """Regenerate ``vendored_dir`` to exactly the catalog. Idempotent.

    Re-running when already in sync makes no filesystem changes and reports an
    empty (unchanged-only) :class:`SyncResult`.
    """
    result = classify(canonical_dir, vendored_dir)
    vendored_dir.mkdir(parents=True, exist_ok=True)
    for name in result.removed:
        shutil.rmtree(vendored_dir / name)
    for name in (*result.added, *result.updated):
        dst = vendored_dir / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(canonical_dir / name, dst)
    return result


def _summarise(result: SyncResult, *, applied: bool) -> str:
    if not result.changed:
        return (
            f"Already in sync: {len(result.unchanged)} vendored catalog "
            "skills, no changes."
        )
    verb_add, verb_upd, verb_rem = (
        ("added", "updated", "removed")
        if applied
        else ("to add", "to update", "to remove")
    )
    parts = []
    if result.added:
        parts.append(f"{verb_add} {len(result.added)} ({', '.join(result.added)})")
    if result.updated:
        parts.append(f"{verb_upd} {len(result.updated)} ({', '.join(result.updated)})")
    if result.removed:
        parts.append(f"{verb_rem} {len(result.removed)} ({', '.join(result.removed)})")
    lead = "Synced vendored catalog: " if applied else "Vendored catalog out of sync: "
    return lead + "; ".join(parts) + "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate the wheel-vendored workflow skill catalog from the "
            "canonical .copilot/skills/ (minus the denylist)."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Report drift and exit non-zero if the vendored catalog is out of "
            "sync, without writing anything (for CI / pre-flight)."
        ),
    )
    args = parser.parse_args(argv)

    if not CANONICAL_SKILLS_DIR.is_dir():
        print(
            f"error: canonical skills dir not found at {CANONICAL_SKILLS_DIR}",
            file=sys.stderr,
        )
        return 2

    if args.check:
        result = classify()
        print(_summarise(result, applied=False))
        if result.changed:
            print(
                "error: vendored catalog is out of sync; run:\n  " + SYNC_COMMAND,
                file=sys.stderr,
            )
            return 1
        return 0

    result = sync()
    print(_summarise(result, applied=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
