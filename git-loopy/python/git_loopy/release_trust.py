"""The platform-trust gate a Release has to pass before it is published.

`git-loopy/conformance/release-trust.json` is the single description of what a
*signed* Release means on each platform. It names the mechanism, the credentials
that mechanism needs, and — per channel — the evidence a Release refuses to
publish without. Nothing here decides those rules; this module reads them and
applies them, so a weakened gate is a reviewed fixture change rather than a
quiet edit to a workflow step.

Two properties are load-bearing:

* **Signing happens before the checksum exists.** cargo-dist signs inside
  ``dist build`` and writes each ``.sha256`` afterwards, so a published checksum
  is a checksum *of the signed artifact*. A gate that signed afterwards would
  publish a digest nothing on disk matches.
* **The gate fails closed.** Evidence is proven present, never inferred from a
  tool's silence. cargo-dist's Windows signer, for instance, skips silently when
  its credentials are unset — an unsigned stable artifact would otherwise sail
  through a green build.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from git_loopy import tui_release
from git_loopy.release_version import (
    ReleaseVersionError,
    is_prerelease,
    read_release_version,
)


TRUST_POLICY_PATH = Path("git-loopy/conformance/release-trust.json")


class ReleaseTrustError(ValueError):
    """A Release cannot be proven to carry the trust its channel requires."""


@dataclass(frozen=True)
class UnavailableEvidence:
    """Evidence a platform cannot carry, recorded by name and reason.

    Stated rather than omitted, for the same reason `tui-artifacts.json` defers
    targets by name: an absent requirement and an impossible one look identical
    from the outside, and only one of them is a decision.
    """

    kind: str
    reason: str


@dataclass(frozen=True)
class SigningMechanism:
    """How one platform's artifacts are signed, and what that proves."""

    platform: str
    id: str
    signing_tool: str | None
    hardware_backed: bool
    cargo_dist_key: str | None
    cargo_dist_value: Any
    signature_authority_prefix: str | None
    hardened_runtime_flag: str | None
    hardened_runtime_option_env: str | None
    notarization_accepted_status: str | None
    credentials: tuple[str, ...]
    required_evidence: dict[str, frozenset[str]]
    unavailable_evidence: tuple[UnavailableEvidence, ...]


@dataclass(frozen=True)
class TrustPolicy:
    """The complete platform-trust policy for one distribution."""

    schema_version: int
    workflow_path: str
    credential_free_jobs: tuple[str, ...]
    signing_job: str
    publication_job: str
    protected_environment: str
    unprotected_environment: str
    protected_ref_prefix: str
    attestation_action: str
    attestation_channels: frozenset[str]
    receipt_name_template: str
    prerelease_channels: dict[str, bool]
    evidence_kinds: tuple[str, ...]
    mechanisms: tuple[SigningMechanism, ...]

    def mechanism_for(self, platform: str) -> SigningMechanism | None:
        """The mechanism that signs ``platform``, or ``None`` if undeclared."""
        for mechanism in self.mechanisms:
            if mechanism.platform == platform:
                return mechanism
        return None

    def channel_for(self, version: str) -> str:
        """The trust channel ``version`` publishes on.

        Derived from the same rule publication uses, so a Release cannot be a
        prerelease on GitHub and a stable Release at the signing gate.
        """
        wanted = is_prerelease(version)
        for channel, prerelease in self.prerelease_channels.items():
            if prerelease is wanted:
                return channel
        raise ReleaseTrustError(
            f"the trust policy declares no channel for version {version!r}"
        )

    @property
    def credentials(self) -> tuple[str, ...]:
        """Every credential name the policy allows a signing job to see."""
        names: list[str] = []
        for mechanism in self.mechanisms:
            names.extend(mechanism.credentials)
        return tuple(names)


def _read_policy_document(repository_root: Path) -> dict[str, Any]:
    path = repository_root / TRUST_POLICY_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseTrustError(f"cannot read trust policy {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseTrustError(f"trust policy {path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ReleaseTrustError(f"trust policy {path} must be a JSON object")
    return document


def load_trust_policy(repository_root: Path) -> TrustPolicy:
    """Read the declared platform-trust policy for this distribution."""
    document = _read_policy_document(repository_root)
    mechanisms = tuple(
        SigningMechanism(
            platform=entry["platform"],
            id=entry["id"],
            signing_tool=entry["signing_tool"],
            hardware_backed=bool(entry["hardware_backed"]),
            cargo_dist_key=entry["cargo_dist_key"],
            cargo_dist_value=entry["cargo_dist_value"],
            signature_authority_prefix=entry["signature_authority_prefix"],
            hardened_runtime_flag=entry["hardened_runtime_flag"],
            hardened_runtime_option_env=entry["hardened_runtime_option_env"],
            notarization_accepted_status=entry["notarization_accepted_status"],
            credentials=tuple(entry["credentials"]),
            required_evidence={
                channel: frozenset(kinds)
                for channel, kinds in entry["required_evidence"].items()
            },
            unavailable_evidence=tuple(
                UnavailableEvidence(kind=item["kind"], reason=item["reason"])
                for item in entry["unavailable_evidence"]
            ),
        )
        for entry in document["mechanisms"]
    )
    attestation = document["attestation"]
    workflow = document["workflow"]
    return TrustPolicy(
        schema_version=int(document["schema_version"]),
        workflow_path=workflow["path"],
        credential_free_jobs=tuple(workflow["credential_free_jobs"]),
        signing_job=workflow["signing_job"],
        publication_job=workflow["publication_job"],
        protected_environment=workflow["protected_environment"],
        unprotected_environment=workflow["unprotected_environment"],
        protected_ref_prefix=workflow["protected_ref_prefix"],
        attestation_action=attestation["action"],
        attestation_channels=frozenset(attestation["required_channels"]),
        receipt_name_template=document["receipt_name_template"],
        prerelease_channels={
            channel["id"]: bool(channel["prerelease"])
            for channel in document["channels"]
        },
        evidence_kinds=tuple(document["evidence_kinds"]),
        mechanisms=mechanisms,
    )


@dataclass(frozen=True)
class TrustReceipt:
    """What one built artifact proved about itself on its own release runner.

    The receipt is written where the evidence exists — a macOS runner is the
    only machine that can ask `codesign` what it just signed — and read where
    the decision is made. Publication therefore never has to infer trust from a
    step that did not fail.
    """

    target: str
    platform: str
    archive_name: str
    release_version: str
    signed: bool
    mechanism: str | None
    evidence: dict[str, Any]

    def holds(self, kind: str) -> bool:
        """Whether this receipt actually carries ``kind`` of evidence."""
        value = self.evidence.get(kind)
        return value is not False and value is not None and value != ""


def _artifact_for_triple(
    repository_root: Path, triple: str
) -> tui_release.PublishedArtifact:
    metadata = tui_release.load_artifact_metadata(repository_root)
    for artifact in tui_release.published_artifacts(metadata):
        if artifact.target.triple == triple:
            return artifact
    raise ReleaseTrustError(f"{triple} is not one of this Release's published targets")


def receipt_path(
    repository_root: Path, artifact_directory: Path, triple: str
) -> Path:
    """Where ``triple``'s trust receipt lives beside its archive."""
    policy = load_trust_policy(repository_root)
    artifact = _artifact_for_triple(repository_root, triple)
    return artifact_directory / policy.receipt_name_template.format(
        archive=artifact.archive_name
    )


def write_trust_receipt(
    repository_root: Path,
    artifact_directory: Path,
    triple: str,
    *,
    release_version: str,
    signed: bool,
    evidence: dict[str, Any],
) -> Path:
    """Record what one release runner observed about its own artifact."""
    policy = load_trust_policy(repository_root)
    artifact = _artifact_for_triple(repository_root, triple)
    platform = artifact.target.os
    mechanism = policy.mechanism_for(platform)
    if mechanism is None:
        raise ReleaseTrustError(
            f"{triple} builds on {platform}, which declares no signing mechanism"
        )
    unknown = sorted(set(evidence) - set(policy.evidence_kinds))
    if unknown:
        raise ReleaseTrustError(
            f"{triple} recorded evidence the trust policy does not name: {unknown}"
        )

    path = receipt_path(repository_root, artifact_directory, triple)
    path.write_text(
        json.dumps(
            {
                "schema_version": policy.schema_version,
                "target": triple,
                "platform": platform,
                "archive": artifact.archive_name,
                "release_version": release_version,
                "signed": bool(signed),
                "mechanism": mechanism.id if signed else None,
                "evidence": evidence,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def read_trust_receipt(
    repository_root: Path, artifact_directory: Path, triple: str
) -> TrustReceipt:
    """Read one artifact's trust receipt, refusing anything unreadable."""
    path = receipt_path(repository_root, artifact_directory, triple)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseTrustError(
            f"{path.name}: no trust receipt was published for {triple}"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseTrustError(f"unusable trust receipt {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ReleaseTrustError(f"unusable trust receipt {path}: not a JSON object")

    evidence = document.get("evidence")
    return TrustReceipt(
        target=str(document.get("target", "")),
        platform=str(document.get("platform", "")),
        archive_name=str(document.get("archive", "")),
        release_version=str(document.get("release_version", "")),
        signed=bool(document.get("signed", False)),
        mechanism=document.get("mechanism"),
        evidence=dict(evidence) if isinstance(evidence, dict) else {},
    )


def _verify_one_artifact(
    repository_root: Path,
    artifact_directory: Path,
    artifact: tui_release.PublishedArtifact,
    *,
    policy: TrustPolicy,
    channel: str,
    version: str,
) -> tuple[TrustReceipt | None, list[str]]:
    problems: list[str] = []
    platform = artifact.target.os
    mechanism = policy.mechanism_for(platform)
    if mechanism is None:
        return None, [
            f"{artifact.archive_name}: {platform} declares no signing mechanism"
        ]

    held: set[str] = set()
    archive = artifact_directory / artifact.archive_name
    checksum = artifact_directory / artifact.checksum_name
    if not archive.is_file():
        problems.append(f"{artifact.archive_name}: the release set is missing it")
    elif not checksum.is_file():
        problems.append(
            f"{artifact.archive_name}: no published checksum "
            f"({artifact.checksum_name})"
        )
    else:
        try:
            tui_release.verify_checksum(archive, checksum)
        except tui_release.TuiReleaseError as exc:
            problems.append(f"{artifact.archive_name}: {exc}")
        else:
            held.add("checksum")

    try:
        receipt = read_trust_receipt(
            repository_root, artifact_directory, artifact.target.triple
        )
    except ReleaseTrustError as exc:
        return None, problems + [str(exc)]

    if receipt.release_version != version:
        problems.append(
            f"{artifact.archive_name}: trust receipt names Release version "
            f"{receipt.release_version!r}, expected {version!r}"
        )
    if receipt.target != artifact.target.triple:
        problems.append(
            f"{artifact.archive_name}: trust receipt names target "
            f"{receipt.target!r}, expected {artifact.target.triple!r}"
        )

    required = mechanism.required_evidence.get(channel, frozenset())
    # An unsigned artifact is named as such rather than left to be inferred from
    # the evidence it happens to be missing. cargo-dist's Windows signer skips
    # silently when its credentials are absent, so "no signature evidence" is a
    # state a perfectly green build reaches.
    if "signature" in required and not receipt.signed:
        problems.append(
            f"{artifact.archive_name} is unsigned; a {channel} Release may not "
            f"ship an unsigned {platform} artifact"
        )
    held.update(kind for kind in policy.evidence_kinds if receipt.holds(kind))
    missing = sorted(required - held)
    if missing:
        problems.append(
            f"{artifact.archive_name}: a {channel} Release requires "
            f"{', '.join(missing)} for {platform}, and it was not proven"
        )
    return receipt, problems


def verify_release_trust(
    repository_root: Path,
    artifact_directory: Path,
    *,
    version: str,
    release_marked_prerelease: bool,
    attestation: Path | None = None,
) -> tuple[TrustReceipt, ...]:
    """Prove the complete Release carries the trust its channel requires.

    Every declared target is checked and every problem is reported together:
    publication is all-or-nothing, so learning about one unsigned artifact per
    re-run would turn a single refusal into a sequence of them.

    ``release_marked_prerelease`` is the marking read back off the GitHub
    Release the artifacts are about to attach to. It is a required argument
    rather than an optional cross-check because the whole of the unsigned
    allowance rests on it: a caller that could omit it could publish an
    unsigned artifact to a Release that says nothing about being one.
    """
    policy = load_trust_policy(repository_root)
    channel = policy.channel_for(version)
    metadata = tui_release.load_artifact_metadata(repository_root)

    receipts: list[TrustReceipt] = []
    problems: list[str] = []

    # The channel decides which evidence is optional; the marking is what tells
    # an operator — and every package channel that resolves "the stable
    # Release" — which channel they are installing from. A version that says
    # prerelease and a Release that does not are two answers to one question,
    # and the lenient half of the gate was applied to the wrong one.
    expected_marking = policy.prerelease_channels[channel]
    if release_marked_prerelease is not expected_marking:
        was = "is" if release_marked_prerelease else "is not"
        problems.append(
            f"version {version} publishes on the {channel} channel, but the "
            f"GitHub Release it attaches to {was} marked as a prerelease"
        )

    for artifact in tui_release.published_artifacts(metadata):
        receipt, artifact_problems = _verify_one_artifact(
            repository_root,
            artifact_directory,
            artifact,
            policy=policy,
            channel=channel,
            version=version,
        )
        problems.extend(artifact_problems)
        if receipt is not None:
            receipts.append(receipt)

    if channel in policy.attestation_channels:
        if attestation is None or not attestation.is_file():
            problems.append(
                f"a {channel} Release must publish a build attestation from "
                f"{policy.attestation_action}, and no attestation bundle was "
                "provided"
            )

    if problems:
        raise ReleaseTrustError("; ".join(problems))
    return tuple(receipts)


ToolRunner = Callable[[list[str]], tuple[int, str, str]]


def run_tool(command: list[str]) -> tuple[int, str, str]:
    """Run one platform tool, returning ``(returncode, stdout, stderr)``."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ReleaseTrustError(f"cannot run {command[0]}: {exc}") from exc
    return completed.returncode, completed.stdout, completed.stderr


def _observe_macos(
    mechanism: SigningMechanism, binary: Path, run: ToolRunner
) -> dict[str, Any]:
    tool = mechanism.signing_tool or "codesign"
    verified, _, _ = run([tool, "--verify", "--strict", "--verbose=2", str(binary)])
    if verified != 0:
        return {}

    _, stdout, stderr = run([tool, "--display", "--verbose=4", str(binary)])
    # `codesign --display` reports on stderr, but has moved before; reading both
    # costs nothing and removes a whole class of "the gate saw an empty string".
    report = f"{stdout}\n{stderr}"

    evidence: dict[str, Any] = {}
    prefix = mechanism.signature_authority_prefix
    for line in report.splitlines():
        authority = line.partition("Authority=")
        if authority[1] and (prefix is None or authority[2].startswith(prefix)):
            evidence["signature"] = authority[2].strip()
            break

    flag = mechanism.hardened_runtime_flag
    if flag is not None:
        for line in report.splitlines():
            marker = line.partition("flags=")
            if marker[1] and flag in marker[2]:
                evidence["hardened_runtime"] = True
                break
    return evidence


_AUTHENTICODE_QUERY = (
    "$ErrorActionPreference = 'Stop'; "
    "$signature = Get-AuthenticodeSignature -LiteralPath {path}; "
    "[pscustomobject]@{{ status = $signature.Status.ToString(); "
    "subject = $signature.SignerCertificate.Subject }} | ConvertTo-Json -Compress"
)


def _observe_windows(
    mechanism: SigningMechanism, binary: Path, run: ToolRunner
) -> dict[str, Any]:
    quoted = "'" + str(binary).replace("'", "''") + "'"
    code, stdout, _ = run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _AUTHENTICODE_QUERY.format(path=quoted),
        ]
    )
    if code != 0:
        return {}
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(report, dict) or report.get("status") != "Valid":
        return {}
    subject = report.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        return {}
    # One observation, two evidence kinds: Authenticode only reports Valid when
    # it could build a chain to a trusted root, and the subject it chained to is
    # the publisher an operator sees in the Windows security prompt.
    return {"signature": subject.strip(), "publisher_identity": subject.strip()}


def observe_signed_binary(
    policy: TrustPolicy,
    platform: str,
    binary: Path,
    *,
    run: ToolRunner = run_tool,
) -> dict[str, Any]:
    """Ask the built artifact itself what it is signed with.

    Observed rather than declared. A release step that passed
    ``--hardened-runtime`` as a literal flag would prove only that someone typed
    it, which is exactly the assurance this gate exists to replace.
    """
    mechanism = policy.mechanism_for(platform)
    if mechanism is None or mechanism.signing_tool is None:
        return {}
    if platform == "macos":
        return _observe_macos(mechanism, binary, run)
    if platform == "windows":
        return _observe_windows(mechanism, binary, run)
    return {}


def collect_evidence(
    policy: TrustPolicy,
    platform: str,
    *,
    binary: Path | None = None,
    notarization_status: str | None = None,
    run: ToolRunner = run_tool,
) -> dict[str, Any]:
    """Everything one release runner can prove about its own artifact."""
    evidence: dict[str, Any] = {}
    if binary is not None:
        evidence.update(observe_signed_binary(policy, platform, binary, run=run))

    mechanism = policy.mechanism_for(platform)
    accepted = mechanism.notarization_accepted_status if mechanism else None
    # Anything other than the one accepted verdict is recorded as no evidence at
    # all. A notary service that answered "Invalid" answered, and a gate that
    # counted an answer rather than the answer would publish it.
    if accepted is not None and notarization_status == accepted:
        evidence["notarization"] = notarization_status
    return evidence


def observe_artifact(
    policy: TrustPolicy,
    artifact_directory: Path,
    artifact: tui_release.PublishedArtifact,
    *,
    observe: bool,
    notarization_status: str | None = None,
    run: ToolRunner = run_tool,
) -> dict[str, Any]:
    """Collect one artifact's evidence, unpacking it when asked to look.

    Observation is opt-in because only a native runner can perform it, and it
    fails closed when it cannot happen: a runner asked to prove a signature and
    unable to find the artifact must say so, not answer "nothing found" in the
    same shape an unsigned artifact would.
    """
    platform = artifact.target.os
    mechanism = policy.mechanism_for(platform)
    if not observe or mechanism is None or mechanism.signing_tool is None:
        return collect_evidence(
            policy, platform, notarization_status=notarization_status, run=run
        )

    archive = artifact_directory / artifact.archive_name
    if not archive.is_file():
        raise ReleaseTrustError(
            f"cannot observe {artifact.archive_name}: it is not in "
            f"{artifact_directory}"
        )
    with tempfile.TemporaryDirectory() as scratch:
        binary = tui_release.extract_helper(archive, artifact, Path(scratch))
        return collect_evidence(
            policy,
            platform,
            binary=binary,
            notarization_status=notarization_status,
            run=run,
        )


def signing_environment_expression(policy: TrustPolicy) -> str:
    """The environment a release job enters, by ref.

    A tagged Release enters the protected environment that holds the signing
    credentials. Everything else — every pull request — enters an environment
    that holds none, which is what makes "unavailable to pull-request jobs" a
    property of the platform rather than of a step's `if:`.
    """
    return (
        "${{ startsWith(github.ref, '"
        f"{policy.protected_ref_prefix}') && '{policy.protected_environment}'"
        f" || '{policy.unprotected_environment}' }}}}"
    )


def hardened_runtime_environment(policy: TrustPolicy) -> dict[str, str]:
    """What the signing step must set so its artifacts are built hardened.

    The value is the mechanism's own ``hardened_runtime_flag`` rather than a
    second copy of it: cargo-dist signs with ``codesign --sign … --options
    $CODESIGN_OPTIONS``, and ``codesign --display`` reports back
    ``flags=…(runtime)``. One string asks for the hardened runtime and the same
    string proves it, so the two ends cannot drift apart.
    """
    return {
        mechanism.hardened_runtime_option_env: mechanism.hardened_runtime_flag
        for mechanism in policy.mechanisms
        if mechanism.hardened_runtime_option_env is not None
        and mechanism.hardened_runtime_flag is not None
    }


def _boolean_argument(value: str) -> bool:
    """Read one of `gh release view --json isPrerelease`'s two answers.

    Rejected rather than coerced. `bool("false")` is `True`, and a marking
    argument that read an unexpected word as "stable" would relax the gate in
    exactly the direction it exists to prevent.
    """
    normalized = value.strip().lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    raise argparse.ArgumentTypeError(
        f"expected 'true' or 'false', not {value!r}; this is the Release's own "
        "prerelease marking, and it may not be guessed"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prove a git-loopy Release carries the trust its channel requires.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="repository root containing the trust policy (default: cwd)",
    )
    common.add_argument("--artifact-dir", type=Path, required=True)
    common.add_argument(
        "--release-version",
        help="the Release version being published (default: the root VERSION)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser(
        "record",
        parents=[common],
        help="record what this release runner observed about its own artifact",
    )
    record.add_argument("--target", required=True)
    record.add_argument(
        "--observe",
        action="store_true",
        help="unpack this artifact and ask it what it is signed with",
    )
    record.add_argument(
        "--notarization-status",
        help="the notary service's verdict for this artifact",
    )

    verify = commands.add_parser(
        "verify",
        parents=[common],
        help="refuse a Release that cannot prove its channel's trust",
    )
    verify.add_argument(
        "--release-marked-prerelease",
        required=True,
        type=_boolean_argument,
        metavar="{true,false}",
        help="whether the GitHub Release being attached to is marked prerelease",
    )
    verify.add_argument(
        "--attestation",
        type=Path,
        help="the published build-provenance attestation bundle",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Release trust gate."""
    args = _build_parser().parse_args(argv)
    try:
        version = args.release_version or read_release_version(
            args.repository_root / "VERSION"
        )
        policy = load_trust_policy(args.repository_root)
        if args.command == "record":
            artifact = _artifact_for_triple(args.repository_root, args.target)
            evidence = observe_artifact(
                policy,
                args.artifact_dir,
                artifact,
                observe=args.observe,
                notarization_status=args.notarization_status,
            )
            path = write_trust_receipt(
                args.repository_root,
                args.artifact_dir,
                args.target,
                release_version=version,
                signed="signature" in evidence,
                evidence=evidence,
            )
            print(path.name)
        else:
            for receipt in verify_release_trust(
                args.repository_root,
                args.artifact_dir,
                version=version,
                release_marked_prerelease=args.release_marked_prerelease,
                attestation=args.attestation,
            ):
                print(receipt.archive_name)
    except (ReleaseTrustError, ReleaseVersionError, tui_release.TuiReleaseError) as exc:
        print(f"Release trust verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
