"""The TUI helper's release-artifact seam.

The fixture at `git-loopy/conformance/tui-artifacts.json` is the one canonical
description of what a Release publishes for the shared **TUI helper** and how an
installer picks its own artifact out of that set. It is data only, so this suite
drives the production seam in `git_loopy.tui_release` rather than restating the
fixture's contents, and pins the identity rules that keep the helper, its
metadata, and the Release version from drifting apart.
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tomllib
from pathlib import Path
from typing import Any

import pytest

from git_loopy import tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "git-loopy/conformance/tui-artifacts.json"
FIXTURE: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_release_builds_the_seven_phase_two_targets() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    assert [target.triple for target in metadata.targets] == [
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "aarch64-unknown-linux-gnu",
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-musl",
        "x86_64-unknown-linux-musl",
    ]


@pytest.mark.parametrize(
    "case", FIXTURE["selection_cases"], ids=lambda case: case["id"]
)
def test_a_host_selects_the_artifact_the_fixture_names(case: dict[str, Any]) -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    if case["target"] is None:
        with pytest.raises(tui_release.TuiReleaseError) as raised:
            tui_release.select_target(
                metadata,
                system=case["system"],
                machine=case["machine"],
                libc=case["libc"],
            )
        assert case["error"] in str(raised.value)
        return

    selected = tui_release.select_target(
        metadata,
        system=case["system"],
        machine=case["machine"],
        libc=case["libc"],
    )
    assert selected.triple == case["target"]


def test_windows_arm64_is_deferred_by_name_rather_than_silently_unsupported() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    deferred = {target.triple: target.reason for target in metadata.deferred_targets}
    assert "aarch64-pc-windows-msvc" in deferred
    assert deferred["aarch64-pc-windows-msvc"]
    assert not {target.triple for target in metadata.targets} & set(deferred)


def test_the_helper_manifest_carries_the_repository_release_version() -> None:
    assert tui_release.helper_release_version(REPOSITORY_ROOT) == (
        (REPOSITORY_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    )


def test_a_helper_manifest_that_drifts_from_version_is_refused(
    tmp_path: Path,
) -> None:
    """ADR-0016: one Release version identifies the whole distribution.

    The helper's own `Cargo.toml` is what `--version` and the `--schema-version`
    probe report, so a manifest that drifts from `VERSION` would publish an
    artifact that truthfully denies belonging to the Release that shipped it.
    """
    root = tmp_path / "clone"
    (root / "git-loopy/tui").mkdir(parents=True)
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "git-loopy/tui/Cargo.toml").write_text(
        '[package]\nname = "git-loopy-tui"\nversion = "1.2.4"\n',
        encoding="utf-8",
    )

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.helper_release_version(root)
    assert "1.2.3" in str(raised.value)
    assert "1.2.4" in str(raised.value)
    assert "Cargo.toml" in str(raised.value)


def test_a_publication_tag_must_name_the_same_release(tmp_path: Path) -> None:
    root = tmp_path / "clone"
    (root / "git-loopy/tui").mkdir(parents=True)
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "git-loopy/tui/Cargo.toml").write_text(
        '[package]\nname = "git-loopy-tui"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    assert (
        tui_release.helper_release_version(root, tag_ref="refs/tags/v1.2.3") == "1.2.3"
    )

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.helper_release_version(root, tag_ref="refs/tags/v1.2.4")
    assert "1.2.4" in str(raised.value)


def test_the_published_artifact_set_is_named_the_same_way_everywhere() -> None:
    """Release automation and both installers derive one set of exact names.

    Pinned as literals rather than recomputed from the template: an installer
    that has to guess a filename is an installer that downloads the wrong file,
    and a naming change has to be a deliberate edit to this list.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    artifacts = tui_release.published_artifacts(metadata)
    assert {
        artifact.target.triple: (
            artifact.archive_name,
            artifact.checksum_name,
            artifact.executable_name,
        )
        for artifact in artifacts
    } == {
        "aarch64-apple-darwin": (
            "git-loopy-tui-aarch64-apple-darwin.tar.xz",
            "git-loopy-tui-aarch64-apple-darwin.tar.xz.sha256",
            "git-loopy-tui",
        ),
        "x86_64-apple-darwin": (
            "git-loopy-tui-x86_64-apple-darwin.tar.xz",
            "git-loopy-tui-x86_64-apple-darwin.tar.xz.sha256",
            "git-loopy-tui",
        ),
        "x86_64-pc-windows-msvc": (
            "git-loopy-tui-x86_64-pc-windows-msvc.zip",
            "git-loopy-tui-x86_64-pc-windows-msvc.zip.sha256",
            "git-loopy-tui.exe",
        ),
        "aarch64-unknown-linux-gnu": (
            "git-loopy-tui-aarch64-unknown-linux-gnu.tar.xz",
            "git-loopy-tui-aarch64-unknown-linux-gnu.tar.xz.sha256",
            "git-loopy-tui",
        ),
        "x86_64-unknown-linux-gnu": (
            "git-loopy-tui-x86_64-unknown-linux-gnu.tar.xz",
            "git-loopy-tui-x86_64-unknown-linux-gnu.tar.xz.sha256",
            "git-loopy-tui",
        ),
        "aarch64-unknown-linux-musl": (
            "git-loopy-tui-aarch64-unknown-linux-musl.tar.xz",
            "git-loopy-tui-aarch64-unknown-linux-musl.tar.xz.sha256",
            "git-loopy-tui",
        ),
        "x86_64-unknown-linux-musl": (
            "git-loopy-tui-x86_64-unknown-linux-musl.tar.xz",
            "git-loopy-tui-x86_64-unknown-linux-musl.tar.xz.sha256",
            "git-loopy-tui",
        ),
    }


def test_every_channel_resolves_one_artifact_url_for_one_release() -> None:
    """The URL an installer downloads from is shared, not re-guessed per channel.

    The shell and PowerShell installers, the Homebrew formula, and the
    winget/Scoop manifests all address the same bytes. A second opinion about
    where a Release publishes them is a channel that installs something else.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    assert (
        tui_release.release_artifact_url(
            metadata,
            release_version="9.9.9",
            artifact="git-loopy-tui-aarch64-apple-darwin.tar.xz",
        )
        == "https://github.com/bradcstevens/git-loopy/releases/download/"
        "v9.9.9/git-loopy-tui-aarch64-apple-darwin.tar.xz"
    )


def test_the_download_url_cannot_drift_from_the_repository_it_publishes_from() -> None:
    """One repository, declared once in the helper manifest.

    The template is duplicated into the shared fixture so no installer has to
    parse `Cargo.toml`, which makes drift the risk this pins: a fork or a rename
    that moved the manifest but not the fixture would keep every channel
    downloading from the old repository's Releases.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    manifest = tomllib.loads(
        (REPOSITORY_ROOT / tui_release.HELPER_MANIFEST_PATH).read_text(encoding="utf-8")
    )

    repository = manifest["package"]["repository"]
    assert metadata.release_download_url_template.startswith(f"{repository}/releases/")


def _write_artifact(directory: Path, name: str, payload: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(payload)
    return path


def test_a_published_checksum_proves_the_artifact_it_names(tmp_path: Path) -> None:
    """The gate every installer runs before it replaces a working helper."""
    archive = _write_artifact(
        tmp_path, "git-loopy-tui-x86_64-apple-darwin.tar.xz", b"helper"
    )
    digest = hashlib.sha256(b"helper").hexdigest()
    manifest = tmp_path / f"{archive.name}.sha256"
    manifest.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    assert tui_release.verify_checksum(archive, manifest) == digest


def test_a_tampered_artifact_is_rejected(tmp_path: Path) -> None:
    archive = _write_artifact(
        tmp_path, "git-loopy-tui-x86_64-apple-darwin.tar.xz", b"tampered"
    )
    digest = hashlib.sha256(b"helper").hexdigest()
    manifest = tmp_path / f"{archive.name}.sha256"
    manifest.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_checksum(archive, manifest)
    assert "checksum" in str(raised.value)


def test_a_checksum_for_a_different_artifact_is_rejected(tmp_path: Path) -> None:
    """A digest that matches proves nothing if it was published for another file."""
    archive = _write_artifact(
        tmp_path, "git-loopy-tui-x86_64-apple-darwin.tar.xz", b"helper"
    )
    digest = hashlib.sha256(b"helper").hexdigest()
    manifest = tmp_path / f"{archive.name}.sha256"
    manifest.write_text(
        f"{digest}  git-loopy-tui-aarch64-apple-darwin.tar.xz\n",
        encoding="utf-8",
    )

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_checksum(archive, manifest)
    assert "aarch64-apple-darwin" in str(raised.value)


def test_an_unreadable_checksum_manifest_is_rejected(tmp_path: Path) -> None:
    archive = _write_artifact(
        tmp_path, "git-loopy-tui-x86_64-apple-darwin.tar.xz", b"helper"
    )
    manifest = tmp_path / f"{archive.name}.sha256"
    manifest.write_text("not a checksum manifest\n", encoding="utf-8")

    with pytest.raises(tui_release.TuiReleaseError):
        tui_release.verify_checksum(archive, manifest)


def _write_fake_helper(path: Path, *, version: str, script: str = "") -> Path:
    """A stand-in for a freshly built artifact, so the gate is testable offline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf 'git-loopy-tui {version}\\n'; exit 0 ;;\n"
        "  --schema-version)\n"
        '    printf \'{"name": "git-loopy-tui", "version": "%s", '
        '"min_event_schema_version": 1, "max_event_schema_version": 1, '
        '"wrapper_contract_version": "1.0"}\\n\' '
        f"'{version}'\n"
        "    exit 0 ;;\n"
        "esac\n"
        f"{script}"
        "cat >/dev/null\n"
        "printf '{\"queue\": []}\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_a_native_artifact_answers_the_probe_and_drains_a_minimal_run(
    tmp_path: Path,
) -> None:
    """The smoke test a native release job runs before anything is published."""
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    helper = _write_fake_helper(tmp_path / "git-loopy-tui", version=version)

    result = tui_release.smoke_test(helper, release_version=version)

    assert result.reported_version == version
    assert result.event_schema_range == (1, 1)
    assert result.events_delivered == 2


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_an_artifact_from_another_release_fails_the_smoke_test(tmp_path: Path) -> None:
    helper = _write_fake_helper(tmp_path / "git-loopy-tui", version="9.9.9")

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.smoke_test(helper, release_version="1.2.3")
    assert "9.9.9" in str(raised.value)


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_an_artifact_that_cannot_drain_a_run_fails_the_smoke_test(
    tmp_path: Path,
) -> None:
    helper = _write_fake_helper(
        tmp_path / "git-loopy-tui",
        version="1.2.3",
        script="exit 3\n",
    )

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.smoke_test(helper, release_version="1.2.3")
    assert "3" in str(raised.value)


def test_the_release_toolchain_is_pinned_to_one_reviewed_version() -> None:
    """PRD #173 locks cargo-dist 0.32.0 so the toolchain cannot drift silently."""
    assert tui_release.pinned_cargo_dist_version(REPOSITORY_ROOT) == "0.32.0"


def test_the_helper_manifest_builds_exactly_the_declared_artifact_set() -> None:
    """One target list, not two.

    The manifest is what actually builds, the fixture is what installers and
    package channels resolve against. A target present in one and absent from
    the other is either an artifact nobody can find or a download that 404s.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    configuration = tui_release.helper_dist_configuration(REPOSITORY_ROOT)

    assert configuration["targets"] == [target.triple for target in metadata.targets]
    assert configuration["checksum"] == metadata.checksum_algorithm
    assert configuration["github-attestations"] is True


def _stage_release_set(
    directory: Path,
    *,
    payload: bytes = b"helper",
    skip: str | None = None,
) -> tui_release.ArtifactMetadata:
    """A directory shaped like a completed build, minus anything ``skip`` names."""
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    for artifact in tui_release.published_artifacts(metadata):
        if artifact.target.triple == skip:
            continue
        archive = directory / artifact.archive_name
        archive.write_bytes(payload)
        (directory / artifact.checksum_name).write_text(
            f"{hashlib.sha256(payload).hexdigest()}  {artifact.archive_name}\n",
            encoding="utf-8",
        )
    return metadata


def test_publication_refuses_an_incomplete_artifact_set(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC5: publication happens only after the complete required set succeeds."""
    _stage_release_set(tmp_path / "artifacts", skip="x86_64-pc-windows-msvc")

    exit_code = tui_release.main(
        [
            "verify-set",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--require-complete-set",
        ]
    )

    assert exit_code == 1
    assert "x86_64-pc-windows-msvc" in capsys.readouterr().err


def test_publication_accepts_the_complete_checksummed_set(tmp_path: Path) -> None:
    _stage_release_set(tmp_path / "artifacts")

    assert (
        tui_release.main(
            [
                "verify-set",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--artifact-dir",
                str(tmp_path / "artifacts"),
                "--require-complete-set",
            ]
        )
        == 0
    )


def test_publication_refuses_a_set_whose_checksum_does_not_hold(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts = tmp_path / "artifacts"
    _stage_release_set(artifacts)
    (artifacts / "git-loopy-tui-x86_64-apple-darwin.tar.xz").write_bytes(b"tampered")

    exit_code = tui_release.main(
        [
            "verify-set",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--artifact-dir",
            str(artifacts),
            "--require-complete-set",
        ]
    )

    assert exit_code == 1
    assert "SHA-256" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_a_native_build_verifies_the_artifact_it_just_produced(tmp_path: Path) -> None:
    """The release job's own gate: unpack, checksum, then ask the helper itself."""
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    staging = tmp_path / "staging"
    _write_fake_helper(staging / "git-loopy-tui", version=version)

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    archive = artifacts / "git-loopy-tui-x86_64-unknown-linux-musl.tar.xz"
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(staging / "git-loopy-tui", arcname="git-loopy-tui")
    (artifacts / f"{archive.name}.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )

    assert (
        tui_release.main(
            [
                "verify-artifact",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--artifact-dir",
                str(artifacts),
                "--target",
                "x86_64-unknown-linux-musl",
                "--smoke-test",
            ]
        )
        == 0
    )


def test_the_helper_package_is_visible_to_the_release_toolchain() -> None:
    """`publish = false` hides a binary from cargo-dist entirely.

    The helper is never published to crates.io, so its manifest says
    `publish = false` — and cargo-dist reads that as "this package has nothing
    to release", refusing the whole workspace before it plans a single
    artifact. The opt-in has to be explicit, or the release pipeline fails at
    its first command for a reason that looks nothing like the cause.
    """
    assert tui_release.helper_package_is_distributable(REPOSITORY_ROOT) is True


def _planned_artifacts(metadata: tui_release.ArtifactMetadata) -> dict[str, Any]:
    """The artifact half of a `dist plan`, derived from the declared set."""
    planned: dict[str, Any] = {}
    for artifact in tui_release.published_artifacts(metadata):
        planned[artifact.archive_name] = {
            "kind": "executable-zip",
            "name": artifact.archive_name,
            "checksum": artifact.checksum_name,
            "target_triples": [artifact.target.triple],
        }
        planned[artifact.checksum_name] = {
            "kind": "checksum",
            "name": artifact.checksum_name,
            "checksum": None,
            "target_triples": [artifact.target.triple],
        }
    planned["sha256.sum"] = {
        "kind": "unified-checksum",
        "name": "sha256.sum",
        "checksum": None,
        "target_triples": None,
    }
    return planned


def _release_plan(
    metadata: tui_release.ArtifactMetadata,
    version: str,
    **overrides: Any,
) -> dict[str, Any]:
    """A `dist plan --output-format=json` document for the declared Release."""
    plan = {
        "dist_version": "0.32.0",
        "announcement_tag": f"v{version}",
        "github_attestations": True,
        "artifacts": _planned_artifacts(metadata),
        "ci": {
            "github": {
                "artifacts_matrix": {
                    "include": [
                        {
                            "runner": target.runner,
                            "targets": [target.triple],
                            **(
                                {"container": {"image": target.container}}
                                if target.container
                                else {}
                            ),
                            **(
                                {"packages_install": target.packages_install}
                                if target.packages_install
                                else {}
                            ),
                        }
                        for target in metadata.targets
                    ]
                },
                "pr_run_mode": "plan",
            }
        },
    }
    plan.update(overrides)
    return plan


def test_the_release_plan_agrees_with_the_declared_artifact_set() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)

    verified = tui_release.verify_release_plan(
        REPOSITORY_ROOT,
        _release_plan(metadata, version),
    )

    assert [artifact.target.triple for artifact in verified] == [
        target.triple for target in metadata.targets
    ]


def test_a_plan_that_would_build_a_different_artifact_set_is_refused() -> None:
    """cargo-dist decides what is actually built; the fixture decides what is
    published. A Release where those disagree ships a name nothing resolves."""
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    plan = _release_plan(metadata, version)
    del plan["artifacts"]["git-loopy-tui-x86_64-pc-windows-msvc.zip"]

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(REPOSITORY_ROOT, plan)
    assert "x86_64-pc-windows-msvc" in str(raised.value)


def test_a_plan_from_a_different_toolchain_is_refused() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(
            REPOSITORY_ROOT,
            _release_plan(metadata, version, dist_version="0.33.0"),
        )
    assert "0.33.0" in str(raised.value)


def test_a_plan_that_would_publish_an_unchecksummed_artifact_is_refused() -> None:
    """AC4: every archive and every generated installer carries a checksum.

    Expressed as a rule over the plan rather than as a list of the artifacts
    that happen to exist today, so enabling a cargo-dist installer later cannot
    quietly ship one nothing can verify.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    plan = _release_plan(metadata, version)
    plan["artifacts"]["git-loopy-tui-installer.sh"] = {
        "kind": "installer",
        "name": "git-loopy-tui-installer.sh",
        "checksum": None,
        "target_triples": [],
    }

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(REPOSITORY_ROOT, plan)
    assert "git-loopy-tui-installer.sh" in str(raised.value)


def test_a_plan_that_would_not_attest_its_artifacts_is_refused() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(
            REPOSITORY_ROOT,
            _release_plan(metadata, version, github_attestations=False),
        )
    assert "attest" in str(raised.value)


def test_a_plan_announcing_another_release_is_refused() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(
            REPOSITORY_ROOT,
            _release_plan(metadata, version, announcement_tag="v9.9.9"),
        )
    assert "v9.9.9" in str(raised.value)


def test_the_build_matrix_carries_the_provisioning_each_target_needs() -> None:
    """AC2: three of the seven targets cannot build on a bare runner.

    cargo-dist's plan prescribes a cross container for both arm64 Linux targets
    and `musl-tools` for x64 musl. A matrix that omits them fails at the linker,
    so the provisioning is declared beside the target it belongs to rather than
    left to whoever writes the workflow.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    assert {
        target.triple: (target.runner, target.container, target.packages_install)
        for target in metadata.targets
    } == {
        "aarch64-apple-darwin": ("macos-14", None, None),
        "x86_64-apple-darwin": ("macos-15-intel", None, None),
        "x86_64-pc-windows-msvc": ("windows-2022", None, None),
        "aarch64-unknown-linux-gnu": (
            "ubuntu-22.04",
            "ghcr.io/rust-cross/manylinux2014-cross:aarch64",
            "python3 -m pip install cargo-zigbuild ziglang",
        ),
        "x86_64-unknown-linux-gnu": ("ubuntu-22.04", None, None),
        "aarch64-unknown-linux-musl": (
            "ubuntu-22.04",
            "messense/rust-musl-cross:aarch64-musl",
            "python3 -m pip install cargo-zigbuild ziglang",
        ),
        "x86_64-unknown-linux-musl": (
            "ubuntu-22.04",
            None,
            "sudo apt-get update && sudo apt-get install -y musl-tools",
        ),
    }


def test_a_plan_that_would_build_on_another_runner_is_refused() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    plan = _release_plan(metadata, version)
    plan["ci"]["github"]["artifacts_matrix"]["include"][0]["runner"] = "macos-13"

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(REPOSITORY_ROOT, plan)
    assert "macos-13" in str(raised.value)


def test_the_helper_manifest_defines_the_profile_the_toolchain_builds_with() -> None:
    """cargo-dist compiles with `--profile dist`, which cargo will not invent.

    `dist init` normally writes this section; a hand-maintained manifest has to
    carry it deliberately, and without it every one of the seven build jobs dies
    at `error: profile 'dist' is not defined` after the artifact plan has already
    succeeded.
    """
    profile = tui_release.helper_release_profile(REPOSITORY_ROOT)

    assert profile["inherits"] == "release"


def test_the_release_pipeline_verifies_a_plan_document_it_was_handed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The entry point the plan job runs, on a plan and on a drifted one."""
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_release_plan(metadata, version)), encoding="utf-8"
    )

    argv = [
        "verify-plan",
        "--repository-root",
        str(REPOSITORY_ROOT),
        "--plan",
        str(plan_path),
        "--tag-ref",
        f"refs/tags/v{version}",
    ]
    assert tui_release.main(argv) == 0
    assert "git-loopy-tui-x86_64-pc-windows-msvc.zip" in capsys.readouterr().out

    plan_path.write_text(
        json.dumps(_release_plan(metadata, version, github_attestations=False)),
        encoding="utf-8",
    )
    assert tui_release.main(argv) == 1
    assert "attest" in capsys.readouterr().err


def test_a_plan_that_provisions_the_wrong_tool_is_refused() -> None:
    """"Some provisioning happened" is not the same as "the right one did".

    cargo-dist words its own provisioning differently from the workflow step
    that mirrors it, so the two cannot be compared verbatim. What has to hold is
    that both install the same thing: a plan whose arm64 Linux step no longer
    reaches for cargo-zigbuild is a plan that will not link, however busy its
    command looks.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    plan = _release_plan(metadata, version)
    for entry in plan["ci"]["github"]["artifacts_matrix"]["include"]:
        if entry["targets"] == ["aarch64-unknown-linux-gnu"]:
            entry["packages_install"] = "exit 99"

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.verify_release_plan(REPOSITORY_ROOT, plan)
    assert "cargo-zigbuild" in str(raised.value)


def test_the_identity_command_publishes_the_channel_it_resolved(
    tmp_path: Path,
) -> None:
    """Which channel a Release is on is decided once, where its version is.

    The package channels only publish stable Releases, so every consumer of the
    identity job needs the same answer. Deriving it a second time from the
    version string in a workflow `if:` is how two jobs end up disagreeing about
    one Release.
    """
    github_output = tmp_path / "github-output"

    assert (
        tui_release.main(
            [
                "identity",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--github-output",
                str(github_output),
            ]
        )
        == 0
    )

    published = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
    )
    version = published["version"]
    assert published["tag"] == f"v{version}"
    assert published["prerelease"] == ("true" if "-" in version else "false")


def test_zig_itself_is_provisioned_wherever_cargo_zigbuild_is() -> None:
    """`cargo-zigbuild` is a cargo subcommand; `zig` is the compiler it shells out to.

    `python3 -m pip install cargo-zigbuild ziglang` happened to pull `ziglang` along in
    `rust-musl-cross:aarch64-musl` and not in `manylinux2014-cross:aarch64` --
    where `pip3` is `/usr/local/bin/pip3` and `python3` is `/usr/bin/python3`,
    two different interpreters, so the module could not land anywhere the
    `python3 -m ziglang` cargo-zigbuild actually runs would find it. That target
    failed with `Failed to find zig` the first time it ever reached the
    compiler. Naming both packages, through the interpreter that will be asked
    for them, is what stops the provisioning depending on whichever transitive
    dependency a wheel happens to declare today.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    through_zig = [
        target
        for target in metadata.targets
        if target.requires_tool == "cargo-zigbuild"
    ]
    assert through_zig, "no target cross-builds through cargo-zigbuild"
    for target in through_zig:
        provisioning = target.packages_install or ""
        assert provisioning.startswith("python3 -m pip install"), target.triple
        assert "ziglang" in provisioning, target.triple
