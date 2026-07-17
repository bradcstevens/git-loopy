"""Static guard: the retired ``afk.sh`` bash launcher stays retired (issue #54).

ADR-0007 makes the ``git-loopy`` Python CLI the single, canonical entrypoint and
**deletes** the ``afk.sh`` convenience launcher that used to hard-code
``GIT_LOOPY_MODEL=claude-opus-4.8 GIT_LOOPY_REASONING_EFFORT=max``. Model and
reasoning effort now fold into the ``--model`` / ``--reasoning-effort`` flags
(top of the precedence chain) and persisted ``config.toml``; there is no bash
launcher and no environment-only launch shim.

This guard is the regression tripwire for that hard cut:

* the ``git-loopy/afk.sh`` file must not come back, and
* no git-tracked, non-exempt file may reference ``afk.sh`` -- so a stray "run it
  with afk.sh" instruction can never re-document the deleted entrypoint.

Scanning the *tracked* surface (not a raw filesystem walk) keeps the check
deterministic and immune to untracked runtime artefacts / scratch files.

The retired thing is the ``afk.sh`` *file*, not the AFK vocabulary: the "AFK
loop", "AFK runner", and "AFK-ready" terms are retained (the technique the tool
orchestrates), and they match none of the forbidden ``afk.sh`` pattern by
construction -- exempt by design, never by allowlist.

A small allowlist of files legitimately narrates the retirement itself -- the
ADRs (immutable history) and the raw feature-request intake -- so they are
exempt, mirroring the sibling retired-branding static guard.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# The retired launcher filename, matched case-insensitively. ``AFK`` on its own
# (the retained *technique* vocabulary) never matches -- only ``afk.sh`` does.
FORBIDDEN = re.compile(r"afk\.sh", re.IGNORECASE)

# Repo-relative POSIX path prefixes that may legitimately mention the retired
# launcher because they *document the retirement itself* (point-in-time history).
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "docs/adr/",  # architecture decision records are immutable history
    "docs/feature-requests/",  # raw, human-owned intake (append-only)
)

# Generated / non-source content that is never text-scanned.
_SKIP_SUFFIXES: tuple[str, ...] = (".lock",)


def _find_repo_root() -> Path | None:
    """Walk up from this file to the repo root.

    The root is the first ancestor holding both ``docs/adr/`` and ``CONTEXT.md``.
    Returns ``None`` when neither is found (e.g. an installed-wheel run with no
    source checkout), which the scan tests treat as "nothing to guard -> skip".
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _tracked_files(repo_root: Path) -> list[str]:
    """Repo-relative POSIX paths of every git-tracked file (``[]`` if unavailable)."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [path for path in completed.stdout.split("\0") if path]


def _is_exempt(rel: str, guard_rel: str) -> bool:
    """A file is exempt if it is this guard or lives under an exempt tree."""
    if rel == guard_rel:
        return True
    return rel.startswith(_EXEMPT_PREFIXES)


def test_afk_sh_launcher_is_deleted() -> None:
    """The ``git-loopy/afk.sh`` launcher file must not exist (ADR-0007 hard cut)."""
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")
    assert not (repo_root / "git-loopy" / "afk.sh").exists(), (
        "git-loopy/afk.sh was deleted by ADR-0007 (single Python entrypoint) -- "
        "it must not come back. Model/effort now fold into the --model / "
        "--reasoning-effort flags and config.toml."
    )


def test_no_afk_sh_references_in_tracked_files() -> None:
    """No tracked, non-exempt file may reference the retired ``afk.sh`` launcher."""
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to scan")

    tracked = _tracked_files(repo_root)
    if not tracked:
        pytest.skip("git-tracked file list unavailable -- nothing to scan")

    guard_rel = Path(__file__).resolve().relative_to(repo_root).as_posix()
    scanned = 0
    failures: list[str] = []
    for rel in tracked:
        if _is_exempt(rel, guard_rel) or rel.endswith(_SKIP_SUFFIXES):
            continue
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = FORBIDDEN.search(line)
            if match:
                failures.append(
                    f"{rel}:{lineno}: {match.group(0)!r} in: {line.strip()[:100]}"
                )

    assert scanned >= 100, (
        f"guard scanned only {scanned} tracked files -- the scan looks broken "
        "(expected the whole repository source + docs surface)"
    )
    assert not failures, (
        "the retired `afk.sh` launcher must not be referenced -- ADR-0007 made "
        "`git-loopy` the single entrypoint and deleted afk.sh. Launch with "
        "`git-loopy` (or `uvx git-loopy`) and set model/effort via --model / "
        "--reasoning-effort or config.toml. Offending lines:\n  "
        + "\n  ".join(failures)
    )


def test_forbidden_pattern_flags_launcher_but_not_retained_afk_vocab() -> None:
    """Guard the guard: the pattern flags the launcher and no retained AFK term."""
    must_flag = (
        "afk.sh",
        "git-loopy/afk.sh",
        "bash afk.sh",
        "run ./afk.sh --select-model",
    )
    for sample in must_flag:
        assert FORBIDDEN.search(sample), f"expected {sample!r} to be flagged"

    must_not_flag = (
        "the AFK loop",  # retained technique vocabulary
        "the AFK runner",
        "AFK-ready issues",
        "AFK-ready",
        "afk-iter.42",  # a bare `afk` token with no `.sh` is not the launcher
        "git-loopy",
    )
    for sample in must_not_flag:
        assert not FORBIDDEN.search(sample), f"did not expect {sample!r} to be flagged"
