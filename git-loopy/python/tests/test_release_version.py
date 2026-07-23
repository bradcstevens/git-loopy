"""Release version authority and metadata validation."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

import pytest


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
        distribution_version("git-loopy")
        == RELEASE_VERSION_FIXTURE["expected_python_distribution_version"]
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
