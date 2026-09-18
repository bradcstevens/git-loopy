"""Pilot coverage for the alternate Textual ``init`` wizard runner."""

from __future__ import annotations

import builtins
from dataclasses import replace
from pathlib import Path

import pytest
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


async def test_scope_step_explains_a_scope_that_is_not_available() -> None:
    """Outside a repository the project scope still has something to say.

    The numbered runner shows it as an unavailable row rather than omitting it,
    so an operator learns *why* only one scope is on offer; the wizard that
    replaces that runner has to keep the explanation, not just the choice.
    """
    app = InitWizardApp(
        scope_options=("global",),
        scope_paths={"global": Path("/home/.config/git-loopy/config.toml")},
        model_choices=(_choice("model"),),
        default_model="model",
        default_effort="high",
        build_skill_selection=lambda _scaffold, _scope: _skills(),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#wizard-choices", DataTable)
        rendered = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
        assert any("not in a git repository" in line for line in rendered)
        assert table.cursor_row == rendered.index(
            next(line for line in rendered if line.startswith("global"))
        )
        await pilot.press("up")  # onto the unavailable row
        await pilot.press("enter")  # a no-op, exactly as a disabled model row is
        await pilot.pause()
        assert app.screen.query("#wizard-choices")
        await pilot.click("#wizard-choices", offset=(1, 1))  # the mouse refuses it too
        await pilot.pause()
        assert app.screen.query("#wizard-choices")
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_a_catalog_with_nothing_selectable_still_opens_and_cancels() -> None:
    """A policy that disables every model leaves setup cancellable, not crashed.

    The composed model Screen already refuses to select a disabled row, so the
    degenerate catalog has to *reach* that refusal: pre-filling the wizard by
    looking for a selectable row that does not exist must not fail before the
    first screen is even drawn.
    """
    disabled = replace(_choice("model"), selectable=False, policy_state="disabled")
    app = _app(choices=(disabled,), scope_locked=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query("#picker-models")
        await pilot.press("enter")  # a no-op on a disabled row
        await pilot.pause()
        assert app.screen.query("#picker-models")
        await pilot.press("ctrl+c")
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


async def test_review_back_to_model_preserves_the_selected_effort() -> None:
    app = _app(choices=(_choice("model"), _choice("other", ("low", "high"))))
    async with app.run_test() as pilot:
        await pilot.press("enter")  # scope
        await pilot.press("down", "enter")  # model -> effort
        await pilot.press("up", "enter")  # low
        await pilot.press("ctrl+s")
        await pilot.pause()

        review = app.screen.query_one("#wizard-review", DataTable)
        model_row = next(
            index
            for index in range(review.row_count)
            if str(review.get_row_at(index)[0]) == "model"
        )
        await pilot.press(*(["down"] * model_row), "b")
        await pilot.press("enter")
        await pilot.pause()

        efforts = app.screen.query_one("#picker-efforts", DataTable)
        assert efforts.cursor_row == 0
        await pilot.press("enter", "ctrl+s", "enter")

    assert app.return_value is not None
    assert (app.return_value.model, app.return_value.effort) == ("other", "low")


async def test_review_back_to_an_unchanged_scope_keeps_selected_skills() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("space", "enter")
        await pilot.pause()
        await pilot.press("b")
        await pilot.press("enter", "ctrl+s", "enter")

    assert app.return_value is not None
    assert app.return_value.enabled_skills == ("codebase-design", "tdd")


async def test_changing_scaffold_rebuilds_required_skills_and_keeps_choices() -> None:
    """The next Skill step answers to the instructions setup will leave behind."""

    rebuilt_from: list[tuple[bool, str, tuple[str, ...]]] = []

    def build(scaffold: bool, _scope: str) -> SkillSelectionModel:
        required = "tdd" if scaffold else "prototype"
        return SkillSelectionModel(
            rows=(
                SkillSelectionRow(name="codebase-design", source="packaged"),
                SkillSelectionRow(
                    name="prototype",
                    source="packaged",
                    required=required == "prototype",
                ),
                SkillSelectionRow(
                    name="tdd",
                    source="packaged",
                    required=required == "tdd",
                ),
            ),
            enabled=(required,),
        )

    def rebuild(
        scaffold: bool, scope: str, enabled: tuple[str, ...]
    ) -> SkillSelectionModel:
        rebuilt_from.append((scaffold, scope, enabled))
        return build(scaffold, scope)

    app = InitWizardApp(
        scope_options=("project", "global"),
        scope_paths={
            "project": Path("/repo/git-loopy/config.toml"),
            "global": Path("/home/.config/git-loopy/config.toml"),
        },
        model_choices=(_choice("model"),),
        default_model="model",
        default_effort="high",
        build_skill_selection=build,
        rebuild_skill_selection=rebuild,
    )
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("space", "enter")  # explicitly enable codebase-design
        await pilot.press("down", "down", "down", "down", "down", "b")
        await pilot.press("down", "enter")  # change scaffold to no
        await pilot.pause()

        rows = app.screen.query_one("#skill-rows", DataTable)
        rendered = {
            str(rows.get_row_at(index)[1]): rows.get_row_at(index)
            for index in range(rows.row_count)
        }
        assert str(rendered["codebase-design"][0]) == "[x]"
        assert str(rendered["prototype"][0]) == "[x]"
        assert str(rendered["prototype"][4]) == "Required"
        assert str(rendered["tdd"][0]) == "[x]"
        await pilot.press("down", "space")
        assert "prototype is a Required Skill" in app.screen.status
        await pilot.press("enter")
        await pilot.press("down", "down", "down", "down", "down", "b")
        await pilot.press("up", "enter")  # restore scaffold
        await pilot.pause()

        rows = app.screen.query_one("#skill-rows", DataTable)
        rendered = {
            str(rows.get_row_at(index)[1]): rows.get_row_at(index)
            for index in range(rows.row_count)
        }
        assert str(rendered["codebase-design"][0]) == "[x]"
        assert str(rendered["prototype"][0]) == "[ ]"
        await pilot.press("ctrl+c")

    assert app.return_value is None
    assert rebuilt_from == [
        (False, "project", ("codebase-design", "tdd")),
        (True, "project", ("codebase-design", "prototype", "tdd")),
    ]


async def test_returning_to_skills_with_unchanged_scaffold_does_not_rebuild() -> None:
    rebuilt_from: list[tuple[bool, str, tuple[str, ...]]] = []

    def rebuild(
        scaffold: bool, scope: str, enabled: tuple[str, ...]
    ) -> SkillSelectionModel:
        rebuilt_from.append((scaffold, scope, enabled))
        return _skills(enabled)

    app = InitWizardApp(
        scope_options=("project", "global"),
        scope_paths={
            "project": Path("/repo/git-loopy/config.toml"),
            "global": Path("/home/.config/git-loopy/config.toml"),
        },
        model_choices=(_choice("model"),),
        default_model="model",
        default_effort="high",
        build_skill_selection=lambda _scaffold, _scope: _skills(),
        rebuild_skill_selection=rebuild,
    )
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("space", "enter")
        await pilot.press("down", "down", "down", "down", "down", "b")
        await pilot.press("enter")  # retain the prefilled scaffold answer
        await pilot.pause()

        rows = app.screen.query_one("#skill-rows", DataTable)
        assert str(rows.get_row_at(0)[0]) == "[x]"
        await pilot.press("ctrl+c")

    assert app.return_value is None
    assert rebuilt_from == []


async def test_review_refuses_a_rebuilt_policy_that_cannot_be_valid() -> None:
    def build(scaffold: bool, _scope: str) -> SkillSelectionModel:
        if scaffold:
            return _skills()
        return SkillSelectionModel(
            rows=(
                SkillSelectionRow(
                    name="prototype",
                    source="packaged",
                    required=True,
                    blocked_reason="not tracked by git",
                ),
                SkillSelectionRow(name="tdd", source="packaged"),
            ),
            enabled=("prototype",),
        )

    def rebuild(
        scaffold: bool, scope: str, _enabled: tuple[str, ...]
    ) -> SkillSelectionModel:
        return build(scaffold, scope)

    app = InitWizardApp(
        scope_options=("project", "global"),
        scope_paths={
            "project": Path("/repo/git-loopy/config.toml"),
            "global": Path("/home/.config/git-loopy/config.toml"),
        },
        model_choices=(_choice("model"),),
        default_model="model",
        default_effort="high",
        build_skill_selection=build,
        rebuild_skill_selection=rebuild,
    )
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("enter")
        await pilot.press("down", "down", "down", "down", "down", "b")
        await pilot.press("down", "enter")  # change scaffold to no
        await pilot.press("ctrl+s", "enter")
        await pilot.pause()

        assert "prototype is blocked: not tracked by git" in str(
            app.screen.query_one("#wizard-status", Static).render()
        )
        await pilot.press("q")

    assert app.return_value is None


async def test_a_skill_policy_that_cannot_be_resolved_ends_setup_without_a_panic() -> (
    None
):
    """The wizard has to fail the way the runner it stands beside fails.

    ``run_init`` resolves the **Skill policy** through a callback that reaches
    the network, and answers a failure with one line and an untouched scope. The
    numbered runner raises that failure with no interface up. The wizard raises
    it from inside a Textual message handler, where an escape is a *panic*: the
    app is torn down through Textual's crash path and a Rich traceback is printed
    over the operator's terminal before ``run_init`` gets to say its line. So the
    failure is recorded and the app exits normally, and :meth:`outcome` re-raises
    it verbatim — same exception, same handler, no crash dump.
    """

    class _PolicyUnavailable(Exception):
        pass

    def refuse(_scaffold: bool, _scope: str) -> SkillSelectionModel:
        raise _PolicyUnavailable("cannot establish a Skill policy")

    app = InitWizardApp(
        scope_options=("project", "global"),
        scope_paths={"project": Path("/repo/git-loopy/config.toml")},
        model_choices=(_choice("model"),),
        default_model="model",
        default_effort="high",
        build_skill_selection=refuse,
        scope_locked=True,
    )
    async with app.run_test() as pilot:
        await pilot.press("enter")  # model
        await pilot.press("enter")  # effort
        await pilot.press("enter")  # routing
        await pilot.press("enter")  # scaffold — resolves the Skill policy
        await pilot.pause()

    assert app.return_value is None
    with pytest.raises(_PolicyUnavailable):
        app.outcome()


async def test_the_review_shortcut_reports_an_unresolvable_policy_too() -> None:
    """``ctrl+s`` resolves the same policy, so it cannot bypass the same report."""

    class _PolicyUnavailable(Exception):
        pass

    def refuse(_scaffold: bool, _scope: str) -> SkillSelectionModel:
        raise _PolicyUnavailable("cannot establish a Skill policy")

    app = InitWizardApp(
        scope_options=("project", "global"),
        scope_paths={"project": Path("/repo/git-loopy/config.toml")},
        model_choices=(_choice("model"),),
        default_model="model",
        default_effort="high",
        build_skill_selection=refuse,
        scope_locked=True,
    )
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert app.return_value is None
    with pytest.raises(_PolicyUnavailable):
        app.outcome()


async def test_a_completed_wizard_reports_its_answers_as_its_outcome() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s", "enter")
        await pilot.pause()

    assert app.outcome() == app.return_value
    assert app.outcome() is not None


def test_the_runner_reports_the_app_outcome_not_merely_its_return_value(
    monkeypatch,
) -> None:
    """The runner's result is whatever :meth:`InitWizardApp.outcome` says.

    That is the wiring an unresolvable **Skill policy** travels on: the app
    records it rather than panicking, and ``run_init``'s handler only ever sees
    it because the runner reports the outcome instead of the raw return value.
    """
    reported = object()
    monkeypatch.setattr(InitWizardApp, "run", lambda self: None)
    monkeypatch.setattr(InitWizardApp, "outcome", lambda self: reported)

    assert (
        run_textual_init_wizard(
            scope_options=("global",),
            scope_paths={"global": Path("/home/.config/git-loopy/config.toml")},
            model_choices=(_choice("model"),),
            default_model="model",
            default_effort="high",
            rebuild_skill_selection=lambda _scaffold, _scope: (),
            skill_selection_model=lambda _scaffold, _scope: _skills(),
        )
        is reported
    )


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


async def test_review_shortcut_replaces_the_open_step_rather_than_burying_it() -> None:
    """The jump to review leaves the step it left, so Back can still walk back."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("enter")  # scope -> model
        await pilot.pause()
        depth = len(app.screen_stack)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert len(app.screen_stack) == depth
        await pilot.press("ctrl+c")

    assert app.return_value is None


async def test_review_shortcut_from_the_skill_step_keeps_its_toggles() -> None:
    """A Skill toggle is a finished answer, so jumping to review carries it."""
    app = _app()
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("space")  # codebase-design, the first row by name
        await pilot.press("ctrl+s")
        await pilot.pause()
        review = app.screen.query_one("#wizard-review", DataTable)
        assert "2 enabled" in str(review.get_row_at(review.row_count - 1)[1])
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled_skills == ("codebase-design", "tdd")


async def test_review_shortcut_from_a_filtered_skill_step_leaves_no_filter_behind() -> None:
    """Only the selection travels; the search box is the Screen's own state."""
    app = _app()
    async with app.run_test() as pilot:
        await _reach_skills(pilot)
        await pilot.press("q", "u", "i")
        await pilot.press("ctrl+s")
        await pilot.pause()
        review = app.screen.query_one("#wizard-review", DataTable)
        skills_row = next(
            index
            for index in range(review.row_count)
            if str(review.get_row_at(index)[0]) == "skills"
        )
        await pilot.press(*(["down"] * skills_row), "b")
        await pilot.pause()
        rows = app.screen.query_one("#skill-rows", DataTable)
        assert rows.row_count == 3
        await pilot.press("ctrl+c")

    assert app.return_value is None
