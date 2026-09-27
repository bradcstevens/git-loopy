"""Release version authority and metadata validation."""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

import pytest
from packaging.version import Version

import git_loopy.release_version as release_version
from git_loopy.release_version import (
    ReleaseVersionError,
    release_line_from_version,
    validate_repository_release_version,
    write_repository_release_version,
)


CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
REPOSITORY_ROOT = Path(__file__).parents[3]
RELEASE_VERSION_FIXTURE: dict[str, Any] = json.loads(
    (CONFORMANCE_DIR / "release-version.json").read_text(encoding="utf-8")
)


def _write_repository_metadata(
    root: Path,
    authority_version: str,
    *,
    source_version: str | None = None,
    runtime_version: str | None = None,
    package_version: str | None = None,
) -> None:
    source_version = source_version or authority_version
    runtime_version = runtime_version or authority_version
    package_version = package_version or authority_version
    (root / "VERSION").write_text(f"{authority_version}\n", encoding="utf-8")

    package_dir = root / "git-loopy" / "python" / "git_loopy"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        f'__version__ = "{source_version}"\n',
        encoding="utf-8",
    )
    (package_dir / "VERSION").write_text(f"{runtime_version}\n", encoding="utf-8")
    (package_dir.parent / "pyproject.toml").write_text(
        "\n".join(
            (
                "[project]",
                'name = "git-loopy"',
                f'version = "{package_version}"',
                "",
            )
        ),
        encoding="utf-8",
    )


def _run_validator(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "git_loopy.release_version",
            "--repository-root",
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    "case",
    RELEASE_VERSION_FIXTURE["valid_versions"],
    ids=lambda case: case["id"],
)
def test_repository_validator_accepts_semver_release_values(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    _write_repository_metadata(tmp_path, case["value"])

    result = _run_validator(tmp_path)

    assert result.returncode == 0, (
        f"validator rejected {case['value']!r}; stderr={result.stderr!r}"
    )
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "case",
    RELEASE_VERSION_FIXTURE["invalid_versions"],
    ids=lambda case: case["id"],
)
def test_repository_validator_rejects_invalid_release_values(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    _write_repository_metadata(tmp_path, "1.2.3")
    (tmp_path / "VERSION").write_text(case["value"], encoding="utf-8")

    result = _run_validator(tmp_path)

    assert result.returncode != 0, f"validator accepted invalid value {case['value']!r}"
    assert result.stdout == ""
    assert "release version validation failed:" in result.stderr
    assert "Semantic Versioning" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "case",
    RELEASE_VERSION_FIXTURE["invalid_authority_inputs"],
    ids=lambda case: case["id"],
)
def test_repository_validator_fails_explicitly_for_unavailable_authority(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    _write_repository_metadata(tmp_path, "1.2.3")
    authority = tmp_path / "VERSION"
    authority.unlink()

    if case["kind"] == "directory":
        authority.mkdir()
    elif case["kind"] == "invalid_utf8":
        authority.write_bytes(b"\xff")

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "release version validation failed:" in result.stderr
    assert "cannot read Release version authority" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "case",
    RELEASE_VERSION_FIXTURE["metadata_drift_cases"],
    ids=lambda case: case["id"],
)
def test_repository_validator_rejects_metadata_drift(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    _write_repository_metadata(
        tmp_path,
        case["authority_version"],
        source_version=case["source_version"],
        runtime_version=case["runtime_version"],
        package_version=case["package_version"],
    )
    extra_args = (
        ("--publication-version", case["publication_version"])
        if case["drift_input"] == "publication"
        else ()
    )

    result = _run_validator(tmp_path, *extra_args)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "release version validation failed:" in result.stderr
    expected_label = {
        "source": "Python source Release version mismatch",
        "runtime": "Python runtime Release version mismatch",
        "package": "Python package Release version mismatch",
        "publication": "Publication Release version mismatch",
    }[case["drift_input"]]
    assert expected_label in result.stderr
    assert case["authority_version"] in result.stderr
    assert case[f"{case['drift_input']}_version"] in result.stderr


def test_repository_validator_accepts_matching_publication_metadata(
    tmp_path: Path,
) -> None:
    case = RELEASE_VERSION_FIXTURE["matching_metadata"]
    _write_repository_metadata(
        tmp_path,
        case["authority_version"],
        source_version=case["source_version"],
        runtime_version=case["runtime_version"],
        package_version=case["package_version"],
    )

    result = _run_validator(
        tmp_path,
        "--publication-version",
        case["publication_version"],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_repository_release_metadata_matches_root_authority() -> None:
    expected = RELEASE_VERSION_FIXTURE["expected_release_version"]

    result = _run_validator(REPOSITORY_ROOT)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8") == f"{expected}\n"


def test_installed_python_distribution_metadata_matches_release_version() -> None:
    assert (
        Version(distribution_version("git-loopy"))
        == Version(RELEASE_VERSION_FIXTURE["expected_python_distribution_version"])
    )


def test_locked_python_distribution_metadata_matches_release_version() -> None:
    lock = tomllib.loads(
        (REPOSITORY_ROOT / "git-loopy/python/uv.lock").read_text(encoding="utf-8")
    )
    git_loopy_packages = [
        package for package in lock["package"] if package.get("name") == "git-loopy"
    ]

    assert len(git_loopy_packages) == 1
    assert (
        git_loopy_packages[0]["version"]
        == RELEASE_VERSION_FIXTURE["expected_python_distribution_version"]
    )


def test_no_fixture_other_than_release_version_contains_live_release_version() -> None:
    # release-version.json is the single Conformance fixture allowed to name
    # the live Release version (#487). Every other fixture proves a wire form
    # or a decision the members can disagree about; the version is neither,
    # so pinning it anywhere else would make an unrelated Release bump
    # rewrite fixtures the gate has no reason to touch.
    live_version = (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    offenders = [
        fixture_path.name
        for fixture_path in sorted(CONFORMANCE_DIR.glob("*.json"))
        if fixture_path.name != "release-version.json"
        and live_version in fixture_path.read_text(encoding="utf-8")
    ]

    assert offenders == [], (
        f"live Release version {live_version!r} pinned outside "
        f"release-version.json in: {offenders}"
    )


def _write_release_distribution(root: Path, version: str = "1.2.3-alpha.4") -> None:
    _write_repository_metadata(root, version)
    python_version = re.sub(
        r"-(alpha|beta|rc)\.",
        lambda stage: {"alpha": "a", "beta": "b", "rc": "rc"}[stage.group(1)],
        version,
    )
    (root / "git-loopy/python/uv.lock").write_text(
        '\n'.join(
            (
                "[[package]]",
                'name = "git-loopy"',
                f'version = "{python_version}"',
                'source = { editable = "." }',
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/tui").mkdir(parents=True)
    (root / "git-loopy/tui/Cargo.toml").write_text(
        '\n'.join(
            (
                "[package]",
                'name = "git-loopy-tui"',
                f'version = "{version}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/tui/Cargo.lock").write_text(
        '\n'.join(
            (
                "[[package]]",
                'name = "git-loopy-tui"',
                f'version = "{version}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/tui/README.md").write_text(
        f'{{"name": "git-loopy-tui", "version": "{version}"}}\n',
        encoding="utf-8",
    )
    (root / "git-loopy/conformance").mkdir()
    (root / "git-loopy/conformance/release-version.json").write_text(
        json.dumps({
            "expected_release_version": version,
            "expected_python_distribution_version": python_version,
            "fixture": "unchanged",
        }) + "\n",
        encoding="utf-8",
    )
    (root / "git-loopy/conformance/other.json").write_text(
        '{"fixture": "unchanged"}\n',
        encoding="utf-8",
    )


def test_release_writer_advances_all_distribution_copies(tmp_path: Path) -> None:
    _write_release_distribution(tmp_path)
    modes_before = {
        path.relative_to(tmp_path): stat.S_IMODE(path.stat().st_mode)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    write_repository_release_version(tmp_path, "2.3.4-alpha.5")

    assert validate_repository_release_version(
        tmp_path, publication_version="2.3.4-alpha.5"
    ) == "2.3.4-alpha.5"
    assert (tmp_path / "git-loopy/python/uv.lock").read_text(encoding="utf-8") == (
        '[[package]]\nname = "git-loopy"\nversion = "2.3.4a5"\n'
        'source = { editable = "." }\n'
    )
    assert (tmp_path / "git-loopy/tui/Cargo.toml").read_text(encoding="utf-8") == (
        '[package]\nname = "git-loopy-tui"\nversion = "2.3.4-alpha.5"\n'
    )
    assert (tmp_path / "git-loopy/tui/Cargo.lock").read_text(encoding="utf-8") == (
        '[[package]]\nname = "git-loopy-tui"\nversion = "2.3.4-alpha.5"\n'
    )
    assert (tmp_path / "git-loopy/tui/README.md").read_text(encoding="utf-8") == (
        '{"name": "git-loopy-tui", "version": "2.3.4-alpha.5"}\n'
    )
    assert json.loads(
        (tmp_path / "git-loopy/conformance/release-version.json").read_text(
            encoding="utf-8"
        )
    ) == {
        "expected_release_version": "2.3.4-alpha.5",
        "expected_python_distribution_version": "2.3.4a5",
        "fixture": "unchanged",
    }
    assert (tmp_path / "git-loopy/conformance/other.json").read_text(
        encoding="utf-8"
    ) == '{"fixture": "unchanged"}\n'
    assert {
        path.relative_to(tmp_path): stat.S_IMODE(path.stat().st_mode)
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == modes_before


def test_release_writer_promotes_the_live_fixture_with_the_distribution(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path)

    write_repository_release_version(tmp_path, "1.2.3")

    fixture = json.loads(
        (tmp_path / "git-loopy/conformance/release-version.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["expected_release_version"] == "1.2.3"
    assert fixture["expected_python_distribution_version"] == "1.2.3"
    assert fixture["fixture"] == "unchanged"
    assert Path("git-loopy/conformance/release-version.json") in (
        release_version.RELEASE_VERSION_PATHS
    )


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        "{}",
        '{"expected_release_version": 1, '
        '"expected_python_distribution_version": "1.2.3a4"}',
        '{"expected_release_version": "9.0.0", '
        '"expected_python_distribution_version": "1.2.3a4"}',
        '{"expected_release_version": "1.2.3-alpha.4", '
        '"expected_python_distribution_version": "9.0.0"}',
        '{"expected_release_version": 1, '
        '"expected_release_version": "1.2.3-alpha.4", '
        '"expected_python_distribution_version": "1.2.3a4"}',
    ],
)
def test_release_writer_refuses_invalid_fixture_without_touching_distribution(
    tmp_path: Path, content: str,
) -> None:
    _write_release_distribution(tmp_path)
    (tmp_path / "git-loopy/conformance/release-version.json").write_text(
        content, encoding="utf-8"
    )
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ReleaseVersionError, match="Release fixture"):
        write_repository_release_version(tmp_path, "2.3.4-alpha.5")

    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


def test_release_line_reader_continues_a_prerelease_counter_from_its_stable_release() -> None:
    last_stable, release_line = release_line_from_version(
        "1.3.0-alpha.7",
        last_stable_version="1.2.3",
    )

    assert last_stable == "1.2.3"
    assert release_line.target == "1.3.0"
    assert release_line.counter == 7


def test_release_promotion_cli_stabilizes_only_a_matching_closed_milestone(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path, version="1.3.0-rc.1")
    notes = tmp_path / "docs/releases"
    notes.mkdir(parents=True)
    for version, body in (
        ("1.3.0-alpha.7", "Final alpha fragment."),
        ("1.3.0-beta.10", "Tenth beta fragment."),
        ("1.3.0-beta.2", "Second beta fragment."),
        ("1.2.4-alpha.1", "Another target's fragment."),
    ):
        (notes / f"v{version}.md").write_text(
            f"# git-loopy {version}\n\n{body}\n", encoding="utf-8"
        )
    output = tmp_path / "github-output"

    result = _run_validator(
        tmp_path,
        "--promote-milestone",
        "v1.3.0",
        "--milestone-state",
        "CLOSED",
        "--github-output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert validate_repository_release_version(tmp_path) == "1.3.0"
    assert (notes / "v1.3.0.md").read_text(encoding="utf-8") == (
        "# git-loopy 1.3.0\n\n"
        "git-loopy 1.3.0 was promoted from the committed development fragments below.\n\n"
        "## Development fragments\n\n"
        "### 1.3.0-alpha.7\n\n"
        "Final alpha fragment.\n\n"
        "### 1.3.0-beta.2\n\n"
        "Second beta fragment.\n\n"
        "### 1.3.0-beta.10\n\n"
        "Tenth beta fragment.\n\n"
        "### 1.3.0-rc.1\n\n"
        "This development fragment advances the Release line to `1.3.0-rc.1` "
        "on the way to stable `1.3.0`.\n"
    )
    assert output.read_text(encoding="utf-8") == (
        "promoted=true\n"
        "version=1.3.0\n"
        "subject=chore(release): promote Release line to 1.3.0\n"
        "notes_path=docs/releases/v1.3.0.md\n"
        "fragment_path=docs/releases/v1.3.0-rc.1.md\n"
    )


def test_release_stage_cli_moves_a_line_forward_and_restarts_its_counter(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path, version="1.3.0-alpha.7")
    output = tmp_path / "github-output"

    result = _run_validator(
        tmp_path, "--advance-stage", "beta", "--github-output", str(output)
    )

    assert result.returncode == 0, result.stderr
    assert validate_repository_release_version(tmp_path) == "1.3.0-beta.1"
    assert (tmp_path / "docs/releases/v1.3.0-beta.1.md").read_text(
        encoding="utf-8"
    ) == (
        "# git-loopy 1.3.0-beta.1\n\n"
        "This development fragment advances the Release line to `1.3.0-beta.1` "
        "on the way to stable `1.3.0`.\n"
    )
    assert output.read_text(encoding="utf-8") == (
        "advanced=true\n"
        "version=1.3.0-beta.1\n"
        "subject=chore(release): advance Release line to 1.3.0-beta.1\n"
        "notes_path=docs/releases/v1.3.0-beta.1.md\n"
        "fragment_path=docs/releases/v1.3.0-beta.1.md\n"
    )


@pytest.mark.parametrize(
    ("version", "stage", "reason"),
    [
        ("1.3.0-rc.1", "beta", "cannot move from rc to beta"),
        ("1.3.0", "rc", "is stable and has no prerelease stage"),
    ],
)
def test_release_stage_cli_refuses_without_touching_the_distribution(
    tmp_path: Path, version: str, stage: str, reason: str
) -> None:
    _write_release_distribution(tmp_path, version=version)

    result = _run_validator(tmp_path, "--advance-stage", stage)

    assert result.returncode == 1
    assert reason in result.stderr
    assert validate_repository_release_version(tmp_path) == version
    assert not (tmp_path / "docs/releases").exists()


def test_release_stage_cli_refuses_a_second_trigger(tmp_path: Path) -> None:
    _write_release_distribution(tmp_path, version="1.3.0-alpha.7")

    result = _run_validator(
        tmp_path, "--advance-stage", "rc", "--promote-milestone", "v1.3.0"
    )

    assert result.returncode == 2
    assert validate_repository_release_version(tmp_path) == "1.3.0-alpha.7"


def test_release_promotion_cli_appends_its_decision_to_a_shared_step_output(
    tmp_path: Path,
) -> None:
    """A step's output file is shared, so a Promotion never truncates it."""
    _write_release_distribution(tmp_path, version="1.3.0-alpha.7")
    output = tmp_path / "github-output"
    output.write_text("already=recorded\n", encoding="utf-8")

    result = _run_validator(
        tmp_path,
        "--promote-milestone",
        "v1.3.0",
        "--milestone-state",
        "CLOSED",
        "--github-output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines()[0] == "already=recorded"


def test_release_promotion_cli_outputs_the_synthesized_current_fragment_path(
    tmp_path: Path,
) -> None:
    """A Promotion tells its workflow to stage every fragment it synthesized."""
    _write_release_distribution(tmp_path, version="1.3.0-alpha.7")
    notes = tmp_path / "docs/releases"
    notes.mkdir(parents=True)
    (notes / "v1.3.0-alpha.1.md").write_text(
        "# git-loopy 1.3.0-alpha.1\n\nFirst development fragment.\n",
        encoding="utf-8",
    )
    output = tmp_path / "github-output"

    result = _run_validator(
        tmp_path,
        "--promote-milestone",
        "v1.3.0",
        "--milestone-state",
        "CLOSED",
        "--github-output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert (notes / "v1.3.0-alpha.7.md").is_file()
    assert "fragment_path=docs/releases/v1.3.0-alpha.7.md\n" in output.read_text(
        encoding="utf-8"
    )


def test_release_promotion_cli_preserves_a_human_authored_stable_note(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path, version="1.3.0-alpha.7")
    notes = tmp_path / "docs/releases"
    notes.mkdir(parents=True)
    stable_note = notes / "v1.3.0.md"
    stable_note.write_text(
        "# git-loopy 1.3.0\n\nA human release essay.\n",
        encoding="utf-8",
    )

    result = _run_validator(
        tmp_path,
        "--promote-milestone",
        "v1.3.0",
        "--milestone-state",
        "CLOSED",
    )

    assert result.returncode == 0, result.stderr
    assert stable_note.read_text(encoding="utf-8") == (
        "# git-loopy 1.3.0\n\nA human release essay.\n"
    )


def test_release_promotion_cli_leaves_an_unmatched_milestone_prerelease_untouched(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path, version="1.3.0-alpha.7")
    output = tmp_path / "github-output"

    result = _run_validator(
        tmp_path,
        "--promote-milestone",
        "v1.2.4",
        "--milestone-state",
        "CLOSED",
        "--github-output",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert validate_repository_release_version(tmp_path) == "1.3.0-alpha.7"
    assert output.read_text(encoding="utf-8") == "promoted=false\n"


def test_release_writer_refuses_invalid_semver_without_touching_distribution(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path)
    before = {
        path.relative_to(tmp_path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ReleaseVersionError, match="Semantic Versioning"):
        write_repository_release_version(tmp_path, "2.3")

    after = {
        path.relative_to(tmp_path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_release_writer_restores_every_copy_when_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_release_distribution(tmp_path)
    before = {
        path.relative_to(tmp_path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    replace = release_version.os.replace
    failed = False

    def fail_uv_lock_replacement(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(destination) == tmp_path / "git-loopy/python/uv.lock" and not failed:
            failed = True
            raise OSError("simulated replacement failure")
        replace(source, destination)

    monkeypatch.setattr(release_version.os, "replace", fail_uv_lock_replacement)

    with pytest.raises(ReleaseVersionError, match="all copies were restored"):
        write_repository_release_version(tmp_path, "2.3.4-alpha.5")

    after = {
        path.relative_to(tmp_path): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_release_writer_refuses_unreadable_copy_without_touching_distribution(
    tmp_path: Path,
) -> None:
    _write_release_distribution(tmp_path)
    cargo_lock = tmp_path / "git-loopy/tui/Cargo.lock"
    cargo_lock.unlink()
    cargo_lock.mkdir()
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ReleaseVersionError, match="cannot read Rust lockfile"):
        write_repository_release_version(tmp_path, "2.3.4-alpha.5")

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not list(tmp_path.rglob("*.git-loopy-release"))


def test_release_writer_removes_staged_files_when_staging_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_release_distribution(tmp_path)
    fsync = release_version.os.fsync
    calls = 0

    def fail_second_copy_backup_fsync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated staging failure")
        fsync(file_descriptor)

    monkeypatch.setattr(
        release_version.os, "fsync", fail_second_copy_backup_fsync
    )

    with pytest.raises(ReleaseVersionError, match="cannot stage"):
        write_repository_release_version(tmp_path, "2.3.4-alpha.5")

    assert not list(tmp_path.rglob("*.git-loopy-release"))
