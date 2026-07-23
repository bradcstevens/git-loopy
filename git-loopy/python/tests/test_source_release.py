"""Fail-closed source-release publication verification."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from git_loopy.source_release import (
    SourceReleaseError,
    inspect_release_tag,
    verify_source_tree_identity,
    verify_tagged_source_release,
)


REPOSITORY_ROOT = Path(__file__).parents[3]
RELEASE_FIXTURE: dict[str, Any] = json.loads(
    (
        REPOSITORY_ROOT / "git-loopy/conformance/release-version.json"
    ).read_text(encoding="utf-8")
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write_release_metadata(root: Path, version: str) -> None:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    package = root / "git-loopy/python/git_loopy"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (package / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (package.parent / "pyproject.toml").write_text(
        f'[project]\nname = "git-loopy"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def _tagged_repository(
    tmp_path: Path,
    *,
    authority_version: str,
    tag_version: str | None = None,
    annotated: bool = True,
    bumped: bool = True,
    notes: bool = True,
) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release-test@example.invalid")

    parent_version = "1.2.2" if bumped else authority_version
    _write_release_metadata(root, parent_version)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "prior release identity")

    _write_release_metadata(root, authority_version)
    if notes:
        notes_path = root / "docs/releases" / f"v{authority_version}.md"
        notes_path.parent.mkdir(parents=True)
        notes_path.write_text(
            f"# git-loopy {authority_version}\n\nEdited release notes.\n",
            encoding="utf-8",
        )
    else:
        (root / "release-change.txt").write_text("release\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "prepare source release")

    tag = f"v{tag_version or authority_version}"
    tag_args = ("tag", "-am", f"git-loopy {tag}", tag) if annotated else ("tag", tag)
    _git(root, *tag_args)
    return root


@pytest.mark.parametrize(
    "case",
    RELEASE_FIXTURE["publication_cases"],
    ids=lambda case: case["id"],
)
def test_annotated_tag_classifies_source_release(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    root = _tagged_repository(tmp_path, authority_version=case["version"])

    release = inspect_release_tag(root, f"refs/tags/v{case['version']}")

    assert release.version == case["version"]
    assert release.tag == f"v{case['version']}"
    assert release.prerelease is case["prerelease"]
    assert release.notes_path == Path(f"docs/releases/v{case['version']}.md")
    assert release.commit == _git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "case",
    RELEASE_FIXTURE["invalid_tag_cases"],
    ids=lambda case: case["id"],
)
def test_release_tag_rejects_invalid_publication_identity(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    root = _tagged_repository(
        tmp_path,
        authority_version=case["authority_version"],
        tag_version=case["tag_version"],
        annotated=case["annotated"],
        bumped=case["bumped"],
        notes=case["notes"],
    )

    with pytest.raises(SourceReleaseError, match=case["error"]):
        inspect_release_tag(root, f"refs/tags/v{case['tag_version']}")


def _copy_source_distribution(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "git-loopy/python").mkdir(parents=True)
    shutil.copy2(REPOSITORY_ROOT / "VERSION", root / "VERSION")
    shutil.copy2(
        REPOSITORY_ROOT / "git-loopy/python/pyproject.toml",
        root / "git-loopy/python/pyproject.toml",
    )
    shutil.copytree(
        REPOSITORY_ROOT / "git-loopy/python/git_loopy",
        root / "git-loopy/python/git_loopy",
    )
    shutil.copytree(
        REPOSITORY_ROOT / "git-loopy/shell",
        root / "git-loopy/shell",
    )
    shutil.copytree(
        REPOSITORY_ROOT / "git-loopy/powershell",
        root / "git-loopy/powershell",
    )
    return root


def test_source_tree_identity_uses_all_real_orchestrator_entrypoints(
    tmp_path: Path,
) -> None:
    root = _copy_source_distribution(tmp_path)

    verify_source_tree_identity(
        root,
        RELEASE_FIXTURE["expected_release_version"],
    )


@pytest.mark.parametrize(
    "case",
    RELEASE_FIXTURE["source_tree_drift_cases"],
    ids=lambda case: case["id"],
)
def test_source_tree_identity_rejects_distribution_drift(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    root = _copy_source_distribution(tmp_path)
    if case["id"] == "package":
        package = root / "git-loopy/python/pyproject.toml"
        package.write_text(
            package.read_text(encoding="utf-8").replace(
                'version = "0.1.0"',
                'version = "9.9.9"',
                1,
            ),
            encoding="utf-8",
        )
    elif case["id"] == "source-entrypoint":
        orchestrator = root / "git-loopy/shell/lib/orchestrator.sh"
        orchestrator.write_text(
            orchestrator.read_text(encoding="utf-8").replace(
                "printf 'git-loopy %s\\n' \"$release_version\"",
                "printf 'git-loopy 9.9.9\\n'",
                1,
            ),
            encoding="utf-8",
        )
    else:
        continuation = root / "git-loopy/shell/lib/continuation.sh"
        continuation.write_text(
            continuation.read_text(encoding="utf-8").replace(
                '    "$release_version" \\\n',
                '    "9.9.9" \\\n',
                1,
            ),
            encoding="utf-8",
        )

    with pytest.raises(SourceReleaseError, match=case["expected_error"]):
        verify_source_tree_identity(
            root,
            RELEASE_FIXTURE["expected_release_version"],
        )


def _replace_release_version(root: Path, old: str, new: str) -> None:
    replacements = (
        root / "VERSION",
        root / "git-loopy/python/git_loopy/__init__.py",
        root / "git-loopy/python/git_loopy/VERSION",
        root / "git-loopy/python/pyproject.toml",
    )
    for path in replacements:
        path.write_text(
            path.read_text(encoding="utf-8").replace(old, new),
            encoding="utf-8",
        )


def _tagged_source_distribution(tmp_path: Path, *, drift: bool = False) -> Path:
    root = _copy_source_distribution(tmp_path)
    version = RELEASE_FIXTURE["expected_release_version"]
    previous = "0.0.0"
    _replace_release_version(root, version, previous)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release-test@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "prior release identity")

    _replace_release_version(root, previous, version)
    if drift:
        orchestrator = root / "git-loopy/shell/lib/orchestrator.sh"
        orchestrator.write_text(
            orchestrator.read_text(encoding="utf-8").replace(
                "printf 'git-loopy %s\\n' \"$release_version\"",
                "printf 'git-loopy 9.9.9\\n'",
                1,
            ),
            encoding="utf-8",
        )
    notes = root / "docs/releases" / f"v{version}.md"
    notes.parent.mkdir(parents=True)
    notes.write_text(f"# git-loopy {version}\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "prepare source release")
    _git(root, "tag", "-am", f"git-loopy v{version}", f"v{version}")
    return root


def test_tagged_source_archive_is_verified_from_committed_publication_input(
    tmp_path: Path,
) -> None:
    root = _tagged_source_distribution(tmp_path)
    version = RELEASE_FIXTURE["expected_release_version"]
    archive = tmp_path / "git-loopy-source.tar"

    release = verify_tagged_source_release(
        root,
        f"refs/tags/v{version}",
        archive,
    )

    assert release.version == version
    assert archive.is_file()
    assert archive.stat().st_size > 0


@pytest.mark.parametrize(
    "case",
    RELEASE_FIXTURE["artifact_drift_cases"],
    ids=lambda case: case["id"],
)
def test_tagged_source_archive_rejects_artifact_identity_drift(
    tmp_path: Path,
    case: dict[str, str],
) -> None:
    root = _tagged_source_distribution(tmp_path, drift=True)
    version = RELEASE_FIXTURE["expected_release_version"]

    with pytest.raises(SourceReleaseError, match=case["expected_error"]):
        verify_tagged_source_release(
            root,
            f"refs/tags/v{version}",
            tmp_path / "drifted-source.tar",
        )


def test_tag_preflight_cli_emits_machine_readable_release_plan(
    tmp_path: Path,
) -> None:
    root = _tagged_repository(tmp_path, authority_version="1.2.3-rc.1")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "git_loopy.source_release",
            "--repository-root",
            str(root),
            "--tag-ref",
            "refs/tags/v1.2.3-rc.1",
            "--tag-only",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "commit": _git(root, "rev-parse", "HEAD"),
        "notes_path": "docs/releases/v1.2.3-rc.1.md",
        "prerelease": True,
        "tag": "v1.2.3-rc.1",
        "version": "1.2.3-rc.1",
    }
    assert result.stderr == ""
