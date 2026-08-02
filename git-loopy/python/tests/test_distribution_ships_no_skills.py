"""A git-loopy distribution ships no Skills (#338).

The workflow catalog is not git-loopy's to redistribute: it is an external
repository with its own history and licence, pinned by ``skill_source.json``
(ADR-0023) and *installed* into git-loopy's config scope at setup and at the
start of every Run (ADR-0025).

That inversion is only real if the artifact says so, which is what these guards
hold:

* **Nothing vendored.** No built distribution carries a Skill, a catalog
  directory, or a redistribution of anyone else's licence — so no release can
  quietly go back to shipping a stale copy.
* **The pin still travels.** The one thing a distribution *must* carry is the
  pin, because that is what an install resolves against.
* **A Run resolves from the installed catalog alone.** The Skill root handed to
  a session is the installed one; neither the consumer project's
  ``.copilot/skills/`` nor the operator's ``~/.copilot/skills/`` is offered.
* **The notice matches the distribution.** With nothing redistributed, the
  third-party notice's job is to name the external source of record an operator
  installs from, not to reproduce a licence for files that are not there.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import git_loopy
from git_loopy import session as session_module
from git_loopy import skill_install
from git_loopy.skill_source import read_skill_source_pin

_THIRD_PARTY_LICENSES_FILENAME = "THIRD_PARTY_LICENSES.txt"


def _package_root() -> Path:
    return Path(git_loopy.__file__).resolve().parent


# ---------------------------------------------------------------------------
# Nothing is vendored
# ---------------------------------------------------------------------------


def test_the_package_carries_no_skill_catalog() -> None:
    """git-loopy ships no Skills, so there is no catalog directory to go stale."""
    assert not (_package_root() / "skills").exists(), (
        "git_loopy/skills/ is back; the Skill catalog is installed from the "
        "pinned external repository, not vendored (ADR-0025)"
    )


def test_no_vendoring_script_survives() -> None:
    """A sync script would only exist to maintain a tree that no longer exists."""
    scripts = _package_root().parent / "scripts"
    if not scripts.is_dir():
        return
    assert not (scripts / "sync_skills.py").exists()


def test_the_pin_still_travels_with_the_package() -> None:
    """The pin is what an install resolves against, so it has to ship."""
    pin_path = _package_root() / "skill_source.json"
    assert pin_path.is_file()

    pin = read_skill_source_pin(pin_path)

    assert pin.repository
    assert pin.url.startswith("https://")
    assert len(pin.revision) == 40


# ---------------------------------------------------------------------------
# A Run resolves from the installed catalog alone
# ---------------------------------------------------------------------------


def test_a_session_is_handed_only_the_installed_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pinned catalog is what runs, not whatever a checkout or host carries."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    directories = session_module._skill_directories(str(tmp_path / "project"))

    assert directories == [str(tmp_path / "config" / "git-loopy" / "skills")]
    assert not any(".copilot" in entry for entry in directories)


def test_the_installed_catalog_is_a_neighbour_of_the_global_config(
    tmp_path: Path,
) -> None:
    """One global scope: the catalog sits beside the config that governs it."""
    from git_loopy.settings import global_config_path

    env = {"XDG_CONFIG_HOME": str(tmp_path)}

    assert (
        skill_install.installed_catalog_dir(env).parent
        == global_config_path(env).parent
    )


# ---------------------------------------------------------------------------
# Provenance: the notice names what an operator installs, not what ships
# ---------------------------------------------------------------------------


def test_the_notice_names_the_external_source_of_record() -> None:
    """An operator reading it learns where their Skills come from."""
    notice = _package_root() / _THIRD_PARTY_LICENSES_FILENAME
    assert notice.is_file()
    text = notice.read_text(encoding="utf-8")
    pin = read_skill_source_pin(_package_root() / "skill_source.json")

    assert pin.repository in text
    assert "skill_source.json" in text


def test_the_notice_does_not_claim_to_redistribute_the_catalog() -> None:
    """Nothing is redistributed, so a reproduced catalog licence would mislead."""
    text = (_package_root() / _THIRD_PARTY_LICENSES_FILENAME).read_text(
        encoding="utf-8"
    )

    assert "vendor" not in text.lower()
    assert not (_package_root() / "SKILL_CATALOG_LICENSE.txt").exists()


# ---------------------------------------------------------------------------
# The built artifact
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel once for this module and hand back the artifact path."""
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is the repo toolchain
        pytest.skip("uv not available to build the wheel")
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


def test_the_built_wheel_contains_no_skill(built_wheel: Path) -> None:
    """The guarantee that matters is about the artifact, not the source tree."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())

    skills = sorted(name for name in names if name.startswith("git_loopy/skills/"))
    assert skills == [], f"the wheel ships Skills: {skills}"
    assert not any(name.endswith("SKILL.md") for name in names)


def test_the_built_wheel_carries_the_pin_and_the_notice(built_wheel: Path) -> None:
    """What an install needs, and what tells an operator where it comes from."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
        assert "git_loopy/skill_source.json" in names
        assert f"git_loopy/{_THIRD_PARTY_LICENSES_FILENAME}" in names
        pin = json.loads(zf.read("git_loopy/skill_source.json"))
        notice = zf.read(f"git_loopy/{_THIRD_PARTY_LICENSES_FILENAME}").decode("utf-8")

    assert pin["revision"] == read_skill_source_pin(
        _package_root() / "skill_source.json"
    ).revision
    assert pin["repository"] in notice


def test_the_built_wheel_redistributes_nobody_elses_licence(
    built_wheel: Path,
) -> None:
    """With nothing vendored, a bundled upstream licence would be unexplained."""
    with zipfile.ZipFile(built_wheel) as zf:
        names = [name for name in zf.namelist() if name.startswith("git_loopy/")]

    prefixes = ("license", "licence", "notice", "copying")
    bundled = sorted(
        name
        for name in names
        if Path(name).name.lower().startswith(prefixes)
        and Path(name).name != _THIRD_PARTY_LICENSES_FILENAME
    )
    assert bundled == [], f"the wheel bundles component licences: {bundled}"
