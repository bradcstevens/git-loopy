"""Tests for ``git_loopy.cli.resolve_config`` — the pure Config resolver (#51).

The resolver merges four sources into an effective :class:`git_loopy.config.RunConfig`
following ADR-0006's precedence chain::

    CLI flag > env var > project config > global config > built-in default

with the two denylists (``deny_tools`` / ``deny_skills``) taken as the **set union**
across all four sources. It is driven entirely through injected inputs — a parsed
``argparse.Namespace``, an environment *mapping*, and the two parsed config tables —
so no test here touches a real TTY, ``os.environ``, or the developer's ``~/.config``.

The persisted (config-tiered) knobs are: ``model``, ``reasoning_effort``,
``max_nmt_strikes``, ``issue_source``, ``include_prs``, ``deny_tools``,
``deny_skills``, ``otel_enabled``, ``interactive``, and ``send_timeout_seconds``.
The per-run-only knobs (``max_iterations``, ``verbosity``, ``render_reasoning``,
``parallel``) are NEVER read from a config file — they resolve from flags/env only.
"""

from __future__ import annotations

import pytest

from git_loopy import cli
from git_loopy.config import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    MODEL_REASONING_EFFORTS,
    REASONING_EFFORT_ORDER,
    SUPPORTED_MODELS,
    RunConfig,
)


def _args(argv: list[str] | None = None):
    """Parse a realistic namespace the way ``main`` does."""
    return cli.build_parser().parse_args(argv or [])


def _resolve(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    project: dict[str, object] | None = None,
    global_: dict[str, object] | None = None,
    measured: dict[str, tuple[str, str]] | None = None,
    measured_provisional: frozenset[str] | None = None,
    warn=cli._warn,
):
    return cli.resolve_config(
        _args(argv),
        env or {},
        project=project or {},
        global_=global_ or {},
        measured=measured or {},
        measured_provisional=measured_provisional or frozenset(),
        warn=warn,
    )


# ---------------------------------------------------------------------------
# Defaults: empty everything -> built-in defaults.
# ---------------------------------------------------------------------------


def test_resolve_all_empty_yields_builtin_defaults() -> None:
    resolved = _resolve()
    run = resolved.run
    assert isinstance(run, RunConfig)
    assert run.model == "claude-opus-5"
    assert run.reasoning_effort == "xhigh"
    assert run.issue_source == "github"
    assert run.include_prs is None
    assert run.max_iterations == 0
    assert run.max_nmt_strikes == 3
    assert run.deny_tools == frozenset()
    assert run.deny_skills == frozenset()
    assert run.verbosity == 0
    assert run.render_reasoning is True
    assert run.otel_enabled is False
    assert run.parallel == 1
    assert run.send_timeout_seconds == DEFAULT_SEND_TIMEOUT_SECONDS
    assert resolved.interactive is None
    assert resolved.run.skill_policy.project.present is False
    assert resolved.run.skill_policy.global_.present is False


def test_enabled_skills_preserves_project_presence_and_explicit_empty() -> None:
    absent = _resolve(global_={"enabled_skills": ["global-skill"]})
    empty = _resolve(
        project={"enabled_skills": []},
        global_={"enabled_skills": ["global-skill"]},
    )

    assert absent.run.skill_policy.project.present is False
    assert absent.run.skill_policy.global_.names == ("global-skill",)
    assert empty.run.skill_policy.project.present is True
    assert empty.run.skill_policy.project.names == ()


def test_enabled_skills_environment_presence_is_exact_replacement_input() -> None:
    populated = _resolve(env={"GIT_LOOPY_ENABLED_SKILLS": "beta, alpha, beta"})
    empty = _resolve(env={"GIT_LOOPY_ENABLED_SKILLS": ""})

    assert populated.run.skill_policy.environment.present is True
    assert populated.run.skill_policy.environment.names == ("alpha", "beta")
    assert empty.run.skill_policy.environment.present is True
    assert empty.run.skill_policy.environment.names == ()


def test_skill_enable_and_disable_flags_are_captured_separately() -> None:
    resolved = _resolve(
        [
            "--enable-skill",
            "alpha",
            "--enable-skill",
            "beta",
            "--disable-skill",
            "beta",
        ]
    )

    assert resolved.run.skill_policy.enable_skills == frozenset({"alpha", "beta"})
    assert resolved.run.skill_policy.disable_skills == frozenset({"beta"})


# ---------------------------------------------------------------------------
# Global-only value affects the run.
# ---------------------------------------------------------------------------


def test_global_only_value_affects_run() -> None:
    resolved = _resolve(global_={"max_nmt_strikes": 7})
    assert resolved.run.max_nmt_strikes == 7


# ---------------------------------------------------------------------------
# Project overrides global key-by-key (unset project keys fall to global).
# ---------------------------------------------------------------------------


def test_project_overrides_global_key_by_key() -> None:
    resolved = _resolve(
        project={"model": "claude-sonnet-4.6"},
        global_={"model": "gpt-5.5", "issue_source": "prds"},
    )
    # project wins on the key it sets ...
    assert resolved.run.model == "claude-sonnet-4.6"
    # ... global still supplies the key project leaves unset.
    assert resolved.run.issue_source == "prds"


# ---------------------------------------------------------------------------
# Full precedence ladder: CLI flag > env > project > global > default.
# `interactive` is the one persisted knob with a real CLI flag this slice.
# ---------------------------------------------------------------------------


def test_interactive_flag_beats_every_lower_tier() -> None:
    resolved = _resolve(
        ["--interactive"],
        env={"GIT_LOOPY_INTERACTIVE": "0"},
        project={"interactive": False},
        global_={"interactive": False},
    )
    assert resolved.interactive is True


def test_interactive_env_beats_project_and_global() -> None:
    resolved = _resolve(
        env={"GIT_LOOPY_INTERACTIVE": "1"},
        project={"interactive": False},
        global_={"interactive": False},
    )
    assert resolved.interactive is True


def test_interactive_project_beats_global() -> None:
    resolved = _resolve(project={"interactive": True}, global_={"interactive": False})
    assert resolved.interactive is True


def test_interactive_global_only() -> None:
    resolved = _resolve(global_={"interactive": True})
    assert resolved.interactive is True


def test_interactive_unset_everywhere_is_none() -> None:
    assert _resolve().interactive is None


# ---------------------------------------------------------------------------
# Denylists: union across all four sources.
# ---------------------------------------------------------------------------


def test_deny_tools_union_across_four_sources() -> None:
    resolved = _resolve(
        ["--deny-tool", "a"],
        env={"GIT_LOOPY_DENY_TOOLS": "b"},
        project={"deny_tools": ["c"]},
        global_={"deny_tools": ["d"]},
    )
    assert resolved.run.deny_tools == frozenset({"a", "b", "c", "d"})


def test_deny_skills_union_across_four_sources() -> None:
    resolved = _resolve(
        ["--deny-skill", "a"],
        env={"GIT_LOOPY_DENY_SKILLS": "b"},
        project={"deny_skills": ["c"]},
        global_={"deny_skills": ["d"]},
    )
    assert resolved.run.deny_skills == frozenset({"a", "b", "c", "d"})


# ---------------------------------------------------------------------------
# Per-run-only knobs are NEVER read from a config file.
# ---------------------------------------------------------------------------


def test_per_run_only_knobs_ignore_config_tables() -> None:
    resolved = _resolve(
        env={},
        project={
            "max_iterations": 99,
            "verbosity": 3,
            "render_reasoning": False,
            "parallel": 5,
        },
        global_={"max_iterations": 42, "parallel": 8},
    )
    run = resolved.run
    assert run.max_iterations == 0  # from args default, not config
    assert run.verbosity == 0
    assert run.render_reasoning is True
    assert run.parallel == 1


def test_per_run_only_knobs_still_come_from_args() -> None:
    resolved = _resolve(
        ["3", "-vv", "--no-reasoning", "--parallel", "4"],
        project={"max_iterations": 99, "parallel": 8},
    )
    run = resolved.run
    assert run.max_iterations == 3
    assert run.verbosity == 2
    assert run.render_reasoning is False
    assert run.parallel == 4


# ---------------------------------------------------------------------------
# send_timeout_seconds: env > project > global > default.
# ---------------------------------------------------------------------------


def test_send_timeout_env_beats_config() -> None:
    resolved = _resolve(
        env={"GIT_LOOPY_SEND_TIMEOUT_SECONDS": "100"},
        project={"send_timeout_seconds": 200},
        global_={"send_timeout_seconds": 300},
    )
    assert resolved.run.send_timeout_seconds == 100.0


def test_send_timeout_project_beats_global() -> None:
    resolved = _resolve(
        project={"send_timeout_seconds": 200.0},
        global_={"send_timeout_seconds": 300.0},
    )
    assert resolved.run.send_timeout_seconds == 200.0


def test_send_timeout_global_only() -> None:
    resolved = _resolve(global_={"send_timeout_seconds": 300})
    assert resolved.run.send_timeout_seconds == 300.0


def test_send_timeout_invalid_env_falls_through_to_config() -> None:
    resolved = _resolve(
        env={"GIT_LOOPY_SEND_TIMEOUT_SECONDS": "not-a-number"},
        global_={"send_timeout_seconds": 300},
    )
    assert resolved.run.send_timeout_seconds == 300.0


def test_send_timeout_nonpositive_config_is_skipped() -> None:
    # A non-positive config value degrades to the next tier rather than
    # crashing RunConfig's ``> 0`` validation.
    resolved = _resolve(
        project={"send_timeout_seconds": 0},
        global_={"send_timeout_seconds": 300},
    )
    assert resolved.run.send_timeout_seconds == 300.0


# ---------------------------------------------------------------------------
# include_prs: env tri-state > project > global > None.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("0", False), ("no", False)],
)
def test_include_prs_env_tristate(raw: str, expected: bool) -> None:
    resolved = _resolve(env={"GIT_LOOPY_INCLUDE_PRS": raw})
    assert resolved.run.include_prs is expected


def test_include_prs_project_beats_global() -> None:
    resolved = _resolve(project={"include_prs": True}, global_={"include_prs": False})
    assert resolved.run.include_prs is True


def test_include_prs_global_only() -> None:
    resolved = _resolve(global_={"include_prs": False})
    assert resolved.run.include_prs is False


def test_include_prs_unset_is_none() -> None:
    assert _resolve().run.include_prs is None


# ---------------------------------------------------------------------------
# issue_source: env > project > global > "github"; invalid -> SystemExit.
# ---------------------------------------------------------------------------


def test_issue_source_project_beats_global() -> None:
    resolved = _resolve(project={"issue_source": "prds"}, global_={"issue_source": "github"})
    assert resolved.run.issue_source == "prds"


def test_issue_source_env_beats_config() -> None:
    resolved = _resolve(
        env={"GIT_LOOPY_ISSUE_SOURCE": "github"},
        project={"issue_source": "prds"},
    )
    assert resolved.run.issue_source == "github"


def test_issue_source_invalid_config_aborts() -> None:
    with pytest.raises(SystemExit):
        _resolve(global_={"issue_source": "gitlab"})


# ---------------------------------------------------------------------------
# otel_enabled: env signal > config; endpoint presence enables.
# ---------------------------------------------------------------------------


def test_otel_endpoint_env_enables() -> None:
    resolved = _resolve(env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"})
    assert resolved.run.otel_enabled is True


def test_otel_enabled_env_truthy() -> None:
    resolved = _resolve(env={"GIT_LOOPY_OTEL_ENABLED": "1"})
    assert resolved.run.otel_enabled is True


def test_otel_env_falsy_beats_config_true() -> None:
    # An explicit env signal (even falsy) wins over a lower config tier.
    resolved = _resolve(
        env={"GIT_LOOPY_OTEL_ENABLED": "0"},
        project={"otel_enabled": True},
    )
    assert resolved.run.otel_enabled is False


def test_otel_project_config_enables() -> None:
    resolved = _resolve(project={"otel_enabled": True})
    assert resolved.run.otel_enabled is True


def test_otel_global_config_enables() -> None:
    resolved = _resolve(global_={"otel_enabled": True})
    assert resolved.run.otel_enabled is True


def test_otel_unset_is_false() -> None:
    assert _resolve().run.otel_enabled is False


# ---------------------------------------------------------------------------
# max_nmt_strikes: env > project > global > 3; invalid -> SystemExit.
# ---------------------------------------------------------------------------


def test_max_nmt_strikes_project_beats_global() -> None:
    resolved = _resolve(project={"max_nmt_strikes": 5}, global_={"max_nmt_strikes": 9})
    assert resolved.run.max_nmt_strikes == 5


def test_max_nmt_strikes_env_beats_config() -> None:
    resolved = _resolve(
        env={"GIT_LOOPY_MAX_NMT_STRIKES": "2"},
        project={"max_nmt_strikes": 5},
    )
    assert resolved.run.max_nmt_strikes == 2


def test_max_nmt_strikes_subone_config_aborts() -> None:
    with pytest.raises(SystemExit):
        _resolve(project={"max_nmt_strikes": 0})


# ---------------------------------------------------------------------------
# model/effort policy sits at the BOTTOM of the chain (gate still applies).
# ---------------------------------------------------------------------------


def test_model_effort_pure_default() -> None:
    run = _resolve().run
    assert (run.model, run.reasoning_effort) == ("claude-opus-5", "xhigh")


def test_config_model_from_project() -> None:
    run = _resolve(project={"model": "gpt-5.5"}).run
    # A config-supplied model is NOT a pure default, so no default effort is
    # injected; effort stays None unless separately configured.
    assert run.model == "gpt-5.5"
    assert run.reasoning_effort is None


def test_config_model_overridden_by_env() -> None:
    run = _resolve(
        env={"GIT_LOOPY_MODEL": "gpt-5.5"},
        project={"model": "claude-sonnet-4.6"},
    ).run
    assert run.model == "gpt-5.5"


def test_capability_gate_forces_none_for_incapable_model(capsys) -> None:
    # claude-sonnet-4.5 supports no reasoning effort; an explicitly requested
    # effort is gated to None (the CLI hard-rejects it otherwise).
    run = _resolve(
        project={"model": "claude-sonnet-4.5", "reasoning_effort": "high"}
    ).run
    assert run.model == "claude-sonnet-4.5"
    assert run.reasoning_effort is None


def test_capability_gate_drops_unsupported_effort_for_known_model() -> None:
    # Locked (#145): a *known* model asked for an effort it does not document
    # now drops the effort to None (was pass-through), so both this run-wide
    # resolver and the init seed gate identically through the one shared gate.
    # gpt-5-mini documents {low, medium, high} but not ``max``.
    messages: list[str] = []
    run = _resolve(
        project={"model": "gpt-5-mini", "reasoning_effort": "max"},
        warn=messages.append,
    ).run
    assert run.model == "gpt-5-mini"
    assert run.reasoning_effort is None
    assert any("gpt-5-mini" in m for m in messages)


def test_invalid_config_effort_aborts() -> None:
    with pytest.raises(SystemExit):
        _resolve(project={"reasoning_effort": "turbo"})


@pytest.mark.parametrize(
    ("project", "global_", "expected"),
    [
        # Efforts the default model (claude-opus-4.8) accepts, so this pins the
        # case-insensitive *normalisation* of a config-file effort rather than
        # the #145 capability gate (which would drop an unsupported effort).
        ({"reasoning_effort": "XHigh"}, {}, "xhigh"),
        ({}, {"reasoning_effort": "MeDiUm"}, "medium"),
    ],
)
def test_project_and_global_config_accept_current_efforts_case_insensitively(
    project: dict[str, object],
    global_: dict[str, object],
    expected: str,
) -> None:
    run = _resolve(
        project=project,
        global_=global_,
        warn=lambda _message: None,
    ).run

    assert run.reasoning_effort == expected


def test_model_flag_overrides_env_and_config() -> None:
    # The real ``--model`` flag (#54) sits at the top of the chain: it wins
    # over env + project + global config.
    resolved = _resolve(
        ["--model", "gpt-5.4"],
        env={"GIT_LOOPY_MODEL": "gpt-5.5"},
        project={"model": "claude-sonnet-4.6"},
        global_={"model": "claude-opus-4.6"},
    )
    assert resolved.run.model == "gpt-5.4"


def test_reasoning_effort_flag_overrides_env_and_config() -> None:
    # ``--reasoning-effort`` wins over env + config for the effort axis.
    resolved = _resolve(
        ["--model", "claude-opus-4.8", "--reasoning-effort", "low"],
        env={"GIT_LOOPY_REASONING_EFFORT": "high"},
        project={"reasoning_effort": "medium"},
        global_={"reasoning_effort": "xhigh"},
    )
    assert resolved.run.reasoning_effort == "low"


# ---------------------------------------------------------------------------
# Injected warn is threaded into the model/effort policy.
# ---------------------------------------------------------------------------


def test_injected_warn_receives_capability_gate_message() -> None:
    messages: list[str] = []
    _resolve(
        project={"model": "claude-sonnet-4.5", "reasoning_effort": "high"},
        warn=messages.append,
    )
    assert any("claude-sonnet-4.5" in m for m in messages)


# ---------------------------------------------------------------------------
# Type-mismatched config values surface a clear SettingsError.
# ---------------------------------------------------------------------------


def test_type_mismatched_config_raises_settings_error() -> None:
    from git_loopy.settings import SettingsError

    with pytest.raises(SettingsError):
        _resolve(project={"max_nmt_strikes": "seven"})


# ---------------------------------------------------------------------------
# main() integration: a persisted config.toml flows loader -> resolver -> run.
# ---------------------------------------------------------------------------


def _fake_loop_run(monkeypatch, captured: list) -> None:
    async def fake_run(cfg, *, driver=None, **_extra) -> int:
        captured.append(cfg)
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


def test_main_reads_project_config_into_run(monkeypatch, tmp_path) -> None:
    # A value set ONLY in the project config.toml must reach the RunConfig the
    # loop receives (end-to-end: load_configs -> resolve_config -> loop.run).
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda intent: False)
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "config.toml").write_text(
        'max_nmt_strikes = 9\nissue_source = "prds"\n', encoding="utf-8"
    )
    captured: list = []
    _fake_loop_run(monkeypatch, captured)

    rc = cli.main(["--no-interactive"])

    assert rc == 0
    assert len(captured) == 1
    assert captured[0].max_nmt_strikes == 9
    assert captured[0].issue_source == "prds"


def test_main_reports_malformed_config_and_exits_one(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "config.toml").write_text(
        'issue_source = "prds\n', encoding="utf-8"  # unterminated string
    )

    rc = cli.main(["--no-interactive"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "not valid TOML" in err
    assert "git-loopy: error:" in err


# ---------------------------------------------------------------------------
# [routing] -> RunConfig.routing (issue #146). Merge project-over-global per
# task-type key; suppress on an explicit --model / --reasoning-effort (flag or
# env); non-fatal off-roster advisory; back-compat empty map.
# ---------------------------------------------------------------------------


_ROUTING_GLOBAL = {
    "routing": {"planning": {"model": "claude-opus-4.8", "effort": "max"}}
}


def test_resolve_no_routing_yields_empty_map() -> None:
    """Back-compat: a config with no [routing] resolves to an empty routing map."""
    assert dict(_resolve().run.routing) == {}


def test_resolve_global_routing_populates_map() -> None:
    run = _resolve(global_=_ROUTING_GLOBAL).run
    assert dict(run.routing) == {"planning": ("claude-opus-4.8", "max")}


def test_resolve_routing_merges_project_over_global_per_task_type() -> None:
    global_ = {
        "routing": {
            "planning": {"model": "claude-opus-4.8", "effort": "max"},
            "docs": {"model": "gpt-5-mini", "effort": "medium"},
        }
    }
    project = {
        "routing": {
            "docs": {"model": "gpt-5-mini", "effort": "low"},  # override whole pair
            "test": {"model": "claude-sonnet-5", "effort": "medium"},  # project-only
        }
    }
    run = _resolve(project=project, global_=global_).run
    assert dict(run.routing) == {
        "planning": ("claude-opus-4.8", "max"),  # global-only survives
        "docs": ("gpt-5-mini", "low"),  # project overrides the whole pair
        "test": ("claude-sonnet-5", "medium"),  # project-only added
    }


@pytest.mark.parametrize(
    "argv", [["--model", "gpt-5-mini"], ["--reasoning-effort", "low"]]
)
def test_resolve_explicit_flag_suppresses_routing(argv: list[str]) -> None:
    run = _resolve(argv, global_=_ROUTING_GLOBAL).run
    assert dict(run.routing) == {}


@pytest.mark.parametrize(
    "env",
    [{"GIT_LOOPY_MODEL": "gpt-5-mini"}, {"GIT_LOOPY_REASONING_EFFORT": "low"}],
)
def test_resolve_explicit_env_suppresses_routing(env: dict[str, str]) -> None:
    run = _resolve(env=env, global_=_ROUTING_GLOBAL).run
    assert dict(run.routing) == {}


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--model", "gpt-5-mini"], {}),
        ([], {"GIT_LOOPY_REASONING_EFFORT": "low"}),
    ],
)
def test_suppressed_routing_still_refuses_a_persisted_unknown_task_type(
    argv: list[str], env: dict[str, str]
) -> None:
    """Suppression disables selection, not validation of persisted Task types."""
    from git_loopy.config import TaskTypeError

    with pytest.raises(TaskTypeError, match="custom"):
        _resolve(
            argv,
            env=env,
            global_={
                "routing": {
                    "custom": {"model": "gpt-5-mini", "effort": "medium"}
                }
            },
        )


def test_suppressed_routing_refuses_an_unknown_key_before_parsing_its_route() -> None:
    """A suppressed map validates Task types without loading inactive route values."""
    from git_loopy.config import TaskTypeError

    with pytest.raises(TaskTypeError, match="custom"):
        _resolve(
            ["--model", "gpt-5-mini"],
            global_={"routing": {"custom": {"model": 42, "effort": "medium"}}},
        )


def test_resolve_config_file_model_does_not_suppress_routing() -> None:
    """A `model` key in a config *file* is the same tier as routing, not an override."""
    run = _resolve(global_={"model": "gpt-5-mini", **_ROUTING_GLOBAL}).run
    assert dict(run.routing) == {"planning": ("claude-opus-4.8", "max")}


def test_resolve_off_roster_routing_model_warns_but_resolves() -> None:
    warnings: list[str] = []
    run = _resolve(
        global_={"routing": {"planning": {"model": "made-up-model", "effort": "max"}}},
        warn=warnings.append,
    ).run
    assert dict(run.routing) == {"planning": ("made-up-model", "max")}
    assert any("made-up-model" in w for w in warnings)


def test_resolve_suppressed_routing_skips_off_roster_advisory() -> None:
    warnings: list[str] = []
    _resolve(
        ["--model", "gpt-5-mini"],
        global_={"routing": {"planning": {"model": "made-up-model", "effort": "max"}}},
        warn=warnings.append,
    )
    assert not any("made-up-model" in w for w in warnings)


def test_resolve_malformed_routing_raises_loudly() -> None:
    from git_loopy import settings

    with pytest.raises(settings.SettingsError):
        _resolve(global_={"routing": {"planning": {"model": "claude-opus-4.8"}}})


# ---------------------------------------------------------------------------
# Roster-drift regression: every model the kit claims to support must resolve
# warning-free through BOTH config-file tiers. The off-roster advisory is a
# typo-catch, so a model that has drifted out of the kit's roster while still
# being live in the Copilot catalog (e.g. `claude-opus-5`) fires it spuriously
# on every startup.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["claude-opus-5", "gemini-3.6-flash"])
def test_resolve_live_catalog_models_are_on_the_roster(model: str) -> None:
    """Models live in the Copilot catalog must not trip the off-roster advisory.

    A hand-maintained mirror of an external catalog can only be pinned against
    hard-coded ids: an assertion derived from
    :data:`~git_loopy.config.SUPPORTED_MODELS` is self-referential and stays
    green while the roster drifts. Both ids shipped in the Copilot catalog
    *after* the roster was last synced, so a config naming them warned on every
    startup even though the run itself worked.
    """
    assert model in SUPPORTED_MODELS
    effort = next(e for e in REASONING_EFFORT_ORDER if e in MODEL_REASONING_EFFORTS[model])
    warnings: list[str] = []
    _resolve(
        global_={
            "model": model,
            "routing": {"planning": {"model": model, "effort": effort}},
        },
        warn=warnings.append,
    )
    assert not any(model in w for w in warnings)



# ---------------------------------------------------------------------------
# Measured routing (#361, ADR-0028): one machine-written tier between global
# Config and the built-in default. It fills gaps and never wins an argument.
#
# Synthetic model identifiers throughout — the merge is a precedence question,
# not a roster question, so a vendor catalogue change must not invalidate these.
# The off-roster advisory is silenced by an injected `warn` where it would
# otherwise be noise.
# ---------------------------------------------------------------------------

_MEASURED = {"docs": ("synthetic-cheap-1", "low")}


def test_measured_entry_supplies_a_pair_where_the_operator_is_silent() -> None:
    """A Task type nobody configured gets the measured value."""
    run = _resolve(measured=_MEASURED, warn=lambda _m: None).run
    assert dict(run.routing) == {"docs": ("synthetic-cheap-1", "low")}


@pytest.mark.parametrize("scope", ["project", "global_"])
def test_hand_written_routing_beats_the_measured_entry(scope: str) -> None:
    """A hand-written `[routing]` entry wins forever, with no flag to make it do so."""
    hand = {"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}}
    run = _resolve(measured=_MEASURED, warn=lambda _m: None, **{scope: hand}).run
    assert dict(run.routing) == {"docs": ("synthetic-fancy-9", "high")}


def test_measured_fills_only_the_task_types_config_leaves_alone() -> None:
    """Measured routing fills gaps; it never overrules, and never removes."""
    run = _resolve(
        measured={
            "docs": ("synthetic-cheap-1", "low"),
            "test": ("synthetic-cheap-2", "medium"),
        },
        global_={"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
        project={"routing": {"planning": {"model": "synthetic-fancy-9", "effort": "max"}}},
        warn=lambda _m: None,
    ).run
    assert dict(run.routing) == {
        "docs": ("synthetic-fancy-9", "high"),  # global Config beats measured
        "test": ("synthetic-cheap-2", "medium"),  # measured fills the gap
        "planning": ("synthetic-fancy-9", "max"),  # project-only survives
    }


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--model", "gpt-5-mini"], {}),
        (["--reasoning-effort", "low"], {}),
        ([], {"GIT_LOOPY_MODEL": "gpt-5-mini"}),
        ([], {"GIT_LOOPY_REASONING_EFFORT": "low"}),
    ],
)
def test_explicit_override_suppresses_the_measured_tier_too(
    argv: list[str], env: dict[str, str]
) -> None:
    """The existing run-wide suppression extends unchanged — measured included."""
    run = _resolve(argv, env=env, measured=_MEASURED, warn=lambda _m: None).run
    assert dict(run.routing) == {}
    assert run.routing_suppressed is True


def test_absent_measured_tier_changes_nothing() -> None:
    """An absent artifact is the ordinary case and warns about nothing."""
    warnings: list[str] = []
    run = _resolve(global_=_ROUTING_GLOBAL, warn=warnings.append).run
    assert dict(run.routing) == {"planning": ("claude-opus-4.8", "max")}
    assert warnings == []


def test_measured_entry_beats_the_builtin_default_for_a_labelled_issue() -> None:
    """The tier is only real if an **Iteration** actually runs on it.

    The two halves of the chain meet here: ``resolve_config`` merges the tiers
    into ``RunConfig.routing``, and ``resolve_iteration_model`` resolves one
    issue's **Task type** against that map. A measured entry must beat the
    run-wide built-in default, and an unmeasured Task type must still fall
    through to it.
    """
    from git_loopy.config import resolve_iteration_model

    run = _resolve(measured=_MEASURED, warn=lambda _m: None).run

    routed = resolve_iteration_model(run, ["task-type:docs"])
    defaulted = resolve_iteration_model(run, ["ready-for-agent"])

    assert (routed.model, routed.reasoning_effort) == ("synthetic-cheap-1", "low")
    assert (defaulted.model, defaulted.reasoning_effort) == (
        run.model,
        run.reasoning_effort,
    )


# ---------------------------------------------------------------------------
# Routing provenance (#364): the resolver names the tier that supplied each
# **Routed pair**, so "why is this model set?" has an answer once a tier exists
# that no operator typed.
# ---------------------------------------------------------------------------


def test_routing_provenance_names_the_tier_each_entry_came_from() -> None:
    """Each task-type key is attributed to the tier whose value is in force."""
    resolved = _resolve(
        measured={
            "docs": ("synthetic-cheap-1", "low"),
            "test": ("synthetic-cheap-2", "medium"),
        },
        global_={"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "high"}}},
        project={
            "routing": {"planning": {"model": "synthetic-fancy-9", "effort": "max"}}
        },
        warn=lambda _m: None,
    )
    assert dict(resolved.routing_provenance) == {
        "docs": cli.RoutingTier.GLOBAL,
        "test": cli.RoutingTier.MEASURED,
        "planning": cli.RoutingTier.PROJECT,
    }


def test_a_provisional_measured_entry_routes_but_is_attributed_separately() -> None:
    """An unmeasured pair in force supplies routing and never reads as measured.

    **Demotion** (ADR-0030) steps up into a rung nobody trialled, so the tier can
    hold a pair that is genuinely in force and genuinely unmeasured. It routes —
    otherwise the demotion would be a work stoppage — but the tier reported for it
    is its own, so no reader mistakes it for evidence (#376).
    """
    resolved = _resolve(
        measured={
            "docs": ("synthetic-cheap-1", "low"),
            "test": ("synthetic-cheap-2", "medium"),
        },
        measured_provisional=frozenset({"test"}),
        warn=lambda _m: None,
    )
    assert dict(resolved.run.routing) == {
        "docs": ("synthetic-cheap-1", "low"),
        "test": ("synthetic-cheap-2", "medium"),
    }
    assert dict(resolved.routing_provenance) == {
        "docs": cli.RoutingTier.MEASURED,
        "test": cli.RoutingTier.PROVISIONAL,
    }


def test_a_hand_written_entry_still_beats_a_provisional_one() -> None:
    """The provisional state changes the *label*, never the precedence chain."""
    resolved = _resolve(
        measured={"docs": ("synthetic-cheap-1", "low")},
        measured_provisional=frozenset({"docs"}),
        project={"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "max"}}},
        warn=lambda _m: None,
    )
    assert dict(resolved.run.routing) == {"docs": ("synthetic-fancy-9", "max")}
    assert dict(resolved.routing_provenance) == {"docs": cli.RoutingTier.PROJECT}


@pytest.mark.parametrize(
    ("argv", "env", "tier"),
    [
        (["--model", "gpt-5-mini"], {}, cli.RoutingTier.CLI_FLAG),
        (["--reasoning-effort", "low"], {}, cli.RoutingTier.CLI_FLAG),
        ([], {"GIT_LOOPY_MODEL": "gpt-5-mini"}, cli.RoutingTier.ENVIRONMENT),
        ([], {"GIT_LOOPY_REASONING_EFFORT": "low"}, cli.RoutingTier.ENVIRONMENT),
    ],
)
def test_run_wide_suppression_is_reported_as_a_tier_not_as_an_empty_map(
    argv: list[str], env: dict[str, str], tier: "cli.RoutingTier"
) -> None:
    """A suppressed report must say *suppressed*, not name a dead tier.

    With routing off run-wide there is no winning tier to name, so the
    provenance map is empty and the tier that turned it off is named instead.
    """
    resolved = _resolve(argv, env=env, measured=_MEASURED, warn=lambda _m: None)
    assert dict(resolved.routing_provenance) == {}
    assert resolved.routing_suppressed_by is tier


def test_routing_provenance_covers_exactly_the_effective_routing_map() -> None:
    """Provenance is derived from the merge, so it cannot name a different set.

    Every key the loop routes on has a tier, and no key that is not routed has
    one — the property that keeps the report and the resolver from drifting.
    """
    resolved = _resolve(
        measured={"docs": ("synthetic-cheap-1", "low")},
        global_={"routing": {"test": {"model": "synthetic-fancy-9", "effort": "high"}}},
        project={"routing": {"docs": {"model": "synthetic-fancy-9", "effort": "max"}}},
        warn=lambda _m: None,
    )
    assert set(resolved.routing_provenance) == set(resolved.run.routing)
    assert resolved.routing_suppressed_by is None


def test_the_suppression_predicate_and_the_reported_tier_are_one_decision() -> None:
    """The boolean the resolver acts on is the tier the report names.

    Two independent copies of "is routing suppressed?" is exactly the drift
    #364 exists to remove, so the predicate is derived from the tier.
    """
    for argv, env in (
        ([], {}),
        (["--model", "gpt-5-mini"], {}),
        (["--reasoning-effort", "low"], {}),
        ([], {"GIT_LOOPY_MODEL": "gpt-5-mini"}),
        ([], {"GIT_LOOPY_MODEL": "   "}),
        ([], {"GIT_LOOPY_REASONING_EFFORT": "low"}),
    ):
        args = _args(argv)
        assert cli._explicit_model_or_effort_override(args, env) is (
            cli.routing_suppressed_by(args, env) is not None
        )


# ---------------------------------------------------------------------------
# The Task-type classifier's own knob (#377, ADR-0029)
# ---------------------------------------------------------------------------


def test_the_classifier_pair_is_absent_by_default_rather_than_defaulted_here() -> None:
    """No knob set is the ordinary case, and it is an *absence*, not a default.

    A knob defaulting to some model id at this layer would be exactly the hidden
    prior ADR-0029 refuses. The default belongs to the **live roster** — the
    cheapest rung of the price staircase — and only
    :func:`~git_loopy.task_type_classifier.resolve_classifier_pair` knows it.
    """
    resolved = _resolve()

    assert resolved.run.classifier_model is None
    assert resolved.run.classifier_effort is None


def test_the_classifier_knob_resolves_env_over_project_over_global() -> None:
    """The kit's own precedence chain, unchanged, applied to one more knob."""
    project = {"classifier_model": "project-model", "classifier_effort": "low"}
    global_ = {"classifier_model": "global-model", "classifier_effort": "high"}

    from_project = _resolve(project=project, global_=global_)
    assert from_project.run.classifier_model == "project-model"
    assert from_project.run.classifier_effort == "low"

    from_global = _resolve(global_=global_)
    assert from_global.run.classifier_model == "global-model"
    assert from_global.run.classifier_effort == "high"

    from_env = _resolve(
        env={
            "GIT_LOOPY_CLASSIFIER_MODEL": "env-model",
            "GIT_LOOPY_CLASSIFIER_REASONING_EFFORT": "medium",
        },
        project=project,
        global_=global_,
    )
    assert from_env.run.classifier_model == "env-model"
    assert from_env.run.classifier_effort == "medium"


def test_the_classifier_knob_is_independent_of_the_run_wide_pair() -> None:
    """Setting the run-wide pair must never move the classifier's (ADR-0029).

    The knob exists precisely so the run-wide default cannot determine a **Task
    type** — and so a **Routed pair** — for every issue. Sharing a source would
    reinstate that, silently.
    """
    resolved = _resolve(
        env={"GIT_LOOPY_MODEL": "gpt-5.5", "GIT_LOOPY_REASONING_EFFORT": "high"}
    )

    assert resolved.run.model == "gpt-5.5"
    assert resolved.run.classifier_model is None
    assert resolved.run.classifier_effort is None


def test_demotion_threshold_resolves_through_the_persisted_tiers() -> None:
    """Project **Config** beats global, and both beat the built-in default.

    ADR-0030 leaves the number a free parameter — *"unlike the Strike limit it is
    not bounded by anything structural"* — so an operator has to be able to move
    it, through the same tier chain every other persisted knob uses rather than a
    mechanism of its own.
    """
    resolved = _resolve(
        project={"demotion_threshold": 5}, global_={"demotion_threshold": 9}
    )

    assert resolved.run.demotion_threshold == 5


def test_demotion_threshold_falls_back_to_the_global_tier() -> None:
    assert _resolve(global_={"demotion_threshold": 9}).run.demotion_threshold == 9


def test_a_sub_one_demotion_threshold_aborts_rather_than_degrading() -> None:
    """``0`` would demote every pair that ever failed once; ``-1`` is nonsense.

    Refused at resolution with the scope named, exactly as ``max_nmt_strikes``
    is: an unattended Run must not quietly reinterpret a knob that rewrites a
    committed file.
    """
    with pytest.raises(SystemExit, match="demotion_threshold"):
        _resolve(project={"demotion_threshold": 0})


# ---------------------------------------------------------------------------
# The **Escalation rung** (#408): config-file-only, default-on, suppressed by an
# explicit model pin — ``[routing]``'s precedence discipline, exactly.
# ---------------------------------------------------------------------------


def test_escalation_is_on_by_default_at_the_built_in_rung() -> None:
    """A Run that says nothing about escalation still escalates.

    Default-on is forced by the locked routing table (ADR-0035): it adopted a
    two-rung-cheaper ``implementation`` pair *on the strength of this backstop
    existing*. A backstop that shipped off would leave that table leaning on
    mechanism which, for every operator who did not opt in, is not there.
    """
    assert _resolve().run.escalation_rung == cli._DEFAULT_ESCALATION_RUNG


def test_the_built_in_rung_sits_one_rung_above_the_default_pair() -> None:
    """ADR-0036's whole point: the default reserves the ceiling for this.

    A rung equal to the **Default pair** would make escalation a no-op for every
    unlabelled issue — which is most of them — so the two constants are pinned
    against each other rather than each against a literal.
    """
    resolved = _resolve().run

    assert resolved.escalation_rung is not None
    assert resolved.escalation_rung[0] == resolved.model
    assert resolved.escalation_rung[1] != resolved.reasoning_effort


def test_a_config_file_names_the_rung() -> None:
    assert _resolve(
        project={"escalation": {"model": "gpt-5.6-sol", "effort": "high"}}
    ).run.escalation_rung == ("gpt-5.6-sol", "high")


def test_project_scope_beats_global_scope_for_the_rung() -> None:
    assert _resolve(
        project={"escalation": {"model": "gpt-5.6-sol", "effort": "high"}},
        global_={"escalation": {"model": "claude-opus-4.8", "effort": "max"}},
    ).run.escalation_rung == ("gpt-5.6-sol", "high")


def test_escalation_can_be_turned_off() -> None:
    """``enabled = false`` is the whole opt-out, and it reads as no rung.

    Absent rather than "present but ignored", so no call site can escalate a Run
    that disabled escalation by consulting the pair without the switch.
    """
    assert _resolve(project={"escalation": {"enabled": False}}).run.escalation_rung is None


def test_disabling_in_project_scope_overrides_a_global_rung() -> None:
    assert (
        _resolve(
            project={"escalation": {"enabled": False}},
            global_={"escalation": {"model": "gpt-5.6-sol", "effort": "high"}},
        ).run.escalation_rung
        is None
    )


@pytest.mark.parametrize(
    "argv, env",
    [
        (["--model", "claude-opus-4.8"], {}),
        (["--reasoning-effort", "low"], {}),
        ([], {"GIT_LOOPY_MODEL": "claude-opus-4.8"}),
        ([], {"GIT_LOOPY_REASONING_EFFORT": "low"}),
    ],
)
def test_an_explicit_pin_suppresses_escalation_as_well_as_routing(
    argv: list[str], env: dict[str, str]
) -> None:
    """Pinning a model means what it says.

    An operator who named a model has taken the model choice away from the
    runner; escalating past it would override an explicit human instruction —
    the same reason the pin already silences **Routing**.
    """
    resolved = _resolve(
        argv,
        env=env,
        project={"escalation": {"model": "gpt-5.6-sol", "effort": "high"}},
    )

    assert resolved.run.escalation_rung is None
    assert resolved.run.routing_suppressed is True


def test_the_rung_is_config_file_only_and_reads_no_environment_variable() -> None:
    """No env tier, on purpose — that is the spine §14 fixed for routing.

    A ``GIT_LOOPY_ESCALATION_*`` would be a *second* way for the environment to
    speak about models, beside the one that already suppresses the machinery
    outright, and the two would disagree about the same Run.
    """
    resolved = _resolve(
        env={
            "GIT_LOOPY_ESCALATION_MODEL": "gpt-5.6-sol",
            "GIT_LOOPY_ESCALATION_EFFORT": "high",
        }
    )

    assert resolved.run.escalation_rung == cli._DEFAULT_ESCALATION_RUNG
