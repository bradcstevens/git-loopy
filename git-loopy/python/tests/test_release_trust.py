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
    """AC3/AC5: stable publication fails closed on any missing piece of trust.

    The Release's own prerelease marking is one of those pieces. The unsigned
    Windows allowance belongs to the prerelease channel alone, and the marking
    is the only thing that tells an operator — or #196's winget and Scoop
    channels, which resolve "the stable Release" — which channel they are
    installing from. It is written by a deliberately separate workflow and
    stays editable afterwards, so the gate reads it back rather than assuming
    the version string settled it.
    """
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
            release_marked_prerelease=case["release_marked_prerelease"],
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
            release_marked_prerelease=case["release_marked_prerelease"],
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
            "--verify": (
                0,
                "",
                "valid on disk\nsatisfies its Designated Requirement\n",
            ),
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
            "--release-marked-prerelease",
            "false",
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
                "--release-marked-prerelease",
                "false",
                "--attestation",
                str(attestation),
            ]
        )
        == 0
    )


@pytest.mark.parametrize("marking", ["", "no", "0", "unknown", "prerelease"])
def test_the_command_will_not_guess_the_release_marking(
    marking: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC3/AC5: an unreadable marking is a refusal, not a default.

    `gh release view` answers `true` or `false`. Anything else means the
    Release was never read — a deleted step, a renamed output, a failed API
    call — and the safe reading of "we do not know which channel this is" is
    not "stable".
    """
    artifacts = _stage_release_set(
        tmp_path / "release-artifacts", version="1.2.3", defect=None
    )

    with pytest.raises(SystemExit) as raised:
        release_trust.main(
            [
                "verify",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--artifact-dir",
                str(artifacts),
                "--release-version",
                "1.2.3",
                "--release-marked-prerelease",
                marking,
            ]
        )

    assert raised.value.code == 2
    assert "--release-marked-prerelease" in capsys.readouterr().err


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


def _signing_step(policy: release_trust.TrustPolicy) -> dict[str, Any]:
    """The one step that actually signs: cargo-dist's `dist build`."""
    build = _workflow(policy)["jobs"][policy.signing_job]
    steps = [
        step for step in build["steps"] if "dist build" in str(step.get("run", ""))
    ]
    assert len(steps) == 1, [step.get("name") for step in build["steps"]]
    return steps[0]


def test_the_build_asks_for_the_hardened_runtime_the_gate_looks_for() -> None:
    """AC1: the option that hardens the artifact is the flag that proves it.

    cargo-dist signs with `/usr/bin/codesign --sign ... --options
    $CODESIGN_OPTIONS`, so that one environment value is the whole of the
    hardened runtime. Drop it and the artifact still signs, still checksums, and
    still builds green — it only stops being hardened, which nothing observes
    until publication refuses seven finished builds. Pinning it against the flag
    `codesign --display` is read for keeps the two ends one string rather than
    two that happen to agree.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    environment = _signing_step(policy).get("env", {})

    required = release_trust.hardened_runtime_environment(policy)

    assert required, "no mechanism declares how it asks for the hardened runtime"
    for name, value in required.items():
        assert environment.get(name) == value, (
            f"the signing step must set {name}={value!r}; without it macOS "
            "artifacts are signed without the hardened runtime"
        )


def test_the_release_reads_no_credential_the_trust_policy_does_not_name() -> None:
    """A secret nobody declared is a signing input nobody reviewed."""
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    text = (REPOSITORY_ROOT / policy.workflow_path).read_text(encoding="utf-8")

    referenced = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))

    assert referenced <= set(policy.credentials), sorted(
        referenced - set(policy.credentials)
    )
    assert referenced, "the release pipeline reads no signing credential at all"


def test_every_declared_credential_actually_reaches_the_signing_job() -> None:
    """AC2/AC6: a credential that is declared but never wired signs nothing.

    The inverse of the test above, and the one that matters for trust rather
    than for secrecy. cargo-dist's Windows signer carries the literal string
    "skipping codesigning, required SSLDOTCOM env-vars aren't set" and its macOS
    signer warns per target — both exit 0. So breaking one credential's wiring
    stops the signing without stopping the build, and the only thing that would
    notice is the publication gate, seven finished builds later, and only on the
    stable channel.

    #316 put a deliberate indirection in that wiring: on a pull request every
    `secrets.*` renders to the empty string, and cargo-dist reads an empty name
    as *supplied*, so the credentials are staged under names it does not read
    and promoted only when a platform's whole set is present. Both halves are
    followed here — the secret has to reach some staging name, and that staging
    name has to reach the name the signer reads — because a staged credential
    that is never promoted is exactly the silent non-signing this pins against.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    steps = [
        step
        for step in _workflow(policy)["jobs"][policy.signing_job]["steps"]
        if isinstance(step, dict)
    ]

    for mechanism in policy.mechanisms:
        for credential in mechanism.credentials:
            expression = f"${{{{ secrets.{credential} }}}}"
            wiring = [
                (name, str(step.get("run", "")))
                for step in steps
                for name, value in step.get("env", {}).items()
                if value == expression
            ]
            assert len(wiring) == 1, (
                f"{mechanism.id} declares {credential}, but the signing job "
                "never reads it from secrets exactly once"
            )
            name, body = wiring[0]
            assert name == credential or f'export {credential}="${name}"' in body, (
                f"{mechanism.id} stages {credential} as {name}, but never "
                "promotes it to the name the signer reads"
            )


def test_a_credential_written_to_disk_is_removed_on_every_path() -> None:
    """AC4: a failed step must not leave a signing key behind for the next one.

    `notarytool` needs the App Store Connect private key as a *file*, so that
    one credential leaves the environment and lands on the runner's disk. Every
    `run:` block is `bash -e`, so a trailing `rm` runs only when everything
    above it succeeded — precisely the path where the key does not get deleted
    is the path where something already went wrong. Removal has to be armed
    before the key is written, not sequenced after it.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    declared = set(policy.credentials)
    build = _workflow(policy)["jobs"][policy.signing_job]

    materializing = [
        step
        for step in build["steps"]
        if any(
            re.search(rf'"\${credential}"\s*>[^>]', str(step.get("run", "")))
            for credential in declared
        )
    ]

    assert materializing, "no step writes a credential to disk; has the gate moved?"
    for step in materializing:
        run = str(step["run"])
        armed = re.search(r"trap\s+'([^']*)'\s+EXIT", run)
        assert armed, (
            f"{step.get('name')!r} writes a signing credential to disk without "
            "arming its removal first"
        )
        assert "rm -f" in armed.group(1), (
            f"{step.get('name')!r} arms a trap that does not remove the "
            f"credential it wrote: {armed.group(1)!r}"
        )


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


def test_publication_reads_the_release_marking_before_it_trusts_the_channel() -> None:
    """AC3: the marking is observed off the Release, ahead of the gate.

    The version string decides which evidence is *required*; the marking is
    what an operator, and every package channel that resolves "the stable
    Release", actually sees. `source-release.yml` writes it — deliberately a
    separate workflow, so that a cross-compilation failure cannot stop a source
    Release — and it stays editable afterwards. Reading it back is what turns
    "clearly marked" from an assumption held in another file into an input this
    gate refuses to publish without.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    publish = _workflow(policy)["jobs"][policy.publication_job]

    names = [step.get("name", step.get("uses", "")) for step in publish["steps"]]
    observed = [
        index
        for index, step in enumerate(publish["steps"])
        if "isPrerelease" in str(step.get("run", ""))
    ]
    verifies = [
        index
        for index, step in enumerate(publish["steps"])
        if "git_loopy.release_trust verify" in str(step.get("run", ""))
    ]
    uploads = [
        index
        for index, step in enumerate(publish["steps"])
        if "gh release upload" in str(step.get("run", ""))
    ]

    assert len(observed) == 1, names
    assert observed[0] < verifies[0] < uploads[0], names

    reader = publish["steps"][observed[0]]
    gate = str(publish["steps"][verifies[0]]["run"])
    assert "--release-marked-prerelease" in gate
    assert f"steps.{reader['id']}.outputs" in gate, (
        "the gate must be handed the marking this job actually read, not a "
        "second opinion about the same Release"
    )


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


def test_a_channel_credential_reaches_only_the_job_that_publishes_it() -> None:
    """A token that can write to another repository is confined by name.

    Signing credentials prove artifacts and stay in the signing job. A channel
    credential *writes* — it pushes a formula or a manifest into a repository
    this project publishes through — so a second job that could read it could
    publish under this project's name. Both live behind the same protected
    environment, and each is pinned to the one job that needs it.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    jobs = _workflow(policy)["jobs"]

    assert policy.channel_credentials, "no distribution channel is published"
    for credential in policy.channel_credentials:
        assert credential.purpose.strip()
        readers = {
            name
            for name, job in jobs.items()
            if f"secrets.{credential.name}" in yaml.safe_dump(job)
        }
        assert readers == {credential.job}
        assert jobs[credential.job]["environment"] == policy.protected_environment
