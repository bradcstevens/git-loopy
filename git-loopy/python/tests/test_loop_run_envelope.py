"""What ``_Loop.drive`` writes into ``wrapper.run.end`` (#398).

The Run envelope is the only durable statement about *why* a Run stopped. A
Dashboard renders it, ``.git-loopy/runs/`` never records it, and an operator who
comes back to a finished Run has nothing else to read — so an outcome that names
the wrong reason is not a cosmetic defect, it is the log lying about the one
fact it exists to carry.

These tests pin every exit from the driver's round loop to the outcome it
actually took, including the two that leave through :class:`BaseException` and
therefore never touch the ``except Exception`` handler.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from git_loopy import events, loop as loop_module
from git_loopy.config import RunConfig


class _NoPreflight:
    """A source whose preflight passes and whose Pool is never read.

    ``drive()`` calls ``preflight()`` before it branches; every test here stubs
    ``_run_one_iteration``, so the Pool itself is out of scope.
    """

    def preflight(self) -> int | None:
        return None


def _bare_loop(*, max_iterations: int) -> Any:
    """A ``_Loop`` carrying only what ``drive()`` reads before its round loop.

    Constructed with ``object.__new__`` rather than the real ``__init__``: git,
    the writers, a Copilot client and the Strike machine are all reached from
    *inside* an iteration, and this suite replaces the iteration wholesale. A
    harness that stood them up would be testing the harness.

    ``_config`` is the *real* ``RunConfig`` rather than a namespace, because
    ``drive()`` reads whole slices of it — the **Run readback** reads the
    ``[routing]`` table, the **Escalation rung** and the context tier — and a
    double that enumerated today's fields would break every time one is added
    without ever catching a bug.
    """
    bare = object.__new__(loop_module._Loop)
    bare._diag = logging.getLogger("test.loop.run_envelope")
    bare._emitted = []
    bare._emit = lambda event_type, **payload: bare._emitted.append(
        {"type": event_type, **payload}
    )
    bare._source = _NoPreflight()
    bare._include_prs = False
    bare._config = RunConfig(
        issue_source="github",
        max_iterations=max_iterations,
        max_nmt_strikes=3,
        parallel=1,
    )
    bare._release_version = "0.0.0-test"
    bare._rate_card = None
    bare._skill_preflight = SimpleNamespace(
        migration_warning=False, event_payload={"policy": "closed-world"}
    )
    return bare


def _run_end(bare: Any) -> dict[str, Any]:
    return next(
        event for event in bare._emitted if event["type"] == events.WRAPPER_RUN_END
    )


def _scripted(*outcomes: Any) -> Any:
    """An ``_run_one_iteration`` that replays ``outcomes``, one per round.

    An entry that is an exception class or instance is raised instead of
    returned, which is how an interrupt is put exactly where a real one lands:
    inside a round, after earlier rounds have already finished.
    """
    calls: list[int] = []

    async def _run_one_iteration(iter_num: int) -> tuple[str, int, int]:
        calls.append(iter_num)
        entry = outcomes[iter_num - 1]
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise entry()
        if isinstance(entry, BaseException):
            raise entry
        return entry, 0, 0

    _run_one_iteration.calls = calls  # type: ignore[attr-defined]
    return _run_one_iteration


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt, asyncio.CancelledError],
    ids=["ctrl-c", "cancelled"],
)
@pytest.mark.asyncio
async def test_an_interrupted_run_does_not_report_the_cap_it_never_reached(
    interrupt: type[BaseException],
) -> None:
    """The regression (#398): an interrupt reported ``iteration_cap``.

    ``KeyboardInterrupt`` and ``CancelledError`` are ``BaseException``, so they
    pass straight through ``drive()``'s ``except Exception`` handler and reach
    the ``finally`` with ``outcome_label`` still holding whatever it was
    initialised to. That initial value used to be ``iteration_cap``.

    The observed failure came from a Run configured ``max_iterations=0``, which
    disables the cap check entirely — so the Run recorded the single outcome its
    own configuration made unreachable, and did it while an operator was trying
    to work out whether the Run had finished or been killed. Pinned here with
    the cap disabled for exactly that reason: with a cap configured, "reported
    the interrupt" and "reported the cap" could both be explained by an
    off-by-one, and this assertion would not be able to tell them apart.
    """
    bare = _bare_loop(max_iterations=0)
    bare._run_one_iteration = _scripted("advanced", "advanced", interrupt)

    with pytest.raises(interrupt):
        await bare.drive()

    end = _run_end(bare)
    # The literal, not the constant: with the constant, a driver that never
    # learned the outcome fails this on `AttributeError` rather than on the
    # value it actually published, and the regression would be pinned by the
    # name of the fix instead of by the behaviour.
    assert end["outcome"] == "interrupted"
    assert end["outcome"] != "iteration_cap"
    # Two rounds finished; the third was cut mid-flight and is not one of them.
    assert end["iterations_run"] == 2
    # Non-vacuity: the cap genuinely could not have fired.
    assert bare._config.max_iterations == 0


@pytest.mark.asyncio
async def test_an_interrupt_before_the_first_round_reports_no_iterations() -> None:
    """The count floors at zero rather than going negative.

    A Run interrupted inside round 1 has finished nothing. ``iterations_run``
    is derived by subtracting the unfinished round, so without a floor this
    path would publish ``-1`` — a number no reader has anywhere to put.
    """
    bare = _bare_loop(max_iterations=0)
    bare._run_one_iteration = _scripted(KeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        await bare.drive()

    end = _run_end(bare)
    assert end["outcome"] == "interrupted"
    assert end["iterations_run"] == 0


def test_the_interrupt_outcome_literal_is_the_one_the_driver_publishes() -> None:
    """The constant and the wire value are one fact, asserted once.

    Every other test here reads the literal, so this is what stops the constant
    drifting away from the string a Dashboard and a replay actually match on.
    """
    assert loop_module.RUN_OUTCOME_INTERRUPTED == "interrupted"
    assert "iteration_cap" in loop_module._RUN_OUTCOMES_MID_ITERATION
    assert loop_module.RUN_OUTCOME_INTERRUPTED in loop_module._RUN_OUTCOMES_MID_ITERATION


@pytest.mark.asyncio
async def test_a_capped_run_still_reports_the_cap_and_its_finished_rounds() -> None:
    """The outcome #398 made honest is still reported when it is true.

    Guards the obvious way to "fix" the regression wrongly: renaming every exit
    would make the interrupt assertion above pass and quietly cost the Run
    envelope the one outcome operators bound their `--iterations` expectations
    to. The count is unchanged from before the fix — the capped round is the one
    that would have exceeded the cap, and it never ran.
    """
    bare = _bare_loop(max_iterations=2)
    scripted = _scripted("advanced", "advanced", "advanced")
    bare._run_one_iteration = scripted

    exit_code = await bare.drive()

    end = _run_end(bare)
    assert end["outcome"] == "iteration_cap"
    assert end["iterations_run"] == 2
    assert exit_code == 0
    # The capped round broke before running: only two rounds were ever driven.
    assert scripted.calls == [1, 2]


@pytest.mark.asyncio
async def test_an_empty_pool_counts_the_round_that_found_it() -> None:
    """An ``empty_pool`` round finished — it read the Pool and found nothing.

    Distinct from the two mid-iteration outcomes above, and the reason the
    count is a set membership rather than a single comparison.
    """
    bare = _bare_loop(max_iterations=0)
    bare._run_one_iteration = _scripted("advanced", "empty_pool")

    exit_code = await bare.drive()

    end = _run_end(bare)
    assert end["outcome"] == "empty_pool"
    assert end["iterations_run"] == 2
    assert exit_code == 0


@pytest.mark.asyncio
async def test_a_crash_is_still_a_crash_and_still_propagates() -> None:
    """An ordinary exception keeps its own outcome and is re-raised.

    ``crashed`` is set by the handler #398 left alone; this is the assertion
    that says so, so a future change to the initial value cannot silently
    absorb a crash into the interrupt path.
    """
    bare = _bare_loop(max_iterations=0)
    bare._run_one_iteration = _scripted("advanced", RuntimeError("iteration blew up"))

    with pytest.raises(RuntimeError, match="iteration blew up"):
        await bare.drive()

    end = _run_end(bare)
    assert end["outcome"] == "crashed"


@pytest.mark.asyncio
async def test_the_envelope_closes_even_when_the_run_is_interrupted() -> None:
    """The ``finally`` still emits: an interrupted Run is not a silent one.

    The whole reason the outcome had to be *named* rather than simply omitted.
    A Run that vanished without a ``wrapper.run.end`` would be unambiguous but
    unreadable — the Dashboard would sit on ``running`` forever.
    """
    bare = _bare_loop(max_iterations=0)
    bare._run_one_iteration = _scripted(KeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        await bare.drive()

    types = [event["type"] for event in bare._emitted]
    assert types[0] == events.WRAPPER_SKILL_POLICY_RESOLVED
    assert types[1] == events.WRAPPER_RUN_START
    assert types[-1] == events.WRAPPER_RUN_END
    assert _run_end(bare)["iter_num"] is None
