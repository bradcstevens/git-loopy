"""Tests for the machine-local ``git-loopy update`` command."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from functools import partial
from pathlib import Path
from typing import Mapping

import pytest

from git_loopy.skill_install import RefreshOutcome


def _refreshed_catalog(root: Path) -> RefreshOutcome:
    """One successful catalog refresh, reported by the shared renderer."""
    from git_loopy import skill_install

    return skill_install.RefreshOutcome(
        catalog=skill_install.InstalledCatalog(
            root=root,
            repository="bradcstevens/git-loopy-skills",
            revision="a" * 40,
            skills=("tdd",),
            sha256="0" * 64,
        ),
        action=skill_install.ACTION_UPDATED,
    )


def _absent_config(config_home: Path) -> str:
    """The verdict ``update`` reports over a config-home with no Config at all.

    Every asset test below runs against one, so the Config repair leads their
    report with this line. It is deliberately not the clean-Config verdict:
    ``update`` names each asset it left alone — and says "PROMPT.md is not
    installed" rather than calling an absent prompt untouched — so a Config that
    is not there has to be reported as not there.
    """
    return (
        "The global Config is not installed "
        f"({config_home / 'git-loopy' / 'config.toml'})."
    )


def _clean_config(config_home: Path) -> str:
    """The verdict over a global Config that exists and has nothing retired."""
    return (
        "No retired routing keys in the global Config "
        f"({config_home / 'git-loopy' / 'config.toml'})."
    )


def test_update_replaces_an_untouched_prompt_override(tmp_path: Path) -> None:
    """An override still matching Scaffold provenance moves to this Release."""
    from git_loopy import scaffold_provenance, skill_install, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    prompt = scope / "PROMPT.md"
    source = tmp_path / "packaged-PROMPT.md"
    scope.mkdir(parents=True)
    prompt.write_text("# Previous Release\n", encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"PROMPT.md": prompt},
        previous=None,
    )
    output: list[str] = []
    catalog = _refreshed_catalog(tmp_path)

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        catalog_refresh=lambda _env: catalog,
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    assert prompt.read_text(encoding="utf-8") == "# Current Release\n"
    provenance = scaffold_provenance.read_scaffold_provenance(scope)
    assert provenance is not None
    assert provenance.assets["PROMPT.md"].release_version == "1.2.4"
    assert output == [
        _absent_config(config_home),
        "Updated PROMPT.md to Release 1.2.4.",
        skill_install.describe_refresh(catalog),
        f"TUI helper ready: {tmp_path / 'git-loopy-tui'}",
    ]


def test_update_preserves_a_customized_prompt_and_reports_upstream_changes(
    tmp_path: Path,
) -> None:
    """Prose changed by an operator stays byte-identical rather than being merged."""
    from git_loopy import scaffold_provenance, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    prompt = scope / "PROMPT.md"
    source = tmp_path / "packaged-PROMPT.md"
    scope.mkdir(parents=True)
    prompt.write_text("# Operator instructions\n", encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    recorded_source = tmp_path / "previous-PROMPT.md"
    recorded_source.write_text("# Previous Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"PROMPT.md": recorded_source},
        previous=None,
    )
    original = prompt.read_bytes()
    output: list[str] = []

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        previous_prompt_fetcher=lambda version: recorded_source.read_text(encoding="utf-8"),
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    assert prompt.read_bytes() == original
    assert output[0] == _absent_config(config_home)
    assert output[1] == "Left customized PROMPT.md from Release 1.2.3 unchanged."
    assert output[2] == (
        "Upstream PROMPT.md changes since Release 1.2.3: 1 line added, 1 line removed."
    )


def test_update_treats_an_unrecorded_prompt_as_customized(tmp_path: Path) -> None:
    """A prompt predating Scaffold provenance is never a replacement candidate."""
    from git_loopy import updatecmd

    config_home = tmp_path / "config-home"
    prompt = config_home / "git-loopy" / "PROMPT.md"
    source = tmp_path / "packaged-PROMPT.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("# Existing instructions\n", encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    original = prompt.read_bytes()
    output: list[str] = []

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    assert prompt.read_bytes() == original
    assert output[0] == _absent_config(config_home)
    assert output[1] == "Left unrecorded PROMPT.md unchanged; treating it as customized."


def test_update_refreshes_managed_assets_when_upstream_prompt_summary_fails(
    tmp_path: Path,
) -> None:
    """A summary failure does not skip the independent machine-managed refreshes."""
    from git_loopy import scaffold_provenance, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    prompt = scope / "PROMPT.md"
    source = tmp_path / "packaged-PROMPT.md"
    scope.mkdir(parents=True)
    prompt.write_text("# Operator instructions\n", encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    recorded_source = tmp_path / "previous-PROMPT.md"
    recorded_source.write_text("# Previous Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"PROMPT.md": recorded_source},
        previous=None,
    )
    refreshed: list[str] = []
    output: list[str] = []

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        previous_prompt_fetcher=lambda _version: (_ for _ in ()).throw(
            OSError("offline")
        ),
        catalog_refresh=lambda _env: refreshed.append("catalog")
        or _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: refreshed.append("helper")
        or tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 1
    assert refreshed == ["catalog", "helper"]
    assert output[2] == "Could not summarize upstream PROMPT.md changes: offline"


def test_a_failed_record_write_leaves_a_prompt_the_next_update_will_not_replace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The record and the content may only ever disagree in the safe direction.

    The record write is the last step and is atomic, so the one state a failure
    can leave is a record that still describes the *previous* content: the
    digest no longer matches, which every consumer reads as customized and no
    consumer will replace.  Deleting the record first would buy the same
    protection for the prompt while taking the Config's entry with it — the
    record ``update`` (#527) needs to repair a Release-retired key.
    """
    from git_loopy import scaffold_provenance, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    prompt = scope / "PROMPT.md"
    config = scope / "config.toml"
    source = tmp_path / "packaged-PROMPT.md"
    scope.mkdir(parents=True)
    prompt.write_text("# Previous Release\n", encoding="utf-8")
    config.write_text('model = "claude-opus-4.8"\n', encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"PROMPT.md": prompt, "config.toml": config},
        previous=None,
    )
    scaffolded = scaffold_provenance.read_scaffold_provenance(scope)
    assert scaffolded is not None

    def fail_record(_scope: Path, **_kwargs: object) -> Path:
        raise scaffold_provenance.ScaffoldProvenanceError("record write failed")

    monkeypatch.setattr(updatecmd, "record_scaffolded_assets", fail_record)

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=lambda _line: None,
    )

    assert result == 1
    assert prompt.read_text(encoding="utf-8") == "# Current Release\n"
    assert scaffold_provenance.read_scaffold_provenance(scope) == scaffolded

    monkeypatch.undo()
    newer = tmp_path / "newer-PROMPT.md"
    newer.write_text("# Newer Release\n", encoding="utf-8")
    output: list[str] = []

    assert (
        updatecmd.run_update(
            env={"XDG_CONFIG_HOME": str(config_home)},
            release_version_reader=lambda: "1.2.5",
            packaged_prompt=newer,
            previous_prompt_fetcher=lambda _version: "# Previous Release\n",
            catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
            helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
            output_fn=output.append,
        )
        == 0
    )
    assert prompt.read_text(encoding="utf-8") == "# Current Release\n"
    assert output[0] == _clean_config(config_home)
    assert output[1] == "Left customized PROMPT.md from Release 1.2.3 unchanged."


def test_update_drives_the_real_refreshers_when_no_seam_is_injected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refreshers every other test injects are not the ones production runs.

    Both machine-managed assets reach ``update`` through an injected callable, so
    without this every one of those tests would stay green against a default that
    refreshed nothing at all.  This is the one test that exercises the wiring an
    operator gets, and it holds both defaults to the same environment the command
    was given.
    """
    from git_loopy import skill_install, tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    source = tmp_path / "packaged-PROMPT.md"
    source.write_text("# Current Release\n", encoding="utf-8")
    catalog = skill_install.InstalledCatalog(
        root=config_home / "git-loopy" / "skills",
        repository="bradcstevens/git-loopy-skills",
        revision="b" * 40,
        skills=("tdd",),
        sha256="0" * 64,
    )
    refreshed_with: list[Mapping[str, str] | None] = []
    installed_with: list[tuple[str, Mapping[str, str]]] = []

    def _refresh_catalog(
        _pin: object = None, **kwargs: object
    ) -> skill_install.RefreshOutcome:
        refreshed_with.append(kwargs.get("env"))  # type: ignore[arg-type]
        return skill_install.RefreshOutcome(
            catalog=catalog, action=skill_install.ACTION_UPDATED
        )

    def _refresh_helper(version: str, helper_env: Mapping[str, str]) -> Path:
        installed_with.append((version, helper_env))
        return config_home / "git-loopy" / "bin" / "git-loopy-tui"

    monkeypatch.setattr(skill_install, "refresh_installed_catalog", _refresh_catalog)
    monkeypatch.setattr(tui_release, "refresh_machine_local_helper", _refresh_helper)
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        output_fn=output.append,
    )

    assert result == 0
    assert refreshed_with == [env]
    assert installed_with == [("1.2.4", env)]
    assert catalog.short_revision in output[2] and catalog.repository in output[2]
    assert output[3] == (
        f"TUI helper ready: {config_home / 'git-loopy' / 'bin' / 'git-loopy-tui'}"
    )


def test_update_completes_outside_a_repository_without_reaching_the_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``update`` is about this machine, not about wherever the operator stands.

    Driven end to end through the real entry point and the real process
    environment, from a directory that is no repository, with every process
    launch an assertion failure.  That one prohibition covers both promises: the
    repository root is resolved by ``git rev-parse`` and the tracker is reached
    by ``gh``, so a command that starts neither can require neither and can write
    to no tracker under any flag it may later grow.
    """
    import subprocess

    from git_loopy import cli as cli_module
    from git_loopy import skill_install, tui_release

    config_home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        skill_install,
        "refresh_installed_catalog",
        lambda *_args, **_kwargs: skill_install.RefreshOutcome(
            catalog=skill_install.InstalledCatalog(
                root=config_home / "git-loopy" / "skills",
                repository="bradcstevens/git-loopy-skills",
                revision="c" * 40,
                skills=("tdd",),
                sha256="0" * 64,
            ),
            action=skill_install.ACTION_CURRENT,
        ),
    )
    monkeypatch.setattr(
        tui_release,
        "refresh_machine_local_helper",
        lambda _version, _env: config_home / "git-loopy" / "bin" / "git-loopy-tui",
    )

    def _no_process(*args: object, **_kwargs: object) -> None:
        raise AssertionError(f"update must not start a process: {args!r}")

    for launcher in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, launcher, _no_process)

    assert cli_module.main(["update"]) == 0


def test_the_upstream_prompt_is_fetched_from_where_a_release_keeps_it() -> None:
    """The one summary an operator with customized prose gets must not 404.

    An installed Runner has no checkout, so the path into a published Release's
    source tree is spelled in code.  Wherever a checkout *is* available, that
    spelling is held to the shared prompt's real location — and to the prompt
    this Runner packages, since a summary is a diff between the two and a Release
    that shipped them apart would report drift that is not there.
    """
    from git_loopy import prompt as prompt_module
    from git_loopy import updatecmd

    repository_root = Path(__file__).parents[3]
    shared_prompt = repository_root / updatecmd.RELEASED_PROMPT_PATH

    assert shared_prompt.is_file()
    assert shared_prompt.read_bytes() == prompt_module.packaged_prompt_path().read_bytes()
    assert updatecmd.released_prompt_url("1.2.3").endswith(
        f"/v1.2.3/{updatecmd.RELEASED_PROMPT_PATH}"
    )


def test_update_reports_no_upstream_changes_for_prose_scaffolded_at_this_release(
    tmp_path: Path,
) -> None:
    """The common customized case must not depend on reaching the network.

    An operator who customized their override and is already on the installed
    Release has nothing upstream to port across, so fetching that Release's own
    prompt to diff it against itself could only ever report nothing — while
    turning every offline ``update`` into a failed one.
    """
    from git_loopy import scaffold_provenance, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    prompt = scope / "PROMPT.md"
    source = tmp_path / "packaged-PROMPT.md"
    scope.mkdir(parents=True)
    prompt.write_text("# Operator instructions\n", encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    scaffolded = tmp_path / "scaffolded-PROMPT.md"
    scaffolded.write_text("# Current Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.4",
        assets={"PROMPT.md": scaffolded},
        previous=None,
    )
    original = prompt.read_bytes()
    output: list[str] = []

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        previous_prompt_fetcher=lambda _version: (_ for _ in ()).throw(
            AssertionError("update must not fetch the Release it is already on")
        ),
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    assert prompt.read_bytes() == original
    assert output[0] == _absent_config(config_home)
    assert output[1] == "Left customized PROMPT.md from Release 1.2.4 unchanged."
    assert output[2] == (
        "No upstream PROMPT.md changes: it was scaffolded from the installed "
        "Release 1.2.4."
    )


def test_update_keeps_the_config_provenance_it_did_not_touch(tmp_path: Path) -> None:
    """Replacing one asset must not make the other one unrecorded.

    The record ``update`` writes describes only the asset it replaced, so the
    Config's entry survives solely by being carried forward.  Losing it would
    silently reclassify an untouched ``config.toml`` as the operator's work —
    the fail-safe direction for prose, and exactly the wrong one for the Config
    repair a retired taxonomy key needs (ADR-0054).
    """
    from git_loopy import scaffold_provenance, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    prompt = scope / "PROMPT.md"
    config = scope / "config.toml"
    source = tmp_path / "packaged-PROMPT.md"
    scope.mkdir(parents=True)
    prompt.write_text("# Previous Release\n", encoding="utf-8")
    config.write_text('model = "claude-opus-4.8"\n', encoding="utf-8")
    source.write_text("# Current Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"PROMPT.md": prompt, "config.toml": config},
        previous=None,
    )
    recorded_config = scaffold_provenance.read_scaffold_provenance(scope)
    assert recorded_config is not None

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=lambda _line: None,
    )

    assert result == 0
    provenance = scaffold_provenance.read_scaffold_provenance(scope)
    assert provenance is not None
    assert provenance.assets["config.toml"] == recorded_config.assets["config.toml"]
    assert provenance.assets["PROMPT.md"].release_version == "1.2.4"


def test_update_installs_the_prompt_a_run_would_otherwise_fall_back_to(
    tmp_path: Path,
) -> None:
    """Bringing an override forward means bringing it to *this* Release's prose.

    The packaged prompt is located here and again by the Run's own precedence
    chain, so the two are held together against the chain's answer: a second
    spelling that drifted would advance the provenance record over content no
    Run reads, and the override would be silently wrong rather than stale.
    """
    from git_loopy import prompt as prompt_module
    from git_loopy import scaffold_provenance, updatecmd

    config_home = tmp_path / "config-home"
    scope = config_home / "git-loopy"
    override = scope / "PROMPT.md"
    scope.mkdir(parents=True)
    override.write_text("# Previous Release\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"PROMPT.md": override},
        previous=None,
    )

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert override.read_text(encoding="utf-8") == prompt_module.load_prompt(
        tmp_path / "no-repo", {"XDG_CONFIG_HOME": str(tmp_path / "no-config")}
    )


def test_update_does_not_call_a_catalog_left_behind_the_pin_refreshed(
    tmp_path: Path,
) -> None:
    """An unreachable upstream keeps the old catalog; that is not a refresh.

    ``refresh_installed_catalog`` keeps an intact installed catalog rather than
    raising when it cannot reach the source, and says so in a warning.  For the
    one command whose whole purpose is the refresh, swallowing that warning
    would report a machine as current while its Skills are still the previous
    Release's.
    """
    from git_loopy import skill_install, updatecmd

    config_home = tmp_path / "config-home"
    kept = skill_install.RefreshOutcome(
        catalog=skill_install.InstalledCatalog(
            root=config_home / "git-loopy" / "skills",
            repository="bradcstevens/git-loopy-skills",
            revision="d" * 40,
            skills=("tdd",),
            sha256="0" * 64,
        ),
        action=skill_install.ACTION_KEPT,
        warning="could not refresh the Skill catalog from the source: offline",
    )
    output: list[str] = []
    helper_refreshed: list[str] = []

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: kept,
        helper_refresh=lambda version, _env: helper_refreshed.append(version)
        or tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 1
    assert helper_refreshed == ["1.2.4"]
    assert kept.warning in output
    assert skill_install.describe_refresh(kept) in output


def test_update_removes_a_retired_global_route_and_backs_up_the_config(
    tmp_path: Path,
) -> None:
    """A Release-retired route no longer locks every Config surface.

    The four surfaces #375 names are a Run, ``config list``, ``config get`` and
    ``config routing set``. All four are exercised here, because "repaired" is a
    claim about the file being *readable again*, not about one key being gone.
    """
    from git_loopy import cli, configcmd, settings, updatecmd
    from git_loopy.config import TaskTypeError

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    config = settings.global_config_path(env)
    settings.write_config(
        config, {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}}
    )
    output: list[str] = []

    def _resolve_a_run() -> None:
        """Exactly what ``cli.main`` does before a Run starts."""
        tables = settings.load_configs(tmp_path, env)
        cli.resolve_config(
            cli.build_parser().parse_args([]),
            env,
            project=tables.project,
            global_=tables.global_,
            measured=tables.measured,
            measured_provisional=tables.measured_provisional,
        )

    with pytest.raises(TaskTypeError):
        _resolve_a_run()

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    assert "custom" not in config.read_text(encoding="utf-8")
    backup = config.with_suffix(".toml.bak")
    assert "custom" in backup.read_text(encoding="utf-8")
    assert any("Removed retired routing key 'custom'" in line for line in output)
    assert any(str(backup) in line for line in output)
    _resolve_a_run()
    kwargs = dict(repo_root=tmp_path, env=env, out=lambda _line: None, err=lambda _line: None)
    assert configcmd.run_list(**kwargs) == 0
    assert configcmd.run_get("task-type:docs", **kwargs) == 0
    assert (
        configcmd.run_routing_set(
            "docs", "gpt-5.4", "high", scope="global", **kwargs
        )
        == 0
    )


def test_update_preserves_a_config_section_this_build_has_no_reader_for(
    tmp_path: Path,
) -> None:
    """The repair rewrites a file written by an older Release, sight unseen.

    That is the whole premise — so a section this build has no name for is
    exactly what it must expect to find, and preserve. Deciding a section's
    entry shape from a list of known names refused the rewrite instead, leaving
    the operator as locked out as before with a backup nothing could use.
    """
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    config.parent.mkdir(parents=True)
    config.write_text(
        '[retired_block]\nattempts = 3\nlabel = "legacy"\n\n'
        '[routing]\ncustom = { model = "gpt-5.4", effort = "high" }\n',
        encoding="utf-8",
    )
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    repaired = settings.load_config_table(config)
    assert repaired["retired_block"] == {"attempts": 3, "label": "legacy"}
    assert settings.table_routing(repaired, scope="global") == {}


def test_update_repairs_a_config_that_also_carries_an_escalation_rung(
    tmp_path: Path,
) -> None:
    """The repair rewrites the whole file, so it has to preserve the rest of it.

    ``[escalation]`` (#408) is a section of scalars, not of inline tables. A
    writer that forced every section into inline tables refused the rewrite, so
    the one command that exists to end the lockout failed on any Config that
    also names an **Escalation rung** — leaving a backup nothing had named.
    """
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    config.parent.mkdir(parents=True)
    config.write_text(
        'model = "gpt-5.4"\n\n'
        "[escalation]\nenabled = true\n"
        'model = "claude-opus-5"\neffort = "xhigh"\n\n'
        '[routing]\ncustom = { model = "gpt-5.4", effort = "high" }\n',
        encoding="utf-8",
    )
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    repaired = settings.load_config_table(config)
    assert settings.table_routing(repaired, scope="global") == {}
    assert settings.table_escalation(repaired, scope="global") == (
        True,
        ("claude-opus-5", "xhigh"),
    )
    assert repaired["model"] == "gpt-5.4"
    assert any("Removed retired routing key 'custom'" in line for line in output)


def test_update_preserves_unrelated_skill_policy_values(
    tmp_path: Path,
) -> None:
    """A routing repair does not normalize an unrelated saved Skill policy."""
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    config.parent.mkdir(parents=True)
    config.write_text(
        'enabled_skills = ["zeta", "zeta", "alpha"]\n\n'
        '[routing]\ncustom = { model = "gpt-5.4", effort = "high" }\n',
        encoding="utf-8",
    )

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert settings.load_config_table(config)["enabled_skills"] == [
        "zeta",
        "zeta",
        "alpha",
    ]


def test_update_dry_run_reports_a_retired_route_without_writing(
    tmp_path: Path,
) -> None:
    """Dry-run shows the exact Config repair without changing any asset."""
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    settings.write_config(
        config, {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}}
    )
    original = config.read_bytes()
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        dry_run=True,
        release_version_reader=lambda: (_ for _ in ()).throw(
            updatecmd.ReleaseVersionError("unavailable")
        ),
        catalog_refresh=lambda _env: (_ for _ in ()).throw(
            AssertionError("dry-run must not refresh the catalog")
        ),
        helper_refresh=lambda _version, _env: (_ for _ in ()).throw(
            AssertionError("dry-run must not refresh the helper")
        ),
        output_fn=output.append,
    )

    assert result == 0
    assert config.read_bytes() == original
    assert not config.with_suffix(".toml.bak").exists()
    assert any("Would remove retired routing key 'custom'" in line for line in output)
    # A preview that silently covers only one of `update`'s four assets has to
    # say so, or an operator reads a clean dry run as the whole command's verdict.
    assert output[-1] == (
        "Dry run: no file was written, and the prompt override, Skill catalog "
        "and TUI helper were not inspected."
    )


def test_update_leaves_a_renamed_route_ambiguous_when_its_target_exists(
    tmp_path: Path,
) -> None:
    """A migration never chooses between the old and new route values.

    The report has to stay actionable, because the key it declines keeps every
    Config surface refused and ``task_type_refusal`` now sends the operator
    *here*. A message that only says "could not" would leave two commands
    pointing at each other — the lockout #375 recorded, one indirection longer.
    """
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    config.parent.mkdir(parents=True)
    config.write_text(
        '[routing]\ncustom = { model = "claude-opus-5", effort = "high" }\n'
        '"task-type:docs" = { model = "gpt-5.4", effort = "high" }\n'
        'docs = { model = "gpt-5-mini", effort = "medium" }\n',
        encoding="utf-8",
    )
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 1
    repaired = settings.load_config_table(config)
    assert settings.table_routing(repaired, scope="global") == {
        "task-type:docs": ("gpt-5.4", "high"),
        "docs": ("gpt-5-mini", "medium"),
    }
    assert "custom" in config.with_suffix(".toml.bak").read_text(encoding="utf-8")
    assert any("task-type:docs" in line and "already configured" in line for line in output)
    assert any("Removed retired routing key 'custom'" in line for line in output)
    assert any(
        "git-loopy config routing unset 'task-type:docs' --global" in line
        for line in output
    )

    # The remedy the message names has to be the one that works, or `update`
    # has only moved the lockout one command further away.
    from git_loopy import configcmd

    surfaces = dict(repo_root=tmp_path, env=env, out=lambda _l: None, err=lambda _l: None)
    assert configcmd.run_list(**surfaces) == 1  # still refused by 'task-type:docs'
    assert (
        configcmd.run_routing_unset("task-type:docs", scope="global", **surfaces) == 0
    )
    assert configcmd.run_list(**surfaces) == 0
    assert settings.table_routing(
        settings.load_config_table(config), scope="global"
    ) == {"docs": ("gpt-5-mini", "medium")}


def test_update_renames_an_unambiguous_route_in_the_project_scope(
    tmp_path: Path,
) -> None:
    """A known old spelling moves to its current key without losing its route."""
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.project_config_path(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text(
        '[routing]\n"task-type:docs" = { model = "gpt-5.4", effort = "high" }\n',
        encoding="utf-8",
    )
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        project_root=tmp_path,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    repaired = settings.load_config_table(config)
    assert settings.table_routing(repaired, scope="project") == {
        "docs": ("gpt-5.4", "high")
    }
    assert any("Renamed retired routing key 'task-type:docs' to 'docs'" in line for line in output)


def test_a_failed_config_write_names_its_backup_and_claims_no_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The backup is the operator's way back, so it is named the moment it exists.

    Reporting it only after the replacement it protects meant a failed write
    left a ``.bak`` nothing had accounted for — beside a Config still carrying
    the retired key, under a report that said the key had been removed.
    """
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    settings.write_config(
        config, {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}}
    )
    original = config.read_bytes()
    monkeypatch.setattr(
        settings,
        "write_config_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read-only")),
    )
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    backup = config.with_suffix(".toml.bak")
    assert result == 1
    assert config.read_bytes() == original
    assert backup.read_bytes() == original
    assert any(str(backup) in line for line in output)
    assert not any("Removed retired routing key" in line for line in output)
    assert any("Could not repair the global Config" in line for line in output)


def test_update_leaves_a_clean_config_without_a_backup(tmp_path: Path) -> None:
    """A current Config is *reported*, never rewritten for the sake of it.

    ``update`` names every asset it changed and every asset it left alone, so
    the Config has to say so too, and name the file it judged — an operator with
    both scopes otherwise cannot tell a clean Config from an unexamined one.
    """
    from git_loopy import settings, updatecmd

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    config = settings.global_config_path(env)
    settings.write_config(
        config, {"routing": {"docs": {"model": "gpt-5.4", "effort": "high"}}}
    )
    original = config.read_bytes()
    output: list[str] = []

    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 0
    assert config.read_bytes() == original
    assert not config.with_suffix(".toml.bak").exists()
    assert any(
        "No retired routing keys in the global Config" in line and str(config) in line
        for line in output
    )


def _make_fake_tar_helper(
    dest_dir: Path,
    artifact: object,
    version: str,
    *,
    schema_range: tuple[int, int] = (1, 1),
    tamper_digest: bool = False,
) -> tuple[bytes, bytes]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    executable_name = getattr(artifact, "executable_name")
    archive_name = getattr(artifact, "archive_name")
    executable = dest_dir / executable_name
    min_schema, max_schema = schema_range
    executable.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf 'git-loopy-tui {version}\\n'; exit 0 ;;\n"
        "  --schema-version)\n"
        f'    printf \'{{"name": "git-loopy-tui", "version": "{version}", '
        f'"min_event_schema_version": {min_schema}, "max_event_schema_version": {max_schema}, '
        f'"wrapper_contract_version": "1.0"}}\\n\' ;\n'
        "    exit 0 ;;\n"
        "esac\n"
        "cat >/dev/null\n"
        "printf '{\"queue\": []}\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    archive = dest_dir / archive_name
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(executable, arcname=executable_name)
    archive_bytes = archive.read_bytes()
    digest = hashlib.sha256(archive_bytes).hexdigest()
    if tamper_digest:
        digest = "0" * 64
    checksum_bytes = f"{digest}  {archive_name}\n".encode("utf-8")
    return archive_bytes, checksum_bytes


@pytest.fixture(
    params=[("Darwin", "arm64", None), ("Linux", "x86_64", "gnu")],
    ids=["macos", "linux"],
)
def helper_host(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str | None]:
    from git_loopy import tui_release

    system, machine, libc = request.param
    monkeypatch.setattr(tui_release.platform, "system", lambda: system)
    monkeypatch.setattr(tui_release.platform, "machine", lambda: machine)
    monkeypatch.setattr(
        tui_release.platform, "libc_ver", lambda: ("glibc" if libc == "gnu" else "", "")
    )
    monkeypatch.setattr(
        tui_release,
        "refresh_machine_local_helper",
        partial(
            tui_release.refresh_machine_local_helper,
            host_system=lambda: system,
            host_machine=lambda: machine,
            host_libc=lambda: libc,
        ),
    )
    return system, machine, libc


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_exact_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_host: tuple[str, str, str | None]
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    artifact = tui_release._runtime_artifact_for_host(*helper_host)
    archive_bytes, checksum_bytes = _make_fake_tar_helper(
        tmp_path / "pkg-124", artifact, "1.2.4"
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
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.archive_name
        ): archive_bytes,
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.checksum_name
        ): checksum_bytes,
    }
    monkeypatch.setattr(tui_release, "_download_release_file", downloads.__getitem__)

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 0
    installed_helper = config_home / "git-loopy" / "bin" / artifact.executable_name
    assert installed_helper.is_file()
    record = config_home / "git-loopy" / "bin" / f"{artifact.executable_name}.release"
    assert record.read_text(encoding="utf-8").strip() == "1.2.4"

    # Runtime discovery attaches to the exact helper
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.4",
        warn=lambda message: pytest.fail(message),
        env=env,
    )
    assert attached == installed_helper


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_verified_older_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_host: tuple[str, str, str | None]
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    artifact = tui_release._runtime_artifact_for_host(*helper_host)
    archive_bytes, checksum_bytes = _make_fake_tar_helper(
        tmp_path / "pkg-124", artifact, "1.2.4"
    )

    release_index = [
        {
            "tag_name": "v1.2.5-alpha.1",
            "draft": False,
            "assets": [],  # source-only exact release
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
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.archive_name
        ): archive_bytes,
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.checksum_name
        ): checksum_bytes,
    }
    monkeypatch.setattr(tui_release, "_download_release_file", downloads.__getitem__)

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.5-alpha.1",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 0
    installed_helper = config_home / "git-loopy" / "bin" / artifact.executable_name
    assert installed_helper.is_file()
    record = config_home / "git-loopy" / "bin" / f"{artifact.executable_name}.release"
    assert record.read_text(encoding="utf-8").strip() == "1.2.4"

    # Runtime discovery attaches to the fallback helper without rejecting it
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.5-alpha.1",
        warn=lambda message: pytest.fail(message),
        env=env,
    )
    assert attached == installed_helper


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_all_source_only_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    release_index = [
        {"tag_name": "v1.2.4", "draft": False, "assets": []},
        {"tag_name": "v1.2.3", "draft": False, "assets": []},
    ]
    monkeypatch.setattr(
        tui_release,
        "_download_release_file",
        lambda _url: json.dumps(release_index).encode("utf-8"),
    )
    monkeypatch.setattr(tui_release.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tui_release.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(tui_release.platform, "libc_ver", lambda: ("", ""))

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 1
    assert any("no published git-loopy-tui Release carrying" in line for line in output)
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.4",
        warn=lambda _m: None,
        env=env,
    )
    assert attached is None


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_newer_only_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    artifact = tui_release._runtime_artifact_for_host("Darwin", "arm64", None)
    release_index = [
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        }
    ]
    monkeypatch.setattr(
        tui_release,
        "_download_release_file",
        lambda _url: json.dumps(release_index).encode("utf-8"),
    )
    monkeypatch.setattr(tui_release.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tui_release.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(tui_release.platform, "libc_ver", lambda: ("", ""))

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.3",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 1
    assert any("no published git-loopy-tui Release carrying" in line for line in output)
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=lambda _m: None,
        env=env,
    )
    assert attached is None


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_incompatible_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_host: tuple[str, str, str | None]
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    artifact = tui_release._runtime_artifact_for_host(*helper_host)
    archive_bytes, checksum_bytes = _make_fake_tar_helper(
        tmp_path / "pkg-124", artifact, "1.2.4", schema_range=(99, 99)
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
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.archive_name
        ): archive_bytes,
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.checksum_name
        ): checksum_bytes,
    }
    monkeypatch.setattr(tui_release, "_download_release_file", downloads.__getitem__)

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 1
    assert any("decodes Event schemas" in line for line in output)
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.4",
        warn=lambda _m: None,
        env=env,
    )
    assert attached is None


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_damaged_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_host: tuple[str, str, str | None]
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    artifact = tui_release._runtime_artifact_for_host(*helper_host)
    archive_bytes, checksum_bytes = _make_fake_tar_helper(
        tmp_path / "pkg-124", artifact, "1.2.4", tamper_digest=True
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
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.archive_name
        ): archive_bytes,
        tui_release._runtime_release_artifact_url(
            "1.2.4", artifact.checksum_name
        ): checksum_bytes,
    }
    monkeypatch.setattr(tui_release, "_download_release_file", downloads.__getitem__)

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 1
    assert any("failed its SHA-256 checksum" in line for line in output)
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.4",
        warn=lambda _m: None,
        env=env,
    )
    assert attached is None


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_update_public_maintenance_failure_preserves_previous_helper_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import tui_release, updatecmd

    config_home = tmp_path / "config-home"
    env = {"XDG_CONFIG_HOME": str(config_home)}
    artifact = tui_release._runtime_artifact_for_host("Darwin", "arm64", None)

    # Pre-existing installation: helper 1.2.0 and its record
    helper_path = config_home / "git-loopy" / "bin" / artifact.executable_name
    _make_fake_tar_helper(tmp_path / "seed", artifact, "1.2.0")
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    # copy executable from seed
    helper_path.write_bytes((tmp_path / "seed" / artifact.executable_name).read_bytes())
    helper_path.chmod(0o755)
    record = helper_path.parent / f"{artifact.executable_name}.release"
    record.write_text("1.2.0\n", encoding="utf-8")

    # Verify pre-existing attaches
    assert (
        tui_release.resolve_runtime_helper(
            tmp_path / "repo",
            release_version="1.2.3",
            warn=lambda message: pytest.fail(message),
            env=env,
        )
        == helper_path
    )

    # Now attempt an update to 1.2.4 where archive download fails
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
    def _download(url: str) -> bytes:
        if "releases?per_page" in url:
            return json.dumps(release_index).encode("utf-8")
        raise OSError("failed to connect to github.com")

    monkeypatch.setattr(tui_release, "_download_release_file", _download)
    monkeypatch.setattr(tui_release.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tui_release.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(tui_release.platform, "libc_ver", lambda: ("", ""))

    output: list[str] = []
    result = updatecmd.run_update(
        env=env,
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=tmp_path / "absent-PROMPT.md",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        output_fn=output.append,
    )
    assert result == 1
    assert any("Could not refresh the TUI helper" in line for line in output)

    # Previous helper and record remain intact
    assert helper_path.is_file()
    assert record.read_text(encoding="utf-8").strip() == "1.2.0"
    attached = tui_release.resolve_runtime_helper(
        tmp_path / "repo",
        release_version="1.2.3",
        warn=lambda message: pytest.fail(message),
        env=env,
    )
    assert attached == helper_path
