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
    warn=cli._warn,
):
    return cli.resolve_config(
        _args(argv),
        env or {},
        project=project or {},
        global_=global_ or {},
        warn=warn,
    )


# ---------------------------------------------------------------------------
# Defaults: empty everything -> built-in defaults.
# ---------------------------------------------------------------------------


def test_resolve_all_empty_yields_builtin_defaults() -> None:
    resolved = _resolve()
    run = resolved.run
    assert isinstance(run, RunConfig)
    assert run.model == "claude-opus-4.8"
    assert run.reasoning_effort == "max"
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
    assert (run.model, run.reasoning_effort) == ("claude-opus-4.8", "max")


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
    async def fake_run(cfg, *, driver=None) -> int:
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
