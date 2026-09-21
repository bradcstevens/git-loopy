"""Explicit update choices, observed through saved Config and external ports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_loopy import dynamic_route, model_listing, settings, updatecmd
from tests.test_updatecmd import _refreshed_catalog


def _update(tmp_path, *, env=None, output=None, **kwargs):
    return updatecmd.run_update(
        env=dict(os.environ) if env is None else env,
        project_root=tmp_path,
        release_version_reader=lambda: "1.2.4",
        catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
        helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        output_fn=([] if output is None else output).append,
        **kwargs,
    )


def _listing(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fetch():
        calls.append("listing")
        return [
            SimpleNamespace(
                id="gpt-5.6-terra",
                name="gpt-5.6-terra",
                policy=SimpleNamespace(state="enabled", terms=""),
                billing=SimpleNamespace(
                    multiplier=1.0,
                    token_prices=SimpleNamespace(
                        max_prompt_tokens=400_000,
                        long_context=SimpleNamespace(max_prompt_tokens=800_000),
                    ),
                ),
                supported_reasoning_efforts=["high"],
                default_reasoning_effort="high",
            )
        ]

    monkeypatch.setattr(model_listing, "fetch_live_models", fetch)
    return calls


def _saved_routing_config(
    tmp_path,
    monkeypatch,
    *,
    entrypoint,
    max_iterations=1,
    max_nmt_strikes=3,
    routing_credit_allowance="2.5",
):
    """Resolve a Run from real saved authorization, not a constructed RunConfig."""
    from git_loopy import cli
    from tests.test_init_routing import _first_setup_for_run

    _listing(monkeypatch)
    path = settings.project_config_path(tmp_path)
    if entrypoint == "init":
        assert not path.exists()
        assert _first_setup_for_run(
            tmp_path, monkeypatch, choice="migrate", saved_route=False,
            routing_credit_allowance=routing_credit_allowance,
        ) == 0
    elif entrypoint == "update":
        settings.write_config_atomic(path, {
            **_authorized_values(),
            "routing_credit_allowance": str(routing_credit_allowance),
            "route_associations": {
                "aa-opus": "claude-opus-5@high", "aa-terra": "gpt-5.6-terra@high",
            },
        })
        assert _update(tmp_path, routing_choice="migrate") == 0
    else:
        assert entrypoint == "recorded"
        assert path.exists()
    assert settings.load_config_table(path)["route_policy"] == "dynamic"
    assert "routing" not in settings.load_config_table(path)
    tables = settings.load_configs(tmp_path, os.environ)
    return cli.resolve_config(
        cli.build_parser().parse_args([str(max_iterations)]),
        {"GIT_LOOPY_MAX_NMT_STRIKES": str(max_nmt_strikes)},
        project=tables.project,
        global_=tables.global_,
        measured=tables.measured,
    ).run


def test_keep_preserves_authored_routes_and_records_strict_static_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    values = {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "context_tier": "long_context",
        "routing": {"implementation": {"model": "gpt-5.6-terra", "effort": "high"}},
        "enabled_skills": ["tdd"],
        "max_nmt_strikes": 4,
    }
    settings.write_config_atomic(path, values)
    original = path.read_bytes()
    calls = _listing(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    output: list[str] = []

    assert _update(tmp_path, routing_choice="keep", output=output) == 0

    assert settings.load_config_table(path) == {**values, "route_policy": "static"}
    assert path.with_suffix(".toml.bak").read_bytes() == original
    assert calls == ["listing"]
    text = "\n".join(output)
    assert "strict" in text
    assert "implicit" in text and "escalation" in text
    assert "inherited" in text and "tier" in text
    assert "Static" in text and "preserved" in text


def _evidence(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fetch(method, url, headers):
        assert method == "GET"
        assert url == dynamic_route.ARTIFICIAL_ANALYSIS_MODELS_URL
        calls.append("evidence")
        return json.dumps(
            {
                "data": [{
                    "id": "aa-terra",
                    "name": "aa-terra",
                    "slug": "aa-terra",
                    "evaluations": {"artificial_analysis_intelligence_index": 40},
                    "median_output_tokens_per_second": 200,
                }],
                "prompt_options": {"parallel_queries": 1},
            }
        ).encode()

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", fetch)
    return calls


def test_guided_migration_collects_explicit_bounds_and_keeps_static_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    values = {
        "routing": {"docs": {"model": "gpt-5.6-terra", "effort": "high"}},
        "route_associations": {"aa-terra": "gpt-5.6-terra@high"},
    }
    settings.write_config_atomic(path, values)
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "private-aa-key")
    answers = iter(["migrate", "30", "2.5", "2"])
    prompts: list[str] = []
    output: list[str] = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    assert _update(
        tmp_path, routing_choice="ask", input_fn=answer, output=output
    ) == 0

    assert settings.load_config_table(path) == {
        **values,
        "route_policy": "dynamic",
        "routing_deadline_seconds": 30.0,
        "routing_credit_allowance": "2.5",
        "selector_concurrency": 2,
    }
    assert len(prompts) == 4
    assert listings == ["listing"] and evidence == ["evidence"]
    text = "\n".join(output)
    assert "uncovered" in text
    assert "post-paid" in text and "overshoot" in text
    assert "No Route selector" in text
    assert "private-aa-key" not in text + path.read_text()


def test_unattended_cli_refuses_an_outstanding_migration_choice_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from git_loopy import cli

    path = settings.project_config_path(tmp_path)
    path.parent.mkdir()
    path.write_text(
        '# deliberately kept\n[routing.docs]\nmodel = "gpt-5.6-terra"\neffort = "high"\n'
    )
    original = path.read_bytes()
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("unattended update prompted")
    )
    calls = _listing(monkeypatch)

    assert cli.main(["update", "--routing", "--project"]) == 1

    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    assert calls == []
    text = capsys.readouterr().out
    assert "--routing keep" in text and "--routing migrate" in text
    assert "Config unchanged" in text


def test_migration_dry_run_is_an_offline_plan_not_a_readiness_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {"model": "gpt-5.6-terra"})
    original = path.read_bytes()
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    output: list[str] = []

    assert _update(
        tmp_path,
        routing_choice="migrate",
        dry_run=True,
        input_fn=lambda _prompt: pytest.fail("dry-run asked for authorization"),
        output=output,
    ) == 0

    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    assert listings == [] and evidence == []
    text = "\n".join(output)
    assert "Planned route_policy = dynamic" in text
    assert "readiness not checked" in text


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("routing_deadline_seconds", "nan"),
        ("routing_deadline_seconds", "inf"),
        ("routing_deadline_seconds", "0"),
        ("routing_credit_allowance", "NaN"),
        ("routing_credit_allowance", "-1"),
        ("selector_concurrency", "1.5"),
        ("selector_concurrency", "0"),
    ],
)
def test_invalid_migration_authorization_returns_a_diagnostic_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key, value
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        "routing_deadline_seconds": 30,
        "routing_credit_allowance": "2.5",
        "selector_concurrency": 1,
        "route_associations": {"aa-terra": "gpt-5.6-terra@high"},
        key: value,
    })
    original = path.read_bytes()
    listings = _listing(monkeypatch)
    output: list[str] = []

    assert _update(tmp_path, routing_choice="migrate", output=output) == 1

    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    assert listings == []
    assert key in "\n".join(output)


def test_keep_checks_the_measured_routes_it_retains_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_config_cmd import _write_measured

    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(
        path, {"model": "gpt-5.6-terra", "reasoning_effort": "high"}
    )
    _write_measured(tmp_path, docs=("unavailable-measured-model", "high"))
    original = path.read_bytes()
    _listing(monkeypatch)
    output: list[str] = []

    assert _update(tmp_path, routing_choice="keep", output=output) == 1

    assert path.read_bytes() == original
    assert "unavailable-measured-model" in "\n".join(output)
    assert not path.with_suffix(".toml.bak").exists()


def _authorized_values():
    return {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "routing_deadline_seconds": 30,
        "routing_credit_allowance": "2.5",
        "selector_concurrency": 1,
        "route_associations": {"aa-terra": "gpt-5.6-terra@high"},
    }


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        ("access", dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV),
        ("limit", "routing_credit_allowance"),
        ("associations", "[route_associations]"),
        ("allowance", "quota_exhausted"),
        ("source", "source_unavailable"),
        ("candidates", "no_runnable_candidate"),
        ("static", "unavailable-static-model"),
    ],
)
def test_migration_refusal_preserves_config_despite_temporary_static_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure, diagnostic
) -> None:
    path = settings.project_config_path(tmp_path)
    values = _authorized_values()
    env = {
        **os.environ,
        dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV: "private-aa-key",
        "GIT_LOOPY_ROUTE_POLICY": "static",
        "GIT_LOOPY_MODEL": "gpt-5.6-terra",
        "GIT_LOOPY_REASONING_EFFORT": "high",
    }
    _listing(monkeypatch)
    _evidence(monkeypatch)
    if failure == "access":
        env.pop(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV)
    elif failure == "limit":
        values.pop("routing_credit_allowance")
    elif failure == "associations":
        values.pop("route_associations")
    elif failure == "allowance":
        values["routing_credit_allowance"] = "0"
    elif failure == "source":
        async def unavailable(*_args):
            raise OSError("unreachable source with private-aa-key")
        monkeypatch.setattr(dynamic_route, "_stdlib_fetch", unavailable)
    elif failure == "candidates":
        values["route_associations"] = {"aa-unmatched": "gpt-5.6-terra@high"}
    elif failure == "static":
        values["routing"] = {
            "docs": {"model": "unavailable-static-model", "effort": "high"}
        }
    settings.write_config_atomic(path, values)
    original = path.read_bytes()
    output: list[str] = []

    assert _update(
        tmp_path, env=env, routing_choice="migrate", output=output
    ) == 1

    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    text = "\n".join(output)
    assert diagnostic in text
    assert "private-aa-key" not in text
    assert "Updated TUI helper" not in text


@pytest.mark.parametrize("stop_at", [0, 1, 2, 3])
def test_cancelling_any_migration_question_preserves_all_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop_at
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {"enabled_skills": ["tdd"]})
    original = path.read_bytes()
    calls = _listing(monkeypatch)
    answers = iter(["migrate", "30", "2.5", "2"][:stop_at])
    output: list[str] = []

    def answer(_prompt):
        value = next(answers, None)
        if value is None:
            raise EOFError
        return value

    assert _update(
        tmp_path, routing_choice="ask", input_fn=answer, output=output
    ) == 1

    assert calls == []
    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    assert "cancelled" in "\n".join(output)


def test_recorded_choice_is_reused_without_prompts_or_another_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, _authorized_values())
    _listing(monkeypatch)
    _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    output: list[str] = []

    assert _update(
        tmp_path,
        routing_choice="ask",
        input_fn=lambda _prompt: pytest.fail("a recorded choice prompted again"),
        output=output,
    ) == 0

    assert path.read_bytes() == saved
    assert not path.with_suffix(".toml.bak.1").exists()
    assert "already recorded" in "\n".join(output)


def test_project_migration_inherits_routes_and_authorization_without_rewriting_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_path = settings.global_config_path(os.environ)
    values = {
        **_authorized_values(),
        "routing": {"implementation": {"model": "gpt-5.6-terra", "effort": "high"}},
        "context_tier": "long_context",
    }
    settings.write_config_atomic(global_path, values)
    original = global_path.read_bytes()
    _listing(monkeypatch)
    _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")

    assert _update(tmp_path, routing_choice="migrate") == 0

    project = settings.load_config_table(settings.project_config_path(tmp_path))
    assert project == {"route_policy": "dynamic"}
    assert global_path.read_bytes() == original


@pytest.mark.parametrize("policy", ["static", "dynamic"])
def test_unattended_project_migration_can_inherit_a_recorded_global_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy
) -> None:
    path = settings.global_config_path(os.environ)
    settings.write_config_atomic(path, {
        **_authorized_values(), "route_policy": policy,
    })
    original = path.read_bytes()
    _listing(monkeypatch)
    _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    output: list[str] = []

    assert _update(tmp_path, routing_choice="ask", output=output) == 0

    assert not settings.project_config_path(tmp_path).exists()
    assert path.read_bytes() == original
    assert "inherited" in "\n".join(output)


@pytest.mark.parametrize("dry_run", [False, True])
def test_global_migration_names_the_correct_retired_key_repair_without_applying_it(
    tmp_path: Path, dry_run: bool
) -> None:
    path = settings.global_config_path(os.environ)
    settings.write_config_atomic(path, {
        "routing": {"retired": {"model": "gpt-5.6-terra", "effort": "high"}},
    })
    original = path.read_bytes()
    output: list[str] = []

    code = updatecmd.run_update(
        env=os.environ, routing_choice="keep", dry_run=dry_run, output_fn=output.append
    )

    assert code == 1
    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    assert "git-loopy update --global" in "\n".join(output)


def test_an_operator_edit_during_live_readiness_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, _authorized_values())
    original = path.read_bytes()
    _listing(monkeypatch)
    fetch = model_listing.fetch_live_models

    async def edited_while_reading():
        path.write_bytes(original + b"\n# operator edit during readiness\n")
        return await fetch()

    monkeypatch.setattr(model_listing, "fetch_live_models", edited_while_reading)
    output: list[str] = []

    assert _update(tmp_path, routing_choice="keep", output=output) == 1

    assert path.read_bytes() == original + b"\n# operator edit during readiness\n"
    assert not path.with_suffix(".toml.bak").exists()
    assert "changed during" in "\n".join(output)


def test_supplied_environment_bounds_are_saved_but_access_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, _authorized_values())
    _listing(monkeypatch)
    _evidence(monkeypatch)
    env = {
        **os.environ,
        dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV: "private-aa-key",
        "GIT_LOOPY_ROUTING_DEADLINE_SECONDS": "45",
        "GIT_LOOPY_ROUTING_CREDIT_ALLOWANCE": "1.25",
        "GIT_LOOPY_SELECTOR_CONCURRENCY": "2",
    }

    assert _update(tmp_path, routing_choice="migrate", env=env) == 0

    assert settings.load_config_table(path) == {
        **_authorized_values(),
        "route_policy": "dynamic",
        "routing_deadline_seconds": 45.0,
        "routing_credit_allowance": "1.25",
        "selector_concurrency": 2,
    }
    assert "private-aa-key" not in path.read_text()


@pytest.mark.parametrize("inherited", [False, True])
def test_dry_run_plans_supplied_bounds_without_claiming_config_will_be_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: bool
) -> None:
    path = settings.project_config_path(tmp_path)
    values = {**_authorized_values(), "route_policy": "dynamic"}
    settings.write_config_atomic(
        settings.global_config_path(os.environ) if inherited else path, values
    )
    before = path.read_bytes() if path.exists() else None
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    env = {
        "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
        "UV_DEFAULT_INDEX": "https://packagefeedproxy.microsoft.io/pypi/simple/",
        "npm_config_registry": "https://packagefeedproxy.microsoft.io/npm/",
        dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV: "aa-token",
        "GIT_LOOPY_ROUTING_DEADLINE_SECONDS": "45",
    }
    output: list[str] = []

    assert _update(
        tmp_path, routing_choice="ask", dry_run=True, env=env, output=output
    ) == 0

    assert (path.read_bytes() if path.exists() else None) == before
    assert listings == [] and evidence == []
    text = "\n".join(output)
    assert "Would use routing_deadline_seconds = 45.0" in text
    assert f"{path} unchanged" not in text
    assert _update(tmp_path, routing_choice="ask", env=env) == 0
    assert settings.load_config_table(path)["routing_deadline_seconds"] == 45.0
