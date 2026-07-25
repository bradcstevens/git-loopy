"""The shared TUI helper's release artifacts, as one canonical description.

`git-loopy/conformance/tui-artifacts.json` is the single place that says which
platforms a Release publishes a **TUI helper** for, what each artifact is called,
and how an installer picks its own. Release automation and the shell and
PowerShell installers all read that one file, so a target that is added, renamed,
or dropped moves every consumer at once instead of leaving one of them guessing.

This module is the production seam over that description. It never downloads
anything: a Run never installs software (PRD #173), and even release automation
hands artifacts to this module rather than the other way round.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from .events import EVENT_SCHEMA_VERSION
from .release_version import ReleaseVersionError, is_prerelease, read_release_version


ARTIFACT_METADATA_PATH = Path("git-loopy/conformance/tui-artifacts.json")
HELPER_MANIFEST_PATH = Path("git-loopy/tui/Cargo.toml")
_HEX_DIGEST = re.compile("[0-9a-fA-F]{64}")


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
    document = _read_fixture(repository_root)
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
            if args.github_output is not None:
                with args.github_output.open("a", encoding="utf-8") as handle:
                    handle.write(f"version={version}\n")
                    handle.write(f"tag=v{version}\n")
                    handle.write(
                        f"prerelease={'true' if is_prerelease(version) else 'false'}\n"
                    )
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
