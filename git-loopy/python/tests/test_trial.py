"""Tests for ``git_loopy.trial`` — the production **Trial** runner (#369, ADR-0027).

One candidate pair works one **Proving task** in its own worktree at a historical
commit, scored by the fix's own tests with the AGENTS.md gate kept as a
pass-to-pass regression guard.

The suite is offline throughout: real temporary git repositories for the
isolation and oracle halves (matching how :mod:`git_loopy.git` and
:mod:`git_loopy.worktree` are already tested), trivial POSIX commands for the
real gate runner, and the scripted Copilot fake for the session.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import pytest
from copilot.generated.session_events import (
    AssistantUsageCopilotUsage,
    AssistantUsageData,
    SessionEvent,
    SessionEventType,
)

from git_loopy.gate import AgentsMdGateRunner, FeedbackLoop, parse_feedback_loops
from git_loopy.git import SubprocessGitClient
from git_loopy.measured_routing import ProvingTask
from git_loopy.proving_set import ProvingCandidate
from git_loopy.staircase import Candidate
from git_loopy import trial as trial_module
from git_loopy.trial import (
    ORACLE_TABLE_FILENAME,
    ReplayTrialRunner,
    apply_oracle,
    covering_loops,
    make_oracle_gate_runner,
    render_oracle_table,
    trial_prompt,
)
from git_loopy.trial_concurrency import TrialRequest, TrialResult, TrialRunner

from tests.fakes import FakeGateRunner

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH; a Trial cannot create a worktree",
)


# --------------------------------------------------------------------------- #
# The oracle's loop selection — pure, and narrower than the gate               #
# --------------------------------------------------------------------------- #

#: This repository's own table, which is the one the rule has to work on.
_REAL_TABLE = (
    FeedbackLoop(
        name="Shell syntax",
        command=(
            "bash -n git-loopy/shell/git-loopy.sh git-loopy/shell/install.sh "
            "git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh"
        ),
    ),
    FeedbackLoop(
        name="Python suite",
        command=(
            "uv run --project git-loopy/python --all-extras python -m pytest -q "
            "git-loopy/python/tests"
        ),
    ),
    FeedbackLoop(
        name="Rust Dashboard core",
        command="cargo test --manifest-path git-loopy/tui/Cargo.toml",
    ),
)


def test_the_oracle_selects_the_narrowest_loop_that_covers_a_test_path() -> None:
    """The oracle is *narrower than the gate*, and this is where that happens.

    ADR-0027's amendment rejects the whole-repo gate as a per-task scorer: it is
    fail-fast over every loop, so a base commit red for any unrelated reason
    fails every pair at every rung. Selecting the loop whose command already
    names the deepest directory containing the oracle's test path keeps the
    fail-to-pass measurement on the fix's own tests.
    """
    selected = covering_loops(
        _REAL_TABLE,
        ("git-loopy/python/tests/test_roster_drift.py",),
    )

    assert [loop.name for loop in selected] == ["Python suite"]


def test_the_oracle_covers_every_one_of_its_test_paths() -> None:
    """A fix that shipped tests in two languages needs both loops, not one.

    The selection is per-path and then unioned, because a Proving task whose
    oracle spans two members would otherwise be scored on half of itself.
    """
    selected = covering_loops(
        _REAL_TABLE,
        (
            "git-loopy/python/tests/test_gate.py",
            "git-loopy/tui/tests/activity_band.rs",
        ),
    )

    assert [loop.name for loop in selected] == ["Python suite", "Rust Dashboard core"]


def test_a_loop_covering_two_paths_is_selected_once() -> None:
    """The union is a set in table order — a loop cannot run twice in one oracle."""
    selected = covering_loops(
        _REAL_TABLE,
        (
            "git-loopy/python/tests/test_gate.py",
            "git-loopy/python/tests/test_git.py",
        ),
    )

    assert [loop.name for loop in selected] == ["Python suite"]


def test_a_placeholder_row_never_becomes_an_oracle() -> None:
    """``FeedbackLoop.runnable`` screens the same rows here as it does at the gate.

    A fresh repository's ``<PLACEHOLDER>`` stub is not a command, and an oracle
    built from one would score every pair on a shell error.
    """
    table = (
        FeedbackLoop(name="Tests", command="<TEST_COMMAND> tests"),
        FeedbackLoop(name="Empty", command=""),
    )

    assert covering_loops(table, ("tests/test_thing.py",)) == ()


def test_a_test_path_no_loop_covers_selects_nothing() -> None:
    """No cover is an answer, not an exception.

    The **Trial** turns this into a red result carrying the detail (a task this
    repository's table cannot score), rather than raising into the dispatcher and
    reading as a crashed sibling.
    """
    assert covering_loops(_REAL_TABLE, ("docs/adr/0027-whatever.md",)) == ()


# --------------------------------------------------------------------------- #
# The oracle runs through the real gate runner, over a generated table         #
# --------------------------------------------------------------------------- #


def test_the_generated_oracle_table_runs_through_the_real_gate_runner(
    tmp_path: Path,
) -> None:
    """Reuse, not a second bounded-subprocess runner.

    ``AgentsMdGateRunner`` is already parameterised by the filename it reads, so
    pointing it at a generated single-purpose table gives the oracle the same
    fail-fast execution, the same per-loop wall-clock bound and the same
    ``LoopFailure`` detail the gate has — with no second code path to keep in
    step. Exercised here with trivial POSIX commands, as #369 asks.
    """
    (tmp_path / ORACLE_TABLE_FILENAME).write_text(
        render_oracle_table(
            (
                FeedbackLoop(name="Python suite", command="true"),
                FeedbackLoop(name="Rust core", command="true"),
            )
        ),
        encoding="utf-8",
    )

    result = make_oracle_gate_runner(timeout_seconds=30.0).run(tmp_path)

    assert result.passed is True
    assert result.ran == ("Python suite", "Rust core")


def test_a_red_oracle_loop_comes_back_as_a_loop_failure(tmp_path: Path) -> None:
    """Fail-fast with the detail attached, exactly as the gate reports it."""
    (tmp_path / ORACLE_TABLE_FILENAME).write_text(
        render_oracle_table(
            (
                FeedbackLoop(name="Python suite", command="echo boom >&2; exit 3"),
                FeedbackLoop(name="Never reached", command="true"),
            )
        ),
        encoding="utf-8",
    )

    result = make_oracle_gate_runner(timeout_seconds=30.0).run(tmp_path)

    assert result.passed is False
    assert result.ran == ("Python suite",)
    assert result.failure is not None
    assert result.failure.returncode == 3
    assert "boom" in result.failure.output_tail


def test_a_command_carrying_a_pipe_survives_the_generated_table(
    tmp_path: Path,
) -> None:
    """The table is markdown, so an unescaped ``|`` would truncate the command.

    Real feedback-loop commands pipe and chain — ``bash -n ... || exit 1`` is in
    this repository's own table — and a loop silently reduced to its first
    fragment would score every pair on a command nobody wrote.
    """
    (tmp_path / ORACLE_TABLE_FILENAME).write_text(
        render_oracle_table(
            (FeedbackLoop(name="Piped", command="false || echo recovered"),)
        ),
        encoding="utf-8",
    )
    parsed = parse_feedback_loops(
        (tmp_path / ORACLE_TABLE_FILENAME).read_text(encoding="utf-8")
    )

    assert [loop.command for loop in parsed] == ["false || echo recovered"]
    assert make_oracle_gate_runner(timeout_seconds=30.0).run(tmp_path).passed is True


# --------------------------------------------------------------------------- #
# A real temporary git repository, with a real fixing commit to replay         #
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _write(repo: Path, relative: str, text: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for key, value in (
        ("user.email", "tester@example.com"),
        ("user.name", "Tester"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        _git(path, "config", key, value)


@dataclass(frozen=True)
class _History:
    """A repository carrying one replayable fix, and the two commits that pin it."""

    repo: Path
    base_commit: str
    oracle_commit: str


def _history(tmp_path: Path, *, rename_the_test: bool = False) -> _History:
    """A repo whose second commit is a fix with its own test change.

    The base commit ships a stale test that passes and a source file that is
    wrong; the fixing commit ships the corrected source *and* a test that fails
    against the old source. That is the fail-before / pass-after property the
    whole replay rests on, made real rather than scripted.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _write(repo, "src/answer.txt", "41")
    _write(repo, "tests/check-old.sh", "#!/bin/sh\nexit 0\n")
    _write(
        repo,
        "AGENTS.md",
        "# Agents\n\n"
        "## Feedback loops\n\n"
        "| Loop | Command |\n"
        "| --- | --- |\n"
        "| Tests | sh tests/*.sh |\n"
        "| Docs | test -f README.md |\n",
    )
    _write(repo, "README.md", "readme\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base_commit = _git(repo, "rev-parse", "HEAD").strip()

    _write(repo, "src/answer.txt", "42")
    _write(
        repo,
        "tests/check-answer.sh",
        '#!/bin/sh\ntest "$(cat src/answer.txt)" = "42"\n',
    )
    if rename_the_test:
        (repo / "tests/check-old.sh").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: the answer is 42\n\nCloses #7")
    oracle_commit = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", base_commit, "--detach")
    _git(repo, "checkout", "-q", "main")
    return _History(repo=repo, base_commit=base_commit, oracle_commit=oracle_commit)


def test_the_oracle_brings_the_fixs_tests_and_never_the_fix(tmp_path: Path) -> None:
    """Passing must mean solving the problem, not matching a diff.

    ADR-0027 admits a replay only because the agent is handed the *tests* the fix
    shipped and not the fix — a worktree carrying the source change would score a
    pair on reading a diff.
    """
    history = _history(tmp_path)
    work = tmp_path / "work"
    _git(
        history.repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "replay",
        str(work),
        history.base_commit,
    )

    apply_oracle(
        work,
        commit=history.oracle_commit,
        paths=("tests/check-answer.sh",),
    )

    assert (work / "tests/check-answer.sh").is_file()
    assert (work / "src/answer.txt").read_text(encoding="utf-8").strip() == "41"


def test_a_test_the_fix_deleted_is_deleted_by_the_oracle(tmp_path: Path) -> None:
    """A renamed test is a delete plus an add, and both halves are the oracle.

    Carrying only the add leaves the superseded file behind, and a stale test
    that can never pass makes the oracle unable to go green for *any* pair —
    which reads as every model being too weak.
    """
    history = _history(tmp_path, rename_the_test=True)
    work = tmp_path / "work"
    _git(
        history.repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "replay",
        str(work),
        history.base_commit,
    )

    apply_oracle(
        work,
        commit=history.oracle_commit,
        paths=("tests/check-answer.sh", "tests/check-old.sh"),
    )

    assert (work / "tests/check-answer.sh").is_file()
    assert not (work / "tests/check-old.sh").exists()


# --------------------------------------------------------------------------- #
# The scripted Copilot fake — the agent's "work" is a real edit in the worktree #
# --------------------------------------------------------------------------- #


class _FakeCopilotSession:
    """Stub :class:`copilot.CopilotSession`: run ``on_send``, emit scripted events."""

    def __init__(
        self,
        *,
        on_event: Callable[[Any], None] | None,
        on_send: Callable[[Path], None] | None,
        working_directory: str | None,
        usage_events: Sequence[float],
        raises: BaseException | None,
    ) -> None:
        self._on_event = on_event
        self._on_send = on_send
        self._working_directory = working_directory
        self._usage_events = usage_events
        self._raises = raises
        self.session_id = "fake-session-id"
        self.prompts: list[str] = []

    async def send_and_wait(self, prompt: str, *, timeout: float = 60.0) -> None:
        self.prompts.append(prompt)
        for credits in self._usage_events:
            if self._on_event is not None:
                self._on_event(_sdk_usage_event(credits))
        if self._on_send is not None:
            self._on_send(Path(self._working_directory or "."))
        if self._raises is not None:
            raise self._raises

    async def disconnect(self) -> None:
        return None


class _FakeCopilotClient:
    """Stub :class:`copilot.CopilotClient` recording every ``create_session`` call."""

    def __init__(
        self,
        *,
        on_send: Callable[[Path], None] | None = None,
        usage_events: Sequence[float] = (),
        raises: BaseException | None = None,
    ) -> None:
        self._on_send = on_send
        self._usage_events = usage_events
        self._raises = raises
        self.create_calls: list[dict[str, Any]] = []
        self.created: list[_FakeCopilotSession] = []

    async def create_session(
        self,
        *,
        on_permission_request: Any = None,
        on_event: Callable[[Any], None] | None = None,
        model: str | None = None,
        working_directory: str | None = None,
        **extra: Any,
    ) -> _FakeCopilotSession:
        if self._raises is not None and isinstance(self._raises, _RefuseSession):
            raise self._raises
        self.create_calls.append(
            {"model": model, "working_directory": working_directory, **extra}
        )
        session = _FakeCopilotSession(
            on_event=on_event,
            on_send=self._on_send,
            working_directory=working_directory,
            usage_events=self._usage_events,
            raises=self._raises,
        )
        self.created.append(session)
        return session


def _sdk_usage_event(credits: float) -> SessionEvent:
    """One ``ASSISTANT_USAGE`` datum, billed in nano-**AI Credits** as the harness bills."""
    return SessionEvent(
        data=AssistantUsageData(
            model="candidate-model",
            input_tokens=1000.0,
            output_tokens=100.0,
            copilot_usage=AssistantUsageCopilotUsage(
                total_nano_aiu=credits * 1_000_000_000
            ),
        ),
        id=uuid4(),
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        type=SessionEventType.ASSISTANT_USAGE,
    )


class _StubConfig:
    deny_tools: frozenset[str] = frozenset()
    deny_skills: frozenset[str] = frozenset()
    verbosity = 0
    render_reasoning = True


class _NullEventLog:
    def write(self, envelope: Mapping[str, Any]) -> None:
        return None


class _NullSinks:
    def dispatch(self, *args: Any, **kwargs: Any) -> None:
        return None

    def reasoning_delta(self, *args: Any, **kwargs: Any) -> None:
        return None

    def message_delta(self, *args: Any, **kwargs: Any) -> None:
        return None


class _RefuseSession(RuntimeError):
    """A client that cannot create a session at all."""


# --------------------------------------------------------------------------- #
# Building a Trial runner over a real repository                               #
# --------------------------------------------------------------------------- #


def _candidate(history: _History, *, oracle_paths: tuple[str, ...]) -> ProvingCandidate:
    return ProvingCandidate(
        issue=7,
        task_type="bugfix",
        base_commit=history.base_commit,
        oracle_commit=history.oracle_commit,
        oracle_paths=oracle_paths,
        task_text="## What to build\n\nThe answer must be 42.\n",
    )


def _runner(
    history: _History,
    tmp_path: Path,
    *,
    client: _FakeCopilotClient,
    oracle_paths: tuple[str, ...] = ("tests/check-answer.sh",),
    gate: Any = None,
    setup: Any = None,
    candidates: Sequence[ProvingCandidate] | None = None,
    clock: Callable[[], float] | None = None,
) -> ReplayTrialRunner:
    candidate = _candidate(history, oracle_paths=oracle_paths)
    return ReplayTrialRunner(
        git=SubprocessGitClient(history.repo),
        candidates=candidates if candidates is not None else (candidate,),
        client=client,
        config=_StubConfig(),
        event_log=_NullEventLog(),
        sinks=_NullSinks(),
        calibration_id="cal01",
        worktree_parent=tmp_path / "trials",
        gate=gate if gate is not None else FakeGateRunner(),
        setup=setup,
        clock=clock or time.monotonic,
    )


def _request(history: _History, *, slot: int = 0) -> TrialRequest:
    return TrialRequest(
        candidate=Candidate(model="candidate-model", effort="high", multiplier=1.0),
        task=ProvingTask(
            issue=7,
            base_commit=history.base_commit,
            oracle_commit=history.oracle_commit,
        ),
        slot=slot,
    )


def _fixes_the_answer(worktree: Path) -> None:
    """What the pair under test "does": the real edit the oracle is asking for."""
    (worktree / "src/answer.txt").write_text("42\n", encoding="utf-8")


def _does_nothing(worktree: Path) -> None:
    return None


# --------------------------------------------------------------------------- #
# The Trial itself                                                             #
# --------------------------------------------------------------------------- #


def test_a_pair_that_satisfies_the_fixs_own_tests_passes_the_trial(
    tmp_path: Path,
) -> None:
    """Fail-before, pass-after, and a green gate — the whole scoring rule at once.

    The oracle is genuinely run twice against a real worktree at a real
    historical commit: red before the pair works (the fix's test is present and
    the source is not), green after. ADR-0027 keeps the whole-repo gate beside it
    as the pass-to-pass regression guard, so both instruments must agree before a
    Trial passes.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.passed is True
    assert result.failure is None
    assert result.oracle_loops == ("Tests",)


def test_a_pair_that_leaves_the_fixs_tests_red_fails_the_trial(
    tmp_path: Path,
) -> None:
    """The oracle decides whether the pair solved the **Proving task**."""
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_does_nothing)
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.passed is False
    assert result.failure is not None
    assert "did not satisfy" in result.failure


def test_an_oracle_already_green_at_the_base_commit_fails_the_trial(
    tmp_path: Path,
) -> None:
    """A task nobody has to solve measures nothing, and must not read as a pass.

    Mining selects on metadata and *"ships a test change"* does not imply the
    test fails before the fix (ADR-0027). An already-solved task that scored as a
    pass would promote whichever pair happened to draw it, having measured
    nothing at all. #380 excludes these at admission; the Trial refuses to score
    one that reaches it anyway.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(
        history, tmp_path, client=client, oracle_paths=("tests/check-old.sh",)
    ).run(_request(history))

    assert result.passed is False
    assert result.failure is not None
    assert "already" in result.failure
    assert client.create_calls == [], "an unmeasurable task must spend nothing"


def test_a_red_gate_fails_a_trial_that_satisfied_its_own_oracle(
    tmp_path: Path,
) -> None:
    """The regression guard is the point of keeping the gate at all.

    ADR-0027 rejected dropping it: it is the only thing standing between a cheap
    pair and work that passes its own tests while breaking everything around it.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(
        history, tmp_path, client=client, gate=FakeGateRunner(default=False)
    ).run(_request(history))

    assert result.passed is False
    assert result.failure is not None
    assert "gate" in result.failure


def test_the_trial_records_the_gate_the_base_commit_declared(tmp_path: Path) -> None:
    """Which gate ran is a fact about the base commit, and a reader may check it.

    Both instruments read the worktree they are given, and that worktree sits at
    a months-old commit — so strengthening today's feedback-loop table does not
    improve a Calibration measured against a frozen set (ADR-0027). Recording the
    loops that actually ran is what lets a reader see that rather than assume it.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(
        history, tmp_path, client=client, gate=AgentsMdGateRunner(timeout_seconds=60.0)
    ).run(_request(history))

    assert result.passed is True
    assert result.gate_loops == ("Tests", "Docs")


# --------------------------------------------------------------------------- #
# The session: constructed directly, on the pair the search chose              #
# --------------------------------------------------------------------------- #


def test_the_session_runs_the_pair_the_search_chose_in_the_trials_worktree(
    tmp_path: Path,
) -> None:
    """No meta-model and no privileged model: the Trial *is* the pair under test.

    Each Trial is run by the candidate under test, which is what makes the search
    free of a bootstrap paradox (ADR-0027). The session is constructed directly
    against the client — not through the orchestrator — which is also what keeps
    a Trial out of a **Run**'s accounting.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    request = TrialRequest(
        candidate=Candidate(model="claude-haiku-4.5", effort=None, multiplier=0.33),
        task=ProvingTask(
            issue=7,
            base_commit=history.base_commit,
            oracle_commit=history.oracle_commit,
        ),
        slot=3,
    )

    result = _runner(history, tmp_path, client=client).run(request)

    assert result.passed is True
    assert len(client.create_calls) == 1
    call = client.create_calls[0]
    assert call["model"] == "claude-haiku-4.5"
    assert call["reasoning_effort"] is None
    assert Path(call["working_directory"]).name.endswith("slot-3")


def test_a_trial_allocates_no_iteration_number(tmp_path: Path) -> None:
    """A **Trial** is not an **Iteration**, and this is where that is structural.

    Strikes are shared and consecutive and reaching the limit ends the Run, so a
    Trial that ticked one could terminate something it has nothing to do with.
    ``iter_num=None`` is the run-scope value, the same carve-out
    :mod:`git_loopy.task_type_session` uses; #371 pins the consequences.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    seen: list[Any] = []

    class _RecordingSession:
        def __init__(self, _client: Any, **kwargs: Any) -> None:
            seen.append(kwargs)

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        async def send_and_wait(self, prompt: str, *, timeout: float) -> None:
            _fixes_the_answer(Path(seen[-1]["working_directory"]))

    runner = ReplayTrialRunner(
        git=SubprocessGitClient(history.repo),
        candidates=(_candidate(history, oracle_paths=("tests/check-answer.sh",)),),
        client=client,
        config=_StubConfig(),
        event_log=_NullEventLog(),
        sinks=_NullSinks(),
        calibration_id="cal01",
        worktree_parent=tmp_path / "trials",
        gate=FakeGateRunner(),
        session_factory=_RecordingSession,
    )
    result = runner.run(_request(history))

    assert result.passed is True
    assert seen[0]["iter_num"] is None


def test_the_prompt_carries_the_issue_as_it_was_stated(tmp_path: Path) -> None:
    """The work is the issue body — the task as it was actually written.

    And nothing a **Run**'s prompt does that a Trial must not: a Trial has no
    tracker to read, nothing to commit and no issue to close.
    """
    history = _history(tmp_path)
    prompt = trial_prompt(_candidate(history, oracle_paths=("tests/check-answer.sh",)))

    assert "The answer must be 42." in prompt
    assert "Do not commit" in prompt


# --------------------------------------------------------------------------- #
# Nothing survives                                                             #
# --------------------------------------------------------------------------- #


def _branches(repo: Path) -> list[str]:
    return _git(repo, "branch", "--format=%(refname:short)").split()


def test_a_passing_trial_leaves_no_worktree_and_no_branch(tmp_path: Path) -> None:
    """A Calibration mints hundreds of these; none of them is evidence of anything.

    ADR-0008 keeps a failed **Lane**'s branch deliberately, for a human to read.
    Nothing merges or reviews a Trial's branch, so both it and the worktree go.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    _runner(history, tmp_path, client=client).run(_request(history))

    assert list((tmp_path / "trials").glob("*")) == []
    assert _branches(history.repo) == ["main"]


def test_no_worktree_survives_an_exception(tmp_path: Path) -> None:
    """The one #369 asks for by name: an exception must not leak a worktree."""
    history = _history(tmp_path)
    client = _FakeCopilotClient(raises=_RefuseSession("no session for you"))
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.passed is False
    assert result.failure is not None and "RefuseSession" in result.failure
    assert list((tmp_path / "trials").glob("*")) == []
    assert _branches(history.repo) == ["main"]


def test_an_interrupt_passes_through_and_still_tears_the_worktree_down(
    tmp_path: Path,
) -> None:
    """An operator's Ctrl-C stops the search *and* leaves no manual cleanup.

    The interrupt has to propagate — the dispatcher turns it into a
    :class:`~git_loopy.trial_concurrency.TrialInterrupt` carrying what was already
    paid for, and a runner that swallowed it would make the Calibration
    unstoppable. Tearing down on the way past is this module's own ``finally``,
    which is exactly the obligation ``trial_concurrency`` leaves here.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer, raises=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _runner(history, tmp_path, client=client).run(_request(history))

    assert list((tmp_path / "trials").glob("*")) == []
    assert _branches(history.repo) == ["main"]


def test_the_operators_branch_and_main_worktree_are_never_touched(
    tmp_path: Path,
) -> None:
    """A Calibration must never modify what the operator is working on."""
    history = _history(tmp_path)
    before = (
        _git(history.repo, "rev-parse", "HEAD"),
        _git(history.repo, "rev-parse", "--abbrev-ref", "HEAD"),
        _git(history.repo, "status", "--porcelain"),
        (history.repo / "src/answer.txt").read_text(encoding="utf-8"),
    )

    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    _runner(history, tmp_path, client=client).run(_request(history))

    assert (
        _git(history.repo, "rev-parse", "HEAD"),
        _git(history.repo, "rev-parse", "--abbrev-ref", "HEAD"),
        _git(history.repo, "status", "--porcelain"),
        (history.repo / "src/answer.txt").read_text(encoding="utf-8"),
    ) == before


def test_concurrent_slots_share_no_worktree_and_no_working_branch(
    tmp_path: Path,
) -> None:
    """The slot is the isolation, and the runner keys both off it.

    ``trial_concurrency`` guarantees no two Trials in flight hold the same slot;
    a runner that named its own worktree would be asserting the uniqueness rather
    than being given it.
    """
    history = _history(tmp_path)
    observed: list[tuple[str, str]] = []

    def _record_and_fix(worktree: Path) -> None:
        observed.append(
            (
                str(worktree),
                _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip(),
            )
        )
        _fixes_the_answer(worktree)

    client = _FakeCopilotClient(on_send=_record_and_fix)
    runner = _runner(history, tmp_path, client=client)
    runner.run(_request(history, slot=0))
    runner.run(_request(history, slot=1))

    assert observed[0][0] != observed[1][0], "two slots shared a worktree"
    assert observed[0][1] != observed[1][1], "two slots shared a working branch"


# --------------------------------------------------------------------------- #
# What a Trial reports                                                         #
# --------------------------------------------------------------------------- #


def test_the_credits_are_the_harnesss_own_billed_figure(tmp_path: Path) -> None:
    """Consumption is read off the harness, never recomputed from tokens (ADR-0026)."""
    history = _history(tmp_path)
    client = _FakeCopilotClient(
        on_send=_fixes_the_answer, usage_events=(0.25, 0.75)
    )
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.credits == Decimal("1.0")


def test_a_session_the_harness_never_billed_reports_unknown_not_zero(
    tmp_path: Path,
) -> None:
    """Unknown is unknown (ADR-0026). A Trial reported as free is a Trial that lies.

    It matters most on the red path this test does *not* take: a crashed session
    may well have been billed, so zeroing it would understate a Calibration's
    burn exactly where the credit ceiling is meant to bite.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.credits is None


def test_the_wall_clock_is_the_trials_own_elapsed_time(tmp_path: Path) -> None:
    """Measured by the Trial, end to end — the third key the search scores on."""
    history = _history(tmp_path)
    ticks = iter([100.0, 137.5])
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(
        history, tmp_path, client=client, clock=lambda: next(ticks)
    ).run(_request(history))

    assert result.wall_clock_seconds == pytest.approx(37.5)


def test_a_pin_no_admitted_task_matches_is_red_and_spends_nothing(
    tmp_path: Path,
) -> None:
    """A Calibration measures only against the admitted set (#380).

    The pin is all a :class:`TrialRequest` carries, so a runner that guessed at an
    unrecognised one would be replaying something nobody verified — and would
    spend an **AI Credit** doing it.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    request = TrialRequest(
        candidate=Candidate(model="m", effort=None, multiplier=1.0),
        task=ProvingTask(
            issue=999,
            base_commit=history.base_commit,
            oracle_commit=history.oracle_commit,
        ),
        slot=0,
    )

    result = _runner(history, tmp_path, client=client).run(request)

    assert result.passed is False
    assert result.failure is not None and "no admitted Proving task" in result.failure
    assert client.create_calls == []
    assert list((tmp_path / "trials").glob("*")) == []


def test_a_worktree_that_will_not_prepare_fails_before_the_session(
    tmp_path: Path,
) -> None:
    """A Trial measured in an unprepared worktree measures the environment.

    Every loop would go red for want of an installed dependency, at every rung,
    and the search would report *incomplete* with nothing to say why.
    """
    history = _history(tmp_path)

    class _RefusingSetup:
        def run(self, worktree: Path) -> Any:
            from git_loopy.worktree import SetupResult

            return SetupResult(command="uv sync", returncode=1, output_tail="nope")

    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(history, tmp_path, client=client, setup=_RefusingSetup()).run(
        _request(history)
    )

    assert result.passed is False
    assert result.failure is not None and "would not prepare" in result.failure
    assert client.create_calls == []


def test_a_task_no_feedback_loop_covers_is_red_with_its_reason(
    tmp_path: Path,
) -> None:
    """No oracle means no measurement, and a Trial says so instead of guessing."""
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(
        history, tmp_path, client=client, oracle_paths=("docs/adr/0001-a.md",)
    ).run(_request(history))

    assert result.passed is False
    assert result.failure is not None and "no feedback loop" in result.failure
    assert client.create_calls == []


def test_the_production_runner_satisfies_the_trial_runner_seam_structurally(
    tmp_path: Path,
) -> None:
    """Production and the scripted fake are interchangeable without inheritance."""
    history = _history(tmp_path)
    runner = _runner(history, tmp_path, client=_FakeCopilotClient())

    assert isinstance(runner, TrialRunner)


def test_a_replay_trial_result_is_a_trial_result(tmp_path: Path) -> None:
    """The search must not have to know the production runner exists.

    ``gate_loops`` and ``oracle_loops`` are a record, not a fourth scoring key —
    ``TrialResult``'s own field set stays exactly the three ADR-0027 scores on,
    plus the failure detail.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert isinstance(result, TrialResult)
    assert {field.name for field in dataclasses.fields(TrialResult)} == {
        "passed",
        "credits",
        "wall_clock_seconds",
        "failure",
    }


def test_no_trial_reaches_the_tracker_or_lands_a_diff_anywhere() -> None:
    """Only historical replay is built here, and the imports say so.

    Two claims a reader cannot check by walking the flow. **No live issue**: a
    Trial replays a *closed* issue whose body arrives with the admitted
    candidates, so reaching :mod:`git_loopy.gh` would be the live-trialling #369
    puts explicitly out of scope. **Nothing lands**: a Trial's diff is never
    merged, pushed or committed anywhere — the worktree is destroyed with the
    work still in it — so the git verbs that would land one may not be reached.
    """
    tree = ast.parse(Path(trial_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None, "from-import with no module name"
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    assert "git_loopy.gh" not in imported
    assert "git_loopy.loop" not in imported, "a Trial never enters the orchestrator"

    reached = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not reached & {"merge", "push", "commit", "add_all", "switch"}


def test_the_agent_sees_the_oracles_tests_and_not_the_oracles_table(
    tmp_path: Path,
) -> None:
    """Two facts about the worktree at the instant the session starts.

    The fix's tests are **present**, because an agent that cannot see what it
    must satisfy is being scored on guessing. The generated oracle table is
    **absent**, because it is scaffolding: an agent that found the list of loops
    it is judged by could edit it, and the measurement would be of the
    scaffolding rather than of the work.
    """
    history = _history(tmp_path)
    seen: dict[str, bool] = {}

    def _inspect_and_fix(worktree: Path) -> None:
        seen["oracle_test"] = (worktree / "tests/check-answer.sh").is_file()
        seen["oracle_table"] = (worktree / ORACLE_TABLE_FILENAME).exists()
        _fixes_the_answer(worktree)

    client = _FakeCopilotClient(on_send=_inspect_and_fix)
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.passed is True
    assert seen == {"oracle_test": True, "oracle_table": False}


def test_a_session_that_raised_after_being_billed_still_reports_its_credits(
    tmp_path: Path,
) -> None:
    """A crashed Trial is red; it is not free.

    ADR-0026's rule is that unknown is unknown — it is not licence to report a
    figure the harness *did* give as zero. A Calibration whose failures spent
    nothing on paper would blow through its credit ceiling without ever tripping
    it.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(
        on_send=_fixes_the_answer,
        usage_events=(0.5,),
        raises=RuntimeError("the harness fell over"),
    )
    result = _runner(history, tmp_path, client=client).run(_request(history))

    assert result.passed is False
    assert result.credits == Decimal("0.5")
    assert list((tmp_path / "trials").glob("*")) == []


def test_a_slot_whose_teardown_failed_is_reclaimed_by_the_next_trial(
    tmp_path: Path,
) -> None:
    """One failed teardown must not kill a slot for the rest of a Calibration.

    ``add_worktree`` refuses a path that already exists, so a leaked worktree
    would make every remaining Trial in that slot red — hours of an unattended
    search reporting *incomplete* over one stale directory. The path is
    namespaced by the Calibration and the slot, so anything sitting at it can
    only be a Trial of ours, which is what makes reclaiming it safe rather than
    destructive.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    runner = _runner(history, tmp_path, client=client)
    leaked = (
        tmp_path
        / "trials"
        / f"{history.repo.name}-calibration-cal01-slot-0"
    )
    _git(history.repo, "worktree", "add", "-q", "-b", "leaked", str(leaked), "main")

    result = runner.run(_request(history, slot=0))

    assert result.passed is True
    assert list((tmp_path / "trials").glob("*")) == []


def test_a_trial_runs_from_inside_a_running_event_loop(tmp_path: Path) -> None:
    """``TrialRunner.run`` is synchronous by contract, and its caller may not be.

    Under ``ThreadedTrialDispatcher`` a Trial lands on a worker thread with no
    loop of its own, so ``asyncio.run`` is right. ``InlineTrialDispatcher``
    reached from async code — which is how ``calibrate`` will drive a serial
    search — leaves ``run`` on a thread that already has one, where ``asyncio.run``
    raises. A Trial that could not run at all there would make the serial
    dispatcher, the one every fixture drives, the broken case.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(on_send=_fixes_the_answer)
    runner = _runner(history, tmp_path, client=client)

    async def _drive() -> Any:
        return runner.run(_request(history))

    result = asyncio.run(_drive())

    assert result.passed is True
    assert list((tmp_path / "trials").glob("*")) == []


def test_a_session_that_raised_inside_a_running_event_loop_is_still_reported(
    tmp_path: Path,
) -> None:
    """The exception has to come back across the thread boundary, not vanish.

    A session failure swallowed by the driving thread would read as a pair that
    silently declined to work — a red Trial with no reason, which is the one
    thing a Trial result must never be.
    """
    history = _history(tmp_path)
    client = _FakeCopilotClient(raises=_RefuseSession("no session for you"))
    runner = _runner(history, tmp_path, client=client)

    async def _drive() -> Any:
        return runner.run(_request(history))

    result = asyncio.run(_drive())

    assert result.passed is False
    assert result.failure is not None and "RefuseSession" in result.failure
    assert list((tmp_path / "trials").glob("*")) == []
