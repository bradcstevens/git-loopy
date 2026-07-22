"""PROTOTYPE: exercise the narrow Continuation publish-to-reconcile state order."""

from __future__ import annotations

import json


def show(state: dict[str, object]) -> None:
    print(json.dumps(state, indent=2, sort_keys=True))


def main() -> None:
    state: dict[str, object] = {
        "durable_transition_evidence": True,
        "index_established": False,
        "producer_revision_appended": False,
        "producer_revision_reread": False,
        "target_open": True,
        "guidance": "Waiting",
    }
    show(state)
    while True:
        command = input("prototype [publish/reconcile/close/quit]> ").strip()
        if command == "publish":
            if not state["durable_transition_evidence"]:
                print("rejected: transition evidence is not durable")
            else:
                state["index_established"] = True
                state["producer_revision_appended"] = True
                state["producer_revision_reread"] = True
        elif command == "reconcile":
            committed = (
                state["producer_revision_appended"]
                and state["producer_revision_reread"]
            )
            state["guidance"] = (
                "Ready" if committed and state["target_open"] else "Waiting"
            )
        elif command == "close":
            state["target_open"] = False
        elif command == "quit":
            break
        else:
            print("unknown command")
        show(state)


if __name__ == "__main__":
    main()
