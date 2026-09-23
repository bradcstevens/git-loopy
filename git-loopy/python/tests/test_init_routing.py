"""Opt-in routing setup crosses the saved Config and live-readiness seams."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from textual.widgets import DataTable

from git_loopy import cli, dynamic_route, init, model_listing, settings
from git_loopy.interactive.init_wizard_app import InitWizardApp, run_textual_init_wizard
from git_loopy.skillscmd import SkillSelectionModel
from tests.test_init import _choice, _packaged, _policy_seams
from tests.test_routing_migration import _authorized_values, _evidence, _listing


@pytest.mark.parametrize("guided", [False, True])
@pytest.mark.parametrize("scope", ["project", "global", "inherited"])
def test_bare_setup_cannot_bypass_recorded_dynamic_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, guided, scope
) -> None:
    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    settings.write_config_atomic(path, {
        **_authorized_values(), "route_policy": "dynamic",
    })
    original = path.read_bytes()
    prompt = path.with_name("PROMPT.md")
    prompt.write_text("operator-owned instructions\n")
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    monkeypatch.setenv("GIT_LOOPY_ROUTE_POLICY", "static")
    monkeypatch.setenv("GIT_LOOPY_MODEL", "gpt-5.6-terra")
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("--yes must never prompt")
    )

    chosen_scope = "global" if scope == "global" else "project"
    result = (
        _guided_init(tmp_path, scope=chosen_scope)
        if guided else cli.main(["init", "--yes", f"--{chosen_scope}"])
    )
    assert result == 1

    assert path.read_bytes() == original
    assert prompt.read_text() == "operator-owned instructions\n"
    if scope == "inherited":
        assert not settings.project_config_path(tmp_path).exists()
    assert listings == [] and evidence == []
    assert dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV in capsys.readouterr().err


@pytest.mark.parametrize(
    "invalid", ['route_policy = "hybrid"\n', "route_policy = 7\n", "[not valid\n"],
)
def test_bare_setup_reports_invalid_inherited_authority_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, invalid
) -> None:
    path = settings.global_config_path(os.environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(invalid)
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)

    assert cli.main(["init", "--yes", "--project"]) == 1

    assert path.read_text() == invalid
    assert not settings.project_config_path(tmp_path).exists()
    assert "no Config" in capsys.readouterr().err


@pytest.mark.parametrize("scope", ["project", "global"])
def test_recorded_setup_custom_walk_adds_no_unvisited_static_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope
) -> None:
    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    settings.write_config_atomic(path, {
        **_authorized_values(), "route_policy": "dynamic",
    })
    if scope == "global":
        project = settings.project_config_path(tmp_path)
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text('route_policy = "hybrid"\n')
    _listing(monkeypatch)
    _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded authority must not prompt")
    )

    async def drive(app, pilot):
        if scope == "global":
            await pilot.press("down")
        await pilot.press("enter", "enter", "enter")
        await pilot.press("down", "enter")
        await pilot.press("ctrl+s")
        await pilot.pause()
        table = app.screen.query_one("#wizard-review", DataTable)
        rows = {
            str(table.get_row_at(i)[0]): str(table.get_row_at(i)[1])
            for i in range(table.row_count)
        }
        assert "no new Static routes" in rows["routing"]
        assert "authorization follows" in rows["routing"]
        await pilot.press("enter")

    assert _pilot_setup(tmp_path, monkeypatch, drive) == 0

    saved = settings.load_config_table(path)
    assert saved["route_policy"] == "dynamic"
    assert saved.get("routing", {}) == {}
    if scope == "global":
        assert settings.project_config_path(tmp_path).read_text() == 'route_policy = "hybrid"\n'


def _pilot_setup(tmp_path, monkeypatch, drive, *, routing_choice=None):
    def run(app):
        async def run_pilot():
            async with app.run_test() as pilot:
                await drive(app, pilot)
        asyncio.run(run_pilot())

    def wizard_runner(
        *, scope_options, scope_paths, model_choices, default_model, default_effort,
        rebuild_skill_selection, scope_locked, routing_choice=None, routing_choices=None,
    ):
        return run_textual_init_wizard(
            scope_options=scope_options,
            scope_paths=scope_paths,
            model_choices=model_choices,
            default_model=default_model,
            default_effort=default_effort,
            rebuild_skill_selection=rebuild_skill_selection,
            skill_selection_model=lambda *_args: SkillSelectionModel(rows=(), enabled=()),
            scope_locked=scope_locked,
            routing_choice=routing_choice,
            routing_choices=routing_choices,
        )

    monkeypatch.setattr(InitWizardApp, "run", run)
    return init.run_init(
        scope=None,
        assume_yes=False,
        repo_root=tmp_path,
        env=dict(os.environ),
        fetch_choices=lambda: [_choice("gpt-5.6-terra", efforts=("high",))],
        wizard_runner=wizard_runner,
        routing_choice=routing_choice,
        **_packaged(tmp_path),
    )


@pytest.mark.parametrize("keep_planning", [False, True])
@pytest.mark.parametrize("revisit_routes", [False, True])
def test_switching_out_of_recorded_setup_preserves_destination_static_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep_planning, revisit_routes,
) -> None:
    settings.write_config_atomic(settings.project_config_path(tmp_path), {
        **_authorized_values(), "route_policy": "dynamic",
    })
    global_path = settings.global_config_path(os.environ)
    routes = {"docs": {"model": "gpt-5.6-terra", "effort": "high"}}
    settings.write_config_atomic(global_path, {
        "model": "gpt-5.6-terra", "reasoning_effort": "high", "routing": routes,
    })

    async def drive(_app, pilot):
        await pilot.press("enter", "enter", "enter")
        await pilot.press("down", "enter")
        if keep_planning:
            await pilot.press("up", "enter")
        await pilot.press("ctrl+s", "b")
        await pilot.press("down", "enter")
        if revisit_routes:
            await pilot.press("enter", "enter", "enter")
        await pilot.press("ctrl+s", "enter")

    assert _pilot_setup(tmp_path, monkeypatch, drive) == 0

    saved = settings.load_config_table(global_path)
    assert saved["routing"] == {
        **routes,
        **({"planning": {"model": "claude-opus-5", "effort": "xhigh"}}
           if keep_planning else {}),
    }
    assert "route_policy" not in saved


@pytest.mark.parametrize("change", ["added", "removed"])
@pytest.mark.parametrize("source", ["project", "global"])
@pytest.mark.parametrize("routing_choice", [None, "ask"])
def test_routing_authority_edited_while_the_wizard_is_open_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, change, source, routing_choice,
) -> None:
    path = (
        settings.project_config_path(tmp_path)
        if source == "project" else settings.global_config_path(os.environ)
    )
    values = _authorized_values()
    before = {**values, **({"route_policy": "dynamic"} if change == "removed" else {})}
    after = {**values, **({"route_policy": "dynamic"} if change == "added" else {})}
    settings.write_config_atomic(path, before)
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("changed authority must not prompt")
    )
    newer: list[bytes] = []

    async def drive(_app, pilot):
        await pilot.press("enter", "enter", "enter")
        await pilot.press("down", "enter", "ctrl+s")
        settings.write_config_atomic(path, after)
        newer.append(path.read_bytes())
        await pilot.press("enter")

    assert _pilot_setup(tmp_path, monkeypatch, drive, routing_choice=routing_choice) == 1

    assert path.read_bytes() == newer[0]
    assert listings == [] and evidence == []
    assert "changed during setup" in capsys.readouterr().err


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


@pytest.mark.parametrize("scope", ["project", "global"])
def test_unattended_new_setup_records_dynamic_without_seeds_or_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, scope: str
) -> None:
    """Fresh ``init --yes`` records Dynamic and invents no spend authority."""
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("unattended new setup prompted")
    )

    assert cli.main(["init", "--yes", f"--{scope}"]) == 0

    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    other = (
        settings.global_config_path(os.environ)
        if scope == "project" else settings.project_config_path(tmp_path)
    )
    saved = settings.load_config_table(path)
    assert saved["route_policy"] == "dynamic"
    assert "routing" not in saved and "escalation" not in saved
    for key in (
        "routing_deadline_seconds",
        "routing_credit_allowance",
        "selector_concurrency",
        "route_associations",
    ):
        assert key not in saved
    rendered = path.read_text()
    assert dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV not in rendered
    assert "private" not in rendered
    assert not other.exists()
    assert listings == [] and evidence == []
    text = capsys.readouterr().out
    assert "route_policy = dynamic" in text
    assert "git-loopy init --routing migrate" in text


def test_unattended_reinit_does_not_infer_dynamic_from_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved scope without a policy is not a new installation."""
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        "model": "gpt-5.6-terra", "reasoning_effort": "high",
    })
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("reinit prompted")
    )

    assert cli.main(["init", "--yes", "--project"]) == 0

    assert "route_policy" not in settings.load_config_table(path)


def _policy_init(tmp_path: Path, policy_choice: str) -> int:
    return init.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=dict(os.environ),
        fetch_choices=lambda: [_choice("gpt-5.6-terra", efforts=("high",))],
        wizard_runner=lambda **_options: init.InitAnswers(
            scope="project",
            model="gpt-5.6-terra",
            effort="high",
            routing=None,
            scaffold=True,
            enabled_skills=(),
            policy_choice=policy_choice,
        ),
        **_packaged(tmp_path),
    )


def test_interactive_fresh_setup_defaults_to_migrate_and_collects_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "private-aa-key")
    answers = iter(["30", "2.5", "2", '{"aa-terra" = "gpt-5.6-terra@high"}'])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert _policy_init(tmp_path, "migrate") == 0

    saved = settings.load_config_table(settings.project_config_path(tmp_path))
    assert saved["route_policy"] == "dynamic"
    assert "routing" not in saved
    assert saved["routing_deadline_seconds"] == 30
    assert "private-aa-key" not in settings.project_config_path(tmp_path).read_text()
    assert listings == ["listing"] and evidence == ["evidence"]


def test_interactive_fresh_setup_keep_needs_no_dynamic_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listings = _listing(monkeypatch)
    evidence = _evidence(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("keep asked for Dynamic limits")
    )

    assert _policy_init(tmp_path, "keep") == 0

    saved = settings.load_config_table(settings.project_config_path(tmp_path))
    assert saved["route_policy"] == "static"
    assert "routing" not in saved and "routing_credit_allowance" not in saved
    assert listings == ["listing"] and evidence == []


def test_cancelling_the_fresh_migrate_default_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _cancel(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _cancel)

    assert _policy_init(tmp_path, "migrate") == 1

    assert not settings.project_config_path(tmp_path).exists()


def test_unattended_new_project_does_not_shadow_inherited_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_path = settings.global_config_path(os.environ)
    settings.write_config_atomic(global_path, {"route_policy": "unselected"})
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)

    assert cli.main(["init", "--yes", "--project"]) == 0

    saved = settings.load_config_table(settings.project_config_path(tmp_path))
    assert "route_policy" not in saved
    assert settings.load_config_table(global_path)["route_policy"] == "unselected"


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
