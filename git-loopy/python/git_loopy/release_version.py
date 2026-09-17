"""Read and validate the distribution Release version."""

from __future__ import annotations

import argparse
import ast
import os
import re
import stat
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from enum import Enum
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
_PYTHON_RUNTIME_VERSION = Path("git-loopy/python/git_loopy/VERSION")
_PYTHON_LOCKFILE = Path("git-loopy/python/uv.lock")
_RUST_MANIFEST = Path("git-loopy/tui/Cargo.toml")
_RUST_LOCKFILE = Path("git-loopy/tui/Cargo.lock")
_TUI_PROBE = Path("git-loopy/tui/README.md")
_RELEASE_NOTES_DIRECTORY = Path("docs/releases")
RELEASE_VERSION_PATHS: tuple[Path, ...] = (
    Path("VERSION"),
    _PYTHON_SOURCE_VERSION,
    _PYTHON_RUNTIME_VERSION,
    _PYTHON_PACKAGE_METADATA,
    _PYTHON_LOCKFILE,
    _RUST_MANIFEST,
    _RUST_LOCKFILE,
    _TUI_PROBE,
)
BUMP_CLASS_LABEL_PREFIX = "semver:"
BUMP_CLASS_KEYS: tuple[str, ...] = ("major", "minor", "patch", "none")


class ReleaseVersionError(ValueError):
    """Release metadata is missing, unreadable, or invalid."""


class BumpClassRefusal(Enum):
    """Why issue labels cannot resolve to one Release-version bump class."""

    UNCLASSIFIED = "unclassified_bump_class"
    UNKNOWN_KEY = "unknown_semver_key"
    CONFLICTING_LABELS = "conflicting_semver_labels"


class BumpClassError(ReleaseVersionError):
    """An issue's ``semver:`` labels are absent, unknown, or conflicting."""

    def __init__(
        self,
        reason: BumpClassRefusal,
        *,
        key: str | None = None,
        keys: Sequence[str] = (),
    ) -> None:
        self.reason = reason
        self.key = key
        self.keys = tuple(keys)
        if reason is BumpClassRefusal.UNCLASSIFIED:
            message = "issue is unclassified: it carries no semver: label"
        elif reason is BumpClassRefusal.UNKNOWN_KEY:
            message = (
                f"unknown semver: key {key!r}; permitted keys: "
                f"{', '.join(BUMP_CLASS_KEYS)}"
            )
        else:
            labels = ", ".join(f"{BUMP_CLASS_LABEL_PREFIX}{value}" for value in keys)
            message = f"issue carries conflicting semver: labels {labels}"
        super().__init__(message)


@dataclass(frozen=True)
class ReleaseLine:
    """The Release target and prerelease counter after one issue closes."""

    target: str
    counter: int

    @property
    def version(self) -> str:
        """Return the target's current prerelease, or the target before any bump."""
        return f"{self.target}-dev.{self.counter}" if self.counter else self.target


@dataclass(frozen=True)
class _ReleaseVersionUpdate:
    path: Path
    content: str


@dataclass(frozen=True)
class _ReleaseNoteSnapshot:
    path: Path
    content: str | None


@dataclass(frozen=True)
class ReleaseNotesWrite:
    """One Release-note update and the paths its Release commit must carry."""

    commit_paths: tuple[Path, ...]
    snapshots: tuple[_ReleaseNoteSnapshot, ...]


def resolve_bump_class(labels: Sequence[str]) -> str:
    """Return an issue's one closed ``semver:`` bump class.

    A missing label is an unclassified fault, deliberately distinct from
    ``semver:none``. Unknown and multiple ``semver:`` labels are refused before
    returning a decision, so callers never silently choose a Release impact.
    """
    keys = tuple(
        label[len(BUMP_CLASS_LABEL_PREFIX) :]
        for label in labels
        if label.startswith(BUMP_CLASS_LABEL_PREFIX)
    )
    unknown = next((key for key in keys if key not in BUMP_CLASS_KEYS), None)
    if unknown is not None:
        raise BumpClassError(BumpClassRefusal.UNKNOWN_KEY, key=unknown)
    if not keys:
        raise BumpClassError(BumpClassRefusal.UNCLASSIFIED)
    if len(keys) != 1:
        raise BumpClassError(BumpClassRefusal.CONFLICTING_LABELS, keys=keys)
    return keys[0]


def advance_release_line(
    last_stable_version: str,
    current_target: str,
    current_counter: int,
    bump_class: str,
) -> ReleaseLine:
    """Apply one closed issue to the Release target ratchet and `dev.N` counter."""
    if bump_class not in BUMP_CLASS_KEYS:
        raise ReleaseVersionError(f"unknown Release-line bump class {bump_class!r}")
    if (
        not isinstance(current_counter, int)
        or isinstance(current_counter, bool)
        or current_counter < 0
    ):
        raise ReleaseVersionError("Release-line counter must be a non-negative integer")

    stable = _release_target_parts(last_stable_version, "Last stable Release version")
    current = _release_target_parts(current_target, "Release target")
    major, minor, patch = stable
    candidate = {
        "major": (major + 1, 0, 0),
        "minor": (major, minor + 1, 0),
        "patch": (major, minor, patch + 1),
        "none": stable,
    }[bump_class]
    target = ".".join(str(part) for part in max(current, candidate))
    counter = current_counter + (bump_class != "none")
    return ReleaseLine(target=target, counter=counter)


def promote_release_line(release_line: ReleaseLine, bump_class: str) -> ReleaseLine:
    """Cut a stable Release from the one Bump class exempt from a milestone.

    A `major` publishes on the label alone; every other class stays on its
    `dev.N` line until the `vX.Y.Z` milestone it promised closes
    ([ADR-0052](../../../docs/adr/0052-the-release-line-advances-per-issue.md)).
    Callers ask this rather than testing the class themselves, so *which* class
    is exempt is one decision rather than one per call site.
    """
    if bump_class not in BUMP_CLASS_KEYS:
        raise ReleaseVersionError(f"unknown Release-line bump class {bump_class!r}")
    if bump_class != "major":
        return release_line
    return ReleaseLine(target=release_line.target, counter=0)


def release_line_commit_subject(version: str) -> str:
    """Word the commit that moves the Release line to ``version``.

    A **Promotion** and an advance are different events in the domain, so they
    read differently in `git log`: only a stable value was cut from its line.
    """
    verb = "advance" if is_prerelease(version) else "promote"
    return f"chore(release): {verb} Release line to {version}"


def promote_closed_milestone(
    current_version: str, milestone_title: str, milestone_state: str
) -> str | None:
    """Return the stable Release a matching closed milestone promotes, if any.

    Deliberately separate from :func:`advance_release_line`: a prerelease takes
    no milestone input at any point, while a milestone-close event may promote
    only the exact `dev.N` target its own title names.
    """
    match = re.fullmatch(r"(\d+\.\d+\.\d+)-dev\.(0|[1-9][0-9]*)", current_version)
    if match is None or milestone_state.casefold() != "closed":
        return None
    target = match.group(1)
    return target if milestone_title == f"v{target}" else None


def release_line_from_version(
    version: str, *, last_stable_version: str | None = None
) -> tuple[str, ReleaseLine]:
    """Resolve persisted Release metadata into its stable base and current line."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-dev\.(0|[1-9][0-9]*))?", version)
    if match is None:
        raise ReleaseVersionError(
            "Release line must be a stable or -dev.N Semantic Versioning value"
        )
    target, counter_text = match.groups()
    _release_target_parts(target, "Release target")
    if counter_text is None:
        return target, ReleaseLine(target=target, counter=0)
    if last_stable_version is None:
        raise ReleaseVersionError(
            "a prerelease Release line requires its last stable Release version"
        )
    stable = _release_target_parts(last_stable_version, "Last stable Release version")
    if tuple(int(part) for part in target.split(".")) < stable:
        raise ReleaseVersionError(
            "Release target cannot precede its last stable Release version"
        )
    return last_stable_version, ReleaseLine(target=target, counter=int(counter_text))


def _release_target_parts(value: str, label: str) -> tuple[int, int, int]:
    _validate_semver(value, label)
    if "-" in value or "+" in value:
        raise ReleaseVersionError(
            f"{label} must be a stable major.minor.patch Semantic Versioning value"
        )
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


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


def _read_release_value(path: Path, label: str) -> str:
    content = _read_metadata_text(path, label)
    if content.endswith("\r\n"):
        value = content[:-2]
    elif content.endswith("\n"):
        value = content[:-1]
    else:
        value = content

    return _validate_semver(value, f"{label} {path}")


def read_release_version(path: Path) -> str:
    """Read one strict Semantic Versioning value from ``path``."""
    return _read_release_value(path, "Release version authority")


def is_prerelease(version: str) -> bool:
    """Whether ``version`` publishes as a prerelease rather than a stable Release.

    One rule, read by everything that has an opinion about a Release's channel.
    A second copy is how a Release ends up marked prerelease on GitHub while a
    signing gate holds it to stable requirements — or, far worse, the reverse.
    """
    return "-" in version.split("+", 1)[0]


def read_runtime_release_version(path: Path | None = None) -> str:
    """Read the exact Release version shipped with the Python distribution."""
    runtime_path = path or Path(__file__).with_name("VERSION")
    return _read_release_value(runtime_path, "Python runtime Release metadata")


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

    runtime_path = repository_root / _PYTHON_RUNTIME_VERSION
    runtime = read_runtime_release_version(runtime_path)
    if runtime != authority:
        raise ReleaseVersionError(
            "Python runtime Release version mismatch: "
            f"expected {authority!r} from VERSION, found {runtime!r} in {runtime_path}"
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


def write_repository_release_version(repository_root: Path, version: str) -> None:
    """Atomically advance every checked-in Release-version copy to ``version``.

    The writer only accepts stable and ``-dev.N`` Release-line values because
    those are the forms whose Python package metadata has an unambiguous PEP
    440 representation. All targets are read and transformed before replacement
    begins, and each original is staged as a rollback file before its target
    changes.
    """
    _validate_semver(version, "Release version")
    python_version = _python_distribution_version(version)
    authority = validate_repository_release_version(repository_root)
    updates = (
        _ReleaseVersionUpdate(repository_root / "VERSION", f"{version}\n"),
        _ReleaseVersionUpdate(
            repository_root / _PYTHON_SOURCE_VERSION,
            _replace_python_source_version(
                _read_metadata_text(
                    repository_root / _PYTHON_SOURCE_VERSION,
                    "Python source Release metadata",
                ),
                authority,
                version,
                repository_root / _PYTHON_SOURCE_VERSION,
            ),
        ),
        _ReleaseVersionUpdate(repository_root / _PYTHON_RUNTIME_VERSION, f"{version}\n"),
        _ReleaseVersionUpdate(
            repository_root / _PYTHON_PACKAGE_METADATA,
            _replace_project_version(
                _read_metadata_text(
                    repository_root / _PYTHON_PACKAGE_METADATA,
                    "Python package Release metadata",
                ),
                authority,
                version,
                repository_root / _PYTHON_PACKAGE_METADATA,
            ),
        ),
        _ReleaseVersionUpdate(
            repository_root / _PYTHON_LOCKFILE,
            _replace_package_version(
                _read_metadata_text(
                    repository_root / _PYTHON_LOCKFILE,
                    "Python lockfile Release metadata",
                ),
                "git-loopy",
                _python_distribution_version(authority),
                python_version,
                repository_root / _PYTHON_LOCKFILE,
            ),
        ),
        _ReleaseVersionUpdate(
            repository_root / _RUST_MANIFEST,
            _replace_package_version(
                _read_metadata_text(
                    repository_root / _RUST_MANIFEST,
                    "Rust manifest Release metadata",
                ),
                "git-loopy-tui",
                authority,
                version,
                repository_root / _RUST_MANIFEST,
            ),
        ),
        _ReleaseVersionUpdate(
            repository_root / _RUST_LOCKFILE,
            _replace_package_version(
                _read_metadata_text(
                    repository_root / _RUST_LOCKFILE,
                    "Rust lockfile Release metadata",
                ),
                "git-loopy-tui",
                authority,
                version,
                repository_root / _RUST_LOCKFILE,
            ),
        ),
        _ReleaseVersionUpdate(
            repository_root / _TUI_PROBE,
            _replace_exactly_once(
                _read_metadata_text(
                    repository_root / _TUI_PROBE,
                    "TUI documented probe Release metadata",
                ),
                re.compile(
                    rf'("version":\s*)"{re.escape(authority)}"',
                ),
                rf'\g<1>"{version}"',
                repository_root / _TUI_PROBE,
                "TUI documented probe Release version",
            ),
        ),
    )
    _apply_release_version_updates(updates)


def write_repository_release_notes(
    repository_root: Path,
    advanced_line: ReleaseLine,
    release_line: ReleaseLine,
) -> ReleaseNotesWrite:
    """Write one development fragment and, on Promotion, a stable draft.

    The caller supplies both lines because a `major` first advances to a
    `dev.N` fragment and then promotes that same target to stable. The stable
    draft composes every fragment for its target unless a human already wrote
    the stable note, which remains publication input unchanged.
    """
    repository_root = repository_root.resolve()
    fragment_path = _release_notes_path(repository_root, advanced_line.version)
    fragment_content = (
        _read_metadata_text(fragment_path, "Release-note fragment")
        if fragment_path.exists()
        else _release_note_fragment_content(advanced_line)
    )
    updates = (
        []
        if fragment_path.exists()
        else [_ReleaseVersionUpdate(fragment_path, fragment_content)]
    )
    commit_paths = [fragment_path.relative_to(repository_root)]

    if release_line.counter == 0:
        stable_path = _release_notes_path(repository_root, release_line.version)
        commit_paths.append(stable_path.relative_to(repository_root))
        if not stable_path.exists():
            updates.append(
                _ReleaseVersionUpdate(
                    stable_path,
                    _compose_stable_release_notes(
                        repository_root,
                        release_line.version,
                        release_line.target,
                        advanced_line.version,
                        fragment_content,
                    ),
                )
            )

    snapshots = tuple(
        _ReleaseNoteSnapshot(
            update.path,
            _read_metadata_text(update.path, "Release-note fragment")
            if update.path.exists()
            else None,
        )
        for update in updates
    )
    _apply_release_note_updates(updates, snapshots)
    return ReleaseNotesWrite(tuple(commit_paths), snapshots)


def restore_repository_release_notes(notes_write: ReleaseNotesWrite) -> None:
    """Restore the Release-note state captured before a refused Release commit."""
    updates = tuple(
        _ReleaseVersionUpdate(snapshot.path, snapshot.content)
        for snapshot in notes_write.snapshots
        if snapshot.content is not None
    )
    restored_paths = {update.path for update in updates}
    _apply_release_note_updates(
        updates,
        tuple(
            _ReleaseNoteSnapshot(path, _read_metadata_text(path, "Release-note fragment"))
            for path in restored_paths
        ),
    )
    for snapshot in notes_write.snapshots:
        if snapshot.content is None:
            try:
                snapshot.path.unlink(missing_ok=True)
            except OSError as exc:
                raise ReleaseVersionError(
                    f"cannot remove generated Release note {snapshot.path}: {exc}"
                ) from exc


def _release_notes_path(repository_root: Path, version: str) -> Path:
    _validate_semver(version, "Release-note version")
    return repository_root / _RELEASE_NOTES_DIRECTORY / f"v{version}.md"


def _release_note_fragment_content(release_line: ReleaseLine) -> str:
    return (
        f"# git-loopy {release_line.version}\n\n"
        "This development fragment advances the Release line to "
        f"`{release_line.version}` on the way to stable `{release_line.target}`.\n"
    )


def _compose_stable_release_notes(
    repository_root: Path,
    stable_version: str,
    target: str,
    pending_version: str,
    pending_content: str,
) -> str:
    fragments: dict[str, tuple[int, str]] = {}
    pattern = re.compile(
        rf"^v{re.escape(target)}-dev\.(0|[1-9][0-9]*)\.md$"
    )
    release_directory = repository_root / _RELEASE_NOTES_DIRECTORY
    if release_directory.is_dir():
        for path in release_directory.iterdir():
            match = pattern.fullmatch(path.name)
            if match is not None and path.is_file():
                fragments[path.stem.removeprefix("v")] = (
                    int(match.group(1)),
                    _read_metadata_text(path, "Release-note fragment"),
                )
    pending_counter = _release_note_counter(pending_version, target)
    fragments[pending_version] = (pending_counter, pending_content)

    lines = [
        f"# git-loopy {stable_version}",
        "",
        f"git-loopy {stable_version} was promoted from the committed development fragments below.",
    ]
    if not fragments:
        return "\n".join(
            [
                *lines,
                "",
                "No development fragments were available when this stable draft was composed.",
                "",
            ]
        )
    lines.extend(("", "## Development fragments"))
    for version, (_counter, content) in sorted(
        fragments.items(), key=lambda item: (item[1][0], item[0])
    ):
        body = _release_note_body(content)
        lines.extend(("", f"### {version}", "", body or "No additional notes were recorded in this fragment."))
    return "\n".join([*lines, ""])


def _release_note_counter(version: str, target: str) -> int:
    match = re.fullmatch(
        rf"{re.escape(target)}-dev\.(0|[1-9][0-9]*)",
        version,
    )
    if match is None:
        raise ReleaseVersionError(
            f"development Release-note version {version!r} does not match target {target!r}"
        )
    return int(match.group(1))


def _release_note_body(content: str) -> str:
    lines = content.replace("\r\n", "\n").splitlines()
    if lines and lines[0].startswith("# "):
        lines.pop(0)
    return "\n".join(lines).strip()


def _apply_release_note_updates(
    updates: Sequence[_ReleaseVersionUpdate],
    snapshots: Sequence[_ReleaseNoteSnapshot],
) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for update in updates:
            update.path.parent.mkdir(parents=True, exist_ok=True)
            staged.append((update.path, _stage_release_note_content(update.path, update.content)))
        for target, replacement in staged:
            os.replace(replacement, target)
    except OSError as exc:
        for snapshot in snapshots:
            if snapshot.content is None:
                snapshot.path.unlink(missing_ok=True)
            else:
                replacement = _stage_release_note_content(snapshot.path, snapshot.content)
                os.replace(replacement, snapshot.path)
        raise ReleaseVersionError(f"cannot write Release notes: {exc}") from exc
    finally:
        _remove_staged_release_version_paths(tuple(path for _target, path in staged))


def _stage_release_note_content(target: Path, content: str) -> Path:
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".git-loopy-release",
        delete=False,
    ) as staged:
        os.chmod(staged.name, mode)
        staged.write(content)
        staged.flush()
        os.fsync(staged.fileno())
    return Path(staged.name)


def _python_distribution_version(version: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-dev\.(\d+))?", version)
    if match is None:
        raise ReleaseVersionError(
            "Release version must be stable or a -dev.N prerelease to update "
            "Python distribution metadata"
        )
    stable, counter = match.groups()
    return f"{stable}.dev{counter}" if counter is not None else stable


def _replace_python_source_version(
    content: str,
    expected: str,
    version: str,
    path: Path,
) -> str:
    return _replace_exactly_once(
        content,
        re.compile(rf'(__version__\s*=\s*["\']){re.escape(expected)}(["\'])'),
        rf"\g<1>{version}\g<2>",
        path,
        "Python source Release version",
    )


def _replace_project_version(
    content: str,
    expected: str,
    version: str,
    path: Path,
) -> str:
    project = re.search(r"(?ms)^\[project\]$(.*?)(?=^\[|\Z)", content)
    if project is None:
        raise ReleaseVersionError(f"cannot find [project] metadata in {path}")
    replacement = _replace_exactly_once(
        project.group(1),
        re.compile(rf'(^version\s*=\s*["\']){re.escape(expected)}(["\']$)', re.MULTILINE),
        rf"\g<1>{version}\g<2>",
        path,
        "Python package Release version",
    )
    return f"{content[:project.start(1)]}{replacement}{content[project.end(1):]}"


def _replace_package_version(
    content: str,
    package_name: str,
    expected: str,
    version: str,
    path: Path,
) -> str:
    headers = list(re.finditer(r"(?m)^\[(?:\[)?package(?:\])?\]$", content))
    matches: list[tuple[int, int]] = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(content)
        section = content[header.start() : end]
        if re.search(
            rf'^name\s*=\s*"{re.escape(package_name)}"$',
            section,
            re.MULTILINE,
        ):
            matches.append((header.start(), end))

    if len(matches) != 1:
        raise ReleaseVersionError(
            f"{package_name} Release metadata must occur exactly once in {path}"
        )
    start, end = matches[0]
    replacement = _replace_exactly_once(
        content[start:end],
        re.compile(rf'(^version\s*=\s*["\']){re.escape(expected)}(["\']$)', re.MULTILINE),
        rf"\g<1>{version}\g<2>",
        path,
        f"{package_name} Release version",
    )
    return f"{content[:start]}{replacement}{content[end:]}"


def _replace_exactly_once(
    content: str,
    pattern: re.Pattern[str],
    replacement: str,
    path: Path,
    label: str,
) -> str:
    updated, count = pattern.subn(replacement, content)
    if count != 1:
        raise ReleaseVersionError(f"{label} must occur exactly once in {path}")
    return updated


def _apply_release_version_updates(updates: Sequence[_ReleaseVersionUpdate]) -> None:
    staged: list[tuple[Path, Path, Path]] = []
    try:
        for update in updates:
            original = _read_metadata_text(update.path, "Release version copy")
            replacement = _stage_release_version_content(update.path, update.content)
            try:
                backup = _stage_release_version_content(update.path, original)
            except (ReleaseVersionError, OSError):
                _remove_staged_release_version_paths((replacement,))
                raise
            staged.append(
                (update.path, replacement, backup)
            )
    except (ReleaseVersionError, OSError) as exc:
        _remove_staged_release_version_files(staged)
        if isinstance(exc, ReleaseVersionError):
            raise
        raise ReleaseVersionError(f"cannot stage Release version metadata: {exc}") from exc

    try:
        replaced: list[tuple[Path, Path, Path]] = []
        for target, replacement, original in staged:
            os.replace(replacement, target)
            replaced.append((target, replacement, original))
    except OSError as exc:
        rollback_error: OSError | None = None
        for target, _replacement, original in reversed(replaced):
            try:
                os.replace(original, target)
            except OSError as rollback_exc:
                rollback_error = rollback_exc
                break
        if rollback_error is not None:
            raise ReleaseVersionError(
                "cannot complete Release version write and rollback failed: "
                f"{rollback_error}"
            ) from exc
        raise ReleaseVersionError(
            f"cannot complete Release version write; all copies were restored: {exc}"
        ) from exc
    finally:
        _remove_staged_release_version_files(staged)


def _stage_release_version_content(target: Path, content: str) -> Path:
    staged_path: Path | None = None
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".git-loopy-release",
            delete=False,
        ) as staged:
            staged_path = Path(staged.name)
            os.chmod(staged_path, mode)
            staged.write(content)
            staged.flush()
            os.fsync(staged.fileno())
        return staged_path
    except OSError:
        if staged_path is not None:
            _remove_staged_release_version_paths((staged_path,))
        raise


def _remove_staged_release_version_paths(paths: Sequence[Path]) -> None:
    cleanup_errors: list[OSError] = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(exc)
    if cleanup_errors:
        raise ReleaseVersionError(
            f"cannot remove staged Release version metadata: {cleanup_errors[0]}"
        ) from cleanup_errors[0]


def _remove_staged_release_version_files(
    staged: Sequence[tuple[Path, Path, Path]],
) -> None:
    _remove_staged_release_version_paths(
        tuple(path for _target, replacement, original in staged for path in (replacement, original))
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or promote git-loopy Release version metadata."
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
    parser.add_argument(
        "--promote-milestone",
        help="closed vX.Y.Z milestone that may promote the current Release line",
    )
    parser.add_argument(
        "--milestone-state",
        default="CLOSED",
        help="state of --promote-milestone (default: CLOSED)",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="optional GitHub Actions output file for the Promotion result",
    )
    return parser


def _write_promotion_output(
    path: Path, promoted: str | None, fragment_path: Path | None
) -> None:
    """Append one Promotion decision to a GitHub Actions step output file.

    Appended rather than written: a step's output file is shared with whatever
    else that step reports, and a Promotion that silently truncated its
    neighbours would be discovered by the workflow reading a stale value.
    The commit subject travels with the decision so the workflow commits a
    Promotion in exactly the words a Runner does.
    """
    lines = [f"promoted={'true' if promoted is not None else 'false'}"]
    if promoted is not None:
        lines += [
            f"version={promoted}",
            f"subject={release_line_commit_subject(promoted)}",
            f"notes_path={_RELEASE_NOTES_DIRECTORY / f'v{promoted}.md'}",
            f"fragment_path={fragment_path}",
        ]
    try:
        with path.open("a", encoding="utf-8") as output:
            output.write("".join(f"{line}\n" for line in lines))
    except OSError as exc:
        raise ReleaseVersionError(
            f"cannot write GitHub Promotion output {path}: {exc}"
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Validate repository metadata or promote a matching closed milestone."""
    args = _build_parser().parse_args(argv)
    try:
        fragment_path: Path | None = None
        current_version = validate_repository_release_version(
            args.repository_root,
            publication_version=args.publication_version,
        )
        promoted = (
            promote_closed_milestone(
                current_version,
                args.promote_milestone,
                args.milestone_state,
            )
            if args.promote_milestone is not None
            else None
        )
        if promoted is not None:
            current_line = ReleaseLine(
                target=promoted,
                counter=_release_note_counter(current_version, promoted),
            )
            write_repository_release_version(args.repository_root, promoted)
            try:
                notes_write = write_repository_release_notes(
                    args.repository_root,
                    current_line,
                    ReleaseLine(target=promoted, counter=0),
                )
                fragment_path = notes_write.commit_paths[0]
            except ReleaseVersionError:
                write_repository_release_version(args.repository_root, current_version)
                raise
        if args.github_output is not None:
            _write_promotion_output(args.github_output, promoted, fragment_path)
    except ReleaseVersionError as exc:
        print(f"release version validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
