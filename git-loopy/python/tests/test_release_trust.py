"""The platform-trust gate a stable helper Release has to pass.

`git-loopy/conformance/release-trust.json` is the one place that says what a
signed Release means on each platform: which mechanism signs it, which secrets
that mechanism needs, which of them a pull request may never see, and which
evidence a *stable* Release refuses to publish without. It is data only, so this
suite drives the production seam in `git_loopy.release_trust` rather than
restating the fixture, and pins the static release configuration against it so a
workflow that stopped signing fails here instead of shipping.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import release_trust, tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "git-loopy/conformance/release-trust.json"
FIXTURE: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
RELEASE_VERSION_FIXTURE: dict[str, Any] = json.loads(
    (REPOSITORY_ROOT / "git-loopy/conformance/release-version.json").read_text(
        encoding="utf-8"
    )
)


def test_every_published_target_has_a_declared_signing_mechanism() -> None:
    """A target with no declared mechanism is a target nobody decided about."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    for target in metadata.targets:
        mechanism = policy.mechanism_for(target.os)
        assert mechanism is not None, (
            f"{target.triple} builds on {target.os}, which declares no signing "
            "mechanism"
        )
        assert mechanism.platform == target.os


@pytest.mark.parametrize(
    "case",
    RELEASE_VERSION_FIXTURE["publication_cases"],
    ids=lambda case: case["id"],
)
def test_the_trust_channel_follows_publication_s_own_prerelease_rule(
    case: dict[str, Any],
) -> None:
    """One Release is not stable for signing and prerelease for publication.

    `release-version.json` already decides which versions publish as
    prereleases. Deriving the trust channel from a second rule would let a
    Release be marked prerelease on GitHub while the signing gate held it to
    stable requirements, or — far worse — the other way round.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)

    channel = policy.channel_for(case["version"])

    assert policy.prerelease_channels[channel] is case["prerelease"]


_EVIDENCE_VALUES: dict[str, Any] = {
    "signature": "Developer ID Application: Example Corp (TEAMID1234)",
    "hardened_runtime": True,
    "notarization": "Accepted",
    "publisher_identity": "CN=Example Corp, O=Example Corp, C=US",
}


def _fully_signed_evidence(platform: str) -> dict[str, Any]:
    """Everything a correctly signed artifact of ``platform`` carries.

    Read off the policy rather than restated per platform, so a new evidence
    kind cannot be added to the gate and quietly satisfied by a harness that
    still supplies the old set.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    mechanism = policy.mechanism_for(platform)
    assert mechanism is not None
    return {
        kind: _EVIDENCE_VALUES[kind]
        for kind in sorted(mechanism.required_evidence["stable"] - {"checksum"})
    }


def _stage_release_set(
    directory: Path, *, version: str, defect: dict[str, Any] | None
) -> Path:
    """Lay out one complete Release set, then introduce the case's defect."""
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    defect = defect or {}
    directory.mkdir(parents=True, exist_ok=True)

    for artifact in tui_release.published_artifacts(metadata):
        platform = artifact.target.os
        damaged = defect.get("platform") == platform
        kind = defect.get("kind") if damaged else None

        archive = directory / artifact.archive_name
        archive.write_bytes(artifact.archive_name.encode("utf-8"))
        if kind != "missing-checksum":
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (directory / artifact.checksum_name).write_text(
                f"{digest}  {artifact.archive_name}\n", encoding="utf-8"
            )
        if kind == "missing-receipt":
            continue

        evidence = {} if kind == "unsigned" else _fully_signed_evidence(platform)
        if kind == "omit-evidence":
            evidence.pop(defect["evidence"], None)
        release_trust.write_trust_receipt(
            REPOSITORY_ROOT,
            directory,
            artifact.target.triple,
            release_version="9.9.9" if kind == "version-drift" else version,
            signed=kind != "unsigned",
            evidence=evidence,
        )
    return directory


@pytest.mark.parametrize(
    "case", FIXTURE["publication_cases"], ids=lambda case: case["id"]
)
def test_publication_carries_the_trust_its_channel_requires(
    case: dict[str, Any], tmp_path: Path
) -> None:
    """AC5: stable publication fails closed on any missing piece of trust."""
    artifacts = _stage_release_set(
        tmp_path / "release-artifacts", version=case["version"], defect=case["defect"]
    )
    attestation = tmp_path / "attestation.jsonl"
    if case["attestation"]:
        attestation.write_text("{}\n", encoding="utf-8")

    if case["allowed"]:
        receipts = release_trust.verify_release_trust(
            REPOSITORY_ROOT,
            artifacts,
            version=case["version"],
            attestation=attestation if case["attestation"] else None,
        )
        assert len(receipts) == len(
            tui_release.load_artifact_metadata(REPOSITORY_ROOT).targets
        )
        return

    with pytest.raises(release_trust.ReleaseTrustError) as raised:
        release_trust.verify_release_trust(
            REPOSITORY_ROOT,
            artifacts,
            version=case["version"],
            attestation=attestation if case["attestation"] else None,
        )
    assert case["error"] in str(raised.value)


class _FakeTool:
    """A recorded platform tool, so observation is exercised off-platform."""

    def __init__(self, replies: dict[str, tuple[int, str, str]]) -> None:
        self._replies = replies
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> tuple[int, str, str]:
        self.commands.append(list(command))
        for marker, reply in self._replies.items():
            if marker in command:
                return reply
        return (1, "", f"unexpected command: {command}")


_CODESIGN_DISPLAY = (
    "Executable=/tmp/git-loopy-tui\n"
    "Identifier=git-loopy-tui\n"
    "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=12+7\n"
    "Signature size=9000\n"
    "Authority=Developer ID Application: Example Corp (TEAMID1234)\n"
    "Authority=Developer ID Certification Authority\n"
    "Authority=Apple Root CA\n"
    "TeamIdentifier=TEAMID1234\n"
)


def test_a_signed_macos_binary_proves_its_authority_and_hardened_runtime() -> None:
    """AC1: the gate reads the artifact, it does not take the pipeline's word."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    tool = _FakeTool(
        {
            "--verify": (0, "", "valid on disk\nsatisfies its Designated Requirement\n"),
            "--display": (0, "", _CODESIGN_DISPLAY),
        }
    )

    evidence = release_trust.observe_signed_binary(
        policy, "macos", Path("/tmp/git-loopy-tui"), run=tool
    )

    assert evidence["signature"] == (
        "Developer ID Application: Example Corp (TEAMID1234)"
    )
    assert evidence["hardened_runtime"] is True


def test_an_ad_hoc_signed_macos_binary_proves_neither() -> None:
    """A binary signed with `-` is signed, and worth nothing to Gatekeeper."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    tool = _FakeTool(
        {
            "--verify": (0, "", "valid on disk\n"),
            "--display": (
                0,
                "",
                "CodeDirectory v=20400 size=800 flags=0x2(adhoc) hashes=12+7\n"
                "Signature=adhoc\n",
            ),
        }
    )

    evidence = release_trust.observe_signed_binary(
        policy, "macos", Path("/tmp/git-loopy-tui"), run=tool
    )

    assert "signature" not in evidence
    assert "hardened_runtime" not in evidence


def test_a_signed_windows_binary_proves_a_verifiable_publisher() -> None:
    """AC2: "signed" is worth nothing without a publisher anyone can read."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    tool = _FakeTool(
        {
            "-Command": (
                0,
                json.dumps(
                    {
                        "status": "Valid",
                        "subject": "CN=Example Corp, O=Example Corp, C=US",
                    }
                ),
                "",
            )
        }
    )

    evidence = release_trust.observe_signed_binary(
        policy, "windows", Path("C:/git-loopy-tui.exe"), run=tool
    )

    assert evidence["signature"] == "CN=Example Corp, O=Example Corp, C=US"
    assert evidence["publisher_identity"] == "CN=Example Corp, O=Example Corp, C=US"


def test_an_unsigned_windows_binary_proves_nothing() -> None:
    """cargo-dist skips Windows signing silently when its credentials are unset."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    tool = _FakeTool(
        {"-Command": (0, json.dumps({"status": "NotSigned", "subject": None}), "")}
    )

    evidence = release_trust.observe_signed_binary(
        policy, "windows", Path("C:/git-loopy-tui.exe"), run=tool
    )

    assert evidence == {}


@pytest.mark.parametrize(
    ("status", "recorded"),
    [("Accepted", True), ("Invalid", False), ("Rejected", False)],
)
def test_only_an_accepted_notary_verdict_counts_as_notarization(
    status: str, recorded: bool, tmp_path: Path
) -> None:
    """AC1: notarization is Apple's answer, and only one answer is a pass."""
    exit_code = release_trust.main(
        [
            "record",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--artifact-dir",
            str(tmp_path),
            "--target",
            "aarch64-apple-darwin",
            "--release-version",
            "1.2.3",
            "--notarization-status",
            status,
        ]
    )

    assert exit_code == 0
    receipt = release_trust.read_trust_receipt(
        REPOSITORY_ROOT, tmp_path, "aarch64-apple-darwin"
    )
    assert receipt.holds("notarization") is recorded


def test_the_command_refuses_a_release_that_cannot_prove_its_trust(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = _stage_release_set(
        tmp_path / "release-artifacts",
        version="1.2.3",
        defect={"platform": "windows", "kind": "unsigned", "evidence": None},
    )
    attestation = tmp_path / "attestation.jsonl"
    attestation.write_text("{}\n", encoding="utf-8")

    exit_code = release_trust.main(
        [
            "verify",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--artifact-dir",
            str(artifacts),
            "--release-version",
            "1.2.3",
            "--attestation",
            str(attestation),
        ]
    )

    assert exit_code == 1
    assert "unsigned" in capsys.readouterr().err


def test_the_command_publishes_a_release_that_proves_its_trust(
    tmp_path: Path,
) -> None:
    artifacts = _stage_release_set(
        tmp_path / "release-artifacts", version="1.2.3", defect=None
    )
    attestation = tmp_path / "attestation.jsonl"
    attestation.write_text("{}\n", encoding="utf-8")

    assert (
        release_trust.main(
            [
                "verify",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--artifact-dir",
                str(artifacts),
                "--release-version",
                "1.2.3",
                "--attestation",
                str(attestation),
            ]
        )
        == 0
    )


def test_the_helper_manifest_enables_the_signing_the_policy_declares() -> None:
    """AC1/AC2: signing runs inside `dist build`, before the checksum exists.

    cargo-dist writes each `.sha256` after it signs, so a published checksum is
    a checksum *of the signed artifact*. Signing anywhere later would publish a
    digest that nothing on disk matches.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    configuration = tui_release.helper_dist_configuration(REPOSITORY_ROOT)

    for mechanism in policy.mechanisms:
        if mechanism.cargo_dist_key is None:
            continue
        assert configuration.get(mechanism.cargo_dist_key) == (
            mechanism.cargo_dist_value
        ), (
            f"{mechanism.platform} artifacts are not signed by "
            f"{mechanism.id} during the build"
        )


def test_only_a_hardware_backed_service_signs_a_stable_windows_artifact() -> None:
    """AC2: a certificate a build runner could exfiltrate is not an identity."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    windows = policy.mechanism_for("windows")

    assert windows is not None
    assert "signature" in windows.required_evidence["stable"]
    assert windows.hardware_backed is True


def test_evidence_a_platform_cannot_carry_is_recorded_by_name() -> None:
    """An absent requirement and an impossible one are not the same thing."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)

    for mechanism in policy.mechanisms:
        for unavailable in mechanism.unavailable_evidence:
            assert unavailable.reason.strip()
            assert unavailable.kind not in mechanism.required_evidence["stable"]


def _workflow(policy: release_trust.TrustPolicy) -> dict[str, Any]:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / policy.workflow_path).read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    return workflow


def test_signing_credentials_live_only_in_the_protected_release_environment() -> None:
    """AC4: a pull request enters an environment that holds no credentials."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    jobs = _workflow(policy)["jobs"]

    assert jobs[policy.signing_job]["environment"] == (
        release_trust.signing_environment_expression(policy)
    )
    for name in policy.credential_free_jobs:
        assert "environment" not in jobs[name]
        assert "secrets." not in yaml.safe_dump(jobs[name])


def test_the_release_reads_no_credential_the_trust_policy_does_not_name() -> None:
    """A secret nobody declared is a signing input nobody reviewed."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    text = (REPOSITORY_ROOT / policy.workflow_path).read_text(encoding="utf-8")

    referenced = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))

    assert referenced <= set(policy.credentials), sorted(
        referenced - set(policy.credentials)
    )
    assert referenced, "the release pipeline reads no signing credential at all"


def test_publication_proves_platform_trust_before_it_uploads_anything() -> None:
    """AC5: the last thing that can stop an untrusted Release is this gate."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    publish = _workflow(policy)["jobs"][policy.publication_job]

    assert publish["environment"] == policy.protected_environment

    names = [step.get("name", step.get("uses", "")) for step in publish["steps"]]
    runs = [
        index
        for index, step in enumerate(publish["steps"])
        if "git_loopy.release_trust verify" in str(step.get("run", ""))
    ]
    attests = [
        index
        for index, step in enumerate(publish["steps"])
        if str(step.get("uses", "")).startswith(policy.attestation_action)
    ]
    uploads = [
        index
        for index, step in enumerate(publish["steps"])
        if "gh release upload" in str(step.get("run", ""))
    ]

    assert len(runs) == 1, names
    assert attests and uploads
    assert attests[0] < runs[0] < uploads[0], names


def test_every_native_runner_records_what_it_can_prove_about_its_artifact() -> None:
    """The receipt is written where the evidence is, and nowhere else."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    build = _workflow(policy)["jobs"][policy.signing_job]

    record = [
        step
        for step in build["steps"]
        if "git_loopy.release_trust record" in str(step.get("run", ""))
    ]
    assert len(record) == 1
    assert record[0]["env"]["OBSERVE"] == (
        "${{ matrix.build == 'native' && '--observe' || '' }}"
    )
    assert "$OBSERVE" in record[0]["run"]

    upload = [
        step
        for step in build["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(upload) == 1
    assert "trust.json" in upload[0]["with"]["path"]


def test_observation_refuses_an_artifact_it_cannot_actually_look_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A runner asked to prove a signature must not answer "nothing found"."""
    exit_code = release_trust.main(
        [
            "record",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--artifact-dir",
            str(tmp_path),
            "--target",
            "x86_64-pc-windows-msvc",
            "--release-version",
            "1.2.3",
            "--observe",
        ]
    )

    assert exit_code == 1
    assert "git-loopy-tui-x86_64-pc-windows-msvc.zip" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())
