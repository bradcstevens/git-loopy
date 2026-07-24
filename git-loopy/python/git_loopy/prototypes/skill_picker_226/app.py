"""PROTOTYPE - shared Skill picker selection model.

Question: can one immutable model preserve selections outside the current search
filter while preventing Required or untracked project rows from producing a
saveable invalid Skill policy, independent of the terminal renderer?
"""

from __future__ import annotations

import json

from .model import Row, State, filter_rows, toggle


def _render(state: State) -> None:
    print("\033[2J\033[H", end="")
    print("\033[1mPROTOTYPE: Skill policy selection\033[0m")
    print(
        json.dumps(
            {
                "query": state.query,
                "enabled": sorted(state.enabled),
                "visible": [row.name for row in state.visible],
                "validation_errors": state.errors,
            },
            indent=2,
        )
    )
    for index, row in enumerate(state.visible, start=1):
        flags = []
        if row.required:
            flags.append("Required")
        if row.blocked:
            flags.append(f"blocked: {row.blocked}")
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(f"{index}. [{'x' if row.name in state.enabled else ' '}] {row.name}{suffix}")
    print(
        "\n\033[1m<number>\033[0m toggle  \033[1m<text>\033[0m filter  "
        "\033[1mc\033[0m clear  \033[1md\033[0m done  \033[1mq\033[0m quit"
    )


def main() -> None:
    state = State(
        rows=(
            Row("code-review", required=True),
            Row("personal-helper"),
            Row("prototype", required=True),
            Row("project-local", blocked="project Skill is not git-tracked"),
        ),
        enabled=frozenset({"code-review", "personal-helper", "prototype"}),
    )
    while True:
        _render(state)
        answer = input("> ").strip()
        if answer.casefold() == "q":
            return
        if answer.casefold() == "c":
            state = filter_rows(state, "")
            continue
        if answer.casefold() == "d":
            if not state.errors:
                print(f"Saveable: {', '.join(sorted(state.enabled))}")
                return
            input("Not saveable; press Enter to continue.")
            continue
        try:
            selected = int(answer) - 1
        except ValueError:
            state = filter_rows(state, answer)
            continue
        if 0 <= selected < len(state.visible):
            state = toggle(state, state.visible[selected].name)


if __name__ == "__main__":
    main()
