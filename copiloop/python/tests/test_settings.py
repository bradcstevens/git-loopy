"""Tests for :mod:`copiloop.settings` — the persistent-Config loader (issue #51).

The loader is the thin I/O half of the persistent-Config seam (ADR-0006): it
locates and parses the two hand-editable ``config.toml`` files (global
``~/.config/copiloop/config.toml`` honouring ``$XDG_CONFIG_HOME`` and project
``./copiloop/config.toml``) and returns their tables, handling the found /
missing / empty / malformed cases exactly like :func:`copiloop.pricing.load_pricing`
isolates *its* I/O. The pure resolver that consumes these tables lives in
:mod:`copiloop.cli` and is tested in ``tests/test_config_resolver.py``.

All path resolution takes an **injected** environment mapping so no test ever
reads the developer's real ``$HOME`` / ``$XDG_CONFIG_HOME``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from copiloop import settings


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_global_config_path_honours_xdg_config_home(tmp_path: Path) -> None:
    """``$XDG_CONFIG_HOME`` wins for the global config location."""
    xdg = tmp_path / "xdg"
    path = settings.global_config_path({"XDG_CONFIG_HOME": str(xdg)})
    assert path == xdg / "copiloop" / "config.toml"


def test_global_config_path_falls_back_to_home_dot_config(tmp_path: Path) -> None:
    """With ``$XDG_CONFIG_HOME`` unset, the global config lives under ``$HOME/.config``."""
    home = tmp_path / "home"
    path = settings.global_config_path({"HOME": str(home)})
    assert path == home / ".config" / "copiloop" / "config.toml"


def test_global_config_path_blank_xdg_falls_back_to_home(tmp_path: Path) -> None:
    """A blank ``$XDG_CONFIG_HOME`` is treated as unset (spec behaviour)."""
    home = tmp_path / "home"
    path = settings.global_config_path({"XDG_CONFIG_HOME": "   ", "HOME": str(home)})
    assert path == home / ".config" / "copiloop" / "config.toml"


def test_project_config_path_is_repo_copiloop_config_toml(tmp_path: Path) -> None:
    """The project config is ``<repo-root>/copiloop/config.toml``."""
    path = settings.project_config_path(tmp_path)
    assert path == tmp_path / "copiloop" / "config.toml"


def test_global_prompt_path_honours_xdg_config_home(tmp_path: Path) -> None:
    """``$XDG_CONFIG_HOME`` wins for the global prompt-override location (ADR-0006)."""
    xdg = tmp_path / "xdg"
    path = settings.global_prompt_path({"XDG_CONFIG_HOME": str(xdg)})
    assert path == xdg / "copiloop" / "PROMPT.md"


def test_global_prompt_path_falls_back_to_home_dot_config(tmp_path: Path) -> None:
    """With ``$XDG_CONFIG_HOME`` unset, the global prompt lives under ``$HOME/.config``."""
    home = tmp_path / "home"
    path = settings.global_prompt_path({"HOME": str(home)})
    assert path == home / ".config" / "copiloop" / "PROMPT.md"


def test_global_prompt_path_blank_xdg_falls_back_to_home(tmp_path: Path) -> None:
    """A blank ``$XDG_CONFIG_HOME`` is treated as unset (same rule as the config path)."""
    home = tmp_path / "home"
    path = settings.global_prompt_path({"XDG_CONFIG_HOME": "   ", "HOME": str(home)})
    assert path == home / ".config" / "copiloop" / "PROMPT.md"


def test_global_prompt_and_config_share_the_same_scope_dir(tmp_path: Path) -> None:
    """The global prompt and config resolve into the same ``<config-home>/copiloop/`` dir."""
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
    (repo / "copiloop").mkdir(parents=True)
    (repo / "copiloop" / "config.toml").write_text(
        'issue_source = "prds"\n', encoding="utf-8"
    )
    xdg = tmp_path / "xdg"
    (xdg / "copiloop").mkdir(parents=True)
    (xdg / "copiloop" / "config.toml").write_text(
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


def test_write_config_creates_scope_dir_and_round_trips(tmp_path: Path) -> None:
    import tomllib

    target = tmp_path / "copiloop" / "config.toml"
    settings.write_config(target, {"model": "gpt-5.4", "max_nmt_strikes": 4})
    assert target.is_file()
    assert tomllib.loads(target.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "max_nmt_strikes": 4,
    }


def test_settings_module_imports_only_stdlib() -> None:
    """The loader stays stdlib-only (like :mod:`copiloop.pricing`)."""
    import copiloop.settings as mod

    # A crude but effective guard: the module's globals must not reference any
    # third-party package the base install doesn't ship.
    import inspect

    source = inspect.getsource(mod)
    for forbidden in ("import rich", "import textual", "import copilot", "opentelemetry"):
        assert forbidden not in source
