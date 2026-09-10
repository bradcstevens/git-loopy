"""The GitHub Actions **Execution host** (#460, spec #445 §D).

Every Actions interaction in this suite is supplied by an in-memory client or a
stubbed ``gh`` invocation. **No test dispatches a real workflow** — that is an
acceptance criterion of the ticket, and
:func:`test_no_test_in_this_suite_dispatches_a_real_workflow` enforces it over
the file itself rather than trusting the reader.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from unittest import mock

import pytest

from git_loopy import github_actions_host
from git_loopy import github_actions_host as host_module
from git_loopy.execution_host import (
    REASON_CHECKPOINT_FAILED,
    ContributionFailure,
    ContributionRequest,
    ContributionSuccess,
    ExecutionHost,
)
from git_loopy.github_actions_host import (
    ACTIONS_CAPACITY_ENV,
    GITHUB_ACTIONS_ISOLATION_GRADE,
    GITHUB_ACTIONS_PLACEMENT,
    LANE_CONTRIBUTION_WORKFLOW,
    MONOTONIC_OBSERVATION_FIELD,
    ActionsError,
    ActionsJob,
    ActionsRun,
    ActionsStep,
    DispatchHandle,
    GitHubActionsExecutionHost,
    SubprocessActionsClient,
    dispatch_token,
    github_actions_capacity,
    read_completion_artifact,
)
from git_loopy.session_outcome import SessionTermination

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
BASE = "a" * 40
COMPLETION_SHA = "b" * 40


def _host(client: Any) -> GitHubActionsExecutionHost:
    """A host whose polling costs nothing, over the supplied client."""
    return GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )


def make_request(**overrides: Any) -> ContributionRequest:
    defaults: dict[str, Any] = {
        "issue_ref": 42,
        "prompt": "implement the issue",
        "base_revision": BASE,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "skill_policy": ("code-review", "tdd"),
        "run_id": RUN_ID,
    }
    defaults.update(overrides)
    return ContributionRequest(**defaults)


@dataclass
class _FakeActionsClient:
    """An in-memory Actions API. Nothing here reaches the network."""

    #: Successive ``find_run`` answers; ``None`` means "not observable yet".
    sightings: list[ActionsRun | None] = field(default_factory=list)
    #: Successive ``get_run`` answers once the run is observable.
    polls: list[ActionsRun] = field(default_factory=list)
    artifacts: dict[str, bytes] = field(default_factory=dict)
    dispatch_error: str | None = None

    dispatched: list[tuple[str, str, Mapping[str, str]]] = field(default_factory=list)
    get_run_calls: list[int] = field(default_factory=list)
    artifact_calls: list[tuple[int, str]] = field(default_factory=list)
    observer: Any = None

    def dispatch(self, workflow: str, ref: str, inputs: Mapping[str, str]) -> None:
        self.dispatched.append((workflow, ref, dict(inputs)))
        if self.dispatch_error is not None:
            raise ActionsError(self.dispatch_error)

    def find_run(self, token: str) -> ActionsRun | None:
        if self.observer is not None:
            self.observer(token)
        return self.sightings.pop(0) if self.sightings else None

    def get_run(self, database_id: int) -> ActionsRun:
        self.get_run_calls.append(database_id)
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def get_artifact(self, database_id: int, name: str) -> Any:
        from git_loopy.github_actions_host import ActionsArtifact

        self.artifact_calls.append((database_id, name))
        if name not in self.artifacts:
            raise ActionsError(f"completed run has no artifact {name!r}")
        return ActionsArtifact(name=name, archive=self.artifacts[name])


def _completed_run(database_id: int = 17, conclusion: str = "success") -> ActionsRun:
    return ActionsRun(
        database_id=database_id,
        status="completed",
        conclusion=conclusion,
        jobs=(
            ActionsJob(
                name="contribution",
                status="completed",
                conclusion=conclusion,
                steps=(
                    ActionsStep(
                        name="Run contribution",
                        status="completed",
                        conclusion=conclusion,
                    ),
                ),
            ),
        ),
    )


async def _instant(_seconds: float) -> None:
    return None


def _archive(
    *,
    completion: Mapping[str, Any] | None = None,
    events: list[Mapping[str, Any]] | None = None,
    members: Mapping[str, str] | None = None,
) -> bytes:
    """Build the zipped completion artifact an Actions job uploads."""
    if members is None:
        default_completion = {
            "remote": "https://github.com/octo/example.git",
            "ref": f"refs/heads/git-loopy/{RUN_ID}/issue-42",
            "sha": COMPLETION_SHA,
            "ending": {
                "outcome": None,
                "progressed": True,
                "termination": "completed",
            },
        }
        default_events = [
            {
                "ts": "2026-09-09T20:00:00.000Z",
                "run_id": RUN_ID,
                "iter": None,
                "type": "assistant.message",
                "text": "done",
                "observed_monotonic": 123.0,
            }
        ]
        members = {
            "completion.json": json.dumps(
                default_completion if completion is None else completion
            ),
            "events.jsonl": "".join(
                json.dumps(event) + "\n"
                for event in (default_events if events is None else events)
            ),
        }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, text in members.items():
            zipped.writestr(name, text)
    return buffer.getvalue()


class _SilentActionsClient:
    """A client that fails the test if the host touches the API."""

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> str:
        raise AssertionError("declaring facts must not reach the Actions API")

    def find_run(self, dispatch_token: str) -> object | None:
        raise AssertionError("declaring facts must not reach the Actions API")

    def get_run(self, database_id: int) -> object:
        raise AssertionError("declaring facts must not reach the Actions API")

    def get_artifact(self, database_id: int, name: str) -> object:
        raise AssertionError("declaring facts must not reach the Actions API")


def test_the_actions_host_declares_placement_grade_and_capacity() -> None:
    """Placement and grade are two independent declarations (spec #445 §A/§C).

    A contribution that runs on GitHub's runner is off the operator's machine,
    so this host is the first to report the *machine boundary* grade — the
    second and last member of the closed grade set. Capacity is the account's
    concurrency ceiling, declared, never a utilization decision.
    """
    host = GitHubActionsExecutionHost(
        client=_SilentActionsClient(),
        capacity=6,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
    )

    assert host.placement == GITHUB_ACTIONS_PLACEMENT == "github-actions"
    assert host.isolation_grade == GITHUB_ACTIONS_ISOLATION_GRADE == "machine boundary"
    assert host.capacity == 6
    assert isinstance(host, ExecutionHost)


def test_the_actions_host_refuses_a_capacity_that_is_not_a_positive_integer() -> None:
    for capacity in (0, -1, True, 1.5, None):
        with pytest.raises(ValueError, match="capacity"):
            GitHubActionsExecutionHost(
                client=_SilentActionsClient(),
                capacity=capacity,  # type: ignore[arg-type]
                workflow_ref="main",
            send_timeout_seconds=21600.0,
            )


def test_capacity_comes_from_the_accounts_declared_concurrency_ceiling() -> None:
    """The ceiling is an account fact the Actions API does not expose.

    A local core count would be a lie about a remote account, so the operator
    states it and a Run that names this host without one is refused rather than
    guessing.
    """
    assert ACTIONS_CAPACITY_ENV == "GIT_LOOPY_GITHUB_ACTIONS_CAPACITY"
    assert github_actions_capacity({ACTIONS_CAPACITY_ENV: "20"}) == 20

    for raw in ("", "0", "-3", "many", "2.5"):
        with pytest.raises(ValueError, match=ACTIONS_CAPACITY_ENV):
            github_actions_capacity({ACTIONS_CAPACITY_ENV: raw})
    with pytest.raises(ValueError, match=ACTIONS_CAPACITY_ENV):
        github_actions_capacity({})


def test_one_reservation_becomes_exactly_one_dispatch_for_that_issue() -> None:
    """A matrix could not do this: its legs are fixed at run start (ADR-0020).

    Rolling dispatch survives only if the moment a Lane slot is reserved is the
    moment a run starts, so the seam's one call produces one
    ``workflow_dispatch`` carrying that contribution's own request.
    """
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    assert len(client.dispatched) == 1
    workflow, ref, inputs = client.dispatched[0]
    assert workflow == LANE_CONTRIBUTION_WORKFLOW
    assert ref == "main"
    payload = json.loads(inputs["request"])
    assert payload["issue_ref"] == 42
    assert payload["run_id"] == RUN_ID
    assert payload["base_revision"] == BASE
    assert payload["prompt"] == "implement the issue"
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["reasoning_effort"] == "high"
    assert inputs["dispatch_token"] == dispatch_token(make_request())


def test_the_orchestrator_holds_the_handle_from_dispatch() -> None:
    """``workflow_dispatch`` answers 204, so the handle cannot come from the API.

    The orchestrator mints the correlation itself and holds it from the instant
    the dispatch is accepted — before the run is observable and therefore
    before it has a ``database_id``. A handle that only existed once the API
    named the run would leave a window in which a dispatched contribution is
    unattributable.
    """
    held: list[tuple[DispatchHandle, ...]] = []
    client = _FakeActionsClient(
        sightings=[None, _completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    client.observer = lambda _token: held.append(host.open_dispatch_handles)
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    first_sighting = held[0]
    assert len(first_sighting) == 1
    handle = first_sighting[0]
    assert handle.token == dispatch_token(make_request())
    assert handle.workflow == LANE_CONTRIBUTION_WORKFLOW
    assert handle.ref == "main"
    assert handle.database_id is None, (
        "the handle must exist before the run is observable"
    )
    assert host.open_dispatch_handles == (), "a settled contribution releases its handle"


def test_a_refused_dispatch_is_a_never_started_value_and_is_not_retried() -> None:
    """One call in, one outcome out — the seam's no-silent-retry refusal."""
    client = _FakeActionsClient(dispatch_error="workflow_dispatch was refused")
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "never_started"
    assert outcome.reason == "workflow_dispatch_refused"
    assert "refused" in outcome.detail
    assert outcome.ending is None
    assert len(client.dispatched) == 1
    assert host.open_dispatch_handles == ()


def test_job_and_step_status_give_coarse_liveness_while_the_contribution_runs() -> None:
    """Actions offers no supported live log stream, so liveness is polled status.

    The platform's own CLI refuses to tail a running job, so a design that
    assumed a live stream would be unsupportable. What the orchestrator gets
    instead is the job's and its steps' coarse status, republished on the
    handle it has held since dispatch.
    """
    running = ActionsRun(
        database_id=17,
        status="in_progress",
        jobs=(
            ActionsJob(
                name="contribution",
                status="in_progress",
                steps=(
                    ActionsStep(
                        name="Set up a git-loopy Lane contribution",
                        status="completed",
                        conclusion="success",
                    ),
                    ActionsStep(name="Run contribution", status="in_progress"),
                ),
            ),
        ),
    )
    client = _FakeActionsClient(
        sightings=[running],
        polls=[running, _completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )
    seen: list[DispatchHandle] = []

    async def observe(seconds: float) -> None:
        seen.extend(host.open_dispatch_handles)

    host._sleep = observe  # type: ignore[assignment]
    asyncio.run(host.run_contribution(make_request()))

    assert seen, "the host must poll while the contribution is still running"
    live = seen[0]
    assert live.database_id == 17, "the handle is bound once the run is observable"
    assert live.liveness is not None
    assert live.liveness.status == "in_progress"
    job = live.liveness.jobs[0]
    assert job.name == "contribution"
    assert job.status == "in_progress"
    assert [step.status for step in job.steps] == ["completed", "in_progress"]


def test_a_completed_run_returns_the_remote_completion_triple() -> None:
    """A remote contribution is named by remote, ref and completion SHA (§E).

    It carries no local branch: Integration materializes the triple by fetching
    the SHA, so naming a local branch here would claim something the
    orchestrator's clone does not yet have.
    """
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionSuccess)
    assert outcome.branch is None
    assert outcome.remote == "https://github.com/octo/example.git"
    assert outcome.ref == f"refs/heads/git-loopy/{RUN_ID}/issue-42"
    assert outcome.sha == COMPLETION_SHA
    assert outcome.placement == GITHUB_ACTIONS_PLACEMENT
    assert outcome.isolation_grade == GITHUB_ACTIONS_ISOLATION_GRADE
    assert outcome.ending is not None
    assert outcome.ending.termination is SessionTermination.COMPLETED
    assert outcome.ending.progressed is True
    assert client.artifact_calls == [(17, f"git-loopy-{RUN_ID}-issue-42")]


def test_ingested_remote_events_are_stripped_of_the_monotonic_observation_field() -> None:
    """Stripped, never re-stamped.

    A remote machine's monotonic clock shares no origin with the
    orchestrator's, so the remote reading is meaningless here — and
    re-stamping every backdated Event with the orchestrator's *ingest* reading
    would collapse a whole contribution onto the instant its artifact was
    read. The backdated wall clock is what survives, untouched.
    """
    events = [
        {
            "ts": "2026-09-09T20:00:00.000Z",
            "run_id": RUN_ID,
            "iter": None,
            "type": "assistant.message",
            "text": "first",
            "observed_monotonic": 12.5,
        },
        {
            "ts": "2026-09-09T22:30:00.000Z",
            "run_id": RUN_ID,
            "iter": None,
            "type": "assistant.message",
            "text": "last",
        },
    ]
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive(events=events)},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionSuccess)
    assert [dict(event) for event in outcome.events] == [
        {
            "ts": "2026-09-09T20:00:00.000Z",
            "run_id": RUN_ID,
            "iter": None,
            "type": "assistant.message",
            "text": "first",
        },
        {
            "ts": "2026-09-09T22:30:00.000Z",
            "run_id": RUN_ID,
            "iter": None,
            "type": "assistant.message",
            "text": "last",
        },
    ]
    assert all(MONOTONIC_OBSERVATION_FIELD not in event for event in outcome.events)


def test_a_job_that_fails_before_the_contribution_step_never_started() -> None:
    """Coarse step status is what tells the two blameless classes apart.

    ADR-0050 keeps ``never_started`` and ``stall`` distinct so an operator can
    read *the host never picked it up* apart from *the host went dark*. On a
    remote host the only evidence for that distinction is the step roster the
    liveness poll already collects.
    """
    failed_setup = ActionsRun(
        database_id=17,
        status="completed",
        conclusion="failure",
        jobs=(
            ActionsJob(
                name="contribution",
                status="completed",
                conclusion="failure",
                steps=(
                    ActionsStep(
                        name="Set up a git-loopy Lane contribution",
                        status="completed",
                        conclusion="failure",
                    ),
                    ActionsStep(
                        name="Run contribution", status="completed", conclusion="skipped"
                    ),
                ),
            ),
        ),
    )
    client = _FakeActionsClient(sightings=[failed_setup], polls=[failed_setup])
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "never_started"
    assert outcome.reason == "workflow_setup_failed"
    assert outcome.ending is None
    assert "Set up a git-loopy Lane contribution=completed/failure" in outcome.detail


def test_a_job_that_fails_after_the_contribution_step_ran_is_a_stall() -> None:
    """The session ran and proved no ending, so no ending is manufactured."""
    failed_contribution = ActionsRun(
        database_id=17,
        status="completed",
        conclusion="failure",
        jobs=(
            ActionsJob(
                name="contribution",
                status="completed",
                conclusion="failure",
                steps=(
                    ActionsStep(
                        name="Set up a git-loopy Lane contribution",
                        status="completed",
                        conclusion="success",
                    ),
                    ActionsStep(
                        name="Run contribution",
                        status="completed",
                        conclusion="failure",
                    ),
                ),
            ),
        ),
    )
    client = _FakeActionsClient(
        sightings=[failed_contribution], polls=[failed_contribution]
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "stall"
    assert outcome.reason == "workflow_run_failed"
    assert outcome.ending is None


def test_the_six_hour_job_cap_bounds_one_contribution_not_a_run() -> None:
    """Actions' cap is hard and plan-independent, so it is spent per contribution."""
    never_finishes = ActionsRun(database_id=17, status="in_progress")
    client = _FakeActionsClient(
        sightings=[never_finishes], polls=[never_finishes, never_finishes]
    )
    ticks = iter([0.0, 100.0, 6 * 60 * 60 + 1.0, 6 * 60 * 60 + 2.0])
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
            workflow_ref="main",
            send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
        clock=lambda: next(ticks),
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "stall"
    assert outcome.reason == "workflow_run_exceeded_job_cap"
    assert outcome.ending is None


def test_a_run_that_never_becomes_observable_is_a_stall() -> None:
    """A dispatch the API never surfaces proves nothing about the contribution."""
    client = _FakeActionsClient(sightings=[])
    ticks = iter([0.0, 1.0, 6 * 60 * 60 + 1.0, 6 * 60 * 60 + 2.0])
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
            workflow_ref="main",
            send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
        clock=lambda: next(ticks),
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "stall"
    assert outcome.reason == "workflow_run_not_observed"
    assert client.get_run_calls == []


def test_an_actions_api_that_stops_answering_is_a_stall() -> None:
    class _DarkClient(_FakeActionsClient):
        def find_run(self, token: str) -> ActionsRun | None:
            raise ActionsError("the Actions API is unavailable")

    client = _DarkClient()
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "stall"
    assert outcome.reason == "workflow_status_unavailable"
    assert "unavailable" in outcome.detail


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        pytest.param({}, "completion.json", id="no-members"),
        pytest.param(
            {"completion.json": "{}"}, "events.jsonl", id="events-missing"
        ),
        pytest.param(
            {"completion.json": "not json", "events.jsonl": ""},
            "not JSON",
            id="completion-unparseable",
        ),
        pytest.param(
            {
                "completion.json": json.dumps({"remote": "r", "ref": "refs/x"}),
                "events.jsonl": "",
            },
            "remote, ref and sha",
            id="incomplete-triple",
        ),
        pytest.param(
            {
                "completion.json": json.dumps(
                    {
                        "remote": "r",
                        "ref": "refs/x",
                        "sha": COMPLETION_SHA,
                        "ending": {"outcome": None, "progressed": True},
                    }
                ),
                "events.jsonl": "",
            },
            "ending",
            id="ending-incomplete",
        ),
    ],
)
def test_an_unusable_completion_artifact_is_a_stall(
    members: dict[str, str], expected: str
) -> None:
    """The artifact is the only remote-to-local channel, so a bad one proves nothing."""
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive(members=members)},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "stall"
    assert outcome.reason == "completion_artifact_unusable"
    assert expected in outcome.detail


def test_a_missing_completion_artifact_is_a_stall() -> None:
    client = _FakeActionsClient(sightings=[_completed_run()], polls=[_completed_run()])
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    outcome = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.classification == "stall"
    assert outcome.reason == "completion_artifact_unusable"


def test_the_host_never_writes_to_the_issue_tracker_and_never_mints_identity() -> None:
    """Two of the seam's six refusals, read off the module rather than asserted.

    A host that imported the tracker client could write to it by accident; one
    that could reach an id generator could mint identity. Neither is
    importable here, and the ``run_id`` the dispatch carries is the one the
    orchestrator handed over.
    """
    source = Path(github_actions_host.__file__).read_text(encoding="utf-8")
    assert "from git_loopy import gh" not in source
    assert "git_loopy.gh" not in source
    assert "ulid" not in source.lower()
    assert "uuid" not in source.lower()

    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request(run_id="01HXR0000000000000000000ZZ")))

    _workflow, _ref, inputs = client.dispatched[0]
    assert json.loads(inputs["request"])["run_id"] == "01HXR0000000000000000000ZZ"
    assert inputs["dispatch_token"].split()[1] == "01HXR0000000000000000000ZZ"


def test_no_test_in_this_suite_dispatches_a_real_workflow() -> None:
    """An acceptance criterion, enforced over the suite rather than trusted.

    Every Actions interaction above is an in-memory client or a stubbed
    subprocess. A test that reached the real API would be slow, flaky and
    would burn the account's concurrency — and would only be noticed once it
    had already dispatched.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    # Assembled from parts so the guard does not trip over its own needles.
    forbidden = (
        "subprocess" + ".run(",
        "workflows/" + LANE_CONTRIBUTION_WORKFLOW + "/dispatches",
        "gh " + "workflow run",
        "SubprocessActionsClient" + ".discover()",
    )
    for needle in forbidden:
        assert needle not in source, f"{needle!r} would reach the real Actions API"


# --------------------------------------------------------------------------
# The production client. Its subprocess boundary is injected, so these tests
# assert the exact ``gh api`` argv without ever running ``gh``.
# --------------------------------------------------------------------------


@dataclass
class _StubGh:
    """Answers ``gh api`` calls from a canned table keyed on the endpoint."""

    answers: dict[str, bytes] = field(default_factory=dict)
    exit_code: int = 0
    stderr: bytes = b""
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> bytes:
        self.calls.append(list(argv))
        if self.exit_code != 0:
            raise ActionsError(
                f"gh Actions call failed ({self.exit_code}): "
                f"{self.stderr.decode()} [{' '.join(argv)}]"
            )
        endpoint = next((arg for arg in argv if arg.startswith("repos/")), "")
        return self.answers.get(endpoint, b"{}")


def test_the_production_client_dispatches_through_the_authenticated_cli() -> None:
    gh = _StubGh()
    client = SubprocessActionsClient("octo/example", run=gh)

    client.dispatch(
        LANE_CONTRIBUTION_WORKFLOW, BASE, {"request": "{}", "dispatch_token": "t"}
    )

    assert gh.calls == [
        [
            "api",
            "--method",
            "POST",
            f"repos/octo/example/actions/workflows/{LANE_CONTRIBUTION_WORKFLOW}"
            "/dispatches",
            "-f",
            f"ref={BASE}",
            "-f",
            "inputs[dispatch_token]=t",
            "-f",
            "inputs[request]={}",
        ]
    ]


def test_the_production_client_finds_a_run_by_its_dispatch_token() -> None:
    """The token is echoed into the run's display title, which is how a 204 is correlated."""
    gh = _StubGh(
        answers={
            "repos/octo/example/actions/runs": json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 5,
                            "display_title": "git-loopy other issue 1",
                            "status": "completed",
                            "conclusion": "success",
                        },
                        {
                            "id": 17,
                            "display_title": f"git-loopy {RUN_ID} issue 42",
                            "status": "in_progress",
                            "conclusion": None,
                        },
                    ]
                }
            ).encode()
        }
    )
    client = SubprocessActionsClient("octo/example", run=gh)

    found = client.find_run(f"git-loopy {RUN_ID} issue 42")

    assert found is not None
    assert found.database_id == 17
    assert found.status == "in_progress"
    assert client.find_run("git-loopy nothing issue 0") is None


def test_the_production_client_reads_job_and_step_status() -> None:
    gh = _StubGh(
        answers={
            "repos/octo/example/actions/runs/17": json.dumps(
                {"id": 17, "status": "in_progress", "conclusion": None}
            ).encode(),
            "repos/octo/example/actions/runs/17/jobs": json.dumps(
                {
                    "jobs": [
                        {
                            "name": "contribution",
                            "status": "in_progress",
                            "conclusion": None,
                            "steps": [
                                {
                                    "name": "Run contribution",
                                    "status": "in_progress",
                                    "conclusion": None,
                                }
                            ],
                        }
                    ]
                }
            ).encode(),
        }
    )
    client = SubprocessActionsClient("octo/example", run=gh)

    run = client.get_run(17)

    assert run.database_id == 17
    assert run.status == "in_progress"
    assert run.jobs[0].name == "contribution"
    assert run.jobs[0].steps[0].name == "Run contribution"


def test_the_production_client_downloads_the_named_completion_artifact() -> None:
    archive = _archive()
    gh = _StubGh(
        answers={
            "repos/octo/example/actions/runs/17/artifacts": json.dumps(
                {
                    "artifacts": [
                        {"id": 3, "name": "something-else"},
                        {"id": 9, "name": f"git-loopy-{RUN_ID}-issue-42"},
                    ]
                }
            ).encode(),
            "repos/octo/example/actions/artifacts/9/zip": archive,
        }
    )
    client = SubprocessActionsClient("octo/example", run=gh)

    artifact = client.get_artifact(17, f"git-loopy-{RUN_ID}-issue-42")

    assert artifact.archive == archive
    assert read_completion_artifact(artifact).sha == COMPLETION_SHA
    with pytest.raises(ActionsError, match="no artifact"):
        client.get_artifact(17, "absent")


def test_a_failing_cli_call_surfaces_as_an_actions_error() -> None:
    gh = _StubGh(exit_code=1, stderr=b"gh: not authenticated")
    client = SubprocessActionsClient("octo/example", run=gh)

    with pytest.raises(ActionsError, match="not authenticated"):
        client.find_run("anything")


def test_a_malformed_actions_response_surfaces_as_an_actions_error() -> None:
    gh = _StubGh(answers={"repos/octo/example/actions/runs": b"[]"})
    client = SubprocessActionsClient("octo/example", run=gh)

    with pytest.raises(ActionsError, match="workflow_runs"):
        client.find_run("anything")


def test_the_dispatch_carries_no_credential_across_the_machine_boundary() -> None:
    """A host receives no credential — so none may travel in its dispatch inputs.

    The job authenticates *itself* with the built-in job token, so the only
    things that cross the boundary are the outcome contract's own input fields
    and the correlation token the orchestrator derived from identity it was
    already handed.
    """
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    _workflow, _ref, inputs = client.dispatched[0]
    assert set(inputs) == {"request", "dispatch_token"}
    assert set(json.loads(inputs["request"])) == {
        "base_revision",
        "disabled_skills",
        "issue_ref",
        "model",
        "prompt",
        "reasoning_effort",
        "run_id",
        "send_timeout_seconds",
    }


def test_the_dispatch_names_a_branch_because_the_api_refuses_a_commit_sha() -> None:
    """The workflow ref and the contribution's base revision are not the same thing.

    ``base_revision`` is a commit SHA --- the orchestrator reads it from
    ``head_sha()`` --- and the workflow-dispatch API resolves ``ref`` to a
    branch or tag and rejects a SHA outright. Conflating the two refuses every
    real dispatch before any contribution starts, and no test that mocks the
    API can see it unless it pins the two apart, which is what this does.

    They answer different questions: the ref chooses *which definition of the
    workflow runs*, and ``base_revision`` chooses *what the job checks out* ---
    which is why it travels inside the request instead.
    """
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="release/1.x",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    _workflow, ref, inputs = client.dispatched[0]
    assert ref == "release/1.x"
    assert ref != BASE, "a commit SHA is not a dispatchable ref"
    assert json.loads(inputs["request"])["base_revision"] == BASE


def test_a_host_without_a_dispatchable_workflow_ref_is_refused_at_construction() -> None:
    """Refused where the Run can still be stopped, not per contribution.

    An empty ref would dispatch nothing, N times, and each failure would be
    reported as a separate never-started contribution instead of the one
    misconfiguration it actually is.
    """
    with pytest.raises(ValueError, match="branch or tag"):
        GitHubActionsExecutionHost(
            client=_SilentActionsClient(),
            capacity=4,
            workflow_ref="",
            send_timeout_seconds=21600.0,
        )


def test_a_shorter_issues_token_never_claims_a_longer_issues_run() -> None:
    """Correlation is exact equality, because tokens nest by construction.

    ``git-loopy <run> issue 46`` is a prefix of ``git-loopy <run> issue 460``,
    and within one Run both are live at once. A containment test would hand
    issue 46's contribution issue 460's run: 46 then supervises work it did not
    request, fails to find its own artifact, and 460's real run is orphaned to
    burn six hours unattributed.
    """
    gh = _StubGh(
        answers={
            "repos/o/r/actions/runs": json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 460460,
                            "status": "completed",
                            "conclusion": "success",
                            "display_title": f"git-loopy {RUN_ID} issue 460",
                        }
                    ]
                }
            ).encode()
        }
    )
    client = SubprocessActionsClient("o/r", run=gh)

    assert client.find_run(f"git-loopy {RUN_ID} issue 46") is None
    assert client.find_run(f"git-loopy {RUN_ID} issue 460") is not None


def test_the_production_client_bounds_every_call_it_makes() -> None:
    """An unbounded `gh` call holds a Lane's supervision open forever.

    The call is made off the event loop, so it starves no other Lane --- but a
    poll that never returns cannot report the very unresponsiveness it exists
    to detect, and the six-hour cap it is measured against would never fire.
    """
    recorded: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003 - subprocess stand-in
        recorded.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

    with mock.patch.object(host_module.subprocess, "run", fake_run):
        host_module._gh(["api", "repos/o/r"])

    assert recorded["timeout"] == host_module.GH_CALL_TIMEOUT_SECONDS


def test_a_gh_call_that_never_answers_becomes_a_stall_rather_than_a_hang() -> None:
    """The timeout is reported as an Actions error, which supervision classifies."""

    def hang(argv, **kwargs):  # noqa: ANN001, ANN003 - subprocess stand-in
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    with mock.patch.object(host_module.subprocess, "run", hang):
        with pytest.raises(ActionsError, match="did not answer"):
            host_module._gh(["api", "repos/o/r"])


def test_actions_calls_do_not_block_the_loop_that_supervises_every_other_lane() -> None:
    """N Lanes share one event loop; a blocking client would freeze all of them.

    The client is synchronous on purpose --- a thin `gh` shell whose argv is
    easy to assert --- so the host has to be the thing that keeps it off the
    loop. Proven by observing the thread each call lands on rather than by
    timing anything.
    """
    supervising = threading.get_ident()
    seen: list[int] = []

    class _ThreadWitness(_FakeActionsClient):
        def dispatch(self, workflow, ref, inputs):  # noqa: ANN001 - client seam
            seen.append(threading.get_ident())
            return super().dispatch(workflow, ref, inputs)

        def find_run(self, token):  # noqa: ANN001 - client seam
            seen.append(threading.get_ident())
            return super().find_run(token)

    client = _ThreadWitness(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    assert seen, "the client was never called"
    assert supervising not in seen, "a blocking API call ran on the supervising loop"


def test_the_runs_disabled_skills_travel_to_the_remote_contribution() -> None:
    """A Skill switched off for this Run stays off on the far side of the boundary.

    The *enabled* half needs no transport: ADR-0025 pins the Skill root to the
    catalog `git-loopy init` installs, and the job installs the same revision.
    The disabled half cannot be re-derived remotely, though --- so without this
    a Run's closed world would quietly have a hole in it shaped exactly like
    the contributions that ran off the operator's machine.
    """
    client = _FakeActionsClient(
        sightings=[_completed_run()],
        polls=[_completed_run()],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=21600.0,
        sleep=_instant,
        poll_interval_seconds=0,
    )
    policy = SimpleNamespace(disabled_skills=("grill-me", "loop-me"))

    asyncio.run(host.run_contribution(make_request(skill_policy=policy)))

    payload = json.loads(client.dispatched[0][2]["request"])
    assert payload["disabled_skills"] == ["grill-me", "loop-me"]


def test_a_failed_checkpoint_is_a_breach_rather_than_a_quiet_success() -> None:
    """A contribution whose dirty tree never reached a commit did not succeed.

    The job checkpoints whatever the agent left uncommitted before it pushes
    (ADR-0004), and that step can fail --- an unmergeable index, a hook, a
    `git` that refuses the tree. When it does, the SHA the job reports is the
    base revision with the agent's work missing from it, which is
    indistinguishable at the seam from a session that legitimately changed
    nothing. Only the job knows which happened, so it says so on the wire and
    the host classifies it as the breach it is.
    """
    completion = {
        "remote": "https://github.com/octo/example.git",
        "ref": f"refs/heads/git-loopy/{RUN_ID}/issue-42",
        "sha": COMPLETION_SHA,
        "ending": {"outcome": None, "progressed": False, "termination": "completed"},
        "checkpoint_failed": True,
    }
    run = _completed_run()
    client = _FakeActionsClient(
        sightings=[run],
        polls=[run],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive(completion=completion)},
    )
    host = _host(client)

    result = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(result, ContributionFailure)
    assert result.reason == REASON_CHECKPOINT_FAILED
    assert result.classification == "breach"
    assert result.ending is not None
    assert result.events, "the Events observed before the failed checkpoint are kept"


def test_a_job_that_failed_after_its_session_still_yields_that_sessions_account(
) -> None:
    """A push that failed is not a stall, and the session before it is not lost.

    The job uploads its artifact on ``always()``, so a run that failed *after*
    the session ended still carries the ending and every Event. Reporting that
    as a stall would blame the agent for a machine's failure and discard hours
    of Events with it; the host reads what survived and names the real fault.
    """
    members = {
        "result.json": json.dumps(
            {
                "ending": {
                    "outcome": None,
                    "progressed": True,
                    "termination": "completed",
                },
                "checkpoint_failed": False,
            }
        ),
        "events.jsonl": json.dumps(
            {
                "ts": "2026-09-09T20:00:00.000Z",
                "run_id": RUN_ID,
                "iter": None,
                "type": "assistant.message",
                "text": "the work happened",
                "observed_monotonic": 12.0,
            }
        )
        + "\n",
    }
    run = _completed_run(conclusion="failure")
    client = _FakeActionsClient(
        sightings=[run],
        polls=[run],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive(members=members)},
    )
    host = _host(client)

    result = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(result, ContributionFailure)
    assert result.reason == "workflow_publication_failed"
    assert result.classification == "breach"
    assert result.ending is not None
    assert result.ending.termination is SessionTermination.COMPLETED
    assert [event["text"] for event in result.events] == ["the work happened"]
    assert "observed_monotonic" not in result.events[0]


def test_a_failed_job_that_never_accounted_for_itself_is_still_a_stall() -> None:
    """Salvage is not a licence to invent an ending the job never wrote.

    A job that died before its session produced a result has nothing to
    salvage, and manufacturing one would report a session that never ran as
    having ended. That case is exactly what the stall classification is for.
    """
    run = _completed_run(conclusion="failure")
    client = _FakeActionsClient(
        sightings=[run],
        polls=[run],
        artifacts={
            f"git-loopy-{RUN_ID}-issue-42": _archive(
                members={"nothing-useful.txt": "the setup step died"}
            )
        },
    )
    host = _host(client)

    result = asyncio.run(host.run_contribution(make_request()))

    assert isinstance(result, ContributionFailure)
    assert result.classification == "stall"
    assert result.ending is None


def test_the_request_carries_the_runs_send_timeout_rather_than_an_sdk_default(
) -> None:
    """The job cannot inherit the orchestrator's timeout, so it is transported.

    The agent SDK's ``send_and_wait`` defaults to a minute when no timeout is
    passed. A contribution is budgeted in hours, so a worker that let the
    default stand would kill nearly every remote contribution a minute in and
    report it as a stall the agent never caused.
    """
    run = _completed_run()
    client = _FakeActionsClient(
        sightings=[run],
        polls=[run],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=1234.5,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    _workflow, _ref, inputs = client.dispatched[0]
    assert json.loads(inputs["request"])["send_timeout_seconds"] == 1234.5


def test_a_host_without_a_send_timeout_is_refused_at_construction() -> None:
    """A non-positive timeout would expire before the session's first turn."""
    with pytest.raises(ValueError, match="send timeout"):
        GitHubActionsExecutionHost(
            client=_SilentActionsClient(),
            capacity=4,
            workflow_ref="main",
            send_timeout_seconds=0,
        )


def test_a_repository_without_the_contribution_workflow_is_refused_at_preflight(
) -> None:
    """The host dispatches into the *target* repository, which must carry it.

    The workflow and its composite setup step are files in a repository, so a
    Run pointed at a project that never installed them has nothing to dispatch
    into. Discovered at the first Lane reservation instead, the omission
    arrives as a refused dispatch with Lanes already open --- an environment
    failure wearing a contribution's clothes. Asked before any Lane exists, it
    is something the operator can actually fix.
    """

    class _Bare:
        def workflow_installed(self, workflow: str) -> bool:
            assert workflow == LANE_CONTRIBUTION_WORKFLOW
            return False

    with pytest.raises(ActionsError, match="no lane-contribution.yml workflow"):
        host_module.assert_workflow_installed(_Bare())


def test_an_installed_workflow_passes_preflight_silently() -> None:
    """The check is a refusal, not a ceremony: an installed workflow says nothing."""

    class _Installed:
        def workflow_installed(self, _workflow: str) -> bool:
            return True

    host_module.assert_workflow_installed(_Installed())


def test_a_missing_workflow_is_an_answer_but_a_broken_api_is_still_an_error() -> None:
    """404 means "absent"; every other failure is the API failing, not an answer.

    Swallowing all errors here would report a repository whose Actions API is
    unreachable, or whose credential lacks the scope, as one that simply has no
    workflow --- sending the operator to install a file that is already there.
    """
    calls: list[list[str]] = []

    def _absent(argv: list[str]) -> bytes:
        calls.append(argv)
        raise ActionsError("gh Actions call failed (1): gh: Not Found (HTTP 404)")

    def _broken(argv: list[str]) -> bytes:
        raise ActionsError("gh Actions call failed (1): HTTP 403: Forbidden")

    assert (
        SubprocessActionsClient("octo/example", run=_absent).workflow_installed(
            LANE_CONTRIBUTION_WORKFLOW
        )
        is False
    )
    assert calls[0][:2] == [
        "api",
        f"repos/octo/example/actions/workflows/{LANE_CONTRIBUTION_WORKFLOW}",
    ]

    with pytest.raises(ActionsError, match="403"):
        SubprocessActionsClient("octo/example", run=_broken).workflow_installed(
            LANE_CONTRIBUTION_WORKFLOW
        )


def test_an_event_quoting_a_unicode_line_separator_is_still_one_record() -> None:
    """JSONL is delimited by newlines, and only by newlines.

    The Event writer serializes with ``ensure_ascii=False``, so a U+2028 an
    agent quoted --- in a diff, a scraped page, a test fixture --- lands raw
    inside the JSON string. Python calls that a line boundary; JSONL does not.
    A parser that agreed with Python would tear the record in two, fail to
    parse either half, and reject the whole artifact of a contribution whose
    only offence was quoting a character.
    """
    line = json.dumps(
        {
            "ts": "2026-09-09T20:00:00.000Z",
            "run_id": RUN_ID,
            "iter": None,
            "type": "assistant.message",
            "text": "before\u2028after\u2029and\u0085more",
        },
        ensure_ascii=False,
    )
    assert len(line.splitlines()) > 1, "non-vacuity: Python would split this"

    events = host_module._parse_events((line + "\n").encode("utf-8"))

    assert len(events) == 1
    assert events[0]["text"] == "before\u2028after\u2029and\u0085more"


def test_the_session_budget_leaves_the_job_room_to_record_its_ending() -> None:
    """The six-hour cap is a wall the platform enforces by killing the job.

    A killed job runs no ``always()`` step, so a session handed the whole cap
    would spend it and then have nothing left to Checkpoint, push, record or
    upload with --- losing the ending and every Event of exactly the
    contributions that ran long enough to be worth keeping.
    """
    budget = host_module.session_budget(host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS)

    assert budget < host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS
    assert (
        host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS - budget
        == host_module.CONTRIBUTION_FINALIZATION_RESERVE_SECONDS
    )
    # A Run whose own timeout is shorter keeps it: the reserve is a ceiling on
    # what the job can afford, not a floor on what the operator asked for.
    assert host_module.session_budget(600.0) == 600.0


def test_a_run_timeout_longer_than_the_job_cap_is_clamped_on_the_wire() -> None:
    """The operator's Run-wide timeout knows nothing about this host's job cap."""
    run = _completed_run()
    client = _FakeActionsClient(
        sightings=[run],
        polls=[run],
        artifacts={f"git-loopy-{RUN_ID}-issue-42": _archive()},
    )
    host = GitHubActionsExecutionHost(
        client=client,
        capacity=4,
        workflow_ref="main",
        send_timeout_seconds=24 * 60 * 60,
        sleep=_instant,
        poll_interval_seconds=0,
    )

    asyncio.run(host.run_contribution(make_request()))

    _workflow, _ref, inputs = client.dispatched[0]
    sent = json.loads(inputs["request"])["send_timeout_seconds"]
    assert sent == host_module.CONTRIBUTION_SESSION_BUDGET_SECONDS
