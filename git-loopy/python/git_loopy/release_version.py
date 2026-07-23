"""Read and validate the distribution Release version."""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path
from typing import Sequence


_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = re.compile(
    rf"""
    (?:0|[1-9][0-9]*)\.
    (?:0|[1-9][0-9]*)\.
    (?:0|[1-9][0-9]*)
    (?:-{_IDENTIFIER}(?:\.{_IDENTIFIER})*)?
    (?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?
    """,
    re.VERBOSE,
)
_PYTHON_SOURCE_VERSION = Path("git-loopy/python/git_loopy/__init__.py")
_PYTHON_PACKAGE_METADATA = Path("git-loopy/python/pyproject.toml")


class ReleaseVersionError(ValueError):
    """Release metadata is missing, unreadable, or invalid."""


def _read_metadata_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseVersionError(f"cannot read {label} {path}: {exc}") from exc


def _validate_semver(value: str, label: str) -> str:
    if not _SEMVER.fullmatch(value):
        raise ReleaseVersionError(
            f"{label} must contain exactly one Semantic Versioning value"
        )
    return value


def read_release_version(path: Path) -> str:
    """Read one strict Semantic Versioning value from ``path``."""
    content = _read_metadata_text(path, "Release version authority")
    if content.endswith("\r\n"):
        value = content[:-2]
    elif content.endswith("\n"):
        value = content[:-1]
    else:
        value = content

    return _validate_semver(value, f"Release version authority {path}")


def _read_python_source_version(path: Path) -> str:
    source = _read_metadata_text(path, "Python source Release metadata")
    try:
        module = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ReleaseVersionError(
            f"Python source Release metadata {path} is not valid Python: {exc.msg}"
        ) from exc

    assignments: list[ast.expr] = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            assignments.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__version__"
            and node.value is not None
        ):
            assignments.append(node.value)

    if len(assignments) != 1 or not (
        isinstance(assignments[0], ast.Constant)
        and isinstance(assignments[0].value, str)
    ):
        raise ReleaseVersionError(
            f"Python source Release metadata {path} must assign exactly one "
            "literal string to __version__"
        )
    return _validate_semver(
        assignments[0].value,
        f"Python source Release metadata {path}",
    )


def _read_python_package_version(path: Path) -> str:
    metadata = _read_metadata_text(path, "Python package Release metadata")
    try:
        parsed = tomllib.loads(metadata)
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseVersionError(
            f"Python package Release metadata {path} is not valid TOML: {exc}"
        ) from exc

    project = parsed.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str):
        raise ReleaseVersionError(
            f"Python package Release metadata {path} must define "
            "[project].version as a string"
        )
    return _validate_semver(version, f"Python package Release metadata {path}")


def validate_repository_release_version(
    repository_root: Path,
    *,
    publication_version: str | None = None,
) -> str:
    """Validate and return the repository's authoritative Release version."""
    authority = read_release_version(repository_root / "VERSION")
    source_path = repository_root / _PYTHON_SOURCE_VERSION
    source = _read_python_source_version(source_path)
    if source != authority:
        raise ReleaseVersionError(
            "Python source Release version mismatch: "
            f"expected {authority!r} from VERSION, found {source!r} in {source_path}"
        )

    package_path = repository_root / _PYTHON_PACKAGE_METADATA
    package = _read_python_package_version(package_path)
    if package != authority:
        raise ReleaseVersionError(
            "Python package Release version mismatch: "
            f"expected {authority!r} from VERSION, found {package!r} in {package_path}"
        )

    if publication_version is not None:
        publication = _validate_semver(
            publication_version,
            "Publication Release version",
        )
        if publication != authority:
            raise ReleaseVersionError(
                "Publication Release version mismatch: "
                f"expected {authority!r} from VERSION, found {publication!r}"
            )
    return authority


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate git-loopy Release version metadata."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="repository root containing VERSION (default: current directory)",
    )
    parser.add_argument(
        "--publication-version",
        help="optional publication metadata value to compare with VERSION",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repository Release version validator."""
    args = _build_parser().parse_args(argv)
    try:
        validate_repository_release_version(
            args.repository_root,
            publication_version=args.publication_version,
        )
    except ReleaseVersionError as exc:
        print(f"release version validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
