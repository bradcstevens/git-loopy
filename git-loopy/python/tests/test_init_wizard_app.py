"""Pilot coverage for the alternate Textual ``init`` wizard runner."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.widgets import DataTable, Static

from git_loopy.interactive.init_wizard_app import InitWizardApp, _Review, _ReviewScreen
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
        skill_selection_model=lambda _scaffold, _scope: _skills(),
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


async def test_cursor_space_and_enter_drive_the_prefilled_wizard() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("down", "enter")
        await pilot.press("enter", "enter", "enter", "enter")
        await pilot.press("space")  # add codebase-design to the Skill policy
        await pilot.press("enter", "enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.scope == "global"
    assert app.return_value.enabled_skills == ("codebase-design", "tdd")


async def test_escape_goes_back_one_step_but_cancels_at_the_first_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.press("escape")
        await pilot.pause()
        assert "Configure git-loopy" in str(app.screen.query_one(Static).render())
        await pilot.press("escape")
        await pilot.pause()

    assert app.return_value is None


async def test_ctrl_c_cancels_from_any_wizard_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter", "ctrl+c")
        await pilot.pause()

    assert app.return_value is None


async def test_model_without_reasoning_effort_skips_that_step() -> None:
    app = _app(choices=(_choice("noreason", ()),), scope_locked=True)
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()

        table = app.screen.query_one("#wizard-choices", DataTable)
        assert str(table.get_row_at(0)[0]).startswith("Use all recommended")


async def test_review_lists_answers_and_back_returns_to_the_selected_step() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        review = app.screen.query_one("#wizard-review", DataTable)
        rendered = "\n".join(
            " ".join(str(cell) for cell in review.get_row_at(index))
            for index in range(review.row_count)
        )
        assert "project" in rendered
        assert "/repo/git-loopy/config.toml" in rendered
        assert "model" in rendered
        assert "high" in rendered
        assert "global default only" in rendered
        assert "PROMPT.md" in rendered
        assert "1 enabled" in rendered

        await pilot.press("down", "down", "down", "down", "b")
        await pilot.pause()
        assert "Scaffold an editable" in str(app.screen.query_one(Static).render())
        await pilot.press("q")

    assert app.return_value is None


async def test_cancel_from_the_review_produces_a_cancelled_runner() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_review(pilot)
        await pilot.press("q")
        await pilot.pause()

    assert app.return_value is None


async def test_review_uses_the_shared_skill_validation_errors() -> None:
    review = _ReviewScreen(
        _Review(
            scope="project",
            config_path=Path("/repo/git-loopy/config.toml"),
            model="model",
            effort="high",
            routing=None,
            scaffold=True,
            skills=_skills(()),
        )
    )

    class Host(App[None]):
        def on_mount(self) -> None:
            self.push_screen(review)

    async with Host().run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert "tdd is a Required Skill" in str(
            review.query_one("#wizard-status", Static).render()
        )
