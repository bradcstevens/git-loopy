"""Static guard: the domain glossary's former names stay retired (#790, #951).

#790 renamed the domain glossary ``CONTEXT.md`` -> ``GLOSSARY.md``, and its
multi-context map ``CONTEXT-MAP.md`` -> ``GLOSSARY-MAP.md``. The Run prompt then
kept a fallback to the old names; #951 retired that too, so this repository names
its glossary one way only.

This guard is the regression tripwire for both:

* no tracked file, at any depth, may be named ``CONTEXT.md`` or ``CONTEXT-MAP.md``
  again, and
* no tracked, non-exempt file may mention either name -- so no instruction,
  prompt or test can send an Agent to a glossary this repository no longer has.

Scanning the *tracked* surface (not a raw filesystem walk) keeps the check
deterministic and immune to untracked runtime artefacts and scratch files.

A small allowlist of files legitimately names the old files because it records
history or narrates the rename: the ADRs and release notes (immutable history), a
mined capture of tracker text, two glossary tests that quote ADR-0019 and ADR-0038
verbatim, and the two documents that explain the rename --
``docs/agents/domain.md`` to Agents, and ``docs/skill-catalog-source.md`` to
operators whose repositories have not migrated.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest

# The retired file names, as written: both files were always upper-case. The
# lookarounds keep a longer name that merely ends in one of them from matching.
FORBIDDEN = re.compile(r"(?<![\w-])CONTEXT(?:-MAP)?\.md(?![\w-])")

_RETIRED_FILES: frozenset[str] = frozenset({"CONTEXT.md", "CONTEXT-MAP.md"})

# Repo-relative POSIX path prefixes that may legitimately mention the retired
# names because they record point-in-time history.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "docs/adr/",  # architecture decision records are immutable history
    "docs/releases/",  # shipped release notes are immutable history
)

# Individual repo-relative files that are exempt for the same reason, or because
# they narrate the rename itself.
_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        # tracker text mined verbatim at a point in time
        "docs/research/completion-proof/mined.json",
        # quote ADR-0019 and ADR-0038 verbatim
        "git-loopy/python/tests/test_glossary_calibration_terms.py",
        "git-loopy/python/tests/test_glossary_collapsed_activity_band.py",
        # narrate the rename: to Agents, and to operators of unmigrated repositories
        "docs/agents/domain.md",
        "docs/skill-catalog-source.md",
    }
)

# Generated / non-source content that is never text-scanned.
_SKIP_SUFFIXES: tuple[str, ...] = (".lock",)


def _find_repo_root() -> Path | None:
    """Walk up from this file to the repo root.

    The root is the first ancestor holding both ``docs/adr/`` and ``GLOSSARY.md``.
    Returns ``None`` when neither is found (e.g. an installed-wheel run with no
    source checkout), which the scan tests treat as "nothing to guard -> skip".
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "GLOSSARY.md").is_file():
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
    """A file is exempt if it is this guard, an exempt file, or under an exempt tree."""
    if rel == guard_rel:
        return True
    if rel in _EXEMPT_FILES:
        return True
    return rel.startswith(_EXEMPT_PREFIXES)


def test_retired_glossary_files_are_not_tracked() -> None:
    """No tracked file, at any depth, carries a retired glossary name (#790)."""
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")

    tracked = _tracked_files(repo_root)
    if not tracked:
        pytest.skip("git-tracked file list unavailable -- nothing to check")

    revived = [rel for rel in tracked if PurePosixPath(rel).name in _RETIRED_FILES]
    assert not revived, (
        "#790 renamed the domain glossary to `GLOSSARY.md` and its map to "
        "`GLOSSARY-MAP.md` -- the old files must not come back: " + ", ".join(revived)
    )


def test_no_retired_glossary_names_in_tracked_files() -> None:
    """No tracked, non-exempt file may name the retired glossary files."""
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
        "the domain glossary is `GLOSSARY.md` (`GLOSSARY-MAP.md` for multiple "
        "contexts): #790 retired `CONTEXT.md` / `CONTEXT-MAP.md`, and #951 retired "
        "the Run prompt's fallback to them. Name the glossary by its current file. "
        "Offending lines:\n  " + "\n  ".join(failures)
    )


def test_forbidden_pattern_flags_retired_names_but_not_current_ones() -> None:
    """Guard the guard: the pattern flags both retired names and no current one."""
    must_flag = (
        "CONTEXT.md",
        "`CONTEXT-MAP.md`",
        "src/ordering/CONTEXT.md",
        "fall back to `CONTEXT.md` / `CONTEXT-MAP.md`.",
    )
    for sample in must_flag:
        assert FORBIDDEN.search(sample), f"expected {sample!r} to be flagged"

    must_not_flag = (
        "GLOSSARY.md",
        "`GLOSSARY-MAP.md`",
        "src/ordering/GLOSSARY.md",
        "_DEFAULT_CONTEXT_TARGET_TOKENS",
    )
    for sample in must_not_flag:
        assert not FORBIDDEN.search(sample), f"did not expect {sample!r} to be flagged"
