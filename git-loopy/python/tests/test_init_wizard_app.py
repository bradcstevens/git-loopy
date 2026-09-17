"""Pilot coverage for the alternate Textual ``init`` wizard runner."""

from __future__ import annotations

import builtins
from pathlib import Path

from textual.widgets import DataTable, Static

from git_loopy import init as init_module
from git_loopy.config import RECOMMENDED_ROUTING
from git_loopy.interactive.init_wizard_app import (
    InitWizardApp,
    run_textual_init_wizard,
)
from git_loopy.interactive.models import ModelChoice
from git_loopy.skillscmd import SkillSelectionModel, SkillSelectionRow


def test_setup_runner_is_selectable_without_being_the_default() -> None:
    assert init_module.select_wizard_runner({}) is init_module._default_wizard_runner
    assert (
        init_module.select_wizard_runner({"GIT_LOOPY_INIT_WIZARD": "0"})
        is init_module._default_wizard_runner
    )
    for opt_in in ("1", "true", "Yes", "on", "textual"):
        assert (
            init_module.select_wizard_runner({"GIT_LOOPY_INIT_WIZARD": opt_in})
            is run_textual_init_wizard
        )


def test_setup_runner_falls_back_when_textual_is_not_installed(monkeypatch) -> None:
    import_module = builtins.__import__

    def import_without_textual(name, *args, **kwargs):
        if name == "git_loopy.interactive.init_wizard_app":
            raise ModuleNotFoundError("No module named 'textual'", name="textual")
        return import_module(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_textual)

    assert (
        init_module.select_wizard_runner({"GIT_LOOPY_INIT_WIZARD": "textual"})
        is init_module._default_wizard_runner
    )


def _choice(id: str, efforts: tuple[str, ...] = ("high",)) -> ModelChoice:
    return ModelChoice(
        id=id,
        name=id,
        multiplier=1.0,
        context_window=200_000,
        supports_reasoning=bool(efforts),
        default_effort=efforts[-1] if efforts else None,
        supported_efforts=efforts,
        selectable=True,
        policy_state="enabled",
    )


def _skills(enabled: tuple[str, ...] = ("tdd",)) -> SkillSelectionModel:
    return SkillSelectionModel(
        rows=(
            SkillSelectionRow(
                name="tdd",
                source="packaged",
                required=True,
                description="Test-driven development",
            ),
            SkillSelectionRow(name="codebase-design", source="packaged"),
            SkillSelectionRow(name="quick-win", source="packaged"),
        ),
        enabled=enabled,
    )


def _app(
    *,
    choices: tuple[ModelChoice, ...] = (_choice("model"),),
    enabled_skills: tuple[str, ...] = ("tdd",),
    scope_locked: bool = False,
) -> InitWizardApp:
    return InitWizardApp(
        scope_options=("project", "global"),
        scope_paths={
            "project": Path("/repo/git-loopy/config.toml"),
            "global": Path("/home/.config/git-loopy/config.toml"),
        },
        model_choices=choices,
        default_model="model",
        default_effort="high",
        build_skill_selection=lambda _scaffold, _scope: _skills(enabled_skills),
        scope_locked=scope_locked,
    )


async def _reach_review(pilot) -> None:
    await pilot.press("enter")  # scope
    await pilot.press("enter")  # model -> effort
    await pilot.press("enter")  # effort
    await pilot.press("enter")  # routing
    await pilot.press("enter")  # scaffold
    await pilot.press("enter")  # Skills
    await pilot.pause()


async def test_cursor_space_and_enter_drive_prefilled_wizard() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("down", "enter")
        await pilot.press("enter", "enter", "enter", "enter")
        await pilot.press("space")
        await pilot.press("enter", "enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.scope == "global"
    assert app.return_value.enabled_skills == ("codebase-design", "tdd")


async def test_escape_steps_back_and_cancels_from_first_available_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.press("escape")
        await pilot.pause()
        assert "Configure git-loopy" in str(app.screen.query_one(Static).render())
        await pilot.press("escape")
        await pilot.pause()

    assert app.return_value is None


async def test_escape_on_the_first_step_cancels_when_scope_is_locked() -> None:
    app = _app(scope_locked=True)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()

    assert app.return_value is None


async def test_model_without_reasoning_effort_skips_its_effort_step() -> None:
    app = _app(choices=(_choice("noreason", ()),), scope_locked=True)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one("#wizard-choices", DataTable)
        assert str(table.get_row_at(0)[0]).startswith("Use all recommended")
        await pilot.press("enter")  # routing
        await pilot.press("enter")  # scaffold
        await pilot.press("enter")  # Skills
        await pilot.press("enter")  # review -> Save
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.model == "noreason"
    assert app.return_value.effort is None


async def test_custom_routing_keeps_recommended_routes_by_default() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter", "enter", "enter")  # scope, model, effort
        await pilot.press("down", "down", "enter")  # custom routing
        for _ in RECOMMENDED_ROUTING:
            await pilot.press("enter")
        await pilot.press("enter", "enter", "enter")  # scaffold, Skills, Save
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.routing == dict(RECOMMENDED_ROUTING)


async def test_review_shortcut_preserves_prefilled_custom_routing() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter", "enter", "enter")  # scope, model, effort
        await pilot.press("down", "down", "enter")  # custom routing
        await pilot.press("ctrl+s", "enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.routing == dict(RECOMMENDED_ROUTING)


async def test_route_override_prefills_the_recommended_effort() -> None:
    app = _app(choices=(_choice("claude-opus-5", ("high", "xhigh", "max")),))
    async with app.run_test() as pilot:
        await pilot.press("enter", "enter", "enter")  # scope, model, effort
        await pilot.press("down", "down", "enter")  # custom routing
        await pilot.press("down", "down", "enter")  # planning: override
        await pilot.press("enter")  # recommended model
        await pilot.pause()

        effort_table = app.screen.query_one("#picker-efforts", DataTable)
        assert effort_table.cursor_row == 1  # planning's recommended "xhigh"
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_escape_in_custom_routing_returns_to_the_previous_task_type() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter", "enter", "enter")  # scope, model, effort
        await pilot.press("down", "down", "enter")  # custom routing
        await pilot.press("enter")  # planning: keep recommended
        await pilot.press("escape")
        await pilot.pause()

        assert "task-type:planning" in str(app.screen.query_one(Static).render())
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_custom_routing_back_keeps_an_explicitly_skipped_route() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter", "enter", "enter")  # scope, model, effort
        await pilot.press("down", "down", "enter")  # custom routing
        await pilot.press("down", "enter")  # planning: do not configure
        await pilot.press("escape")
        await pilot.pause()

        choices = app.screen.query_one("#wizard-choices", DataTable)
        assert choices.cursor_row == 1
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_review_lists_answers_and_back_returns_to_selected_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        review = app.screen.query_one("#wizard-review", DataTable)
        rendered = "\n".join(
            " ".join(str(cell) for cell in review.get_row_at(index))
            for index in range(review.row_count)
        )
        for expected in (
            "project",
            "/repo/git-loopy/config.toml",
            "model",
            "high",
            "disabled",
            "PROMPT.md",
            "1 enabled",
        ):
            assert expected in rendered

        await pilot.press("down", "down", "down", "down", "down", "b")
        await pilot.pause()
        assert "Scaffold an editable" in str(app.screen.query_one(Static).render())
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_review_shortcut_saves_prefilled_answers() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled_skills == ("tdd",)


async def test_cancel_from_review_produces_cancellation() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        await pilot.press("q")
        await pilot.pause()

    assert app.return_value is None


async def test_review_refuses_invalid_policy_from_shared_model() -> None:
    app = _app(enabled_skills=())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s", "enter")
        await pilot.pause()
        assert "tdd is a Required Skill" in str(
            app.screen.query_one("#wizard-status", Static).render()
        )
        await pilot.press("q")

    assert app.return_value is None


async def _reach_skills(pilot) -> None:
    """Walk the prefilled steps up to (and including) opening the Skill step."""
    await pilot.press("enter")  # scope
    await pilot.press("enter")  # model
    await pilot.press("enter")  # effort
    await pilot.press("enter")  # routing
    await pilot.press("enter")  # scaffold
    await pilot.pause()


async def test_skill_step_filters_by_typing_without_dropping_a_selection() -> None:
    """Typing narrows the view only; a Skill chosen before the filter is saved."""
    app = _app()
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("space")  # codebase-design, the first row by name
        await pilot.press("q", "u", "i")
        await pilot.pause()
        rows = app.screen.query_one("#skill-rows", DataTable)
        assert [str(rows.get_row_at(i)[1]) for i in range(rows.row_count)] == [
            "quick-win"
        ]
        await pilot.press("enter")  # confirm while the selection is off-screen
        await pilot.pause()
        review = app.screen.query_one("#wizard-review", DataTable)
        assert "2 enabled" in str(review.get_row_at(review.row_count - 1)[1])
        await pilot.press("enter")

    assert app.return_value is not None
    assert app.return_value.enabled_skills == ("codebase-design", "tdd")


async def test_skill_step_refuses_a_required_row_and_shows_the_reason() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        rows = app.screen.query_one("#skill-rows", DataTable)
        required = next(
            index
            for index in range(rows.row_count)
            if str(rows.get_row_at(index)[1]) == "tdd"
        )
        assert str(rows.get_row_at(required)[4]) == "Required"
        await pilot.press(*(["down"] * required))
        await pilot.press("space")
        await pilot.pause()
        assert "tdd is a Required Skill" in app.screen.status
        await pilot.press("enter")
        await pilot.press("enter")

    assert app.return_value is not None
    assert app.return_value.enabled_skills == ("tdd",)


async def test_ctrl_c_cancels_outright_from_a_step_in_the_middle() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("ctrl+c")
        await pilot.pause()

    assert app.return_value is None


async def test_ctrl_c_cancels_outright_from_a_choice_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter")  # scope
        await pilot.press("enter")  # model
        await pilot.press("enter")  # effort -> routing
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

    assert app.return_value is None


async def test_review_cancel_control_produces_a_cancellation() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        await pilot.click("#wizard-cancel")
        await pilot.pause()

    assert app.return_value is None


async def test_review_save_control_commits_the_collected_answers() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        await pilot.click("#wizard-save")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.scope == "project"


async def test_review_back_control_returns_to_the_selected_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        review = app.screen.query_one("#wizard-review", DataTable)
        model_row = next(
            index
            for index in range(review.row_count)
            if str(review.get_row_at(index)[0]) == "model"
        )
        await pilot.press(*(["down"] * model_row))
        await pilot.click("#wizard-back")
        await pilot.pause()
        assert app.screen.query("#picker-models")
        await pilot.press("ctrl+c")

    assert app.return_value is None


def test_wizard_import_graph_never_reaches_live_run_state() -> None:
    """Setup must not couple to the Run, so hosting it in the Dashboard cannot.

    Asserted against the real import graph rather than the source text: a
    transitive import would couple the wizard just as hard as a direct one.
    """
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import git_loopy.interactive.init_wizard_app;"
        "print('git_loopy.interactive.state' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


async def test_review_back_on_the_config_row_returns_to_the_scope_step() -> None:
    """The config path is what the scope decides, not a step of its own."""
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        review = app.screen.query_one("#wizard-review", DataTable)
        config_row = next(
            index
            for index in range(review.row_count)
            if str(review.get_row_at(index)[0]) == "config"
        )
        await pilot.press(*(["down"] * config_row))
        await pilot.press("b")
        await pilot.pause()
        assert "Configure git-loopy" in str(app.screen.query_one(Static).render())
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_review_shortcut_on_the_review_screen_does_not_stack_a_second_one() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        depth = len(app.screen_stack)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert len(app.screen_stack) == depth
        await pilot.press("ctrl+c")

    assert app.return_value is None
