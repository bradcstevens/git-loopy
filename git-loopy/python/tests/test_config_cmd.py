"""Tests for :mod:`git_loopy.configcmd` — the ``git-loopy config`` subcommand group.

The five operations — ``edit`` / ``set`` / ``get`` / ``list`` / ``path`` — are a
convenience over hand-editing ``config.toml`` (issue #56, ADR-0006). ``set`` /
``edit`` / ``path`` act on a chosen **scope** (matching the ``init`` wizard's
``--global`` / ``--project`` model); ``get`` / ``list`` show the **effective
merged** value(s) resolved across every source (env > project > global >
default) via :func:`git_loopy.cli.resolve_config`.

Everything is injected — a captured ``out`` / ``err`` sink, tmp scope dirs (via
an injected ``repo_root`` + ``env``), and a fake ``launch_editor`` — so no test
touches the real TTY, ``~/.config``, or spawns an editor.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from git_loopy import configcmd, settings


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Sink:
    """A capturing ``out`` / ``err`` callable."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _Input:
    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)

    def __call__(self, _prompt: str) -> str:
        if not self._answers:
            raise EOFError
        return self._answers.pop(0)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    base = {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Key registry + coercion (set validates/coerces per key type)
# ---------------------------------------------------------------------------


def test_registry_covers_exactly_the_persisted_schema() -> None:
    assert set(configcmd.SETTABLE_KEYS) == {
        "model",
        "reasoning_effort",
        "issue_source",
        "max_nmt_strikes",
        "include_prs",
        "otel_enabled",
        "interactive",
        "send_timeout_seconds",
        "deny_tools",
        "deny_skills",
    }


def test_coerce_bool_accepts_truthy_and_falsy_tokens() -> None:
    assert configcmd.coerce_value("include_prs", "true") is True
    assert configcmd.coerce_value("interactive", "0") is False
    assert configcmd.coerce_value("otel_enabled", "Yes") is True


def test_coerce_bool_rejects_junk() -> None:
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("include_prs", "maybe")


def test_coerce_int_and_float_validate_bounds() -> None:
    assert configcmd.coerce_value("max_nmt_strikes", "5") == 5
    assert configcmd.coerce_value("send_timeout_seconds", "1800") == 1800.0
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("max_nmt_strikes", "0")
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("send_timeout_seconds", "0")
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("max_nmt_strikes", "notanint")


def test_coerce_enum_keys_validate_choices() -> None:
    assert configcmd.coerce_value("reasoning_effort", "HIGH") == "high"
    assert configcmd.coerce_value("reasoning_effort", "MiNiMaL") == "minimal"
    assert configcmd.coerce_value("reasoning_effort", "NONE") == "none"
    assert configcmd.coerce_value("issue_source", "prds") == "prds"
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("reasoning_effort", "ultra")
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("issue_source", "gitlab")


def test_coerce_csv_keys_split_into_string_lists() -> None:
    assert configcmd.coerce_value("deny_tools", "bash, write") == ["bash", "write"]
    assert configcmd.coerce_value("deny_skills", "") == []


def test_coerce_unknown_key_raises() -> None:
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("not_a_key", "x")


# ---------------------------------------------------------------------------
# `config set` — persist one typed key to a scope, merging (no editor)
# ---------------------------------------------------------------------------


def test_set_writes_one_key_to_project_by_default_in_repo(tmp_path: Path) -> None:
    out, err = _Sink(), _Sink()
    rc = configcmd.run_set(
        "model", "gpt-5.4", scope=None, repo_root=tmp_path, env=_env(tmp_path),
        out=out, err=err,
    )
    assert rc == 0
    path = settings.project_config_path(tmp_path)
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {"model": "gpt-5.4"}
    assert "project" in out.text and str(path) in out.text


def test_set_preserves_existing_keys(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"model": "gpt-5.4", "max_nmt_strikes": 5})
    rc = configcmd.run_set(
        "reasoning_effort", "high", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=_Sink(),
    )
    assert rc == 0
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "max_nmt_strikes": 5,
        "reasoning_effort": "high",
    }


def test_set_coerces_types_not_bare_strings(tmp_path: Path) -> None:
    configcmd.run_set(
        "include_prs", "true", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=_Sink(),
    )
    configcmd.run_set(
        "send_timeout_seconds", "1800", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=_Sink(),
    )
    parsed = tomllib.loads(
        settings.project_config_path(tmp_path).read_text(encoding="utf-8")
    )
    assert parsed["include_prs"] is True  # bool, not "true"
    assert parsed["send_timeout_seconds"] == 1800.0  # float, not "1800"


def test_set_global_scope_writes_under_config_home(tmp_path: Path) -> None:
    env = _env(tmp_path)
    rc = configcmd.run_set(
        "model", "gpt-5.4", scope="global", repo_root=None, env=env,
        out=_Sink(), err=_Sink(),
    )
    assert rc == 0
    assert settings.global_config_path(env).is_file()


def test_set_rejects_unknown_key(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_set(
        "bogus", "x", scope="project", repo_root=tmp_path, env=_env(tmp_path),
        out=_Sink(), err=err,
    )
    assert rc == 1
    assert "bogus" in err.text
    assert not settings.project_config_path(tmp_path).exists()


def test_set_rejects_bad_value(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_set(
        "max_nmt_strikes", "0", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=err,
    )
    assert rc == 1
    assert "max_nmt_strikes" in err.text
    assert not settings.project_config_path(tmp_path).exists()


def test_set_project_scope_outside_repo_errors(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_set(
        "model", "gpt-5.4", scope="project", repo_root=None, env=_env(tmp_path),
        out=_Sink(), err=err,
    )
    assert rc == 1
    assert "project" in err.text and "repository" in err.text


# ---------------------------------------------------------------------------
# `config routing set` — validate and merge one task-type route
# ---------------------------------------------------------------------------


def test_routing_set_validates_and_preserves_sibling_routes(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(
        path,
        {
            "model": "claude-opus-4.8",
            "routing": {
                "planning": {"model": "claude-opus-4.8", "effort": "max"},
            },
        },
    )

    rc = configcmd.run_routing_set(
        "docs",
        "gpt-5-mini",
        "medium",
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        out=_Sink(),
        err=_Sink(),
    )

    assert rc == 0
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "model": "claude-opus-4.8",
        "routing": {
            "planning": {"model": "claude-opus-4.8", "effort": "max"},
            "docs": {"model": "gpt-5-mini", "effort": "medium"},
        },
    }


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("not-in-roster", "high"),
        ("gpt-5-mini", "max"),
        ("gpt-5-mini", "ultra"),
    ],
)
def test_routing_set_rejects_invalid_model_or_effort_without_writing(
    tmp_path: Path, model: str, effort: str
) -> None:
    err = _Sink()

    rc = configcmd.run_routing_set(
        "docs",
        model,
        effort,
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        out=_Sink(),
        err=err,
    )

    assert rc == 1
    assert "routing" in err.text.lower()
    assert not settings.project_config_path(tmp_path).exists()


def test_routing_unset_removes_only_the_named_route(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(
        path,
        {
            "model": "gpt-5.4",
            "routing": {
                "planning": {"model": "claude-opus-4.8", "effort": "max"},
                "docs": {"model": "gpt-5-mini", "effort": "medium"},
            },
        },
    )

    rc = configcmd.run_routing_unset(
        "planning",
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        out=_Sink(),
        err=_Sink(),
    )

    assert rc == 0
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "routing": {
            "docs": {"model": "gpt-5-mini", "effort": "medium"},
        },
    }


def test_routing_list_prints_effective_project_over_global_map(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    settings.write_config(
        settings.global_config_path(env),
        {
            "routing": {
                "planning": {"model": "claude-opus-4.8", "effort": "max"},
                "docs": {"model": "gpt-5-mini", "effort": "low"},
            }
        },
    )
    settings.write_config(
        settings.project_config_path(tmp_path),
        {
            "routing": {
                "docs": {"model": "gpt-5-mini", "effort": "medium"},
            }
        },
    )
    out = _Sink()

    rc = configcmd.run_routing_list(
        repo_root=tmp_path, env=env, out=out, err=_Sink()
    )

    assert rc == 0
    assert out.lines == [
        "task-type:docs = gpt-5-mini @ medium",
        "task-type:planning = claude-opus-4.8 @ max",
    ]


def test_routing_use_recommended_seeds_core_and_preserves_custom_routes(
    tmp_path: Path,
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(
        path,
        {
            "routing": {
                "custom": {"model": "gpt-5.4", "effort": "high"},
                "docs": {"model": "gpt-5.4", "effort": "high"},
            }
        },
    )

    rc = configcmd.run_routing_use_recommended(
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        out=_Sink(),
        err=_Sink(),
    )

    assert rc == 0
    routing = tomllib.loads(path.read_text(encoding="utf-8"))["routing"]
    assert routing["custom"] == {"model": "gpt-5.4", "effort": "high"}
    assert routing["planning"] == {"model": "claude-opus-4.8", "effort": "max"}
    assert routing["docs"] == {"model": "gpt-5-mini", "effort": "medium"}
    assert len(routing) == 7


def test_routing_guided_accept_all_commits_recommended_core(tmp_path: Path) -> None:
    from git_loopy import init as init_module
    from git_loopy.config import RECOMMENDED_ROUTING

    rc = configcmd.run_routing_guided(
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(""),
        out=_Sink(),
        err=_Sink(),
        fetch_choices=init_module._static_choices,
    )

    assert rc == 0
    parsed = tomllib.loads(
        settings.project_config_path(tmp_path).read_text(encoding="utf-8")
    )
    assert {
        key: (entry["model"], entry["effort"])
        for key, entry in parsed["routing"].items()
    } == dict(RECOMMENDED_ROUTING)


def test_routing_guided_cancel_writes_nothing(tmp_path: Path) -> None:
    from git_loopy import init as init_module

    rc = configcmd.run_routing_guided(
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("n", "q"),
        out=_Sink(),
        err=_Sink(),
        fetch_choices=init_module._static_choices,
    )

    assert rc == 1
    assert not settings.project_config_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# `config get` / `config list` — effective merged values via the resolver
# ---------------------------------------------------------------------------


def test_get_falls_back_to_builtin_default(tmp_path: Path) -> None:
    out = _Sink()
    rc = configcmd.run_get(
        "model", repo_root=tmp_path, env=_env(tmp_path), out=out, err=_Sink()
    )
    assert rc == 0
    assert out.text == "claude-opus-4.8"  # built-in default, no quotes


def test_get_reflects_project_config(tmp_path: Path) -> None:
    settings.write_config(
        settings.project_config_path(tmp_path), {"model": "gpt-5.4"}
    )
    out = _Sink()
    configcmd.run_get(
        "model", repo_root=tmp_path, env=_env(tmp_path), out=out, err=_Sink()
    )
    assert out.text == "gpt-5.4"


def test_get_env_overrides_project(tmp_path: Path) -> None:
    settings.write_config(
        settings.project_config_path(tmp_path), {"model": "gpt-5.4"}
    )
    out = _Sink()
    configcmd.run_get(
        "model",
        repo_root=tmp_path,
        env=_env(tmp_path, GIT_LOOPY_MODEL="claude-opus-4.7"),
        out=out,
        err=_Sink(),
    )
    assert out.text == "claude-opus-4.7"  # env tier wins over project


def test_get_global_used_when_no_project(tmp_path: Path) -> None:
    env = _env(tmp_path)
    settings.write_config(settings.global_config_path(env), {"issue_source": "prds"})
    out = _Sink()
    configcmd.run_get(
        "issue_source", repo_root=tmp_path, env=env, out=out, err=_Sink()
    )
    assert out.text == "prds"


def test_get_denylist_is_union_across_sources(tmp_path: Path) -> None:
    settings.write_config(
        settings.project_config_path(tmp_path), {"deny_tools": ["write"]}
    )
    out = _Sink()
    configcmd.run_get(
        "deny_tools",
        repo_root=tmp_path,
        env=_env(tmp_path, GIT_LOOPY_DENY_TOOLS="bash"),
        out=out,
        err=_Sink(),
    )
    assert out.text == "bash,write"  # env ∪ project, sorted


def test_get_tri_state_none_renders_empty(tmp_path: Path) -> None:
    out = _Sink()
    configcmd.run_get(
        "interactive", repo_root=tmp_path, env=_env(tmp_path), out=out, err=_Sink()
    )
    assert out.text == ""  # unset interactive intent -> empty (auto)


def test_get_unknown_key_errors(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_get(
        "bogus", repo_root=tmp_path, env=_env(tmp_path), out=_Sink(), err=err
    )
    assert rc == 1
    assert "bogus" in err.text


def test_get_works_outside_a_repo(tmp_path: Path) -> None:
    env = _env(tmp_path)
    settings.write_config(settings.global_config_path(env), {"model": "gpt-5.4"})
    out = _Sink()
    rc = configcmd.run_get(
        "model", repo_root=None, env=env, out=out, err=_Sink()
    )
    assert rc == 0
    assert out.text == "gpt-5.4"


def test_list_shows_every_effective_key(tmp_path: Path) -> None:
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"model": "gpt-5.4", "max_nmt_strikes": 5},
    )
    out = _Sink()
    rc = configcmd.run_list(
        repo_root=tmp_path, env=_env(tmp_path), out=out, err=_Sink()
    )
    assert rc == 0
    lines = set(out.lines)
    assert "model = gpt-5.4" in lines
    assert "max_nmt_strikes = 5" in lines
    assert "issue_source = github" in lines  # default surfaces too
    assert "send_timeout_seconds = 7200" in lines  # whole float, no .0 tail
    # Every settable key appears exactly once.
    assert len(out.lines) == len(configcmd.SETTABLE_KEYS)


def test_get_malformed_config_errors_cleanly(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("model = = broken\n", encoding="utf-8")
    err = _Sink()
    rc = configcmd.run_get(
        "model", repo_root=tmp_path, env=_env(tmp_path), out=_Sink(), err=err
    )
    assert rc == 1
    assert "TOML" in err.text or "config" in err.text.lower()


# ---------------------------------------------------------------------------
# `config path` — print the resolved config.toml location(s)
# ---------------------------------------------------------------------------


def test_path_no_flag_prints_both_scopes_in_repo(tmp_path: Path) -> None:
    env = _env(tmp_path)
    out = _Sink()
    rc = configcmd.run_path(
        scope=None, repo_root=tmp_path, env=env, out=out, err=_Sink()
    )
    assert rc == 0
    assert str(settings.project_config_path(tmp_path)) in out.text
    assert str(settings.global_config_path(env)) in out.text
    assert "project" in out.text and "global" in out.text


def test_path_project_flag_prints_bare_path(tmp_path: Path) -> None:
    out = _Sink()
    rc = configcmd.run_path(
        scope="project", repo_root=tmp_path, env=_env(tmp_path), out=out, err=_Sink()
    )
    assert rc == 0
    assert out.text == str(settings.project_config_path(tmp_path))  # bare, scriptable


def test_path_global_flag_prints_bare_path(tmp_path: Path) -> None:
    env = _env(tmp_path)
    out = _Sink()
    rc = configcmd.run_path(
        scope="global", repo_root=tmp_path, env=env, out=out, err=_Sink()
    )
    assert rc == 0
    assert out.text == str(settings.global_config_path(env))


def test_path_no_flag_outside_repo_prints_global_only(tmp_path: Path) -> None:
    env = _env(tmp_path)
    out, err = _Sink(), _Sink()
    rc = configcmd.run_path(scope=None, repo_root=None, env=env, out=out, err=err)
    assert rc == 0
    assert str(settings.global_config_path(env)) in out.text
    assert "project" not in out.text  # unavailable off-repo
    assert "project" in err.text  # noted on stderr


def test_path_project_flag_outside_repo_errors(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_path(
        scope="project", repo_root=None, env=_env(tmp_path), out=_Sink(), err=err
    )
    assert rc == 1
    assert "project" in err.text and "repository" in err.text


# ---------------------------------------------------------------------------
# `config edit` — open the scope's config.toml in $VISUAL / $EDITOR
# ---------------------------------------------------------------------------


class _FakeEditor:
    """Records the argv it was launched with; returns a scripted exit code."""

    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(argv)
        return self.rc


def test_edit_launches_editor_on_project_path_by_default(tmp_path: Path) -> None:
    editor = _FakeEditor()
    rc = configcmd.run_edit(
        scope=None,
        repo_root=tmp_path,
        env=_env(tmp_path, EDITOR="nano"),
        out=_Sink(),
        err=_Sink(),
        launch_editor=editor,
    )
    assert rc == 0
    path = settings.project_config_path(tmp_path)
    assert editor.calls == [["nano", str(path)]]


def test_edit_seeds_a_header_stub_when_missing(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    assert not path.exists()
    configcmd.run_edit(
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path, EDITOR="nano"),
        out=_Sink(),
        err=_Sink(),
        launch_editor=_FakeEditor(),
    )
    assert path.is_file()
    assert "#" in path.read_text(encoding="utf-8")  # header comment seeded
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {}  # valid + empty


def test_edit_does_not_touch_an_existing_file(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"model": "gpt-5.4"})
    before = path.read_text(encoding="utf-8")
    configcmd.run_edit(
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path, EDITOR="nano"),
        out=_Sink(),
        err=_Sink(),
        launch_editor=_FakeEditor(),
    )
    assert path.read_text(encoding="utf-8") == before


def test_edit_global_scope_targets_config_home(tmp_path: Path) -> None:
    env = _env(tmp_path, EDITOR="nano")
    editor = _FakeEditor()
    configcmd.run_edit(
        scope="global", repo_root=tmp_path, env=env, out=_Sink(), err=_Sink(),
        launch_editor=editor,
    )
    assert editor.calls == [["nano", str(settings.global_config_path(env))]]


def test_edit_prefers_visual_and_splits_args(tmp_path: Path) -> None:
    editor = _FakeEditor()
    configcmd.run_edit(
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path, VISUAL="code --wait", EDITOR="nano"),
        out=_Sink(),
        err=_Sink(),
        launch_editor=editor,
    )
    path = settings.project_config_path(tmp_path)
    assert editor.calls == [["code", "--wait", str(path)]]  # VISUAL wins, split


def test_edit_without_any_editor_errors(tmp_path: Path) -> None:
    err = _Sink()
    editor = _FakeEditor()
    rc = configcmd.run_edit(
        scope="project", repo_root=tmp_path, env=_env(tmp_path), out=_Sink(),
        err=err, launch_editor=editor,
    )
    assert rc == 1
    assert "EDITOR" in err.text or "editor" in err.text
    assert editor.calls == []  # never launched


def test_edit_project_outside_repo_errors(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_edit(
        scope="project", repo_root=None, env=_env(tmp_path, EDITOR="nano"),
        out=_Sink(), err=err, launch_editor=_FakeEditor(),
    )
    assert rc == 1
    assert "project" in err.text and "repository" in err.text


def test_edit_returns_editor_exit_code(tmp_path: Path) -> None:
    rc = configcmd.run_edit(
        scope="project", repo_root=tmp_path, env=_env(tmp_path, EDITOR="nano"),
        out=_Sink(), err=_Sink(), launch_editor=_FakeEditor(rc=3),
    )
    assert rc == 3
