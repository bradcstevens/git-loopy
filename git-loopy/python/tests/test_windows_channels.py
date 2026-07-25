"""The winget and Scoop channel seam.

`git-loopy/conformance/windows-channels.json` is the one place that says which
published **TUI helper** artifact the two Windows package managers install, which
published targets they deliberately do not, and what a committed manifest has to
prove before it is allowed to reach operators. It is data only, so this suite
drives the production seam in `git_loopy.windows_channels` rather than restating
the fixture's contents.

Both channels install one artifact — the signed `x86_64-pc-windows-msvc` zip —
so everything that could point somewhere else is a single fact stated once and
proven twice, in the two formats the two ecosystems read.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import release_trust
from git_loopy import tui_release
from git_loopy import windows_channels


REPOSITORY_ROOT = Path(__file__).parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "git-loopy/conformance/windows-channels.json"
FIXTURE: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

WINDOWS_ARCHIVE = "git-loopy-tui-x86_64-pc-windows-msvc.zip"
WINDOWS_DIGEST = "5" * 64
PUBLISHER = "CN=Brad Stevens, O=Brad Stevens, L=Redmond, S=Washington, C=US"


def test_both_channels_install_the_one_artifact_windows_package_managers_run() -> None:
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)

    assert [channel.id for channel in policy.channels] == ["winget", "scoop"]
    assert policy.installed_target == "x86_64-pc-windows-msvc"
    assert policy.channel("winget").repository == "bradcstevens/winget-pkgs"
    assert policy.channel("scoop").repository == "bradcstevens/scoop-git-loopy"


def test_every_published_target_is_either_installed_or_excluded_by_name() -> None:
    """No target may fall out of these channels by being forgotten.

    `tui-artifacts.json` publishes seven artifacts and Windows package managers
    run exactly one of them. The difference between "cannot" and "nobody
    noticed" is the whole point of recording the reason: a Windows arm64 target
    added to the Release later fails this rather than silently never reaching
    either channel.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)

    excluded = {target.triple: target.reason for target in policy.excluded_targets}
    published = {target.triple for target in metadata.targets}

    assert policy.installed_target not in excluded
    assert {policy.installed_target} | set(excluded) == published
    assert all(reason.strip() for reason in excluded.values())


def test_the_installed_artifact_is_resolved_the_way_an_installer_resolves_it() -> None:
    """The channels and `install.ps1` resolve the same archive for the same host.

    A manifest that named its own archive could install a *different* build of
    the same Release, so the channel's host shape is run back through the shared
    selection seam both installers use rather than through the triple the policy
    happens to name.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)

    artifact = windows_channels.windows_artifact(metadata, policy)

    installer_choice = tui_release.select_target(
        metadata,
        system=policy.host_system,
        machine=policy.host_machine,
        libc=policy.host_libc,
    )
    assert artifact.target.triple == installer_choice.triple
    assert artifact.target.triple == policy.installed_target
    assert artifact.archive_name == WINDOWS_ARCHIVE
    assert artifact.executable_name == "git-loopy-tui.exe"


def published_checksums(directory: Path) -> Path:
    """The `.sha256` manifest a completed Release publishes, as downloaded."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{WINDOWS_ARCHIVE}.sha256").write_text(
        f"{WINDOWS_DIGEST}  {WINDOWS_ARCHIVE}\n", encoding="utf-8"
    )
    return directory


def published_receipt(
    directory: Path,
    defect: str = "signed",
    release_version: str = "1.2.3",
    publisher: str = PUBLISHER,
) -> Path:
    """The Windows artifact's trust receipt, as the release runner wrote it.

    Written through `release_trust.write_trust_receipt` rather than hand-rolled,
    so a receipt this suite calls signed is one the platform-trust gate would
    also call signed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if defect == "missing":
        return directory

    evidence: dict[str, Any] = {
        "signature": publisher,
        "publisher_identity": publisher,
        "checksum": WINDOWS_DIGEST,
    }
    if defect == "no-publisher-identity":
        del evidence["publisher_identity"]
    release_trust.write_trust_receipt(
        REPOSITORY_ROOT,
        directory,
        "x86_64-pc-windows-msvc",
        release_version="9.9.9" if defect == "version-drift" else release_version,
        signed=defect != "unsigned",
        evidence={} if defect == "unsigned" else evidence,
    )
    return directory


@pytest.mark.parametrize(
    "case", FIXTURE["publication_cases"], ids=lambda case: case["id"]
)
def test_only_a_stable_release_reaches_a_windows_channel(
    case: dict[str, Any], tmp_path: Path
) -> None:
    """`winget install` and `scoop install` are the stable channel and only that.

    Neither operator names a version, so the version they get is whatever the
    channel last wrote down — and a prerelease is exactly the Release whose
    Windows artifact the platform-trust gate allows to be *unsigned*. So the
    refusal lives here rather than in the workflow's `if:`: a channel that could
    be pointed at an `-rc.1` build by editing one condition is a channel that can
    ship an unsigned binary under the plain name.
    """
    checksums = published_checksums(tmp_path / "checksums")
    receipts = published_receipt(tmp_path / "receipts", release_version=case["version"])

    for channel in ("winget", "scoop"):
        if case["published"]:
            rendered = windows_channels.render_manifests(
                REPOSITORY_ROOT,
                channel,
                release_version=case["version"],
                release_marked_prerelease=case["marked_prerelease"],
                checksum_dir=checksums,
                receipt_dir=receipts,
            )
            assert any(case["version"] in text for text in rendered.values())
            continue

        with pytest.raises(windows_channels.WindowsChannelError) as raised:
            windows_channels.render_manifests(
                REPOSITORY_ROOT,
                channel,
                release_version=case["version"],
                release_marked_prerelease=case["marked_prerelease"],
                checksum_dir=checksums,
                receipt_dir=receipts,
            )
        assert case["error"] in str(raised.value)


@pytest.mark.parametrize("case", FIXTURE["signing_cases"], ids=lambda case: case["id"])
def test_only_a_signed_windows_artifact_reaches_a_windows_channel(
    case: dict[str, Any], tmp_path: Path
) -> None:
    """AC1: the channels install the *signed* stable Windows artifact.

    A package manager is the one path where nobody reads the bytes, and on
    Windows the thing an operator is shown instead is the publisher SmartScreen
    resolved. That name comes from the Authenticode certificate the release
    runner observed on the artifact it had just signed, recorded in the
    Release's own trust receipt. So the channels read that receipt back rather
    than assuming publication implied it: the platform-trust gate is what makes
    a stable Release refuse an unsigned artifact, and a channel that inferred
    signing from "the Release exists" would be trusting a gate it never asked.
    """
    published_checksums(tmp_path / "checksums")
    receipts = published_receipt(tmp_path / "receipts", case["receipt"])

    if case["published"]:
        identity = windows_channels.signing_identity(
            REPOSITORY_ROOT, receipts, release_version="1.2.3"
        )
        assert identity == PUBLISHER
        return

    with pytest.raises(windows_channels.WindowsChannelError) as raised:
        windows_channels.signing_identity(
            REPOSITORY_ROOT, receipts, release_version="1.2.3"
        )
    assert case["error"] in str(raised.value)


def test_the_windows_channels_require_the_evidence_the_trust_policy_requires() -> None:
    """The evidence is the platform-trust gate's, read rather than restated.

    `release-trust.json` already says what a *stable* Windows artifact must
    prove. A channel fixture that listed its own required evidence would be a
    second place to relax it, and the two could disagree without either failing.
    """
    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)

    required = windows_channels.required_windows_evidence(REPOSITORY_ROOT)

    mechanism = policy.mechanism_for("windows")
    assert mechanism is not None
    assert required == tuple(mechanism.required_evidence["stable"])
    assert "signature" in required and "publisher_identity" in required


def rendered(
    channel: str,
    tmp_path: Path,
    release_version: str = "1.2.3",
) -> dict[str, str]:
    """One channel's committed metadata for a published stable Release."""
    return windows_channels.render_manifests(
        REPOSITORY_ROOT,
        channel,
        release_version=release_version,
        release_marked_prerelease=False,
        checksum_dir=published_checksums(tmp_path / "checksums"),
        receipt_dir=published_receipt(
            tmp_path / "receipts", release_version=release_version
        ),
    )


def test_the_winget_manifests_name_this_releases_installer_and_publisher(
    tmp_path: Path,
) -> None:
    """AC1: the exact published identity, digest, and signer, in winget's shape.

    winget's community repository takes a multi-file manifest, so the three
    files are read back as YAML rather than matched as text: what winget
    installs is decided by its own parser, and a generator whose output only
    *looks* right is the failure this whole channel exists to prevent.
    """
    manifests = rendered("winget", tmp_path)
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)
    channel = policy.channel("winget")

    assert set(manifests) == set(channel.committed_files("1.2.3"))
    documents = {}
    for text in manifests.values():
        document = yaml.safe_load(text)
        documents[document["ManifestType"]] = document
    version, installer, locale = (
        documents["version"],
        documents["installer"],
        documents["defaultLocale"],
    )

    for document in (version, installer, locale):
        assert document["PackageIdentifier"] == "bradcstevens.git-loopy-tui"
        assert document["PackageVersion"] == "1.2.3"
        assert document["ManifestVersion"] == "1.6.0"

    assert version["DefaultLocale"] == "en-US"
    assert locale["PackageLocale"] == "en-US"
    assert installer["InstallerType"] == "zip"
    assert installer["NestedInstallerType"] == "portable"
    assert installer["NestedInstallerFiles"] == [
        {
            "RelativeFilePath": "git-loopy-tui.exe",
            "PortableCommandAlias": "git-loopy-tui",
        }
    ]
    assert installer["Installers"] == [
        {
            "Architecture": "x64",
            "InstallerUrl": (
                "https://github.com/bradcstevens/git-loopy/releases/download"
                f"/v1.2.3/{WINDOWS_ARCHIVE}"
            ),
            "InstallerSha256": WINDOWS_DIGEST,
        }
    ]
    assert locale["Publisher"] == "Brad Stevens"
    assert locale["ReleaseNotesUrl"].endswith("/releases/tag/v1.2.3")


def test_the_scoop_manifest_names_this_releases_artifact_and_checksum(
    tmp_path: Path,
) -> None:
    """AC2: the stable Windows artifact and its exact published checksum."""
    (path,) = rendered("scoop", tmp_path).items()
    manifest = json.loads(path[1])

    assert path[0] == "bucket/git-loopy-tui.json"
    assert manifest["version"] == "1.2.3"
    assert manifest["architecture"]["64bit"] == {
        "url": (
            "https://github.com/bradcstevens/git-loopy/releases/download"
            f"/v1.2.3/{WINDOWS_ARCHIVE}"
        ),
        "hash": WINDOWS_DIGEST,
    }
    assert manifest["bin"] == "git-loopy-tui.exe"


def test_the_scoop_manifest_proves_the_installed_helper_is_this_release(
    tmp_path: Path,
) -> None:
    """The channel's own acceptance check runs on the operator's machine.

    A manifest can only promise the bytes it points at. `post_install` is where
    the installed helper is asked, in the operator's own shell, whether it is the
    Release the manifest claims — and the comparison is whole-string, because a
    substring match would accept `1.2.3-rc.1` for `1.2.3`, which is exactly the
    build a stable channel exists to keep out.
    """
    ((_, text),) = rendered("scoop", tmp_path).items()
    probe = "\n".join(json.loads(text)["post_install"])

    assert "--version" in probe
    assert "$dir\\git-loopy-tui.exe" in probe
    assert '-ne "git-loopy-tui 1.2.3"' in probe
    assert "throw" in probe


def test_the_scoop_manifest_never_updates_itself_behind_the_gate(
    tmp_path: Path,
) -> None:
    """A bucket that could refresh itself is a bucket the gate never sees.

    Scoop's `checkver`/`autoupdate` let a bucket scrape a new version and rewrite
    its own URL and hash. That is the whole of this channel's job done by
    something that never reads the trust receipt, never checks the Release's
    marking, and would happily publish a prerelease.
    """
    ((_, text),) = rendered("scoop", tmp_path).items()
    manifest = json.loads(text)

    assert "checkver" not in manifest
    assert "autoupdate" not in manifest


def test_a_format_that_cannot_carry_a_claim_records_which_and_why() -> None:
    """The two ecosystems are not symmetric, and the difference is a decision.

    Scoop has no publisher field; winget has no post-install hook. Left absent,
    each channel would silently be held to the weaker bar of whichever format
    has fewer fields. Named, the gap is something a reader can disagree with.
    """
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)

    claims = {
        (claim.channel, claim.claim): claim.reason
        for claim in policy.unavailable_claims
    }

    assert set(claims) == {("winget", "version_probe"), ("scoop", "publisher")}
    assert all(reason.strip() for reason in claims.values())


@pytest.mark.parametrize("case", FIXTURE["drift_cases"], ids=lambda case: case["id"])
def test_drifted_channel_metadata_is_refused(
    case: dict[str, Any], tmp_path: Path
) -> None:
    """AC4: install and upgrade metadata cannot point anywhere but this Release.

    Each case changes exactly one thing about otherwise-published metadata. The
    refusal is what stops an operator's `winget upgrade` or `scoop update` from
    fetching a build nobody in this pipeline ever verified, or from crediting it
    to a publisher nobody in this pipeline ever observed.
    """
    manifests = rendered(case["channel"], tmp_path)

    with pytest.raises(windows_channels.WindowsChannelError) as raised:
        windows_channels.verify_manifests(
            REPOSITORY_ROOT,
            case["channel"],
            drifted(case["channel"], manifests, case),
            release_version="1.2.3",
            release_marked_prerelease=False,
            checksum_dir=tmp_path / "checksums",
            receipt_dir=tmp_path / "receipts",
        )

    assert case["error"] in str(raised.value)


def drifted(
    channel: str, manifests: dict[str, str], case: dict[str, Any]
) -> dict[str, str]:
    """One published channel's metadata with exactly one thing changed."""
    field = case["field"]
    replacements = {
        "winget": {
            "version": (
                'PackageVersion: "1.2.3"',
                f'PackageVersion: "{case["value"]}"',
            ),
            "url": (
                'InstallerUrl: "https://github.com/bradcstevens/git-loopy/releases'
                f'/download/v1.2.3/{WINDOWS_ARCHIVE}"',
                f'InstallerUrl: "{case["value"]}"',
            ),
            "digest": (
                f'InstallerSha256: "{WINDOWS_DIGEST}"',
                f'InstallerSha256: "{case["value"]}"',
            ),
            "publisher": (
                'Publisher: "Brad Stevens"',
                f'Publisher: "{case["value"]}"',
            ),
            "identifier": (
                'PackageIdentifier: "bradcstevens.git-loopy-tui"',
                f'PackageIdentifier: "{case["value"]}"',
            ),
        },
        "scoop": {
            "version": ('"version": "1.2.3"', f'"version": "{case["value"]}"'),
            "url": (
                '"url": "https://github.com/bradcstevens/git-loopy/releases/download'
                f'/v1.2.3/{WINDOWS_ARCHIVE}"',
                f'"url": "{case["value"]}"',
            ),
            "digest": (f'"hash": "{WINDOWS_DIGEST}"', f'"hash": "{case["value"]}"'),
        },
    }
    if field == "version_probe":
        manifest = json.loads(next(iter(manifests.values())))
        del manifest["post_install"]
        return {path: json.dumps(manifest, indent=4) + "\n" for path in manifests}

    old, new = replacements[channel][field]
    changed = {path: text.replace(old, new) for path, text in manifests.items()}
    assert changed != manifests, f"drift case {case['id']} changed nothing"
    return changed


def channel_argv(command: str, channel: str, tmp_path: Path, root: Path) -> list[str]:
    return [
        command,
        "--channel",
        channel,
        "--repository-root",
        str(REPOSITORY_ROOT),
        "--release-version",
        "1.2.3",
        "--release-marked-prerelease",
        "false",
        "--checksum-dir",
        str(published_checksums(tmp_path / "checksums")),
        "--receipt-dir",
        str(published_receipt(tmp_path / "receipts")),
        "--channel-root",
        str(root),
    ]


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_the_render_command_writes_the_metadata_and_the_gate_accepts_it(
    channel: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC3: the generator and the gate are two commands over the same files.

    What reaches operators is whatever is committed to the channel repository,
    so the thing proven is the committed text — including text a human edited
    after the generator ran.
    """
    root = tmp_path / "channel"

    assert windows_channels.main(channel_argv("render", channel, tmp_path, root)) == 0
    written = capsys.readouterr().out.split()

    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)
    expected = policy.channel(channel).committed_files("1.2.3")
    assert [Path(path).relative_to(root).as_posix() for path in written] == list(
        expected
    )

    assert windows_channels.main(channel_argv("verify", channel, tmp_path, root)) == 0
    assert capsys.readouterr().out.strip() == WINDOWS_ARCHIVE


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_a_hand_edited_channel_file_fails_the_gate_and_exits_nonzero(
    channel: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "channel"
    assert windows_channels.main(channel_argv("render", channel, tmp_path, root)) == 0
    capsys.readouterr()

    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)
    for relative in policy.channel(channel).committed_files("1.2.3"):
        edited = root / relative
        edited.write_text(
            edited.read_text(encoding="utf-8").replace(WINDOWS_DIGEST, "0" * 64),
            encoding="utf-8",
        )

    assert windows_channels.main(channel_argv("verify", channel, tmp_path, root)) == 1
    assert "is not the one the Release published" in capsys.readouterr().err


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_channel_metadata_that_was_never_committed_is_refused(
    channel: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent file is not a passing gate.

    A `verify` that read nothing and said nothing would be indistinguishable
    from one that proved something, and the whole channel would be a step that
    cannot fail.
    """
    assert (
        windows_channels.main(
            channel_argv("verify", channel, tmp_path, tmp_path / "empty")
        )
        == 1
    )
    assert "cannot read channel metadata" in capsys.readouterr().err


def test_a_malformed_invocation_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        windows_channels.main(["render", "--channel", "winget"])

    assert raised.value.code == 2


@pytest.mark.parametrize(
    "marking", [None, "", "unknown", "yes"], ids=["omitted", "empty", "unknown", "yes"]
)
def test_an_unreadable_release_marking_is_refused_rather_than_defaulted(
    marking: str | None, tmp_path: Path
) -> None:
    """A channel that could guess the Release channel can guess it wrong."""
    argv = [
        "render",
        "--channel",
        "scoop",
        "--repository-root",
        str(REPOSITORY_ROOT),
        "--release-version",
        "1.2.3",
        "--checksum-dir",
        str(published_checksums(tmp_path / "checksums")),
        "--receipt-dir",
        str(published_receipt(tmp_path / "receipts")),
        "--channel-root",
        str(tmp_path / "channel"),
    ]
    if marking is not None:
        argv += ["--release-marked-prerelease", marking]

    with pytest.raises(SystemExit) as raised:
        windows_channels.main(argv)

    assert raised.value.code == 2


def test_an_unknown_channel_is_named_rather_than_silently_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = windows_channels.main(
        channel_argv("render", "chocolatey", tmp_path, tmp_path / "channel")
    )

    assert exit_code == 1
    assert "is not a Windows channel this project publishes" in capsys.readouterr().err


def trust_policy() -> release_trust.TrustPolicy:
    """The release pipeline's own description of itself.

    Where a channel job sits in that pipeline — after publication, inside the
    protected environment, reading one named credential — belongs to the
    pipeline rather than to this channel, so it is read from there rather than
    restated here. Two fixtures naming one job is two places to relax it.
    """
    return release_trust.load_trust_policy(REPOSITORY_ROOT)


def workflow() -> dict[str, Any]:
    document = yaml.safe_load(
        (REPOSITORY_ROOT / trust_policy().workflow_path).read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def channel_job(channel: str) -> dict[str, Any]:
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)
    return workflow()["jobs"][policy.channel(channel).channel_job]


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_a_channel_is_updated_only_after_the_artifacts_are_published(
    channel: str,
) -> None:
    """AC3: a channel is opened from a completed Release, not alongside one.

    Publication is what proves the artifacts: the complete set, the checksums,
    the attestation, the platform-trust gate, and finally the upload. A channel
    job that ran in parallel could publish a URL for a Release whose artifacts
    were refused, and a 404 in a manifest is indistinguishable to an operator
    from a network fault.
    """
    policy = trust_policy()
    job = channel_job(channel)

    assert policy.publication_job in job["needs"]
    assert job["if"] == "needs.identity.outputs.prerelease == 'false'"
    assert job["environment"] == policy.protected_environment


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_a_channel_never_rebuilds_or_substitutes_what_the_release_published(
    channel: str,
) -> None:
    """AC3: no rebuilding or substituting binaries.

    The digest a manifest publishes is the Release's own `.sha256`, and the
    publisher is its own `.trust.json`. A job that could compile would be a job
    that could write down a digest nobody outside it will ever compute.
    """
    job = yaml.safe_dump(channel_job(channel))

    assert "gh release download" in job
    assert "--pattern '*.sha256'" in job
    assert "--pattern '*.trust.json'" in job
    for rebuilding in ("dist build", "cargo build", "cargo install", "sha256sum"):
        assert rebuilding not in job


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_a_channel_is_verified_before_it_is_opened(channel: str) -> None:
    """AC4: nothing reaches a channel that has not been proven against the Release."""
    commands = [str(step.get("run", "")) for step in channel_job(channel)["steps"]]

    verified = next(
        index
        for index, run in enumerate(commands)
        if "windows_channels verify" in run and f"--channel {channel}" in run
    )
    opened = next(index for index, run in enumerate(commands) if "gh pr create" in run)
    assert verified < opened


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_the_job_checks_out_the_repository_the_policy_names(channel: str) -> None:
    """One channel repository, named once.

    The paths inside the checkout are already policy, and so is the repository
    they are committed to. A job that named its own would publish verified
    metadata somewhere nobody has installed from.
    """
    declared = windows_channels.load_channel_policy(REPOSITORY_ROOT).channel(channel)
    checkouts = [
        step["with"]
        for step in channel_job(channel)["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
        and "repository" in step.get("with", {})
    ]

    assert [checkout["repository"] for checkout in checkouts] == [declared.repository]
    credential = next(
        entry
        for entry in trust_policy().channel_credentials
        if entry.job == declared.channel_job
    )
    assert checkouts[0]["token"] == "${{ secrets.%s }}" % credential.name


def test_every_windows_channel_declares_its_credential_in_the_one_registry() -> None:
    """A token that writes outside this repository is declared where every one is.

    `release-trust.json` is the single credential registry, and the release
    pipeline is refused if it reads any secret that fixture does not name. A
    channel carrying its own credential list could add one nothing reviewed.
    """
    declared = {entry.job: entry for entry in trust_policy().channel_credentials}
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)

    for channel in policy.channels:
        assert channel.channel_job in declared, channel.id
        assert declared[channel.channel_job].purpose.strip()


def pull_request_step(channel: str) -> dict[str, Any]:
    """The step that commits a channel's metadata and opens its Release PR."""
    return next(
        step
        for step in channel_job(channel)["steps"]
        if "gh pr create" in str(step.get("run", ""))
    )


def step_environment(step: dict[str, Any], **supplied: str) -> dict[str, str]:
    """The step's own declared environment, with the workflow's expressions supplied.

    Read from the step rather than restated, so a job that grows a variable the
    script depends on cannot pass here while failing on a runner. A `${{ }}`
    expression is the one thing a test has to stand in for; everything else is
    a literal the workflow already decided.
    """
    declared = {
        name: str(value)
        for name, value in step.get("env", {}).items()
        if not str(value).startswith("${{")
    }
    return {**os.environ, **declared, **supplied}


def stub_gh(directory: Path, default_branch: str = "main") -> Path:
    """A `gh` the step's bash can reach without a token or a live repository.

    The step asks the *base* repository for its default branch rather than
    reading the checkout's current branch, because a cross-repository pull
    request's base lives in a repository this checkout is not. That call has to
    be answered for the rest of the script to run at all, and every invocation
    is recorded so a test can prove which ones happened.
    """
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "gh"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$GH_CALLS"\n'
        'if [ "$1" = "repo" ] && [ "$2" = "view" ]; then\n'
        f"  echo {default_branch}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_a_channel_push_updates_its_own_branch_on_a_rerun(
    channel: str, tmp_path: Path
) -> None:
    """A rerun must be able to replace the branch it opened last time.

    `actions/checkout` fetches one branch, so a rerun holds no remote-tracking
    ref for the Release branch — and a bare `--force-with-lease` refuses as
    "stale info" rather than updating. This runs the step's own bash against a
    real bare remote, twice, which is the only way to find that out.
    """
    step = pull_request_step(channel)
    script = str(step["run"])
    script = script[: script.index("gh pr create")]
    stub_gh(tmp_path / "bin")

    remote = tmp_path / "channel.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True)
    (seed / "README.md").write_text("channel\n", encoding="utf-8")
    for command in (
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "seed"],
        ["git", "push", "origin", "main"],
    ):
        subprocess.run(command, cwd=seed, check=True)

    def publish(marker: str) -> subprocess.CompletedProcess[str]:
        checkout = tmp_path / f"channel-{marker}"
        subprocess.run(
            ["git", "clone", "--single-branch", str(remote), str(checkout)], check=True
        )
        written = checkout / "manifests" / "git-loopy-tui.txt"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(marker, encoding="utf-8")
        return subprocess.run(
            ["bash", "-eo", "pipefail", "-c", script],
            cwd=checkout,
            env=step_environment(
                step,
                RELEASE_TAG="v1.2.3",
                RELEASE_VERSION="1.2.3",
                GH_CALLS=str(tmp_path / f"gh-{marker}.log"),
                PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            ),
            capture_output=True,
            text=True,
        )

    first = publish("first")
    assert first.returncode == 0, first.stderr
    rerun = publish("second")
    assert rerun.returncode == 0, rerun.stderr

    published = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "show",
            "git-loopy-tui-v1.2.3:manifests/git-loopy-tui.txt",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert published.stdout == "second"


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_an_unchanged_channel_stops_before_it_opens_a_pull_request(
    channel: str, tmp_path: Path
) -> None:
    """Re-running a Release that is already published is not an update."""
    step = pull_request_step(channel)
    script = str(step["run"])
    stub_gh(tmp_path / "bin")
    calls = tmp_path / "gh.log"

    remote = tmp_path / "channel.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    checkout = tmp_path / "channel"
    subprocess.run(["git", "clone", str(remote), str(checkout)], check=True)
    (checkout / "README.md").write_text("channel\n", encoding="utf-8")
    for command in (
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "seed"],
        ["git", "push", "origin", "main"],
    ):
        subprocess.run(command, cwd=checkout, check=True)

    completed = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=checkout,
        env=step_environment(
            step,
            RELEASE_TAG="v1.2.3",
            RELEASE_VERSION="1.2.3",
            GH_CALLS=str(calls),
            PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        ),
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "already publishes v1.2.3" in completed.stdout
    assert "pr create" not in calls.read_text(encoding="utf-8")


# The certificate-subject vocabulary.
#
# `publisher_identity` is not a name someone chose for this project; it is
# whatever `Get-AuthenticodeSignature` reported for `SignerCertificate.Subject`
# on the artifact the release runner had just signed. That is .NET's
# `X509Certificate2.Subject`, which renders a distinguished name in one specific
# grammar — and winget's `Publisher` is the one thing a Windows operator is
# shown *instead of* a digest, so reading that grammar wrong is not cosmetic.
#
# The grammar below was observed by round-tripping each subject through
# `X500DistinguishedName` under PowerShell 7 rather than read off a spec.

DOTNET_SUBJECT_RENDERINGS = [
    pytest.param(
        "CN=Brad Stevens, O=Brad Stevens, L=Redmond, S=Washington, C=US",
        "Brad Stevens",
        id="a-value-needing-no-quoting-is-bare",
    ),
    pytest.param(
        'CN="Contoso, Inc.", O=Contoso Corp, L=Redmond, S=WA, C=US',
        "Contoso, Inc.",
        id="a-value-holding-a-comma-is-quoted-whole",
    ),
    pytest.param(
        'CN="Say ""Hi""", O=Contoso Corp',
        'Say "Hi"',
        id="a-quote-inside-a-quoted-value-is-doubled",
    ),
    pytest.param(
        'CN="Contoso ", O=Contoso Corp',
        "Contoso",
        id="a-value-quoted-only-to-keep-its-spaces",
    ),
    pytest.param(
        "O=Contoso Corp, C=US",
        "O=Contoso Corp, C=US",
        id="a-subject-with-no-common-name-passes-through-whole",
    ),
]


@pytest.mark.parametrize(("subject", "expected"), DOTNET_SUBJECT_RENDERINGS)
def test_the_publisher_shown_is_the_whole_common_name_the_certificate_carries(
    subject: str, expected: str
) -> None:
    """A publisher is read out of the subject, not truncated at its first comma.

    `CN=Contoso, Inc.` is an ordinary organisation-validated code-signing
    identity, and .NET writes it as `CN="Contoso, Inc."`. A reader that split the
    subject on every comma would publish that helper under `"Contoso` — a name
    with a stray quote in it that matches no company and that an operator
    checking a Windows security prompt would have to guess about.
    """
    assert windows_channels.publisher_display_name(subject) == expected


def test_a_comma_bearing_publisher_survives_generation_and_its_own_gate(
    tmp_path: Path,
) -> None:
    """The control: the channel, not the reader, is what has to hold.

    Byte-equality between `render` and `verify` cannot catch this on its own —
    both sides read the same receipt through the same function, so a truncating
    reader agrees with itself perfectly. What the drift gate would then be
    proving is that the committed manifest credits the publisher this code
    *invented*, which is precisely the assurance winget's `Publisher` field is
    there to give and not give.

    So the name is asserted against the certificate subject the receipt carries,
    and the rendered text is then put back through `verify_manifests` to prove
    the gate still accepts what generation produced.
    """
    subject = 'CN="Contoso, Inc.", O=Contoso Corp, L=Redmond, S=WA, C=US'
    checksums = published_checksums(tmp_path / "checksums")
    receipts = published_receipt(tmp_path / "receipts", publisher=subject)

    manifests = windows_channels.render_manifests(
        REPOSITORY_ROOT,
        "winget",
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=checksums,
        receipt_dir=receipts,
    )

    locale = next(
        document
        for document in (yaml.safe_load(text) for text in manifests.values())
        if document["ManifestType"] == "defaultLocale"
    )
    assert locale["Publisher"] == "Contoso, Inc."

    assert (
        windows_channels.verify_manifests(
            REPOSITORY_ROOT,
            "winget",
            manifests,
            release_version="1.2.3",
            release_marked_prerelease=False,
            checksum_dir=checksums,
            receipt_dir=receipts,
        ).archive_name
        == WINDOWS_ARCHIVE
    )


@pytest.mark.parametrize(
    "installers",
    [
        pytest.param("Installers: 1", id="a-scalar-where-a-sequence-belongs"),
        pytest.param("Installers:\n  Architecture: x64", id="a-mapping"),
        pytest.param('Installers: "x64"', id="a-string"),
    ],
)
def test_a_winget_manifest_whose_installers_is_not_a_sequence_is_refused_by_name(
    installers: str,
) -> None:
    """Every refusal this gate makes has to survive being handed nonsense.

    `Installers` is a YAML sequence, and a committed manifest is a file a human
    can edit. A reader that iterated it without looking raises `TypeError` three
    frames down, which escapes the one error type `main` turns into an exit code
    -- so the release job would fail with a traceback and exit 1 for a reason
    that reads like a bug in this tool rather than drift in the manifest.
    """
    manifest = (
        "PackageIdentifier: bradcstevens.git-loopy-tui\n"
        'PackageVersion: "1.2.3"\n'
        f"{installers}\n"
        'ManifestType: "installer"\n'
    )

    with pytest.raises(windows_channels.WindowsChannelError) as raised:
        windows_channels.read_winget_claims({"installer.yaml": manifest})

    assert "Installers" in str(raised.value)


def test_a_manifest_this_gate_never_read_cannot_ride_along(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC4: what reaches operators is the whole committed directory, not a list.

    The gate reads the files the policy names, and the job commits whatever is
    in the checkout — `git add -A`, because a winget version directory is new on
    every Release. Those are not the same set. winget resolves a package from
    every manifest in its version directory, so a second locale manifest left
    there by an earlier run, or added by anyone with write access to the fork,
    is metadata an operator can install through and this gate never opened.

    Refused by name, and only where a directory is this channel's own: Scoop's
    bucket holds every other package in it, so it declares no exclusive
    directory rather than claiming one and refusing its neighbours.
    """
    root = tmp_path / "channel"
    assert windows_channels.main(channel_argv("render", "winget", tmp_path, root)) == 0
    capsys.readouterr()

    committed = windows_channels.load_channel_policy(REPOSITORY_ROOT).channel("winget")
    smuggled = root / Path(committed.committed_files("1.2.3")[0]).with_name(
        "bradcstevens.git-loopy-tui.locale.fr-FR.yaml"
    )
    smuggled.write_text(
        "PackageIdentifier: bradcstevens.git-loopy-tui\n"
        'PackageVersion: "1.2.3"\n'
        'PackageLocale: "fr-FR"\n'
        "Publisher: \"Quelqu'un D'Autre\"\n"
        'ManifestType: "locale"\n'
        'ManifestVersion: "1.6.0"\n',
        encoding="utf-8",
    )

    assert windows_channels.main(channel_argv("verify", "winget", tmp_path, root)) == 1
    refusal = capsys.readouterr().err
    assert "locale.fr-FR.yaml" in refusal
    assert "this gate never read" in refusal


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_a_channel_opens_its_pull_request_where_operators_install_from(
    channel: str,
) -> None:
    """AC3: the manifests have to land in the repository the package resolves from.

    winget's default source is `microsoft/winget-pkgs`, and the way a project
    publishes into it is to push a branch to its own fork and open a pull
    request *across* repositories. The checked-out repository is therefore not
    the repository the pull request belongs to, and `--head` has to name the
    fork's owner or the base repository looks for that branch in itself.

    Both are stated in the policy rather than left to `gh`'s resolution of the
    checkout's remotes: a job whose base repository is inferred is a job that
    can silently open a pull request against a fork nobody installs from.
    """
    declared = windows_channels.load_channel_policy(REPOSITORY_ROOT).channel(channel)
    opening = next(
        step
        for step in channel_job(channel)["steps"]
        if "gh pr create" in str(step.get("run", ""))
    )
    environment = opening["env"]
    run = str(opening["run"])

    assert environment["PR_BASE_REPOSITORY"] == declared.pull_request_base_repository
    assert environment["CHANNEL_REPOSITORY"] == declared.repository
    assert '--repo "$PR_BASE_REPOSITORY"' in run
    # The head owner is derived from the checked-out repository rather than
    # written down twice: a fork whose owner disagreed with its own checkout is
    # a branch the base repository cannot find.
    assert 'head="${CHANNEL_REPOSITORY%%/*}:$branch"' in run
    assert '--head "$head"' in run
    assert "gh pr edit" in run and run.count('--repo "$PR_BASE_REPOSITORY"') == 2


def test_the_winget_channel_publishes_through_a_fork_of_the_community_repository() -> (
    None
):
    """The one channel whose metadata leaves this project's own namespace.

    Scoop's bucket is a repository operators add by name, so its pull request is
    its own. winget's is not: the package identifier and the committed path are
    both `microsoft/winget-pkgs` conventions, and a manifest that never reaches
    that repository is one `winget install bradcstevens.git-loopy-tui` -- the
    command this project's own README publishes -- cannot resolve.
    """
    policy = windows_channels.load_channel_policy(REPOSITORY_ROOT)
    winget = policy.channel("winget")
    scoop = policy.channel("scoop")

    assert winget.pull_request_base_repository == "microsoft/winget-pkgs"
    assert winget.repository != winget.pull_request_base_repository
    assert winget.committed_files("1.2.3")[0].startswith("manifests/b/bradcstevens/")
    assert scoop.pull_request_base_repository == scoop.repository


@pytest.mark.parametrize("channel", ["winget", "scoop"])
def test_the_pull_request_a_channel_actually_opens_names_the_right_repository(
    channel: str, tmp_path: Path
) -> None:
    """The control for the cross-repository fix: what `gh` was asked to do.

    Matching the step's text proves the flags are written down. Running it
    proves they survive the shell — `${CHANNEL_REPOSITORY%%/*}` is a parameter
    expansion, and a head reference that expanded to the wrong thing would look
    correct in the YAML and open a pull request in a repository nobody installs
    from.
    """
    step = pull_request_step(channel)
    declared = windows_channels.load_channel_policy(REPOSITORY_ROOT).channel(channel)
    stub_gh(tmp_path / "bin", default_branch="master")
    calls = tmp_path / "gh.log"

    remote = tmp_path / "channel.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(remote)], check=True)
    checkout = tmp_path / "channel"
    subprocess.run(["git", "clone", str(remote), str(checkout)], check=True)
    (checkout / "manifest.txt").write_text("published\n", encoding="utf-8")

    completed = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", str(step["run"])],
        cwd=checkout,
        env=step_environment(
            step,
            RELEASE_TAG="v1.2.3",
            RELEASE_VERSION="1.2.3",
            GH_CALLS=str(calls),
            PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        ),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    opened = next(
        line
        for line in calls.read_text(encoding="utf-8").splitlines()
        if line.startswith("pr create")
    )
    owner = declared.repository.split("/")[0]
    assert f"--repo {declared.pull_request_base_repository}" in opened
    assert f"--head {owner}:git-loopy-tui-v1.2.3" in opened
    # The base is the base repository's own default branch, not this checkout's:
    # a fork of the community repository is checked out on whatever branch it
    # tracks, and that is a different repository's answer to the question.
    assert "--base master" in opened
