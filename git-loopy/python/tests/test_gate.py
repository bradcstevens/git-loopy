"""Tests for ``git_loopy.gate`` — the runner-side Integration gate seam (#60).

Grounding: ADR-0009. In Parallel mode, **Integration** merges a **Lane**'s branch
into base and must re-run the target repo's own ``AGENTS.md`` feedback loops as the
load-bearing quality gate; :mod:`git_loopy.gate` is what runs them *from the runner
side* and reports green/red.

Two layers are exercised here:

* :func:`git_loopy.gate.parse_feedback_loops` — a pure parser of the ``AGENTS.md``
  ``## Feedback loops`` table (platform-independent).
* :class:`git_loopy.gate.AgentsMdGateRunner` — the production adapter, exercised
  against real ``tmp_path`` worktrees with real shell commands (``true`` / ``false``
  / ``sh -c``), mirroring how ``tests/test_git.py`` drives real ``git``. These are
  guarded to POSIX hosts because they assume a ``/bin/sh``-style shell.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from git_loopy import gate
from git_loopy.gate import (
    DEFAULT_GATE_TIMEOUT_SECONDS,
    GATE_TIMEOUT_ENV_VAR,
    AgentsMdGateRunner,
    FeedbackLoop,
    GateError,
    GateResult,
    GateRunner,
    LoopFailure,
    parse_feedback_loops,
    resolve_gate_timeout_seconds,
    run_gate_off_loop,
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


def test_module_entrypoint_returns_nonzero_for_a_red_declared_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agents(tmp_path, [("Tests", "false")])
    monkeypatch.chdir(tmp_path)

    assert gate.main() == 1


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
# Timeout — every loop the gate runs is bounded (#374)                         #
# --------------------------------------------------------------------------- #


@requires_posix
def test_runner_reports_a_loop_that_exceeds_its_bound_as_a_timeout(
    tmp_path: Path,
) -> None:
    """A hung loop is a red gate naming that loop, and the suite does not hang."""
    _write_agents(tmp_path, [("Hang", "sleep 30"), ("Never", "true")])
    started = time.monotonic()
    result = AgentsMdGateRunner(timeout_seconds=0.5).run(tmp_path)
    elapsed = time.monotonic() - started

    assert elapsed < 20  # bounded: the 30s sleep did not run to completion
    assert result.passed is False
    assert result.ran == ("Hang",)  # fail-fast still holds for a timeout
    assert result.failure is not None
    assert result.failure.name == "Hang"


@requires_posix
def test_timeout_is_distinguishable_from_a_non_zero_exit(tmp_path: Path) -> None:
    """A timeout is not a test failure and must not be reported as one."""
    _write_agents(tmp_path, [("Hang", "sleep 30")])
    timed_out = AgentsMdGateRunner(timeout_seconds=0.5).run(tmp_path).failure
    assert timed_out is not None
    assert timed_out.timed_out is True
    assert timed_out.returncode is None  # never a synthetic 124 / -9
    assert timed_out.timeout_seconds == 0.5
    assert "timed out" in timed_out.summary

    _write_agents(tmp_path, [("Hang", "sh -c 'exit 3'")])
    exited = AgentsMdGateRunner(timeout_seconds=30).run(tmp_path).failure
    assert exited is not None
    assert exited.timed_out is False
    assert exited.returncode == 3
    assert exited.timeout_seconds is None
    assert "timed out" not in exited.summary


def test_loop_failure_refuses_a_collapsed_timeout() -> None:
    """The two red channels cannot be mixed into one another."""
    with pytest.raises(ValueError):
        LoopFailure("Tests", "false", 124, "", timed_out=True, timeout_seconds=1.0)
    with pytest.raises(ValueError):
        LoopFailure("Tests", "false", None, "", timed_out=True)
    with pytest.raises(ValueError):
        LoopFailure("Tests", "false", None, "")


@requires_posix
def test_timeout_reaps_the_whole_process_tree(tmp_path: Path) -> None:
    """A backgrounded grandchild holding the pipes cannot outlive the bound.

    This is the failure a naive bounded ``subprocess.run`` still has: killing the
    shell leaves its children holding stdout/stderr, so the *drain* blocks and the
    "bounded" run hangs anyway. Every loop in this repo's own table is a shell
    construct that spawns children, so it is the realistic case, not a corner one.
    """
    _write_agents(tmp_path, [("Hang", "sleep 30 & wait")])
    started = time.monotonic()
    result = AgentsMdGateRunner(timeout_seconds=0.5).run(tmp_path)
    elapsed = time.monotonic() - started

    assert result.failure is not None and result.failure.timed_out is True
    # Comfortably under the post-kill drain grace: the group kill freed the pipes.
    assert elapsed < 4


@requires_posix
def test_a_drain_that_cannot_finish_still_releases_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descendant that escapes the group kill must not leak a zombie or its pipes.

    The group kill reaps the whole tree in the ordinary case, so the post-kill
    drain finishes and ``communicate`` closes the pipes and reaps the shell for
    us. A descendant that put *itself* in a new session escapes that kill, keeps
    the pipes open, and the drain then expires — at which point the runner has to
    do both jobs itself. Integration runs this gate after every Lane merge for the
    whole life of one long Run, so leaking a zombie and two descriptors per
    timed-out loop is unbounded growth in exactly the unattended path the bound
    exists to protect.
    """
    escaped = (
        f"{shlex.quote(sys.executable)} -c "
        f"{shlex.quote('import os, time; os.setsid(); time.sleep(30)')} & sleep 30"
    )
    _write_agents(tmp_path, [("Hang", escaped)])
    monkeypatch.setattr(gate, "_DRAIN_GRACE_SECONDS", 0.5)

    spawned: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def _recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(process)
        return process

    monkeypatch.setattr(gate.subprocess, "Popen", _recording_popen)

    result = AgentsMdGateRunner(timeout_seconds=0.5).run(tmp_path)
    assert result.failure is not None and result.failure.timed_out is True

    assert len(spawned) == 1, "the gate spawned something other than the one loop"
    process = spawned[0]
    assert process.returncode is not None, "the killed loop was never reaped"
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed


def test_runner_refuses_a_non_positive_bound() -> None:
    """There is no value meaning "unbounded" — that is the whole point (#374)."""
    for bad in (0, -1, -0.5):
        with pytest.raises(ValueError):
            AgentsMdGateRunner(timeout_seconds=bad)


def test_runner_refuses_an_infinite_bound() -> None:
    """``inf`` *is* a value meaning "unbounded", so it is refused like the rest.

    It also cannot be honoured even if it were wanted: ``communicate(timeout=inf)``
    raises :exc:`OverflowError`, which is not a :exc:`~git_loopy.gate.GateError`,
    so it would escape Integration's red-gate handling entirely and take the Run
    down while leaving the loop running.
    """
    for unbounded in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(ValueError):
            AgentsMdGateRunner(timeout_seconds=unbounded)


@requires_posix
def test_an_interrupted_gate_does_not_orphan_the_loop_it_was_waiting_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling a Run must not leave a loop writing into the Integration worktree.

    The bound handles the loop that hangs; this is the loop that was behaving
    perfectly well when the *operator* stopped. Without cleanup on the way out,
    the gate's child survives the interrupt still holding — and still mutating —
    the private Integration stage the runner is about to reap.
    """
    _write_agents(tmp_path, [("Hang", "sleep 30")])

    spawned: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def _interrupting_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(process)
        original = process.communicate

        def _interrupt_once(*a: object, **k: object) -> tuple[str, str]:
            process.communicate = original  # type: ignore[method-assign]
            raise KeyboardInterrupt

        process.communicate = _interrupt_once  # type: ignore[method-assign]
        return process

    monkeypatch.setattr(gate.subprocess, "Popen", _interrupting_popen)

    with pytest.raises(KeyboardInterrupt):
        AgentsMdGateRunner(timeout_seconds=30).run(tmp_path)

    assert len(spawned) == 1
    process = spawned[0]
    assert process.returncode == -signal.SIGKILL, "the loop outlived the interrupt"
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed


# --------------------------------------------------------------------------- #
# run_gate_off_loop — the gate a Run awaits without freezing (#901)            #
# --------------------------------------------------------------------------- #


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@requires_posix
def test_the_event_loop_keeps_running_while_a_gate_row_runs(tmp_path: Path) -> None:
    """A gate row occupies a worker, never the Run's event loop (#901)."""
    _write_agents(tmp_path, [("Slow", "sleep 1")])
    runner = AgentsMdGateRunner(timeout_seconds=10)

    async def scenario() -> tuple[GateResult, int]:
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        ticker = asyncio.create_task(tick())
        try:
            result = await run_gate_off_loop(runner, tmp_path)
        finally:
            ticker.cancel()
        return result, ticks

    result, ticks = asyncio.run(scenario())

    assert result.passed
    # A frozen loop would tick at most once, after the gate returned.
    assert ticks >= 5, f"the event loop stalled behind the gate ({ticks} ticks)"


@requires_posix
def test_an_off_loop_gate_timeout_is_reported_exactly_as_before(tmp_path: Path) -> None:
    """Moving the row off the loop changes no gate result, timeouts included."""
    _write_agents(tmp_path, [("Hang", "sleep 30 & wait")])
    runner = AgentsMdGateRunner(timeout_seconds=0.5)

    started = time.monotonic()
    off_loop = asyncio.run(run_gate_off_loop(runner, tmp_path))
    elapsed = time.monotonic() - started
    direct = runner.run(tmp_path)

    assert elapsed < 4
    assert off_loop.passed is False and off_loop.ran == direct.ran == ("Hang",)
    assert off_loop.failure is not None and direct.failure is not None
    assert off_loop.failure.timed_out is True
    assert off_loop.failure.timeout_seconds == 0.5
    assert off_loop.failure.summary == direct.failure.summary


@requires_posix
def test_cancelling_an_off_loop_gate_reaps_its_whole_process_group(
    tmp_path: Path,
) -> None:
    """A Stop that cancels Integration kills the row's tree before it propagates.

    The worker thread a row runs on cannot be interrupted by the cancellation
    itself, so without an explicit signal the row would run on — for up to its
    hour-long bound — writing into a private Integration stage the Runner is
    about to reap, and holding the Run's executor open at shutdown.
    """
    from git_loopy.persist import create_writers

    writers = create_writers(tmp_path, mirror_diagnostics_to_stderr=False)
    pid_file = tmp_path / "grandchild.pid"
    _write_agents(
        tmp_path, [("Hang", f"sleep 30 & echo $! > {shlex.quote(str(pid_file))}; wait")]
    )
    ticks = iter([0.0, 31.0])
    runner = AgentsMdGateRunner(
        timeout_seconds=60, diagnostics=writers.diagnostics,
        clock=lambda: next(ticks),
    )

    async def scenario() -> float:
        task = asyncio.create_task(run_gate_off_loop(runner, tmp_path))
        deadline = time.monotonic() + 5
        while not pid_file.exists() or not pid_file.read_text().strip():
            assert time.monotonic() < deadline, "the gate row never started"
            await asyncio.sleep(0.02)
        cancelled_at = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.monotonic() - cancelled_at

    took = asyncio.run(scenario())
    grandchild = int(pid_file.read_text().strip())

    assert took < 4, f"cancellation waited {took:.1f}s on the row"
    deadline = time.monotonic() + 2
    while _pid_alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _pid_alive(grandchild), "the row's process group outlived the Stop"

    text = writers.diagnostics_path.read_text(encoding="utf-8")
    assert "Gate row 'Hang': interrupted; duration=31.000s; bound=60s" in text
    warnings = [line for line in text.splitlines() if "WARNING" in line]
    assert len(warnings) == 1
    assert "Gate row 'Hang' exceeded half its bound" in warnings[0]


def test_an_off_loop_gate_still_reports_a_gate_that_cannot_run(tmp_path: Path) -> None:
    """``GateError`` crosses the thread boundary unchanged."""
    with pytest.raises(GateError):
        asyncio.run(run_gate_off_loop(AgentsMdGateRunner(), tmp_path))


def test_gate_timeout_resolves_from_the_environment() -> None:
    """The bound is configurable, and a stray value degrades to the default."""
    assert resolve_gate_timeout_seconds({}) == DEFAULT_GATE_TIMEOUT_SECONDS
    assert resolve_gate_timeout_seconds({GATE_TIMEOUT_ENV_VAR: "90"}) == 90.0
    assert resolve_gate_timeout_seconds({GATE_TIMEOUT_ENV_VAR: " 12.5 "}) == 12.5
    for stray in ("", "   ", "abc", "0", "-30"):
        assert (
            resolve_gate_timeout_seconds({GATE_TIMEOUT_ENV_VAR: stray})
            == DEFAULT_GATE_TIMEOUT_SECONDS
        )


def test_gate_timeout_env_var_cannot_spell_unbounded() -> None:
    """``float`` parses these; none of them may reach the runner as a bound."""
    for unbounded in ("inf", "Infinity", "-inf", "nan", "1e400"):
        assert (
            resolve_gate_timeout_seconds({GATE_TIMEOUT_ENV_VAR: unbounded})
            == DEFAULT_GATE_TIMEOUT_SECONDS
        )


# --------------------------------------------------------------------------- #
# _make_gate_runner factory — monkeypatchable like the other _make_* factories #
# --------------------------------------------------------------------------- #


def test_make_gate_runner_builds_the_production_runner() -> None:
    from git_loopy import loop

    runner = loop._make_gate_runner()
    assert isinstance(runner, AgentsMdGateRunner)
    assert runner.timeout_seconds == DEFAULT_GATE_TIMEOUT_SECONDS


def test_make_gate_runner_binds_the_configured_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory reads ``GIT_LOOPY_GATE_TIMEOUT_SECONDS``, like ``_make_worktree_setup``."""
    from git_loopy import loop

    monkeypatch.setenv(GATE_TIMEOUT_ENV_VAR, "  45  ")
    assert loop._make_gate_runner().timeout_seconds == 45.0

    monkeypatch.setenv(GATE_TIMEOUT_ENV_VAR, "nonsense")
    assert loop._make_gate_runner().timeout_seconds == DEFAULT_GATE_TIMEOUT_SECONDS


def test_make_gate_runner_is_monkeypatchable(monkeypatch: pytest.MonkeyPatch) -> None:
    from git_loopy import loop

    sentinel = AgentsMdGateRunner()
    monkeypatch.setattr("git_loopy.loop._make_gate_runner", lambda **_kwargs: sentinel)
    assert loop._make_gate_runner() is sentinel


@requires_posix
@pytest.mark.parametrize(
    ("command", "duration", "outcome", "warning"),
    [
        ("true", 0.025, "passed", False),
        ("true", 0.026, "passed", True),
        ("false", 0.026, "failed", True),
        ("sleep 30", 0.05, "timed out", True),
    ],
)
def test_every_gate_row_records_duration_and_bound_in_the_run_log(
    tmp_path: Path, command: str, duration: float, outcome: str, warning: bool,
) -> None:
    from git_loopy.persist import create_writers

    writers = create_writers(tmp_path, mirror_diagnostics_to_stderr=False)
    _write_agents(tmp_path, [("Measured row", command), ("Next row", "true")])
    ticks = iter([0.0, duration, 1.0, 1.001])
    result = AgentsMdGateRunner(
        timeout_seconds=0.05, diagnostics=writers.diagnostics,
        clock=lambda: next(ticks),
    ).run(tmp_path)

    text = writers.diagnostics_path.read_text(encoding="utf-8")
    assert f"Gate row 'Measured row': {outcome}; duration={duration:.3f}s; bound=0.05s" in text
    warnings = [line for line in text.splitlines() if "WARNING" in line]
    assert len(warnings) == int(warning)
    if warning:
        assert "'Measured row'" in warnings[0]
        assert "half" in warnings[0]
    if outcome == "passed":
        assert result.passed
        assert "Gate row 'Next row': passed; duration=0.001s; bound=0.05s" in text
    else:
        assert not result.passed
        assert "Next row" not in text
