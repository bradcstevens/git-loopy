"""Static guard: every retired product brand stays retired (issues #50, ADR-0012).

This project has been renamed twice, each time as a hard cut:

* ADR-0005 renamed ``ralph-afk`` -> ``copiloop``.
* ADR-0012 renamed ``copiloop`` -> ``git-loopy``.

Both old brands are now retired. The distribution, the importable module, the
console script, the environment-variable prefix, and the runtime/source
directory names of *each* retired brand must never reappear. The current brand
is ``git-loopy`` (distribution / console command / on-disk ``git-loopy/`` +
``.git-loopy/`` directories) with the importable package spelled ``git_loopy``
(Python modules cannot contain a hyphen) and the ``GIT_LOOPY_*`` env prefix.

This guard is the single regression tripwire for both renames: it text-scans
every git-tracked file in the repository and fails if any retired branding
comes back. Scanning the *tracked* surface (not a raw filesystem walk) keeps the
check deterministic and immune to whatever untracked runtime artefacts,
virtualenvs, or scratch files happen to sit in a working tree.

The *technique* the tool orchestrates keeps its name -- the "Ralph loop" (an
unattended, iterative loop that drives the Copilot CLI). That phrase matches
none of the forbidden patterns below, so it is exempt by construction, never by
allowlist.

Forbidden (matched case-insensitively) substrings::

    ralph_afk    the retired ralph-afk importable module / OTel span prefix
    ralph-afk    the retired ralph-afk distribution / console-script name
    RALPH_       the retired ralph-afk environment-variable prefix
    .ralph/      the retired ralph-afk runtime-artefact directory
    ralph/       the retired ralph-afk source directory
    copiloop     the retired copiloop brand in every form (module, script,
                 distribution, ``COPILOOP_*`` env prefix, ``copiloop/`` /
                 ``.copiloop/`` directories) -- a unique coinage, so a plain
                 case-insensitive substring catches all of them at once

The ``ralph`` forms stay narrow on purpose so the retained "Ralph loop" concept
and the repo slug's ``ralph-starter`` segment do not trip the guard; ``copiloop``
is a plain substring because the whole brand is retired with no surviving use.

A small allowlist of files legitimately narrates the retirements themselves --
the rename ADRs and their siblings, the raw feature-request intake, the domain
glossary's "flagged ambiguities", and this guard -- so they are exempt.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# The retired-branding patterns, matched case-insensitively. ``.ralph/`` is a
# subset of ``ralph/`` but is spelled out for clarity of intent in failures.
# ``copiloop`` is a plain substring: the coinage is unique to the retired brand,
# so every cased form (COPILOOP_, Copiloop, copiloop/, .copiloop/) is covered.
FORBIDDEN = re.compile(
    r"ralph_afk|ralph-afk|RALPH_|\.ralph/|ralph/|copiloop",
    re.IGNORECASE,
)

# Repo-relative POSIX path prefixes that may legitimately mention the retired
# names because they *document the retirement itself* (point-in-time history).
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "docs/adr/",  # architecture decision records are immutable history
    "docs/feature-requests/",  # raw, human-owned intake (append-only)
)

# Individual repo-relative files that are exempt for the same reason.
_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "CONTEXT.md",  # domain glossary: the "flagged ambiguities" narrate the renames
    }
)

# Generated / non-source content that is never text-scanned.
_SKIP_SUFFIXES: tuple[str, ...] = (".lock",)


def _find_repo_root() -> Path | None:
    """Walk up from this file to the repo root.

    The root is the first ancestor holding both ``docs/adr/`` and ``CONTEXT.md``.
    Returns ``None`` when neither is found (e.g. an installed-wheel run with no
    source checkout), which the scan test treats as "nothing to guard -> skip".
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
    """A file is exempt if it is this guard, an exempt file, or under an exempt tree."""
    if rel == guard_rel:
        return True
    if rel in _EXEMPT_FILES:
        return True
    return rel.startswith(_EXEMPT_PREFIXES)


def test_no_retired_branding_in_tracked_files() -> None:
    """Every tracked, non-exempt text file must be free of any retired branding."""
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
            # Missing (broken symlink / submodule gitlink) or a binary blob:
            # there is no text branding to scan.
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = FORBIDDEN.search(line)
            if match:
                failures.append(f"{rel}:{lineno}: {match.group(0)!r} in: {line.strip()[:100]}")

    assert scanned >= 100, (
        f"guard scanned only {scanned} tracked files -- the scan looks broken "
        "(expected the whole repository source + docs surface)"
    )
    assert not failures, (
        "retired branding must not reappear -- ADR-0005 renamed the product to "
        "`copiloop` and ADR-0012 then renamed it to `git-loopy`, both as hard "
        "cuts. Use the current `git-loopy` / `git_loopy` / `GIT_LOOPY_` / "
        "`git-loopy/` / `.git-loopy/` forms instead. Offending lines:\n  "
        + "\n  ".join(failures)
    )


def test_forbidden_pattern_matches_retired_names_but_not_retained_ones() -> None:
    """Guard the guard: the pattern flags every retired form and no retained one."""
    must_flag = (
        # retired ralph-afk brand (ADR-0005)
        "ralph_afk",
        "import ralph_afk.loop",
        "ralph-afk",
        "uv run ralph-afk",
        "RALPH_MODEL",
        "RALPH_OTEL_ENABLED=1",
        ".ralph/logs/run.jsonl",
        "ralph/python/ralph_afk",
        # retired copiloop brand (ADR-0012)
        "copiloop",
        "import copiloop.loop",
        "COPILOOP_MODEL",
        "COPILOOP_OTEL_ENABLED=1",
        "CopiloopApp",
        ".copiloop/logs/run.jsonl",
        "copiloop/python",
    )
    for sample in must_flag:
        assert FORBIDDEN.search(sample), f"expected {sample!r} to be flagged as retired branding"

    must_not_flag = (
        # current git-loopy brand -- must never be flagged
        "git-loopy",
        "git_loopy",
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_OTEL_ENABLED",
        "GitLoopyApp",
        ".git-loopy/logs/run.jsonl",
        "git-loopy/python",
        "git_loopy.run",
        # retained concept + former repo slug -- no _, -afk, ralph/, or copiloop
        "the Ralph loop technique",
        "github-copilot-ralph-starter-kit",
    )
    for sample in must_not_flag:
        assert not FORBIDDEN.search(sample), f"did not expect {sample!r} to be flagged"
