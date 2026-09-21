"""Opt-in routing setup crosses the saved Config and live-readiness seams."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from git_loopy import cli, dynamic_route, init, model_listing, settings
from tests.test_init import _choice, _packaged, _policy_seams
from tests.test_routing_migration import _authorized_values, _evidence, _listing


def test_first_project_setup_inherits_explicit_dynamic_authorization_without_static_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    global_path = settings.global_config_path(os.environ)
    settings.write_config_atomic(global_path, _authorized_values())
    original = global_path.read_bytes()
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "private-aa-key")
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("--yes must never prompt")
    )

    assert cli.main(["init", "--yes", "--project", "--routing", "migrate"]) == 0

    path = settings.project_config_path(tmp_path)
    saved = settings.load_config_table(path)
    assert saved["route_policy"] == "dynamic"
    assert "routing" not in saved
    assert "routing_deadline_seconds" not in saved
    assert "routing_credit_allowance" not in saved
    assert "selector_concurrency" not in saved
    assert "route_associations" not in saved
    assert global_path.read_bytes() == original
    assert listings == ["listing"] and evidence == ["evidence"]
    text = capsys.readouterr().out
    assert "uncovered" in text and "No Route selector" in text
    assert "private-aa-key" not in text + path.read_text()


@pytest.mark.parametrize("edited_scope", ["project", "global"])
def test_routing_setup_does_not_overwrite_or_authorize_config_edited_during_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, edited_scope
) -> None:
    path = settings.project_config_path(tmp_path)
    global_path = settings.global_config_path(os.environ)
    settings.write_config_atomic(path, {"max_nmt_strikes": 4})
    settings.write_config_atomic(global_path, _authorized_values())
    original = path.read_bytes()
    edited = path if edited_scope == "project" else global_path
    edited_original = edited.read_bytes()
    prompt = path.with_name("PROMPT.md")
    prompt.write_text("operator prompt\n")
    _listing(monkeypatch)
    fetch = model_listing.fetch_live_models

    async def changed():
        edited.write_bytes(edited_original + b"\n# operator edit during readiness\n")
        return await fetch()

    monkeypatch.setattr(model_listing, "fetch_live_models", changed)
    _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")

    assert _guided_init(tmp_path, routing_choice="migrate") == 1

    assert edited.read_bytes() == edited_original + b"\n# operator edit during readiness\n"
    if edited_scope == "global":
        assert path.read_bytes() == original
    assert prompt.read_text() == "operator prompt\n"
    assert "changed during" in capsys.readouterr().err


def _guided_init(
    tmp_path: Path, *, scope="project", routing=None, env=None, **kwargs
) -> int:
    return init.run_init(
        scope=scope,
        assume_yes=False,
        repo_root=tmp_path,
        env=dict(os.environ) if env is None else env,
        fetch_choices=lambda: [_choice("gpt-5.6-terra", efforts=("high",))],
        wizard_runner=lambda **_options: init.InitAnswers(
            scope=scope,
            model="gpt-5.6-terra",
            effort="high",
            routing=routing,
            scaffold=True,
            enabled_skills=(),
        ),
        **_packaged(tmp_path),
        **kwargs,
    )


@pytest.mark.parametrize("scope", ["project", "global"])
def test_guided_first_setup_collects_bounds_and_authored_associations_before_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, scope
) -> None:
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "private-aa-key")
    answers = iter([
        "migrate", "30", "2.5", "2", '{"aa-terra" = "gpt-5.6-terra@high"}',
    ])
    prompts: list[str] = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)

    assert _guided_init(tmp_path, scope=scope, routing_choice="ask") == 0

    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    assert settings.load_config_table(path) == {
        **_authorized_values(),
        "route_policy": "dynamic",
        "selector_concurrency": 2,
        "enabled_skills": [],
    }
    assert len(prompts) == 5
    assert listings == ["listing"] and evidence == ["evidence"]
    text = capsys.readouterr().out
    assert "verified" in text.lower() and "not inferred" in text
    assert "post-paid" in text and "overshoot" in text
    assert "private-aa-key" not in text + path.read_text()


def test_unattended_routing_reinit_preserves_the_authored_run_wide_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = settings.project_config_path(tmp_path)
    values = {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "context_tier": "long_context",
        "routing": {"docs": {"model": "gpt-5.6-terra", "effort": "high"}},
    }
    settings.write_config_atomic(path, values)
    _listing(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)

    assert cli.main(["init", "--yes", "--project", "--routing", "keep"]) == 0

    saved = settings.load_config_table(path)
    assert {key: saved[key] for key in values} == values
    assert saved["route_policy"] == "static"


@pytest.mark.parametrize("inherited", [False, True])
def test_unattended_routing_setup_keeps_authored_prompt_and_skill_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited
) -> None:
    path = settings.project_config_path(tmp_path)
    source = settings.global_config_path(os.environ) if inherited else path
    values = {**_authorized_values(), "enabled_skills": ["prototype", "tdd"]}
    settings.write_config_atomic(source, values)
    original = source.read_bytes()
    prompt = source.with_name("PROMPT.md")
    prompt.write_text("operator-owned instructions\n")
    _listing(monkeypatch)
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)

    assert cli.main(["init", "--yes", "--project", "--routing", "keep"]) == 0

    saved = settings.load_config_table(path)
    assert saved["route_policy"] == "static"
    assert prompt.read_text() == "operator-owned instructions\n"
    if inherited:
        assert "enabled_skills" not in saved
        assert not path.with_name("PROMPT.md").exists()
        assert source.read_bytes() == original
    else:
        assert saved["enabled_skills"] == values["enabled_skills"]


@pytest.mark.parametrize(
    "collected_routes", [{}, {"bugfix": ("gpt-5.6-terra", "high")}]
)
def test_skipped_static_rows_survive_opt_in_routing_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collected_routes
) -> None:
    path = settings.project_config_path(tmp_path)
    original_routes = {"docs": {"model": "gpt-5.6-terra", "effort": "high"}}
    settings.write_config_atomic(path, {
        **_authorized_values(), "routing": original_routes,
    })
    _listing(monkeypatch)

    assert _guided_init(
        tmp_path, routing_choice="keep", routing=collected_routes
    ) == 0

    saved = settings.load_config_table(path)["routing"]
    assert saved["docs"] == original_routes["docs"]
    assert set(saved) == {"docs", *collected_routes}


@pytest.mark.parametrize("stop_at", range(5))
def test_cancelling_routing_setup_writes_no_operator_choices_or_tracker_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, stop_at
) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {"max_nmt_strikes": 4})
    original = path.read_bytes()
    prompt = path.with_name("PROMPT.md")
    prompt.write_text("operator prompt\n")
    answers = iter(["migrate", "30", "2.5", "2"][:stop_at])
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)

    class NoTracker:
        def label_catalog(self):
            pytest.fail("cancelled setup contacted the tracker")

    def answer(_prompt):
        value = next(answers, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr("builtins.input", answer)

    assert _guided_init(
        tmp_path, routing_choice="ask", label_client=NoTracker()
    ) == 1

    assert path.read_bytes() == original
    assert prompt.read_text() == "operator prompt\n"
    assert not path.with_name("scaffold-provenance.json").exists()
    assert listings == [] and evidence == []
    text = capsys.readouterr().err
    assert "cancelled" in text and "no Config" in text


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        ("access", dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV),
        ("deadline", "routing_deadline_seconds"),
        ("allowance", "quota_exhausted"),
        ("concurrency", "selector_concurrency"),
        ("source", "source_unavailable"),
        ("candidates", "no_runnable_candidate"),
        ("static", "unavailable-static-model"),
    ],
)
def test_routing_setup_refusal_preserves_every_asset_despite_temporary_run_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, failure, diagnostic
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
    elif failure == "deadline":
        values["routing_deadline_seconds"] = "nan"
    elif failure == "allowance":
        values["routing_credit_allowance"] = "0"
    elif failure == "concurrency":
        values["selector_concurrency"] = 0
    elif failure == "source":
        async def unavailable(*_args):
            raise OSError("source unavailable with private-aa-key")
        monkeypatch.setattr(dynamic_route, "_stdlib_fetch", unavailable)
    elif failure == "candidates":
        values["route_associations"] = {"aa-unmatched": "gpt-5.6-terra@high"}
    elif failure == "static":
        values["routing"] = {
            "docs": {"model": "unavailable-static-model", "effort": "high"},
        }
    settings.write_config_atomic(path, values)
    original = path.read_bytes()
    prompt = path.with_name("PROMPT.md")
    prompt.write_text("operator prompt\n")
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("all answers already supplied")
    )

    assert _guided_init(tmp_path, routing_choice="migrate", env=env) == 1

    assert path.read_bytes() == original
    assert prompt.read_text() == "operator prompt\n"
    assert not path.with_name("scaffold-provenance.json").exists()
    captured = capsys.readouterr()
    assert diagnostic in captured.err
    assert "private-aa-key" not in captured.err + captured.out


@pytest.mark.parametrize("choice", ["ask", "migrate"])
def test_unattended_first_setup_neither_invents_consent_nor_prompts_for_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, choice
) -> None:
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("unattended setup prompted")
    )

    assert cli.main(["init", "--yes", "--project", "--routing", choice]) == 1

    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert listings == [] and evidence == []
    text = capsys.readouterr().err
    assert ("keep-or-migrate" if choice == "ask" else "routing_deadline_seconds") in text
    assert "git-loopy init --routing" in text


@pytest.mark.parametrize(
    "association",
    [
        "not a table",
        '["aa-terra"]',
        '{"aa-terra" = 123}',
        '{"aa-terra" = "gpt-5.6-terra@unknown"}',
        '{"aa-terra" = "gpt-5.6-terra@high"}\nmodel = "injected"',
        "{}",
    ],
)
def test_invalid_authored_associations_never_save_partial_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, association
) -> None:
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    answers = iter(["30", "2.5", "2", association])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    _listing(monkeypatch)
    _evidence(monkeypatch)

    assert _guided_init(tmp_path, routing_choice="migrate") == 1

    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert "no Config" in capsys.readouterr().err


@pytest.mark.parametrize("scope", ["project", "global"])
def test_guided_static_setup_needs_no_leaderboard_access_or_routing_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope
) -> None:
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("Static setup asked for access")
    )

    assert _guided_init(tmp_path, scope=scope, routing_choice="keep") == 0

    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    saved = settings.load_config_table(path)
    assert saved["route_policy"] == "static"
    assert "routing_credit_allowance" not in saved and "route_associations" not in saved
    assert listings == ["listing"] and evidence == []


def _first_setup_for_run(
    tmp_path, monkeypatch, *, choice, saved_route, routing_credit_allowance="2.5",
    selector_concurrency=1,
):
    """Author first setup through init, retaining the real Run's Skill catalog."""
    from git_loopy.prompt import packaged_required_skills
    from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner

    answers = iter([
        "30", str(routing_credit_allowance), str(selector_concurrency),
        '{"aa-opus" = "claude-opus-5@high", "aa-terra" = "gpt-5.6-terra@high"}',
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    required = packaged_required_skills()
    return init.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=dict(os.environ),
        routing_choice=choice,
        fetch_choices=lambda: [_choice("gpt-5.6-terra", efforts=("high",))],
        wizard_runner=lambda **_options: init.InitAnswers(
            scope="project",
            model="gpt-5.6-terra",
            effort="high",
            routing={"implementation": ("gpt-5.6-terra", "high")} if saved_route else None,
            scaffold=True,
            enabled_skills=required,
        ),
        **_policy_seams(
            tmp_path,
            required_skills=required,
            catalog=SkillCatalog(
                winners={name: SkillCatalogWinner(name, "packaged") for name in required}
            ),
        ),
    )
