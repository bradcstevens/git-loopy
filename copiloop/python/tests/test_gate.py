"""Tests for ``copiloop.gate`` — the runner-side Integration gate seam (#60).

Grounding: ADR-0009. In Parallel mode, **Integration** merges a **Lane**'s branch
into base and must re-run the target repo's own ``AGENTS.md`` feedback loops as the
load-bearing quality gate; :mod:`copiloop.gate` is what runs them *from the runner
side* and reports green/red.

Two layers are exercised here:

* :func:`copiloop.gate.parse_feedback_loops` — a pure parser of the ``AGENTS.md``
  ``## Feedback loops`` table (platform-independent).
* :class:`copiloop.gate.AgentsMdGateRunner` — the production adapter, exercised
  against real ``tmp_path`` worktrees with real shell commands (``true`` / ``false``
  / ``sh -c``), mirroring how ``tests/test_git.py`` drives real ``git``. These are
  guarded to POSIX hosts because they assume a ``/bin/sh``-style shell.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from copiloop.gate import (
    AgentsMdGateRunner,
    FeedbackLoop,
    GateError,
    GateResult,
    GateRunner,
    LoopFailure,
    parse_feedback_loops,
)

requires_posix = pytest.mark.skipif(
    os.name != "posix",
    reason="gate commands assume a POSIX /bin/sh shell",
)


# --------------------------------------------------------------------------- #
# parse_feedback_loops — pure table parser                                     #
# --------------------------------------------------------------------------- #


SAMPLE_AGENTS = textwrap.dedent(
    """\
    # Agent guide

    ## Feedback loops

    Intro prose about when to run each loop.

    | Loop       | Command         | When to run      |
    | ---------- | --------------- | ---------------- |
    | Lint       | `ruff check .`  | Any code change  |
    | Type-check | `ty check .`    | Any typed change |
    | Unit tests | `uv run pytest` | Any code change  |

    Run only the loops relevant to what you changed.

    ## Code conventions

    | Not | A | Feedback loop |
    | --- | - | ------------- |
    | x   | y | z             |
    """
)


def test_parse_extracts_name_and_command_per_data_row() -> None:
    assert parse_feedback_loops(SAMPLE_AGENTS) == [
        FeedbackLoop("Lint", "ruff check ."),
        FeedbackLoop("Type-check", "ty check ."),
        FeedbackLoop("Unit tests", "uv run pytest"),
    ]


def test_parse_is_scoped_to_the_feedback_loops_section() -> None:
    # The unrelated table under "## Code conventions" must not leak in.
    names = {loop.name for loop in parse_feedback_loops(SAMPLE_AGENTS)}
    assert names == {"Lint", "Type-check", "Unit tests"}


def test_parse_returns_empty_without_a_feedback_loops_section() -> None:
    assert parse_feedback_loops("# Guide\n\nNo loops here.\n") == []


def test_parse_handles_escaped_pipe_in_a_command() -> None:
    md = textwrap.dedent(
        """\
        ## Feedback loops

        | Loop  | Command                 | When to run |
        | ----- | ----------------------- | ----------- |
        | Piped | `pytest \\| tee out.log` | Any change  |
        """
    )
    assert parse_feedback_loops(md) == [FeedbackLoop("Piped", "pytest | tee out.log")]


def test_parse_locates_command_column_when_columns_are_reordered() -> None:
    md = textwrap.dedent(
        """\
        ## Feedback loops

        | When to run | Command     | Loop  |
        | ----------- | ----------- | ----- |
        | Any change  | `make test` | Tests |
        """
    )
    assert parse_feedback_loops(md) == [FeedbackLoop("Tests", "make test")]


# --------------------------------------------------------------------------- #
# Value objects                                                                #
# --------------------------------------------------------------------------- #


def test_feedback_loop_runnable_rejects_placeholders_and_empties() -> None:
    assert FeedbackLoop("Lint", "ruff check .").runnable is True
    assert FeedbackLoop("Lint", "<PM> lint").runnable is False
    assert FeedbackLoop("Lint", "").runnable is False


def test_gate_result_green_and_red_constructors() -> None:
    green = GateResult.green(["Lint", "Tests"])
    assert green.passed is True
    assert green.ran == ("Lint", "Tests")
    assert green.failure is None

    failure = LoopFailure("Tests", "false", 1, "boom")
    red = GateResult.red(["Lint", "Tests"], failure)
    assert red.passed is False
    assert red.ran == ("Lint", "Tests")
    assert red.failure is failure


# --------------------------------------------------------------------------- #
# AgentsMdGateRunner — production adapter (real shell commands)                #
# --------------------------------------------------------------------------- #


def _write_agents(worktree: Path, rows: list[tuple[str, str]]) -> None:
    """Write a minimal ``AGENTS.md`` with a ``## Feedback loops`` table."""
    lines = [
        "# Guide",
        "",
        "## Feedback loops",
        "",
        "| Loop | Command | When to run |",
        "| ---- | ------- | ----------- |",
    ]
    for name, command in rows:
        lines.append(f"| {name} | `{command}` | always |")
    lines.append("")
    (worktree / "AGENTS.md").write_text("\n".join(lines), encoding="utf-8")


def test_agentsmd_runner_satisfies_gate_runner_protocol() -> None:
    assert isinstance(AgentsMdGateRunner(), GateRunner)
    assert not isinstance(object(), GateRunner)


@requires_posix
def test_runner_green_runs_all_loops(tmp_path: Path) -> None:
    _write_agents(tmp_path, [("Lint", "true"), ("Tests", "true")])
    result = AgentsMdGateRunner().run(tmp_path)
    assert result.passed is True
    assert result.ran == ("Lint", "Tests")
    assert result.failure is None


@requires_posix
def test_runner_red_is_fail_fast_and_surfaces_detail(tmp_path: Path) -> None:
    _write_agents(
        tmp_path,
        [
            ("Lint", "true"),
            ("Tests", "sh -c 'echo boom >&2; exit 3'"),
            ("Build", "true"),
        ],
    )
    result = AgentsMdGateRunner().run(tmp_path)
    assert result.passed is False
    assert result.failure is not None
    assert result.failure.name == "Tests"
    assert result.failure.returncode == 3
    assert "boom" in result.failure.output_tail
    # Fail-fast: the loop after the first red one never ran.
    assert result.ran == ("Lint", "Tests")


@requires_posix
def test_runner_executes_loops_in_the_given_worktree(tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "sentinel").write_text("ok", encoding="utf-8")
    _write_agents(here, [("Check", "cat sentinel")])
    # Green only if the command runs with cwd == the worktree (sentinel lives there).
    assert AgentsMdGateRunner().run(here).passed is True

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write_agents(elsewhere, [("Check", "cat sentinel")])
    assert AgentsMdGateRunner().run(elsewhere).passed is False


def test_runner_raises_gate_error_when_agents_md_missing(tmp_path: Path) -> None:
    with pytest.raises(GateError):
        AgentsMdGateRunner().run(tmp_path)


def test_runner_raises_gate_error_when_no_runnable_loops(tmp_path: Path) -> None:
    _write_agents(tmp_path, [("Lint", "<PM> lint")])  # placeholder only
    with pytest.raises(GateError):
        AgentsMdGateRunner().run(tmp_path)


# --------------------------------------------------------------------------- #
# _make_gate_runner factory — monkeypatchable like the other _make_* factories #
# --------------------------------------------------------------------------- #


def test_make_gate_runner_builds_the_production_runner() -> None:
    from copiloop import loop

    assert isinstance(loop._make_gate_runner(), AgentsMdGateRunner)


def test_make_gate_runner_is_monkeypatchable(monkeypatch: pytest.MonkeyPatch) -> None:
    from copiloop import loop

    sentinel = AgentsMdGateRunner()
    monkeypatch.setattr("copiloop.loop._make_gate_runner", lambda: sentinel)
    assert loop._make_gate_runner() is sentinel
