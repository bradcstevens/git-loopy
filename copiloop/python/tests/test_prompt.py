"""Tests for the packaged-default prompt + project>global>packaged resolution.

Issue #52, ADR-0006 — the "run copiloop from anywhere" story:

* ``copiloop.loop._read_prompt`` resolves **project > global > packaged** and
  falls through to the packaged default when no override file exists, so a bare
  run in a repo with no ``copiloop/`` folder still has a working prompt.
* The wheel ships a *real* default ``PROMPT.md`` (not a stub): it carries the
  load-bearing runner contract (task selection, the working marker, the issue
  FINAL SEQUENCE, and the ``Closes #N`` close keyword).
* Hatchling packages ``PROMPT.md`` into the built wheel as ``copiloop/PROMPT.md``.

Every resolver test injects the repo root and a global directory as tmp paths,
so no test reads the developer's real ``$HOME`` / ``$XDG_CONFIG_HOME`` or the
real ``copiloop/`` tree (the packaged default is read from its installed
location, which is stable).
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import copiloop
from copiloop import loop as loop_module
from copiloop import settings


def _global_env(global_home: Path) -> dict[str, str]:
    """An env mapping whose global scope resolves under ``global_home``."""
    return {"XDG_CONFIG_HOME": str(global_home)}


def _write_project_prompt(
    repo_root: Path, text: str, *, name: str = "PROMPT.md"
) -> None:
    d = repo_root / "copiloop"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def _write_global_prompt(global_home: Path, text: str) -> None:
    path = settings.global_prompt_path(_global_env(global_home))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Resolution ladder: project > global > packaged
# ---------------------------------------------------------------------------


def test_read_prompt_falls_through_to_packaged_default(tmp_path: Path) -> None:
    """No project or global prompt anywhere -> the packaged default is used."""
    repo = tmp_path / "repo"  # note: no copiloop/ folder at all
    repo.mkdir()
    global_home = tmp_path / "xdg"  # empty -> no global override
    text = loop_module._read_prompt(repo, _global_env(global_home))
    assert text == loop_module._packaged_prompt_path().read_text(encoding="utf-8")
    assert text.strip(), "packaged default prompt must be non-empty"


def test_read_prompt_global_overrides_packaged(tmp_path: Path) -> None:
    """A global ``~/.config/copiloop/PROMPT.md`` beats the packaged default."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    _write_global_prompt(global_home, "GLOBAL PROMPT")
    assert (
        loop_module._read_prompt(repo, _global_env(global_home)) == "GLOBAL PROMPT"
    )


def test_read_prompt_project_overrides_global_and_packaged(tmp_path: Path) -> None:
    """A project ``./copiloop/PROMPT.md`` beats both global and packaged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    _write_global_prompt(global_home, "GLOBAL PROMPT")
    _write_project_prompt(repo, "PROJECT PROMPT")
    assert (
        loop_module._read_prompt(repo, _global_env(global_home)) == "PROJECT PROMPT"
    )


def test_read_prompt_project_lowercase_prompt_md_supported(tmp_path: Path) -> None:
    """The legacy lowercase ``copiloop/prompt.md`` probe is preserved."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    _write_project_prompt(repo, "lower project", name="prompt.md")
    assert (
        loop_module._read_prompt(repo, _global_env(global_home)) == "lower project"
    )


def test_read_prompt_ladder_project_gt_global_gt_packaged(tmp_path: Path) -> None:
    """The full ladder in one test: remove each tier and watch the fallback."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    packaged = loop_module._packaged_prompt_path().read_text(encoding="utf-8")

    # 1) nothing configured -> packaged default.
    assert loop_module._read_prompt(repo, _global_env(global_home)) == packaged

    # 2) add a global override -> global wins over packaged.
    _write_global_prompt(global_home, "GLOBAL")
    assert loop_module._read_prompt(repo, _global_env(global_home)) == "GLOBAL"

    # 3) add a project override -> project wins over global (and packaged).
    _write_project_prompt(repo, "PROJECT")
    assert loop_module._read_prompt(repo, _global_env(global_home)) == "PROJECT"


# ---------------------------------------------------------------------------
# Packaged default: present, non-empty, and the real runner contract
# ---------------------------------------------------------------------------


def _packaged_prompt_text() -> str:
    return loop_module._packaged_prompt_path().read_text(encoding="utf-8")


def test_packaged_prompt_is_present_and_nonempty() -> None:
    assert loop_module._packaged_prompt_path().is_file()
    assert _packaged_prompt_text().strip()


@pytest.mark.parametrize(
    "marker",
    [
        "# ISSUES",  # the pool/source contract header
        "TASK SELECTION",  # single-task priority order
        "<working issue=N>",  # the working marker
        "FINAL SEQUENCE",  # the issue-closure contract
        "Closes #",  # the close-keyword backstop
    ],
)
def test_packaged_prompt_carries_runner_contract(marker: str) -> None:
    """The shipped default is the real runner prompt, not a placeholder stub."""
    assert marker in _packaged_prompt_text(), (
        f"packaged PROMPT.md is missing the load-bearing marker {marker!r}"
    )


# ---------------------------------------------------------------------------
# Wheel packaging: PROMPT.md ships as copiloop/PROMPT.md in the built artifact
# ---------------------------------------------------------------------------


def test_prompt_md_is_packaged_into_the_built_wheel(tmp_path: Path) -> None:
    """Hatchling packages ``PROMPT.md`` into the wheel (verified in the artifact)."""
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is the repo toolchain
        pytest.skip("uv not available to build the wheel")
    # <site>/copiloop/__init__.py -> .../copiloop/python/copiloop -> .../copiloop/python
    package_dir = Path(copiloop.__file__).resolve().parent.parent
    if not (package_dir / "pyproject.toml").is_file():  # pragma: no cover - non-editable install
        pytest.skip("copiloop is not a source checkout; cannot build the wheel")
    out = tmp_path / "dist"
    result = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out)],
        cwd=str(package_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"uv build failed (exit {result.returncode}):\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    wheels = list(out.glob("*.whl"))
    assert wheels, f"no wheel produced in {out}"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert "copiloop/PROMPT.md" in names, (
        f"PROMPT.md missing from the built wheel; members were:\n{names}"
    )
