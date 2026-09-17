"""Tests for the machine-local ``git-loopy update`` command."""

from __future__ import annotations

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
        "Updated PROMPT.md to Release 1.2.4.",
        skill_install.describe_refresh(catalog),
        f"Updated TUI helper: {tmp_path / 'git-loopy-tui'}",
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
    assert output[0] == "Left customized PROMPT.md from Release 1.2.3 unchanged."
    assert output[1] == (
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
    assert output[0] == "Left unrecorded PROMPT.md unchanged; treating it as customized."


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
    assert output[1] == "Could not summarize upstream PROMPT.md changes: offline"


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
    assert output[0] == "Left customized PROMPT.md from Release 1.2.3 unchanged."


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
    assert catalog.short_revision in output[1] and catalog.repository in output[1]
    assert output[2] == (
        f"Updated TUI helper: {config_home / 'git-loopy' / 'bin' / 'git-loopy-tui'}"
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
    assert output[0] == "Left customized PROMPT.md from Release 1.2.4 unchanged."
    assert output[1] == (
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
