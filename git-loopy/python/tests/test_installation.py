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
