"""The shared TUI helper's release artifacts, as one canonical description.

`git-loopy/conformance/tui-artifacts.json` is the single place that says which
platforms a Release publishes a **TUI helper** for, what each artifact is called,
and how an installer picks its own. Release automation and the shell and
PowerShell installers all read that one file, so a target that is added, renamed,
or dropped moves every consumer at once instead of leaving one of them guessing.

This module is the production seam over that description. Maintenance downloads
verified helpers, and publication reads back their canonical public bytes. A Run
only discovers and probes an already installed helper; it never installs one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from .events import EVENT_SCHEMA_VERSION
from .distribution_mode import DistributionModeError, resolve_distribution_mode
from .release_version import ReleaseVersionError, is_prerelease, read_release_version
from .settings import global_dir


ARTIFACT_METADATA_PATH = Path("git-loopy/conformance/tui-artifacts.json")
HELPER_MANIFEST_PATH = Path("git-loopy/tui/Cargo.toml")

#: The command every channel installs the helper under, and the suffix a Windows
#: artifact carries. Both are declared by ``ARTIFACT_METADATA_PATH``, which needs
#: a checkout to read — so an installed Runner that has no checkout spells them
#: here, in the module that owns where a helper is found, rather than wherever it
#: happens to need them.
HELPER_COMMAND_NAME = "git-loopy-tui"
_WINDOWS_EXECUTABLE_SUFFIX = ".exe"
_RUNTIME_RELEASE_URL = (
    "https://github.com/bradcstevens/git-loopy/releases/download/v{version}/{artifact}"
)
_RUNTIME_RELEASE_INDEX_URL = (
    "https://api.github.com/repos/bradcstevens/git-loopy/releases?per_page=100&page={page}"
)

#: The page size ``_RUNTIME_RELEASE_INDEX_URL`` asks for, and the ceiling on how
#: many of those pages one resolution will walk. GitHub returns releases
#: newest-first, so a bounded walk still sees every candidate that could win
#: newest-at-or-below; the ceiling exists so a host that never shortens a page
#: cannot stall ``git-loopy update`` indefinitely.
_RELEASE_INDEX_PAGE_SIZE = 100
_RELEASE_INDEX_PAGE_LIMIT = 10

_HEX_DIGEST = re.compile("[0-9a-fA-F]{64}")
_SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)(?:-(?P<prerelease>"
    r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

#: The discovery ranks that are components of one packaged distribution, and the
#: repair each one's Release drift has. Wrapper contract §15 refuses both rather
#: than attaching; only a ``PATH`` helper, which belongs to a separate
#: installation, may attach across a Release difference.
_DISTRIBUTION_HELPER_REPAIRS = {
    "clone-local": "",
    "machine-local": " Run `git-loopy update` to refresh it.",
}


class TuiReleaseError(ValueError):
    """The helper's release metadata is missing, unreadable, or inconsistent."""


@dataclass(frozen=True)
class ArtifactTarget:
    """One platform the Release publishes a helper artifact for."""

    triple: str
    os: str
    arch: str
    libc: str | None
    runner: str
    build: str
    container: str | None
    packages_install: str | None
    requires_tool: str | None

    @property
    def is_native(self) -> bool:
        """Whether a release runner can execute what it just built."""
        return self.build == "native"


@dataclass(frozen=True)
class DeferredTarget:
    """A platform this Release deliberately does not publish, and why.

    Deferral is recorded rather than left to the absence of a target so an
    operator on Windows arm64 is told the Phase 2 bar excludes them, instead of
    reading the same "no artifact" sentence a typo would produce.
    """

    triple: str
    os: str
    arch: str
    reason: str


@dataclass(frozen=True)
class ArtifactMetadata:
    """The complete published artifact set for one Release."""

    command_name: str
    targets: tuple[ArtifactTarget, ...]
    deferred_targets: tuple[DeferredTarget, ...]
    host_systems: dict[str, str]
    host_machines: dict[str, str]
    archive_name_template: str
    checksum_name_template: str
    checksum_algorithm: str
    archive_formats: dict[str, tuple[str, str]]
    release_download_url_template: str
    release_index_url_template: str = _RUNTIME_RELEASE_INDEX_URL


@dataclass(frozen=True)
class PublishedArtifact:
    """One target's archive, its checksum file, and the helper inside it."""

    target: ArtifactTarget
    archive_name: str
    checksum_name: str
    executable_name: str


def _optional_text(value: Any) -> str | None:
    """A fixture field that is either a string or deliberately absent."""
    return None if value is None else str(value)


def _read_fixture(repository_root: Path) -> dict[str, Any]:
    path = repository_root / ARTIFACT_METADATA_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TuiReleaseError(
            f"cannot read helper artifact metadata {path}: {exc}"
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TuiReleaseError(
            f"helper artifact metadata {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise TuiReleaseError(f"helper artifact metadata {path} must be an object")
    return parsed


def load_artifact_metadata(repository_root: Path) -> ArtifactMetadata:
    """Read the canonical artifact description from ``repository_root``."""
    return _parse_artifact_metadata(_read_fixture(repository_root))


def _parse_artifact_metadata(document: dict[str, Any]) -> ArtifactMetadata:
    """Turn one canonical artifact document into the runtime description."""
    aliases = document["host_aliases"]
    return ArtifactMetadata(
        command_name=str(document["command_name"]),
        targets=tuple(
            ArtifactTarget(
                triple=str(record["triple"]),
                os=str(record["os"]),
                arch=str(record["arch"]),
                libc=None if record["libc"] is None else str(record["libc"]),
                runner=str(record["runner"]),
                build=str(record["build"]),
                container=_optional_text(record["container"]),
                packages_install=_optional_text(record["packages_install"]),
                requires_tool=_optional_text(record["requires_tool"]),
            )
            for record in document["targets"]
        ),
        deferred_targets=tuple(
            DeferredTarget(
                triple=str(record["triple"]),
                os=str(record["os"]),
                arch=str(record["arch"]),
                reason=str(record["reason"]),
            )
            for record in document["deferred_targets"]
        ),
        host_systems={
            str(key): str(value) for key, value in aliases["systems"].items()
        },
        host_machines={
            str(key): str(value) for key, value in aliases["machines"].items()
        },
        archive_name_template=str(document["archive_name_template"]),
        checksum_name_template=str(document["checksum_name_template"]),
        checksum_algorithm=str(document["checksum_algorithm"]),
        archive_formats={
            str(host_os): (
                str(record["extension"]),
                str(record["executable_suffix"]),
            )
            for host_os, record in document["archive_formats"].items()
        },
        release_download_url_template=str(document["release_download_url_template"]),
        release_index_url_template=str(
            document.get("release_index_url_template", _RUNTIME_RELEASE_INDEX_URL)
        ),
    )


def release_artifact_url(
    metadata: ArtifactMetadata,
    *,
    release_version: str,
    artifact: str,
) -> str:
    """Where one Release publishes one artifact.

    Every distribution channel — both installers, the Homebrew tap, the winget
    and Scoop manifests — downloads the same bytes, so the address is derived
    from one shared template rather than restated per channel. A channel that
    resolves its own URL is a channel that can install a different Release.
    """
    return metadata.release_download_url_template.format(
        version=release_version,
        artifact=artifact,
    )


@dataclass(frozen=True)
class SemanticVersion:
    """The SemVer identity of one Release."""

    raw: str
    core: tuple[int, int, int]
    prerelease: tuple[str, ...] | None


def resolve_published_release(
    declared_version: str,
    published_versions: Sequence[str],
) -> str:
    """Resolve the newest published helper Release no newer than the tree.

    A Release line advances on every issue, while the cross-platform helper is
    only present on completed GitHub Releases. The selected version remains the
    helper's identity: callers must verify its ``--version`` output against this
    result, not against the tree's possibly newer declaration.
    """
    declared = _parse_semver(declared_version, "declared Release version")
    selected: SemanticVersion | None = None
    for published_version in published_versions:
        published = _parse_semver(published_version, "published helper Release")
        if _compare_semver(published, declared) > 0:
            continue
        if (
            selected is None
            or _compare_semver(published, selected) > 0
            or (
                _compare_semver(published, selected) == 0
                and published_version == declared_version
            )
        ):
            selected = published

    if selected is None:
        raise TuiReleaseError(
            "no published git-loopy-tui Release is at or below declared Release "
            f"version {declared_version!r}"
        )
    return selected.raw


def _parse_semver(
    version: str,
    label: str,
) -> SemanticVersion:
    match = _SEMVER.fullmatch(version)
    if match is None:
        raise TuiReleaseError(f"{label} {version!r} is not valid Semantic Versioning")
    prerelease = (
        tuple(match.group("prerelease").split("."))
        if match.group("prerelease") is not None
        else None
    )
    if prerelease is not None and any(
        identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        for identifier in prerelease
    ):
        raise TuiReleaseError(f"{label} {version!r} is not valid Semantic Versioning")
    return SemanticVersion(
        raw=version,
        core=(int(match.group("major")), int(match.group("minor")), int(match.group("patch"))),
        prerelease=prerelease,
    )


def _compare_semver(
    left: SemanticVersion,
    right: SemanticVersion,
) -> int:
    if left.core != right.core:
        return -1 if left.core < right.core else 1
    if left.prerelease is None or right.prerelease is None:
        if left.prerelease is None and right.prerelease is None:
            return 0
        return 1 if left.prerelease is None else -1

    for left_identifier, right_identifier in zip(left.prerelease, right.prerelease):
        if left_identifier == right_identifier:
            continue
        left_numeric = left_identifier.isdigit()
        right_numeric = right_identifier.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_identifier) < int(right_identifier) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_identifier < right_identifier else 1
    if len(left.prerelease) == len(right.prerelease):
        return 0
    return -1 if len(left.prerelease) < len(right.prerelease) else 1


def require_stable_release(
    release_version: str,
    *,
    release_marked_prerelease: bool,
    channel: str,
) -> str:
    """Refuse to publish anything but a stable Release to a package channel.

    Every maintained channel is the stable one, and an operator running `brew
    install`, `winget install`, or `scoop install` never names a version — so the
    version they get is whatever that channel last wrote down. A prerelease is
    precisely the Release whose Windows artifact the platform-trust gate allows
    to be unsigned and whose macOS artifact needs no notary verdict, so letting
    one reach a channel would publish, under the plain name, the one build that
    gate deliberately holds to a lower bar.

    Two things say which channel a Release is on and they are asked separately.
    The version string decides what the platform-trust gate *required*; the
    prerelease flag on the Release itself is what an operator, and a channel
    resolving "the stable Release", actually sees. The marking is editable after
    publication and is applied by a different workflow, so a channel that
    inferred it from the version would be resolving "the stable Release" on the
    strength of a fact it never read.

    One rule for every channel rather than one per channel: a second copy is a
    second place to relax it, and the two could disagree without either failing.
    """
    if is_prerelease(release_version):
        raise TuiReleaseError(
            f"{channel} is the stable channel: {release_version!r} is a "
            "prerelease and is published through the GitHub Release alone"
        )
    if release_marked_prerelease:
        raise TuiReleaseError(
            f"{release_version!r} is a stable version but the completed Release "
            f"is marked prerelease; {channel} publishes what operators see as "
            "stable, so the two must agree"
        )
    return release_version


def published_artifacts(metadata: ArtifactMetadata) -> tuple[PublishedArtifact, ...]:
    """Every artifact one Release must publish, in declaration order.

    The names carry no version. A Release addresses its artifacts by tag, and a
    filename that repeated the version would give a mismatched download two ways
    to look right; `--version` and the checksum are what prove identity.
    """
    return tuple(artifact_for(metadata, target) for target in metadata.targets)


def artifact_for(
    metadata: ArtifactMetadata,
    target: ArtifactTarget,
) -> PublishedArtifact:
    """The archive, checksum, and executable names for one target."""
    try:
        extension, executable_suffix = metadata.archive_formats[target.os]
    except KeyError as exc:
        raise TuiReleaseError(
            f"helper artifact metadata declares no archive format for {target.os}"
        ) from exc

    archive_name = metadata.archive_name_template.format(
        command=metadata.command_name,
        target=target.triple,
        extension=extension,
    )
    return PublishedArtifact(
        target=target,
        archive_name=archive_name,
        checksum_name=metadata.checksum_name_template.format(archive=archive_name),
        executable_name=f"{metadata.command_name}{executable_suffix}",
    )


def select_target(
    metadata: ArtifactMetadata,
    *,
    system: str,
    machine: str,
    libc: str | None = None,
) -> ArtifactTarget:
    """Resolve the one artifact a host with this shape should install.

    ``system`` and ``machine`` are what the host calls itself — `uname`,
    `platform.system()`, `$PSVersionTable`, `PROCESSOR_ARCHITECTURE` — so the
    alias tables live in the shared metadata and every installer normalizes the
    same way. ``libc`` is Linux's alone; a host that cannot tell which one it has
    takes the statically linked musl build, because that one runs either way.
    """
    host_os = metadata.host_systems.get(system.strip().lower())
    host_arch = metadata.host_machines.get(machine.strip().lower())

    for deferred in metadata.deferred_targets:
        if deferred.os == host_os and deferred.arch == host_arch:
            raise TuiReleaseError(
                f"no {metadata.command_name} artifact for {system} {machine}: "
                f"{deferred.reason}"
            )

    candidates = [
        target
        for target in metadata.targets
        if target.os == host_os and target.arch == host_arch
    ]
    if not candidates:
        raise TuiReleaseError(
            f"no {metadata.command_name} artifact is published for {system} {machine}"
        )
    if len(candidates) == 1:
        return candidates[0]

    wanted = (libc or "").strip().lower() or "musl"
    for candidate in candidates:
        if candidate.libc == wanted:
            return candidate
    raise TuiReleaseError(
        f"no {metadata.command_name} artifact is published for "
        f"{system} {machine} against {libc}"
    )


def _read_helper_manifest(repository_root: Path) -> dict[str, Any]:
    path = repository_root / HELPER_MANIFEST_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TuiReleaseError(f"cannot read helper manifest {path}: {exc}") from exc
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise TuiReleaseError(
            f"helper manifest {path} is not valid TOML: {exc}"
        ) from exc


def helper_release_version(
    repository_root: Path,
    *,
    tag_ref: str | None = None,
) -> str:
    """The one Release version the helper must build, report, and publish as.

    ADR-0016 makes the root `VERSION` the authority for the whole distribution.
    The helper's `Cargo.toml` is what `--version` and the `--schema-version`
    probe answer with, so drift between the two would publish an artifact that
    truthfully denies belonging to the Release that shipped it — which is
    exactly what the installers' pinned-version check would then reject.
    """
    try:
        authority = read_release_version(repository_root / "VERSION")
    except ReleaseVersionError as exc:
        raise TuiReleaseError(str(exc)) from exc

    manifest_path = repository_root / HELPER_MANIFEST_PATH
    manifest = _read_helper_manifest(repository_root)
    package = manifest.get("package")
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str):
        raise TuiReleaseError(
            f"helper manifest {manifest_path} must define [package].version"
        )
    if version != authority:
        raise TuiReleaseError(
            "TUI helper Release version mismatch: expected "
            f"{authority!r} from VERSION, found {version!r} in "
            f"{HELPER_MANIFEST_PATH.name} ({manifest_path})"
        )

    if tag_ref is not None:
        tag = tag_ref.rsplit("/", 1)[-1]
        if tag != f"v{authority}":
            raise TuiReleaseError(
                "publication tag mismatch: expected "
                f"'v{authority}' from VERSION, found {tag!r}"
            )
    return authority


def probe_runtime_helper(
    helper: Path,
    *,
    event_schema_version: int = EVENT_SCHEMA_VERSION,
    timeout: float = 5.0,
) -> RuntimeHelperProbe:
    """Read one installed helper's release + schema identity.

    The runtime path is looser than publication smoke tests: it proves the helper
    is executable, reports a Release version, and accepts this Event schema. It
    deliberately does not drain a synthetic Run — an operator may have only the
    already-installed helper the repository or PATH provides.
    """
    probe = _run_helper(helper, ["--schema-version"], timeout=timeout)
    if probe.returncode != 0:
        raise TuiReleaseError(
            f"runtime helper {helper} exited {probe.returncode} for --schema-version"
        )
    try:
        document = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise TuiReleaseError(
            f"runtime helper {helper} did not answer --schema-version with JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise TuiReleaseError(
            f"runtime helper {helper} answered --schema-version with "
            "something other than an object"
        )
    try:
        minimum = int(document["min_event_schema_version"])
        maximum = int(document["max_event_schema_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TuiReleaseError(
            f"runtime helper {helper} published no Event-schema range"
        ) from exc
    if not minimum <= event_schema_version <= maximum:
        raise TuiReleaseError(
            f"runtime helper {helper} decodes Event schemas "
            f"{minimum}-{maximum}, not {event_schema_version}"
        )
    version = document.get("version")
    if not isinstance(version, str) or not version.strip():
        raise TuiReleaseError(f"runtime helper {helper} published no Release version")
    return RuntimeHelperProbe(
        path=helper,
        reported_version=version,
        event_schema_range=(minimum, maximum),
    )


def helper_release_record_path(helper: Path) -> Path:
    """The record naming the Release one installed helper was resolved as.

    Stated once, here, because the installer writes it and both the runtime
    discovery and ``uninstall`` read it: a helper and the record proving its
    resolved identity are one unit, and a second spelling of this name would
    let them come apart.
    """
    return helper.parent / f"{helper.name}.release"


def read_installed_helper_release(helper: Path) -> str | None:
    """Read the resolved Release recorded when this helper was installed."""
    record = helper_release_record_path(helper)
    try:
        lines = record.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    if len(lines) != 1 or not lines[0].strip():
        return None
    return lines[0].strip()


def resolve_runtime_helper(
    repository_root: Path,
    *,
    release_version: str,
    warn: Callable[[str], None],
    env: Mapping[str, str] | None = None,
    event_schema_version: int = EVENT_SCHEMA_VERSION,
    timeout: float = 5.0,
) -> Path | None:
    """Find and validate the helper a TTY Run may attach with.

    Discovery follows the shell family's runtime convention: a clone-local helper
    at ``.git-loopy/bin/git-loopy-tui`` wins over ``PATH``. Between them sits the
    machine-local helper ``git-loopy update`` installs (ADR-0054) — a location
    this Runner's own installation lifecycle owns and no other member writes,
    which is why it is a rank here rather than a change to that shared
    convention. Both of those are components of one packaged distribution, so
    Wrapper contract §15 refuses either on Release drift unless verified
    against the installer-recorded resolved Release (ADR-0052, #492); a ``PATH``
    helper is a separate installation and may be newer or older, so its drift is
    surfaced with a warning instead. Any probe failure or schema mismatch
    degrades to ``None``.
    """
    environ = os.environ if env is None else env
    clone_local = _executable(
        (repository_root / ".git-loopy" / "bin" / HELPER_COMMAND_NAME,)
    )
    machine_local = _executable(machine_local_helper_paths(environ))
    path_helper = shutil.which(HELPER_COMMAND_NAME)
    if clone_local is not None:
        candidates: tuple[tuple[str, Path], ...] = (("clone-local", clone_local),)
    elif machine_local is not None:
        candidates = (("machine-local", machine_local),)
    elif path_helper:
        candidates = (("PATH", Path(path_helper)),)
    else:
        candidates = ()
    for origin, helper in candidates:
        try:
            probe = probe_runtime_helper(
                helper,
                event_schema_version=event_schema_version,
                timeout=timeout,
            )
        except TuiReleaseError as exc:
            warn(f"ignoring the {origin} git-loopy-tui helper ({exc}).")
            continue
        if probe.reported_version == release_version:
            return probe.path
        if origin in _DISTRIBUTION_HELPER_REPAIRS:
            recorded = read_installed_helper_release(helper)
            if recorded is not None and probe.reported_version == recorded:
                try:
                    parsed_recorded = _parse_semver(recorded, "installed helper Release")
                    parsed_runner = _parse_semver(release_version, "Runner Release version")
                    is_valid_fallback = _compare_semver(parsed_recorded, parsed_runner) <= 0
                except TuiReleaseError:
                    is_valid_fallback = False
                if is_valid_fallback:
                    return probe.path
            warn(
                f"ignoring the {origin} git-loopy-tui helper "
                f"({helper}) because it reports Release {probe.reported_version!r}, "
                f"not this Runner's {release_version!r}."
                f"{_DISTRIBUTION_HELPER_REPAIRS[origin]}"
            )
            return None
        warn(
            f"the {origin} git-loopy-tui helper "
            f"({helper}) reports Release {probe.reported_version!r}, not "
            f"this Runner's {release_version!r}; attaching anyway."
        )
        return probe.path
    return None


def machine_local_helper_paths(env: Mapping[str, str]) -> tuple[Path, ...]:
    """Every name the machine-local helper answers to, most specific first.

    This is a third location, distinct from the clone-local and ``PATH`` helpers
    :func:`resolve_runtime_helper` also discovers: a clone-local helper belongs
    to one checkout and a ``PATH`` helper belongs to the package manager that put
    it there, so neither is git-loopy's own machine state to refresh or remove.
    This is the copy ADR-0054 hands to ``update`` and ``uninstall``, beside the
    Config the same scope carries but in a ``bin/`` directory, so an executable
    never lands where a Run reads Config.
    """
    bin_dir = global_dir(env) / "bin"
    return (
        bin_dir / HELPER_COMMAND_NAME,
        bin_dir / f"{HELPER_COMMAND_NAME}{_WINDOWS_EXECUTABLE_SUFFIX}",
    )


def _executable(candidates: tuple[Path, ...]) -> Path | None:
    """Return the first candidate this host can actually run."""
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _host_libc() -> str | None:
    """Normalize Python's libc spelling to the artifact description's vocabulary."""
    library = platform.libc_ver()[0].strip().lower()
    if "musl" in library:
        return "musl"
    if library in {"glibc", "gnu"}:
        return "gnu"
    return None


def _runtime_artifact_for_host(
    system: str,
    machine: str,
    libc: str | None,
) -> PublishedArtifact:
    """Resolve this package's published helper without requiring a source checkout."""
    host_os = {
        "darwin": "macos",
        "macos": "macos",
        "linux": "linux",
        "windows": "windows",
        "win32": "windows",
    }.get(system.strip().lower())
    host_arch = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x64",
        "x64": "x64",
        "x86_64": "x64",
        "armv7": "armv7",
        "armv7l": "armv7",
    }.get(machine.strip().lower())
    wanted_libc = (libc or "").strip().lower() or "musl"
    triples = {
        ("macos", "arm64", "any"): "aarch64-apple-darwin",
        ("macos", "x64", "any"): "x86_64-apple-darwin",
        ("windows", "x64", "any"): "x86_64-pc-windows-msvc",
        ("linux", "arm64", "gnu"): "aarch64-unknown-linux-gnu",
        ("linux", "x64", "gnu"): "x86_64-unknown-linux-gnu",
        ("linux", "arm64", "musl"): "aarch64-unknown-linux-musl",
        ("linux", "x64", "musl"): "x86_64-unknown-linux-musl",
    }
    key = (host_os, host_arch, wanted_libc if host_os == "linux" else "any")
    triple = triples.get(key)
    if triple is None:
        deferred = {
            ("windows", "arm64"): "Windows arm64 is deferred beyond Phase 2",
            ("linux", "armv7"): "32-bit ARM Linux is deferred beyond Phase 2",
        }.get((host_os, host_arch))
        if deferred is not None:
            raise TuiReleaseError(
                f"no {HELPER_COMMAND_NAME} artifact for {system} {machine}: {deferred}"
            )
        raise TuiReleaseError(
            f"no {HELPER_COMMAND_NAME} artifact is published for {system} {machine}"
        )
    extension = "zip" if host_os == "windows" else "tar.xz"
    executable_name = (
        f"{HELPER_COMMAND_NAME}{_WINDOWS_EXECUTABLE_SUFFIX}"
        if host_os == "windows"
        else HELPER_COMMAND_NAME
    )
    target = ArtifactTarget(
        triple=triple,
        os=host_os or "",
        arch=host_arch or "",
        libc=None if host_os != "linux" else wanted_libc,
        runner="",
        build="",
        container=None,
        packages_install=None,
        requires_tool=None,
    )
    archive_name = f"{HELPER_COMMAND_NAME}-{triple}.{extension}"
    return PublishedArtifact(
        target=target,
        archive_name=archive_name,
        checksum_name=f"{archive_name}.sha256",
        executable_name=executable_name,
    )


def fetch_published_helper_releases(
    artifact: PublishedArtifact,
    *,
    fetch: Callable[[str], bytes] | None = None,
    index_url_template: str = _RUNTIME_RELEASE_INDEX_URL,
) -> tuple[str, ...]:
    """Discover every non-draft published Release carrying this host's helper.

    A Release qualifies only if it is published and carries *both* this host's
    archive and its checksum manifest: a source-only Release, and one whose
    upload was interrupted halfway, are equally unusable as helper candidates
    and are skipped here rather than failing a download later.
    """
    download_fn = fetch or _download_release_file
    versions: list[str] = []
    for page in range(1, _RELEASE_INDEX_PAGE_LIMIT + 1):
        url = index_url_template.format(page=page)
        try:
            payload = download_fn(url)
            document = json.loads(payload.decode("utf-8"))
        except (OSError, HTTPException, UnicodeError, json.JSONDecodeError) as exc:
            raise TuiReleaseError(
                f"cannot read published helper Releases from {url}: {exc}"
            ) from exc
        if not isinstance(document, list):
            raise TuiReleaseError(
                f"cannot read published helper Releases from {url}: "
                "expected a list of releases"
            )
        for release in document:
            if not isinstance(release, dict) or release.get("draft") is True:
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag.startswith("v"):
                continue
            assets = release.get("assets")
            if not isinstance(assets, list):
                continue
            asset_names = {
                item.get("name") for item in assets if isinstance(item, dict)
            }
            if (
                artifact.archive_name in asset_names
                and artifact.checksum_name in asset_names
            ):
                versions.append(tag.removeprefix("v"))
        if len(document) < _RELEASE_INDEX_PAGE_SIZE:
            break
    return tuple(versions)


def refresh_machine_local_helper(
    release_version: str,
    env: Mapping[str, str],
    *,
    host_system: Callable[[], str] = platform.system,
    host_machine: Callable[[], str] = platform.machine,
    host_libc: Callable[[], str | None] = _host_libc,
    artifact_resolver: Callable[
        [str, str, str | None], PublishedArtifact
    ] = _runtime_artifact_for_host,
    download: Callable[[str], bytes] | None = None,
    releases_fetcher: Callable[[PublishedArtifact], Sequence[str]] | None = None,
    index_url_template: str = _RUNTIME_RELEASE_INDEX_URL,
    event_schema_version: int = EVENT_SCHEMA_VERSION,
) -> Path:
    """Replace the machine-local helper with the verified installed Release artifact.

    Resolution is *newest at or below* the declared Release (ADR-0052): a
    per-issue Release line advances faster than the cross-compiled helper is
    published, so an exact match is preferred but its absence falls back rather
    than failing. Trust does not relax with it — every candidate is still
    checksum-verified, identity-verified against the version it was resolved as,
    and proven to speak this Event schema before it is activated.

    The Release index and download URLs come from this module's constants rather
    than from ``tui-artifacts.json``, because an installed Runner has no source
    checkout; reading that fixture relative to the working directory would let
    an unrelated tree redirect where ``update`` looks.

    A refusal is attributed to published identity, never to where a candidate
    was unpacked. Absence names the host archive it required, because "no
    Release publishes a helper" and "no Release publishes *this host's* helper"
    are different faults with different remedies and are otherwise
    indistinguishable. An exhausted history names the *newest* rejected
    candidate — the helper newest-at-or-below would have chosen — rather than
    the oldest, and names it by its Release and artifact, because the scratch
    directory each candidate is verified in is deleted before the refusal
    reaches anybody.
    """
    artifact = artifact_resolver(host_system(), host_machine(), host_libc())
    destination = global_dir(env) / "bin" / artifact.executable_name
    destination_record = helper_release_record_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fetch = download or _download_release_file

    if releases_fetcher is not None:
        published_versions = releases_fetcher(artifact)
    else:
        published_versions = fetch_published_helper_releases(
            artifact,
            fetch=fetch,
            index_url_template=index_url_template,
        )

    remaining_versions = list(published_versions)
    selected_version: str | None = None
    extracted_helper: Path | None = None
    newest_rejection: tuple[str, str] | None = None

    with tempfile.TemporaryDirectory(
        prefix=f".{HELPER_COMMAND_NAME}-", dir=destination.parent
    ) as scratch_dir:
        scratch = Path(scratch_dir)

        while remaining_versions:
            try:
                candidate_version = resolve_published_release(
                    release_version, remaining_versions
                )
            except TuiReleaseError:
                break

            candidate_dir = scratch / candidate_version
            candidate_dir.mkdir(parents=True, exist_ok=True)
            archive = candidate_dir / artifact.archive_name
            checksum = candidate_dir / artifact.checksum_name
            archive_url = _runtime_release_artifact_url(
                candidate_version, artifact.archive_name
            )
            checksum_url = _runtime_release_artifact_url(
                candidate_version, artifact.checksum_name
            )
            try:
                archive.write_bytes(fetch(archive_url))
            except TuiReleaseError:
                raise
            except (OSError, HTTPException) as exc:
                raise TuiReleaseError(
                    f"cannot download release artifact {archive_url}: {exc}"
                ) from exc
            try:
                checksum.write_bytes(fetch(checksum_url))
            except TuiReleaseError:
                raise
            except (OSError, HTTPException) as exc:
                raise TuiReleaseError(
                    f"cannot download release artifact {checksum_url}: {exc}"
                ) from exc
            verify_checksum(archive, checksum)
            extracted = extract_helper(
                archive, artifact, candidate_dir / "extracted"
            )
            try:
                probe = probe_runtime_helper(
                    extracted, event_schema_version=event_schema_version
                )
            except TuiReleaseError as exc:
                if newest_rejection is None:
                    newest_rejection = (
                        candidate_version,
                        str(exc).replace(str(extracted), artifact.archive_name),
                    )
                remaining_versions = [
                    v for v in remaining_versions if v != candidate_version
                ]
                continue
            if probe.reported_version != candidate_version:
                raise TuiReleaseError(
                    f"release helper {artifact.archive_name} reports Release "
                    f"{probe.reported_version!r}, not {candidate_version!r}"
                )
            selected_version = candidate_version
            extracted_helper = extracted
            break

        if selected_version is None or extracted_helper is None:
            if newest_rejection is not None:
                rejected_version, reason = newest_rejection
                raise TuiReleaseError(
                    f"no published git-loopy-tui Release carrying "
                    f"{artifact.archive_name} at or below declared Release version "
                    f"{release_version!r} can serve this Runner; the newest "
                    f"candidate, Release {rejected_version!r}, was rejected: "
                    f"{reason}"
                )
            raise TuiReleaseError(
                f"no published git-loopy-tui Release carrying {artifact.archive_name} "
                f"is at or below declared Release version {release_version!r}"
            )

        backup_helper = scratch / "backup_helper"
        backup_record = scratch / "backup_record"
        had_helper = destination.exists()
        had_record = destination_record.exists()
        if had_helper:
            shutil.copy2(destination, backup_helper)
        if had_record:
            shutil.copy2(destination_record, backup_record)

        staged_record = scratch / f"{artifact.executable_name}.release"
        staged_record.write_text(f"{selected_version}\n", encoding="utf-8")

        try:
            os.replace(staged_record, destination_record)
            os.replace(extracted_helper, destination)
        except Exception as exc:
            if had_record:
                shutil.copy2(backup_record, destination_record)
            else:
                destination_record.unlink(missing_ok=True)
            if had_helper:
                shutil.copy2(backup_helper, destination)
            else:
                destination.unlink(missing_ok=True)
            raise TuiReleaseError(f"cannot activate TUI helper: {exc}") from exc

    return destination


def _download_release_file(url: str) -> bytes:
    """Download one public Release asset, surfacing transport failures to the caller."""
    try:
        with urlopen(url, timeout=30) as response:
            return response.read()
    except (OSError, HTTPException) as exc:
        raise TuiReleaseError(f"cannot download release artifact {url}: {exc}") from exc


def _runtime_release_artifact_url(release_version: str, artifact: str) -> str:
    return _RUNTIME_RELEASE_URL.format(version=release_version, artifact=artifact)


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                hasher.update(block)
    except OSError as exc:
        raise TuiReleaseError(f"cannot read release artifact {path}: {exc}") from exc
    return hasher.hexdigest()


def published_digest(checksum_manifest: Path, archive_name: str) -> str:
    """The SHA-256 one published manifest declares for one archive.

    Reading the manifest is separated from hashing the archive because the
    package channels never hold the archive: a tap is generated from the
    `.sha256` files a completed Release published, and downloading seven
    multi-megabyte archives to copy four digests out of them would be a second
    chance to write down a different number.
    """
    try:
        manifest = checksum_manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TuiReleaseError(
            f"cannot read checksum manifest {checksum_manifest}: {exc}"
        ) from exc

    entries = [line.split(maxsplit=1) for line in manifest.splitlines() if line.strip()]
    published = {
        name.lstrip("*").strip(): digest.strip().lower()
        for digest, name in (entry for entry in entries if len(entry) == 2)
        if _HEX_DIGEST.fullmatch(digest.strip())
    }
    if not published:
        raise TuiReleaseError(
            f"checksum manifest {checksum_manifest} declares no SHA-256 entry"
        )
    if archive_name not in published:
        raise TuiReleaseError(
            f"checksum manifest {checksum_manifest} publishes "
            f"{sorted(published)} rather than {archive_name}"
        )
    return published[archive_name]


def verify_checksum(archive: Path, checksum_manifest: Path) -> str:
    """Prove ``archive`` is the artifact its published checksum names.

    Both halves are checked. A digest that matches proves nothing if it was
    published for a different file, so the manifest's filename has to be this
    artifact's too — otherwise a correct macOS arm64 checksum would happily
    bless the x64 archive an installer downloaded by mistake.
    """
    expected = published_digest(checksum_manifest, archive.name)
    actual = _digest(archive)
    if actual != expected:
        raise TuiReleaseError(
            f"release artifact {archive.name} failed its SHA-256 checksum: "
            f"expected {expected}, computed {actual}"
        )
    return actual


@dataclass(frozen=True)
class SmokeTestResult:
    """What a freshly built artifact proved about itself."""

    reported_version: str
    event_schema_range: tuple[int, int]
    events_delivered: int


@dataclass(frozen=True)
class RuntimeHelperProbe:
    """What a runtime-discovered helper proved about itself."""

    path: Path
    reported_version: str
    event_schema_range: tuple[int, int]


def minimal_run_trace(run_id: str = "01HXR0000000000000000000AA") -> str:
    """The shortest Event trace that is still a whole Run.

    A Run-start and a Run-end, in the envelope every Orchestrator emits. It is
    deliberately not a rich trace: this proves the artifact *runs* — reads the
    stream, projects it, exits cleanly — while the Dashboard's semantics are
    already pinned by the shared `dashboard-insights.json` fixtures.
    """
    started = (
        '{"ts": "2026-05-16T00:00:00.000Z", "run_id": "%s", "iter": null, '
        '"type": "wrapper.run.start", "count": 0, "issues": []}' % run_id
    )
    ended = (
        '{"ts": "2026-05-16T00:00:01.000Z", "run_id": "%s", "iter": null, '
        '"type": "wrapper.run.end", "reason": "complete", "iterations": 0}' % run_id
    )
    return f"{started}\n{ended}\n"


def _run_helper(
    helper: Path,
    arguments: list[str],
    *,
    stdin: str | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - the artifact under test is the point
            [str(helper), *arguments],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except OSError as exc:
        raise TuiReleaseError(f"cannot run release artifact {helper}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TuiReleaseError(
            f"release artifact {helper} did not exit within {timeout:g}s "
            f"of {' '.join(arguments) or 'a minimal Run'}"
        ) from exc


def smoke_test(
    helper: Path,
    *,
    release_version: str,
    event_schema_version: int = EVENT_SCHEMA_VERSION,
    timeout: float = 60.0,
) -> SmokeTestResult:
    """Prove one native artifact is the helper this Release meant to publish.

    Three questions, in the order that makes a failure legible: does it say it
    is this Release, does it accept this Event schema, and can it drain a whole
    Run and exit cleanly? Only a native runner can ask them — a cross-built
    artifact cannot execute here, so its release job checks build and metadata
    identity instead and says so rather than pretending it ran.
    """
    reported = _run_helper(helper, ["--version"], timeout=timeout)
    if reported.returncode != 0:
        raise TuiReleaseError(
            f"release artifact {helper.name} exited {reported.returncode} for --version"
        )
    expected = f"git-loopy-tui {release_version}"
    if reported.stdout.strip() != expected:
        raise TuiReleaseError(
            f"release artifact {helper.name} reports "
            f"{reported.stdout.strip()!r}, not {expected!r}"
        )

    probe = _run_helper(helper, ["--schema-version"], timeout=timeout)
    if probe.returncode != 0:
        raise TuiReleaseError(
            f"release artifact {helper.name} exited {probe.returncode} "
            "for --schema-version"
        )
    try:
        document = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise TuiReleaseError(
            f"release artifact {helper.name} did not answer --schema-version "
            f"with JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise TuiReleaseError(
            f"release artifact {helper.name} answered --schema-version with "
            "something other than an object"
        )
    try:
        minimum = int(document["min_event_schema_version"])
        maximum = int(document["max_event_schema_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TuiReleaseError(
            f"release artifact {helper.name} published no Event-schema range"
        ) from exc
    if not minimum <= event_schema_version <= maximum:
        raise TuiReleaseError(
            f"release artifact {helper.name} decodes Event schemas "
            f"{minimum}-{maximum}, not {event_schema_version}"
        )
    probed_version = document.get("version")
    if probed_version != release_version:
        raise TuiReleaseError(
            f"release artifact {helper.name} probes as Release "
            f"{probed_version!r}, not {release_version!r}"
        )

    trace = minimal_run_trace()
    drained = _run_helper(helper, [], stdin=trace, timeout=timeout)
    if drained.returncode != 0:
        raise TuiReleaseError(
            f"release artifact {helper.name} exited {drained.returncode} "
            "draining a minimal Run"
        )

    return SmokeTestResult(
        reported_version=release_version,
        event_schema_range=(minimum, maximum),
        events_delivered=len(trace.splitlines()),
    )


def helper_dist_configuration(repository_root: Path) -> dict[str, Any]:
    """The pinned cargo-dist configuration the helper manifest declares."""
    manifest = _read_helper_manifest(repository_root)
    workspace = manifest.get("workspace")
    metadata = workspace.get("metadata") if isinstance(workspace, dict) else None
    configuration = metadata.get("dist") if isinstance(metadata, dict) else None
    if not isinstance(configuration, dict):
        raise TuiReleaseError(
            f"helper manifest {repository_root / HELPER_MANIFEST_PATH} declares "
            "no [workspace.metadata.dist] release configuration"
        )
    return configuration


def helper_package_is_distributable(repository_root: Path) -> bool:
    """Whether cargo-dist can see the helper binary at all.

    The helper is never published to crates.io, so its manifest says
    `publish = false` — and cargo-dist treats that as "this package has nothing
    to release", refusing the entire workspace before it plans one artifact.
    `[package.metadata.dist] dist = true` is the documented opt-in, and it has
    to be explicit: without it the pipeline fails at its first command with a
    diagnostic about an empty workspace, which points nowhere near the cause.
    """
    manifest = _read_helper_manifest(repository_root)
    package = manifest.get("package")
    metadata = package.get("metadata") if isinstance(package, dict) else None
    configuration = metadata.get("dist") if isinstance(metadata, dict) else None
    opted_in = (
        configuration.get("dist") if isinstance(configuration, dict) else None
    )
    if opted_in is not True:
        raise TuiReleaseError(
            f"helper manifest {repository_root / HELPER_MANIFEST_PATH} sets "
            "publish = false without [package.metadata.dist] dist = true, so "
            "cargo-dist has nothing to release"
        )
    return True


def helper_release_profile(repository_root: Path) -> dict[str, Any]:
    """The cargo profile cargo-dist compiles the release artifacts with.

    cargo-dist always builds `--profile dist`, and cargo will not invent a
    profile it has never been told about. `dist init` normally writes this
    section; a hand-maintained manifest has to carry it deliberately, and
    without it every build job dies at `error: profile 'dist' is not defined`
    *after* the artifact plan has already succeeded — which is the worst place
    for it, because the plan is what everything else trusts.
    """
    manifest = _read_helper_manifest(repository_root)
    profiles = manifest.get("profile")
    profile = profiles.get("dist") if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise TuiReleaseError(
            f"helper manifest {repository_root / HELPER_MANIFEST_PATH} defines "
            "no [profile.dist], so cargo-dist cannot build it"
        )
    return profile


def pinned_cargo_dist_version(repository_root: Path) -> str:
    """The exact cargo-dist the release pipeline is allowed to run.

    Read from the manifest rather than the fixture so there is one authority;
    the fixture repeats it for the non-Python consumers and this module proves
    the two agree.
    """
    configuration = helper_dist_configuration(repository_root)
    pinned = configuration.get("cargo-dist-version")
    if not isinstance(pinned, str) or not pinned:
        raise TuiReleaseError("helper manifest declares no pinned cargo-dist-version")
    metadata = _read_fixture(repository_root)
    declared = metadata.get("cargo_dist_version")
    if declared != pinned:
        raise TuiReleaseError(
            "release toolchain mismatch: helper manifest pins cargo-dist "
            f"{pinned!r}, artifact metadata names {declared!r}"
        )
    return pinned


def _plan_field(plan: dict[str, Any], key: str) -> Any:
    if key not in plan:
        raise TuiReleaseError(f"the release plan declares no {key!r}")
    return plan[key]


def planned_builds(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """cargo-dist's own answer to "how is each target built".

    Which runner, inside which container, after installing what. The hand-written
    workflow restates this, and restating it is exactly how three of the seven
    targets came to be scheduled onto runners that cannot link them.
    """
    ci = _plan_field(plan, "ci")
    github = ci.get("github") if isinstance(ci, dict) else None
    matrix = github.get("artifacts_matrix") if isinstance(github, dict) else None
    include = matrix.get("include") if isinstance(matrix, dict) else None
    if not isinstance(include, list):
        raise TuiReleaseError("the release plan declares no GitHub build matrix")

    builds: dict[str, dict[str, Any]] = {}
    for entry in include:
        for triple in entry.get("targets", []):
            builds[str(triple)] = entry
    return builds


def verify_release_plan(
    repository_root: Path,
    plan: dict[str, Any],
    *,
    tag_ref: str | None = None,
) -> tuple[PublishedArtifact, ...]:
    """Prove cargo-dist would build exactly the Release this repository declares.

    Four authorities have to agree before a byte is compiled: `VERSION` says
    which Release this is, the helper manifest says what `--version` will answer,
    the shared artifact metadata says what installers and package channels will
    resolve, and cargo-dist's plan says what will actually be produced. Only the
    last one is discovered rather than committed, so it is the one that has to be
    checked at release time instead of in a unit test — and it is checked against
    the other three rather than merely printed.
    """
    metadata = load_artifact_metadata(repository_root)
    release_version = helper_release_version(repository_root, tag_ref=tag_ref)
    helper_package_is_distributable(repository_root)

    pinned = pinned_cargo_dist_version(repository_root)
    planned_toolchain = _plan_field(plan, "dist_version")
    if planned_toolchain != pinned:
        raise TuiReleaseError(
            f"the release plan was produced by cargo-dist {planned_toolchain!r}, "
            f"not the pinned {pinned!r}"
        )

    announced = _plan_field(plan, "announcement_tag")
    if announced != f"v{release_version}":
        raise TuiReleaseError(
            f"the release plan announces {announced!r}, not "
            f"'v{release_version}' from VERSION"
        )

    if _plan_field(plan, "github_attestations") is not True:
        raise TuiReleaseError(
            "the release plan would publish artifacts it does not attest"
        )

    artifacts = _plan_field(plan, "artifacts")
    if not isinstance(artifacts, dict):
        raise TuiReleaseError("the release plan declares no artifacts")

    # A rule over every planned artifact rather than a list of the ones that
    # exist today: enabling a cargo-dist installer later must not be able to
    # ship one nothing can verify.
    for name, record in sorted(artifacts.items()):
        kind = record.get("kind")
        if kind in ("checksum", "unified-checksum"):
            continue
        if not record.get("checksum"):
            raise TuiReleaseError(
                f"the release plan would publish {name} ({kind}) with no "
                f"{metadata.checksum_algorithm} checksum"
            )

    declared = published_artifacts(metadata)
    planned_archives = {
        str(record.get("name")): record
        for record in artifacts.values()
        if record.get("kind") == "executable-zip"
    }
    problems: list[str] = []
    for artifact in declared:
        record = planned_archives.pop(artifact.archive_name, None)
        if record is None:
            problems.append(
                f"the release plan builds no {artifact.archive_name} for "
                f"{artifact.target.triple}"
            )
            continue
        if record.get("checksum") != artifact.checksum_name:
            problems.append(
                f"the release plan checksums {artifact.archive_name} as "
                f"{record.get('checksum')!r}, not {artifact.checksum_name!r}"
            )
        if list(record.get("target_triples") or []) != [artifact.target.triple]:
            problems.append(
                f"the release plan builds {artifact.archive_name} for "
                f"{record.get('target_triples')}, not {artifact.target.triple}"
            )
    for surplus in sorted(planned_archives):
        problems.append(f"the release plan builds an undeclared {surplus}")

    builds = planned_builds(plan)
    for target in metadata.targets:
        entry = builds.get(target.triple)
        if entry is None:
            problems.append(f"the release plan schedules no build for {target.triple}")
            continue
        if entry.get("runner") != target.runner:
            problems.append(
                f"the release plan builds {target.triple} on "
                f"{entry.get('runner')!r}, not the declared {target.runner!r}"
            )
        container = entry.get("container")
        image = container.get("image") if isinstance(container, dict) else None
        if _optional_text(image) != target.container:
            problems.append(
                f"the release plan builds {target.triple} in container "
                f"{image!r}, not the declared {target.container!r}"
            )
        # cargo-dist words its provisioning differently from the workflow step
        # that mirrors it, so the two cannot be compared verbatim. What has to
        # hold is that both reach for the same tool: "some provisioning
        # happened" would accept a command that installs nothing.
        provisioning = str(entry.get("packages_install") or "")
        if bool(provisioning) != bool(target.packages_install):
            problems.append(
                f"the release plan {'needs' if provisioning else 'needs no'} "
                f"package installation for {target.triple}, and the declared "
                f"metadata disagrees"
            )
        elif target.requires_tool and target.requires_tool not in provisioning:
            problems.append(
                f"the release plan provisions {target.triple} without "
                f"{target.requires_tool}, which it cannot build without"
            )

    if problems:
        raise TuiReleaseError("; ".join(problems))
    return declared


def _read_plan(path: Path) -> dict[str, Any]:
    raw = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TuiReleaseError(f"the release plan is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TuiReleaseError("the release plan must be an object")
    return parsed


def extract_helper(
    archive: Path,
    artifact: PublishedArtifact,
    destination: Path,
) -> Path:
    """Unpack just the helper out of one release archive.

    Only the one member is taken, by exact name, so an archive that carries
    anything else cannot write it anywhere: a release artifact is verified
    before it is trusted, and unpacking is the first place that matters.
    """
    destination.mkdir(parents=True, exist_ok=True)
    unpacked = destination / artifact.executable_name

    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                member = _named_member(
                    archive,
                    artifact.executable_name,
                    bundle.namelist(),
                )
                unpacked.write_bytes(bundle.read(member))
        else:
            with tarfile.open(archive, "r:*") as bundle:
                member = _named_member(
                    archive,
                    artifact.executable_name,
                    bundle.getnames(),
                )
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise TuiReleaseError(
                        f"release artifact {archive.name} carries {member} as "
                        "something other than a file"
                    )
                unpacked.write_bytes(extracted.read())
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise TuiReleaseError(
            f"cannot unpack release artifact {archive}: {exc}"
        ) from exc

    unpacked.chmod(0o755)
    return unpacked


def _named_member(archive: Path, executable_name: str, members: list[str]) -> str:
    for member in members:
        if PurePosixPath(member).name == executable_name:
            return member
    raise TuiReleaseError(
        f"release artifact {archive.name} carries no {executable_name}"
    )


def verify_artifact(
    repository_root: Path,
    artifact_directory: Path,
    triple: str,
    *,
    smoke_test_native: bool = False,
) -> PublishedArtifact:
    """Prove one built artifact is complete, intact, and named as declared."""
    metadata = load_artifact_metadata(repository_root)
    release_version = helper_release_version(repository_root)
    targets = {target.triple: target for target in metadata.targets}
    if triple not in targets:
        raise TuiReleaseError(
            f"{triple} is not one of this Release's published targets"
        )

    artifact = artifact_for(metadata, targets[triple])
    archive = artifact_directory / artifact.archive_name
    checksum = artifact_directory / artifact.checksum_name
    for path in (archive, checksum):
        if not path.is_file():
            raise TuiReleaseError(
                f"the release set is missing {path.name} for {triple}"
            )
    verify_checksum(archive, checksum)

    if smoke_test_native:
        if not artifact.target.is_native:
            raise TuiReleaseError(
                f"{triple} is cross-built and cannot be smoke-tested on its "
                "release runner"
            )
        with tempfile.TemporaryDirectory() as scratch:
            helper = extract_helper(archive, artifact, Path(scratch))
            smoke_test(helper, release_version=release_version)
    return artifact


def verify_release_set(
    repository_root: Path,
    artifact_directory: Path,
) -> tuple[PublishedArtifact, ...]:
    """Prove the *complete* declared set is present and intact.

    Publication is all-or-nothing: a Release that shipped six of seven archives
    would leave one platform's installers resolving a name that 404s, so the
    missing targets are reported together rather than one job at a time.
    """
    metadata = load_artifact_metadata(repository_root)
    verified: list[PublishedArtifact] = []
    problems: list[str] = []
    for artifact in published_artifacts(metadata):
        try:
            verified.append(
                verify_artifact(
                    repository_root,
                    artifact_directory,
                    artifact.target.triple,
                )
            )
        except TuiReleaseError as exc:
            problems.append(str(exc))
    if problems:
        raise TuiReleaseError("; ".join(problems))
    return tuple(verified)


def _public_release_evidence(document: Any, names: Sequence[str]) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("assets"), list):
        raise TuiReleaseError("public Release readback must declare its assets")
    assets: dict[str, Any] = {}
    for asset in document["assets"]:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise TuiReleaseError("public Release readback contains an invalid asset")
        name = asset["name"]
        if name in assets:
            raise TuiReleaseError(f"public Release readback duplicates {name}")
        assets[name] = {
            field: asset.get(field)
            for field in ("id", "name", "browser_download_url", "state", "size", "digest")
        }
    # Download counts, last-downloaded timestamps, and other host bookkeeping
    # can change because this very proof downloaded the files.
    return {
        "identity": {
            field: document.get(field)
            for field in ("id", "tag_name", "name", "html_url", "draft", "prerelease")
        },
        "assets": {name: assets.get(name) for name in names},
    }


def verify_published_release(
    repository_root: Path,
    artifact_directory: Path,
    *,
    tag_ref: str,
    distribution_mode: str,
    attestation: Path | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> tuple[PublishedArtifact, ...]:
    """Prove public bytes match the complete, locally verified build outputs.

    The tagged policy selects the promise. This does not create a Release or
    repair an attachment: any absent or disagreeing evidence is a refusal.
    Native execution remains the build runner's obligation; cross-target
    readback proves bytes and trust, never claims to execute another platform.
    """
    from . import release_trust

    version = helper_release_version(repository_root, tag_ref=tag_ref)
    try:
        mode = resolve_distribution_mode(repository_root, distribution_mode)
    except DistributionModeError as exc:
        raise TuiReleaseError(str(exc)) from exc
    if mode != "artifact-bearing":
        raise TuiReleaseError("source-only publication promises no helper artifacts")

    metadata = load_artifact_metadata(repository_root)
    policy = release_trust.load_trust_policy(repository_root)
    artifacts = verify_release_set(repository_root, artifact_directory)
    try:
        release_trust.verify_release_trust(
            repository_root, artifact_directory, version=version,
            release_marked_prerelease=is_prerelease(version), attestation=attestation,
        )
    except release_trust.ReleaseTrustError as exc:
        raise TuiReleaseError(str(exc)) from exc
    tag = f"v{version}"
    release_url = (
        metadata.release_index_url_template.split("?", 1)[0]
        + "/tags/" + quote(tag, safe="")
    )
    download = fetch or _download_release_file
    try:
        record = json.loads(download(release_url))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TuiReleaseError(f"unreadable public Release readback: {exc}") from exc
    if not isinstance(record, dict) or not isinstance(record.get("assets"), list):
        raise TuiReleaseError("public Release readback must declare its assets")
    release_page = metadata.release_download_url_template.split("/download/", 1)[0]
    expected_identity = {
        "tag_name": tag,
        "name": f"git-loopy {version}",
        "html_url": f"{release_page}/tag/{tag}",
        "draft": False,
        "prerelease": is_prerelease(version),
    }
    for field, expected in expected_identity.items():
        actual = record.get(field)
        if type(actual) is not type(expected) or actual != expected:
            raise TuiReleaseError(
                f"public Release {field} is {actual!r}, expected {expected!r}"
            )
    assets = {asset.get("name"): asset for asset in record["assets"]
              if isinstance(asset, dict) and isinstance(asset.get("name"), str)}
    names = [
        name
        for artifact in artifacts
        for name in (
            artifact.archive_name,
            artifact.checksum_name,
            policy.receipt_name_template.format(archive=artifact.archive_name),
        )
    ]
    before = _public_release_evidence(record, names)
    for name in names:
        if name not in assets:
            raise TuiReleaseError(f"public Release {tag} is missing {name}")
        expected_asset = {
            "browser_download_url": release_artifact_url(
                metadata, release_version=version, artifact=name,
            ),
            "state": "uploaded",
            "size": (artifact_directory / name).stat().st_size,
        }
        for field, expected in expected_asset.items():
            actual = assets[name].get(field)
            if type(actual) is not type(expected) or actual != expected:
                raise TuiReleaseError(
                    f"public asset {name} {field} is {actual!r}, expected {expected!r}"
                )
    with tempfile.TemporaryDirectory(prefix="git-loopy-public-helpers-") as scratch:
        downloaded = Path(scratch)
        for name in names:
            url = release_artifact_url(metadata, release_version=version, artifact=name)
            path = downloaded / name
            path.write_bytes(download(url))
            if _digest(path) != _digest(artifact_directory / name):
                raise TuiReleaseError(
                    f"public artifact {name} differs from the verified build output"
                )
        verify_release_set(repository_root, downloaded)
        for artifact in artifacts:
            extract_helper(
                downloaded / artifact.archive_name, artifact,
                downloaded / "unpacked" / artifact.target.triple,
            )
        try:
            release_trust.verify_release_trust(
                repository_root, downloaded, version=version,
                release_marked_prerelease=record["prerelease"],
                attestation=attestation,
            )
        except release_trust.ReleaseTrustError as exc:
            raise TuiReleaseError(str(exc)) from exc
        if policy.channel_for(version) in policy.attestation_channels:
            repository = urlsplit(release_page).path.removesuffix("/releases").strip("/")
            for artifact in artifacts:
                _verify_public_attestation(downloaded / artifact.archive_name, repository)
    try:
        after = json.loads(download(release_url))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TuiReleaseError(f"unreadable final Release readback: {exc}") from exc
    if _public_release_evidence(after, names) != before:
        raise TuiReleaseError("public Release changed during canonical download verification")
    return artifacts


def _verify_public_attestation(archive: Path, repository: str) -> None:
    try:
        result = subprocess.run(
            ["gh", "attestation", "verify", str(archive), "--repo", repository],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TuiReleaseError(
            f"cannot verify public attestation for {archive.name}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise TuiReleaseError(
            f"public attestation for {archive.name} was not verified: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify git-loopy TUI helper release artifacts.",
    )
    # Shared rather than global so it reads the same before or after the
    # subcommand: a release step that has to remember where a flag goes is a
    # release step that fails at the worst moment.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="repository root containing VERSION (default: current directory)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    identity = commands.add_parser(
        "identity",
        parents=[common],
        help="prove VERSION, the helper manifest, and the tag name one Release",
    )
    identity.add_argument("--tag-ref", help="the publication ref, when tagging")
    identity.add_argument(
        "--distribution-mode",
        help="explicit publication distribution mode (e.g. 'source-only')",
    )
    identity.add_argument(
        "--github-output",
        type=Path,
        help="append the resolved version and tag to this GitHub output file",
    )

    plan = commands.add_parser(
        "verify-plan",
        parents=[common],
        help="prove cargo-dist would build exactly the declared Release",
    )
    plan.add_argument(
        "--plan",
        type=Path,
        required=True,
        help="a `dist plan --output-format=json` document, or - for stdin",
    )
    plan.add_argument("--tag-ref", help="the publication ref, when tagging")

    artifact = commands.add_parser(
        "verify-artifact",
        parents=[common],
        help="verify one built artifact's name, checksum, and identity",
    )
    artifact.add_argument("--artifact-dir", type=Path, required=True)
    artifact.add_argument("--target", required=True)
    artifact.add_argument(
        "--smoke-test",
        action="store_true",
        help="also run the artifact: probe, drain a minimal Run, exit cleanly",
    )

    release_set = commands.add_parser(
        "verify-set",
        parents=[common],
        help="verify the complete declared artifact set before publication",
    )
    release_set.add_argument("--artifact-dir", type=Path, required=True)
    release_set.add_argument(
        "--require-complete-set",
        action="store_true",
        help="fail unless every declared target is present and intact",
    )
    published = commands.add_parser(
        "verify-published",
        parents=[common],
        help="prove canonical public downloads match the verified build outputs",
    )
    published.add_argument("--artifact-dir", type=Path, required=True)
    published.add_argument("--tag-ref", required=True)
    published.add_argument("--distribution-mode", required=True)
    published.add_argument("--attestation", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the helper release verifier."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "identity":
            version = helper_release_version(
                args.repository_root,
                tag_ref=args.tag_ref,
            )
            try:
                dist_mode = resolve_distribution_mode(
                    args.repository_root,
                    explicit_mode=args.distribution_mode,
                )
            except DistributionModeError as exc:
                raise TuiReleaseError(str(exc)) from exc

            if args.github_output is not None:
                with args.github_output.open("a", encoding="utf-8") as handle:
                    handle.write(f"version={version}\n")
                    handle.write(f"tag=v{version}\n")
                    handle.write(
                        f"prerelease={'true' if is_prerelease(version) else 'false'}\n"
                    )
                    handle.write(f"distribution_mode={dist_mode}\n")
            print(version)
        elif args.command == "verify-plan":
            for artifact in verify_release_plan(
                args.repository_root,
                _read_plan(args.plan),
                tag_ref=args.tag_ref,
            ):
                print(artifact.archive_name)
        elif args.command == "verify-artifact":
            artifact = verify_artifact(
                args.repository_root,
                args.artifact_dir,
                args.target,
                smoke_test_native=args.smoke_test,
            )
            print(artifact.archive_name)
        elif args.command == "verify-published":
            for artifact in verify_published_release(
                args.repository_root,
                args.artifact_dir,
                tag_ref=args.tag_ref,
                distribution_mode=args.distribution_mode,
                attestation=args.attestation,
            ):
                print(artifact.archive_name)
        else:
            if not args.require_complete_set:
                raise TuiReleaseError(
                    "verify-set publishes nothing without --require-complete-set"
                )
            for artifact in verify_release_set(
                args.repository_root,
                args.artifact_dir,
            ):
                print(artifact.archive_name)
    except TuiReleaseError as exc:
        print(f"helper release verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
