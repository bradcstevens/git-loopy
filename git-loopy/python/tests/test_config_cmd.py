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

from git_loopy import configcmd, measured_routing, settings
from git_loopy.config import RECOMMENDED_ROUTING


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
        "classifier_model",
        "classifier_effort",
        "issue_source",
        "max_nmt_strikes",
        "demotion_threshold",
        "include_prs",
        "otel_enabled",
        "interactive",
        "send_timeout_seconds",
        "deny_tools",
        "deny_skills",
        "enabled_skills",
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
    assert configcmd.coerce_value(
        "enabled_skills", "unknown-skill, alpha, alpha"
    ) == ["alpha", "unknown-skill"]


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


def test_set_enabled_skills_round_trips_explicit_empty_and_preserves_siblings(
    tmp_path: Path,
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"model": "gpt-5.4", "enabled_skills": ["alpha"]})

    rc = configcmd.run_set(
        "enabled_skills",
        "",
        scope="project",
        repo_root=tmp_path,
        env=_env(tmp_path),
        out=_Sink(),
        err=_Sink(),
    )

    assert rc == 0
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "enabled_skills": [],
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
        "task-type:docs = gpt-5-mini @ medium (project Config)",
        "task-type:planning = claude-opus-4.8 @ max (global Config)",
    ]


def test_routing_list_shows_measured_entries_the_operator_has_not_configured(
    tmp_path: Path,
) -> None:
    """The **Measured routing** tier fills gaps in what `config routing list` reports.

    A tier a Run honours but the reporting command cannot see would leave an
    operator reading one map while the loop routes on another (#361, ADR-0028).
    """
    env = _env(tmp_path)
    def _measured(model: str, effort: str) -> measured_routing.MeasuredEntry:
        return measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.MEASURED,
            model=model,
            effort=effort,
            trials_passed=5,
            trials_total=5,
            rungs_walked=1,
            credits=412.0,
            wall_clock_seconds=903,
            rungs=(
                measured_routing.Rung(
                    model=model, effort=effort, passed=5, total=5, credits=412.0
                ),
            ),
            proving_tasks=(
                measured_routing.ProvingTask(
                    issue=214, base_commit="9747237", oracle_commit="e31ceab"
                ),
            ),
        )

    measured_routing.write_measured_routing(
        tmp_path,
        measured_routing.MeasuredRouting(
            entries={
                "docs": _measured("synthetic-cheap-1", "low"),
                "test": _measured("synthetic-cheap-2", "medium"),
            },
            provenance=measured_routing.Provenance(
                cli_version="1.0.67",
                calibrated_at="2026-08-13T14:02:11Z",
                candidate_count=85,
                gate_loops=("Python suite",),
            ),
        ),
    )
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
    )
    out = _Sink()

    rc = configcmd.run_routing_list(repo_root=tmp_path, env=env, out=out, err=_Sink())

    assert rc == 0
    assert out.lines == [
        "task-type:docs = synthetic-fancy-9 @ high (project Config)",  # hand-written wins
        "task-type:test = synthetic-cheap-2 @ medium (measured)",  # measured fills the gap
    ]


def test_routing_use_recommended_replaces_every_canonical_route(
    tmp_path: Path,
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(
        path,
        {
            "routing": {
                "review": {"model": "gpt-5.4", "effort": "high"},
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
    assert routing["review"] == {"model": "gpt-5.6-terra", "effort": "xhigh"}
    assert routing["planning"] == {"model": "claude-opus-5", "effort": "max"}
    assert routing["docs"] == {"model": "claude-sonnet-5", "effort": "low"}
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
    assert out.text == "claude-opus-5"  # built-in default, no quotes


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


def test_get_enabled_skills_uses_presence_aware_replacement(tmp_path: Path) -> None:
    env = _env(tmp_path)
    settings.write_config(
        settings.global_config_path(env), {"enabled_skills": ["global-skill"]}
    )
    settings.write_config(
        settings.project_config_path(tmp_path), {"enabled_skills": []}
    )
    out = _Sink()

    configcmd.run_get(
        "enabled_skills", repo_root=tmp_path, env=env, out=out, err=_Sink()
    )

    assert out.text == ""


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


# ---------------------------------------------------------------------------
# Routing provenance (#364) — `get` / `list` / `routing list` name the tier that
# supplied a **Routed pair**, so a value the operator never typed can be traced.
# ---------------------------------------------------------------------------


def _write_measured(repo_root: Path, **pairs: tuple[str, str]) -> None:
    """Hand-place a **Measured routing** artifact carrying ``pairs``."""

    def _entry(model: str, effort: str) -> measured_routing.MeasuredEntry:
        return measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.MEASURED,
            model=model,
            effort=effort,
            trials_passed=5,
            trials_total=5,
            rungs_walked=1,
            credits=412.0,
            wall_clock_seconds=903,
            rungs=(
                measured_routing.Rung(
                    model=model, effort=effort, passed=5, total=5, credits=412.0
                ),
            ),
            proving_tasks=(
                measured_routing.ProvingTask(
                    issue=214, base_commit="9747237", oracle_commit="e31ceab"
                ),
            ),
        )

    measured_routing.write_measured_routing(
        repo_root,
        measured_routing.MeasuredRouting(
            entries={key: _entry(*pair) for key, pair in pairs.items()},
            provenance=measured_routing.Provenance(
                cli_version="1.0.67",
                calibrated_at="2026-08-13T14:02:11Z",
                candidate_count=85,
                gate_loops=("Python suite",),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("scope_table", "expected"),
    [
        (None, "synthetic-cheap-1 @ low (measured)"),
        ("project", "synthetic-fancy-9 @ high (project Config)"),
        ("global", "synthetic-fancy-9 @ high (global Config)"),
    ],
)
def test_get_on_a_routing_key_names_the_tier_that_supplied_it(
    tmp_path: Path, scope_table: str | None, expected: str
) -> None:
    """A **Routed pair** is reported with the tier whose value is in force."""
    env = _env(tmp_path)
    _write_measured(tmp_path, docs=("synthetic-cheap-1", "low"))
    if scope_table is not None:
        path = (
            settings.project_config_path(tmp_path)
            if scope_table == "project"
            else settings.global_config_path(env)
        )
        settings.write_config(
            path,
            {"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
        )
    out = _Sink()

    rc = configcmd.run_get(
        "task-type:docs", repo_root=tmp_path, env=env, out=out, err=_Sink()
    )

    assert rc == 0
    assert out.text == expected


def test_get_on_an_unrouted_task_type_names_the_builtin_default(
    tmp_path: Path,
) -> None:
    """A Task type no tier names falls through, and says so.

    ``RECOMMENDED_ROUTING`` is a seeding core, not a resolution tier: a
    repository that has never been **Calibrated** must not have its unrouted
    Task types reported as if some tier had supplied them.
    """
    env = _env(tmp_path)
    _write_measured(tmp_path, docs=("synthetic-cheap-1", "low"))
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"model": "gpt-5.4", "reasoning_effort": "high"},
    )
    out, err = _Sink(), _Sink()

    rc = configcmd.run_get(
        "task-type:planning", repo_root=tmp_path, env=env, out=out, err=err
    )

    assert rc == 0
    # The run-wide pair, not the RECOMMENDED_ROUTING entry for `planning`
    # (claude-opus-5 @ max) — the recommended core seeds Config, it does not
    # resolve, and the report must not imply otherwise.
    assert out.text == "gpt-5.4 @ high (built-in default)"
    assert RECOMMENDED_ROUTING["planning"][0] not in out.text
    # ...but the operator is told the recommended core exists and is unadopted,
    # so "built-in default" is not mistaken for "nothing is available".
    assert "use-recommended" in err.text


@pytest.mark.parametrize(
    ("env_extra", "tier"),
    [
        ({"GIT_LOOPY_MODEL": "gpt-5-mini"}, "environment variable"),
        ({"GIT_LOOPY_REASONING_EFFORT": "low"}, "environment variable"),
    ],
)
def test_get_reports_run_wide_suppression_rather_than_a_dead_tier(
    tmp_path: Path, env_extra: dict[str, str], tier: str
) -> None:
    """With routing off run-wide, the measured value is not in force — say so."""
    env = _env(tmp_path, **env_extra)
    _write_measured(tmp_path, docs=("synthetic-cheap-1", "low"))
    out = _Sink()

    rc = configcmd.run_get(
        "task-type:docs", repo_root=tmp_path, env=env, out=out, err=_Sink()
    )

    assert rc == 0
    assert f"({tier} — routing suppressed run-wide)" in out.text
    assert "synthetic-cheap-1" not in out.text


# ---------------------------------------------------------------------------
# The fourth status (#376, ADR-0030): a pair in force that nobody measured must
# never be reported as a measured one.
# ---------------------------------------------------------------------------


def _write_provisional(repo_root: Path, key: str) -> None:
    """Hand-place an artifact whose one record is **provisional**, not measured."""
    measured_routing.write_measured_routing(
        repo_root,
        measured_routing.MeasuredRouting(
            entries={
                key: measured_routing.MeasuredEntry(
                    status=measured_routing.MeasuredStatus.PROVISIONAL,
                    model="synthetic-cheap-2",
                    effort="medium",
                    replaced_model="synthetic-cheap-1",
                    replaced_effort="low",
                    replaced_after_no_progress=3,
                    reason=measured_routing.ProvisionalReason.DEMOTION,
                )
            },
            provenance=measured_routing.Provenance(
                cli_version="1.0.67",
                calibrated_at="2026-08-13T14:02:11Z",
                candidate_count=85,
                gate_loops=("Python suite",),
            ),
        ),
    )


def test_get_names_a_provisional_pair_as_unmeasured(tmp_path: Path) -> None:
    """The pair is in force, so it is reported — under its own tier, never as measured."""
    env = _env(tmp_path)
    _write_provisional(tmp_path, "docs")
    out = _Sink()

    rc = configcmd.run_get(
        "task-type:docs", repo_root=tmp_path, env=env, out=out, err=_Sink()
    )

    assert rc == 0
    assert out.text == "synthetic-cheap-2 @ medium (provisional (unmeasured))"
    assert "(measured)" not in out.text


def test_list_names_a_provisional_pair_as_unmeasured(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _write_provisional(tmp_path, "docs")
    out = _Sink()

    rc = configcmd.run_list(repo_root=tmp_path, env=env, out=out, err=_Sink())

    assert rc == 0
    assert (
        "task-type:docs = synthetic-cheap-2 @ medium (provisional (unmeasured))"
        in out.lines
    )


def test_routing_list_names_a_provisional_pair_as_unmeasured(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _write_provisional(tmp_path, "docs")
    out = _Sink()

    rc = configcmd.run_routing_list(repo_root=tmp_path, env=env, out=out, err=_Sink())

    assert rc == 0
    assert out.lines == [
        "task-type:docs = synthetic-cheap-2 @ medium (provisional (unmeasured))"
    ]


def test_get_rejects_a_malformed_task_type_key(tmp_path: Path) -> None:
    err = _Sink()
    rc = configcmd.run_get(
        "task-type:", repo_root=tmp_path, env=_env(tmp_path), out=_Sink(), err=err
    )
    assert rc == 1
    assert "empty" in err.text


def test_list_names_the_tier_for_every_routing_entry_it_renders(
    tmp_path: Path,
) -> None:
    """`config list` renders the routing map beside the settings, tiers named."""
    env = _env(tmp_path)
    _write_measured(
        tmp_path,
        docs=("synthetic-cheap-1", "low"),
        test=("synthetic-cheap-2", "medium"),
    )
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
    )
    out = _Sink()

    rc = configcmd.run_list(repo_root=tmp_path, env=env, out=out, err=_Sink())

    assert rc == 0
    routing_lines = [line for line in out.lines if line.startswith("task-type:")]
    assert routing_lines == [
        "task-type:docs = synthetic-fancy-9 @ high (project Config)",
        "task-type:test = synthetic-cheap-2 @ medium (measured)",
    ]
    # The scalar keys are untouched by the addition.
    assert len(out.lines) == len(configcmd.SETTABLE_KEYS) + len(routing_lines)


def test_routing_list_names_the_tier_for_every_entry(tmp_path: Path) -> None:
    """The routing-only surface answers "why is this model set?" too."""
    env = _env(tmp_path)
    _write_measured(tmp_path, test=("synthetic-cheap-2", "medium"))
    settings.write_config(
        settings.global_config_path(env),
        {"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
    )
    out = _Sink()

    rc = configcmd.run_routing_list(repo_root=tmp_path, env=env, out=out, err=_Sink())

    assert rc == 0
    assert out.lines == [
        "task-type:docs = synthetic-fancy-9 @ high (global Config)",
        "task-type:test = synthetic-cheap-2 @ medium (measured)",
    ]


@pytest.mark.parametrize("op", ["path", "edit"])
def test_no_scope_op_accepts_a_measured_scope(tmp_path: Path, op: str) -> None:
    """The artifact is machine-written, so there is no scope to edit or point at.

    An operator who wants rid of a measured value deletes the file; one who wants
    to overrule it writes a Config entry that wins (ADR-0028).
    """
    env = _env(tmp_path)
    err = _Sink()
    kwargs: dict[str, object] = {
        "scope": "measured",
        "repo_root": tmp_path,
        "env": env,
        "out": _Sink(),
        "err": err,
    }
    if op == "edit":
        kwargs["launch_editor"] = lambda _argv: pytest.fail("no editor may launch")

    rc = getattr(configcmd, f"run_{op}")(**kwargs)

    assert rc == 1
    assert "measured" in err.text
    assert not (tmp_path / "git-loopy" / "routing.measured.toml").exists()


def test_get_on_a_routing_key_qualifies_nothing_in_serial_mode(
    tmp_path: Path,
) -> None:
    """The tier has effect in the default mode, so there is nothing to note (#404).

    ``config get`` used to add an inertness note on stderr, because a serial
    **Iteration** ran on the run-wide pair whatever the chain resolved. ADR-0037
    made the resolved pair the pair the session runs on, so the note would now
    tell an operator their configuration has no effect on the very Run where it
    does — the same unexplainability #364 exists to remove, pointed the other
    way.
    """
    env = _env(tmp_path)
    _write_measured(tmp_path, docs=("synthetic-cheap-1", "low"))
    out, err = _Sink(), _Sink()

    rc = configcmd.run_get(
        "task-type:docs", repo_root=tmp_path, env=env, out=out, err=err
    )

    assert rc == 0
    assert out.text == "synthetic-cheap-1 @ low (measured)"
    assert "note:" not in err.text


@pytest.mark.parametrize("surface", ["get", "list", "routing_list"])
def test_no_reporting_surface_qualifies_a_routed_pair_by_parallelism(
    tmp_path: Path, surface: str
) -> None:
    """One rule, three surfaces, and the rule reversed once (#404).

    ``config get``, ``config list`` and ``config routing list`` each printed the
    same inertness note from :mod:`git_loopy.routing_scope`, so that they could
    not drift from the refusal ``git-loopy calibrate`` printed for the same
    reason. The reason is gone from all four at once — which is what reading one
    rule from one module bought.
    """
    env = _env(tmp_path)
    _write_measured(tmp_path, docs=("synthetic-cheap-1", "low"))
    err = _Sink()
    kwargs: dict[str, object] = {
        "repo_root": tmp_path,
        "env": env,
        "out": _Sink(),
        "err": err,
    }
    if surface == "get":
        kwargs["key"] = "task-type:docs"

    rc = getattr(configcmd, f"run_{surface}")(**kwargs)

    assert rc == 0
    assert "inert" not in err.text
    assert "--parallel" not in err.text


def test_list_reports_suppression_rather_than_dropping_the_routing_map(
    tmp_path: Path,
) -> None:
    """Suppression must be *said*, not shown as an absence.

    A run-wide override empties the effective routing map, and a `config list`
    that simply stops printing routing looks identical to a repository that
    configured none — the operator is told nothing about why their routes are
    not in force.
    """
    env = _env(tmp_path, GIT_LOOPY_MODEL="gpt-5-mini")
    _write_measured(tmp_path, test=("synthetic-cheap-2", "medium"))
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
    )
    out = _Sink()

    rc = configcmd.run_list(repo_root=tmp_path, env=env, out=out, err=_Sink())

    assert rc == 0
    routing_lines = [line for line in out.lines if line.startswith("task-type:")]
    assert routing_lines == [
        "task-type:docs = gpt-5-mini "
        "(environment variable — routing suppressed run-wide)",
        "task-type:test = gpt-5-mini "
        "(environment variable — routing suppressed run-wide)",
    ]


def test_get_refuses_a_task_type_outside_the_closed_taxonomy(
    tmp_path: Path,
) -> None:
    """An invalid task type reports the permitted keys instead of a default."""
    err = _Sink()
    rc = configcmd.run_get(
        "task-type:migrations",
        repo_root=tmp_path,
        env=_env(tmp_path),
        out=_Sink(),
        err=err,
    )
    assert rc == 1
    assert "migrations" in err.text
    for key in RECOMMENDED_ROUTING:
        assert key in err.text


# ---------------------------------------------------------------------------
# A persisted Task type outside the closed taxonomy (#375)
# ---------------------------------------------------------------------------
#
# Closing the taxonomy refuses an out-of-taxonomy key at every *write* seam, but
# a repository configured before the closure already carries one on disk. The
# refusal an operator meets there has to be a refusal — a named reason on the
# error channel and a non-zero status — rather than a traceback out of the
# resolver, and it has to leave them a way back: a taxonomy that can only be
# complied with by hand-editing TOML is not closed, it is stuck.


def _legacy_routing_config(tmp_path: Path, key: str = "custom") -> Path:
    """Write a project Config carrying one routing key outside the taxonomy."""
    path = settings.project_config_path(tmp_path)
    settings.write_config(
        path, {"routing": {key: {"model": "gpt-5.4", "effort": "high"}}}
    )
    return path


@pytest.mark.parametrize(
    "operation",
    ["list", "routing_list", "get"],
    ids=["config-list", "config-routing-list", "config-get"],
)
def test_a_read_surface_refuses_a_persisted_task_type_it_cannot_route(
    tmp_path: Path, operation: str
) -> None:
    """Reading a Config carrying a legacy key refuses; it never raises."""
    _legacy_routing_config(tmp_path)
    err = _Sink()
    kwargs = dict(repo_root=tmp_path, env=_env(tmp_path), out=_Sink(), err=err)

    if operation == "list":
        rc = configcmd.run_list(**kwargs)
    elif operation == "routing_list":
        rc = configcmd.run_routing_list(**kwargs)
    else:
        rc = configcmd.run_get("task-type:docs", **kwargs)

    assert rc == 1
    assert "custom" in err.text
    for key in RECOMMENDED_ROUTING:
        assert key in err.text


def test_the_refusal_names_the_command_that_clears_the_offending_key(
    tmp_path: Path,
) -> None:
    """A refusal an operator cannot act on is a lockout, so it names the remedy."""
    _legacy_routing_config(tmp_path)
    err = _Sink()

    rc = configcmd.run_routing_list(
        repo_root=tmp_path, env=_env(tmp_path), out=_Sink(), err=err
    )

    assert rc == 1
    assert "config routing unset custom" in err.text


def test_a_global_legacy_route_names_the_global_recovery_command(
    tmp_path: Path,
) -> None:
    """A recovery command must target the scope that carries the legacy key."""
    env = _env(tmp_path)
    settings.write_config(
        settings.global_config_path(env),
        {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}},
    )
    err = _Sink()

    rc = configcmd.run_routing_list(
        repo_root=tmp_path, env=env, out=_Sink(), err=err
    )

    assert rc == 1
    assert "config routing unset custom --global" in err.text


def test_routing_unset_clears_a_key_outside_the_taxonomy(tmp_path: Path) -> None:
    """Removal is how an operator complies, so it is the one op the closure allows.

    Refusing a key is a rule about what may be *written*; deleting one already
    on disk is the operator agreeing with the rule. Refusing that too would make
    the taxonomy uncloseable in practice — hand-edited TOML would be the only
    way to comply with it.
    """
    path = _legacy_routing_config(tmp_path)
    out, err = _Sink(), _Sink()

    rc = configcmd.run_routing_unset(
        "custom", scope="project", repo_root=tmp_path, env=_env(tmp_path),
        out=out, err=err,
    )

    assert rc == 0, err.text
    assert "custom" not in path.read_text(encoding="utf-8")


def test_routing_unset_leaves_the_taxonomy_closed_to_writes(tmp_path: Path) -> None:
    """``set`` still refuses an out-of-taxonomy key; only removal is permitted."""
    err = _Sink()

    rc = configcmd.run_routing_set(
        "custom", "gpt-5.4", "high", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=err,
    )

    assert rc == 1
    assert "custom" in err.text
    assert not settings.project_config_path(tmp_path).exists()


def test_a_write_refused_by_a_persisted_key_names_that_key_and_the_remedy(
    tmp_path: Path,
) -> None:
    """Setting a *valid* route is refused by a legacy sibling, so the error says which.

    The operator typed ``docs``; the key the closure objects to is ``custom``,
    already on disk. An error naming only "unsupported task type 'custom'" reads
    as the tool rejecting what was just typed, so the refusal has to say the key
    came from the Config and how to clear it.
    """
    path = _legacy_routing_config(tmp_path)
    err = _Sink()

    rc = configcmd.run_routing_set(
        "docs", "gpt-5.4", "high", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=err,
    )

    assert rc == 1
    assert "config routing unset custom" in err.text
    assert "custom" in path.read_text(encoding="utf-8")  # refused, not laundered


def test_the_remedy_clears_a_legacy_key_living_in_the_global_scope(
    tmp_path: Path,
) -> None:
    """The advertised command has to reach the key wherever it actually lives.

    Scope defaults to *project* inside a repo, so an unscoped ``routing unset``
    aimed at a global legacy key would report success, write the project file,
    and leave the Run blocked by the key it claimed to clear. An out-of-taxonomy
    key is invalid in every scope, so clearing it is a repair rather than a
    scoped edit.
    """
    env = _env(tmp_path)
    global_path = settings.global_config_path(env)
    settings.write_config(
        global_path, {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}}
    )
    out, err = _Sink(), _Sink()

    rc = configcmd.run_routing_unset(
        "custom", scope=None, repo_root=tmp_path, env=env, out=out, err=err
    )

    assert rc == 0, err.text
    assert "custom" not in global_path.read_text(encoding="utf-8")


def test_the_remedy_clears_a_legacy_key_whose_spelling_no_write_op_accepts(
    tmp_path: Path,
) -> None:
    """A pre-closure Config could hold any quoted TOML key, so removal accepts any.

    The syntactic charset check exists to catch a typo in a key being *written*.
    Applied to removal it strands exactly the keys with no other way out — the
    refusal would name a command that refuses itself.
    """
    path = settings.project_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[routing]\n"migration v2" = { model = "gpt-5.4", effort = "high" }\n',
        encoding="utf-8",
    )
    out, err = _Sink(), _Sink()

    rc = configcmd.run_routing_unset(
        "migration v2", scope="project", repo_root=tmp_path, env=_env(tmp_path),
        out=out, err=err,
    )

    assert rc == 0, err.text
    assert "migration v2" not in path.read_text(encoding="utf-8")


def _write_raw_routing(tmp_path: Path, body: str) -> Path:
    """Write a project Config whose ``[routing]`` table is exactly ``body``."""
    path = settings.project_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[routing]\n{body}", encoding="utf-8")
    return path


def test_a_persisted_routing_key_is_read_literally_not_renormalized(
    tmp_path: Path,
) -> None:
    """A prefixed ``[routing]`` key is refused, never folded onto the bare one.

    ``resolve_iteration_model`` already refuses ``task-type:docs`` as a routing
    *key* — the permitted keys are the bare seven — so a Config carrying it has
    never routed. Silently stripping the prefix on write made the write surface
    disagree with the resolver, and where the bare key was also present the two
    collapsed and one route was dropped from the operator's file.
    """
    path = _write_raw_routing(
        tmp_path,
        '"task-type:docs" = { model = "gpt-5.4", effort = "high" }\n'
        'docs = { model = "gpt-5-mini", effort = "medium" }\n',
    )
    err = _Sink()

    rc = configcmd.run_routing_set(
        "test", "gpt-5-mini", "medium", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=err,
    )

    assert rc == 1
    assert "task-type:docs" in err.text
    assert path.read_text(encoding="utf-8").count("model") == 2  # nothing dropped


def test_the_remedy_clears_a_prefixed_routing_key_by_its_literal_spelling(
    tmp_path: Path,
) -> None:
    """The refusal names ``unset task-type:docs``, so that has to clear that key."""
    path = _write_raw_routing(
        tmp_path,
        '"task-type:docs" = { model = "gpt-5.4", effort = "high" }\n'
        'docs = { model = "gpt-5-mini", effort = "medium" }\n',
    )
    err = _Sink()

    rc = configcmd.run_routing_unset(
        "task-type:docs", scope="project", repo_root=tmp_path,
        env=_env(tmp_path), out=_Sink(), err=err,
    )

    assert rc == 0, err.text
    remaining = path.read_text(encoding="utf-8")
    assert "task-type:docs" not in remaining
    assert "docs = " in remaining  # the bare route it collided with survives


# ---------------------------------------------------------------------------
# The Task-type classifier's own knob (#377, ADR-0029)
# ---------------------------------------------------------------------------


def test_the_classifier_pair_is_settable_and_readable_as_its_own_keys() -> None:
    """A named, overridable, visible prior — which is the whole knob (ADR-0029).

    The prior cannot be removed: something has to classify. What the knob buys is
    that it is *named* rather than inherited from the run-wide default, and it is
    only genuinely overridable if it reaches the surface an operator actually
    uses.
    """
    assert "classifier_model" in configcmd.SETTABLE_KEYS
    assert "classifier_effort" in configcmd.SETTABLE_KEYS

    assert configcmd.coerce_value("classifier_model", "cheap-model") == "cheap-model"
    assert configcmd.coerce_value("classifier_effort", " LOW ") == "low"


def test_an_unsendable_classifier_effort_is_refused_at_the_set_seam() -> None:
    """The same effort vocabulary the run-wide knob is held to.

    An effort outside :data:`~git_loopy.config.REASONING_EFFORTS` is not sendable
    by anyone, so accepting it here would persist a value every classifying
    session then fails on — once per unlabelled issue, unattended.
    """
    with pytest.raises(configcmd.ConfigCommandError):
        configcmd.coerce_value("classifier_effort", "enthusiastic")
