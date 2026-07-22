"""Throwaway interactive terminal shell for issue #212.

Run from ``git-loopy/python``:

    uv run git-loopy-prototype-continuation

Use ``--snapshot all`` to print every scenario and refresh phase without an
interactive terminal.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap
from dataclasses import dataclass
from typing import Iterable

from .model import (
    Artifact,
    ContinuationAction,
    ObservationState,
    Projection,
    build_projection,
)
from .scenarios import SCENARIOS, SCENARIO_BY_KEY, Scenario

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
REVERSE = "\x1b[7m"


@dataclass(frozen=True)
class Entry:
    section: str
    action: ContinuationAction


def _style(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{RESET}" if enabled else text


def _wrap(label: str, value: str, width: int) -> list[str]:
    prefix = f"  {label}: "
    available = max(30, width - len(prefix))
    chunks = textwrap.wrap(
        value,
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    return [prefix + chunks[0], *(" " * len(prefix) + chunk for chunk in chunks[1:])]


def _artifact_text(artifact: Artifact) -> str:
    text = f"{artifact.role} {artifact.locator}"
    if artifact.url:
        text += f" <{artifact.url}>"
    if artifact.note:
        text += f" ({artifact.note})"
    return text


def _status(action: ContinuationAction) -> str:
    if action.observation_state is not ObservationState.VERIFIED:
        return action.attention_label or action.observation_state.value.upper()
    return "BLOCKED" if action.blockers else "READY"


def _entry_line(
    number: int,
    entry: Entry,
    *,
    selected: bool,
    color: bool,
    width: int,
) -> list[str]:
    action = entry.action
    marker = ">" if selected else " "
    status = _status(action)
    interaction = action.interaction.value
    primary = (
        f"{marker} {number}. {status:<8} {interaction:<8} "
        f"{action.title} [{action.kind}]"
    )
    lines = [primary[:width]]
    if action.blockers:
        blockers = ", ".join(blocker.locator for blocker in action.blockers)
        lines.extend(_wrap("waits for", blockers, width))
    elif action.observation_state is not ObservationState.VERIFIED:
        lines.extend(_wrap("diagnostic", action.attention_detail or "Needs refresh.", width))
    else:
        lines.extend(_wrap("target", action.target.locator, width))
    if selected:
        lines[0] = _style(lines[0], REVERSE, color)
    return lines


def _section(
    title: str,
    entries: Iterable[Entry],
    *,
    start: int,
    selected_index: int,
    color: bool,
    width: int,
) -> tuple[list[str], int]:
    materialized = tuple(entries)
    if not materialized:
        return [], start
    lines = ["", _style(title, BOLD, color)]
    index = start
    for entry in materialized:
        lines.extend(
            _entry_line(
                index + 1,
                entry,
                selected=index == selected_index,
                color=color,
                width=width,
            )
        )
        index += 1
    return lines, index


def _detail(entry: Entry, *, color: bool, width: int) -> list[str]:
    action = entry.action
    lines = ["", _style("SELECTED", BOLD, color)]
    if action.observation_state is ObservationState.VERIFIED:
        command_label = "COMMAND" if not action.blockers else "COMMAND WHEN READY"
        lines.extend(
            [
                _style(command_label, BOLD, color),
                f"  {action.instruction}",
            ]
        )
    else:
        lines.extend(
            _wrap(
                "Diagnostic",
                action.attention_detail or "This scope is outside actionable ordering.",
                width,
            )
        )
    lines.extend(_wrap("Why now", action.why_now, width))
    lines.extend(_wrap("Target", _artifact_text(action.target), width))
    if action.supporting:
        context = " | ".join(_artifact_text(item) for item in action.supporting)
        lines.extend(_wrap("Context", context, width))
    lines.extend(_wrap("Identity", action.identity, width))
    return lines


def render(
    scenario: Scenario,
    frame_index: int,
    *,
    selected_index: int = 0,
    expanded: bool = False,
    color: bool = True,
    width: int | None = None,
) -> tuple[str, tuple[Entry, ...]]:
    width = width or min(110, max(72, shutil.get_terminal_size((100, 30)).columns))
    snapshot = scenario.frames[frame_index]
    projection: Projection = build_projection(snapshot, expanded=expanded)

    ready_entries = tuple(Entry("READY", action) for action in projection.visible_ready)
    blocked_entries = tuple(
        Entry("BLOCKED", action) for action in projection.visible_blocked
    )
    attention_entries = tuple(
        Entry("NEEDS ATTENTION", action) for action in projection.visible_attention
    )
    entries = ready_entries + blocked_entries + attention_entries
    if entries:
        selected_index = max(0, min(selected_index, len(entries) - 1))
    else:
        selected_index = 0

    lines = [
        _style("CONTINUATION PROTOTYPE #212", BOLD, color)
        + " "
        + _style("[FIXTURE DATA - NO WRITES]", DIM, color),
        f"Scenario: {scenario.title}",
        f"Phase:    {snapshot.phase}",
        (
            f"Observed: {snapshot.observed_at} | source {snapshot.source_revision} | "
            f"{snapshot.active_workstreams} active Workstream(s)"
        ),
        (
            f"State:    {len(projection.ready)} Ready | "
            f"{len(projection.blocked)} Blocked | "
            f"{len(projection.attention)} Needs attention | "
            f"{len(snapshot.outcomes)} outcome(s)"
        ),
        (
            f"Refresh:  +{snapshot.delta.added} added | "
            f"-{snapshot.delta.retired} retired | "
            f"{snapshot.delta.changed} changed - {snapshot.delta.note}"
        ),
    ]

    if projection.hitl_stop:
        lines.extend(["", _style("HITL STOP", BOLD, color) + f" - {projection.hitl_stop}"])
    if projection.project_complete:
        lines.extend(
            [
                "",
                _style("COMPLETE", BOLD, color)
                + " - Every Workstream has an explicit, destination-satisfied outcome.",
            ]
        )
    elif snapshot.waiting_notice:
        lines.extend(
            [
                "",
                _style("WAITING", BOLD, color) + f" - {snapshot.waiting_notice}",
            ]
        )

    index = 0
    section_lines, index = _section(
        "READY",
        ready_entries,
        start=index,
        selected_index=selected_index,
        color=color,
        width=width,
    )
    lines.extend(section_lines)
    section_lines, index = _section(
        "BLOCKED",
        blocked_entries,
        start=index,
        selected_index=selected_index,
        color=color,
        width=width,
    )
    lines.extend(section_lines)
    section_lines, index = _section(
        "NEEDS ATTENTION",
        attention_entries,
        start=index,
        selected_index=selected_index,
        color=color,
        width=width,
    )
    lines.extend(section_lines)

    hidden_actions = projection.hidden_ready + projection.hidden_blocked
    if hidden_actions:
        lines.append(
            _style(
                f"  {hidden_actions} additional verified action(s) hidden; press [a] to expand.",
                DIM,
                color,
            )
        )
    if projection.hidden_attention:
        lines.append(
            _style(
                f"  {projection.hidden_attention} additional diagnostic(s) hidden; press [a] to expand.",
                DIM,
                color,
            )
        )

    if snapshot.retirements:
        receipt = snapshot.retirements[0]
        replacement = (
            f" -> {receipt.replacement_identity}" if receipt.replacement_identity else ""
        )
        lines.extend(
            [
                "",
                _style("RETIRED THIS REFRESH", BOLD, color),
                (
                    f"  {receipt.action_title}: {receipt.reason} via "
                    f"{receipt.evidence.locator}{replacement}"
                ),
            ]
        )
        if len(snapshot.retirements) > 1:
            lines.append(f"  +{len(snapshot.retirements) - 1} more retirement receipt(s)")

    if snapshot.outcomes:
        lines.extend(["", _style("OUTCOMES", BOLD, color)])
        for outcome in snapshot.outcomes:
            satisfied = "destination satisfied" if outcome.destination_satisfied else "closed"
            lines.append(
                f"  {outcome.disposition.value.upper():<10} {outcome.title} - "
                f"{satisfied} via {outcome.evidence.locator}"
            )

    if entries:
        lines.extend(_detail(entries[selected_index], color=color, width=width))

    frame_number = frame_index + 1
    lines.extend(
        [
            "",
            _style(
                f"[j/k] select  [r] refresh ({frame_number}/{len(scenario.frames)})  "
                "[a] compact/all  [1-7] scenario  [q] quit",
                DIM,
                color,
            ),
        ]
    )
    return "\n".join(lines), entries


def _snapshot(name: str, *, expanded: bool) -> int:
    scenarios = SCENARIOS if name == "all" else (SCENARIO_BY_KEY[name],)
    chunks: list[str] = []
    for scenario in scenarios:
        for frame_index in range(len(scenario.frames)):
            rendered, _ = render(
                scenario,
                frame_index,
                expanded=expanded,
                color=False,
                width=100,
            )
            chunks.append(rendered)
    print("\n" + ("\n\n" + "=" * 100 + "\n\n").join(chunks))
    return 0


def _interactive() -> int:
    scenario_index = 0
    frame_index = 0
    selected_index = 0
    expanded = False

    while True:
        scenario = SCENARIOS[scenario_index]
        rendered, entries = render(
            scenario,
            frame_index,
            selected_index=selected_index,
            expanded=expanded,
            color=sys.stdout.isatty(),
        )
        print("\033[2J\033[H", end="")
        print(rendered)
        command = input("> ").strip().lower()

        if command in {"q", "quit", "exit"}:
            return 0
        if command in {"j", "down"} and entries:
            selected_index = min(selected_index + 1, len(entries) - 1)
        elif command in {"k", "up"} and entries:
            selected_index = max(selected_index - 1, 0)
        elif command in {"r", "refresh"}:
            frame_index = min(frame_index + 1, len(scenario.frames) - 1)
            selected_index = 0
        elif command in {"a", "all"}:
            expanded = not expanded
            selected_index = 0
        elif command.isdigit() and 1 <= int(command) <= len(SCENARIOS):
            scenario_index = int(command) - 1
            frame_index = 0
            selected_index = 0
            expanded = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        choices=("all", *SCENARIO_BY_KEY),
        help="print all phases for one scenario, or every scenario",
    )
    parser.add_argument(
        "--expanded",
        action="store_true",
        help="show every current Action and diagnostic in snapshot mode",
    )
    args = parser.parse_args(argv)
    if args.snapshot:
        return _snapshot(args.snapshot, expanded=args.expanded)
    return _interactive()


if __name__ == "__main__":
    raise SystemExit(main())
