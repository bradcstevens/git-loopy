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


def test_inventory_proves_a_windows_uv_tool_launcher(tmp_path: Path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    executable = appdata / "uv" / "bin" / "git-loopy.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    inventory = installation.inspect_installation(
        env={"APPDATA": str(appdata)},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

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


def test_inventory_reads_a_packed_head_ref(tmp_path: Path) -> None:
    commit = "a" * 40
    repository, executable = _write_checkout(tmp_path, commit=commit, tag_commit=commit)
    (repository / ".git" / "refs" / "heads" / "main").unlink()
    (repository / ".git" / "packed-refs").write_text(
        f"{commit} refs/heads/main\n",
        encoding="utf-8",
    )

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit == commit


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


def test_inventory_classifies_config_home_assets_from_scaffold_provenance(
    tmp_path: Path,
) -> None:
    """The inventory resolves assets only through its injected environment."""
    from git_loopy import scaffold_provenance

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    config = scope / "config.toml"
    prompt = scope / "PROMPT.md"
    catalog = scope / "skills"
    helper = scope / "bin" / "git-loopy-tui"
    catalog.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    config.write_text("[run]\n", encoding="utf-8")
    prompt.write_text("# Prompt\n", encoding="utf-8")
    helper.touch()
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"config.toml": config, "PROMPT.md": prompt},
        previous=None,
    )

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(config_home)},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.assets == (
        installation.InstalledAsset(
            name="config.toml",
            path=config,
            present=True,
            classification="untouched",
            release_version="1.2.3",
        ),
        installation.InstalledAsset(
            name="PROMPT.md",
            path=prompt,
            present=True,
            classification="untouched",
            release_version="1.2.3",
        ),
        installation.InstalledAsset(
            name="installed catalog",
            path=catalog,
            present=True,
            classification="unrecorded",
            release_version=None,
        ),
        installation.InstalledAsset(
            name="TUI helper",
            path=helper,
            present=True,
            classification="unrecorded",
            release_version=None,
        ),
    )


def test_inventory_reports_an_asset_scaffold_provenance_never_covers_as_unrecorded(
    tmp_path: Path,
) -> None:
    """An installed catalog is re-cut wholesale, so it is never operator work.

    Scaffold provenance records the Config and the prompt override and nothing
    else, so claiming a present catalog or helper as customized would refuse a
    refresh ADR-0025 says happens on every Run anyway.
    """
    scope = tmp_path / "config-home" / "git-loopy"
    (scope / "skills").mkdir(parents=True)
    helper = scope / "bin" / "git-loopy-tui"
    helper.parent.mkdir(parents=True)
    helper.touch()

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert [
        (asset.name, asset.classification)
        for asset in inventory.assets
        if asset.name in {"installed catalog", "TUI helper"}
    ] == [("installed catalog", "unrecorded"), ("TUI helper", "unrecorded")]


def test_inventory_classifies_changed_assets_as_customized(tmp_path: Path) -> None:
    from git_loopy import scaffold_provenance

    scope = tmp_path / "config-home" / "git-loopy"
    config = scope / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[run]\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"config.toml": config},
        previous=None,
    )
    config.write_text("[run]\nmodel = 'custom'\n", encoding="utf-8")

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.assets[0].classification == "customized"


def test_inventory_treats_an_asset_without_provenance_as_customized(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "config-home" / "git-loopy"
    config = scope / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[run]\n", encoding="utf-8")

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.assets[0].classification == "customized"


def test_inventory_keeps_assets_when_scaffold_provenance_cannot_be_read(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "config-home" / "git-loopy"
    config = scope / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[run]\n", encoding="utf-8")
    (scope / "scaffold-provenance.json").write_text("{", encoding="utf-8")

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.assets[0].classification == "customized"


def test_inventory_reports_an_absent_asset_as_unrecorded(tmp_path: Path) -> None:
    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert [asset.classification for asset in inventory.assets] == [
        "unrecorded",
        "unrecorded",
        "unrecorded",
        "unrecorded",
    ]


def test_inventory_separates_an_absent_asset_from_one_it_cannot_prove(
    tmp_path: Path,
) -> None:
    """Drift is only reportable against the assets that are actually there.

    Both an absent catalog and a present one are ``unrecorded``, so presence is
    the fact that keeps "you have not installed this" distinguishable from "this
    is installed and Scaffold provenance proves nothing about it".
    """
    scope = tmp_path / "config-home" / "git-loopy"
    (scope / "skills").mkdir(parents=True)

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert {asset.name: asset.present for asset in inventory.assets} == {
        "config.toml": False,
        "PROMPT.md": False,
        "installed catalog": True,
        "TUI helper": False,
    }


def test_inventory_finds_the_tui_helper_where_the_family_installs_it(
    tmp_path: Path,
) -> None:
    """The helper lives in a ``bin/`` directory, as it does in every clone.

    ``.git-loopy/bin/git-loopy-tui`` is the one path the shell installer writes
    and every Orchestrator reads, so the machine-local copy keeps the shape —
    and an executable stays out of the directory a Run reads Config from.
    """
    scope = tmp_path / "config-home" / "git-loopy"
    helper = scope / "bin" / "git-loopy-tui"
    helper.parent.mkdir(parents=True)
    helper.touch()

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert [
        (asset.path, asset.present)
        for asset in inventory.assets
        if asset.name == "TUI helper"
    ] == [(helper, True)]


def test_inventory_finds_the_windows_tui_helper_artifact(tmp_path: Path) -> None:
    """A Release publishes ``git-loopy-tui.exe`` for Windows targets.

    Inventorying only the extensionless name would report every Windows
    installation as having no helper at all.
    """
    scope = tmp_path / "config-home" / "git-loopy"
    helper = scope / "bin" / "git-loopy-tui.exe"
    helper.parent.mkdir(parents=True)
    helper.touch()

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    assert [
        (asset.path, asset.present)
        for asset in inventory.assets
        if asset.name == "TUI helper"
    ] == [(helper, True)]


def test_inventory_classifies_every_asset_scaffold_provenance_can_record(
    tmp_path: Path,
) -> None:
    """Whatever ``init`` can record, ``info`` can report drift for.

    The two lists are declared separately, so a Release that starts recording a
    new editable asset without inventorying it would leave that asset drifting
    silently — exactly the failure the record exists to end.
    """
    from git_loopy import scaffold_provenance

    scope = tmp_path / "config-home" / "git-loopy"
    scope.mkdir(parents=True)
    written = {
        name: scope / name for name in sorted(scaffold_provenance.SCAFFOLDED_ASSET_NAMES)
    }
    for name, path in written.items():
        path.write_text(f"{name} body\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets=written,
        previous=None,
    )

    inventory = installation.inspect_installation(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=tmp_path / "bin" / "git-loopy",
        release_version_reader=lambda: "1.2.3",
    )

    classified = {
        asset.path: (asset.classification, asset.release_version)
        for asset in inventory.assets
    }
    assert {path: classified.get(path) for path in written.values()} == {
        path: ("untouched", "1.2.3") for path in written.values()
    }


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
        "assets": [
            {
                "name": "config.toml",
                "path": str(tmp_path / "home/.config/git-loopy/config.toml"),
                "present": False,
                "classification": "unrecorded",
                "release_version": None,
            },
            {
                "name": "PROMPT.md",
                "path": str(tmp_path / "home/.config/git-loopy/PROMPT.md"),
                "present": False,
                "classification": "unrecorded",
                "release_version": None,
            },
            {
                "name": "installed catalog",
                "path": str(tmp_path / "home/.config/git-loopy/skills"),
                "present": False,
                "classification": "unrecorded",
                "release_version": None,
            },
            {
                "name": "TUI helper",
                "path": str(tmp_path / "home/.config/git-loopy/bin/git-loopy-tui"),
                "present": False,
                "classification": "unrecorded",
                "release_version": None,
            },
        ],
    }


def test_inventory_ignores_a_consumer_repository_around_this_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A project-local install must not adopt its host project's identity.

    Regression for the review of #534: the ``__file__`` fallback reported the
    surrounding repository's HEAD, and — when that project happened to carry a
    tag matching this Release version — a published verdict earned by another
    repository entirely.
    """
    project = tmp_path / "operator-project"
    module = project / ".venv" / "lib" / "git_loopy" / "installation.py"
    module.parent.mkdir(parents=True)
    module.touch()
    git = project / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "main").write_text(f"{'9' * 40}\n", encoding="utf-8")
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "tags").mkdir(parents=True)
    (git / "refs" / "tags" / "v1.2.3").write_text(f"{'9' * 40}\n", encoding="utf-8")

    executable = tmp_path / "elsewhere" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()

    monkeypatch.setattr(installation, "__file__", str(module))
    monkeypatch.setattr(installation, "_metadata_identity", lambda _version: None)

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit is None
    assert inventory.published is None
    assert inventory.edge_install is None


def test_inventory_still_reads_identity_from_this_modules_own_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard rejects foreign repositories without disabling the fallback."""
    repository, _ = _write_checkout(tmp_path, commit="7" * 40, tag_commit="8" * 40)
    module = repository / "git-loopy" / "python" / "git_loopy" / "installation.py"
    module.touch()

    executable = tmp_path / "elsewhere" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()

    monkeypatch.setattr(installation, "__file__", str(module))
    monkeypatch.setattr(installation, "_metadata_identity", lambda _version: None)

    inventory = installation.inspect_installation(
        env={"HOME": str(tmp_path / "home")},
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
    )

    assert inventory.resolved_commit == "7" * 40
    assert inventory.published is False
    assert inventory.edge_install is True
