"""PROTOTYPE: one normalized Iteration payload as the accounting authority.

Question: can serial and Parallel activity produce one finalized payload that
both the live Summary and persistence consume without independently rebuilding
accounting or issue contributions?
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def initial_state() -> dict[str, Any]:
    return {
        "next_iter": 1,
        "events": [],
        "finalized": [],
        "live_summary": [],
        "persisted_rows": [],
    }


def reduce(state: dict[str, Any], action: str) -> dict[str, Any]:
    state = deepcopy(state)
    iter_num = state["next_iter"]
    if action == "serial":
        issues = [{"issue": 177, "status": "closed", "tokens_in": 80}]
        outcome = "closed"
    elif action == "parallel":
        issues = [
            {"issue": 177, "status": "advanced", "tokens_in": 80},
            {"issue": 178, "status": "closed", "tokens_in": 120},
        ]
        outcome = "parallel"
    elif action == "empty":
        issues = []
        outcome = "no_progress"
    elif action == "reset":
        return initial_state()
    else:
        return state

    payload = {
        "iter": iter_num,
        "outcome": outcome,
        "summary": {
            "tokens_in": sum(issue["tokens_in"] for issue in issues),
            "strikes": 0 if any(issue["status"] == "closed" for issue in issues) else 1,
        },
        "issues": issues,
    }
    state["events"].append({"type": "wrapper.iteration.end", **payload})
    state["finalized"].append(payload)
    state["live_summary"].append(payload)
    state["persisted_rows"].append(payload)
    state["next_iter"] += 1
    return state


def authority_is_shared(state: dict[str, Any]) -> bool:
    return (
        state["finalized"] == state["live_summary"]
        and state["finalized"] == state["persisted_rows"]
    )
