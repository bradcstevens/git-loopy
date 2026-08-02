#!/usr/bin/env python3
"""Regenerate git-loopy's packaged Skill fallback from the pinned catalog.

git-loopy's authoring workflow ships as Skills. Those Skills reach a built
distribution through two arrows with one direction of flow (ADR-0023,
ADR-0025)::

    bradcstevens/git-loopy-skills @ the pinned revision   (source of record)
        -> git_loopy/skills/ + git_loopy/skill_fallback.json  (packaged fallback)
    .copilot/skills/{push, research}                      (git-loopy's own)
        -> the same packaged fallback

The first arrow is ``python -m git_loopy.skill_source``: it acquires exactly the
immutable revision recorded in ``git_loopy/skill_source.json`` and refuses it
unless it proves out. This script is the second: it cuts the packaged fallback
from that proven checkout under the committed inclusion policy
(``git_loopy.skill_fallback.INCLUSION_POLICY``), copying the selected Skills
byte-for-byte, removing anything the policy no longer selects, redistributing
the upstream licence, and writing the record every later check reads.

The policy's second clause is the named few Skills that carry git-loopy's
Continuation contract (``INCLUSION_POLICY.project_owned``). Those are this
project's interface rather than the generic catalog's, so they are cut from this
repository's ``.copilot/skills/`` and the record says so per Skill. Every other
packaged Skill is a byte-for-byte redistribution of the pinned revision, and
this repository's copies of *those* are its own consumer copies — read during
its Runs, never an input to what it ships.

Neither arrow runs during a Run. An Iteration resolves its Skills from the
packaged fallback and the consumer project's own ``.copilot/skills/``, from
disk, never the network.

Two modes, both explicit and both reviewable::

    # regenerate (needs an acquisition; writes the fallback and its record)
    uv run --project git-loopy/python python git-loopy/python/scripts/sync_skills.py

    # verify the committed fallback offline (no acquisition, no network)
    uv run --project git-loopy/python python git-loopy/python/scripts/sync_skills.py --check

``--check`` is what CI runs, and the same verification runs against the
extracted tree of a tagged Release (``git_loopy.source_release``), so a packaged
fallback, a source revision, an inclusion policy, or a provenance claim that
drifts fails in all three places with the same named diagnosis.

Deliberately **not** a pre-commit hook: regeneration stays a committed diff a
reviewer reads next to the pin bump that justifies it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# scripts/ -> python/ -> importable git_loopy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from git_loopy.skill_fallback import (  # noqa: E402
    INCLUSION_POLICY,
    PACKAGED,
    REGENERATE_COMMAND,
    FallbackGeneration,
    SkillFallbackError,
    generate_packaged_fallback,
    verify_packaged_fallback,
)
from git_loopy.skill_source import (  # noqa: E402
    ACQUIRE_COMMAND,
    DEFAULT_CHECKOUT,
    SkillSourceError,
    read_skill_source_pin,
    validate_skill_source,
)

#: Named in drift messages so a forgotten regeneration is self-correcting.
SYNC_COMMAND = REGENERATE_COMMAND


def _summarise(result: FallbackGeneration) -> str:
    manifest = result.manifest
    lead = (
        f"{manifest.repository} @ {manifest.short_revision}: "
        f"{len(manifest.skills)} Skills packaged "
        f"({len(manifest.adopted)} adopted, "
        f"{len(manifest.project_owned)} project-owned)"
    )
    if not result.changed:
        return f"{lead}; already in sync, no changes."
    parts = []
    if result.added:
        parts.append(f"added {len(result.added)} ({', '.join(result.added)})")
    if result.updated:
        parts.append(f"updated {len(result.updated)} ({', '.join(result.updated)})")
    if result.removed:
        parts.append(f"removed {len(result.removed)} ({', '.join(result.removed)})")
    return f"{lead}; " + "; ".join(parts) + "."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate git-loopy's packaged Skill fallback from an acquisition "
            "of the pinned external catalog revision, or verify the committed "
            "fallback offline."
        )
    )
    parser.add_argument(
        "--from",
        dest="checkout",
        type=Path,
        default=DEFAULT_CHECKOUT,
        help=(
            "the acquired checkout of the pinned revision to cut from "
            f"(default: {DEFAULT_CHECKOUT}, where `{ACQUIRE_COMMAND}` lands it)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "verify the committed packaged fallback against its pin and record "
            "without acquiring anything or writing anything (for CI / pre-flight)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.check:
        try:
            verified = verify_packaged_fallback(PACKAGED, policy=INCLUSION_POLICY)
        except SkillFallbackError as exc:
            print(f"packaged Skill fallback check failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"packaged Skill fallback verified: {len(verified.skills)} Skills "
            f"({len(verified.adopted)} cut from {verified.repository} @ "
            f"{verified.revision[:12]}, {len(verified.project_owned)} project-owned) "
            f"(catalog {verified.catalog_sha256[:12]})"
        )
        return 0

    try:
        pin = read_skill_source_pin(PACKAGED.pin)
        checkout = validate_skill_source(pin, args.checkout)
    except SkillSourceError as exc:
        print(
            f"cannot regenerate the packaged Skill fallback: {exc}\n"
            f"acquire the pinned revision first with:\n  {ACQUIRE_COMMAND}",
            file=sys.stderr,
        )
        return 1

    try:
        result = generate_packaged_fallback(
            pin, checkout, PACKAGED, policy=INCLUSION_POLICY
        )
    except SkillFallbackError as exc:
        print(f"packaged Skill fallback generation failed: {exc}", file=sys.stderr)
        return 1

    print(_summarise(result))
    stale = INCLUSION_POLICY.excluded_absent(checkout.skills)
    if stale:
        print(
            "note: the inclusion policy excludes Skills the pinned revision does "
            f"not carry: {', '.join(stale)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
