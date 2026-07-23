"""Verify one tag-driven, source-only git-loopy Release."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from git_loopy.release_version import (
    ReleaseVersionError,
    validate_repository_release_version,
)


class SourceReleaseError(ValueError):
    """The tagged source distribution is not safe to publish."""


@dataclass(frozen=True)
class SourceRelease:
    """Verified publication identity and GitHub Release classification."""

    version: str
    tag: str
    commit: str
    prerelease: bool
    notes_path: Path


def _run_git(
    repository_root: Path,
    *arguments: str,
    text: bool = True,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        capture_output=True,
        text=text,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if isinstance(stderr, bytes):
            detail = stderr.decode("utf-8", errors="replace")
        else:
            detail = stderr
        raise SourceReleaseError(
            f"git {' '.join(arguments)} failed: {detail or 'no diagnostic'}"
        )
    return result


def _git_text(repository_root: Path, *arguments: str) -> str:
    result = _run_git(repository_root, *arguments)
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def inspect_release_tag(repository_root: Path, tag_ref: str) -> SourceRelease:
    """Validate one annotated tag at HEAD and return its publication plan."""
    repository_root = repository_root.resolve()
    if not tag_ref.startswith("refs/tags/v") or "/" in tag_ref.removeprefix(
        "refs/tags/"
    ):
        raise SourceReleaseError(
            "publication ref must be exactly refs/tags/v<Release version>"
        )

    tag = tag_ref.removeprefix("refs/tags/")
    if _git_text(repository_root, "cat-file", "-t", tag_ref) != "tag":
        raise SourceReleaseError(f"Release tag {tag} must be annotated")

    commit = _git_text(repository_root, "rev-parse", f"{tag_ref}^{{commit}}")
    head = _git_text(repository_root, "rev-parse", "HEAD")
    if commit != head:
        raise SourceReleaseError(
            f"Release tag {tag} targets {commit}, but publication input is {head}"
        )

    try:
        version = validate_repository_release_version(repository_root)
    except ReleaseVersionError as exc:
        raise SourceReleaseError(str(exc)) from exc

    if tag != f"v{version}":
        raise SourceReleaseError(
            f"Release tag {tag} does not match committed Release version {version}"
        )

    tracked_metadata = (
        "VERSION",
        "git-loopy/python/git_loopy/__init__.py",
        "git-loopy/python/git_loopy/VERSION",
        "git-loopy/python/pyproject.toml",
    )
    changed_metadata = _git_text(
        repository_root,
        "diff",
        "--name-only",
        "HEAD",
        "--",
        *tracked_metadata,
    )
    if changed_metadata:
        raise SourceReleaseError(
            "Release identity must be committed before its tag is created"
        )

    try:
        parent = _git_text(repository_root, "rev-parse", f"{commit}^")
    except SourceReleaseError as exc:
        raise SourceReleaseError(
            "Release commit must carry an explicit Release-version bump"
        ) from exc
    changed = _git_text(
        repository_root,
        "diff",
        "--name-only",
        parent,
        commit,
        "--",
        "VERSION",
    )
    if changed != "VERSION":
        raise SourceReleaseError(
            "Release commit must carry an explicit Release-version bump in VERSION"
        )

    notes_path = Path("docs/releases") / f"{tag}.md"
    notes = repository_root / notes_path
    try:
        notes_text = notes.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SourceReleaseError(
            f"Release {tag} requires committed UTF-8 edited release notes at "
            f"{notes_path}: {exc}"
        ) from exc
    if not notes_text.strip():
        raise SourceReleaseError(
            f"Release {tag} requires non-empty edited release notes at {notes_path}"
        )
    _run_git(repository_root, "cat-file", "-e", f"{commit}:{notes_path.as_posix()}")
    if _git_text(
        repository_root,
        "diff",
        "--name-only",
        "HEAD",
        "--",
        notes_path.as_posix(),
    ):
        raise SourceReleaseError(
            f"edited release notes must be committed before tagging: {notes_path}"
        )

    version_without_build = version.split("+", 1)[0]
    prerelease = "-" in version_without_build
    return SourceRelease(
        version=version,
        tag=tag,
        commit=commit,
        prerelease=prerelease,
        notes_path=notes_path,
    )


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SourceReleaseError(
            f"source Release verification requires {name} on PATH"
        )
    return executable


def _python_entrypoint() -> str:
    for name in ("git-loopy", "git-loopy.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return str(candidate)
    raise SourceReleaseError(
        "source Release verification requires the installed git-loopy console script"
    )


def _run_identity_command(
    family: str,
    surface: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise SourceReleaseError(
            f"{family} {surface} failed with exit {result.returncode}: "
            f"{result.stderr.strip() or 'no diagnostic'}"
        )
    if result.stderr:
        raise SourceReleaseError(
            f"{family} {surface} wrote unexpected stderr: {result.stderr.strip()}"
        )
    return result.stdout


def verify_source_tree_identity(source_root: Path, expected_version: str) -> None:
    """Verify package metadata and every public identity surface in a source tree."""
    source_root = source_root.resolve()
    try:
        version = validate_repository_release_version(
            source_root,
            publication_version=expected_version,
        )
    except ReleaseVersionError as exc:
        raise SourceReleaseError(str(exc)) from exc

    python_path = source_root / "git-loopy/python"
    env = os.environ.copy()
    existing_python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(python_path), existing_python_path)
        if part
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    commands = {
        "python": [_python_entrypoint()],
        "shell": [
            _required_executable("bash"),
            str(source_root / "git-loopy/shell/git-loopy.sh"),
        ],
        "PowerShell": [
            _required_executable("pwsh"),
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(source_root / "git-loopy/powershell/git-loopy.ps1"),
        ],
    }
    expected_output = f"git-loopy {version}\n"
    for family, command in commands.items():
        output = _run_identity_command(
            family,
            "--version",
            [*command, "--version"],
            cwd=source_root,
            env=env,
        )
        if output != expected_output:
            raise SourceReleaseError(
                f"{family} --version mismatch: expected {expected_output!r}, "
                f"found {output!r}"
            )

        manifest_output = _run_identity_command(
            family,
            "Continuation capability manifest",
            [*command, "continuation", "capabilities"],
            cwd=source_root,
            env=env,
        )
        try:
            manifest = json.loads(manifest_output)
            manifest_version = manifest["capabilities"]["release_version"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourceReleaseError(
                f"{family} Continuation capability manifest is invalid"
            ) from exc
        if manifest_version != version:
            raise SourceReleaseError(
                f"{family} Continuation capability manifest mismatch: "
                f"expected {version!r}, found {manifest_version!r}"
            )


def _extract_source_archive(archive_path: Path, destination: Path) -> Path:
    with tarfile.open(archive_path, mode="r:") as archive:
        members = archive.getmembers()
        if not members:
            raise SourceReleaseError("generated source archive is empty")
        for member in members:
            target = (destination / member.name).resolve()
            if (
                destination.resolve() not in target.parents
                and target != destination.resolve()
            ):
                raise SourceReleaseError(
                    f"generated source archive contains unsafe path {member.name!r}"
                )
        archive.extractall(destination, filter="data")

    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise SourceReleaseError(
            "generated source archive must contain one distribution root"
        )
    return roots[0]


def verify_tagged_source_release(
    repository_root: Path,
    tag_ref: str,
    archive_output: Path,
) -> SourceRelease:
    """Generate and verify the exact tagged GitHub source-archive input."""
    release = inspect_release_tag(repository_root, tag_ref)
    archive_output = archive_output.resolve()
    archive_output.parent.mkdir(parents=True, exist_ok=True)
    if archive_output.exists():
        raise SourceReleaseError(f"source archive already exists: {archive_output}")

    _run_git(
        repository_root,
        "archive",
        "--format=tar",
        f"--prefix=git-loopy-{release.version}/",
        f"--output={archive_output}",
        release.commit,
    )
    with tempfile.TemporaryDirectory(prefix="git-loopy-source-release-") as temporary:
        source_root = _extract_source_archive(archive_output, Path(temporary))
        verify_source_tree_identity(source_root, release.version)
    return release


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify one tag-driven git-loopy source Release."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="tagged repository root (default: current directory)",
    )
    parser.add_argument(
        "--tag-ref",
        required=True,
        help="full annotated tag ref, exactly refs/tags/v<Release version>",
    )
    parser.add_argument(
        "--tag-only",
        action="store_true",
        help="verify committed tag identity without generating the source archive",
    )
    parser.add_argument(
        "--archive-output",
        type=Path,
        help="path for the generated source archive",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="optional GitHub Actions output file",
    )
    return parser


def _release_payload(release: SourceRelease) -> dict[str, object]:
    payload = asdict(release)
    payload["notes_path"] = release.notes_path.as_posix()
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Run source-release preflight or complete archive verification."""
    args = _build_parser().parse_args(argv)
    if not args.tag_only and args.archive_output is None:
        print(
            "source Release verification failed: --archive-output is required "
            "unless --tag-only is selected",
            file=sys.stderr,
        )
        return 2

    try:
        if args.tag_only:
            release = inspect_release_tag(args.repository_root, args.tag_ref)
        else:
            release = verify_tagged_source_release(
                args.repository_root,
                args.tag_ref,
                args.archive_output,
            )
    except SourceReleaseError as exc:
        print(f"source Release verification failed: {exc}", file=sys.stderr)
        return 1

    payload = _release_payload(release)
    print(json.dumps(payload, sort_keys=True))
    if args.github_output is not None:
        output_lines = (
            f"version={release.version}\n"
            f"tag={release.tag}\n"
            f"prerelease={str(release.prerelease).lower()}\n"
            f"notes_path={release.notes_path.as_posix()}\n"
        )
        try:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(output_lines)
        except OSError as exc:
            print(
                f"source Release verification failed: cannot write GitHub output: {exc}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
