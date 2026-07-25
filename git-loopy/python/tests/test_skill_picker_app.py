"""Pilot tests for ``git_loopy.interactive.skill_picker_app`` (issue #231).

Gated behind ``pytest.importorskip("textual")`` so a base installation without
the ``[tui]`` extra skips them — the optional picker is an alternate renderer
over the same **Skill policy** selection model, never a new requirement.

These drive the real :class:`SkillPickerApp` through Textual's Pilot to prove
the behaviours that need a running app: arrow navigation, space toggling,
search, confirmation, and cancellation. The selection *rules* themselves
(Required rows, blocked rows, filter-independent selections) live in the shared
:class:`~git_loopy.skillscmd.SkillSelectionModel` and are unit-tested ungated in
``test_skills_cmd.py``; what is proven here is that this renderer routes every
operator action through that one model rather than keeping its own state.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Input  # noqa: E402

from git_loopy.interactive.skill_picker_app import SkillPickerApp  # noqa: E402
from git_loopy.skillscmd import (  # noqa: E402
    SkillSelectionModel,
    SkillSelectionResult,
    SkillSelectionRow,
    run_plain_skill_picker,
)


def _model(enabled: tuple[str, ...] = ("codebase-design", "tdd")) -> SkillSelectionModel:
    return SkillSelectionModel(
        rows=(
            SkillSelectionRow(
                name="tdd",
                source="packaged",
                description="Test-driven development",
                copilot_enabled=True,
                required=True,
            ),
            SkillSelectionRow(
                name="codebase-design",
                source="personal",
                description="Deep module design",
                copilot_enabled=True,
            ),
            SkillSelectionRow(
                name="team-deploy",
                source="project",
                description="Untracked project Skill",
                blocked_reason="project Skill is not git-tracked",
            ),
        ),
        enabled=enabled,
    )


def _rendered(app: SkillPickerApp) -> str:
    table = app.query_one("#skill-rows", DataTable)
    return "\n".join(
        " ".join(str(cell) for cell in table.get_row_at(index))
        for index in range(table.row_count)
    )


async def test_arrow_navigation_space_toggle_and_enter_returns_the_selection() -> None:
    """Down/space/enter is the whole happy path, and it returns the shared result."""
    app = SkillPickerApp(_model())
    async with app.run_test() as pilot:
        # Rows are name-sorted by the shared model: codebase-design, tdd, team-deploy.
        await pilot.press("space")  # toggle codebase-design off
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled == ("tdd",)


async def test_search_filters_rows_without_discarding_hidden_selections() -> None:
    """A filter narrows the *view*; a selection outside it survives untouched."""
    app = SkillPickerApp(_model())
    async with app.run_test() as pilot:
        await pilot.press("t", "e", "a", "m")
        await pilot.pause()
        assert app.query_one("#skill-search", Input).value == "team"
        # Only team-deploy matches, and codebase-design is no longer visible.
        assert [row.name for row in app.selection.visible_rows] == ["team-deploy"]
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled == ("codebase-design", "tdd")


async def test_required_row_is_marked_and_cannot_be_disabled() -> None:
    """A Required Skill is visibly Required and refuses to leave the selection."""
    app = SkillPickerApp(_model())
    async with app.run_test() as pilot:
        assert "Required" in _rendered(app)
        await pilot.press("down")  # cursor onto tdd
        await pilot.press("space")
        await pilot.pause()
        assert "tdd" in app.selection.enabled
        assert "Required Skill" in app.status
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled == ("codebase-design", "tdd")


async def test_blocked_row_stays_visible_and_cannot_be_enabled() -> None:
    """An untracked project winner is shown, marked, and refused — not hidden."""
    app = SkillPickerApp(_model())
    async with app.run_test() as pilot:
        assert "not git-tracked" in _rendered(app)
        await pilot.press("down", "down")  # cursor onto team-deploy
        await pilot.press("space")
        await pilot.pause()
        assert "team-deploy" not in app.selection.enabled
        assert "blocked" in app.status
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled == ("codebase-design", "tdd")


async def test_escape_cancels_and_returns_no_selection() -> None:
    """Cancelling returns ``None``, which the collection seam turns into a no-write."""
    app = SkillPickerApp(_model())
    async with app.run_test() as pilot:
        await pilot.press("space")  # a discarded edit
        await pilot.press("escape")
        await pilot.pause()

    assert app.return_value is None


async def test_confirm_is_refused_while_a_required_skill_is_unselected() -> None:
    """A seed missing a Required Skill cannot be saved from the optional picker either.

    The plain picker refuses the same selection, because both ask the one shared
    model for :attr:`SkillSelectionModel.validation_errors` rather than deciding
    saveability themselves.
    """
    app = SkillPickerApp(_model(enabled=("codebase-design",)))
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert app.return_value is None
        assert "tdd is a Required Skill" in app.status
        # Selecting it makes the same keystroke save.
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is not None
    assert app.return_value.enabled == ("codebase-design", "tdd")


async def test_both_pickers_return_the_same_result_for_the_same_decision() -> None:
    """The optional picker is interchangeable with the plain one, byte for byte.

    ``collect_skill_policy`` calls whichever runner it was handed and then puts
    the result through one validation/commit seam, so the two implementations
    must be substitutable: same type, same value, for the same operator
    decision (here: switch ``codebase-design`` off, save).
    """
    plain_answers = iter(("1", "done", "yes"))
    plain = run_plain_skill_picker(
        _model(),
        input_fn=lambda _prompt: next(plain_answers),
        output_fn=lambda _line: None,
    )

    app = SkillPickerApp(_model())
    async with app.run_test() as pilot:
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()

    assert type(app.return_value) is type(plain) is SkillSelectionResult
    assert app.return_value == plain == SkillSelectionResult(("tdd",))
