"""``git_loopy.trial`` — the production **Trial** runner (#369, ADR-0027).

One candidate pair works one **Proving task** in its own worktree at a historical
commit, scored by the fix's own tests with the AGENTS.md gate kept as a
pass-to-pass regression guard. Everything a **Calibration** actually *spends*
happens here; :mod:`git_loopy.calibration_search` decides which Trials to buy and
:mod:`git_loopy.trial_concurrency` decides how many run at once, and neither of
them touches a worktree or a session.

The module composes four seams that already exist and modifies none of them:

* **Isolation** — :meth:`~git_loopy.git.GitClient.add_worktree` already takes an
  arbitrary commit-ish as its base, so a **Proving task**'s base commit works
  today. The **Lane** primitive of ADR-0008, reused rather than reinvented.
* **Prepare** — :class:`~git_loopy.worktree.WorktreeSetup`, because a fresh
  worktree has the source but not the environment the feedback loops need.
* **Oracle** — :func:`~git_loopy.gate.parse_feedback_loops` selects the loops
  that already cover the fix's own test paths, and
  :class:`~git_loopy.gate.AgentsMdGateRunner` runs them from a generated
  single-purpose table. Its ``agents_filename`` parameter is what makes that
  reuse rather than a second bounded-subprocess runner.
* **Work** — an :class:`~git_loopy.session.IterationSession` constructed directly
  against the Copilot client on the candidate pair, with ``iter_num=None``. That
  is the same carve-out :mod:`git_loopy.task_type_session` uses and for the same
  reason: **Strikes** are shared and consecutive, so a session that could tick
  them might end a **Run** that this one has nothing to do with (#371).

Design notes:

* **The oracle is narrower than the gate, and that is the whole amendment.**
  ADR-0027 rejected the whole-repo gate as a per-task scorer — it is fail-fast,
  so a base commit red for any unrelated reason fails every pair at every rung
  and nothing distinguishes *"this model is too weak"* from *"this task was never
  runnable."* :func:`covering_loops` picks the loops whose commands already name
  the deepest directory containing an oracle path, so the fail-to-pass
  measurement is over the fix's own tests. The full gate still runs, as the
  **pass-to-pass regression guard** that stops a cheap pair satisfying its own
  tests while breaking a neighbouring suite.
* **The gate that runs is the base commit's, not today's.** Both instruments read
  the worktree they are given, and that worktree sits at a months-old commit —
  so strengthening this repository's feedback-loop table does *not* improve a
  Calibration measured against a frozen set. Refreshing the **Proving set** is
  what propagates a stronger gate (ADR-0027), and
  :class:`ReplayTrialResult` records which loops actually ran so a reader can
  check that rather than assume it.
* **Nothing survives.** The worktree and its branch are torn down in a
  ``finally`` that catches :class:`BaseException`, so a ``KeyboardInterrupt``
  through a Trial in flight leaves no manual cleanup — which is exactly the case
  :class:`~git_loopy.trial_concurrency.TrialInterrupt` leaves to this module.
* **The pin is resolved, not re-mined.** A :class:`~git_loopy.trial_concurrency.TrialRequest`
  carries a :class:`~git_loopy.measured_routing.ProvingTask` — issue number and
  two commits — and a Trial additionally needs the oracle's paths and the issue
  body. Those arrive with the admitted candidates the runner is constructed with,
  so a Trial reaches the tracker exactly never and the whole path stays offline.
* **A red Trial is a result, never a raise.** Every failure this module can
  anticipate — an unknown pin, a worktree that would not prepare, an oracle no
  loop covers, an oracle that was already green at the base commit, a session
  that raised — comes back as a red :class:`ReplayTrialResult` with the detail.
  Its **Consumption** is whatever was actually observed, which may be unknown and
  is never zeroed (ADR-0026): a crashed session may well have been billed.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from git_loopy import git as git_module
from git_loopy.events import USAGE_TOKENS
from git_loopy.gate import (
    FeedbackLoop,
    GateError,
    GateResult,
    GateRunner,
    parse_feedback_loops,
)
from git_loopy.git import GitClient, GitError
from git_loopy.proving_set import ProvingCandidate
from git_loopy.trial_concurrency import TrialRequest, TrialResult
from git_loopy.usage import BillingSample, UsageTally
from git_loopy.worktree import WorktreeSetup

__all__ = [
    "ORACLE_TABLE_FILENAME",
    "ReplayTrialResult",
    "ReplayTrialRunner",
    "apply_oracle",
    "covering_loops",
    "make_oracle_gate_runner",
    "render_oracle_table",
    "trial_branch_name",
    "trial_prompt",
]

#: The generated single-purpose feedback-loop table the oracle is run from,
#: written into the Trial's own worktree and removed the moment the oracle has
#: run. A dotfile so an agent that lists the tree does not read it as work, and
#: never committed, because a Trial commits nothing at all.
ORACLE_TABLE_FILENAME = ".git-loopy-oracle.md"


# --------------------------------------------------------------------------- #
# The oracle: the fix's own tests, and nothing wider                           #
# --------------------------------------------------------------------------- #


def _ancestors(path: str) -> list[str]:
    """``a/b/c.py`` -> ``["a/b/c.py", "a/b", "a"]`` — deepest first."""
    parts = [part for part in path.split("/") if part]
    return ["/".join(parts[: n + 1]) for n in range(len(parts) - 1, -1, -1)]


def covering_loops(
    loops: Sequence[FeedbackLoop],
    oracle_paths: Sequence[str],
) -> tuple[FeedbackLoop, ...]:
    """The runnable loops that already cover the oracle's test paths.

    For each oracle path, the *deepest* directory of it that any runnable loop's
    command names wins, and every loop naming that directory is selected. Deepest
    first is what makes the result narrower than the gate rather than
    accidentally equal to it: a table declaring both a whole-repo loop and a
    per-member one yields the per-member one.

    Args:
        loops: The base commit's parsed ``## Feedback loops`` rows, in table
            order. Non-runnable rows (empty, or a still-``<PLACEHOLDER>`` stub)
            are screened exactly as the gate screens them.
        oracle_paths: The fixing commit's test paths, and nothing else.

    Returns:
        The selected loops in table order, deduplicated. Empty when no loop
        covers any oracle path — a **Trial** turns that into a red result with
        the detail, because a task this table cannot score is not a task the pair
        failed.
    """
    runnable = [loop for loop in loops if loop.runnable]
    selected: list[FeedbackLoop] = []
    for path in oracle_paths:
        for ancestor in _ancestors(path):
            matches = [loop for loop in runnable if ancestor in loop.command]
            if matches:
                selected.extend(matches)
                break
    return tuple(
        loop for index, loop in enumerate(selected) if loop not in selected[:index]
    )


def render_oracle_table(loops: Sequence[FeedbackLoop]) -> str:
    """Render ``loops`` as an ``AGENTS.md``-shaped ``## Feedback loops`` table.

    The one thing that has to be right is the escaping: the table is markdown,
    real loop commands pipe and chain, and an unescaped ``|`` would silently
    reduce a command to its first fragment. :func:`~git_loopy.gate._split_row`
    honours ``\\|``, so that is what is written.
    """
    rows = "\n".join(
        f"| {loop.name.replace('|', chr(92) + '|')} "
        f"| {loop.command.replace('|', chr(92) + '|')} |"
        for loop in loops
    )
    return (
        "# Trial oracle\n\n"
        "Generated by `git_loopy.trial` for one **Trial** and removed the moment "
        "it has run. Never committed.\n\n"
        "## Feedback loops\n\n"
        "| Loop | Command |\n"
        "| --- | --- |\n"
        f"{rows}\n"
    )


def make_oracle_gate_runner(*, timeout_seconds: float) -> GateRunner:
    """The oracle instrument: the gate runner, pointed at the generated table.

    ``AgentsMdGateRunner`` is already parameterised by the filename it reads, so
    this is reuse rather than a second bounded-subprocess runner to keep in step
    — the oracle inherits the gate's fail-fast execution, its per-loop
    wall-clock bound (#374) and its :class:`~git_loopy.gate.LoopFailure` detail.
    """
    from git_loopy.gate import AgentsMdGateRunner

    return AgentsMdGateRunner(
        agents_filename=ORACLE_TABLE_FILENAME,
        timeout_seconds=timeout_seconds,
    )


def apply_oracle(worktree: Path, *, commit: str, paths: Sequence[str]) -> None:
    """Bring the fixing commit's test-path changes into ``worktree``, and nothing else.

    Both halves of "the fix's test changes" are applied: a path the fix added or
    modified is checked out from ``commit``, and a path the fix *deleted* is
    deleted here too. Carrying only the additions leaves a superseded test
    behind, and a stale test that can never pass makes the oracle unable to go
    green for any pair — which reads as every model being too weak.

    Goes through :mod:`git_loopy.git`'s own invoker rather than a new
    :class:`~git_loopy.git.GitClient` method: #369 composes the git client *as it
    is*, and a public ``checkout_paths`` would be a change to a seam this work
    may not modify. Recorded as a follow-up for whoever next owns ``git.py``.

    Args:
        worktree: The Trial's worktree, already at the **Proving task**'s base
            commit.
        commit: The fixing commit, whose test paths are the oracle.
        paths: Those test paths, and nothing else. The fix itself is deliberately
            absent — a replay that carried it would score a pair on reading a
            diff (ADR-0027).

    Raises:
        GitError: If git refuses the checkout or the removal.
    """
    if not paths:
        return
    listed = git_module._run(
        ["ls-tree", "-r", "--name-only", "-z", commit, "--", *paths],
        cwd=worktree,
    )
    present = {entry for entry in listed.split("\0") if entry}
    if present:
        git_module._run(
            ["checkout", commit, "--", *sorted(present)],
            cwd=worktree,
        )
    removed = [path for path in paths if path not in present]
    if removed:
        git_module._run(
            ["rm", "-q", "-f", "--ignore-unmatch", "--", *removed],
            cwd=worktree,
        )


# --------------------------------------------------------------------------- #
# Naming: a Trial's worktree is bounded by the slot, its branch never collides #
# --------------------------------------------------------------------------- #

_TRIAL_SEQUENCE = itertools.count(1)


def trial_branch_name(calibration_id: str, sequence: int) -> str:
    """The working branch for one **Trial**.

    Sequenced rather than keyed on the slot, because a slot is *reused* the
    moment the Trial holding it returns and the same slot legitimately works the
    same **Proving task** again at the next rung. A branch that a failed teardown
    left behind must never be a name the next Trial tries to create.
    """
    return f"git-loopy/calibration/{calibration_id}/trial-{sequence}"


# --------------------------------------------------------------------------- #
# The result                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReplayTrialResult(TrialResult):
    """A :class:`~git_loopy.trial_concurrency.TrialResult`, plus what was measured with.

    A subclass rather than four more fields, deliberately: ADR-0027 scores a
    Trial lexicographically on exactly *cleared the gate*, *credits* and *wall
    clock*, and ``TrialResult``'s field set is pinned so no fourth scoring key can
    appear for a weighted composite to occupy. These two are not scoring keys —
    nothing branches on them — they are the record of **which** instruments ran,
    which is what makes "the gate that runs is the one declared at the base
    commit" checkable rather than promised.

    Attributes:
        gate_loops: The loop names the AGENTS.md gate at the base commit ran, in
            order. Fail-fast, so a red gate's list ends at the loop that failed.
        oracle_loops: The loop names the oracle ran — the subset of that same
            table covering the fix's own test paths.
    """

    gate_loops: tuple[str, ...] = ()
    oracle_loops: tuple[str, ...] = ()


class _CreditMeter:
    """Fold ``usage.tokens`` into a tally, so a Trial can report what it cost.

    Deliberately *not* the Run's :class:`~git_loopy.rolling_pressure.RunCostMeter`:
    a Trial's **Consumption** belongs to the **Calibration** and must not reach a
    Run's totals (#371). The figure is the harness's own billed Credits, never
    recomputed from tokens and a price (ADR-0026), and a session that reported
    none leaves it *unknown* rather than zero — a crashed session may well have
    been billed.
    """

    def __init__(self) -> None:
        self._tally = UsageTally()
        self._lock = threading.Lock()

    def observe(self, event: Mapping[str, Any]) -> None:
        if event.get("type") != USAGE_TOKENS:
            return
        model = event.get("model")
        with self._lock:
            self._tally.add(
                str(model) if isinstance(model, str) and model else None,
                _nonnegative_int(event.get("input")),
                _nonnegative_int(event.get("output")),
                BillingSample.from_event(event),
            )

    @property
    def credits(self) -> Decimal | None:
        with self._lock:
            return self._tally.credits


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def trial_prompt(candidate: ProvingCandidate) -> str:
    """The Trial session's prompt: the issue as it was actually stated.

    Built here rather than read from ``PROMPT.md`` for the reason
    :func:`~git_loopy.task_type_classifier.classifier_prompt` gives: the shared
    prompt is the **Run**'s prompt and operator-overridable, and a measurement
    whose instructions an override can quietly rewrite is not a measurement.

    Everything a **Run**'s prompt does that a **Trial** must not is forbidden
    explicitly: there is no tracker to read (the issue is closed and its body is
    right here), nothing to commit (a Trial's diff is never merged anywhere and
    the worktree does not survive), and no issue to close.
    """
    return (
        "Do the work described below, in this worktree.\n\n"
        "The tests that judge it are already present. Make them pass by solving "
        "the problem they describe — do not edit or delete a test to satisfy it, "
        "and do not break any test that already passes.\n\n"
        "Do not commit, do not push, do not create a branch, and do not touch "
        "the issue tracker. Editing the working tree is the whole of the task.\n\n"
        f"=== Issue #{candidate.issue} ===\n"
        f"{candidate.task_text}\n"
    )


#: How long one Trial's agent session may run before it is abandoned. A Trial
#: that hangs would otherwise hold its worktree, its slot and the search's
#: wall-clock ceiling for as long as the harness cared to stay silent.
DEFAULT_SEND_TIMEOUT_SECONDS = 30 * 60


class ReplayTrialRunner:
    """The production :class:`~git_loopy.trial_concurrency.TrialRunner`.

    Constructed once per **Calibration** and called once per **Trial**, from the
    calling thread under :class:`~git_loopy.trial_concurrency.InlineTrialDispatcher`
    and from a worker thread under
    :class:`~git_loopy.trial_concurrency.ThreadedTrialDispatcher`. Every field of
    it is either injected or derived from the request, so the same object serves
    every rung and every slot without holding per-Trial state.

    Args:
        git: The operator's repository client. Used exactly as it is: a worktree
            is added at the **Proving task**'s base commit and removed again, and
            the operator's own branch and main worktree are never addressed.
        candidates: The admitted **Proving set**. A
            :class:`~git_loopy.trial_concurrency.TrialRequest` carries only the
            pin (issue number and two commits); the oracle's paths and the issue
            body arrive here, which is what keeps a Trial off the tracker and the
            whole path offline.
        client: The Copilot client the session is constructed against.
        config: A :class:`~git_loopy.session.SessionConfig`-conforming object.
        event_log: The replay-log writer the session records through.
        sinks: The caller's sink fan-out.
        calibration_id: Identifies this Calibration. Used as the session's
            ``run_id`` and as the working branches' namespace. #371 is where
            Calibration gets its own record and this stops being a Run's id.
        worktree_parent: Where Trial worktrees are created. Defaults to the
            repository's *sibling* directory, never inside it (ADR-0008).
        setup: The worktree preparation seam. Defaults to
            :class:`~git_loopy.worktree.CommandWorktreeSetup`'s auto-detect.
        gate: The pass-to-pass regression guard. Defaults to
            :class:`~git_loopy.gate.AgentsMdGateRunner`, which reads the
            ``AGENTS.md`` **in the worktree it is given** — so the gate that runs
            is the one the base commit declared.
        oracle: The fail-to-pass instrument. Defaults to
            :func:`make_oracle_gate_runner`.
        send_timeout_seconds: The agent session's bound.
        gate_timeout_seconds: The per-loop bound both instruments inherit (#374).
        skill_exposure: The Run-scoped closed-world Skill projection, if any.
        session_factory: Overridable for tests;
            :class:`~git_loopy.session.IterationSession` otherwise, imported
            lazily so this module stays importable without the SDK.
        clock: The monotonic source the wall clock is measured from.
        warn: Optional diagnostic sink. A Trial reports its failures in its
            result; this is for the things that are nobody's failure, like a
            worktree that would not tear down.
    """

    def __init__(
        self,
        *,
        git: GitClient,
        candidates: Sequence[ProvingCandidate],
        client: Any,
        config: Any,
        event_log: Any,
        sinks: Any,
        calibration_id: str,
        worktree_parent: Path | None = None,
        setup: WorktreeSetup | None = None,
        gate: GateRunner | None = None,
        oracle: GateRunner | None = None,
        send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        gate_timeout_seconds: float | None = None,
        skill_exposure: Any = None,
        session_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._git = git
        self._candidates = {
            (c.issue, c.base_commit, c.oracle_commit): c for c in candidates
        }
        self._client = client
        self._config = config
        self._event_log = event_log
        self._sinks = sinks
        self._calibration_id = calibration_id
        self._worktree_parent = Path(
            worktree_parent if worktree_parent is not None else git.root.parent
        )
        self._setup = setup if setup is not None else _default_setup()
        timeout = (
            gate_timeout_seconds
            if gate_timeout_seconds is not None
            else _default_gate_timeout()
        )
        self._gate = gate if gate is not None else _default_gate(timeout)
        self._oracle = (
            oracle
            if oracle is not None
            else make_oracle_gate_runner(timeout_seconds=timeout)
        )
        self._send_timeout_seconds = send_timeout_seconds
        self._skill_exposure = skill_exposure
        self._session_factory = session_factory
        self._clock = clock
        self._warn = warn

    # -- the seam ---------------------------------------------------------- #

    def run(self, request: TrialRequest) -> ReplayTrialResult:
        """Replay one **Proving task** on one candidate pair, and score it.

        Never raises for anything a Trial can be responsible for: an unknown pin,
        a worktree that would not prepare, an oracle no loop covers, a session
        that raised — each comes back red with the detail, because a raise here
        would read as the crashed sibling
        :func:`~git_loopy.trial_concurrency._isolated` reports. A
        :class:`BaseException` — an operator's Ctrl-C — *does* pass through, so
        the search can stop; the worktree is torn down on the way past.
        """
        started = self._clock()
        meter = _CreditMeter()
        candidate = self._candidates.get(
            (request.task.issue, request.task.base_commit, request.task.oracle_commit)
        )
        if candidate is None:
            return self._red(
                started,
                meter,
                f"no admitted Proving task matches the pin "
                f"#{request.task.issue} {request.task.base_commit[:12]}"
                f"..{request.task.oracle_commit[:12]}",
            )

        path = self._worktree_path(request.slot)
        branch = trial_branch_name(self._calibration_id, next(_TRIAL_SEQUENCE))
        try:
            self._git.add_worktree(path, branch=branch, base=candidate.base_commit)
        except GitError as exc:
            return self._red(started, meter, f"the worktree could not be created: {exc}")

        try:
            return self._measure(request, candidate, path, started, meter)
        except Exception as exc:  # noqa: BLE001 — a Trial's fault is its own result
            return self._red(
                started, meter, f"the Trial raised {type(exc).__name__}: {exc}"
            )
        finally:
            # Success, red gate, exception and interruption alike: a cancelled
            # search must not leave manual cleanup behind (ADR-0027).
            self._teardown(path, branch)

    # -- the four steps ---------------------------------------------------- #

    def _measure(
        self,
        request: TrialRequest,
        candidate: ProvingCandidate,
        path: Path,
        started: float,
        meter: _CreditMeter,
    ) -> ReplayTrialResult:
        setup = self._setup.run(path)
        if not setup.passed:
            return self._red(
                started,
                meter,
                f"the worktree would not prepare: {setup.command!r} exited "
                f"{setup.returncode}",
            )

        loops = covering_loops(
            parse_feedback_loops(_read_agents_md(path)), candidate.oracle_paths
        )
        if not loops:
            return self._red(
                started,
                meter,
                "no feedback loop at the base commit covers the oracle paths "
                f"{list(candidate.oracle_paths)}",
            )
        oracle_names = tuple(loop.name for loop in loops)

        apply_oracle(
            path, commit=candidate.oracle_commit, paths=candidate.oracle_paths
        )

        before = self._run_oracle(path, loops)
        if before.passed:
            return self._red(
                started,
                meter,
                "the oracle was already green at the base commit, so this "
                "Proving task measures nothing",
                oracle_loops=oracle_names,
            )

        self._work(candidate, path, request, meter)

        after = self._run_oracle(path, loops)
        if not after.passed:
            return self._red(
                started,
                meter,
                f"the pair did not satisfy the fix's own tests: "
                f"{_why(after)}",
                oracle_loops=oracle_names,
            )

        gate = self._gate.run(path)
        if not gate.passed:
            return self._red(
                started,
                meter,
                f"the AGENTS.md gate went red on {_why(gate)}",
                gate_loops=gate.ran,
                oracle_loops=oracle_names,
            )

        return ReplayTrialResult(
            passed=True,
            credits=meter.credits,
            wall_clock_seconds=self._clock() - started,
            failure=None,
            gate_loops=gate.ran,
            oracle_loops=oracle_names,
        )

    def _run_oracle(self, path: Path, loops: Sequence[FeedbackLoop]) -> GateResult:
        """Run the covering loops from a generated table, and leave nothing behind."""
        table = path / ORACLE_TABLE_FILENAME
        table.write_text(render_oracle_table(loops), encoding="utf-8")
        try:
            return self._oracle.run(path)
        except GateError as exc:
            return GateResult.red(
                (),
                _loop_failure("oracle", str(exc)),
            )
        finally:
            table.unlink(missing_ok=True)

    def _work(
        self,
        candidate: ProvingCandidate,
        path: Path,
        request: TrialRequest,
        meter: _CreditMeter,
    ) -> None:
        """Run the agent session on the candidate pair, in the Trial's worktree."""
        _await(
            self._session(
                candidate=candidate,
                path=path,
                pair=request.candidate,
                meter=meter,
            )
        )

    async def _session(
        self,
        *,
        candidate: ProvingCandidate,
        path: Path,
        pair: Any,
        meter: _CreditMeter,
    ) -> None:
        factory = self._session_factory
        if factory is None:
            # Deferred so this module stays importable — and its rules testable —
            # without the SDK the session layer pulls in.
            from git_loopy.session import IterationSession

            factory = IterationSession
        async with factory(
            self._client,
            config=self._config,
            event_log=self._event_log,
            sinks=self._sinks,
            run_id=self._calibration_id,
            # Not an Iteration: no number to allocate, no Run summary row to
            # occupy, and structurally out of reach of the Strike machine, which
            # is ticked by the orchestrator this path never enters (#371).
            iter_num=None,
            model=pair.model,
            reasoning_effort=pair.effort,
            working_directory=str(path),
            skill_exposure=self._skill_exposure,
            event_observer=meter,
        ) as session:
            await session.send_and_wait(
                trial_prompt(candidate), timeout=self._send_timeout_seconds
            )

    # -- teardown ---------------------------------------------------------- #

    def _teardown(self, path: Path, branch: str) -> None:
        """Remove the worktree *and* its branch — a Trial leaves no breadcrumb.

        ADR-0008 keeps a failed **Lane**'s branch deliberately, as evidence for a
        human to read. A Trial's branch is not evidence of anything: nothing
        merges it, nobody reviews it, and one Calibration mints hundreds. Both go.
        Best-effort throughout, because a teardown that raised would replace a
        measured result with a crash.
        """
        try:
            self._git.remove_worktree(path, force=True)
        except Exception as exc:  # noqa: BLE001
            self._report(f"the Trial worktree at {path} could not be removed: {exc}")
        try:
            self._git.delete_branch(branch)
        except Exception as exc:  # noqa: BLE001
            self._report(f"the Trial branch {branch} could not be deleted: {exc}")

    # -- helpers ----------------------------------------------------------- #

    def _worktree_path(self, slot: int) -> Path:
        """The slot's worktree, in a sibling directory outside the repo (ADR-0008).

        Keyed on the **slot** and not on the Trial, which is what bounds a host
        prepared for ``width`` worktrees to holding exactly that many however
        many Trials a Calibration runs.
        """
        return self._worktree_parent / (
            f"{self._git.root.name}-calibration-{self._calibration_id}-slot-{slot}"
        )

    def _red(
        self,
        started: float,
        meter: _CreditMeter,
        failure: str,
        *,
        gate_loops: tuple[str, ...] = (),
        oracle_loops: tuple[str, ...] = (),
    ) -> ReplayTrialResult:
        return ReplayTrialResult(
            passed=False,
            credits=meter.credits,
            wall_clock_seconds=self._clock() - started,
            failure=failure,
            gate_loops=gate_loops,
            oracle_loops=oracle_loops,
        )

    def _report(self, message: str) -> None:
        if self._warn is None:
            return
        try:
            self._warn(message)
        except Exception:  # pragma: no cover - defensive
            pass


def _read_agents_md(worktree: Path) -> str:
    try:
        return (worktree / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _why(result: GateResult) -> str:
    if result.failure is None:  # pragma: no cover - defensive
        return "an unnamed loop"
    return f"{result.failure.name!r} ({result.failure.summary})"


def _loop_failure(name: str, detail: str) -> Any:
    from git_loopy.gate import LoopFailure

    return LoopFailure(name=name, command="", returncode=1, output_tail=detail)


def _await(coroutine: Any) -> None:
    """Drive ``coroutine`` to completion from a synchronous ``run``.

    ``TrialRunner.run`` is synchronous by contract, and under
    :class:`~git_loopy.trial_concurrency.ThreadedTrialDispatcher` it is also on a
    worker thread with no loop of its own — so ``asyncio.run`` is right in the
    ordinary case. The exception is a caller that is *already* inside a loop
    (``calibrate`` reaching an inline dispatcher from async code), where
    ``asyncio.run`` raises; that gets its own thread, because the alternative is
    a Trial that cannot run at all.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coroutine)
        return
    raised: list[BaseException] = []

    def _drive() -> None:
        try:
            asyncio.run(coroutine)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
            raised.append(exc)

    thread = threading.Thread(target=_drive, name="git-loopy-trial-session")
    thread.start()
    thread.join()
    if raised:
        raise raised[0]


def _default_setup() -> WorktreeSetup:
    from git_loopy.worktree import CommandWorktreeSetup

    return CommandWorktreeSetup()


def _default_gate(timeout_seconds: float) -> GateRunner:
    from git_loopy.gate import AgentsMdGateRunner

    return AgentsMdGateRunner(timeout_seconds=timeout_seconds)


def _default_gate_timeout() -> float:
    import os

    from git_loopy.gate import resolve_gate_timeout_seconds

    return resolve_gate_timeout_seconds(os.environ)
