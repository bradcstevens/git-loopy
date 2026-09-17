"""``git_loopy.interactive.init_wizard_app`` — the setup wizard (issue #506).

One continuous, keyboard-driven walk through every question ``git-loopy init``
asks — scope, model, reasoning effort, per-task-type routing, prompt scaffold,
and **Skill policy** — ending on a review screen over ``Save`` / ``Back`` /
``Cancel``. It is an *alternate* runner behind the single wizard-runner seam
:func:`git_loopy.init.run_init` already owns (issue #504), selected by
:func:`git_loopy.init.select_wizard_runner`; issue #508 makes it the default and
deletes the numbered renderers it replaces.

Keys: ``up``/``down`` move, ``space`` toggles a Skill, ``enter`` advances or
confirms, ``esc`` goes back one step — and cancels on the first step, where
there is nowhere back to — and ``ctrl+c`` always cancels outright. ``ctrl+s``
jumps straight to review, which is answerable because every step is pre-filled
with its default.

The model and Skill steps *compose* the shared Screens
(:class:`~git_loopy.interactive.picker_app.ModelPickerScreen`,
:class:`~git_loopy.interactive.skill_picker_app.SkillPickerScreen`) rather than
reimplementing selection, so the wizard and the standalone pickers cannot
disagree; each subclass here redeclares only the keys whose *meaning* changes.
The effort step is the composed Screen's own auto-skip: a model that supports no
reasoning effort dismisses with ``effort=None``.

Interface state — step order, which answer is current, the review cursor — lives
in :class:`InitWizardApp`. There is no second model mirroring the widget tree:
the **Skill policy**'s validity is still asked of
:class:`~git_loopy.skillscmd.SkillSelectionModel`, which is a domain model rather
than a widget one. Nothing here reads :mod:`git_loopy.interactive.state`, which
is what keeps a later Dashboard-hosted wizard from coupling setup to the Run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Static

from git_loopy.config import RECOMMENDED_ROUTING
from git_loopy.interactive.models import ModelChoice, Selection, default_cursor_index
from git_loopy.interactive.picker_app import ModelPickerScreen
from git_loopy.interactive.skill_picker_app import SkillPickerScreen
from git_loopy.skillscmd import SkillSelectionModel, SkillSelectionResult

if TYPE_CHECKING:
    from git_loopy.init import InitAnswers

__all__ = ["InitWizardApp", "run_textual_init_wizard"]

_BACK = object()
_CANCEL = object()
_CHOICES = "wizard-choices"
_REVIEW = "wizard-review"
_STATUS = "wizard-status"


class _ChoiceScreen(Screen[object]):
    """A prefilled single-choice wizard step."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True, show=False),
        Binding("down", "cursor_down", "Down", priority=True, show=False),
        Binding("enter", "confirm", "Continue", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True, show=False),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(
        self, title: str, choices: Sequence[tuple[str, str]], *, default: int = 0
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
        if 0 <= table.cursor_row < len(self._choices):
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
    """The shared model Screen with wizard-level back and cancel results."""

    def action_picker_back(self) -> None:
        if self.at_model_stage:
            self.dismiss(_BACK)
        else:
            super().action_picker_back()

    def action_cancel(self) -> None:
        self.dismiss(_CANCEL)


class _WizardSkillPickerScreen(SkillPickerScreen):
    """The shared Skill Screen with wizard-level back and cancel results.

    Only the two keys whose *meaning* changes are redeclared. ``space``,
    ``enter``, ``up``, ``down`` and "anything else types into the search box"
    are inherited, so the Skill step cannot drift from the standalone editor —
    and no key the base leaves free to the search box is claimed here.
    """

    BINDINGS = [
        Binding("escape", "wizard_back", "Back", priority=True),
        Binding("ctrl+c", "wizard_cancel", "Cancel", priority=True, show=False),
    ]

    def action_wizard_back(self) -> None:
        self.dismiss(_BACK)

    def action_wizard_cancel(self) -> None:
        self.dismiss(_CANCEL)


@dataclass(frozen=True)
class _Review:
    scope: str
    config_path: Path
    model: str
    effort: str | None
    routing: Mapping[str, tuple[str, str]] | None
    scaffold: bool
    skills: SkillSelectionModel


def _describe_routing(routing: Mapping[str, tuple[str, str]] | None) -> str:
    if routing is None:
        return "disabled (existing routes are preserved)"
    if not routing:
        return "no task type routed"
    return ", ".join(
        f"{kind}: {model} @ {effort}" for kind, (model, effort) in routing.items()
    )


class _ReviewScreen(Screen["tuple[str, str] | object"]):
    """The collect-then-commit terminus and correction point.

    A full-screen application erases itself on exit, so this is the only place
    the guarantee that nothing is written until every answer is in becomes
    something an operator can see rather than a comment in :mod:`git_loopy.init`.
    """

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
        #: Each review line as ``(step, label, value)``. ``step`` is what a
        #: ``Back`` resolves to, so the wizard routes on the step the row was
        #: drawn *for* rather than on the text that happened to be rendered.
        self._lines: tuple[tuple[str, str, str], ...] = (
            ("scope", "scope", review.scope),
            ("scope", "config", str(review.config_path)),
            ("model", "model", review.model),
            ("model", "effort", review.effort or "none"),
            ("routing", "routing", _describe_routing(review.routing)),
            (
                "scaffold",
                "scaffold",
                "PROMPT.md" if review.scaffold else "no prompt scaffold",
            ),
            ("skills", "skills", f"{len(review.skills.enabled)} enabled"),
        )

    def compose(self) -> ComposeResult:
        yield Static("Review setup — nothing is written until you save")
        yield DataTable(id=_REVIEW, cursor_type="row", zebra_stripes=True)
        yield Static("", id=_STATUS)
        yield Button("Save", id="wizard-save", variant="success")
        yield Button("Back", id="wizard-back")
        yield Button("Cancel", id="wizard-cancel", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(f"#{_REVIEW}", DataTable)
        table.add_column("Setting")
        table.add_column("Value")
        for _step, label, value in self._lines:
            table.add_row(label, value, key=label)
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
        index = min(max(table.cursor_row, 0), len(self._lines) - 1)
        self.dismiss(("back", self._lines[index][0]))

    def action_cancel(self) -> None:
        self.dismiss(_CANCEL)

    @on(Button.Pressed, "#wizard-save")
    def _save_button(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#wizard-back")
    def _back_button(self) -> None:
        self.action_back()

    @on(Button.Pressed, "#wizard-cancel")
    def _cancel_button(self) -> None:
        self.action_cancel()


class InitWizardApp(App["InitAnswers | None"]):
    """One continuous, prefilled setup flow over the ``WizardRunner`` seam."""

    TITLE = "git-loopy · setup"
    BINDINGS = [Binding("ctrl+s", "review", "Review", priority=True)]

    def __init__(
        self,
        *,
        scope_options: Sequence[str],
        scope_paths: Mapping[str, Path],
        model_choices: Sequence[ModelChoice],
        default_model: str,
        default_effort: str | None,
        build_skill_selection: Callable[[bool, str], SkillSelectionModel],
        scope_locked: bool = False,
    ) -> None:
        super().__init__()
        self._scope_options = tuple(scope_options)
        self._scope_paths = dict(scope_paths)
        self._model_choices = tuple(model_choices)
        self._default_model = default_model
        self._default_effort = default_effort
        self._build_skill_selection = build_skill_selection
        self._scope_locked = scope_locked
        self._scope = self._scope_options[0]
        self._selection = self._initial_selection()
        self._routing: dict[str, tuple[str, str]] | None = None
        self._route_index = 0
        self._scaffold = True
        self._skills: SkillSelectionModel | None = None

    def _initial_selection(self) -> Selection:
        index = default_cursor_index(self._model_choices, preferred=self._default_model)
        choice = self._model_choices[index]
        if not choice.selectable:
            choice = next(option for option in self._model_choices if option.selectable)
        effort = (
            self._default_effort
            if choice.id == self._default_model
            and self._default_effort in choice.supported_efforts
            else choice.default_effort
        )
        return Selection(choice.id, effort)

    def on_mount(self) -> None:
        self._show_scope_or_model()

    def action_review(self) -> None:
        """Reach review directly because every setup question has a default."""
        if isinstance(self.screen, _ReviewScreen):
            return
        self._ensure_skills()
        self._show_review()

    def _show_scope_or_model(self) -> None:
        if self._scope_locked:
            self._show_model()
            return
        choices = tuple(
            (
                scope,
                "project (this repository)" if scope == "project" else "global (this machine)",
            )
            for scope in self._scope_options
        )
        self.push_screen(
            _ChoiceScreen(
                "Configure git-loopy for which scope?",
                choices,
                default=self._scope_options.index(self._scope),
            ),
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
                cursor=default_cursor_index(choices, preferred=self._selection.model),
            ),
            self._on_model,
        )

    def _show_routing(self) -> None:
        recommended = dict(RECOMMENDED_ROUTING)
        default = 0 if self._routing == recommended else 1 if self._routing is None else 2
        self.push_screen(
            _ChoiceScreen(
                "Configure per-task-type routing?",
                (
                    ("recommended", "Use all recommended task-type routes"),
                    ("disabled", "Do not configure routing"),
                    ("custom", "Configure each task type"),
                ),
                default=default,
            ),
            self._on_routing,
        )

    def _show_route_action(self) -> None:
        key, (model, effort) = tuple(RECOMMENDED_ROUTING.items())[self._route_index]
        configured = None if self._routing is None else self._routing.get(key)
        default = 0 if configured == (model, effort) else 1 if configured is None else 2
        self.push_screen(
            _ChoiceScreen(
                f"task-type:{key} ({model} @ {effort}):",
                (
                    ("keep", "Keep recommended"),
                    ("skip", "Do not configure this task type"),
                    ("override", "Choose another model and effort"),
                ),
                default=default,
            ),
            self._on_route_action,
        )

    def _show_route_model(self) -> None:
        key, (model, effort) = tuple(RECOMMENDED_ROUTING.items())[self._route_index]
        choices = tuple(choice for choice in self._model_choices if choice.supported_efforts)
        self.push_screen(
            _WizardModelPickerScreen(
                choices,
                cursor=default_cursor_index(choices, preferred=model),
            ),
            self._on_route_model,
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
            self._skills = self._build_skill_selection(self._scaffold, self._scope)

    def _show_skills(self) -> None:
        self._ensure_skills()
        assert self._skills is not None
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
        if result in (_BACK, _CANCEL):
            self.exit(None)
        else:
            self._scope = str(result)
            self._skills = None
            self._show_model()

    def _on_model(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            if self._scope_locked:
                self.exit(None)
            else:
                self._show_scope_or_model()
        elif isinstance(result, Selection):
            self._selection = result
            self._show_routing()

    def _on_routing(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_model()
        elif result == "recommended":
            self._routing = dict(RECOMMENDED_ROUTING)
            self._show_scaffold()
        elif result == "disabled":
            self._routing = None
            self._show_scaffold()
        else:
            self._routing = self._routing or {}
            self._route_index = 0
            self._show_route_action()

    def _advance_route(self) -> None:
        self._route_index += 1
        if self._route_index == len(RECOMMENDED_ROUTING):
            self._show_scaffold()
        else:
            self._show_route_action()

    def _on_route_action(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_routing()
        elif result == "override":
            self._show_route_model()
        else:
            assert self._routing is not None
            key, recommended = tuple(RECOMMENDED_ROUTING.items())[self._route_index]
            if result == "keep":
                self._routing[key] = recommended
            else:
                self._routing.pop(key, None)
            self._advance_route()

    def _on_route_model(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_route_action()
        elif isinstance(result, Selection):
            assert result.effort is not None
            assert self._routing is not None
            key = tuple(RECOMMENDED_ROUTING)[self._route_index]
            self._routing[key] = (result.model, result.effort)
            self._advance_route()

    def _on_scaffold(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_routing()
        else:
            scaffold = result == "yes"
            if scaffold != self._scaffold:
                self._skills = None
            self._scaffold = scaffold
            self._show_skills()

    def _on_skills(self, result: object) -> None:
        if result is _CANCEL:
            self.exit(None)
        elif result is _BACK:
            self._show_scaffold()
        elif isinstance(result, SkillSelectionResult):
            assert self._skills is not None
            self._skills = replace(self._skills, enabled=result.enabled)
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
        destinations: dict[str, Callable[[], None]] = {
            "scope": self._show_scope_or_model,
            "model": self._show_model,
            "routing": self._show_routing,
            "scaffold": self._show_scaffold,
            "skills": self._show_skills,
        }
        destinations.get(target, self._show_skills)()


def run_textual_init_wizard(
    *,
    scope_options: Sequence[str],
    scope_paths: Mapping[str, Path],
    model_choices: Sequence[ModelChoice],
    default_model: str,
    default_effort: str | None,
    rebuild_skill_selection: Callable[..., tuple[str, ...]],
    skill_selection_model: Callable[[bool, str], SkillSelectionModel],
    scope_locked: bool = False,
) -> InitAnswers | None:
    """Run the alternate fullscreen setup wizard and return its answer set."""
    del rebuild_skill_selection
    return InitWizardApp(
        scope_options=scope_options,
        scope_paths=scope_paths,
        model_choices=model_choices,
        default_model=default_model,
        default_effort=default_effort,
        build_skill_selection=skill_selection_model,
        scope_locked=scope_locked,
    ).run()
