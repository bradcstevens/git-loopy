"""Tests for :mod:`git_loopy.settings` — the persistent-Config loader (issue #51).

The loader is the thin I/O half of the persistent-Config seam (ADR-0006): it
locates and parses the two hand-editable ``config.toml`` files (global
``~/.config/git-loopy/config.toml`` honouring ``$XDG_CONFIG_HOME`` and project
``./git-loopy/config.toml``) and returns their tables, handling the found /
missing / empty / malformed cases exactly like :func:`git_loopy.pricing.load_pricing`
isolates *its* I/O. The pure resolver that consumes these tables lives in
:mod:`git_loopy.cli` and is tested in ``tests/test_config_resolver.py``.

All path resolution takes an **injected** environment mapping so no test ever
reads the developer's real ``$HOME`` / ``$XDG_CONFIG_HOME``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import settings


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_global_config_path_honours_xdg_config_home(tmp_path: Path) -> None:
    """``$XDG_CONFIG_HOME`` wins for the global config location."""
    xdg = tmp_path / "xdg"
    path = settings.global_config_path({"XDG_CONFIG_HOME": str(xdg)})
    assert path == xdg / "git-loopy" / "config.toml"


def test_global_config_path_falls_back_to_home_dot_config(tmp_path: Path) -> None:
    """With ``$XDG_CONFIG_HOME`` unset, the global config lives under ``$HOME/.config``."""
    home = tmp_path / "home"
    path = settings.global_config_path({"HOME": str(home)})
    assert path == home / ".config" / "git-loopy" / "config.toml"


def test_global_config_path_blank_xdg_falls_back_to_home(tmp_path: Path) -> None:
    """A blank ``$XDG_CONFIG_HOME`` is treated as unset (spec behaviour)."""
    home = tmp_path / "home"
    path = settings.global_config_path({"XDG_CONFIG_HOME": "   ", "HOME": str(home)})
    assert path == home / ".config" / "git-loopy" / "config.toml"


def test_project_config_path_is_repo_git_loopy_config_toml(tmp_path: Path) -> None:
    """The project config is ``<repo-root>/git-loopy/config.toml``."""
    path = settings.project_config_path(tmp_path)
    assert path == tmp_path / "git-loopy" / "config.toml"


def test_global_prompt_path_honours_xdg_config_home(tmp_path: Path) -> None:
    """``$XDG_CONFIG_HOME`` wins for the global prompt-override location (ADR-0006)."""
    xdg = tmp_path / "xdg"
    path = settings.global_prompt_path({"XDG_CONFIG_HOME": str(xdg)})
    assert path == xdg / "git-loopy" / "PROMPT.md"


def test_global_prompt_path_falls_back_to_home_dot_config(tmp_path: Path) -> None:
    """With ``$XDG_CONFIG_HOME`` unset, the global prompt lives under ``$HOME/.config``."""
    home = tmp_path / "home"
    path = settings.global_prompt_path({"HOME": str(home)})
    assert path == home / ".config" / "git-loopy" / "PROMPT.md"


def test_global_prompt_path_blank_xdg_falls_back_to_home(tmp_path: Path) -> None:
    """A blank ``$XDG_CONFIG_HOME`` is treated as unset (same rule as the config path)."""
    home = tmp_path / "home"
    path = settings.global_prompt_path({"XDG_CONFIG_HOME": "   ", "HOME": str(home)})
    assert path == home / ".config" / "git-loopy" / "PROMPT.md"


def test_global_prompt_and_config_share_the_same_scope_dir(tmp_path: Path) -> None:
    """The global prompt and config resolve into the same ``<config-home>/git-loopy/`` dir."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    assert (
        settings.global_prompt_path(env).parent
        == settings.global_config_path(env).parent
    )


# ---------------------------------------------------------------------------
# load_config_table — found / missing / empty / malformed
# ---------------------------------------------------------------------------


def test_load_config_table_parses_a_found_file(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('model = "claude-opus-4.8"\nmax_nmt_strikes = 5\n', encoding="utf-8")
    table = settings.load_config_table(p)
    assert table == {"model": "claude-opus-4.8", "max_nmt_strikes": 5}


def test_load_config_table_missing_file_returns_empty(tmp_path: Path) -> None:
    """A missing config file is a normal 'no config here' — not an error."""
    assert settings.load_config_table(tmp_path / "nope.toml") == {}


def test_load_config_table_none_path_returns_empty() -> None:
    assert settings.load_config_table(None) == {}


def test_load_config_table_empty_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("", encoding="utf-8")
    assert settings.load_config_table(p) == {}


def test_load_config_table_malformed_raises_settings_error(tmp_path: Path) -> None:
    """Malformed TOML is surfaced clearly with the offending path."""
    p = tmp_path / "config.toml"
    p.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.load_config_table(p)
    assert str(p) in str(excinfo.value)


# ---------------------------------------------------------------------------
# load_configs — both scopes together
# ---------------------------------------------------------------------------


def test_load_configs_returns_project_and_global_tables(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "git-loopy").mkdir(parents=True)
    (repo / "git-loopy" / "config.toml").write_text(
        'issue_source = "prds"\n', encoding="utf-8"
    )
    xdg = tmp_path / "xdg"
    (xdg / "git-loopy").mkdir(parents=True)
    (xdg / "git-loopy" / "config.toml").write_text(
        'model = "gpt-5.5"\n', encoding="utf-8"
    )

    tables = settings.load_configs(repo, {"XDG_CONFIG_HOME": str(xdg)})

    assert tables.project == {"issue_source": "prds"}
    assert tables.global_ == {"model": "gpt-5.5"}


def test_load_configs_both_missing_returns_empty_tables(tmp_path: Path) -> None:
    tables = settings.load_configs(tmp_path, {"XDG_CONFIG_HOME": str(tmp_path / "x")})
    assert tables.project == {}
    assert tables.global_ == {}


# ---------------------------------------------------------------------------
# Typed table readers
# ---------------------------------------------------------------------------


def test_table_str_reads_present_and_absent() -> None:
    assert settings.table_str({"model": "x"}, "model", scope="global") == "x"
    assert settings.table_str({}, "model", scope="global") is None


def test_table_str_wrong_type_raises_with_scope() -> None:
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.table_str({"model": 123}, "model", scope="project")
    msg = str(excinfo.value)
    assert "project" in msg and "model" in msg


def test_table_bool_reads_present_and_absent() -> None:
    assert settings.table_bool({"interactive": True}, "interactive", scope="g") is True
    assert settings.table_bool({"interactive": False}, "interactive", scope="g") is False
    assert settings.table_bool({}, "interactive", scope="g") is None


def test_table_bool_rejects_non_bool() -> None:
    with pytest.raises(settings.SettingsError):
        settings.table_bool({"interactive": "yes"}, "interactive", scope="g")


def test_table_int_reads_present_and_absent() -> None:
    assert settings.table_int({"max_nmt_strikes": 4}, "max_nmt_strikes", scope="g") == 4
    assert settings.table_int({}, "max_nmt_strikes", scope="g") is None


def test_table_int_rejects_bool_and_non_int() -> None:
    # TOML booleans are ints in Python; the reader must not accept True as 1.
    with pytest.raises(settings.SettingsError):
        settings.table_int({"n": True}, "n", scope="g")
    with pytest.raises(settings.SettingsError):
        settings.table_int({"n": "4"}, "n", scope="g")


def test_table_float_accepts_int_and_float() -> None:
    assert settings.table_float({"t": 10}, "t", scope="g") == 10.0
    assert settings.table_float({"t": 2.5}, "t", scope="g") == 2.5
    assert settings.table_float({}, "t", scope="g") is None


def test_table_float_rejects_bool_and_str() -> None:
    with pytest.raises(settings.SettingsError):
        settings.table_float({"t": True}, "t", scope="g")
    with pytest.raises(settings.SettingsError):
        settings.table_float({"t": "2.5"}, "t", scope="g")


def test_table_str_list_reads_present_and_absent() -> None:
    assert settings.table_str_list({"deny_tools": ["a", "b"]}, "deny_tools", scope="g") == [
        "a",
        "b",
    ]
    assert settings.table_str_list({}, "deny_tools", scope="g") == []


def test_table_str_list_rejects_non_list_and_non_str_items() -> None:
    with pytest.raises(settings.SettingsError):
        settings.table_str_list({"deny_tools": "a"}, "deny_tools", scope="g")
    with pytest.raises(settings.SettingsError):
        settings.table_str_list({"deny_tools": ["a", 2]}, "deny_tools", scope="g")


# ---------------------------------------------------------------------------
# [routing] typed reader (issue #146): type -> (model, effort). Absent -> {};
# malformed entries raise SettingsError naming the scope + offending key.
# ---------------------------------------------------------------------------


def test_table_routing_absent_returns_empty() -> None:
    assert settings.table_routing({}, scope="project") == {}


def test_table_routing_reads_well_formed_entries() -> None:
    table = {
        "routing": {
            "planning": {"model": "claude-opus-4.8", "effort": "max"},
            "docs": {"model": "gpt-5-mini", "effort": "medium"},
        }
    }
    assert settings.table_routing(table, scope="global") == {
        "planning": ("claude-opus-4.8", "max"),
        "docs": ("gpt-5-mini", "medium"),
    }


def test_table_routing_rejects_non_table_routing_value() -> None:
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.table_routing({"routing": "nope"}, scope="project")
    assert "project" in str(excinfo.value)
    assert "routing" in str(excinfo.value)


def test_table_routing_rejects_non_table_entry() -> None:
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.table_routing(
            {"routing": {"planning": "claude-opus-4.8"}}, scope="global"
        )
    msg = str(excinfo.value)
    assert "global" in msg and "planning" in msg


@pytest.mark.parametrize(
    "entry",
    [
        {"model": "claude-opus-4.8"},  # missing effort
        {"effort": "max"},  # missing model
        {"model": "claude-opus-4.8", "effort": "max", "extra": "x"},  # extra key
    ],
)
def test_table_routing_rejects_missing_or_extra_keys(entry: dict) -> None:
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.table_routing({"routing": {"planning": entry}}, scope="project")
    msg = str(excinfo.value)
    assert "project" in msg and "planning" in msg


@pytest.mark.parametrize(
    "entry",
    [
        {"model": 123, "effort": "max"},
        {"model": "claude-opus-4.8", "effort": 5},
    ],
)
def test_table_routing_rejects_non_string_values(entry: dict) -> None:
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.table_routing({"routing": {"docs": entry}}, scope="global")
    msg = str(excinfo.value)
    assert "global" in msg and "docs" in msg


# ---------------------------------------------------------------------------
# TOML writer (the config I/O module owns write next to read)
# ---------------------------------------------------------------------------


def test_dump_config_toml_round_trips_every_scalar_and_list_type() -> None:
    import tomllib

    values = {
        "model": "gpt-5.4",
        "reasoning_effort": "high",
        "include_prs": True,
        "otel_enabled": False,
        "max_nmt_strikes": 5,
        "send_timeout_seconds": 3600.5,
        "deny_tools": ["bash", "write"],
        "deny_skills": [],
    }
    assert tomllib.loads(settings.dump_config_toml(values)) == values


def test_dump_config_toml_escapes_backslashes_and_quotes() -> None:
    import tomllib

    text = settings.dump_config_toml({"model": 'a"b\\c'})
    assert tomllib.loads(text) == {"model": 'a"b\\c'}


def test_dump_config_toml_header_is_comment_only(tmp_path: Path) -> None:
    import tomllib

    text = settings.dump_config_toml(
        {"model": "gpt-5.4"}, header=("a header line", "and another")
    )
    assert "# a header line" in text
    assert "# and another" in text
    # Comments are ignored by tomllib — only the data round-trips.
    assert tomllib.loads(text) == {"model": "gpt-5.4"}


def test_dump_config_toml_rejects_unsupported_value_type() -> None:
    with pytest.raises(settings.SettingsError):
        settings.dump_config_toml({"nested": {"a": 1}})
    with pytest.raises(settings.SettingsError):
        settings.dump_config_toml({"listed": [1, 2]})


# ---------------------------------------------------------------------------
# TOML writer — the one bounded table-valued extension (issue #146): a
# top-level dict-valued key (e.g. `[routing]`) emits as a `[section]` block of
# inline `{ ... }` tables. Scalars/lists first, then sections, so a bare
# `key = value` line is never captured into a section.
# ---------------------------------------------------------------------------


def test_dump_config_toml_emits_routing_section_of_inline_tables() -> None:
    import tomllib

    values = {
        "model": "claude-opus-4.8",
        "reasoning_effort": "max",
        "routing": {
            "planning": {"model": "claude-opus-4.8", "effort": "max"},
            "implementation": {"model": "claude-sonnet-5", "effort": "high"},
            "docs": {"model": "gpt-5-mini", "effort": "medium"},
        },
    }
    text = settings.dump_config_toml(values)
    assert "[routing]" in text
    # One inline table per line — one value-literal per member, no array-of-tables.
    assert 'planning = { model = "claude-opus-4.8", effort = "max" }' in text
    assert tomllib.loads(text) == values


def test_dump_config_toml_places_table_sections_after_scalar_keys() -> None:
    # A top-level scalar must be emitted before any `[section]` header, else TOML
    # parses it as a member of that section.
    text = settings.dump_config_toml(
        {
            "routing": {"docs": {"model": "gpt-5-mini", "effort": "medium"}},
            "model": "gpt-5-mini",
        }
    )
    lines = text.splitlines()
    section_idx = lines.index("[routing]")
    model_idx = next(i for i, line in enumerate(lines) if line.startswith("model ="))
    assert model_idx < section_idx


def test_dump_config_toml_empty_routing_section_round_trips() -> None:
    import tomllib

    text = settings.dump_config_toml({"routing": {}})
    assert tomllib.loads(text) == {"routing": {}}


def test_routing_round_trips_writer_reader_writer() -> None:
    # The AC's end-to-end loop: a `[routing]` map survives writer -> reader ->
    # writer byte-for-byte. `table_routing` reads the emitted section into the
    # semantic `{key: (model, effort)}` map, and re-emitting that map reproduces
    # the original text exactly.
    import tomllib

    routing = {
        "planning": {"model": "claude-opus-4.8", "effort": "max"},
        "docs": {"model": "gpt-5-mini", "effort": "medium"},
    }
    first = settings.dump_config_toml({"routing": routing})
    parsed = settings.table_routing(tomllib.loads(first), scope="project")
    assert parsed == {
        "planning": ("claude-opus-4.8", "max"),
        "docs": ("gpt-5-mini", "medium"),
    }
    rebuilt = {
        key: {"model": model, "effort": effort}
        for key, (model, effort) in parsed.items()
    }
    assert settings.dump_config_toml({"routing": rebuilt}) == first


def test_dump_config_toml_rejects_non_inline_table_routing_entry() -> None:
    # A section entry MUST itself be an inline table; a scalar value is rejected
    # (this is why the pre-existing `{"nested": {"a": 1}}` case still raises).
    with pytest.raises(settings.SettingsError):
        settings.dump_config_toml({"routing": {"planning": "claude-opus-4.8"}})


def test_dump_config_toml_rejects_nesting_inside_inline_table() -> None:
    # No multi-level nesting: an inline-table member must be a scalar.
    with pytest.raises(settings.SettingsError):
        settings.dump_config_toml({"routing": {"planning": {"model": {"x": 1}}}})
    with pytest.raises(settings.SettingsError):
        settings.dump_config_toml({"routing": {"planning": {"model": ["a"]}}})


def test_write_config_creates_scope_dir_and_round_trips(tmp_path: Path) -> None:
    import tomllib

    target = tmp_path / "git-loopy" / "config.toml"
    settings.write_config(target, {"model": "gpt-5.4", "max_nmt_strikes": 4})
    assert target.is_file()
    assert tomllib.loads(target.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "max_nmt_strikes": 4,
    }


def test_settings_module_imports_only_stdlib() -> None:
    """The loader stays stdlib-only (like :mod:`git_loopy.pricing`)."""
    import git_loopy.settings as mod

    # A crude but effective guard: the module's globals must not reference any
    # third-party package the base install doesn't ship.
    import inspect

    source = inspect.getsource(mod)
    for forbidden in ("import rich", "import textual", "import copilot", "opentelemetry"):
        assert forbidden not in source
