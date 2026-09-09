"""GitHub Actions implementation of the Execution host seam (#460).

The host starts exactly one named workflow for a Lane contribution, observes
the workflow's coarse job status until it completes, then reads the durable
completion artifact.  The artifact is the only remote-to-local event channel:
Actions does not expose a supported live log stream.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import time
import zipfile
from dataclasses import dataclass
from os import environ
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from git_loopy.execution_host import (
    ContributionFailure,
    ContributionOutcome,
    ContributionRequest,
    ContributionSuccess,
    IsolationGrade,
    Placement,
)
from git_loopy.session_outcome import (
    SessionOutcome,
    SessionOutcomeRecord,
    SessionTermination,
)

__all__ = [
    "ActionsStep",
    "ActionsJob",
    "ActionsRun",
    "ActionsArtifact",
    "ActionsClient",
    "SubprocessActionsClient",
    "GitHubActionsExecutionHost",
    "github_actions_capacity",
]

_WORKFLOW = "lane-contribution.yml"
_MAX_CONTRIBUTION_SECONDS = 6 * 60 * 60
_CAPACITY_ENV = "GIT_LOOPY_GITHUB_ACTIONS_CAPACITY"


@dataclass(frozen=True)
class ActionsStep:
    """One Actions job step's observable status."""

    name: str
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class ActionsJob:
    """One Actions job's coarse liveness record."""

    name: str
    status: str
    conclusion: str | None
    steps: tuple[ActionsStep, ...]


@dataclass(frozen=True)
class ActionsRun:
    """A dispatched workflow run and its observable job state."""

    database_id: int
    display_title: str
    status: str
    conclusion: str | None
    jobs: tuple[ActionsJob, ...] = ()


@dataclass(frozen=True)
class ActionsArtifact:
    """The completed contribution's zipped artifact."""

    name: str
    archive: bytes


class ActionsClient(Protocol):
    """The small GitHub Actions API surface this host needs."""

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        """Dispatch one workflow run."""
        ...

    def find_run(self, display_title: str) -> ActionsRun | None:
        """Find the just-dispatched run by its unique display title."""
        ...

    def get_run(self, database_id: int) -> ActionsRun:
        """Read live workflow, job, and step status."""
        ...

    def get_artifact(self, database_id: int, name: str) -> ActionsArtifact:
        """Read one completed artifact without extracting it to disk."""
        ...


class ActionsError(RuntimeError):
    """A GitHub Actions CLI call failed or returned an unusable response."""


class SubprocessActionsClient:
    """Actions API client implemented with the authenticated ``gh`` CLI."""

    def __init__(self, repository: str) -> None:
        self._repository = repository

    @classmethod
    def discover(cls) -> "SubprocessActionsClient":
        """Bind the client to the repository resolved by the authenticated CLI."""
        command = ["gh", "repo", "view", "--json", "nameWithOwner"]
        try:
            completed = subprocess.run(command, capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise ActionsError("gh not found on PATH") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ActionsError(
                f"gh could not resolve the Actions repository ({completed.returncode}): "
                f"{detail}"
            )
        try:
            name_with_owner = json.loads(completed.stdout)["nameWithOwner"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ActionsError("gh repository response has no nameWithOwner") from exc
        if not isinstance(name_with_owner, str) or not name_with_owner:
            raise ActionsError("gh repository response has invalid nameWithOwner")
        return cls(name_with_owner)

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        fields = [
            item
            for key, value in inputs.items()
            for item in ("-f", f"inputs[{key}]={value}")
        ]
        self._run_bytes(
            [
                "api",
                "--method",
                "POST",
                f"repos/{self._repository}/actions/workflows/{workflow}/dispatches",
                "-f",
                f"ref={ref}",
                *fields,
            ]
        )

    def find_run(self, display_title: str) -> ActionsRun | None:
        data = self._run_json(
            [
                "api",
                "--method",
                "GET",
                f"repos/{self._repository}/actions/runs",
                "-f",
                "event=workflow_dispatch",
                "-f",
                "per_page=100",
            ]
        )
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise ActionsError("Actions runs response has no workflow_runs list")
        for raw in runs:
            run = _parse_run(raw)
            if run.display_title == display_title:
                return run
        return None

    def get_run(self, database_id: int) -> ActionsRun:
        raw = self._run_json(
            ["api", f"repos/{self._repository}/actions/runs/{database_id}"]
        )
        run = _parse_run(raw)
        jobs = self._run_json(
            ["api", f"repos/{self._repository}/actions/runs/{database_id}/jobs"]
        )
        raw_jobs = jobs.get("jobs") if isinstance(jobs, dict) else None
        if not isinstance(raw_jobs, list):
            raise ActionsError("Actions jobs response has no jobs list")
        return ActionsRun(
            database_id=run.database_id,
            display_title=run.display_title,
            status=run.status,
            conclusion=run.conclusion,
            jobs=tuple(_parse_job(raw_job) for raw_job in raw_jobs),
        )

    def get_artifact(self, database_id: int, name: str) -> ActionsArtifact:
        data = self._run_json(
            ["api", f"repos/{self._repository}/actions/runs/{database_id}/artifacts"]
        )
        artifacts = data.get("artifacts") if isinstance(data, dict) else None
        if not isinstance(artifacts, list):
            raise ActionsError("Actions artifacts response has no artifacts list")
        match = next(
            (
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict) and artifact.get("name") == name
            ),
            None,
        )
        if match is None or not isinstance(match.get("id"), int):
            raise ActionsError(f"completed run has no artifact {name!r}")
        archive = self._run_bytes(
            [
                "api",
                f"repos/{self._repository}/actions/artifacts/{match['id']}/zip",
            ]
        )
        return ActionsArtifact(name=name, archive=archive)

    def _run_json(self, args: Sequence[str]) -> Any:
        raw = self._run_bytes(args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ActionsError(f"gh Actions response was not JSON: {exc}") from exc

    def _run_bytes(self, args: Sequence[str]) -> bytes:
        command = ["gh", *args]
        try:
            completed = subprocess.run(command, capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise ActionsError("gh not found on PATH") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ActionsError(
                f"gh Actions command failed ({completed.returncode}): {detail}"
            )
        return completed.stdout


class GitHubActionsExecutionHost:
    """Run each Lane contribution in one GitHub Actions workflow."""

    def __init__(
        self,
        *,
        client: ActionsClient,
        capacity: int,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = _MAX_CONTRIBUTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("execution host capacity must be a finite positive integer")
        self._client = client
        self._capacity = capacity
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._sleep = sleep

    @property
    def placement(self) -> Placement:
        return "github-actions"

    @property
    def isolation_grade(self) -> IsolationGrade:
        return "machine boundary"

    @property
    def capacity(self) -> int:
        return self._capacity

    async def run_contribution(self, request: ContributionRequest) -> ContributionOutcome:
        title = _run_title(request)
        artifact_name = _artifact_name(request)
        try:
            self._client.dispatch(
                _WORKFLOW,
                request.base_revision,
                {"request": _request_json(request)},
            )
        except ActionsError as exc:
            return ContributionFailure(
                reason="workflow_dispatch_failed",
                classification="never_started",
                ending=None,
                detail=str(exc),
            )
        started = self._clock()
        run: ActionsRun | None = None
        try:
            while self._clock() - started < self._timeout_seconds:
                if run is None:
                    discovered = self._client.find_run(title)
                    if discovered is not None:
                        run = self._client.get_run(discovered.database_id)
                else:
                    run = self._client.get_run(run.database_id)
                if run is not None and run.status == "completed":
                    break
                await self._sleep(self._poll_interval_seconds)
        except ActionsError as exc:
            return ContributionFailure(
                reason="workflow_status_unavailable",
                classification="stall",
                ending=None,
                detail=str(exc),
            )
        if run is None:
            return ContributionFailure(
                reason="workflow_run_not_observed",
                classification="stall",
                ending=None,
                detail=f"Actions workflow {title!r} did not become observable",
            )
        if run.status != "completed":
            return ContributionFailure(
                reason="workflow_run_timed_out",
                classification="stall",
                ending=None,
                detail=f"Actions workflow {run.database_id} exceeded six hours",
            )
        if run.conclusion != "success":
            return ContributionFailure(
                reason="workflow_failed",
                classification="never_started",
                ending=None,
                detail=_liveness_detail(run),
            )
        try:
            artifact = self._client.get_artifact(run.database_id, artifact_name)
            completion, events = _read_artifact(artifact)
        except (
            ActionsError,
            OSError,
            UnicodeDecodeError,
            ValueError,
            zipfile.BadZipFile,
        ) as exc:
            return ContributionFailure(
                reason="completion_artifact_unavailable",
                classification="stall",
                ending=None,
                detail=str(exc),
            )
        return ContributionSuccess(
            branch=None,
            remote=completion["remote"],
            ref=completion["ref"],
            sha=completion["sha"],
            events=events,
            placement=self.placement,
            isolation_grade=self.isolation_grade,
            ending=completion["ending"],
        )


def github_actions_capacity(env: Mapping[str, str] = environ) -> int:
    """Read the target account's declared concurrent-job ceiling.

    GitHub does not expose account concurrency through the Actions API.  The
    account owner supplies the ceiling explicitly rather than letting a runner
    CPU count masquerade as an account-wide limit.
    """
    raw = env.get(_CAPACITY_ENV)
    try:
        capacity = int(raw) if raw is not None else 0
    except ValueError as exc:
        raise ValueError(
            f"{_CAPACITY_ENV} must be a finite positive integer"
        ) from exc
    if capacity < 1:
        raise ValueError(f"{_CAPACITY_ENV} must be a finite positive integer")
    return capacity


def _request_json(request: ContributionRequest) -> str:
    return json.dumps(
        {
            "issue_ref": request.issue_ref,
            "prompt": request.prompt,
            "base_revision": request.base_revision,
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "run_id": request.run_id,
        },
        separators=(",", ":"),
    )


def _run_title(request: ContributionRequest) -> str:
    return f"git-loopy {request.run_id} issue {request.issue_ref}"


def _artifact_name(request: ContributionRequest) -> str:
    return f"git-loopy-{request.run_id}-issue-{request.issue_ref}"


def _read_artifact(
    artifact: ActionsArtifact,
) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
    with zipfile.ZipFile(io.BytesIO(artifact.archive)) as zipped:
        names = {name.rsplit("/", 1)[-1]: name for name in zipped.namelist()}
        try:
            completion_raw = zipped.read(names["completion.json"])
            events_raw = zipped.read(names["events.jsonl"])
        except KeyError as exc:
            raise ValueError(
                f"artifact {artifact.name!r} must contain completion.json and events.jsonl"
            ) from exc
    try:
        completion = json.loads(completion_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("completion artifact is not JSON") from exc
    if not isinstance(completion, dict):
        raise ValueError("completion artifact must be a JSON object")
    remote, ref, sha, ending = _parse_completion(completion)
    events: list[Mapping[str, Any]] = []
    for line in events_raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("event artifact lines must be JSON objects")
        event.pop("observed_monotonic", None)
        events.append(event)
    return (
        {"remote": remote, "ref": ref, "sha": sha, "ending": ending},
        tuple(events),
    )


def _parse_completion(
    completion: Mapping[str, Any],
) -> tuple[str, str, str, SessionOutcomeRecord]:
    remote = completion.get("remote")
    ref = completion.get("ref")
    sha = completion.get("sha")
    ending = completion.get("ending")
    if not all(isinstance(value, str) and value for value in (remote, ref, sha)):
        raise ValueError("completion artifact must name remote, ref, and sha")
    if not isinstance(ending, Mapping):
        raise ValueError("completion artifact must carry a session ending")
    outcome = ending.get("outcome")
    termination = ending.get("termination")
    progressed = ending.get("progressed")
    if outcome is not None and not isinstance(outcome, str):
        raise ValueError("completion ending outcome must be a string or null")
    if not isinstance(termination, str) or not isinstance(progressed, bool):
        raise ValueError("completion ending must name termination and progressed")
    try:
        return (
            remote,
            ref,
            sha,
            SessionOutcomeRecord(
                outcome=None if outcome is None else SessionOutcome(outcome),
                progressed=progressed,
                termination=SessionTermination(termination),
            ),
        )
    except ValueError as exc:
        raise ValueError(f"completion ending is invalid: {exc}") from exc


def _parse_run(raw: Any) -> ActionsRun:
    if not isinstance(raw, dict):
        raise ActionsError("Actions run must be a JSON object")
    try:
        return ActionsRun(
            database_id=int(raw["id"]),
            display_title=str(raw["display_title"]),
            status=str(raw["status"]),
            conclusion=(
                None if raw.get("conclusion") is None else str(raw["conclusion"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ActionsError(f"Actions run is malformed: {exc}") from exc


def _parse_job(raw: Any) -> ActionsJob:
    if not isinstance(raw, dict):
        raise ActionsError("Actions job must be a JSON object")
    steps = raw.get("steps", [])
    if not isinstance(steps, list):
        raise ActionsError("Actions job steps must be a list")
    try:
        return ActionsJob(
            name=str(raw["name"]),
            status=str(raw["status"]),
            conclusion=(
                None if raw.get("conclusion") is None else str(raw["conclusion"])
            ),
            steps=tuple(
                ActionsStep(
                    name=str(step["name"]),
                    status=str(step["status"]),
                    conclusion=(
                        None
                        if step.get("conclusion") is None
                        else str(step["conclusion"])
                    ),
                )
                for step in steps
                if isinstance(step, dict)
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ActionsError(f"Actions job is malformed: {exc}") from exc


def _liveness_detail(run: ActionsRun) -> str:
    jobs = "; ".join(
        f"{job.name}={job.status}/{job.conclusion or 'pending'}"
        + (
            " ("
            + ", ".join(
                f"{step.name}={step.status}/{step.conclusion or 'pending'}"
                for step in job.steps
            )
            + ")"
            if job.steps
            else ""
        )
        for job in run.jobs
    )
    return f"Actions workflow {run.database_id} concluded {run.conclusion!r}: {jobs}"
