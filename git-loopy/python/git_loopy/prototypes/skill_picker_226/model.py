"""PROTOTYPE: pure state model for the shared Skill-policy picker."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Row:
    name: str
    required: bool = False
    blocked: str | None = None


@dataclass(frozen=True)
class State:
    rows: tuple[Row, ...]
    enabled: frozenset[str]
    query: str = ""

    @property
    def visible(self) -> tuple[Row, ...]:
        query = self.query.casefold()
        return tuple(row for row in self.rows if query in row.name.casefold())

    @property
    def errors(self) -> tuple[str, ...]:
        errors = [
            f"{row.name} is Required"
            for row in self.rows
            if row.required and row.name not in self.enabled
        ]
        errors.extend(
            f"{row.name} is blocked: {row.blocked}"
            for row in self.rows
            if row.name in self.enabled and row.blocked is not None
        )
        return tuple(errors)


def filter_rows(state: State, query: str) -> State:
    return replace(state, query=query.strip())


def toggle(state: State, name: str) -> State:
    row = next(row for row in state.rows if row.name == name)
    enabled = set(state.enabled)
    if name in enabled:
        if row.required:
            return state
        enabled.remove(name)
    elif row.blocked is None:
        enabled.add(name)
    return replace(state, enabled=frozenset(enabled))
