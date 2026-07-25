"""``git_loopy.interactive.skill_picker_app`` — the optional Skill picker (issue #231).

The presentation half of **Skill policy** editing. The base installation's
searchable multi-select is :func:`git_loopy.skillscmd.run_plain_skill_picker`;
this is an alternate renderer and controller over the *same*
:class:`~git_loopy.skillscmd.SkillSelectionModel`, returning the same
:class:`~git_loopy.skillscmd.SkillSelectionResult` (or ``None`` on cancel). Every
operator action is applied by asking that shared model — ``filter``, ``toggle``,
``validation_errors`` — so the two pickers cannot disagree about what a valid
saved policy is, and neither one owns the validation/commit seam.

Keys: ``up``/``down`` move the cursor, ``space`` toggles the row under it,
``enter`` confirms, ``escape`` / ``ctrl+c`` cancel; anything else types into the
search box. Those five are ``priority`` bindings so they keep working while the
search box holds focus — which costs nothing, because a canonical Skill name is
``[a-z][a-z0-9]*(-[a-z0-9]+)*`` (see
:func:`git_loopy.skill_policy.is_canonical_skill_name`) and so can never contain
a space or a newline for the binding to shadow.

Filtering narrows the *view* only. The shared model keeps ``enabled`` and
``query`` apart, so a Skill selected before a search stays selected while it is
off-screen and is still in the saved policy afterwards.

This module imports Textual at the top, so — like
:mod:`git_loopy.interactive.picker_app` — it is reached only through a lazy
import (:func:`git_loopy.skillscmd.run_textual_skill_picker`), and a base
installation without the ``[tui]`` extra never loads it.
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Input, Static

from git_loopy.skillscmd import (
    SkillSelectionError,
    SkillSelectionModel,
    SkillSelectionResult,
    SkillSelectionRow,
)

__all__ = ["SkillPickerApp"]

_SEARCH = "skill-search"
_ROWS = "skill-rows"
_STATUS = "skill-status"

_COLUMNS = (
    ("", "enabled"),
    ("Skill", "name"),
    ("Source", "source"),
    ("Copilot", "copilot"),
    ("Status", "status"),
    ("Description", "description"),
)


def _copilot_state(enabled: bool | None) -> str:
    if enabled is None:
        return "unavailable"
    return "enabled" if enabled else "disabled"


def _status_cell(row: SkillSelectionRow) -> str:
    """The one column that says why a row may not be freely toggled.

    Required and blocked are stated as words rather than only as styling: a
    dimmed row explains nothing to an operator who is wondering why space did
    not work, and a blocked project winner must stay *visible* and re-selectable
    once it is tracked rather than disappear from the catalog view.
    """
    if row.required:
        return "Required"
    if row.blocked_reason is not None:
        return f"blocked: {row.blocked_reason}"
    return ""


class _SearchInput(Input):
    """Search box that declines ``space`` so it can mean "toggle".

    Textual removes an App binding from the active chain whenever the focused
    widget says it *could* consume that key (``Screen._binding_chain``), and an
    ``Input`` claims every printable character. Declining exactly one of them is
    what lets "type to search, space to toggle" work without a focus mode — and
    it costs the search nothing, because a canonical Skill name is
    ``[a-z][a-z0-9]*(-[a-z0-9]+)*`` and so can never contain a space to search
    for.
    """

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if key == "space":
            return False
        return super().check_consume_key(key, character)


class SkillPickerApp(App["SkillSelectionResult | None"]):
    """Searchable multi-select over one :class:`SkillSelectionModel`."""

    TITLE = "git-loopy · select Skills"

    CSS = """
    #skill-search {
        dock: top;
    }
    #skill-status {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        # priority: these must outrank the focused search Input, which is safe
        # because no canonical Skill name contains whitespace or a newline.
        Binding("space", "toggle_skill", "Toggle", priority=True),
        Binding("enter", "confirm", "Save", priority=True),
        Binding("up", "cursor_up", "Up", priority=True, show=False),
        Binding("down", "cursor_down", "Down", priority=True, show=False),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True, show=False),
    ]

    def __init__(self, model: SkillSelectionModel) -> None:
        super().__init__()
        #: The single source of selection truth; every action replaces it.
        self.selection = model
        #: The latest refusal or hint, read by tests and shown in the footer bar.
        self.status = ""

    def compose(self) -> ComposeResult:
        yield _SearchInput(placeholder="Search Skills", id=_SEARCH)
        yield DataTable(id=_ROWS, cursor_type="row", zebra_stripes=True)
        yield Static(id=_STATUS)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(f"#{_ROWS}", DataTable)
        for label, key in _COLUMNS:
            table.add_column(label, key=key)
        self._redraw()
        self._set_status(
            "space toggles · enter saves · esc cancels · type to search"
        )
        self.query_one(f"#{_SEARCH}", Input).focus()

    # -- rendering ---------------------------------------------------------

    def _redraw(self) -> None:
        """Repaint the visible rows, keeping the cursor on a real row."""
        table = self.query_one(f"#{_ROWS}", DataTable)
        cursor = table.cursor_row
        table.clear()
        enabled = frozenset(self.selection.enabled)
        for row in self.selection.visible_rows:
            cells: list[object] = [
                "[x]" if row.name in enabled else "[ ]",
                row.name,
                row.source,
                _copilot_state(row.copilot_enabled),
                _status_cell(row),
                row.description,
            ]
            if row.blocked_reason is not None:
                cells = [Text(str(cell), style="dim") for cell in cells]
            table.add_row(*cells, key=row.name)
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    def _set_status(self, message: str) -> None:
        self.status = message
        self.query_one(f"#{_STATUS}", Static).update(message)

    def _row_under_cursor(self) -> SkillSelectionRow | None:
        table = self.query_one(f"#{_ROWS}", DataTable)
        visible = self.selection.visible_rows
        index = table.cursor_row
        if not visible or not 0 <= index < len(visible):
            return None
        return visible[index]

    # -- search ------------------------------------------------------------

    @on(Input.Changed, f"#{_SEARCH}")
    def _on_search_changed(self, event: Input.Changed) -> None:
        # Filtering goes through the shared model, which keeps `enabled`
        # independent of `query` — so nothing selected is lost off-screen.
        self.selection = self.selection.filter(event.value)
        self._redraw()
        if not self.selection.visible_rows:
            self._set_status("No matching Skills.")

    # -- actions -----------------------------------------------------------

    def action_cursor_up(self) -> None:
        self.query_one(f"#{_ROWS}", DataTable).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one(f"#{_ROWS}", DataTable).action_cursor_down()

    def action_toggle_skill(self) -> None:
        row = self._row_under_cursor()
        if row is None:
            return
        try:
            self.selection = self.selection.toggle(row.name)
        except SkillSelectionError as exc:
            self._set_status(f"Cannot toggle: {exc}.")
            return
        self._redraw()
        state = "enabled" if row.name in self.selection.enabled else "disabled"
        self._set_status(f"{row.name} {state}.")

    def action_confirm(self) -> None:
        errors = self.selection.validation_errors
        if errors:
            self._set_status(f"Cannot save: {'; '.join(errors)}.")
            return
        self.exit(SkillSelectionResult(self.selection.enabled))

    def action_cancel(self) -> None:
        """esc / Ctrl+C: the caller writes nothing at all."""
        self.exit(None)
