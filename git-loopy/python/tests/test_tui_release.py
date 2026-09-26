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
    assert FIXTURE["release_index_url_template"].startswith(
        repository.replace("https://github.com/", "https://api.github.com/repos/")
        + "/releases?"
    )


@pytest.mark.parametrize(
    "case", FIXTURE["release_resolution_cases"], ids=lambda case: case["id"]
)
def test_a_helper_resolves_the_release_the_artifact_fixture_names(
    case: dict[str, Any],
) -> None:
    if case["resolved_version"] is None:
        with pytest.raises(tui_release.TuiReleaseError) as raised:
            tui_release.resolve_published_release(
                case["declared_version"], case["published_versions"]
            )
        assert case["error"] in str(raised.value)
        return

    assert (
        tui_release.resolve_published_release(
            case["declared_version"], case["published_versions"]
        )
        == case["resolved_version"]
    )


@pytest.mark.parametrize(
    ("declared", "published", "expected"),
    [
        # A prerelease is lower precedence than its own release.
        ("1.2.3", ["1.2.3", "1.2.3-rc.1"], "1.2.3"),
        ("1.2.3-rc.1", ["1.2.3", "1.2.3-rc.1"], "1.2.3-rc.1"),
        # Numeric prerelease identifiers compare numerically, not as text.
        ("1.2.3", ["1.2.3-alpha.2", "1.2.3-alpha.10"], "1.2.3-alpha.10"),
        # Numeric identifiers rank below alphanumeric ones.
        ("1.2.3", ["1.2.3-alpha.1", "1.2.3-alpha.beta"], "1.2.3-alpha.beta"),
        # A larger set of prerelease fields wins when the prefix is equal.
        ("1.2.3", ["1.2.3-alpha", "1.2.3-alpha.1"], "1.2.3-alpha.1"),
        # Core precedence dominates any prerelease comparison.
        ("1.3.0", ["1.2.9", "1.3.0-rc.1"], "1.3.0-rc.1"),
        ("1.3.0-rc.1", ["1.2.9", "1.3.0"], "1.2.9"),
        # Build metadata is ignored for precedence, so an exact declared match
        # breaks the equal-precedence tie rather than order of discovery.
        ("1.2.3", ["1.2.3+build.5", "1.2.3"], "1.2.3"),
        ("1.2.3", ["1.2.3", "1.2.3+build.5"], "1.2.3"),
    ],
)
def test_release_resolution_follows_semantic_versioning_precedence(
    declared: str, published: list[str], expected: str
) -> None:
    """Ordering is SemVer's, not string ordering.

    ``alpha.10`` sorting below ``alpha.2`` would quietly install a helper several
    Releases stale, which the identity check cannot catch because the stale
    helper honestly reports the version it was resolved as.
    """
    assert tui_release.resolve_published_release(declared, published) == expected


@pytest.mark.parametrize(
    "version", ["1.2", "1.2.3.4", "v1.2.3", "1.2.3-01", "", "latest"]
)
def test_release_resolution_refuses_a_version_that_is_not_semantic(
    version: str,
) -> None:
    """An unparseable version is named rather than silently skipped."""
    with pytest.raises(tui_release.TuiReleaseError):
        tui_release.resolve_published_release("1.2.3", [version])


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


def _write_fake_helper(
    path: Path,
    *,
    version: str,
    script: str = "",
    event_schema_range: tuple[int, int] = (1, 1),
) -> Path:
    """A stand-in for a freshly built artifact, so the gate is testable offline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    min_schema, max_schema = event_schema_range
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf 'git-loopy-tui {version}\\n'; exit 0 ;;\n"
        "  --schema-version)\n"
        f'    printf \'{{"name": "git-loopy-tui", "version": "%s", '
        f'"min_event_schema_version": {min_schema}, "max_event_schema_version": {max_schema}, '
        f'"wrapper_contract_version": "1.0"}}\\n\' '
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
def test_refresh_machine_local_helper_replaces_it_with_this_releases_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified Release helper lands where a Run then attaches to it.

    The install and the discovery are asserted together because they are only
    one feature: a refresh that wrote anywhere else would leave the operator
    running the same stale interface it claimed to have replaced.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    helper = _write_fake_helper(
        tmp_path / artifact.executable_name,
        version="1.2.4",
    )
    archive = tmp_path / artifact.archive_name
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(helper, arcname=artifact.executable_name)
    checksum = tmp_path / artifact.checksum_name
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )
    release_index = [
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
    ]
    downloads = {
        tui_release._RUNTIME_RELEASE_INDEX_URL.format(page=1): json.dumps(
            release_index
        ).encode("utf-8"),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.archive_name
        ): archive.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.checksum_name
        ): checksum.read_bytes(),
    }
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    installed = tui_release.refresh_machine_local_helper(
        "1.2.4",
        env,
        host_system=lambda: "Darwin",
        host_machine=lambda: "arm64",
        host_libc=lambda: None,
        artifact_resolver=lambda _system, _machine, _libc: artifact,
        download=downloads.__getitem__,
    )

    assert installed in tui_release.machine_local_helper_paths(env)
    assert installed.is_file()
    assert tui_release.probe_runtime_helper(installed).reported_version == "1.2.4"
    assert tui_release.read_installed_helper_release(installed) == "1.2.4"
    assert (
        tui_release.resolve_runtime_helper(
            tmp_path / "repo",
            release_version="1.2.4",
            warn=lambda message: pytest.fail(message),
            env=env,
        )
        == installed
    )


@pytest.mark.parametrize(
    "case",
    FIXTURE["selection_cases"],
    ids=lambda case: case["id"],
)
def test_runtime_helper_selection_agrees_with_the_canonical_artifact_description(
    case: dict[str, Any],
) -> None:
    """An installed Runner has no checkout, so its built-in selector is held to it."""
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    if case["target"] is None:
        with pytest.raises(tui_release.TuiReleaseError) as raised:
            tui_release._runtime_artifact_for_host(
                case["system"],
                case["machine"],
                case["libc"],
            )
        assert case["error"] in str(raised.value)
        return
    expected = tui_release.artifact_for(
        metadata,
        tui_release.select_target(
            metadata,
            system=case["system"],
            machine=case["machine"],
            libc=case["libc"],
        ),
    )

    resolved = tui_release._runtime_artifact_for_host(
        case["system"],
        case["machine"],
        case["libc"],
    )

    assert (
        resolved.target.triple,
        resolved.archive_name,
        resolved.checksum_name,
        resolved.executable_name,
    ) == (
        expected.target.triple,
        expected.archive_name,
        expected.checksum_name,
        expected.executable_name,
    )
    assert tui_release._runtime_release_artifact_url("9.9.9", expected.archive_name) == (
        tui_release.release_artifact_url(
            metadata,
            release_version="9.9.9",
            artifact=expected.archive_name,
        )
    )


def test_machine_local_helper_paths_sit_in_a_bin_directory_beside_the_config(
    tmp_path: Path,
) -> None:
    """The one place the machine-local helper's location is spelled.

    ``update`` installs it and ``uninstall`` removes it through the installation
    inventory (ADR-0054), so the location is declared here, beside the two
    locations :func:`resolve_runtime_helper` discovers, rather than in whichever
    command happens to need it. It stays out of the directory a Run reads Config
    from, and the Windows artifact is named because a machine-local helper that
    inventoried only the extensionless name would be invisible on Windows.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    _extension, windows_suffix = metadata.archive_formats["windows"]
    config_home = tmp_path / "config-home"

    paths = tui_release.machine_local_helper_paths({"XDG_CONFIG_HOME": str(config_home)})

    bin_dir = config_home / "git-loopy" / "bin"
    assert paths == (
        bin_dir / metadata.command_name,
        bin_dir / f"{metadata.command_name}{windows_suffix}",
    )


def test_the_runtime_helper_name_is_the_one_the_release_publishes() -> None:
    """A Runner with no checkout still has to look for the published command.

    Every location this module resolves — clone-local, ``PATH``, and the
    machine-local copy — is built from ``HELPER_COMMAND_NAME``, which restates
    what the canonical artifact description declares because an installed Runner
    cannot read that file. A rename there would otherwise leave every one of
    them looking for a command no Release publishes any more.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    _extension, windows_suffix = metadata.archive_formats["windows"]

    assert tui_release.HELPER_COMMAND_NAME == metadata.command_name
    assert tui_release._WINDOWS_EXECUTABLE_SUFFIX == windows_suffix


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_prefers_the_clone_local_binary_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = tui_release.helper_release_version(REPOSITORY_ROOT)
    repo_root = tmp_path / "repo"
    clone_local = _write_fake_helper(
        repo_root / ".git-loopy/bin/git-loopy-tui", version=version
    )
    path_helper = _write_fake_helper(tmp_path / "path-bin/git-loopy-tui", version=version)
    monkeypatch.setenv("PATH", str(path_helper.parent))

    helper = tui_release.resolve_runtime_helper(
        repo_root, release_version=version, warn=lambda _message: None
    )

    assert helper == clone_local


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_rejects_a_clone_local_release_mismatch(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _write_fake_helper(repo_root / ".git-loopy/bin/git-loopy-tui", version="9.9.9")
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        repo_root,
        release_version="1.2.3",
        warn=warnings.append,
    )

    assert helper is None
    assert len(warnings) == 1
    assert "clone-local" in warnings[0]
    assert "9.9.9" in warnings[0]


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_attaches_to_the_helper_update_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper ``update`` refreshes is one a Run can actually attach with.

    ``update`` (ADR-0054) installs into the machine-local slot, which nothing
    else writes.  A Run that could not discover it there would leave the
    Dashboard stuck behind the Orchestrator no matter how often the operator
    refreshed the helper.
    """
    config_home = tmp_path / "config-home"
    machine_local = _write_fake_helper(
        tui_release.machine_local_helper_paths({"XDG_CONFIG_HOME": str(config_home)})[0],
        version="1.2.3",
    )
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=warnings.append,
        env={"XDG_CONFIG_HOME": str(config_home)},
    )

    assert helper == machine_local
    assert warnings == []


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_ranks_clone_local_then_machine_local_then_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order only means something when more than one candidate exists.

    Machine-local sitting *above* ``PATH`` is the whole point of refreshing it:
    an operator who runs ``update`` and then keeps attaching to the older helper
    another package manager put on their ``PATH`` got nothing for it.  Each rank
    is therefore taken away in turn rather than tested alone.
    """
    version = "1.2.3"
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    repo_root = tmp_path / "repo"
    clone_local = _write_fake_helper(
        repo_root / ".git-loopy/bin/git-loopy-tui", version=version
    )
    machine_local = _write_fake_helper(
        tui_release.machine_local_helper_paths(env)[0], version=version
    )
    path_helper = _write_fake_helper(tmp_path / "path-bin/git-loopy-tui", version=version)
    monkeypatch.setenv("PATH", str(path_helper.parent))

    def resolve() -> Path | None:
        return tui_release.resolve_runtime_helper(
            repo_root,
            release_version=version,
            warn=lambda message: pytest.fail(message),
            env=env,
        )

    assert resolve() == clone_local
    clone_local.unlink()
    assert resolve() == machine_local
    machine_local.unlink()
    assert resolve() == path_helper


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_fails_closed_on_a_machine_local_release_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git-loopy's own artifact is held to Release equality, and names its repair.

    The machine-local helper is a component of this packaged distribution, so
    the Wrapper contract requires exact Release equality and a fail-closed
    response to drift — unlike a ``PATH`` helper, which another package manager
    owns and which may attach with a warning. The one thing that produces this
    state is an ``upgrade`` that has outrun its ``update``, so the warning says
    so rather than only reporting the two versions.
    """
    config_home = tmp_path / "config-home"
    _write_fake_helper(
        tui_release.machine_local_helper_paths({"XDG_CONFIG_HOME": str(config_home)})[0],
        version="9.9.9",
    )
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=warnings.append,
        env={"XDG_CONFIG_HOME": str(config_home)},
    )

    assert helper is None
    assert len(warnings) == 1
    assert "machine-local" in warnings[0]
    assert "9.9.9" in warnings[0]
    assert "git-loopy update" in warnings[0]


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_accepts_a_path_release_mismatch_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper_path = _write_fake_helper(tmp_path / "bin/git-loopy-tui", version="9.9.9")
    monkeypatch.setenv("PATH", str(helper_path.parent))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=warnings.append,
    )

    assert helper == helper_path
    assert len(warnings) == 1
    assert "PATH" in warnings[0]
    assert "9.9.9" in warnings[0]


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_attaches_to_verified_machine_local_fallback_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    machine_local = _write_fake_helper(helper_path, version="0.8.0")
    record = helper_path.parent / f"{helper_path.name}.release"
    record.write_text("0.8.0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=warnings.append,
        env=env,
    )

    assert helper == machine_local
    assert warnings == []


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_attaches_to_verified_clone_local_fallback_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    helper_path = repo_root / ".git-loopy" / "bin" / "git-loopy-tui"
    clone_local = _write_fake_helper(helper_path, version="0.8.0")
    record = helper_path.parent / f"{helper_path.name}.release"
    record.write_text("0.8.0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        repo_root,
        release_version="1.2.3",
        warn=warnings.append,
    )

    assert helper == clone_local
    assert warnings == []


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_refuses_tampered_machine_local_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    _write_fake_helper(helper_path, version="0.8.0")
    record = helper_path.parent / f"{helper_path.name}.release"
    record.write_text("0.8.1\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=warnings.append,
        env=env,
    )

    assert helper is None
    assert len(warnings) == 1
    assert "machine-local" in warnings[0]
    assert "0.8.0" in warnings[0]
    assert "git-loopy update" in warnings[0]


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_runtime_helper_refuses_newer_machine_local_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    _write_fake_helper(helper_path, version="2.0.0")
    record = helper_path.parent / f"{helper_path.name}.release"
    record.write_text("2.0.0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    warnings: list[str] = []

    helper = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=warnings.append,
        env=env,
    )

    assert helper is None
    assert len(warnings) == 1
    assert "machine-local" in warnings[0]
    assert "2.0.0" in warnings[0]
    assert "git-loopy update" in warnings[0]


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_selects_older_fallback_when_exact_version_is_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    helper = _write_fake_helper(
        tmp_path / artifact.executable_name,
        version="1.2.4",
    )
    archive = tmp_path / artifact.archive_name
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(helper, arcname=artifact.executable_name)
    checksum = tmp_path / artifact.checksum_name
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )
    release_index = [
        {
            "tag_name": "v1.2.5-alpha.1",
            "draft": False,
            "assets": [],  # source-only!
        },
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        },
    ]
    downloads = {
        tui_release._RUNTIME_RELEASE_INDEX_URL.format(page=1): json.dumps(
            release_index
        ).encode("utf-8"),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.archive_name
        ): archive.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.checksum_name
        ): checksum.read_bytes(),
    }
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    installed = tui_release.refresh_machine_local_helper(
        "1.2.5-alpha.1",
        env,
        host_system=lambda: "Darwin",
        host_machine=lambda: "arm64",
        host_libc=lambda: None,
        artifact_resolver=lambda _system, _machine, _libc: artifact,
        download=downloads.__getitem__,
    )

    assert installed in tui_release.machine_local_helper_paths(env)
    assert installed.is_file()
    assert tui_release.probe_runtime_helper(installed).reported_version == "1.2.4"
    assert tui_release.read_installed_helper_release(installed) == "1.2.4"
    assert (
        tui_release.resolve_runtime_helper(
            tmp_path / "repo",
            release_version="1.2.5-alpha.1",
            warn=lambda message: pytest.fail(message),
            env=env,
        )
        == installed
    )


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
@pytest.mark.parametrize("published", [(), ("1.2.4",)])
def test_refresh_preserves_a_matching_local_build_for_an_explicit_source_only_release(
    tmp_path: Path, published: tuple[str, ...]
) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    installed = _write_fake_helper(
        tui_release.machine_local_helper_paths(env)[0], version="1.2.5-alpha.1"
    )
    record = tui_release.helper_release_record_path(installed)
    record.write_text("1.2.5-alpha.1\n", encoding="utf-8")
    before = installed.read_bytes(), record.read_bytes()
    policy_url = (
        "https://raw.githubusercontent.com/bradcstevens/git-loopy/"
        "v1.2.5-alpha.1/git-loopy/conformance/release-trust.json"
    )
    requested: list[str] = []

    def download(url: str) -> bytes:
        requested.append(url)
        assert url == policy_url, "a matching local source build must not be downgraded"
        return json.dumps({
            "distribution_mode": "source-only",
            "distribution_modes": ["source-only", "artifact-bearing"],
        }).encode()

    refreshed = tui_release.refresh_machine_local_helper(
        "1.2.5-alpha.1",
        env,
        host_system=lambda: "Darwin",
        host_machine=lambda: "arm64",
        host_libc=lambda: None,
        releases_fetcher=lambda _artifact: published,
        download=download,
    )

    assert refreshed == installed
    assert (installed.read_bytes(), record.read_bytes()) == before
    assert requested == [policy_url]
    assert tui_release.resolve_runtime_helper(
        tmp_path / "repo", release_version="1.2.5-alpha.1",
        warn=lambda message: pytest.fail(message), env=env,
    ) == installed


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
@pytest.mark.parametrize(
    "policy",
    [
        b'{"distribution_mode":"artifact-bearing","distribution_modes":["source-only","artifact-bearing"]}',
        b'{"distribution_mode":"unknown","distribution_modes":["source-only"]}',
        b'{"distribution_modes":["source-only"]}',
        b"not JSON",
    ],
)
def test_refresh_does_not_infer_source_only_from_missing_assets(
    tmp_path: Path, policy: bytes
) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    installed = _write_fake_helper(
        tui_release.machine_local_helper_paths(env)[0], version="1.2.5-alpha.1"
    )
    before = installed.read_bytes()

    with pytest.raises(tui_release.TuiReleaseError):
        tui_release.refresh_machine_local_helper(
            "1.2.5-alpha.1", env,
            host_system=lambda: "Darwin", host_machine=lambda: "arm64",
            host_libc=lambda: None, releases_fetcher=lambda _artifact: (),
            download=lambda _url: policy,
        )

    assert installed.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_a_matching_local_helper_cannot_hide_an_unreadable_release_index(
    tmp_path: Path,
) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    _write_fake_helper(
        tui_release.machine_local_helper_paths(env)[0], version="1.2.5-alpha.1"
    )

    def unavailable(_url: str) -> bytes:
        raise tui_release.TuiReleaseError("release index unavailable")

    with pytest.raises(tui_release.TuiReleaseError, match="release index unavailable"):
        tui_release.refresh_machine_local_helper(
            "1.2.5-alpha.1", env,
            host_system=lambda: "Darwin", host_machine=lambda: "arm64",
            host_libc=lambda: None, download=unavailable,
        )


def test_the_release_index_reader_stops_paginating_on_an_endless_index(
    tmp_path: Path,
) -> None:
    """A server that never shortens a page must not hang maintenance forever.

    ``update`` walks the Release index over the network. GitHub returns releases
    newest-first, so a bounded walk still sees every candidate that could win
    newest-at-or-below; an unbounded one is just a way for a misbehaving host to
    stall the operator's terminal.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    full_page = [
        {
            "tag_name": f"v1.0.{index}",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
        for index in range(100)
    ]
    pages: list[int] = []

    def _fetch(url: str) -> bytes:
        pages.append(len(pages) + 1)
        if len(pages) > 200:  # the test itself must not hang
            raise AssertionError("pagination did not terminate")
        return json.dumps(full_page).encode("utf-8")

    published = tui_release.fetch_published_helper_releases(artifact, fetch=_fetch)

    assert len(pages) <= tui_release._RELEASE_INDEX_PAGE_LIMIT
    assert published


def test_the_runtime_url_templates_mirror_the_declared_artifact_metadata() -> None:
    """Maintenance builds Release URLs from constants, so they must not drift.

    ``refresh_machine_local_helper`` runs inside an *installed* Runner, which has
    no source checkout to read ``tui-artifacts.json`` from. It therefore mirrors
    the fixture's two templates as module constants; this pins the mirror so the
    fixture cannot move without the Runner moving with it.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)

    assert tui_release._RUNTIME_RELEASE_URL == metadata.release_download_url_template
    assert tui_release._RUNTIME_RELEASE_INDEX_URL == (
        metadata.release_index_url_template
    )


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_reads_the_index_independently_of_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maintenance is not steered by whatever tree the operator happens to stand in.

    An installed Runner is run from arbitrary directories. Resolving the Release
    index through a fixture found relative to the working directory would let an
    unrelated checkout redirect where ``update`` looks for helpers.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    helper = _write_fake_helper(tmp_path / artifact.executable_name, version="1.2.4")
    archive = tmp_path / artifact.archive_name
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(helper, arcname=artifact.executable_name)
    checksum = tmp_path / artifact.checksum_name
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )

    # A tree the operator merely happens to stand in, naming another index.
    foreign = tmp_path / "foreign"
    doctored = dict(FIXTURE)
    doctored["release_index_url_template"] = "https://example.invalid/releases?{page}"
    fixture_path = foreign / "git-loopy" / "conformance" / "tui-artifacts.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps(doctored), encoding="utf-8")
    monkeypatch.chdir(foreign)

    release_index = [
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
    ]
    downloads = {
        tui_release._RUNTIME_RELEASE_INDEX_URL.format(page=1): json.dumps(
            release_index
        ).encode("utf-8"),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.archive_name
        ): archive.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.checksum_name
        ): checksum.read_bytes(),
    }
    requested: list[str] = []

    def _download(url: str) -> bytes:
        requested.append(url)
        return downloads[url]

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    installed = tui_release.refresh_machine_local_helper(
        "1.2.5-alpha.1",
        env,
        host_system=lambda: "Darwin",
        host_machine=lambda: "arm64",
        host_libc=lambda: None,
        artifact_resolver=lambda _system, _machine, _libc: artifact,
        download=_download,
    )

    assert installed.is_file()
    assert not any("example.invalid" in url for url in requested)


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_preserves_previous_installation_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    existing = _write_fake_helper(helper_path, version="1.2.0")
    record = helper_path.parent / f"{helper_path.name}.release"
    record.write_text("1.2.0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    release_index = [
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
    ]

    def _failing_download(url: str) -> bytes:
        if "releases?per_page" in url:
            return json.dumps(release_index).encode("utf-8")
        raise OSError("network error downloading archive")

    with pytest.raises(tui_release.TuiReleaseError) as exc:
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            host_system=lambda: "Darwin",
            host_machine=lambda: "arm64",
            host_libc=lambda: None,
            artifact_resolver=lambda _system, _machine, _libc: artifact,
            download=_failing_download,
        )
    assert "cannot download release artifact" in str(exc.value)

    # Existing installation must remain intact and discoverable
    assert existing.is_file()
    assert record.read_text(encoding="utf-8").strip() == "1.2.0"
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.4",
        warn=lambda message: pytest.fail(message),
        env=env,
    )
    assert attached == existing


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_refuses_a_newer_only_published_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A helper newer than the installed Runner is never selected.

    Falling *forward* would attach a Run to a helper built against a later
    Event schema than the Runner emits, which is the drift the resolved-identity
    record exists to prevent rather than to excuse.
    """
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    existing = _write_fake_helper(helper_path, version="1.2.0")
    record = tui_release.helper_release_record_path(helper_path)
    record.write_text("1.2.0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    release_index = [
        {
            "tag_name": tag,
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
        for tag in ("v2.0.0", "v1.3.0")
    ]

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            host_system=lambda: "Darwin",
            host_machine=lambda: "arm64",
            host_libc=lambda: None,
            artifact_resolver=lambda _system, _machine, _libc: artifact,
            download=lambda _url: json.dumps(release_index).encode("utf-8"),
        )

    assert "no published git-loopy-tui Release carrying" in str(raised.value)
    assert existing.is_file()
    assert record.read_text(encoding="utf-8").strip() == "1.2.0"


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_refuses_a_damaged_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A published archive whose checksum does not hold is refused, not installed.

    A damaged or tampered artifact is not a reason to fall further back: that
    would let anyone who can corrupt one Release silently downgrade the helper.
    The previous verified installation stays usable instead.
    """
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    existing = _write_fake_helper(helper_path, version="1.2.0")
    record = tui_release.helper_release_record_path(helper_path)
    record.write_text("1.2.0\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    release_index = [
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
    ]
    checksum_body = (
        f"{hashlib.sha256(b'the artifact this Release promised').hexdigest()}  "
        f"{artifact.archive_name}\n"
    )

    def _download(url: str) -> bytes:
        if "releases?per_page" in url:
            return json.dumps(release_index).encode("utf-8")
        if url.endswith(".sha256"):
            return checksum_body.encode("utf-8")
        return b"the artifact an attacker substituted"

    with pytest.raises(tui_release.TuiReleaseError):
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            host_system=lambda: "Darwin",
            host_machine=lambda: "arm64",
            host_libc=lambda: None,
            artifact_resolver=lambda _system, _machine, _libc: artifact,
            download=_download,
        )

    assert existing.is_file()
    assert tui_release.probe_runtime_helper(existing).reported_version == "1.2.0"
    assert record.read_text(encoding="utf-8").strip() == "1.2.0"


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_rollback_on_activation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    helper_path = tui_release.machine_local_helper_paths(env)[0]
    existing = _write_fake_helper(helper_path, version="1.2.0")
    record = helper_path.parent / f"{helper_path.name}.release"
    record.write_text("1.2.0\n", encoding="utf-8")

    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    helper = _write_fake_helper(
        tmp_path / artifact.executable_name,
        version="1.2.4",
    )
    archive = tmp_path / artifact.archive_name
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(helper, arcname=artifact.executable_name)
    checksum = tmp_path / artifact.checksum_name
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )
    release_index = [
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
    ]
    downloads = {
        tui_release._RUNTIME_RELEASE_INDEX_URL.format(page=1): json.dumps(
            release_index
        ).encode("utf-8"),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.archive_name
        ): archive.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.checksum_name
        ): checksum.read_bytes(),
    }

    # Simulate activation failure on replacing the executable
    real_replace = os.replace
    def _faulty_replace(src: object, dst: object) -> None:
        if str(dst).endswith(artifact.executable_name):
            raise OSError("permission denied during activation")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _faulty_replace)

    with pytest.raises(tui_release.TuiReleaseError) as exc:
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            host_system=lambda: "Darwin",
            host_machine=lambda: "arm64",
            host_libc=lambda: None,
            artifact_resolver=lambda _system, _machine, _libc: artifact,
            download=downloads.__getitem__,
        )
    assert "cannot activate TUI helper" in str(exc.value)

    # Rollback must restore the previous helper and record
    assert existing.is_file()
    assert record.read_text(encoding="utf-8").strip() == "1.2.0"
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.4",
        warn=lambda message: pytest.fail(message),
        env=env,
    )
    assert attached == existing


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_skips_incompatible_helper_for_older_compatible_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    # Helper 1.2.4 is incompatible (event schema 99)
    incompat_helper = _write_fake_helper(
        tmp_path / "incompat" / artifact.executable_name,
        version="1.2.4",
        event_schema_range=(99, 99),
    )
    archive_124 = tmp_path / "124" / artifact.archive_name
    archive_124.parent.mkdir(parents=True)
    with tarfile.open(archive_124, "w:xz") as bundle:
        bundle.add(incompat_helper, arcname=artifact.executable_name)
    checksum_124 = tmp_path / "124" / artifact.checksum_name
    checksum_124.write_text(
        f"{hashlib.sha256(archive_124.read_bytes()).hexdigest()}  {archive_124.name}\n",
        encoding="utf-8",
    )

    # Helper 1.2.3 is compatible (event schema 1)
    compat_helper = _write_fake_helper(
        tmp_path / "compat" / artifact.executable_name,
        version="1.2.3",
        event_schema_range=(1, 1),
    )
    archive_123 = tmp_path / "123" / artifact.archive_name
    archive_123.parent.mkdir(parents=True)
    with tarfile.open(archive_123, "w:xz") as bundle:
        bundle.add(compat_helper, arcname=artifact.executable_name)
    checksum_123 = tmp_path / "123" / artifact.checksum_name
    checksum_123.write_text(
        f"{hashlib.sha256(archive_123.read_bytes()).hexdigest()}  {archive_123.name}\n",
        encoding="utf-8",
    )

    release_index = [
        {
            "tag_name": "v1.2.4",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        },
        {
            "tag_name": "v1.2.3",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        },
    ]

    downloads = {
        tui_release._RUNTIME_RELEASE_INDEX_URL.format(page=1): json.dumps(
            release_index
        ).encode("utf-8"),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.archive_name
        ): archive_124.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.4", artifact=artifact.checksum_name
        ): checksum_124.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.3", artifact=artifact.archive_name
        ): archive_123.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="1.2.3", artifact=artifact.checksum_name
        ): checksum_123.read_bytes(),
    }
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    installed = tui_release.refresh_machine_local_helper(
        "1.2.4",
        env,
        host_system=lambda: "Darwin",
        host_machine=lambda: "arm64",
        host_libc=lambda: None,
        artifact_resolver=lambda _system, _machine, _libc: artifact,
        download=downloads.__getitem__,
    )

    assert installed.is_file()
    assert tui_release.probe_runtime_helper(installed).reported_version == "1.2.3"
    assert tui_release.read_installed_helper_release(installed) == "1.2.3"


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_an_all_incompatible_history_refuses_by_naming_its_newest_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name the published Release that was rejected, not where it was unpacked.

    Resolution is newest-at-or-below, so the newest rejected candidate is the
    helper the operator expected to receive and the one whose incompatibility
    explains the refusal. The scratch directory each candidate is verified in is
    deleted before the refusal reaches anybody, so a message that names it sends
    the operator to a path that no longer exists.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    downloads: dict[str, bytes] = {}
    release_index = []
    for version, schema in (("1.2.4", (99, 99)), ("1.0.0", (50, 50))):
        helper = _write_fake_helper(
            tmp_path / version / artifact.executable_name,
            version=version,
            event_schema_range=schema,
        )
        archive = tmp_path / version / artifact.archive_name
        with tarfile.open(archive, "w:xz") as bundle:
            bundle.add(helper, arcname=artifact.executable_name)
        checksum = tmp_path / version / artifact.checksum_name
        checksum.write_text(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
            encoding="utf-8",
        )
        release_index.append(
            {
                "tag_name": f"v{version}",
                "draft": False,
                "assets": [
                    {"name": artifact.archive_name},
                    {"name": artifact.checksum_name},
                ],
            }
        )
        downloads[
            tui_release.release_artifact_url(
                metadata, release_version=version, artifact=artifact.archive_name
            )
        ] = archive.read_bytes()
        downloads[
            tui_release.release_artifact_url(
                metadata, release_version=version, artifact=artifact.checksum_name
            )
        ] = checksum.read_bytes()
    downloads[tui_release._RUNTIME_RELEASE_INDEX_URL.format(page=1)] = json.dumps(
        release_index
    ).encode("utf-8")

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(tui_release.TuiReleaseError) as raised:
        tui_release.refresh_machine_local_helper(
            "1.3.0",
            env,
            host_system=lambda: "Darwin",
            host_machine=lambda: "arm64",
            host_libc=lambda: None,
            artifact_resolver=lambda _system, _machine, _libc: artifact,
            download=downloads.__getitem__,
        )

    message = str(raised.value)
    assert "1.2.4" in message
    assert artifact.archive_name in message
    assert str(config_home) not in message


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_refresh_machine_local_helper_distinguishes_index_failure_and_absence(
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )

    # 1. Unreadable index (malformed JSON)
    with pytest.raises(tui_release.TuiReleaseError) as exc:
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            artifact_resolver=lambda _s, _m, _l: artifact,
            download=lambda _url: b"NOT VALID JSON",
        )
    assert "cannot read published helper Releases from" in str(exc.value)

    # 2. Absence of usable helper (all-source-only or newer-only)
    empty_index = [{"tag_name": "v1.2.4", "draft": False, "assets": []}]
    with pytest.raises(tui_release.TuiReleaseError) as exc:
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            artifact_resolver=lambda _s, _m, _l: artifact,
            download=lambda _url: json.dumps(empty_index).encode("utf-8"),
        )
    assert "no published git-loopy-tui Release carrying" in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_an_absent_helper_refusal_names_the_host_artifact_it_required(
    tmp_path: Path,
) -> None:
    """Absence is only actionable once the operator knows what was looked for.

    Every published Release carrying no asset at all and every published Release
    carrying every host's asset but this one produce the same outcome, and an
    operator cannot tell a deferred platform from an unpublished helper unless
    the refusal names the archive it required.
    """
    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    index = [{"tag_name": "v1.2.4", "draft": False, "assets": []}]

    with pytest.raises(tui_release.TuiReleaseError) as exc:
        tui_release.refresh_machine_local_helper(
            "1.2.4",
            env,
            artifact_resolver=lambda _s, _m, _l: artifact,
            download=lambda _url: json.dumps(index).encode("utf-8"),
        )

    message = str(exc.value)
    assert artifact.archive_name in message
    assert "1.2.4" in message


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
