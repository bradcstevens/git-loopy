"""Alternate Textual runner for the ``git-loopy init`` answer seam.

The default init runner remains the numbered terminal flow.  This module is an
opt-in, fullscreen implementation of the same runner contract: it owns only
the transient interface state and returns the complete answer set for
``run_init`` to validate and write.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from git_loopy.config import RECOMMENDED_ROUTING
from git_loopy.interactive.models import ModelChoice, Selection, default_cursor_index
from git_loopy.interactive.picker_app import ModelPickerScreen
from git_loopy.interactive.skill_picker_app import SkillPickerScreen
from git_loopy.skillscmd import SkillSelectionModel, SkillSelectionResult

__all__ = ["InitWizardApp", "run_textual_init_wizard"]

_BACK = object()
_CANCEL = object()
_CHOICES = "wizard-choices"
_REVIEW = "wizard-review"
_STATUS = "wizard-status"
_MODEL_BACK = Selection("__wizard_back__", None)
_MODEL_CANCEL = Selection("__wizard_cancel__", None)
_SKILL_BACK = SkillSelectionResult(("__wizard_back__",))
_SKILL_CANCEL = SkillSelectionResult(("__wizard_cancel__",))


class _ChoiceScreen(Screen[object]):
    """One pre-filled choice in the setup flow."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True, show=False),
        Binding("down", "cursor_down", "Down", priority=True, show=False),
        Binding("enter", "confirm", "Continue", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True, show=False),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        choices: Sequence[tuple[str, str]],
        *,
        default: int = 0,
    ) -> None:
        super().__init__()
        self._title = title
        self._choices = tuple(choices)
        self._default = default

    def compose(self) -> ComposeResult:
        yield Static(self._title)
        yield DataTable(id=_CHOICES, cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(f"#{_CHOICES}", DataTable)
        table.add_column("Choice")
        for value, label in self._choices:
            table.add_row(label, key=value)
        if table.row_count:
            table.move_cursor(row=self._default)
        table.focus()

    def action_cursor_up(self) -> None:
        self.query_one(f"#{_CHOICES}", DataTable).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one(f"#{_CHOICES}", DataTable).action_cursor_down()

    def action_confirm(self) -> None:
        table = self.query_one(f"#{_CHOICES}", DataTable)
        if table.cursor_row < 0 or not self._choices:
            return
        self.dismiss(self._choices[table.cursor_row][0])

    def action_back(self) -> None:
        self.dismiss(_BACK)

    def action_cancel(self) -> None:
        self.dismiss(_CANCEL)

    @on(DataTable.RowSelected, f"#{_CHOICES}")
    def _on_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self.dismiss(str(event.row_key.value))


class _WizardModelPickerScreen(ModelPickerScreen):
    """The shared model Screen with wizard-level escape and cancel outcomes."""

    def action_picker_back(self) -> None:
        if self._chosen is not None:
            super().action_picker_back()
        else:
            self.dismiss(_MODEL_BACK)

    def action_cancel(self) -> None:
        self.dismiss(_MODEL_CANCEL)


class _WizardSkillPickerScreen(SkillPickerScreen):
    """The shared Skill Screen with distinct wizard back and cancel actions."""

    BINDINGS = [
        Binding("space", "toggle_skill", "Toggle", priority=True),
        Binding("enter", "confirm", "Continue", priority=True),
        Binding("up", "cursor_up", "Up", priority=True, show=False),
        Binding("down", "cursor_down", "Down", priority=True, show=False),
        Binding("escape", "wizard_back", "Back", priority=True),
        Binding("ctrl+c", "wizard_cancel", "Cancel", priority=True, show=False),
        Binding("q", "wizard_cancel", "Cancel"),
    ]

    def action_wizard_back(self) -> None:
        self.dismiss(_SKILL_BACK)

    def action_wizard_cancel(self) -> None:
        self.dismiss(_SKILL_CANCEL)


@dataclass(frozen=True)
class _Review:
    scope: str
    config_path: Path
    model: str
    effort: str | None
    routing: Mapping[str, tuple[str, str]] | None
    scaffold: bool
    skills: SkillSelectionModel


class _ReviewScreen(Screen[tuple[str, str] | object]):
    """The terminal review and correction point before ``run_init`` writes."""

    BINDINGS = [
        Binding("enter", "save", "Save", priority=True),
        Binding("b", "back", "Back", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True, show=False),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(self, review: _Review) -> None:
        super().__init__()
        self._review = review

    def compose(self) -> ComposeResult:
        yield Static("Review setup — Enter Save · b Back · q Cancel")
        yield DataTable(id=_REVIEW, cursor_type="row", zebra_stripes=True)
        yield Static("", id=_STATUS)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(f"#{_REVIEW}", DataTable)
        table.add_column("Setting")
        table.add_column("Value")
        routing = self._review.routing
        routing_value = (
            "global default only"
            if routing is None
            else ", ".join(
                f"{kind}: {model} @ {effort}"
                for kind, (model, effort) in routing.items()
            )
        )
        rows = (
            ("scope", self._review.scope),
            ("model", self._review.model),
            ("effort", self._review.effort or "none"),
            ("routing", routing_value),
            ("scaffold", "PROMPT.md" if self._review.scaffold else "no prompt scaffold"),
            ("skills", f"{len(self._review.skills.enabled)} enabled"),
            ("config", str(self._review.config_path)),
        )
        for key, value in rows:
            table.add_row(key, value, key=key)
        table.focus()

    def action_save(self) -> None:
        errors = self._review.skills.validation_errors
        if errors:
            self.query_one(f"#{_STATUS}", Static).update(
                f"Cannot save: {'; '.join(errors)}."
            )
            return
        self.dismiss(("save", ""))

    def action_back(self) -> None:
        table = self.query_one(f"#{_REVIEW}", DataTable)
        index = table.cursor_row
        if not 0 <= index < table.row_count:
            index = 0
        self.dismiss(("back", str(table.get_row_at(index)[0])))

    def action_cancel(self) -> None:
        self.dismiss(_CANCEL)


class InitWizardApp(App[Any]):
    """One continuous, pre-filled setup flow over the injected answer contract."""

    TITLE = "git-loopy · setup"
    BINDINGS = [
        Binding("ctrl+s", "review", "Review", priority=True),
    ]

    def __init__(
        self,
        *,
        scope_options: Sequence[str],
        scope_paths: Mapping[str, Path],
        model_choices: Sequence[ModelChoice],
        default_model: str,
        default_effort: str | None,
        skill_selection_model: Callable[[bool, str], SkillSelectionModel],
        scope_locked: bool = False,
    ) -> None:
        super().__init__()
        self._scope_options = tuple(scope_options)
        self._scope_paths = dict(scope_paths)
        self._model_choices = tuple(model_choices)
        self._default_model = default_model
        self._default_effort = default_effort
        self._skill_selection_model = skill_selection_model
        self._scope_locked = scope_locked
        self._scope = self._scope_options[0]
        self._selection = Selection(default_model, default_effort)
        self._routing: dict[str, tuple[str, str]] | None = None
        self._scaffold = True
        self._skills: SkillSelectionModel | None = None

    def on_mount(self) -> None:
        self._show_scope_or_model()

    def action_review(self) -> None:
        """Skip directly to review; defaults make each unvisited step valid."""
        self._ensure_skills()
        self._show_review()

    def _show_scope_or_model(self) -> None:
        if self._scope_locked:
            self._show_model()
            return
        choices = tuple(
            (scope, "project (this repository)" if scope == "project" else "global (this machine)")
            for scope in self._scope_options
        )
        self.push_screen(
            _ChoiceScreen("Configure git-loopy for which scope?", choices),
            self._on_scope,
        )

    def _show_model(self) -> None:
        choices = tuple(
            replace(choice, default_effort=self._default_effort)
            if choice.id == self._default_model
            and self._default_effort in choice.supported_efforts
            else choice
            for choice in self._model_choices
        )
        self.push_screen(
            _WizardModelPickerScreen(
                choices,
                cursor=default_cursor_index(
                    choices, preferred=self._selection.model
                ),
            ),
            self._on_model,
        )

    def _show_routing(self) -> None:
        default = 0 if self._routing is not None else 1
        self.push_screen(
            _ChoiceScreen(
                "Configure per-task-type routing?",
                (
                    ("recommended", "Use all recommended task-type routes"),
                    ("none", "Use the global model and effort for every task type"),
                ),
                default=default,
            ),
            self._on_routing,
        )

    def _show_scaffold(self) -> None:
        self.push_screen(
            _ChoiceScreen(
                "Scaffold an editable PROMPT.md override?",
                (("yes", "Yes, scaffold PROMPT.md"), ("no", "No prompt scaffold")),
                default=0 if self._scaffold else 1,
            ),
            self._on_scaffold,
        )

    def _ensure_skills(self) -> None:
        if self._skills is None:
            self._skills = self._skill_selection_model(self._scaffold, self._scope)

    def _show_skills(self) -> None:
        self._skills = self._skill_selection_model(self._scaffold, self._scope)
        self.push_screen(_WizardSkillPickerScreen(self._skills), self._on_skills)

    def _show_review(self) -> None:
        self._ensure_skills()
        assert self._skills is not None
        self.push_screen(
            _ReviewScreen(
                _Review(
                    scope=self._scope,
                    config_path=self._scope_paths[self._scope],
                    model=self._selection.model,
                    effort=self._selection.effort,
                    routing=self._routing,
                    scaffold=self._scaffold,
                    skills=self._skills,
                )
            ),
            self._on_review,
        )

    def _on_scope(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is not _BACK:
            self._scope = str(result)
            self._skills = None
            self._show_model()

    def _on_model(self, result: object) -> None:
        if result is _MODEL_CANCEL:
            self.exit(None)
        elif result is _MODEL_BACK:
            self._show_scope_or_model()
        elif isinstance(result, Selection):
            self._selection = result
            self._show_routing()

    def _on_routing(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_model()
        else:
            self._routing = (
                dict(RECOMMENDED_ROUTING) if result == "recommended" else None
            )
            self._show_scaffold()

    def _on_scaffold(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_routing()
        else:
            self._scaffold = result == "yes"
            self._skills = None
            self._show_skills()

    def _on_skills(self, result: object) -> None:
        if result is _SKILL_CANCEL:
            self.exit(None)
        elif result is _SKILL_BACK:
            self._show_scaffold()
        elif isinstance(result, SkillSelectionResult):
            assert self._skills is not None
            self._skills = self._skills.__class__(
                rows=self._skills.rows,
                enabled=result.enabled,
                query=self._skills.query,
            )
            self._show_review()

    def _on_review(self, result: tuple[str, str] | object) -> None:
        if result is _CANCEL:
            self.exit(None)
            return
        if not isinstance(result, tuple):
            return
        action, target = result
        if action == "save":
            from git_loopy.init import InitAnswers

            assert self._skills is not None
            self.exit(
                InitAnswers(
                    scope=self._scope,
                    model=self._selection.model,
                    effort=self._selection.effort,
                    routing=self._routing,
                    scaffold=self._scaffold,
                    enabled_skills=self._skills.enabled,
                )
            )
            return
        if not isinstance(target, str):
            self._show_skills()
            return
        destinations: dict[str, Callable[[], None]] = {
            "scope": self._show_scope_or_model,
            "model": self._show_model,
            "effort": self._show_model,
            "routing": self._show_routing,
            "scaffold": self._show_scaffold,
            "skills": self._show_skills,
            "config": self._show_scope_or_model,
        }
        destination = destinations.get(target)
        if destination is None:
            self._show_skills()
        else:
            destination()


def run_textual_init_wizard(
    *,
    scope_options: Sequence[str],
    scope_paths: Mapping[str, Path],
    model_choices: Sequence[ModelChoice],
    default_model: str,
    default_effort: str | None,
    skill_selection_model: Callable[[bool, str], SkillSelectionModel],
    scope_locked: bool = False,
    **_ignored: object,
) -> Any:
    """Run the alternate fullscreen setup wizard and return its answer set."""
    return InitWizardApp(
        scope_options=scope_options,
        scope_paths=scope_paths,
        model_choices=model_choices,
        default_model=default_model,
        default_effort=default_effort,
        skill_selection_model=skill_selection_model,
        scope_locked=scope_locked,
    ).run()
