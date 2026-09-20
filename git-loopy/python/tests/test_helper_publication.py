"""Canonical downloads, not an upload acknowledgement, complete helper publication."""

from __future__ import annotations

import hashlib
import io
import json
import shlex
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from git_loopy import release_trust, tui_release


ROOT = Path(__file__).parents[3]
VERSION = "1.2.3-rc.1"
TAG = f"v{VERSION}"
RELEASE_URL = f"https://api.github.com/repos/bradcstevens/git-loopy/releases/tags/{TAG}"
DOWNLOAD_URL = f"https://github.com/bradcstevens/git-loopy/releases/download/{TAG}/"


class PublicRelease:
    """A recording HTTP adapter serving the same bytes as a verified build."""

    def __init__(self, directory: Path, *, version: str = VERSION, signed: bool = False) -> None:
        self.version = version
        self.tag = f"v{version}"
        self.release_url = RELEASE_URL.replace(TAG, self.tag)
        self.download_url = DOWNLOAD_URL.replace(TAG, self.tag)
        self.root = directory / "repository"
        self.built = directory / "built"
        self.built.mkdir()
        (self.root / "git-loopy/conformance").mkdir(parents=True)
        (self.root / "git-loopy/tui").mkdir()
        (self.root / "VERSION").write_text(version + "\n")
        (self.root / "git-loopy/tui/Cargo.toml").write_text(
            f'[package]\nname = "git-loopy-tui"\nversion = "{version}"\n'
        )
        for name in ("tui-artifacts.json", "release-trust.json"):
            content = json.loads((ROOT / "git-loopy/conformance" / name).read_text())
            if name == "release-trust.json":
                content["distribution_mode"] = "artifact-bearing"
            (self.root / "git-loopy/conformance" / name).write_text(json.dumps(content))

        metadata = tui_release.load_artifact_metadata(self.root)
        for artifact in tui_release.published_artifacts(metadata):
            archive = self.built / artifact.archive_name
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(artifact.executable_name, b"helper")
            else:
                with tarfile.open(archive, "w:xz") as bundle:
                    member = tarfile.TarInfo(artifact.executable_name)
                    member.size = len(b"helper")
                    bundle.addfile(member, io.BytesIO(b"helper"))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (self.built / artifact.checksum_name).write_text(
                f"{digest}  {archive.name}\n"
            )
            release_trust.write_trust_receipt(
                self.root, self.built, artifact.target.triple,
                release_version=version, signed=signed,
                evidence={
                    "signature": "Developer ID Application: Example Corp (TEAMID1234)",
                    "hardened_runtime": True,
                    "notarization": "Accepted",
                    "publisher_identity": "CN=Example Corp",
                } if signed else {},
            )

        self.files = {self.download_url + path.name: path.read_bytes()
                      for path in self.built.iterdir()}
        self.record = {
            "tag_name": self.tag,
            "name": f"git-loopy {version}",
            "html_url": f"https://github.com/bradcstevens/git-loopy/releases/tag/{self.tag}",
            "draft": False,
            "prerelease": "-" in version,
            "assets": [
                {"name": path.name, "browser_download_url": self.download_url + path.name,
                 "state": "uploaded", "size": path.stat().st_size}
                for path in self.built.iterdir()
            ],
        }
        self.requests: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.requests.append(url)
        if url == self.release_url:
            return json.dumps(self.record).encode()
        if url not in self.files:
            raise tui_release.TuiReleaseError(f"HTTP 404: {url}")
        return self.files[url]

    def verify(self, *, attestation: Path | None = None) -> tuple[tui_release.PublishedArtifact, ...]:
        return tui_release.verify_published_release(
            self.root, self.built, tag_ref=self.tag, attestation=attestation,
            distribution_mode="artifact-bearing", fetch=self.fetch,
        )


def test_upload_success_without_public_assets_is_not_publication(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)
    host.record["assets"] = []

    with pytest.raises(tui_release.TuiReleaseError, match="missing.*aarch64-apple-darwin"):
        host.verify()

    assert host.requests == [RELEASE_URL]


def test_stable_readback_requires_both_local_and_public_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PublicRelease(tmp_path, version="1.2.3", signed=True)
    with pytest.raises(tui_release.TuiReleaseError, match="attestation"):
        host.verify()
    assert host.requests == []

    bundle = tmp_path / "attestation.jsonl"
    bundle.write_text("{}\n")
    commands: list[list[str]] = []

    def verify(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "verified", "")

    monkeypatch.setattr(tui_release.subprocess, "run", verify)
    assert len(host.verify(attestation=bundle)) == 7
    assert len(commands) == 7
    for command in commands:
        assert command[:3] == ["gh", "attestation", "verify"]
        assert command[-2:] == ["--repo", "bradcstevens/git-loopy"]


def test_complete_prerelease_downloads_every_archive_checksum_and_receipt(
    tmp_path: Path,
) -> None:
    host = PublicRelease(tmp_path)

    artifacts = host.verify()

    assert len(artifacts) == 7
    assert len(host.files) == 21
    assert set(host.requests) == {RELEASE_URL, *host.files}
    assert host.requests.count(RELEASE_URL) == 2


@pytest.mark.parametrize(("field", "value"), [
    ("tag_name", "v9.9.9"),
    ("name", "git-loopy 9.9.9"),
    ("html_url", "https://github.com/other/project/releases/tag/" + TAG),
    ("draft", True),
    ("prerelease", False),
    ("prerelease", "true"),
])
def test_disagreeing_release_identity_is_refused_before_downloads(
    tmp_path: Path, field: str, value: object,
) -> None:
    host = PublicRelease(tmp_path)
    host.record[field] = value

    with pytest.raises(tui_release.TuiReleaseError, match=field):
        host.verify()

    assert host.requests == [RELEASE_URL]


@pytest.mark.parametrize(("field", "value"), [
    ("browser_download_url", "https://example.invalid/substitute"),
    ("state", "starter"),
    ("size", 0),
])
def test_an_asset_must_be_uploaded_at_its_canonical_url(
    tmp_path: Path, field: str, value: object,
) -> None:
    host = PublicRelease(tmp_path)
    host.record["assets"][0][field] = value

    with pytest.raises(tui_release.TuiReleaseError, match=field):
        host.verify()

    assert host.requests == [RELEASE_URL]


@pytest.mark.parametrize("suffix", [".tar.xz", ".sha256", ".trust.json"])
def test_a_listed_but_undownloadable_asset_is_incomplete(
    tmp_path: Path, suffix: str,
) -> None:
    host = PublicRelease(tmp_path)
    url = next(url for url in host.files if url.endswith(suffix))
    del host.files[url]

    with pytest.raises(tui_release.TuiReleaseError, match="HTTP 404"):
        host.verify()


@pytest.mark.parametrize("suffix", [".tar.xz", ".sha256", ".trust.json"])
def test_public_bytes_must_match_the_verified_build(
    tmp_path: Path, suffix: str,
) -> None:
    host = PublicRelease(tmp_path)
    url = next(url for url in host.files if url.endswith(suffix))
    host.files[url] += b"changed"

    with pytest.raises(tui_release.TuiReleaseError, match="differs from the verified build"):
        host.verify()


def test_source_only_never_launches_download_or_trust_work(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)
    policy_path = host.root / "git-loopy/conformance/release-trust.json"
    policy = json.loads(policy_path.read_text())
    policy["distribution_mode"] = "source-only"
    policy_path.write_text(json.dumps(policy))

    for mode in ("source-only", "artifact-bearing", "unknown"):
        with pytest.raises(tui_release.TuiReleaseError, match="source-only|Unknown"):
            tui_release.verify_published_release(
                host.root, host.built, tag_ref=TAG,
                distribution_mode=mode, fetch=host.fetch,
            )
    assert host.requests == []


def test_changing_release_marking_during_download_cannot_complete(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)

    def fetch(url: str) -> bytes:
        if url == RELEASE_URL and host.requests:
            host.record["prerelease"] = False
        return host.fetch(url)

    with pytest.raises(tui_release.TuiReleaseError, match="changed during"):
        tui_release.verify_published_release(
            host.root, host.built, tag_ref=TAG,
            distribution_mode="artifact-bearing", fetch=fetch,
        )


def test_download_counters_are_not_release_identity(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)

    def fetch(url: str) -> bytes:
        if url == RELEASE_URL and host.requests:
            for asset in host.record["assets"]:
                asset["download_count"] = 42
        return host.fetch(url)

    assert len(tui_release.verify_published_release(
        host.root, host.built, tag_ref=TAG,
        distribution_mode="artifact-bearing", fetch=fetch,
    )) == 7


def test_an_asset_added_during_readback_changes_the_release(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)

    def fetch(url: str) -> bytes:
        if url == RELEASE_URL and host.requests:
            host.record["assets"].append({
                "name": "unexpected.txt", "state": "uploaded", "size": 4,
                "browser_download_url": DOWNLOAD_URL + "unexpected.txt",
            })
        return host.fetch(url)

    with pytest.raises(tui_release.TuiReleaseError, match="changed during"):
        tui_release.verify_published_release(
            host.root, host.built, tag_ref=TAG,
            distribution_mode="artifact-bearing", fetch=fetch,
        )


def test_an_undeclared_helper_target_is_not_part_of_the_verified_release(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)
    name = "git-loopy-tui-aarch64-pc-windows-msvc.zip"
    host.record["assets"].append({
        "name": name, "state": "uploaded", "size": 4,
        "browser_download_url": DOWNLOAD_URL + name,
    })

    with pytest.raises(tui_release.TuiReleaseError, match="unpromised helper asset"):
        host.verify()
    assert host.requests == [RELEASE_URL]


@pytest.mark.parametrize("defect", ["duplicate", "receipt-identity", "missing-local-receipt"])
def test_missing_or_ambiguous_proof_cannot_complete(tmp_path: Path, defect: str) -> None:
    host = PublicRelease(tmp_path)
    receipt = next(host.built.glob("*.trust.json"))
    if defect == "duplicate":
        host.record["assets"].append(host.record["assets"][0])
        diagnostic = "duplicates"
    elif defect == "receipt-identity":
        document = json.loads(receipt.read_text())
        document["release_version"] = "9.9.9"
        receipt.write_text(json.dumps(document))
        diagnostic = "Release version"
    else:
        receipt.unlink()
        diagnostic = "no trust receipt"

    with pytest.raises(tui_release.TuiReleaseError, match=diagnostic):
        host.verify()


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"assets": false}'])
def test_unreadable_release_readback_fails_closed(tmp_path: Path, body: bytes) -> None:
    host = PublicRelease(tmp_path)
    with pytest.raises(tui_release.TuiReleaseError, match="readback"):
        tui_release.verify_published_release(
            host.root, host.built, tag_ref=TAG,
            distribution_mode="artifact-bearing", fetch=lambda _: body,
        )


def test_final_readback_does_not_coerce_a_boolean_marking(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)

    def fetch(url: str) -> bytes:
        if url == RELEASE_URL and host.requests:
            host.record["prerelease"] = 1
        return host.fetch(url)

    with pytest.raises(tui_release.TuiReleaseError, match="prerelease|changed during"):
        tui_release.verify_published_release(
            host.root, host.built, tag_ref=TAG,
            distribution_mode="artifact-bearing", fetch=fetch,
        )


def test_unsigned_stable_cannot_be_read_back_as_a_completed_publication(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path, version="1.2.3")

    with pytest.raises(tui_release.TuiReleaseError, match="unsigned"):
        host.verify()
    assert host.requests == []


def test_unavailable_public_attestation_blocks_stable_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PublicRelease(tmp_path, version="1.2.3", signed=True)
    bundle = tmp_path / "attestation.jsonl"
    bundle.write_text("{}\n")
    monkeypatch.setattr(
        tui_release.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "not found"),
    )

    with pytest.raises(tui_release.TuiReleaseError, match="public attestation.*not found"):
        host.verify(attestation=bundle)


def test_cross_target_readback_proves_archive_shape_without_execution(tmp_path: Path) -> None:
    host = PublicRelease(tmp_path)
    archive = host.built / "git-loopy-tui-aarch64-unknown-linux-gnu.tar.xz"
    with tarfile.open(archive, "w:xz"):
        pass
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    for path in (archive, checksum):
        host.files[DOWNLOAD_URL + path.name] = path.read_bytes()
        for asset in host.record["assets"]:
            if asset["name"] == path.name:
                asset["size"] = path.stat().st_size

    with pytest.raises(tui_release.TuiReleaseError, match="carries no git-loopy-tui"):
        host.verify()


@pytest.mark.parametrize("missing", [False, True])
def test_publication_workflow_runs_the_real_readback_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], missing: bool,
) -> None:
    host = PublicRelease(tmp_path)
    if missing:
        host.record["assets"] = []
    monkeypatch.setattr(tui_release, "_download_release_file", host.fetch)
    workflow = yaml.safe_load((ROOT / ".github/workflows/tui-release.yml").read_text())
    steps = workflow["jobs"]["publish"]["steps"]
    readback = next(step for step in steps if step.get("id") == "public-readback")
    upload = next(step for step in steps if "gh release upload" in step.get("run", ""))
    assert steps.index(readback) > steps.index(upload)
    command = readback["run"].replace("${{ needs.identity.outputs.tag }}", TAG)
    command = command.replace("${{ steps.attest.outputs.bundle-path }}", "")
    arguments = shlex.split(command)
    arguments = arguments[arguments.index("git_loopy.tui_release") + 1:]
    arguments[arguments.index("--repository-root") + 1] = str(host.root)
    arguments[arguments.index("--artifact-dir") + 1] = str(host.built)

    status = tui_release.main(arguments)

    assert status == (1 if missing else 0)
    output = capsys.readouterr()
    if missing:
        assert "missing" in output.err
        assert output.out == ""
    else:
        assert len(output.out.splitlines()) == 7
        assert host.requests.count(RELEASE_URL) == 2
