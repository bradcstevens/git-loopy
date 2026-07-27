"""Static guard: this repository can gate its own Integration.

git-loopy dogfoods itself — an operator runs the runner *on this repository*, so
this repository is also a consumer repository and must satisfy the same contract
every other consumer does. :class:`~git_loopy.gate.AgentsMdGateRunner` reads
``AGENTS.md``, parses its ``## Feedback loops`` table, and raises
:exc:`~git_loopy.gate.GateError` when the table is absent or every row is still a
``<PLACEHOLDER>``. Integration treats that error as **red**, so a repository with
no table can never land a Lane: every merge is reverted, all three
auto-resolution attempts read red, and every Lane falls back to a serial
Iteration. Nothing in the suite says why, because the failure lives in a markdown
file no test reads.

This guard is that test. It pins that ``AGENTS.md`` declares runnable feedback
loops, and that those loops cover all four **Runner family** members
``.github/workflows/runner-family-gate.yml`` gates — so the loop an autonomous
Iteration runs before committing cannot drift away from the loop CI runs after.

It reads only tracked files, so it needs neither a live runner nor credentials,
and degrades to a skip on an installed-wheel run with no source checkout,
mirroring the sibling ``test_workflow_lint`` / ``test_runner_family_gate``
guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.gate import FeedbackLoop, parse_feedback_loops


REPOSITORY_ROOT = Path(__file__).parents[3]
AGENTS_PATH = REPOSITORY_ROOT / "AGENTS.md"


def _loops() -> list[FeedbackLoop]:
    if not (REPOSITORY_ROOT / ".github/workflows").is_dir():
        pytest.skip(f"no source checkout: {AGENTS_PATH} is not part of a wheel")
    assert AGENTS_PATH.is_file(), "this repository must ship its own AGENTS.md"
    return parse_feedback_loops(AGENTS_PATH.read_text(encoding="utf-8"))


def test_this_repository_declares_runnable_feedback_loops() -> None:
    """The exact condition ``AgentsMdGateRunner`` raises ``GateError`` on."""
    runnable = [loop for loop in _loops() if loop.runnable]

    assert runnable, (
        "AGENTS.md must declare a `## Feedback loops` table with at least one "
        "concrete command; without it AgentsMdGateRunner raises GateError and "
        "Integration reads every Lane as red"
    )


# One marker set per Runner family member: fragments that can only appear in a
# command that actually exercises that member's sources. The Python and Rust
# rows pin the invocation that *runs* the suite; the shell and PowerShell rows
# pin the parse invocation over that port's tree, because the per-merge gate
# deliberately stops at parsing for those two (see the prose under the table —
# their behavioural suites are fork-bound and far too slow to run per merge, so
# runner-family-gate.yml owns them).
FAMILY_MARKERS = {
    "Python Orchestrator": ("pytest", "git-loopy/python/tests"),
    "shell Orchestrator": ("bash -n", "git-loopy/shell/"),
    "PowerShell Orchestrator": ("pwsh", "git-loopy/powershell"),
    "Rust Dashboard core": ("cargo test", "git-loopy/tui/Cargo.toml"),
}


@pytest.mark.parametrize(("member", "markers"), sorted(FAMILY_MARKERS.items()))
def test_every_runner_family_member_has_a_declared_loop(
    member: str, markers: tuple[str, ...]
) -> None:
    """An Iteration must be able to check what CI runs, for the whole family.

    ``runner-family-gate.yml`` gates all four members. A table that names only
    the Python reference would let an Iteration land a change that breaks the
    shell, PowerShell, or Rust port with a green local gate.
    """
    commands = "\n".join(loop.command for loop in _loops() if loop.runnable)

    for marker in markers:
        assert marker in commands, (
            f"no feedback loop checks the {member}'s sources; "
            f"runner-family-gate.yml gates it, so the AGENTS.md table must too "
            f"(expected a command containing {marker!r})"
        )
