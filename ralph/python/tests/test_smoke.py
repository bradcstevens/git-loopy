"""Smoke test for the ``ralph-afk`` console script (issue #2).

The single assertion this slice promises: ``ralph-afk --help`` exits 0 and
its help text surfaces the positional ``<max-iterations>`` argument. This
covers the scaffold-stub contract — that the entry point is wired through
``[project.scripts]`` and that ``argparse`` is configured.

Deeper behavioural coverage (the iteration driver, the wrapper contract,
the JSONL writer, the renderer, …) lands in later slices.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest


def _ralph_afk_command() -> list[str]:
    """Prefer the installed console script; fall back to ``python -m``.

    ``uv sync --project ralph/python`` puts ``ralph-afk`` on the venv's PATH
    via ``[project.scripts]``. If the test happens to run in an environment
    where the script isn't on PATH yet (e.g. partial install), fall back to
    invoking the module directly so the smoke remains meaningful.
    """
    if shutil.which("ralph-afk"):
        return ["ralph-afk"]
    return [sys.executable, "-m", "ralph_afk.cli"]


def test_ralph_afk_help_exits_zero() -> None:
    """`ralph-afk --help` prints help and exits 0."""
    cmd = _ralph_afk_command() + ["--help"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"ralph-afk --help exited {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # The positional <max-iterations> arg must be visible in --help output,
    # so any user (or wrapper) reading the help can discover the surface.
    assert "max-iterations" in result.stdout or "max_iterations" in result.stdout, (
        f"--help output did not surface the positional max-iterations "
        f"argument; stdout was:\n{result.stdout}"
    )


def test_ralph_afk_rejects_negative_iterations() -> None:
    """Negative max_iterations is rejected with a non-zero exit and clear error."""
    cmd = _ralph_afk_command() + ["-1"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0, (
        "ralph-afk should reject a negative max_iterations argument; "
        f"got exit 0 with stdout={result.stdout!r}"
    )
    assert "max_iterations" in result.stderr or "non-negative" in result.stderr, (
        f"expected a max_iterations validation message on stderr; "
        f"stderr was:\n{result.stderr}"
    )


@pytest.mark.parametrize("source", ["github", "prds"])
def test_ralph_afk_accepts_documented_issue_sources(
    source: str,
    tmp_path,
    monkeypatch,
) -> None:
    """Both documented ISSUE_SOURCE values are accepted by the scaffold stub.

    The stub does not yet collect issues — that lands in the loop slice
    (issue #10). This test only locks in the env-var surface so future
    slices don't accidentally rename the supported values.
    """
    # Run from a fresh tmp_path that is a git repo so resolve_repo_root() succeeds.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setenv("ISSUE_SOURCE", source)
    cmd = _ralph_afk_command()
    result = subprocess.run(
        cmd,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"ralph-afk should accept ISSUE_SOURCE={source!r}; "
        f"exit={result.returncode} stderr={result.stderr!r}"
    )
    assert f"ISSUE_SOURCE={source}" in result.stdout, (
        f"stub should echo ISSUE_SOURCE; stdout was:\n{result.stdout}"
    )


def test_ralph_afk_rejects_unknown_issue_source(tmp_path, monkeypatch) -> None:
    """An unsupported ISSUE_SOURCE value is rejected with a clear error.

    Matches the bash runner's behaviour at ralph/afk.sh:68-73.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setenv("ISSUE_SOURCE", "gitlab")
    result = subprocess.run(
        _ralph_afk_command(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0, (
        "ralph-afk should reject an unknown ISSUE_SOURCE value; "
        f"got exit 0 with stdout={result.stdout!r}"
    )
    assert "ISSUE_SOURCE" in result.stderr, (
        f"expected ISSUE_SOURCE validation message on stderr; "
        f"stderr was:\n{result.stderr}"
    )


def test_resolve_repo_root_works_from_child_directory(tmp_path) -> None:
    """`ralph-afk` works from a child dir, resolving repo root via git.

    Acceptance criterion: running from any cwd inside the repo should
    resolve to the repo root rather than the cwd. Verified end-to-end by
    invoking the console script from a nested subdirectory of a fresh git
    repo and asserting the printed ``repo_root`` is the repo root, not the
    nested cwd.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    nested = tmp_path / "deep" / "nested" / "child"
    nested.mkdir(parents=True)
    result = subprocess.run(
        _ralph_afk_command(),
        cwd=nested,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"exit={result.returncode} stderr={result.stderr!r}"
    )
    # macOS aliases /var to /private/var; both Path.resolve() calls must
    # agree, so we compare resolved Paths rather than raw strings.
    import re
    from pathlib import Path
    match = re.search(r"repo_root=(\S+)", result.stdout)
    assert match, f"expected 'repo_root=...' in stdout:\n{result.stdout}"
    printed = Path(match.group(1)).resolve()
    expected = tmp_path.resolve()
    assert printed == expected, (
        f"repo_root should resolve to the repo root, not the cwd; "
        f"printed={printed!r} expected={expected!r}"
    )
