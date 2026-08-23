"""Tests for the headless installation identity inventory (issue #521)."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import installation


def _write_checkout(
    tmp_path: Path, *, commit: str, tag_commit: str | None = None
) -> tuple[Path, Path]:
    repository = tmp_path / "checkout"
    executable = repository / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (repository / "git-loopy" / "python" / "git_loopy").mkdir(parents=True)
    (repository / "git-loopy" / "python" / "git_loopy" / "VERSION").touch()
    git = repository / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "main").write_text(f"{commit}\n", encoding="utf-8")
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    if tag_commit is not None:
        (git / "refs" / "tags").mkdir(parents=True)
        (git / "refs" / "tags" / "v1.2.3").write_text(
            f"{tag_commit}\n", encoding="utf-8"
        )
    return repository, executable


@pytest.mark.parametrize(
    ("env", "executable", "channel"),
    [
        (
            {"HOME": "/operator"},
            Path("/operator/.local/share/uv/tools/git-loopy/bin/git-loopy"),
            "uv-tool",
        ),
        (
            {"HOMEBREW_PREFIX": "/brew"},
            Path("/brew/Cellar/git-loopy/1.2.3/bin/git-loopy"),
            "homebrew",
        ),
    ],
)
def test_inventory_proves_supported_channel_locations(
    env: dict[str, str], executable: Path, channel: str
) -> None:
    inventory = installation.inspect_installation(
        env=env,
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.install_channel == installation.InstallChannel(
        name=channel, proven=True
    )


def test_inventory_proves_a_uv_tool_launcher_symlink(tmp_path: Path) -> None:
    target = tmp_path / ".local" / "share" / "uv" / "tools" / "git-loopy" / "bin"
    target.mkdir(parents=True)
    target_executable = target / "git-loopy"
    target_executable.touch()
    launcher = tmp_path / ".local" / "bin" / "git-loopy"
    launcher.parent.mkdir()
    launcher.symlink_to(target_executable)

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path)},
        executable_path=launcher,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.executable == str(launcher)
    assert inventory.install_channel == installation.InstallChannel(
        name="uv-tool", proven=True
    )


def test_inventory_does_not_guess_at_the_shared_xdg_bin_location() -> None:
    """The shell launcher and uv can both own this pathname."""
    inventory = installation.inspect_installation(
        env={"HOME": "/operator"},
        executable_path=Path("/operator/.local/bin/git-loopy"),
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.install_channel == installation.InstallChannel(
        name="unproven", proven=False
    )


def test_inventory_proves_the_shell_installers_own_launcher(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "git-loopy"
    executable.parent.mkdir()
    executable.write_text(
        '#!/usr/bin/env bash\nexec "/clone/git-loopy/shell/git-loopy.sh" "$@"\n',
        encoding="utf-8",
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.install_channel == installation.InstallChannel(
        name="installer-launcher", proven=True
    )


def test_inventory_distinguishes_an_edge_install_from_a_published_release(
    tmp_path: Path,
) -> None:
    edge_commit = "a" * 40
    published_commit = "b" * 40
    _repository, executable = _write_checkout(
        tmp_path, commit=edge_commit, tag_commit=published_commit
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit == edge_commit
    assert inventory.published is False
    assert inventory.edge_install is True


def test_inventory_reports_a_tagged_release_as_published(tmp_path: Path) -> None:
    commit = "c" * 40
    _repository, executable = _write_checkout(
        tmp_path, commit=commit, tag_commit=commit
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit == commit
    assert inventory.published is True
    assert inventory.edge_install is False


def test_inventory_keeps_missing_release_tag_evidence_unknown(tmp_path: Path) -> None:
    _repository, executable = _write_checkout(tmp_path, commit="d" * 40)

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.published is None
    assert inventory.edge_install is None


def test_inventory_recognizes_a_packed_annotated_release_tag(tmp_path: Path) -> None:
    commit = "e" * 40
    tag_object = "f" * 40
    repository, executable = _write_checkout(tmp_path, commit=commit)
    (repository / ".git" / "packed-refs").write_text(
        f"{tag_object} refs/tags/v1.2.3\n^{commit}\n",
        encoding="utf-8",
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.published is True
    assert inventory.edge_install is False


def test_inventory_uses_package_metadata_for_a_consumer_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    consumer_commit = "1" * 40
    repository, executable = _write_checkout(tmp_path, commit=consumer_commit)
    (repository / "git-loopy").rename(repository / "consumer")
    monkeypatch.setattr(
        installation,
        "_metadata_identity",
        lambda _version: ("2" * 40, False),
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit == "2" * 40
    assert inventory.edge_install is True


def test_inventory_does_not_report_a_consumer_head_without_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, executable = _write_checkout(tmp_path, commit="1" * 40)
    (repository / "git-loopy").rename(repository / "consumer")
    monkeypatch.setattr(installation, "_metadata_identity", lambda _version: None)

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit is None
    assert inventory.published is None
    assert inventory.edge_install is None


def test_inventory_reports_vcs_commit_with_unknown_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "3" * 40

    class _Distribution:
        def read_text(self, filename: str) -> str:
            assert filename == "direct_url.json"
            return (
                '{"url": "https://example.invalid/git-loopy", '
                '"vcs_info": {"commit_id": "' + commit + '", '
                '"requested_revision": "main"}}'
            )

    monkeypatch.setattr(installation, "distribution", lambda _name: _Distribution())

    inventory = installation.inspect_installation(
        env={"HOME": "/operator"},
        executable_path=Path("/operator/bin/git-loopy"),
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit == commit
    assert inventory.published is None
    assert inventory.edge_install is None


def test_inventory_json_shape_is_stable(tmp_path: Path) -> None:
    commit = "d" * 40
    _repository, executable = _write_checkout(
        tmp_path, commit=commit, tag_commit=commit
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.json_dict() == {
        "schema_version": 1,
        "artifact": "python-runner",
        "executable": str(executable),
        "install_channel": {"name": "unproven", "proven": False},
        "release_version": "1.2.3",
        "resolved_commit": commit,
        "published": True,
        "edge_install": False,
        "assets": [],
    }
