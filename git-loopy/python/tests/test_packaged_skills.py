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

Because the catalog is vendored from Matt Pocock's MIT-licensed skills
(``mattpocock/skills``), redistributing it in the wheel means the upstream
copyright + permission notice must travel *inside* the package too (#124). The
final guards below assert ``git_loopy/THIRD_PARTY_LICENSES.txt`` carries the
verbatim upstream MIT notice and actually ships in the built wheel.
"""

from __future__ import annotations

import filecmp
import importlib.util
import shutil
import subprocess
import zipfile
from importlib.resources import files
from pathlib import Path

import pytest

import git_loopy
from git_loopy import init as init_module
from git_loopy.prompt import parse_required_skills

# ---------------------------------------------------------------------------
# The upstream MIT notice, reproduced verbatim from ``mattpocock/skills`` (the
# source of the vendored workflow skill catalog). Kept here as the guard's pin
# so a truncated, altered, or empty THIRD_PARTY_LICENSES.txt fails the guard.
# ---------------------------------------------------------------------------

_THIRD_PARTY_LICENSES_FILENAME = "THIRD_PARTY_LICENSES.txt"

_UPSTREAM_MIT_NOTICE = """\
MIT License

Copyright (c) 2026 Matt Pocock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def _packaged_third_party_licenses_path() -> Path:
    """The third-party licence notice shipped inside the wheel as package data."""
    return Path(str(files("git_loopy") / _THIRD_PARTY_LICENSES_FILENAME))


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
# Third-party licence notice: the vendored catalog is MIT-licensed upstream work
# ---------------------------------------------------------------------------


def test_third_party_licenses_notice_carries_the_verbatim_upstream_mit_notice() -> None:
    """The packaged notice reproduces the upstream MIT notice verbatim (#124)."""
    notice = _packaged_third_party_licenses_path()
    assert notice.is_file(), (
        f"{_THIRD_PARTY_LICENSES_FILENAME} is missing from the git_loopy package"
    )
    text = notice.read_text(encoding="utf-8")
    assert _UPSTREAM_MIT_NOTICE in text, (
        "THIRD_PARTY_LICENSES.txt does not carry the verbatim upstream MIT notice"
    )
    # The attribution names the upstream repository it was vendored from.
    assert "mattpocock/skills" in text


def _bundled_license_files() -> list[Path]:
    """Every LICENSE/NOTICE/COPYING file bundled inside the vendored catalog."""
    prefixes = ("license", "licence", "notice", "copying")
    return [
        path
        for path in _packaged_skills_path().rglob("*")
        if path.is_file() and path.name.lower().startswith(prefixes)
    ]


def _skills_bundling_their_own_license() -> set[str]:
    """Vendored skills that ship their own LICENSE/NOTICE/COPYING file."""
    root = _packaged_skills_path()
    return {path.relative_to(root).parts[0] for path in _bundled_license_files()}


def test_bundled_component_licenses_are_acknowledged() -> None:
    """Every skill shipping its own license is named.

    The aggregate MIT notice does not cover a component under a different license,
    so any vendored skill that carries its own LICENSE/NOTICE must be acknowledged
    by name in THIRD_PARTY_LICENSES.txt. This catches a newly vendored,
    differently-licensed skill drifting in unaccounted for (#124).
    """
    text = _packaged_third_party_licenses_path().read_text(encoding="utf-8")
    bundling = _skills_bundling_their_own_license()
    unacknowledged = sorted(name for name in bundling if name not in text)
    assert not unacknowledged, (
        "vendored skills ship their own LICENSE/NOTICE but are not acknowledged in "
        f"{_THIRD_PARTY_LICENSES_FILENAME}: {unacknowledged}"
    )


# ---------------------------------------------------------------------------
# Wheel packaging: every catalog skill + the MIT notice ships inside the wheel
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel once for this module and hand back the artifact path.

    The wheel build is the slowest thing in this module, so a single build is
    shared by every guard that inspects the packaged artifact.
    """
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is the repo toolchain
        pytest.skip("uv not available to build the wheel")
    # <site>/git_loopy/__init__.py -> .../git-loopy/python/git_loopy -> .../git-loopy/python
    package_dir = Path(git_loopy.__file__).resolve().parent.parent
    if not (package_dir / "pyproject.toml").is_file():  # pragma: no cover
        pytest.skip("git-loopy is not a source checkout; cannot build the wheel")
    out = tmp_path_factory.mktemp("dist")
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
    return wheels[0]


def test_every_catalog_skill_is_packaged_into_the_built_wheel(
    built_wheel: Path,
) -> None:
    """Hatchling packages every vendored catalog skill (verified in-artifact)."""
    canonical = _canonical_skills_dir()
    if canonical is None:  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; cannot enumerate the catalog")
    with zipfile.ZipFile(built_wheel) as zf:
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


def test_wheel_prompt_required_skills_exist_in_wheel_catalog(
    built_wheel: Path,
) -> None:
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
        prompt = zf.read("git_loopy/PROMPT.md").decode("utf-8")

    required_skills = parse_required_skills(prompt)

    assert required_skills is not None
    missing = [
        name
        for name in required_skills
        if f"git_loopy/skills/{name}/SKILL.md" not in names
    ]
    assert missing == []


def test_third_party_licenses_notice_is_packaged_into_the_built_wheel(
    built_wheel: Path,
) -> None:
    """The MIT attribution notice actually ships inside the built wheel (#124)."""
    arcname = f"git_loopy/{_THIRD_PARTY_LICENSES_FILENAME}"
    with zipfile.ZipFile(built_wheel) as zf:
        assert arcname in set(zf.namelist()), (
            f"{arcname} is missing from the built wheel"
        )
        packaged = zf.read(arcname)
    # The packaged copy is byte-identical to the committed source notice ...
    assert packaged == _packaged_third_party_licenses_path().read_bytes()
    # ... and carries the verbatim upstream MIT notice + its attribution.
    text = packaged.decode("utf-8")
    assert _UPSTREAM_MIT_NOTICE in text
    assert "mattpocock/skills" in text


def test_bundled_component_licenses_ship_in_the_built_wheel(built_wheel: Path) -> None:
    """Any bundled component-license files actually ship (#124).

    When a differently licensed component carries its own license in the catalog,
    that file must travel in the wheel or the attribution reference dangles.
    """
    root = _packaged_skills_path()
    expected = {
        f"git_loopy/skills/{path.relative_to(root).as_posix()}"
        for path in _bundled_license_files()
    }
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
    missing = sorted(arc for arc in expected if arc not in names)
    assert not missing, f"bundled component licenses missing from the wheel: {missing}"
