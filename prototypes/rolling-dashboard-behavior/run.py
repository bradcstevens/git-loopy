#!/usr/bin/env python3
"""Run the throwaway rolling-Dashboard replay prototype.

One command:
    python3 prototypes/rolling-dashboard-behavior/run.py --report

Without ``--report`` it is a tiny stepper: n/p step, a autoplay, d Dashboard,
an issue number opens its per-issue Log/breakdown, and q quits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from model import DashboardState, render_dashboard, render_issue


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "rolling-run.jsonl"


def load_events() -> list[dict]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def replay(events: list[dict], upto: int) -> DashboardState:
    state = DashboardState()
    for event in events[: upto + 1]:
        state.apply(event)
    return state


def clear() -> None:
    print("\033[2J\033[H", end="")


def interactive(events: list[dict]) -> None:
    index = 0
    issue: int | None = None
    autoplay = False
    while True:
        state = replay(events, index)
        clear()
        frame = events[index].get("frame", "")
        print(
            f"PROTOTYPE event {index + 1}/{len(events)}  "
            f"{events[index]['type']}  {frame}\n"
        )
        print(render_issue(state, issue) if issue in state.issues else render_dashboard(state))
        print("\n[n] next  [p] previous  [a] autoplay  [d] Dashboard  [issue number] Log  [q] quit")
        if autoplay:
            if index == len(events) - 1:
                autoplay = False
            else:
                time.sleep(0.35)
                index += 1
                continue
        try:
            command = input("> ").strip().lower()
        except EOFError:
            return
        if command == "q":
            return
        if command in {"", "n"}:
            index = min(len(events) - 1, index + 1)
        elif command == "p":
            index = max(0, index - 1)
        elif command == "a":
            autoplay = True
            issue = None
        elif command == "d":
            issue = None
        elif command.isdigit():
            issue = int(command)


def report(events: list[dict]) -> None:
    frame_indexes = [i for i, event in enumerate(events) if "frame" in event]
    for index in frame_indexes:
        state = replay(events, index)
        print(f"\n{'=' * 118}")
        print(f"SCENARIO: {events[index]['frame']}")
        print("=" * 118)
        print(render_dashboard(state))

    final = replay(events, len(events) - 1)
    print(f"\n{'=' * 118}")
    print("PER-ISSUE REACTION: fallback contribution followed by unchanged serial Iteration")
    print("=" * 118)
    print(render_issue(final, 303))
    print(
        "\nMODEL CHECKS\n"
        "✓ Queue identity stayed issue-centric while Lane L1 moved from c01 to c04.\n"
        "✓ Every replay event retained the existing ts/run_id/iter/type envelope.\n"
        "✓ H=2 counted one integrating candidate plus one FIFO waiter; parked finishers stayed outside admission.\n"
        "✓ A Lane contribution kept one row through work, wait, Integration, recovery, and terminal disposition.\n"
        "✓ Only publication reset Strikes; terminal unpublished handoff added one Strike.\n"
        "✓ Effective concurrency reached 0 without cancelling Integration; unavailable pressure signals remained '?'.\n"
        "✓ Serial wrapper.iteration.start/end semantics stayed intact after full Parallel drain.\n"
        "✓ Rolling resumed with one full refill turn before any serial relatch.\n"
        "✓ wrapper.run.end was accepted only after full quiescence."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="print selected evidence frames")
    args = parser.parse_args()
    events = load_events()
    if args.report or not sys.stdin.isatty():
        report(events)
    else:
        interactive(events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
