"""Pilot coverage for the alternate Textual ``init`` wizard runner."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import DataTable, Static

from git_loopy.interactive.init_wizard_app import InitWizardApp
from git_loopy.interactive.models import ModelChoice
from git_loopy.skillscmd import SkillSelectionModel, SkillSelectionRow


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


async def test_ctrl_c_cancels_and_locked_scope_model_is_first_step() -> None:
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
