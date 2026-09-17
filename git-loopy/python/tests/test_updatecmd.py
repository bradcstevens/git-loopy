"""Tests for the machine-local ``git-loopy update`` command."""

from __future__ import annotations

from pathlib import Path

import pytest

def test_update_replaces_an_untouched_prompt_override(tmp_path: Path) -> None:
    """An override still matching Scaffold provenance moves to this Release."""
    from git_loopy import scaffold_provenance, updatecmd

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

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        catalog_refresh=lambda _env: "catalog refreshed",
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
        "catalog refreshed",
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
        catalog_refresh=lambda _env: "catalog refreshed",
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
        catalog_refresh=lambda _env: "catalog refreshed",
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
        catalog_refresh=lambda _env: refreshed.append("catalog") or "catalog refreshed",
        helper_refresh=lambda _version, _env: refreshed.append("helper")
        or tmp_path / "git-loopy-tui",
        output_fn=output.append,
    )

    assert result == 1
    assert refreshed == ["catalog", "helper"]
    assert output[1] == "Could not summarize upstream PROMPT.md changes: offline"


def test_update_invalidates_stale_provenance_before_replacing_a_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed record write leaves the safe unrecorded state, never a stale digest."""
    from git_loopy import scaffold_provenance, updatecmd

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

    def fail_record(_scope: Path, **_kwargs: object) -> Path:
        raise scaffold_provenance.ScaffoldProvenanceError("record write failed")

    monkeypatch.setattr(updatecmd, "record_scaffolded_assets", fail_record)

    result = updatecmd.run_update(
        env={"XDG_CONFIG_HOME": str(config_home)},
        release_version_reader=lambda: "1.2.4",
        packaged_prompt=source,
        catalog_refresh=lambda _env: "catalog refreshed",
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
    )

    assert result == 1
    assert prompt.read_text(encoding="utf-8") == "# Current Release\n"
    assert scaffold_provenance.read_scaffold_provenance(scope) is None
