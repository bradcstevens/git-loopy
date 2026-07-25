"""The winget and Scoop distribution channels for the shared TUI helper.

Two package managers, one artifact. `winget install git-loopy-tui` and
`scoop install git-loopy-tui` both resolve the same signed
`x86_64-pc-windows-msvc` zip that a completed Release already built, signed,
verified, attested, and attached — so everything that could point somewhere else
is one fact, stated once here and written into the two formats the two
ecosystems read.

Nothing here builds, rebuilds, or re-signs an artifact, and nothing recomputes a
digest. The bytes these channels install are exactly the bytes `tui-release.yml`
published; this module only writes down where they are, what they hash to, and
who signed them, and then refuses to believe its own output without checking it
against the Release again.

`git-loopy/conformance/windows-channels.json` is the policy. It names the one
target Windows package managers run, records the other six as excluded *by name
and reason* rather than by absence, names the probe winget cannot run, and pins
the drift a committed manifest is refused for.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .release_trust import (
    ReleaseTrustError,
    load_trust_policy,
    read_trust_receipt,
)
from .tui_release import (
    ArtifactMetadata,
    PublishedArtifact,
    TuiReleaseError,
    artifact_for,
    load_artifact_metadata,
    published_digest,
    release_artifact_url,
    require_stable_release,
    select_target,
)


CHANNEL_POLICY_PATH = Path("git-loopy/conformance/windows-channels.json")


class WindowsChannelError(ValueError):
    """A Windows channel's metadata is missing, unreadable, or drifted."""


@dataclass(frozen=True)
class ExcludedTarget:
    """A published target the Windows channels deliberately do not install.

    Recorded by name for the same reason `tui-artifacts.json` defers a platform
    by name: an absent entry and a deliberate omission look identical from the
    outside, and only one of them is a decision.
    """

    triple: str
    reason: str


@dataclass(frozen=True)
class UnavailableClaim:
    """A fact one channel's format cannot carry, and why.

    The two ecosystems are not symmetric. Scoop runs `post_install` on the
    operator's own machine, so its manifest proves the installed helper reports
    this Release; winget has no such hook. winget shows an operator a publisher
    and has a field for it; a Scoop manifest has none. Recording each gap by
    name keeps it a decision rather than something a reader has to notice is
    missing — and keeps a channel from being quietly held to the weaker bar of
    whichever format has fewer fields.
    """

    channel: str
    claim: str
    reason: str


@dataclass(frozen=True)
class Channel:
    """One Windows package manager, and the repository its metadata lives in."""

    id: str
    channel_job: str
    repository: str
    package_identifier: str
    package_name: str
    publisher_url: str
    manifest_version: str | None
    default_locale: str | None
    installer_architecture: str
    files: tuple[str, ...]

    def committed_files(self, release_version: str) -> tuple[str, ...]:
        """Where this channel's metadata is committed for one Release.

        winget addresses a package version by path, so the location is part of
        what the Release publishes; Scoop's bucket holds one file per package
        whatever the version. Both are stated in the policy so the generator,
        the gate, and the job that commits the result cannot each resolve a
        different file.
        """
        return tuple(path.format(version=release_version) for path in self.files)


@dataclass(frozen=True)
class ChannelPolicy:
    """Everything the winget and Scoop channels agree on about themselves."""

    helper_command: str
    installed_target: str
    trusted_artifact_url_prefix: str
    project_url: str
    license: str
    license_url: str
    release_notes_url_template: str
    short_description: str
    host_system: str
    host_machine: str
    host_libc: str | None
    excluded_targets: tuple[ExcludedTarget, ...]
    channels: tuple[Channel, ...]
    unavailable_claims: tuple[UnavailableClaim, ...]

    def channel(self, channel_id: str) -> Channel:
        """The one channel named ``channel_id``."""
        for candidate in self.channels:
            if candidate.id == channel_id:
                return candidate
        raise WindowsChannelError(
            f"{channel_id!r} is not a Windows channel this project publishes: "
            f"{', '.join(candidate.id for candidate in self.channels)}"
        )

    def release_notes_url(self, release_version: str) -> str:
        return self.release_notes_url_template.format(version=release_version)


def _read_policy_document(repository_root: Path) -> dict[str, Any]:
    path = repository_root / CHANNEL_POLICY_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WindowsChannelError(f"cannot read channel policy {path}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WindowsChannelError(
            f"channel policy {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise WindowsChannelError(f"channel policy {path} must be an object")
    return parsed


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def load_channel_policy(repository_root: Path) -> ChannelPolicy:
    """Read the canonical winget/Scoop channel policy from ``repository_root``."""
    document = _read_policy_document(repository_root)
    host = document["host"]
    return ChannelPolicy(
        helper_command=str(document["helper_command"]),
        installed_target=str(document["installed_target"]),
        trusted_artifact_url_prefix=str(document["trusted_artifact_url_prefix"]),
        project_url=str(document["project_url"]),
        license=str(document["license"]),
        license_url=str(document["license_url"]),
        release_notes_url_template=str(document["release_notes_url_template"]),
        short_description=str(document["short_description"]),
        host_system=str(host["system"]),
        host_machine=str(host["machine"]),
        host_libc=_optional_text(host["libc"]),
        excluded_targets=tuple(
            ExcludedTarget(triple=str(record["triple"]), reason=str(record["reason"]))
            for record in document["excluded_targets"]
        ),
        channels=tuple(
            Channel(
                id=str(record["id"]),
                channel_job=str(record["channel_job"]),
                repository=str(record["repository"]),
                package_identifier=str(record["package_identifier"]),
                package_name=str(record["package_name"]),
                publisher_url=str(record["publisher_url"]),
                manifest_version=_optional_text(record["manifest_version"]),
                default_locale=_optional_text(record["default_locale"]),
                installer_architecture=str(record["installer_architecture"]),
                files=tuple(str(path) for path in record["files"]),
            )
            for record in document["channels"]
        ),
        unavailable_claims=tuple(
            UnavailableClaim(
                channel=str(record["channel"]),
                claim=str(record["claim"]),
                reason=str(record["reason"]),
            )
            for record in document["unavailable_claims"]
        ),
    )


def windows_artifact(
    metadata: ArtifactMetadata,
    policy: ChannelPolicy,
) -> PublishedArtifact:
    """The published archive both Windows channels install.

    Resolved through the same selection seam `install.ps1` uses, from the host
    shape rather than from the target triple the policy names. A manifest that
    picked its own archive could install a different build of the same Release;
    this way the channels and the installer can only ever disagree by failing
    here.
    """
    try:
        target = select_target(
            metadata,
            system=policy.host_system,
            machine=policy.host_machine,
            libc=policy.host_libc,
        )
    except TuiReleaseError as exc:
        raise WindowsChannelError(
            f"the Windows channels install {policy.installed_target}, which this "
            f"Release does not publish: {exc}"
        ) from exc
    if target.triple != policy.installed_target:
        raise WindowsChannelError(
            f"the Windows channels name {policy.installed_target!r}, but a host "
            f"of that shape installs {target.triple!r}"
        )
    return artifact_for(metadata, target)


def required_windows_evidence(repository_root: Path) -> tuple[str, ...]:
    """What a *stable* Windows artifact has to prove before a channel takes it.

    Read from `release-trust.json` rather than restated here. The platform-trust
    gate already decides what signed means on Windows, and a channel fixture
    that carried its own list would be a second place to relax it — one that
    could disagree with the gate without either of them failing.
    """
    policy = load_trust_policy(repository_root)
    mechanism = policy.mechanism_for("windows")
    if mechanism is None:
        raise WindowsChannelError(
            "the trust policy declares no Windows signing mechanism, so nothing "
            "here can say what a signed Windows artifact proves"
        )
    return tuple(mechanism.required_evidence["stable"])


def signing_identity(
    repository_root: Path,
    receipt_dir: Path,
    *,
    release_version: str,
) -> str:
    """Who signed the Windows artifact this Release published.

    On Windows the operator is not shown a digest; they are shown a publisher.
    That name is the subject of the Authenticode certificate the release runner
    observed on the artifact it had just signed, recorded in the Release's own
    trust receipt — so it is read back from there rather than declared, and a
    Release whose Windows artifact is unsigned, unattributable, or receipted for
    a *different* Release never reaches either channel.

    The receipt is asked rather than assumed. The platform-trust gate is what
    makes a stable Release refuse an unsigned artifact, and a channel that
    inferred signing from "the Release exists" would be trusting a gate it never
    consulted.
    """
    policy = load_channel_policy(repository_root)
    try:
        receipt = read_trust_receipt(
            repository_root, receipt_dir, policy.installed_target
        )
    except ReleaseTrustError as exc:
        raise WindowsChannelError(str(exc)) from exc

    if receipt.release_version != release_version:
        raise WindowsChannelError(
            f"{receipt.archive_name}: trust receipt names Release version "
            f"{receipt.release_version!r}, expected {release_version!r}"
        )
    if not receipt.signed:
        raise WindowsChannelError(
            f"{receipt.archive_name} is not signed, and a Windows package "
            "manager installs it without anyone reading the bytes"
        )
    for kind in required_windows_evidence(repository_root):
        if kind == "checksum":
            continue
        if not receipt.holds(kind):
            raise WindowsChannelError(
                f"{receipt.archive_name}: the Release published no {kind} for "
                "the artifact these channels install"
            )

    identity = str(receipt.evidence["publisher_identity"]).strip()
    if not identity:
        raise WindowsChannelError(
            f"{receipt.archive_name}: publisher_identity is empty, so nothing "
            "here can say who a channel would be publishing under"
        )
    return identity


def _distinguished_name_components(subject: str) -> list[tuple[str, str]]:
    """One certificate subject, split the way the runtime that wrote it splits it.

    `publisher_identity` is `Get-AuthenticodeSignature`'s
    `SignerCertificate.Subject`, which is .NET's `X509Certificate2.Subject`, and
    that renders a distinguished name in one observable grammar: a value is bare
    unless it needs quoting, in which case it is wrapped in `"` and any `"`
    inside it is doubled. So a comma only separates components when it is
    outside quotes — `CN="Contoso, Inc."` is one component, not two.

    Splitting on every comma instead is the difference between publishing a
    helper under `Contoso, Inc.` and publishing it under `"Contoso`.
    """
    components: list[tuple[str, str]] = []
    name: list[str] = []
    value: list[str] = []
    seen_separator = False
    quoted = False
    index = 0
    while index < len(subject):
        character = subject[index]
        if quoted:
            if character == '"':
                if subject[index + 1 : index + 2] == '"':
                    value.append('"')
                    index += 2
                    continue
                quoted = False
            else:
                value.append(character)
        elif character == '"' and seen_separator and not "".join(value).strip():
            quoted = True
            value = []
        elif character == "=" and not seen_separator:
            seen_separator = True
        elif character == ",":
            components.append(("".join(name).strip(), "".join(value).strip()))
            name, value, seen_separator = [], [], False
        elif seen_separator:
            value.append(character)
        else:
            name.append(character)
        index += 1
    components.append(("".join(name).strip(), "".join(value).strip()))
    return [(name, value) for name, value in components if name]


def publisher_display_name(certificate_subject: str) -> str:
    """The publisher an operator is shown, out of the certificate subject.

    winget's `Publisher` is a display name and the receipt records a whole
    distinguished name, so the common name is lifted out. A subject with no `CN`
    passes through whole rather than emptied: an unusual certificate should be
    published under an ugly name it can be checked against, not under none.
    """
    for name, value in _distinguished_name_components(certificate_subject):
        if name.upper() == "CN" and value:
            return value
    return certificate_subject.strip()


def _yaml_scalar(value: str) -> str:
    """One string, as a YAML scalar that cannot be read as anything else.

    Every JSON string escape is also a YAML double-quoted escape, so `json.dumps`
    is a correct quoter here and a hand-rolled one would be one more thing that
    can be subtly wrong about a publisher name nobody chose.
    """
    return json.dumps(value)


_WINGET_HEADER = (
    "# yaml-language-server: $schema=https://aka.ms/winget-manifest.{kind}"
    ".{manifest_version}.schema.json"
)

_GENERATED_NOTICE = (
    "Generated from the completed git-loopy v{version} Release by "
    "`python -m git_loopy.windows_channels render --channel {channel}`. Do not "
    "edit by hand: the URL, digest, and publisher here are proven against that "
    "Release, and a hand edit is exactly the drift `verify` refuses."
)


def _generated_comment(release_version: str, channel: str, prefix: str) -> list[str]:
    """The "do not edit by hand" notice, wrapped, in one format's comment form."""
    notice = _GENERATED_NOTICE.format(version=release_version, channel=channel)
    lines: list[str] = []
    current = ""
    for word in notice.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and len(prefix) + len(candidate) > 79:
            lines.append(prefix + current)
            current = word
        else:
            current = candidate
    lines.append(prefix + current)
    return lines


@dataclass(frozen=True)
class ManifestFacts:
    """The three things a Windows channel says about one completed Release.

    One artifact, so one URL, one digest, and one publisher. Both channels write
    these same facts down; only the file format differs, which is why they are
    resolved once rather than once per channel.
    """

    artifact: PublishedArtifact
    url: str
    digest: str
    publisher: str


def manifest_facts(
    repository_root: Path,
    *,
    release_version: str,
    checksum_dir: Path,
    receipt_dir: Path,
) -> ManifestFacts:
    """Everything both Windows channels publish about one completed Release."""
    metadata = load_artifact_metadata(repository_root)
    policy = load_channel_policy(repository_root)
    artifact = windows_artifact(metadata, policy)

    try:
        digest = published_digest(
            checksum_dir / artifact.checksum_name, artifact.archive_name
        )
    except TuiReleaseError as exc:
        raise WindowsChannelError(
            f"the Release publishes no usable checksum for "
            f"{artifact.archive_name}: {exc}"
        ) from exc

    return ManifestFacts(
        artifact=artifact,
        url=release_artifact_url(
            metadata, release_version=release_version, artifact=artifact.archive_name
        ),
        digest=digest,
        publisher=publisher_display_name(
            signing_identity(
                repository_root, receipt_dir, release_version=release_version
            )
        ),
    )


def version_probe(policy: ChannelPolicy, release_version: str) -> list[str]:
    """The check that runs on the operator's own machine, not in CI.

    Scoop's `post_install` is the one hook either channel gives a manifest on the
    installing machine, and it is where a digest that is right about the wrong
    archive stops being invisible. The comparison is whole-string: a substring
    match would accept `1.2.3-rc.1` for `1.2.3`, which is precisely the build a
    stable channel exists to keep out.
    """
    expected = f"{policy.helper_command} {release_version}"
    executable = f"$dir\\{policy.helper_command}.exe"
    return [
        f'$reported = (& "{executable}" --version | Out-String).Trim()',
        f'if ($reported -ne "{expected}") {{',
        f'    throw "$reported is not the {expected} this manifest published"',
        "}",
    ]


def _render_winget(
    policy: ChannelPolicy,
    channel: Channel,
    facts: ManifestFacts,
    release_version: str,
) -> dict[str, str]:
    manifest_version = channel.manifest_version
    locale = channel.default_locale
    if manifest_version is None or locale is None:
        raise WindowsChannelError(
            "the winget channel needs a manifest version and a default locale; "
            "a manifest without either is not one winget will accept"
        )
    header = _generated_comment(release_version, channel.id, "# ")
    identity = [
        f"PackageIdentifier: {_yaml_scalar(channel.package_identifier)}",
        f"PackageVersion: {_yaml_scalar(release_version)}",
    ]
    trailer = [f"ManifestVersion: {_yaml_scalar(manifest_version)}", ""]

    version_manifest = [
        _WINGET_HEADER.format(kind="version", manifest_version=manifest_version),
        *header,
        *identity,
        f"DefaultLocale: {_yaml_scalar(locale)}",
        'ManifestType: "version"',
        *trailer,
    ]

    installer_manifest = [
        _WINGET_HEADER.format(kind="installer", manifest_version=manifest_version),
        *header,
        *identity,
        'InstallerType: "zip"',
        'NestedInstallerType: "portable"',
        "NestedInstallerFiles:",
        f"- RelativeFilePath: {_yaml_scalar(facts.artifact.executable_name)}",
        f"  PortableCommandAlias: {_yaml_scalar(policy.helper_command)}",
        "Installers:",
        f"- Architecture: {_yaml_scalar(channel.installer_architecture)}",
        f"  InstallerUrl: {_yaml_scalar(facts.url)}",
        f"  InstallerSha256: {_yaml_scalar(facts.digest)}",
        'ManifestType: "installer"',
        *trailer,
    ]

    locale_manifest = [
        _WINGET_HEADER.format(kind="defaultLocale", manifest_version=manifest_version),
        *header,
        *identity,
        f"PackageLocale: {_yaml_scalar(locale)}",
        f"Publisher: {_yaml_scalar(facts.publisher)}",
        f"PublisherUrl: {_yaml_scalar(channel.publisher_url)}",
        f"PackageName: {_yaml_scalar(channel.package_name)}",
        f"PackageUrl: {_yaml_scalar(policy.project_url)}",
        f"License: {_yaml_scalar(policy.license)}",
        f"LicenseUrl: {_yaml_scalar(policy.license_url)}",
        f"ShortDescription: {_yaml_scalar(policy.short_description)}",
        f"ReleaseNotesUrl: {_yaml_scalar(policy.release_notes_url(release_version))}",
        'ManifestType: "defaultLocale"',
        *trailer,
    ]

    paths = channel.committed_files(release_version)
    return dict(
        zip(
            paths,
            [
                "\n".join(version_manifest),
                "\n".join(installer_manifest),
                "\n".join(locale_manifest),
            ],
            strict=True,
        )
    )


def _render_scoop(
    policy: ChannelPolicy,
    channel: Channel,
    facts: ManifestFacts,
    release_version: str,
) -> dict[str, str]:
    manifest = {
        "##": _generated_comment(release_version, channel.id, ""),
        "version": release_version,
        "description": policy.short_description,
        "homepage": policy.project_url,
        "license": policy.license,
        "architecture": {
            channel.installer_architecture: {
                "url": facts.url,
                "hash": facts.digest,
            }
        },
        "bin": facts.artifact.executable_name,
        "post_install": version_probe(policy, release_version),
    }
    (path,) = channel.committed_files(release_version)
    return {path: json.dumps(manifest, indent=4) + "\n"}


def render_manifests(
    repository_root: Path,
    channel_id: str,
    *,
    release_version: str,
    release_marked_prerelease: bool,
    checksum_dir: Path,
    receipt_dir: Path,
) -> dict[str, str]:
    """One channel's committed metadata for one completed Release.

    Generated, never authored, and keyed by the path each file is committed at
    so the generator and the gate cannot resolve different files. The URL comes
    from the shared download template, the digest from the Release's own
    published checksum, and the publisher from its own trust receipt — so a
    manifest cannot name an artifact, a hash, or a signer this Release did not
    publish without the generation itself failing first.
    """
    policy = load_channel_policy(repository_root)
    channel = policy.channel(channel_id)
    try:
        require_stable_release(
            release_version,
            release_marked_prerelease=release_marked_prerelease,
            channel=f"the {channel.id} channel",
        )
    except TuiReleaseError as exc:
        raise WindowsChannelError(str(exc)) from exc

    facts = manifest_facts(
        repository_root,
        release_version=release_version,
        checksum_dir=checksum_dir,
        receipt_dir=receipt_dir,
    )
    if channel.id == "winget":
        return _render_winget(policy, channel, facts, release_version)
    return _render_scoop(policy, channel, facts, release_version)


@dataclass(frozen=True)
class ChannelClaims:
    """What committed channel metadata says about itself, read back out.

    Verification reads the published text rather than only re-rendering and
    comparing: a re-render tells an operator that two strings differ, while
    reading the claims lets each drift be refused by its own name — a version
    from another Release, an artifact from another platform, a host nobody
    vouches for, a publisher nobody observed.

    Unlike a Homebrew formula, neither of these formats is a programming
    language. YAML and JSON have parsers, and the one this gate uses is the same
    kind winget and Scoop use, so the claims read here are the claims those tools
    will act on. Canonical equality still backs it: what a duplicate key means is
    a property of a parser rather than of a document.
    """

    identifier: str | None
    version: str | None
    url: str | None
    digest: str | None
    publisher: str | None
    probe: str | None


def _yaml_document(path: str, text: str) -> dict[str, Any]:
    """One committed winget manifest, read the way winget reads it.

    PyYAML is imported here rather than at module scope because the runtime
    runner never reads YAML and the base install carries no parser for it. This
    is release automation, which installs the `release` extra; a checkout that
    did not gets told which extra it is missing rather than an ImportError from
    three frames down.
    """
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - release tooling only
        raise WindowsChannelError(
            "reading a committed winget manifest needs a YAML parser: install "
            "the git-loopy Python kit's `release` extra"
        ) from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WindowsChannelError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise WindowsChannelError(f"{path} is not a winget manifest document")
    return document


def _one(values: list[Any], path: str, field: str) -> Any:
    """The single entry of a list winget's schema allows to hold several.

    `Installers` and `NestedInstallerFiles` are sequences, and a second entry is
    another artifact this Release did not publish. Refused rather than indexed:
    a reader that took `[0]` would let anything after it through unseen.
    """
    if len(values) != 1:
        raise WindowsChannelError(
            f"{path} declares {len(values)} entries under {field}, but this "
            "Release publishes one Windows artifact"
        )
    entry = values[0]
    if not isinstance(entry, dict):
        raise WindowsChannelError(f"{path}: {field} entry is not a mapping")
    return entry


def read_winget_claims(files: dict[str, str]) -> ChannelClaims:
    """Parse the identity, installer, digest, and publisher out of a manifest set."""
    identifiers: set[str] = set()
    versions: set[str] = set()
    installer: dict[str, Any] = {}
    publisher: str | None = None

    for path, text in sorted(files.items()):
        document = _yaml_document(path, text)
        identifiers.add(str(document.get("PackageIdentifier")))
        versions.add(str(document.get("PackageVersion")))
        if document.get("ManifestType") == "installer":
            installer = _one(list(document.get("Installers") or []), path, "Installers")
        elif document.get("ManifestType") == "defaultLocale":
            found = document.get("Publisher")
            publisher = None if found is None else str(found)

    if len(identifiers) != 1 or len(versions) != 1:
        raise WindowsChannelError(
            "the winget manifests disagree about what package they describe: "
            f"identifiers {sorted(identifiers)}, versions {sorted(versions)}"
        )
    url = installer.get("InstallerUrl")
    digest = installer.get("InstallerSha256")
    return ChannelClaims(
        identifier=identifiers.pop(),
        version=versions.pop(),
        url=None if url is None else str(url),
        digest=None if digest is None else str(digest),
        publisher=publisher,
        probe=None,
    )


def read_scoop_claims(files: dict[str, str], channel: Channel) -> ChannelClaims:
    """Parse the version, URL, hash, and version probe out of a bucket manifest."""
    ((path, text),) = files.items()
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WindowsChannelError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise WindowsChannelError(f"{path} is not a Scoop manifest document")

    architecture = document.get("architecture")
    if not isinstance(architecture, dict):
        raise WindowsChannelError(f"{path} declares no per-architecture download")
    slot = architecture.get(channel.installer_architecture)
    if not isinstance(slot, dict):
        raise WindowsChannelError(
            f"{path} installs nothing for {channel.installer_architecture}"
        )

    probe = document.get("post_install")
    if isinstance(probe, list):
        rendered = "\n".join(str(line) for line in probe)
    else:
        rendered = None if probe is None else str(probe)

    url = slot.get("url")
    digest = slot.get("hash")
    return ChannelClaims(
        identifier=channel.package_identifier,
        version=None if document.get("version") is None else str(document["version"]),
        url=None if url is None else str(url),
        digest=None if digest is None else str(digest),
        publisher=None,
        probe=rendered,
    )


def read_claims(channel: Channel, files: dict[str, str]) -> ChannelClaims:
    """What one channel's committed metadata says, in that channel's format."""
    if channel.id == "winget":
        return read_winget_claims(files)
    return read_scoop_claims(files, channel)


def verify_manifests(
    repository_root: Path,
    channel_id: str,
    files: dict[str, str],
    *,
    release_version: str,
    release_marked_prerelease: bool,
    checksum_dir: Path,
    receipt_dir: Path,
) -> PublishedArtifact:
    """Refuse channel metadata that could install anything but this Release.

    Four independent things can drift and each is proven separately: the package
    an operator asks for, the version the metadata claims, the artifact it
    fetches, and the digest it accepts for those bytes. winget also states a
    publisher, which on Windows is the one thing an operator is shown instead of
    a hash, so it is proven against the certificate the release runner observed.
    Scoop states a version probe — the only check that runs on the operator's own
    machine, where an upgrade to a stale archive would otherwise be silent.

    Reading the claims is how each realistic drift earns its own refusal, but it
    cannot be what makes the gate sound. The committed text must also be exactly
    the text this Release generates; anything else is refused whether or not the
    reader understood it.
    """
    policy = load_channel_policy(repository_root)
    channel = policy.channel(channel_id)
    canonical = render_manifests(
        repository_root,
        channel_id,
        release_version=release_version,
        release_marked_prerelease=release_marked_prerelease,
        checksum_dir=checksum_dir,
        receipt_dir=receipt_dir,
    )
    facts = manifest_facts(
        repository_root,
        release_version=release_version,
        checksum_dir=checksum_dir,
        receipt_dir=receipt_dir,
    )

    missing = sorted(set(canonical) - set(files))
    if missing:
        raise WindowsChannelError(
            f"the {channel.id} channel publishes nothing at {', '.join(missing)}, "
            "so what an operator installs is not what this gate read"
        )
    claims = read_claims(channel, {path: files[path] for path in canonical})

    if claims.identifier != channel.package_identifier:
        raise WindowsChannelError(
            f"the {channel.id} channel declares package identifier "
            f"{claims.identifier!r}, but this project publishes "
            f"{channel.package_identifier!r}"
        )
    if claims.version != release_version:
        raise WindowsChannelError(
            f"the {channel.id} channel declares version {claims.version!r}, but "
            f"the completed Release published {release_version!r}"
        )
    if claims.url is None or not claims.url.startswith(
        policy.trusted_artifact_url_prefix
    ):
        raise WindowsChannelError(
            f"the {channel.id} channel fetches from an untrusted artifact host: "
            f"{claims.url} is outside {policy.trusted_artifact_url_prefix}"
        )
    if claims.url != facts.url:
        raise WindowsChannelError(
            f"the {channel.id} channel installs {claims.url}, which is not this "
            f"Release's Windows artifact ({facts.url})"
        )
    if claims.digest != facts.digest:
        raise WindowsChannelError(
            f"the {channel.id} channel's digest {claims.digest!r} is not the one "
            f"the Release published for {facts.artifact.archive_name}"
        )
    if channel.id == "winget" and claims.publisher != facts.publisher:
        raise WindowsChannelError(
            f"the {channel.id} channel credits publisher {claims.publisher!r}, "
            f"but the Release's Windows artifact was signed by {facts.publisher!r}"
        )
    if channel.id == "scoop":
        expected = "\n".join(version_probe(policy, release_version))
        if claims.probe != expected:
            raise WindowsChannelError(
                f"the {channel.id} channel never proves the installed helper "
                f"reports {policy.helper_command} {release_version}: its "
                "post_install must run --version and compare the whole answer"
            )

    for path, expected_text in canonical.items():
        if files[path] == expected_text:
            continue
        divergence = next(
            (
                published
                for published, generated in zip(
                    files[path].splitlines(), expected_text.splitlines()
                )
                if published != generated
            ),
            f"{path} is longer or shorter than the one this Release generates",
        )
        raise WindowsChannelError(
            f"{path} is not the text this Release generates, so what it does is "
            f"not what this gate read: {divergence.strip()!r}"
        )
    return facts.artifact


def _boolean_argument(value: str) -> bool:
    """A flag read back off the Release, refused rather than guessed.

    `bool("false")` is `True`, and the safe reading of "we could not tell which
    channel this is" is not "stable". A deleted step, a renamed output, or a
    failed API call reaches this as an empty or unexpected string and is a usage
    error, not a default.
    """
    normalized = value.strip().lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    raise argparse.ArgumentTypeError(
        f"expected 'true' or 'false' from the Release, found {value!r}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m git_loopy.windows_channels",
        description=(
            "Generate and verify the winget and Scoop channels for a completed Release."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--channel",
        required=True,
        help="which Windows package manager's metadata to write or prove",
    )
    common.add_argument(
        "--repository-root",
        type=Path,
        default=Path("."),
        help="the git-loopy checkout holding the shared channel policy",
    )
    common.add_argument(
        "--release-version",
        required=True,
        help="the completed Release this metadata is generated from",
    )
    common.add_argument(
        "--checksum-dir",
        type=Path,
        required=True,
        help="the .sha256 manifests downloaded from that Release",
    )
    common.add_argument(
        "--receipt-dir",
        type=Path,
        required=True,
        help=(
            "the .trust.json receipts downloaded from that Release; the Windows "
            "one is what says who signed the artifact these channels install"
        ),
    )
    common.add_argument(
        "--release-marked-prerelease",
        required=True,
        type=_boolean_argument,
        help=(
            "the prerelease flag the completed GitHub Release carries; required "
            "because a channel resolves 'the stable Release' and cannot infer it"
        ),
    )
    common.add_argument(
        "--channel-root",
        type=Path,
        required=True,
        help="the channel repository checkout; paths inside it are policy",
    )

    commands.add_parser(
        "render",
        parents=[common],
        help="write the channel metadata this Release publishes",
    )
    commands.add_parser(
        "verify",
        parents=[common],
        help="refuse metadata that could install another Release",
    )
    return parser


def committed_files(
    repository_root: Path,
    channel_root: Path,
    channel_id: str,
    release_version: str,
) -> dict[str, Path]:
    """Where one channel's metadata lives inside its repository checkout.

    Stated once, in the policy, so the generator, the gate, and the job that
    commits the result cannot each resolve a different file — a channel where one
    of them wrote somewhere else is a channel that verifies metadata nobody
    installs.
    """
    channel = load_channel_policy(repository_root).channel(channel_id)
    return {
        path: channel_root / path for path in channel.committed_files(release_version)
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the winget/Scoop channel generator and its drift gate."""
    args = _build_parser().parse_args(argv)
    try:
        locations = committed_files(
            args.repository_root,
            args.channel_root,
            args.channel,
            args.release_version,
        )
        if args.command == "render":
            rendered = render_manifests(
                args.repository_root,
                args.channel,
                release_version=args.release_version,
                release_marked_prerelease=args.release_marked_prerelease,
                checksum_dir=args.checksum_dir,
                receipt_dir=args.receipt_dir,
            )
            for path, text in rendered.items():
                written = locations[path]
                written.parent.mkdir(parents=True, exist_ok=True)
                written.write_text(text, encoding="utf-8")
                print(written)
        else:
            published: dict[str, str] = {}
            for path, location in locations.items():
                try:
                    published[path] = location.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise WindowsChannelError(
                        f"cannot read channel metadata {location}: {exc}"
                    ) from exc
            artifact = verify_manifests(
                args.repository_root,
                args.channel,
                published,
                release_version=args.release_version,
                release_marked_prerelease=args.release_marked_prerelease,
                checksum_dir=args.checksum_dir,
                receipt_dir=args.receipt_dir,
            )
            print(artifact.archive_name)
    except WindowsChannelError as exc:
        print(f"Windows channel verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
