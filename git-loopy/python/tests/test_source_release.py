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


def _write_trust_policy(
    destination: Path, *, distribution_mode: str = "source-only"
) -> None:
    """Seed a scratch tree's trust policy with an explicitly declared mode.

    A scratch scenario declares its own promise rather than inheriting this
    repository's. Copying the live policy verbatim would make every case below
    change meaning the day git-loopy flips modes, which is exactly how the
    source-only cases here would start proving nothing.
    """
    policy = json.loads(
        (REPOSITORY_ROOT / "git-loopy/conformance/release-trust.json").read_text(
            encoding="utf-8"
        )
    )
    policy["distribution_mode"] = distribution_mode
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")


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
    trust_dir = root / "git-loopy/conformance"
    trust_dir.mkdir(parents=True, exist_ok=True)
    _write_trust_policy(trust_dir / "release-trust.json")


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
            f"# git-loopy {authority_version}\n\nAgent-authored release notes.\n",
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


@pytest.mark.parametrize("staged", [False, True], ids=["worktree", "index"])
def test_tagged_publication_refuses_an_uncommitted_distribution_promise(
    tmp_path: Path,
    staged: bool,
) -> None:
    root = _tagged_source_distribution(tmp_path)
    version = RELEASE_FIXTURE["expected_release_version"]
    policy_path = root / "git-loopy/conformance/release-trust.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["distribution_mode"] = "artifact-bearing"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    if staged:
        _git(root, "add", str(policy_path))
    archive = tmp_path / "source.tar"

    with pytest.raises(SourceReleaseError, match="trust policy must be committed"):
        verify_tagged_source_release(
            root,
            f"refs/tags/v{version}",
            archive,
            distribution_mode="artifact-bearing",
        )

    assert not archive.exists()


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
    (root / "git-loopy/conformance").mkdir(parents=True)
    _write_trust_policy(root / "git-loopy/conformance/release-trust.json")
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
    shutil.copytree(
        REPOSITORY_ROOT / "git-loopy/paths",
        root / "git-loopy/paths",
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
    ("entrypoint", "original", "qualified"),
    [
        (
            "git-loopy/shell/lib/orchestrator.sh",
            "printf 'git-loopy %s\\n'",
            "printf 'git-loopy shell %s\\n'",
        ),
        (
            "git-loopy/powershell/GitLoopy.Orchestrator.psm1",
            '"git-loopy $(Get-GitLoopyReleaseVersion)"',
            '"git-loopy powershell $(Get-GitLoopyReleaseVersion)"',
        ),
    ],
)
def test_source_identity_accepts_qualified_non_python_entrypoints(
    tmp_path: Path, entrypoint: str, original: str, qualified: str,
) -> None:
    root = _copy_source_distribution(tmp_path)
    script = root / entrypoint
    content = script.read_text(encoding="utf-8")
    assert original in content
    script.write_text(content.replace(original, qualified, 1), encoding="utf-8")

    verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


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
                f'version = "{RELEASE_FIXTURE["expected_release_version"]}"',
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
        raise AssertionError(f"unhandled source-tree drift case: {case['id']}")

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


def test_source_identity_checks_dormant_versions_independently_of_python(
    tmp_path: Path,
) -> None:
    root = _copy_source_distribution(tmp_path)
    _replace_release_version(root, RELEASE_FIXTURE["expected_release_version"], "1.0.0")

    verify_source_tree_identity(root, "1.0.0")


def _write_native_identity(root: Path, member: str, output: str) -> None:
    if member == "shell":
        entrypoint = root / "git-loopy/shell/git-loopy.sh"
        content = f"printf '%s\\n' '{output}'\n"
    else:
        entrypoint = root / "git-loopy/powershell/git-loopy.ps1"
        content = f"Write-Output '{output}'\n"
    entrypoint.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("member", ["shell", "powershell"])
@pytest.mark.parametrize("qualified", [False, True], ids=["plain", "qualified"])
@pytest.mark.parametrize("field", ["release_version", "release.version"])
def test_source_identity_follows_the_declared_release_authority(
    tmp_path: Path, member: str, qualified: bool, field: str,
) -> None:
    root = _copy_source_distribution(tmp_path)
    manifest_path = root / f"git-loopy/paths/{member}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_authority"] = {
        "file": f"git-loopy/{member}/release.json",
        "field": field,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    authority = root / f"git-loopy/{member}/release.json"
    authority.write_text(
        json.dumps(
            {"release_version": "0.12.0", "consumed_release": None}
            if field == "release_version"
            else {"release": {"version": "0.12.0"}}
        ),
        encoding="utf-8",
    )
    identity = f"git-loopy {member}" if qualified else "git-loopy"
    _write_native_identity(root, member, f"{identity} 0.12.0")

    verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize("qualified", [False, True], ids=["plain", "qualified"])
def test_source_identity_refuses_the_baseline_when_authority_has_advanced(
    tmp_path: Path, qualified: bool,
) -> None:
    root = _copy_source_distribution(tmp_path)
    manifest_path = root / "git-loopy/paths/shell.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_authority"] = {
        "file": "git-loopy/shell/release.json",
        "field": "release_version",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "git-loopy/shell/release.json").write_text(
        '{"release_version":"0.12.0"}', encoding="utf-8",
    )
    identity = "git-loopy shell" if qualified else "git-loopy"
    _write_native_identity(root, "shell", f"{identity} 0.11.2")

    with pytest.raises(SourceReleaseError, match=r"shell --version mismatch.*0\.12\.0"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize(
    "authority",
    [
        None,
        {},
        [],
        "baseline.release_version",
        {"field": "baseline.release_version"},
        {"file": None, "field": "baseline.release_version"},
        {"file": 123, "field": "baseline.release_version"},
        {"file": "", "field": "baseline.release_version"},
        {"file": "release\u0000.json", "field": "release_version"},
        {"file": "git-loopy/paths/shell.json"},
        {"file": "git-loopy/paths/shell.json", "field": None},
        {"file": "git-loopy/paths/shell.json", "field": []},
        {"file": "git-loopy/paths/shell.json", "field": ""},
        {"file": "git-loopy/paths/shell.json", "field": ".baseline.release_version"},
        {"file": "git-loopy/paths/shell.json", "field": "baseline..release_version"},
        {"file": "git-loopy/paths/shell.json", "field": "baseline.release_version."},
        {"file": "git-loopy/paths/shell.json", "field": "baseline.missing"},
    ],
)
def test_source_identity_refuses_invalid_release_authority_declarations(
    tmp_path: Path, authority: object,
) -> None:
    root = _copy_source_distribution(tmp_path)
    manifest_path = root / "git-loopy/paths/shell.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_authority"] = authority
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SourceReleaseError, match="Path manifest"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize(
    "content",
    [
        None,
        b"\xff",
        b"{",
        b"null",
        b"[]",
        b"{}",
        b'{"release_version":null}',
        b'{"release_version":123}',
        b'{"release_version":"v0.11.2"}',
        b'{"release_version":"0.11"}',
        b'{"release_version":"0.11.2\\n"}',
        b'{"release_version":" 0.11.2"}',
    ],
)
def test_source_identity_refuses_missing_or_malformed_authority_files(
    tmp_path: Path, content: bytes | None,
) -> None:
    root = _copy_source_distribution(tmp_path)
    manifest_path = root / "git-loopy/paths/shell.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_authority"] = {
        "file": "git-loopy/shell/release.json",
        "field": "release_version",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if content is not None:
        (root / "git-loopy/shell/release.json").write_bytes(content)

    with pytest.raises(SourceReleaseError, match="Path manifest"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize("escape", ["absolute", "parent", "symlink"])
def test_source_identity_refuses_authority_outside_the_source_tree(
    tmp_path: Path, escape: str,
) -> None:
    root = _copy_source_distribution(tmp_path)
    outside = tmp_path / "release.json"
    outside.write_text('{"release_version":"0.11.2"}', encoding="utf-8")
    file = "../release.json"
    if escape == "absolute":
        file = str(outside)
    elif escape == "symlink":
        file = "git-loopy/shell/release.json"
        (root / file).symlink_to(outside)
    manifest_path = root / "git-loopy/paths/shell.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_authority"] = {"file": file, "field": "release_version"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SourceReleaseError, match="within the source tree"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize(
    "output",
    [
        "git-loopy shell 9.9.9",
        "git-loopy powershell 0.11.2",
        "git-loopy Shell 0.11.2",
        "git-loopy shell 0.11.2 extra",
        "git-loopy shell 0.11.2\nextra",
    ],
)
def test_source_identity_refuses_mismatched_qualified_output(
    tmp_path: Path, output: str,
) -> None:
    root = _copy_source_distribution(tmp_path)
    _write_native_identity(root, "shell", output)

    with pytest.raises(SourceReleaseError, match="shell --version mismatch"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize("identity", ["git-loopy python {release_version}", "git-loopy 9.9.9"])
def test_source_identity_keeps_python_unqualified_and_version_checked(
    tmp_path: Path, identity: str,
) -> None:
    root = _copy_source_distribution(tmp_path)
    cli = root / "git-loopy/python/git_loopy/cli.py"
    content = cli.read_text(encoding="utf-8")
    original = 'print(f"git-loopy {release_version}")'
    assert original in content
    cli.write_text(content.replace(original, f'print(f"{identity}")', 1), encoding="utf-8")

    with pytest.raises(SourceReleaseError, match="python --version mismatch"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


@pytest.mark.parametrize("content", [None, "{}", '{"baseline":{"release_version":123}}'])
def test_source_identity_refuses_incomplete_or_invalid_path_identity(
    tmp_path: Path, content: str | None,
) -> None:
    root = _copy_source_distribution(tmp_path)
    manifest = root / "git-loopy/paths/shell.json"
    if content is None:
        manifest.unlink()
    else:
        manifest.write_text(content, encoding="utf-8")
    with pytest.raises(SourceReleaseError, match="Path manifest"):
        verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


def test_source_identity_still_verifies_pre_split_distributions(tmp_path: Path) -> None:
    root = _copy_source_distribution(tmp_path)
    shutil.rmtree(root / "git-loopy/paths")
    (root / "git-loopy/shell/git-loopy.sh").write_text(
        'source "$(dirname "$0")/lib/release-version.sh"\n'
        'printf "git-loopy %s\\n" "$(git_loopy_read_release_version '
        '"$(dirname "$0")/../../VERSION")"\n',
        encoding="utf-8",
    )
    (root / "git-loopy/powershell/git-loopy.ps1").write_text(
        'Import-Module (Join-Path $PSScriptRoot "GitLoopy.Release.psm1")\n'
        '"git-loopy $(Get-GitLoopyReleaseVersion '
        '-Path (Join-Path $PSScriptRoot "../../VERSION"))"\n',
        encoding="utf-8",
    )
    verify_source_tree_identity(root, RELEASE_FIXTURE["expected_release_version"])


def _tagged_source_distribution(
    tmp_path: Path,
    *,
    drift: bool = False,
    committed_policy: bool = True,
    distribution_mode: str = "source-only",
) -> Path:
    root = _copy_source_distribution(tmp_path)
    policy_path = root / "git-loopy/conformance/release-trust.json"
    if committed_policy:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["distribution_mode"] = distribution_mode
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
    else:
        policy_path.unlink()
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


@pytest.mark.parametrize("distribution_mode", ["source-only", "artifact-bearing"])
def test_tagged_source_archive_is_verified_from_committed_publication_input(
    tmp_path: Path,
    distribution_mode: str,
) -> None:
    root = _tagged_source_distribution(tmp_path, distribution_mode=distribution_mode)
    version = RELEASE_FIXTURE["expected_release_version"]
    archive = tmp_path / "git-loopy-source.tar"

    release = verify_tagged_source_release(
        root,
        f"refs/tags/v{version}",
        archive,
        distribution_mode=distribution_mode,
    )

    assert release.version == version
    assert release.distribution_mode == distribution_mode
    assert archive.is_file()
    assert archive.stat().st_size > 0


def test_a_worktree_policy_cannot_supply_a_promise_missing_from_the_tag(
    tmp_path: Path,
) -> None:
    root = _tagged_source_distribution(tmp_path, committed_policy=False)
    version = RELEASE_FIXTURE["expected_release_version"]
    _write_trust_policy(root / "git-loopy/conformance/release-trust.json")
    archive = tmp_path / "source.tar"

    with pytest.raises(SourceReleaseError, match="trust policy must be committed"):
        verify_tagged_source_release(
            root,
            f"refs/tags/v{version}",
            archive,
            distribution_mode="source-only",
        )

    assert not archive.exists()


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
        "distribution_mode": "source-only",
        "notes_path": "docs/releases/v1.2.3-rc.1.md",
        "prerelease": True,
        "tag": "v1.2.3-rc.1",
        "version": "1.2.3-rc.1",
    }
    assert result.stderr == ""
