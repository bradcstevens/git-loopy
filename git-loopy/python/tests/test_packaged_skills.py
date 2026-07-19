"""The workflow skill catalog ships inside the wheel and stays in sync (#122).

``git-loopy init`` scaffolds git-loopy's full **workflow skill catalog** into the
chosen scope's ``.copilot/skills/`` (ADR-0006, "run from anywhere"). For that to
work from a checkout-free install, the catalog has to travel *inside* the wheel
as package data -- exactly like the packaged ``PROMPT.md`` and ``pricing.toml``.

The repo-root ``.copilot/skills/`` is the single human-edited canonical source of
truth; ``git_loopy/skills/`` holds *generated* vendored copies regenerated
wholesale by ``scripts/sync_skills.py`` (the committed sync command). The vendored
catalog is exactly ``subdirs(.copilot/skills/) - SKILL_DENYLIST`` -- every
canonical skill except the three optional tool/vendor integrations.

These guards assert the vendored catalog (a) is exactly the canonical set minus
the denylist and byte-identical to canonical, (b) never contains a denied skill,
(c) ships in the built wheel for every catalog skill, and (d) the sync is
idempotent. A drift failure names the sync command so a forgotten regeneration is
self-correcting.
"""

from __future__ import annotations

import filecmp
import importlib.util
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import git_loopy
from git_loopy import init as init_module

# ---------------------------------------------------------------------------
# Import the committed sync script so the guard shares its single source of
# truth (the denylist + the enumeration) -- the sync and the guard can never
# disagree about which skills are vendored.
# ---------------------------------------------------------------------------

_SYNC_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_skills.py"
_spec = importlib.util.spec_from_file_location("sync_skills", _SYNC_SCRIPT)
assert _spec is not None and _spec.loader is not None
sync_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_skills)


def _packaged_skills_path() -> Path:
    return init_module._packaged_skills_path()


def _find_repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _canonical_skills_dir() -> Path | None:
    repo_root = _find_repo_root()
    if repo_root is None:  # pragma: no cover - installed wheel, no source checkout
        return None
    canonical = repo_root / ".copilot" / "skills"
    return canonical if canonical.is_dir() else None


def _assert_dirs_identical(left: Path, right: Path) -> None:
    cmp = filecmp.dircmp(str(left), str(right))
    assert not cmp.left_only, f"only in canonical {left}: {cmp.left_only}"
    assert not cmp.right_only, f"only in vendored {right}: {cmp.right_only}"
    _, mismatch, errors = filecmp.cmpfiles(
        str(left), str(right), cmp.common_files, shallow=False
    )
    assert not mismatch, f"content drift between copies: {mismatch}"
    assert not errors, f"uncomparable files: {errors}"
    assert not cmp.funny_files, f"uncomparable files: {cmp.funny_files}"
    for sub in cmp.common_dirs:
        _assert_dirs_identical(left / sub, right / sub)


# ---------------------------------------------------------------------------
# The shared denylist + enumeration (pinned)
# ---------------------------------------------------------------------------


def test_denylist_is_exactly_the_three_integrations() -> None:
    """The one place the exclusion set is defined names exactly the three."""
    assert sync_skills.SKILL_DENYLIST == frozenset(
        {"microsoft-docs", "microsoft-foundry", "playwright-cli"}
    )


def test_catalog_enumeration_is_canonical_subdirs_minus_denylist() -> None:
    """The catalog is ``subdirs(canonical) - denylist`` -- no allowlist."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; nothing to enumerate")
    all_subdirs = {child.name for child in canonical.iterdir() if child.is_dir()}
    expected = sorted(all_subdirs - sync_skills.SKILL_DENYLIST)
    names = sync_skills.catalog_skill_names(canonical)
    assert names == expected
    assert not (set(names) & sync_skills.SKILL_DENYLIST)


# ---------------------------------------------------------------------------
# The vendored package dir is a container of skills, nothing else
# ---------------------------------------------------------------------------


def test_packaged_skills_dir_only_holds_skill_subdirectories() -> None:
    """The ``skills/`` package dir is a container of skills, not loose files."""
    for child in _packaged_skills_path().iterdir():
        assert child.is_dir(), f"unexpected non-skill entry in skills/: {child.name}"


def test_every_vendored_skill_is_the_real_skill() -> None:
    """Every vendored catalog skill carries its ``SKILL.md``."""
    for child in _packaged_skills_path().iterdir():
        skill_md = child / "SKILL.md"
        assert skill_md.is_file(), f"vendored skill {child.name} has no SKILL.md"


# ---------------------------------------------------------------------------
# Sync guard: vendored == canonical - denylist, byte-identical, no denied leak
# ---------------------------------------------------------------------------


def test_denied_skills_are_absent_from_the_vendored_catalog() -> None:
    """None of the three denied integrations may leak into the wheel."""
    vendored = _packaged_skills_path()
    for denied in sync_skills.SKILL_DENYLIST:
        assert not (vendored / denied).exists(), (
            f"denied skill {denied!r} leaked into the vendored catalog"
        )


def test_vendored_catalog_matches_canonical_minus_denylist() -> None:
    """Vendored subdirs equal the catalog and are byte-identical to canonical."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; nothing to sync-check")
    vendored = _packaged_skills_path()
    expected = set(sync_skills.catalog_skill_names(canonical))
    present = {child.name for child in vendored.iterdir() if child.is_dir()}
    assert present == expected, (
        "vendored catalog drifted from canonical; regenerate with:\n  "
        f"{sync_skills.SYNC_COMMAND}\n"
        f"unexpected/missing: {sorted(present ^ expected)}"
    )
    for name in expected:
        _assert_dirs_identical(canonical / name, vendored / name)


def test_sync_reports_no_drift_on_the_committed_tree() -> None:
    """The committed vendored tree is already in sync (no forgotten sync)."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; nothing to sync-check")
    result = sync_skills.classify(canonical, _packaged_skills_path())
    assert not result.changed, (
        "vendored catalog is out of sync with canonical; regenerate with:\n  "
        f"{sync_skills.SYNC_COMMAND}\n"
        f"added={result.added} updated={result.updated} removed={result.removed}"
    )


# ---------------------------------------------------------------------------
# Sync mechanism: wholesale regenerate + idempotent no-op
# ---------------------------------------------------------------------------


def test_sync_regenerates_catalog_and_is_idempotent(tmp_path: Path) -> None:
    """Sync materialises the whole catalog; re-running is a no-op."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; nothing to sync")
    vendored = tmp_path / "skills"
    first = sync_skills.sync(canonical, vendored)
    expected = set(sync_skills.catalog_skill_names(canonical))
    assert set(first.added) == expected
    assert not first.removed and not first.updated
    present = {child.name for child in vendored.iterdir() if child.is_dir()}
    assert present == expected
    for name in expected:
        _assert_dirs_identical(canonical / name, vendored / name)

    second = sync_skills.sync(canonical, vendored)
    assert not second.changed, "a second sync should be a no-op"


def test_sync_prunes_denied_and_stale_skills(tmp_path: Path) -> None:
    """A leaked/denied or removed skill is pruned by a wholesale sync."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; nothing to sync")
    vendored = tmp_path / "skills"
    vendored.mkdir()
    # A denied integration and a stale/deleted skill wrongly sitting in vendored.
    (vendored / "microsoft-docs").mkdir()
    (vendored / "microsoft-docs" / "SKILL.md").write_text("stale", encoding="utf-8")
    (vendored / "deleted-skill").mkdir()
    (vendored / "deleted-skill" / "SKILL.md").write_text("stale", encoding="utf-8")

    result = sync_skills.sync(canonical, vendored)
    assert "microsoft-docs" in result.removed
    assert "deleted-skill" in result.removed
    assert not (vendored / "microsoft-docs").exists()
    assert not (vendored / "deleted-skill").exists()


# ---------------------------------------------------------------------------
# Wheel packaging: every catalog skill ships as git_loopy/skills/... in the wheel
# ---------------------------------------------------------------------------


def test_every_catalog_skill_is_packaged_into_the_built_wheel(tmp_path: Path) -> None:
    """Hatchling packages every vendored catalog skill (verified in-artifact)."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; cannot enumerate the catalog")
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is the repo toolchain
        pytest.skip("uv not available to build the wheel")
    # <site>/git_loopy/__init__.py -> .../git-loopy/python/git_loopy -> .../git-loopy/python
    package_dir = Path(git_loopy.__file__).resolve().parent.parent
    if not (package_dir / "pyproject.toml").is_file():  # pragma: no cover
        pytest.skip("git-loopy is not a source checkout; cannot build the wheel")
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
        names = set(zf.namelist())
    missing = [
        f"git_loopy/skills/{name}/SKILL.md"
        for name in sync_skills.catalog_skill_names(canonical)
        if f"git_loopy/skills/{name}/SKILL.md" not in names
    ]
    assert not missing, f"catalog skills missing from the built wheel: {missing}"
    # And no denied integration slipped into the artifact.
    for denied in sync_skills.SKILL_DENYLIST:
        assert f"git_loopy/skills/{denied}/SKILL.md" not in names, (
            f"denied skill {denied!r} was packaged into the wheel"
        )
