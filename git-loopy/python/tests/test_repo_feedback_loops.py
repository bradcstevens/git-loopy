"""git-loopy's own ``AGENTS.md`` must be gate-runnable — it has to dogfood #60.

In Parallel mode, **Integration** merges a **Lane**'s branch into a private stage and
re-runs the merged worktree's own ``AGENTS.md`` feedback loops as the load-bearing
quality gate (ADR-0009, :mod:`git_loopy.gate`). When a repository declares no
*runnable* loops, :class:`~git_loopy.gate.AgentsMdGateRunner` raises
:exc:`~git_loopy.gate.GateError` — a "cannot gate" condition — and
``Loop._gate_green`` turns that into a red gate.

For most of its life this repository had no ``## Feedback loops`` section at all, so
its own gate could never run: **every** Lane merge came back red no matter what it
contained, drove the contribution into conflict auto-resolution, and ended
unpublished. Whole Iterations of finished work were merged and then reverted for a
reason that had nothing to do with the contribution.

These guards pin the input the gate reads. They are deliberately about *this*
repository's file rather than a ``tmp_path`` fixture (``tests/test_gate.py`` already
covers the parser and the adapter in the abstract): the bug was never in the gate, it
was in git-loopy failing to declare the one table its own Integration stage reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from git_loopy.gate import AgentsMdGateRunner, GateResult, parse_feedback_loops

_SECTION_HEADING = "## Feedback loops"

# An absolute path baked into a declared command — a loop that only runs on the
# machine it was written on. Integration runs in a throwaway private worktree on
# whatever host the operator has, so a pinned prefix like `/opt/homebrew/bin/bash`
# is a red gate everywhere else.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w./-])(?:/[A-Za-z0-9_.-]+){2,}|[A-Za-z]:\\\\")


def _find_repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


@pytest.fixture(scope="module")
def repo_root() -> Path:
    root = _find_repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout: this guard is about the repo's own AGENTS.md")
    return root


@pytest.fixture(scope="module")
def agents_md(repo_root: Path) -> str:
    return (repo_root / "AGENTS.md").read_text(encoding="utf-8")


def test_repo_agents_md_declares_a_feedback_loops_section(agents_md: str) -> None:
    assert _SECTION_HEADING in agents_md, (
        f"the repo root AGENTS.md has no {_SECTION_HEADING!r} section, so "
        "AgentsMdGateRunner cannot gate this repository at all"
    )


def test_repo_agents_md_declares_runnable_feedback_loops(agents_md: str) -> None:
    """The exact condition ``AgentsMdGateRunner.run`` screens on before executing."""
    loops = parse_feedback_loops(agents_md)
    runnable = [loop for loop in loops if loop.runnable]
    assert runnable, (
        f"the repo root AGENTS.md parses to {len(loops)} loop(s), none runnable; "
        "AgentsMdGateRunner raises GateError and Loop._gate_green reports red, so "
        "every Lane merge fails Integration regardless of its contents"
    )


def test_the_production_gate_can_run_this_repository(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real adapter over the real file; only the execution is stubbed.

    Running the declared suites for real would take minutes and is what CI is for.
    What has to be pinned here is that ``run()`` reaches the execution step at all
    instead of raising :exc:`~git_loopy.gate.GateError` on its way there.
    """
    ran_commands: list[str] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(command: str, **kwargs: object) -> _Completed:
        ran_commands.append(command)
        return _Completed()

    monkeypatch.setattr("git_loopy.gate.subprocess.run", _fake_run)

    result = AgentsMdGateRunner().run(repo_root)

    assert isinstance(result, GateResult)
    assert result.passed
    assert ran_commands, "the gate declared loops but executed none of them"
    assert len(result.ran) == len(ran_commands)


def test_declared_loops_commit_no_host_specific_paths(agents_md: str) -> None:
    """Commands resolve their tools through ``PATH``, not through one host's layout."""
    offenders = {
        loop.name: loop.command
        for loop in parse_feedback_loops(agents_md)
        if loop.runnable and _ABSOLUTE_PATH_RE.search(loop.command) is not None
    }
    assert not offenders, (
        "feedback loop commands must not hard-code host-specific executable paths "
        f"(Integration runs in a throwaway worktree on any host): {offenders}"
    )


def test_declared_loops_are_named_and_unique(agents_md: str) -> None:
    """A failing loop is reported by name, so an unnamed or duplicated one is mute."""
    names = [loop.name for loop in parse_feedback_loops(agents_md) if loop.runnable]
    assert all(names), f"every runnable loop needs a name for the red report: {names}"
    assert len(set(names)) == len(names), f"duplicate feedback loop names: {names}"
