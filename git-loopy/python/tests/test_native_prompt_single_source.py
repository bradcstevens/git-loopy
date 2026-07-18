"""Static guard: the native Orchestrators single-source the shared PROMPT.md.

Issue #81 (phase-1 native Orchestrators) requires each port's prompt resolution
to fall back to the packaged prompt "**without adding another prompt copy**" and
for the shell and PowerShell ports to "reuse the existing shared prompt". The
per-port boundary suites only ever inject a *synthetic* packaged prompt into a
scratch repo, so nothing pins the load-bearing single-source invariant:

* there is exactly **one** shared prompt at ``git-loopy/PROMPT.md`` plus the
  byte-identical wheel copy at ``git-loopy/python/git_loopy/PROMPT.md`` (kept in
  sync by ``test_prompt.test_packaged_prompt_matches_project_prompt_byte_for_byte``),
  and **no** port-local ``PROMPT.md`` under ``git-loopy/shell/`` or
  ``git-loopy/powershell/``; and
* the shell and PowerShell entry points resolve their packaged prompt one level
  up out of the port directory -- i.e. to the shared ``git-loopy/PROMPT.md`` --
  rather than shipping or pointing at a divergent copy.

This guard is the regression tripwire for that invariant. It scans the
*git-tracked* surface (not a raw filesystem walk) so the check is deterministic
and immune to untracked scratch prompts a working tree may hold. On an
installed-wheel run with no source checkout it degrades to a skip, mirroring the
sibling ``test_no_afk_sh`` / ``test_no_retired_branding`` static guards.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# The two -- and only two -- tracked prompt copies (repo-relative POSIX paths).
# The shared prompt the native ports fall back to, and the wheel copy the Python
# package ships; a byte-for-byte sync guard lives in ``test_prompt``.
SHARED_PROMPT = "git-loopy/PROMPT.md"
WHEEL_PROMPT = "git-loopy/python/git_loopy/PROMPT.md"
EXPECTED_PROMPT_FILES: frozenset[str] = frozenset({SHARED_PROMPT, WHEEL_PROMPT})

# The native ports live here; neither may ship its own prompt copy.
_PORT_PREFIXES: tuple[str, ...] = (
    "git-loopy/shell/",
    "git-loopy/powershell/",
)

# A ``PROMPT.md`` (any case) anywhere under the ``git-loopy/`` tree. The trailing
# path segment must be exactly ``prompt.md`` -- an unrelated doc such as
# ``create-prompt.md`` is not a prompt copy and must not match.
_PROMPT_FILE = re.compile(r"^git-loopy/(?:.*/)?prompt\.md$", re.IGNORECASE)


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


def _tracked_prompt_files(repo_root: Path) -> set[str]:
    """The tracked ``PROMPT.md``/``prompt.md`` copies under ``git-loopy/``."""
    return {path for path in _tracked_files(repo_root) if _PROMPT_FILE.match(path)}


def test_shared_prompt_is_the_single_native_source() -> None:
    """Exactly the shared prompt + wheel copy are tracked -- no other copy."""
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")
    if not _tracked_files(repo_root):
        pytest.skip("git-tracked file list unavailable -- nothing to scan")

    prompt_files = _tracked_prompt_files(repo_root)
    assert prompt_files == set(EXPECTED_PROMPT_FILES), (
        "the native Orchestrators must reuse the single shared "
        "git-loopy/PROMPT.md (kept byte-identical to the packaged wheel copy) "
        "and must not add another prompt copy. Unexpected or missing prompt "
        f"files: {sorted(prompt_files ^ set(EXPECTED_PROMPT_FILES))}"
    )


def test_native_ports_do_not_ship_their_own_prompt() -> None:
    """No ``PROMPT.md`` lives under the shell or PowerShell port directories."""
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")
    if not _tracked_files(repo_root):
        pytest.skip("git-tracked file list unavailable -- nothing to scan")

    offenders = sorted(
        path
        for path in _tracked_prompt_files(repo_root)
        if path.startswith(_PORT_PREFIXES)
    )
    assert not offenders, (
        "a native port shipped its own prompt copy instead of reusing the "
        f"shared git-loopy/PROMPT.md: {offenders}"
    )


def test_native_entrypoints_wire_the_shared_packaged_prompt() -> None:
    """Both entry points resolve their packaged prompt to the shared copy.

    The shell and PowerShell launchers each compute their packaged prompt one
    directory *above* the port (``git-loopy/shell`` / ``git-loopy/powershell``),
    which is the shared ``git-loopy/`` tree -- not a port-local file.
    """
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")

    shared = repo_root / SHARED_PROMPT
    assert shared.is_file() and shared.read_text(encoding="utf-8").strip(), (
        "the shared git-loopy/PROMPT.md the native ports fall back to must exist "
        "and be non-empty"
    )

    shell_text = (repo_root / "git-loopy/shell/git-loopy.sh").read_text(
        encoding="utf-8"
    )
    assert '"$script_dir/.."' in shell_text and "/PROMPT.md" in shell_text, (
        "git-loopy.sh must resolve its packaged prompt one level up out of "
        "shell/ (the shared git-loopy/PROMPT.md)"
    )
    assert '"$script_dir/PROMPT.md"' not in shell_text, (
        "git-loopy.sh must not point at a shell-local PROMPT.md copy"
    )

    ps_text = (repo_root / "git-loopy/powershell/git-loopy.ps1").read_text(
        encoding="utf-8"
    )
    assert "Split-Path -Parent $PSScriptRoot" in ps_text and '"PROMPT.md"' in ps_text, (
        "git-loopy.ps1 must resolve its packaged prompt one level up out of "
        "powershell/ (the shared git-loopy/PROMPT.md)"
    )


def test_prompt_matcher_flags_port_copies_but_not_unrelated_docs() -> None:
    """Guard the guard: the matcher catches prompt copies, not lookalike docs."""
    for flagged in (
        SHARED_PROMPT,
        WHEEL_PROMPT,
        "git-loopy/shell/PROMPT.md",
        "git-loopy/powershell/prompt.md",
    ):
        assert _PROMPT_FILE.match(flagged), f"expected {flagged!r} to be flagged"

    for ignored in (
        "git-loopy/python/git_loopy/create-prompt.md",
        ".copilot/skills/foundry/create-prompt.md",
        "docs/agents/domain.md",
    ):
        assert not _PROMPT_FILE.match(ignored), f"did not expect {ignored!r} flagged"
