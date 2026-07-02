"""The bootstrap agent skill ships inside the wheel and stays in sync (issue #53).

``copiloop init`` offers to scaffold copiloop's agent skills into the chosen
scope's ``.copilot/skills/`` (ADR-0006, "run from anywhere"). For that to work in
a fresh checkout-free install, the skill has to travel *inside* the wheel as
package data -- exactly like the packaged ``PROMPT.md`` and ``pricing.toml``.

The repo-root ``.copilot/skills/setup-agent-skills/`` is the human-facing
canonical copy; ``copiloop/skills/setup-agent-skills/`` is the vendored copy that
ships in the wheel. These tests assert the vendored copy (a) exists and is the
real skill, (b) is packaged into the built wheel, and (c) has not drifted from
the repo-root canonical.
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import copiloop
from copiloop import init as init_module

_SKILL_DIRNAME = "setup-agent-skills"


def _packaged_skill_dir() -> Path:
    return init_module._packaged_skills_path() / _SKILL_DIRNAME


def test_packaged_skill_is_present_and_is_the_real_skill() -> None:
    skill = _packaged_skill_dir()
    assert skill.is_dir(), f"vendored skill missing at {skill}"
    skill_md = skill / "SKILL.md"
    assert skill_md.is_file(), "vendored skill has no SKILL.md"
    assert f"name: {_SKILL_DIRNAME}" in skill_md.read_text(encoding="utf-8"), (
        "packaged SKILL.md is not the real setup-agent-skills skill"
    )


def test_packaged_skills_dir_only_holds_skill_subdirectories() -> None:
    """The ``skills/`` package dir is a container of skills, not loose files."""
    for child in init_module._packaged_skills_path().iterdir():
        assert child.is_dir(), f"unexpected non-skill entry in skills/: {child.name}"


# ---------------------------------------------------------------------------
# Wheel packaging: the skill ships as copiloop/skills/... in the built artifact
# ---------------------------------------------------------------------------


def test_skill_is_packaged_into_the_built_wheel(tmp_path: Path) -> None:
    """Hatchling packages the vendored skill into the wheel (verified in-artifact)."""
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is the repo toolchain
        pytest.skip("uv not available to build the wheel")
    # <site>/copiloop/__init__.py -> .../copiloop/python/copiloop -> .../copiloop/python
    package_dir = Path(copiloop.__file__).resolve().parent.parent
    if not (package_dir / "pyproject.toml").is_file():  # pragma: no cover
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
    wanted = f"copiloop/skills/{_SKILL_DIRNAME}/SKILL.md"
    assert wanted in names, (
        f"{wanted} missing from the built wheel; members were:\n{names}"
    )


# ---------------------------------------------------------------------------
# Sync guard: the vendored copy stays byte-identical to the repo-root canonical
# ---------------------------------------------------------------------------


def _find_repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _assert_dirs_identical(left: Path, right: Path) -> None:
    cmp = filecmp.dircmp(str(left), str(right))
    assert not cmp.left_only, f"only in canonical {left}: {cmp.left_only}"
    assert not cmp.right_only, f"only in vendored {right}: {cmp.right_only}"
    assert not cmp.diff_files, f"content drift between copies: {cmp.diff_files}"
    assert not cmp.funny_files, f"uncomparable files: {cmp.funny_files}"
    for sub in cmp.common_dirs:
        _assert_dirs_identical(left / sub, right / sub)


def test_vendored_skill_is_in_sync_with_repo_root_canonical() -> None:
    """The wheel-vendored skill must not drift from the repo-root canonical copy."""
    repo_root = _find_repo_root()
    if repo_root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("not a source checkout; nothing to sync-check")
    canonical = repo_root / ".copilot" / "skills" / _SKILL_DIRNAME
    if not canonical.is_dir():  # pragma: no cover - defensive
        pytest.skip(f"repo-root canonical skill not found at {canonical}")
    _assert_dirs_identical(canonical, _packaged_skill_dir())
