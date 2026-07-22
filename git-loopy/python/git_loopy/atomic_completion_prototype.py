"""PROTOTYPE: exercise atomic completion validation before publication.

Question: can one parse/validate boundary keep every invalid envelope from
reaching GitHub while separating full-record identity from Action semantics?
"""

from __future__ import annotations

import copy
import hashlib
import json


BASE = {
    "disposition": "continue",
    "actions": [
        {
            "instruction": {"mode": "skill", "value": "/to-spec 237"},
            "prerequisites": [],
            "interaction": {
                "classification": "AFK-safe",
                "evidence": {"kind": "transition-owner-attestation"},
            },
            "completion_condition": {"kind": "issue-closed"},
            "basis": [{"kind": "issue", "number": 237}],
            "summary": "Publish the specification",
        }
    ],
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def validate(value: dict[str, object]) -> tuple[str, str]:
    actions = value.get("actions")
    if value.get("disposition") != "continue" or not isinstance(actions, list):
        raise ValueError("invalid envelope")
    action = actions[0]
    if not isinstance(action, dict) or not action.get("basis"):
        raise ValueError("invalid Action")
    semantics = {
        key: action[key]
        for key in (
            "instruction",
            "prerequisites",
            "interaction",
            "completion_condition",
        )
    }
    return (
        hashlib.sha256(canonical(value)).hexdigest(),
        hashlib.sha256(canonical(semantics)).hexdigest(),
    )


def main() -> None:
    state: dict[str, object] = {"github_calls": 0}
    while True:
        choice = input("prototype [valid/presentation/partial/quit]> ").strip()
        if choice == "quit":
            break
        request = copy.deepcopy(BASE)
        if choice == "presentation":
            request["actions"][0]["summary"] = "Reworded presentation"
        elif choice == "partial":
            request["actions"][0]["basis"] = []
        elif choice != "valid":
            print("unknown command")
            continue
        try:
            revision_id, semantic_fingerprint = validate(request)
        except ValueError as exc:
            state["result"] = f"rejected before GitHub: {exc}"
        else:
            state["github_calls"] = int(state["github_calls"]) + 1
            state["result"] = "accepted"
            state["revision_id"] = revision_id
            state["semantic_fingerprint"] = semantic_fingerprint
        print(json.dumps(state, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
