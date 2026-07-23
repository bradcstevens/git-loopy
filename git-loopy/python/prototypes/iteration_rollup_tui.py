"""Run with:

uv run --project git-loopy/python python git-loopy/python/prototypes/iteration_rollup_tui.py
"""

from __future__ import annotations

import json

from iteration_rollup_logic import authority_is_shared, initial_state, reduce


def render(state: dict[str, object]) -> None:
    print("\033[2J\033[H", end="")
    print("\033[1mPROTOTYPE: normalized Iteration authority\033[0m")
    print(json.dumps(state, indent=2))
    verdict = "shared" if authority_is_shared(state) else "DRIFTED"
    print(f"\n\033[1mAuthority:\033[0m {verdict}")
    print(
        "\n\033[1ms\033[0m serial  \033[1mp\033[0m parallel  "
        "\033[1me\033[0m empty  \033[1mr\033[0m reset  \033[1mq\033[0m quit"
    )


def main() -> None:
    state = initial_state()
    while True:
        render(state)
        choice = input("> ").strip().lower()
        if choice == "q":
            return
        state = reduce(
            state,
            {
                "s": "serial",
                "p": "parallel",
                "e": "empty",
                "r": "reset",
            }.get(choice, ""),
        )


if __name__ == "__main__":
    main()
