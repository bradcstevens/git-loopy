"""The GitHub Actions worker: what runs inside the dispatched job (#460).

Every test here runs the worker in-process with the Copilot backend stubbed
out. Nothing dispatches a workflow, nothing reaches the network, and nothing
needs a runner — the worker's whole contract is *read a request, run one
session, leave an artifact*, and each of those three is observable from a
temporary directory.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy import copilot_client as copilot_client_module
from git_loopy import github_actions_host as host_module
from git_loopy import session as session_module
from git_loopy import github_actions_worker as worker
from git_loopy.git import GitError
from git_loopy.session_outcome import (
    SessionError,
    SessionErrorKind,
    SessionOutcome,
    SessionTermination,
)

RUN_ID = "01M24Q7VKPKN6M52SMSF3NR3AE"


def make_request(**overrides: object) -> dict[str, object]:
    """A well-formed contribution request, in the host's own wire shape."""
    request: dict[str, object] = {
        "issue_ref": 460,
        "prompt": "Work issue 460.",
        "base_revision": "main",
        "model": "claude-opus-4.8",
        "reasoning_effort": "high",
        "run_id": RUN_ID,
        "send_timeout_seconds": 21600.0,
    }
    request.update(overrides)
    return request


class _FakeGit:
    """A git worktree the worker can Checkpoint, with no subprocess behind it."""

    def __init__(
        self,
        *,
        dirty: bool = False,
        untracked: bool = False,
        head: str = "a" * 40,
        commit_fails: bool = False,
    ) -> None:
        self._dirty = dirty
        self._untracked = untracked
        self._head = head
        self._commit_fails = commit_fails
        self.added = False
        self.messages: list[str] = []

    def head_sha(self) -> str:
        return self._head

    def is_dirty(self) -> bool:
        return self._dirty

    def has_untracked(self) -> bool:
        return self._untracked

    def add_all(self) -> None:
        self.added = True

    def commit(self, message: str) -> str:
        if self._commit_fails:
            raise GitError("git commit", 1, "nothing to commit")
        self.messages.append(message)
        self._dirty = False
        self._untracked = False
        self._head = "b" * 40
        return self._head


def _silent_session(termination: SessionTermination = SessionTermination.COMPLETED):
    async def runner(request, log):  # noqa: ANN001 - injected seam
        del request, log
        return termination

    return runner


def _emitting_session(*payloads: dict[str, object]):
    async def runner(request, log):  # noqa: ANN001 - injected seam
        for payload in payloads:
            log.write(dict(payload, run_id=request["run_id"]))
        return SessionTermination.COMPLETED

    return runner


# --- The request the job is handed --------------------------------------


def test_the_worker_reads_its_contribution_request_from_the_job_environment() -> None:
    """The request arrives as a workflow input, not as a fetched file.

    A job that had to fetch its own instructions would need a credential to do
    it; handing the request in as an input is what lets the host transport no
    secret at all.
    """
    request = worker.parse_request({worker.REQUEST_ENV: json.dumps(make_request())})

    assert request["issue_ref"] == 460
    assert request["prompt"] == "Work issue 460."
    assert request["run_id"] == RUN_ID


@pytest.mark.parametrize(
    ("environ", "needle"),
    [
        pytest.param({}, "REQUEST is required", id="absent"),
        pytest.param({worker.REQUEST_ENV: "{"}, "must be JSON", id="unparseable"),
        pytest.param({worker.REQUEST_ENV: "[]"}, "JSON object", id="not-an-object"),
        pytest.param(
            {worker.REQUEST_ENV: json.dumps(make_request(prompt=""))},
            "prompt",
            id="empty-prompt",
        ),
        pytest.param(
            {worker.REQUEST_ENV: json.dumps({"prompt": "x", "run_id": RUN_ID, "send_timeout_seconds": 60})},
            "base_revision",
            id="no-base-revision",
        ),
        pytest.param(
            {worker.REQUEST_ENV: json.dumps(make_request(issue_ref=""))},
            "issue_ref",
            id="no-issue",
        ),
    ],
)
def test_an_unusable_request_fails_the_job_rather_than_running_a_degenerate_session(
    environ: dict[str, str], needle: str
) -> None:
    """A job that cannot name its own contribution must fail loudly.

    The alternative — running something and letting the orchestrator classify
    the wreckage — spends six hours of the cap to learn what one parse would
    have said immediately.
    """
    with pytest.raises(worker.ContributionRequestError, match=needle):
        worker.parse_request(environ)


# --- The artifact the job leaves behind ---------------------------------


def test_the_contribution_writes_its_events_to_the_artifact_the_host_reads(
    tmp_path: Path,
) -> None:
    """The Event stream is complete and late: a file, uploaded at end of job.

    The platform offers no live log streaming, so the contribution's Events
    reach the orchestrator as an artifact member — and its base name is the one
    the host's reader looks for, which is why both sides import the same
    constant instead of spelling it twice.
    """
    asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=_FakeGit(),
            session_runner=_emitting_session(
                {"type": "session.start", "iter": None},
                {"type": "session.idle", "iter": None},
            ),
        )
    )

    events_path = tmp_path / worker.EVENTS_FILENAME
    lines = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert [line["type"] for line in lines] == ["session.start", "session.idle"]


def test_the_events_artifact_member_is_the_name_the_host_reads() -> None:
    """One name, defined once. The two halves cannot drift apart.

    An earlier candidate uploaded ``git-loopy-events.jsonl`` while the reader
    looked for ``events.jsonl``; every remote contribution would have come back
    unreadable. Sharing the constant is what makes that class of defect
    unrepresentable.
    """
    assert worker.EVENTS_FILENAME == host_module.CONTRIBUTION_ARTIFACT_EVENTS


def test_each_event_is_flushed_as_it_is_written_so_a_capped_job_still_leaves_a_trail(
    tmp_path: Path,
) -> None:
    """The six-hour cap can kill the job mid-session, and often will.

    A partial-but-parseable stream is worth far more to an operator than a
    buffer that never reached disk, so the log flushes per record rather than
    on close.
    """
    log = worker._ArtifactEventLog(tmp_path / worker.EVENTS_FILENAME)
    log.write({"type": "session.start", "run_id": RUN_ID, "iter": None})

    mid_job = (tmp_path / worker.EVENTS_FILENAME).read_text()

    assert json.loads(mid_job.splitlines()[0])["type"] == "session.start"


def test_the_session_ending_is_written_beside_the_events_for_the_completion_record(
    tmp_path: Path,
) -> None:
    """The host needs the ending, and only the job can observe it.

    ``ContributionSuccess`` carries an ending, so the artifact has to carry one
    too — the orchestrator cannot reconstruct it from a job conclusion, which
    says nothing about whether the Agent got anywhere.
    """
    asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=_FakeGit(dirty=True),
            session_runner=_silent_session(),
        )
    )

    result = json.loads((tmp_path / worker.RESULT_FILENAME).read_text())
    assert result["ending"]["progressed"] is True
    assert result["ending"]["termination"] == "completed"
    assert result["ending"]["outcome"] is None
    assert result["checkpoint_failed"] is False


def test_a_session_that_moved_nothing_is_recorded_as_the_silent_ending(
    tmp_path: Path,
) -> None:
    """No commit, no progress — and the ending says so rather than guessing."""
    ending = asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=_FakeGit(),
            session_runner=_silent_session(),
        )
    )

    assert ending.progressed is False
    assert ending.outcome is SessionOutcome.NO_PROGRESS


def test_a_crashed_session_is_recorded_as_a_crash_and_still_leaves_an_artifact(
    tmp_path: Path,
) -> None:
    """A crash is a fact the orchestrator must be told, not one it infers.

    Classification belongs to the orchestrator (ADR-0050) — but it can only
    classify what it is handed, so the job reports its ending even when the
    session went badly.
    """
    ending = asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=_FakeGit(),
            session_runner=_silent_session(SessionTermination.CRASHED),
        )
    )

    assert ending.outcome is SessionOutcome.CRASH
    assert (tmp_path / worker.RESULT_FILENAME).exists()


# --- The one commit a host may author -----------------------------------


def test_a_dirty_tree_is_checkpointed_so_the_work_can_reach_the_remote() -> None:
    """ADR-0004's Checkpoint, on the far side of the machine boundary.

    Without it, an agent that left the tree uncommitted would have its whole
    contribution discarded when the runner is torn down — there is no next
    iteration on that machine to pick the residue up.
    """
    git = _FakeGit(dirty=True)

    assert worker.checkpoint_dirty_tree(git, 460) is True
    assert git.added is True
    assert git.messages[0].startswith("Checkpoint: capture work-in-progress for issue 460")


def test_the_checkpoint_carries_its_trailer_and_no_closing_keyword() -> None:
    """A Checkpoint must never close the issue it captures work for.

    It is Runner-authored and excluded from Strike progress; a closing keyword
    in it would hand the wrapper's auto-close backstop a false completion.
    """
    git = _FakeGit(untracked=True)

    worker.checkpoint_dirty_tree(git, 460)

    message = git.messages[0]
    assert "GitLoopy-Checkpoint: 460" in message
    assert "Closes #" not in message


def test_a_clean_tree_is_not_checkpointed() -> None:
    """Nothing to capture, so no commit — the host authors on need, not habit."""
    git = _FakeGit()

    assert worker.checkpoint_dirty_tree(git, 460) is True
    assert git.messages == []


def test_a_checkpoint_that_fails_leaves_the_contribution_without_progress(
    tmp_path: Path,
) -> None:
    """A failed capture is not silently retried and not silently ignored.

    The job has no durable branch to hand back, so the ending it writes must
    say the contribution did not progress — the orchestrator then disposes of
    it exactly as it would a local Lane that produced nothing.
    """
    ending = asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=_FakeGit(dirty=True, commit_fails=True),
            session_runner=_silent_session(),
        )
    )

    assert ending.progressed is False


# --- The refusals, on the far side of the boundary ----------------------


def test_the_worker_never_writes_to_the_issue_tracker() -> None:
    """The host never writes to the tracker, and neither does its worker.

    Closure is the orchestrator's signal to emit, and a remote job commenting
    on an issue would race the very iteration that supervises it.
    """
    source = Path(worker.__file__).read_text()

    assert "gh issue" not in source
    assert "issue_comment" not in source


def test_the_worker_never_mints_run_identity() -> None:
    """``run_id`` is handed in, never generated — one of the seam's refusals."""
    source = Path(worker.__file__).read_text()

    assert "ulid" not in source.lower()
    assert 'request["run_id"]' in source


def test_the_worker_never_retries_the_session_silently(tmp_path: Path) -> None:
    """One dispatch, one session. Repetition is the scheduler's decision."""
    attempts = 0

    async def runner(request, log):  # noqa: ANN001 - injected seam
        nonlocal attempts
        del request, log
        attempts += 1
        return SessionTermination.CRASHED

    asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=_FakeGit(),
            session_runner=runner,
        )
    )

    assert attempts == 1


def test_the_worker_reads_no_credential_from_its_environment() -> None:
    """No stored secret crosses the boundary, so none is read on the far side.

    The job authenticates with the built-in job token, which the platform
    places in the environment for ``gh`` and the Copilot CLI to find — the
    worker itself never handles it.
    """
    source = Path(worker.__file__).read_text()

    assert "PAT" not in source
    assert "secrets." not in source
    assert "os.environ" in source  # only for REQUEST / RUNNER_TEMP


def test_the_main_entry_point_refuses_to_run_without_the_runner_scratch_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The artifact needs somewhere to live, and Actions names that place."""
    monkeypatch.setenv(worker.REQUEST_ENV, json.dumps(make_request()))
    monkeypatch.delenv(worker.RUNNER_TEMP_ENV, raising=False)

    with pytest.raises(worker.ContributionRequestError, match=worker.RUNNER_TEMP_ENV):
        worker.main([])


def test_the_job_session_spends_the_transported_timeout_not_the_sdk_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contribution is budgeted in hours; the SDK's implicit default is 60s.

    The orchestrator's send timeout cannot be inherited across the machine
    boundary --- the job is a fresh process on someone else's runner --- so it
    travels on the request and the session must actually spend it. Left
    implicit, ``send_and_wait`` would abandon nearly every real contribution
    one minute in and report a stall the agent never caused.
    """
    recorded: dict[str, Any] = {}

    class _Session:
        def __init__(self, _client: Any, **kwargs: Any) -> None:
            recorded.update(kwargs)

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        async def send_and_wait(self, prompt: str, **kwargs: Any) -> None:
            recorded["prompt"] = prompt
            recorded["send_kwargs"] = kwargs

    class _Client:
        async def start(self) -> None: ...

        async def stop(self) -> None: ...

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "IterationSession", _Session)
    monkeypatch.setattr(
        copilot_client_module, "make_copilot_client", lambda **_kw: _Client()
    )

    observed = asyncio.run(
        worker._run_agent_session(
            {
                "prompt": "do the work",
                "run_id": RUN_ID,
                "issue_ref": 42,
                "model": None,
                "reasoning_effort": None,
                "disabled_skills": ["triage", "loop-me"],
                "send_timeout_seconds": 21600.0,
            },
            worker._ArtifactEventLog(tmp_path / worker.EVENTS_FILENAME),
        )
    )

    assert observed.termination is SessionTermination.COMPLETED
    assert recorded["send_kwargs"] == {"timeout": 21600.0}


def test_the_transported_skill_policy_hides_denied_skills_not_merely_gates_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deny_skills`` alone would leave a denied Skill visible to the agent.

    The session reads the *exposure* to decide what the agent can see and the
    config to decide what it may invoke. A job that passed no exposure would
    advertise every Skill in the pinned catalog and then refuse the ones the
    Run denied at invocation --- a different Run than the one the orchestrator
    resolved, and one that wastes the agent's turns discovering the refusal.
    """
    recorded: dict[str, Any] = {}

    class _Session:
        def __init__(self, _client: Any, **kwargs: Any) -> None:
            recorded.update(kwargs)

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        async def send_and_wait(self, _prompt: str, **_kwargs: Any) -> None: ...

    class _Client:
        async def start(self) -> None: ...

        async def stop(self) -> None: ...

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "IterationSession", _Session)
    monkeypatch.setattr(
        copilot_client_module, "make_copilot_client", lambda **_kw: _Client()
    )

    asyncio.run(
        worker._run_agent_session(
            {
                "prompt": "do the work",
                "run_id": RUN_ID,
                "issue_ref": 42,
                "model": None,
                "reasoning_effort": None,
                "disabled_skills": ["triage", "loop-me"],
                "send_timeout_seconds": 60.0,
            },
            worker._ArtifactEventLog(tmp_path / worker.EVENTS_FILENAME),
        )
    )

    exposure = recorded["skill_exposure"]
    assert exposure is not None, "an absent exposure leaves denied Skills visible"
    assert set(exposure.disabled_skills) == {"triage", "loop-me"}
    assert recorded["config"].deny_skills == frozenset({"triage", "loop-me"})


def test_an_ending_the_stream_alone_witnessed_survives_the_job(tmp_path: Path) -> None:
    """The ``await`` boundary sees only what was raised, which is half an ending.

    A content filter that refused a call, or an Agent declaring its issue
    unworkable, is reported on the Event stream and raises nothing. A job that
    read only the termination would resolve both to the silent ending --- so a
    remote Lane would charge the wrong Strike and lose the one signal that
    tells an unworkable issue from an unproductive turn.
    """

    async def _observing(
        _request: object, _log: object
    ) -> worker.SessionObservation:
        return worker.SessionObservation(
            termination=SessionTermination.COMPLETED,
            no_more_tasks=True,
        )

    git = _FakeGit(head="a" * 40)
    ending = asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=git,
            session_runner=_observing,
        )
    )

    assert ending.progressed is False
    assert ending.outcome is SessionOutcome.NO_MORE_TASKS


def test_a_checkpoint_failure_never_masks_the_condition_the_stream_named(
    tmp_path: Path,
) -> None:
    """Two witnesses, and the one that names an actionable condition wins.

    The Checkpoint's own failure is an ``UNKNOWN`` identity --- true, but it
    says nothing an operator can act on. When the harness already reported a
    quota refusal, reporting the Checkpoint instead would describe the
    contribution by its last symptom rather than its cause.
    """
    named = SessionError(
        kind=SessionErrorKind.QUOTA_EXHAUSTED,
        origin="harness",
        error_type="RateLimitError",
        message="quota exhausted",
    )

    async def _refused(_request: object, _log: object) -> worker.SessionObservation:
        return worker.SessionObservation(
            termination=SessionTermination.COMPLETED, error=named
        )

    git = _FakeGit(head="a" * 40, dirty=True, commit_fails=True)
    ending = asyncio.run(
        worker.run_contribution_job(
            make_request(),
            directory=tmp_path,
            git=git,
            session_runner=_refused,
        )
    )

    assert ending.error is not None
    assert ending.error.kind is SessionErrorKind.QUOTA_EXHAUSTED
    result = json.loads((tmp_path / worker.RESULT_FILENAME).read_text())
    assert result["checkpoint_failed"] is True, (
        "the Checkpoint's own failure is still reported, just not as the identity"
    )
