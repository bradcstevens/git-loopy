"""``git_loopy.github_actions_host`` — the GitHub Actions Execution host (#460).

The second implementation of the :mod:`git_loopy.execution_host` seam, and the
first that puts a **Lane contribution** on a machine that is not the operator's.

**One workflow run per issue, dispatched on demand.** The orchestrator's
scheduler reserves a Lane slot and that reservation *becomes* a dispatch — one
``workflow_dispatch`` per contribution. A matrix is refused: its legs are fixed
at run start, so a matrix can only ever express the **Wave** that ADR-0020
retired, and rolling dispatch survives only if a reservation can turn into a
dispatch at the moment it is taken.

**The orchestrator keeps supervising from the operator's machine.** Actions'
six-hour job cap is hard and plan-independent, so it must bound one
*contribution* rather than a whole Run.

**Liveness is coarse and live; the Event stream is complete and late.** The
platform offers no supported live log stream — its own CLI refuses to tail a
running job — so this host polls job and step status for coarse liveness and
retrieves the contribution's complete Events from the end-of-job artifact,
which is API-readable as soon as it is uploaded. Remote Events therefore
arrive batched, late and **backdated**; :func:`read_completion_artifact`
strips the monotonic observation field and never re-stamps it, because a
remote machine's monotonic clock has no relationship to the orchestrator's.

**The host authenticates itself and never receives a credential.** The job
authenticates with the built-in ``GITHUB_TOKEN`` plus a Copilot-requests write
permission; nothing is transported to it and no secret is stored.

The seam's six refusals hold here exactly as they do locally: this host
declares capacity but never schedules, is no policy engine, never silently
retries (one call in, one outcome out — a failed dispatch is a *value*), never
writes to the issue tracker, is not the Run's observability endpoint, and
never mints identity — the **dispatch token** it correlates a run by is
derived from the ``run_id`` and issue reference it was *handed*.
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
from typing import Any, Awaitable, Callable, Mapping, Protocol, TypeVar

from git_loopy.execution_host import (
    REASON_CHECKPOINT_FAILED,
    ContributionFailure,
    ContributionOutcome,
    ContributionRequest,
    ContributionSuccess,
    IsolationGrade,
    Placement,
)
from git_loopy.session_outcome import (
    SessionError,
    SessionErrorKind,
    SessionOutcome,
    SessionOutcomeRecord,
    SessionTermination,
)

__all__ = [
    "ACTIONS_CAPACITY_ENV",
    "CONTRIBUTION_ARTIFACT_COMPLETION",
    "CONTRIBUTION_ARTIFACT_EVENTS",
    "CONTRIBUTION_ARTIFACT_RESULT",
    "CONTRIBUTION_JOB_TIMEOUT_SECONDS",
    "GITHUB_ACTIONS_ISOLATION_GRADE",
    "GITHUB_ACTIONS_PLACEMENT",
    "LANE_CONTRIBUTION_WORKFLOW",
    "MONOTONIC_OBSERVATION_FIELD",
    "ActionsArtifact",
    "ActionsClient",
    "ActionsError",
    "ActionsJob",
    "ActionsRun",
    "ActionsStep",
    "CONTRIBUTION_STEP_NAME",
    "DispatchHandle",
    "GitHubActionsExecutionHost",
    "RemoteCompletion",
    "SubprocessActionsClient",
    "artifact_name",
    "dispatch_token",
    "assert_workflow_installed",
    "workflow_ref",
    "ACTIONS_WORKFLOW_REF_ENV",
    "github_actions_capacity",
    "liveness_summary",
    "read_completion_artifact",
    "read_partial_result",
    "session_budget",
]

#: This host's declared placement — the identifier the parallel capability
#: manifest carries beside ``"local"`` (#450).
GITHUB_ACTIONS_PLACEMENT: Placement = "github-actions"

#: The second and last grade in the closed set (spec #445 §C): the
#: contribution runs on a machine the operator does not own, with none of the
#: operator's credentials or network.
GITHUB_ACTIONS_ISOLATION_GRADE: IsolationGrade = "machine boundary"

#: The workflow one contribution is dispatched into.
LANE_CONTRIBUTION_WORKFLOW = "lane-contribution.yml"

_T = TypeVar("_T")

#: Actions' hard, plan-independent job cap. It bounds one contribution, never
#: a Run.
CONTRIBUTION_JOB_TIMEOUT_SECONDS = 6 * 60 * 60
#: Time reserved, inside the job cap, for everything that has to happen *after*
#: the session: the Checkpoint, the push, the completion record and the artifact
#: upload. The cap is a hard, plan-independent wall the platform enforces by
#: killing the job, and a killed job runs no `always()` step --- so a session
#: allowed to spend the entire cap would take its own ending and every Event
#: down with it precisely when the contribution ran long enough to matter.
CONTRIBUTION_FINALIZATION_RESERVE_SECONDS = 10 * 60
CONTRIBUTION_SESSION_BUDGET_SECONDS = (
    CONTRIBUTION_JOB_TIMEOUT_SECONDS - CONTRIBUTION_FINALIZATION_RESERVE_SECONDS
)

#: The account's concurrent-job ceiling. GitHub does not expose it through the
#: Actions API, so the operator declares it rather than letting a local core
#: count masquerade as an account-wide limit.
ACTIONS_CAPACITY_ENV = "GIT_LOOPY_GITHUB_ACTIONS_CAPACITY"

#: The branch or tag whose *definition* of the contribution workflow runs.
#: Overridable because an operator testing a change to the workflow needs to
#: dispatch the version on their branch, not the one already on the default.
ACTIONS_WORKFLOW_REF_ENV = "GIT_LOOPY_GITHUB_ACTIONS_WORKFLOW_REF"

#: The two members of a contribution's completion artifact.
CONTRIBUTION_ARTIFACT_COMPLETION = "completion.json"
CONTRIBUTION_ARTIFACT_EVENTS = "events.jsonl"
CONTRIBUTION_ARTIFACT_RESULT = "result.json"

#: The observation field a remote Event must never carry across the machine
#: boundary. It is *stripped*, never re-stamped: a remote machine's monotonic
#: clock shares no origin with the orchestrator's, and re-stamping every
#: batched Event at ingest time would collapse a six-hour contribution onto
#: one instant.
MONOTONIC_OBSERVATION_FIELD = "observed_monotonic"


class ActionsError(RuntimeError):
    """An Actions API call failed or answered with something unusable."""


@dataclass(frozen=True)
class ActionsStep:
    """One Actions job step's coarse, polled status."""

    name: str
    status: str
    conclusion: str | None = None


@dataclass(frozen=True)
class ActionsJob:
    """One Actions job's coarse, polled status and its steps."""

    name: str
    status: str
    conclusion: str | None = None
    steps: tuple[ActionsStep, ...] = ()


@dataclass(frozen=True)
class ActionsRun:
    """A dispatched workflow run as the API reports it."""

    database_id: int
    status: str
    conclusion: str | None = None
    jobs: tuple[ActionsJob, ...] = ()


@dataclass(frozen=True)
class ActionsArtifact:
    """A completed run's artifact, read as bytes rather than onto disk."""

    name: str
    archive: bytes


@dataclass(frozen=True)
class DispatchHandle:
    """What the orchestrator holds for a contribution **from dispatch**.

    ``workflow_dispatch`` answers ``204 No Content`` — it never returns a run
    id — so a handle that waited for the API to name the run would not exist
    until the run did. Instead the orchestrator mints the correlation itself:
    the **dispatch token** goes out *with* the dispatch, the workflow echoes it
    into its own ``run-name``, and the handle exists from the instant the
    dispatch is accepted. ``database_id`` is filled in later, when the run
    becomes observable; the handle's identity never depends on it.

    The token is derived from the ``run_id`` and issue reference the host was
    *handed* — deriving, not minting, keeps the seam's no-identity refusal.
    """

    token: str
    workflow: str
    ref: str
    database_id: int | None = None
    liveness: ActionsRun | None = None

    def observed_as(self, run: ActionsRun) -> "DispatchHandle":
        """Return this handle bound to the run the API finally named."""
        return DispatchHandle(
            token=self.token,
            workflow=self.workflow,
            ref=self.ref,
            database_id=run.database_id,
            liveness=run,
        )


class ActionsClient(Protocol):
    """The small Actions API surface this host needs.

    Deliberately four calls with no live-log method: the platform offers no
    supported way to tail a running job, so a client that promised one could
    not be implemented.
    """

    def dispatch(self, workflow: str, ref: str, inputs: Mapping[str, str]) -> None:
        """Dispatch exactly one workflow run. Raise :class:`ActionsError` on refusal."""
        ...

    def find_run(self, token: str) -> ActionsRun | None:
        """Return the run whose display title carries ``token``, if visible yet."""
        ...

    def get_run(self, database_id: int) -> ActionsRun:
        """Read one run's coarse job and step status."""
        ...

    def get_artifact(self, database_id: int, name: str) -> ActionsArtifact:
        """Read a completed run's named artifact."""
        ...


def github_actions_capacity(env: Mapping[str, str] = environ) -> int:
    """Return the account's declared concurrent-job ceiling.

    Raises:
        ValueError: when the ceiling is absent or is not a finite positive
            integer. A Run that names this host without one is refused at
            preflight rather than running on a guess.
    """
    raw = env.get(ACTIONS_CAPACITY_ENV)
    problem = (
        f"{ACTIONS_CAPACITY_ENV} must be a finite positive integer "
        "(the account's concurrent-job ceiling)"
    )
    if raw is None:
        raise ValueError(f"{problem}; it is not set")
    try:
        capacity = int(raw)
    except ValueError as exc:
        raise ValueError(f"{problem}; got {raw!r}") from exc
    if capacity < 1:
        raise ValueError(f"{problem}; got {raw!r}")
    return capacity


def workflow_ref(client: "SupportsDefaultBranch") -> str:
    """Resolve the branch or tag the contribution workflow is dispatched on.

    Asked of the repository rather than assumed, and kept strictly separate
    from a contribution's ``base_revision``: the dispatch API resolves ``ref``
    to a branch or tag and refuses a commit SHA, which is exactly what
    ``base_revision`` is.
    """
    override = environ.get(ACTIONS_WORKFLOW_REF_ENV, "").strip()
    return override or client.default_branch()


def assert_workflow_installed(client: "SupportsWorkflowLookup") -> None:
    """Refuse at preflight if the repository has no contribution workflow.

    The host dispatches into the repository the Run is operating on, and that
    repository is not necessarily this one --- the workflow and its composite
    setup step are files, so an operator pointing a Run at their own project
    has to install them there. Left unchecked, the omission surfaces as a
    refused dispatch at the *first Lane reservation*, by which time Lanes are
    open and the failure looks like a contribution's rather than the
    environment's. Asked once, before any Lane exists, it is a preflight
    refusal an operator can act on.
    """
    if not client.workflow_installed(LANE_CONTRIBUTION_WORKFLOW):
        raise ActionsError(
            f"the repository has no {LANE_CONTRIBUTION_WORKFLOW} workflow. The "
            "Actions Execution host dispatches into the repository the Run "
            "operates on, so that repository must carry "
            f".github/workflows/{LANE_CONTRIBUTION_WORKFLOW} and the "
            ".github/actions/setup-lane-contribution composite step it uses."
        )


class SupportsDefaultBranch(Protocol):
    """The one repository fact resolving a dispatchable ref needs."""

    def default_branch(self) -> str: ...


class SupportsWorkflowLookup(Protocol):
    """The one repository fact a preflight installation check needs."""

    def workflow_installed(self, workflow: str) -> bool: ...


def dispatch_token(request: ContributionRequest) -> str:
    """Derive the correlation token for one contribution's workflow run."""
    return f"git-loopy {request.run_id} issue {request.issue_ref}"


def artifact_name(request: ContributionRequest) -> str:
    """Derive the completion artifact's name for one contribution."""
    return f"git-loopy-{request.run_id}-issue-{request.issue_ref}"


GhRunner = Callable[[list[str]], bytes]


#: Every Actions API call is bounded. These calls are made on a worker thread
#: rather than the event loop, but an unbounded one would still hold that
#: thread and that Lane's supervision open forever -- and a hung poll cannot
#: report the very unresponsiveness it is meant to detect.
GH_CALL_TIMEOUT_SECONDS = 120.0


def _gh(argv: list[str]) -> bytes:
    """Invoke the operator's authenticated ``gh`` CLI and return its stdout."""
    try:
        completed = subprocess.run(
            ["gh", *argv],
            capture_output=True,
            check=False,
            timeout=GH_CALL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ActionsError("gh is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ActionsError(
            f"gh Actions call did not answer within {GH_CALL_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ActionsError(
            f"gh Actions call failed ({completed.returncode}): {detail}"
        )
    return completed.stdout


class SubprocessActionsClient:
    """The production :class:`ActionsClient`, over the authenticated ``gh`` CLI.

    The orchestrator side reuses the operator's own CLI credential exactly as
    every other GitHub call in git-loopy does; nothing is minted, stored, or
    handed to the remote job — the job authenticates itself with the built-in
    job token.

    The subprocess boundary is injected as ``run`` so the argv this client
    builds can be asserted without ever invoking ``gh``.
    """

    def __init__(self, repository: str, *, run: GhRunner = _gh) -> None:
        self._repository = repository
        self._run = run

    @classmethod
    def discover(cls, *, run: GhRunner = _gh) -> "SubprocessActionsClient":
        """Bind to the repository the authenticated CLI resolves."""
        raw = run(["repo", "view", "--json", "nameWithOwner"])
        try:
            name_with_owner = json.loads(raw)["nameWithOwner"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ActionsError("gh did not name the Actions repository") from exc
        if not isinstance(name_with_owner, str) or not name_with_owner:
            raise ActionsError("gh named an invalid Actions repository")
        return cls(name_with_owner, run=run)

    def default_branch(self) -> str:
        """The branch a dispatch resolves the workflow definition on.

        Asked of the repository rather than assumed, because the dispatch API
        takes a branch or tag and refuses a commit SHA -- so the contribution's
        ``base_revision`` can never serve here.
        """
        raw = self._run(["repo", "view", "--json", "defaultBranchRef"])
        try:
            branch = json.loads(raw)["defaultBranchRef"]["name"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ActionsError("gh did not name the repository default branch") from exc
        if not isinstance(branch, str) or not branch:
            raise ActionsError("gh named an invalid repository default branch")
        return branch

    def workflow_installed(self, workflow: str) -> bool:
        """Whether the dispatch target actually carries this workflow.

        A 404 is the answer, not a failure: the question is precisely whether
        the file is absent, so the one status code that means "it is" must not
        propagate as an API error.
        """
        try:
            self._run(
                [
                    "api",
                    f"repos/{self._repository}/actions/workflows/{workflow}",
                    "--jq",
                    ".id",
                ]
            )
        except ActionsError as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                return False
            raise
        return True

    def dispatch(self, workflow: str, ref: str, inputs: Mapping[str, str]) -> None:
        fields = [
            item
            for key in sorted(inputs)
            for item in ("-f", f"inputs[{key}]={inputs[key]}")
        ]
        self._run(
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

    def find_run(self, token: str) -> ActionsRun | None:
        data = self._json(
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
            raise ActionsError("Actions runs response carries no workflow_runs list")
        for raw in runs:
            if not isinstance(raw, dict):
                raise ActionsError("Actions run is not a JSON object")
            # Exact, never substring: the token for issue 46 is a prefix of
            # the token for issue 460, so a containment test inside one Run
            # would hand one contribution the other's run -- and orphan the
            # run it actually started.
            if str(raw.get("display_title", "")) == token:
                return _parse_run(raw)
        return None

    def get_run(self, database_id: int) -> ActionsRun:
        run = _parse_run(
            self._json(["api", f"repos/{self._repository}/actions/runs/{database_id}"])
        )
        payload = self._json(
            ["api", f"repos/{self._repository}/actions/runs/{database_id}/jobs"]
        )
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise ActionsError("Actions jobs response carries no jobs list")
        return ActionsRun(
            database_id=run.database_id,
            status=run.status,
            conclusion=run.conclusion,
            jobs=tuple(_parse_job(job) for job in jobs),
        )

    def get_artifact(self, database_id: int, name: str) -> ActionsArtifact:
        payload = self._json(
            ["api", f"repos/{self._repository}/actions/runs/{database_id}/artifacts"]
        )
        artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
        if not isinstance(artifacts, list):
            raise ActionsError("Actions artifacts response carries no artifacts list")
        match = next(
            (
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict)
                and artifact.get("name") == name
                and isinstance(artifact.get("id"), int)
            ),
            None,
        )
        if match is None:
            raise ActionsError(f"completed run {database_id} has no artifact {name!r}")
        return ActionsArtifact(
            name=name,
            archive=self._run(
                ["api", f"repos/{self._repository}/actions/artifacts/{match['id']}/zip"]
            ),
        )

    def _json(self, argv: list[str]) -> Any:
        raw = self._run(argv)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ActionsError(f"Actions response was not JSON: {exc}") from exc


def _parse_run(raw: Any) -> ActionsRun:
    if not isinstance(raw, dict):
        raise ActionsError("Actions run is not a JSON object")
    try:
        return ActionsRun(
            database_id=int(raw["id"]),
            status=str(raw["status"]),
            conclusion=None if raw.get("conclusion") is None else str(raw["conclusion"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ActionsError(f"Actions run is malformed: {exc}") from exc


def _parse_job(raw: Any) -> ActionsJob:
    if not isinstance(raw, dict):
        raise ActionsError("Actions job is not a JSON object")
    steps = raw.get("steps", [])
    if not isinstance(steps, list):
        raise ActionsError("Actions job steps are not a list")
    try:
        return ActionsJob(
            name=str(raw["name"]),
            status=str(raw["status"]),
            conclusion=None if raw.get("conclusion") is None else str(raw["conclusion"]),
            steps=tuple(
                ActionsStep(
                    name=str(step["name"]),
                    status=str(step["status"]),
                    conclusion=(
                        None if step.get("conclusion") is None else str(step["conclusion"])
                    ),
                )
                for step in steps
                if isinstance(step, dict)
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ActionsError(f"Actions job is malformed: {exc}") from exc


class GitHubActionsExecutionHost:
    """Run one **Lane contribution** in one dispatched GitHub Actions run."""

    def __init__(
        self,
        *,
        client: ActionsClient,
        capacity: int,
        workflow_ref: str,
        send_timeout_seconds: float,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = CONTRIBUTION_JOB_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError(
                "execution host capacity must be a finite positive integer"
            )
        if not workflow_ref:
            raise ValueError("execution host workflow ref must name a branch or tag")
        if send_timeout_seconds <= 0:
            raise ValueError("execution host send timeout must be positive")
        self._client = client
        self._capacity = capacity
        self._workflow_ref = workflow_ref
        self._send_timeout_seconds = send_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._sleep = sleep
        self._handles: dict[str, DispatchHandle] = {}

    @property
    def workflow_ref(self) -> str:
        """The branch or tag the contribution workflow is dispatched on.

        Deliberately **not** the contribution's ``base_revision``. The dispatch
        API resolves ``ref`` to a branch or tag and rejects a commit SHA
        outright, and ``base_revision`` is a SHA — so conflating the two would
        refuse every real dispatch before any contribution started. They answer
        different questions anyway: this names *which definition of the
        workflow runs*, and ``base_revision`` names *what the job checks out*,
        which is why the job takes the latter from its request rather than from
        the ref it was started on.
        """
        return self._workflow_ref

    @property
    def placement(self) -> Placement:
        return GITHUB_ACTIONS_PLACEMENT

    @property
    def isolation_grade(self) -> IsolationGrade:
        return GITHUB_ACTIONS_ISOLATION_GRADE

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def open_dispatch_handles(self) -> tuple[DispatchHandle, ...]:
        """The handles this host currently holds, oldest dispatch first.

        A handle appears the instant its dispatch is accepted and disappears
        when its contribution settles, so an in-flight remote contribution is
        attributable for its whole life — including the window before the API
        can name its run.
        """
        return tuple(self._handles.values())

    async def run_contribution(
        self, request: ContributionRequest
    ) -> ContributionOutcome:
        """Dispatch one run for ``request``, supervise it, and report its outcome.

        One call in, one outcome out: a refused dispatch, an unobservable run,
        an exceeded cap and an unreadable artifact are all *values*, never
        exceptions and never a second attempt.
        """
        token = dispatch_token(request)
        handle = DispatchHandle(
            token=token, workflow=LANE_CONTRIBUTION_WORKFLOW, ref=self._workflow_ref
        )
        self._handles[token] = handle
        try:
            return await self._supervise(request, token)
        finally:
            self._handles.pop(token, None)

    async def _supervise(
        self, request: ContributionRequest, token: str
    ) -> ContributionOutcome:
        try:
            await self._call(
                self._client.dispatch,
                LANE_CONTRIBUTION_WORKFLOW,
                self._workflow_ref,
                {
                    "request": _request_json(request, self._send_timeout_seconds),
                    "dispatch_token": token,
                },
            )
        except ActionsError as exc:
            return ContributionFailure(
                reason="workflow_dispatch_refused",
                classification="never_started",
                ending=None,
                detail=str(exc),
            )
        try:
            run = await self._await_completion(token)
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
                detail=(
                    f"no Actions run carrying {token!r} became observable within "
                    f"{self._timeout_seconds:.0f}s"
                ),
            )
        if run.status != "completed":
            return ContributionFailure(
                reason="workflow_run_exceeded_job_cap",
                classification="stall",
                ending=None,
                detail=(
                    f"Actions run {run.database_id} was still {run.status!r} after "
                    f"{self._timeout_seconds:.0f}s: {liveness_summary(run)}"
                ),
            )
        if run.conclusion != "success":
            # The job uploads its result and its Events unconditionally, so a
            # run that failed *after* the session may still be able to account
            # for itself. A session with a known ending is a breach --- the
            # host had it and lost it --- and calling that a blameless stall
            # would throw away the only explanation there is (ADR-0050).
            salvaged = await self._salvage_ending(run, artifact_name(request))
            if salvaged is not None:
                ending, events = salvaged
                return ContributionFailure(
                    reason="workflow_publication_failed",
                    classification="breach",
                    ending=ending,
                    events=events,
                    detail=(
                        f"Actions run {run.database_id} concluded "
                        f"{run.conclusion!r} after its session ended: "
                        f"{liveness_summary(run)}"
                    ),
                )
            reached_the_agent = _contribution_step_ran(run)
            return ContributionFailure(
                reason=(
                    "workflow_run_failed"
                    if reached_the_agent
                    else "workflow_setup_failed"
                ),
                classification="stall" if reached_the_agent else "never_started",
                ending=None,
                detail=(
                    f"Actions run {run.database_id} concluded {run.conclusion!r}: "
                    f"{liveness_summary(run)}"
                ),
            )
        name = artifact_name(request)
        try:
            artifact = await self._call(self._client.get_artifact, run.database_id, name)
            completion = read_completion_artifact(artifact)
        except (ActionsError, ValueError, OSError, zipfile.BadZipFile) as exc:
            return ContributionFailure(
                reason="completion_artifact_unusable",
                classification="stall",
                ending=None,
                detail=f"Actions run {run.database_id} artifact {name!r}: {exc}",
            )
        if completion.checkpoint_failed:
            # ADR-0004's Checkpoint is the one commit a host authors on its own
            # initiative, so failing it is the host's breach. The ending is
            # carried through intact: the session is not at fault, and the
            # Run's wire already publishes a matching terminal reason readers
            # must tell apart from `unchanged_branch`.
            return ContributionFailure(
                reason=REASON_CHECKPOINT_FAILED,
                classification="breach",
                ending=completion.ending,
                events=completion.events,
                detail=(
                    f"Actions run {run.database_id} could not Checkpoint the "
                    "worktree its agent left dirty"
                ),
            )
        return ContributionSuccess(
            branch=None,
            remote=completion.remote,
            ref=completion.ref,
            sha=completion.sha,
            events=completion.events,
            placement=self.placement,
            isolation_grade=self.isolation_grade,
            ending=completion.ending,
        )

    async def _salvage_ending(
        self, run: ActionsRun, name: str
    ) -> tuple[SessionOutcomeRecord, tuple[Mapping[str, Any], ...]] | None:
        """Recover a failed run's session ending from whatever it did upload.

        The artifact upload runs on ``always()`` and the result is written
        independently of the push, so a job that failed *after* its session ---
        a protected ref, a vanished remote --- still hands back an ending and a
        complete Event stream. Returning ``None`` means the job genuinely
        cannot account for itself; anything else would be inventing an
        explanation the artifact does not support.
        """
        try:
            artifact = await self._call(self._client.get_artifact, run.database_id, name)
            return read_partial_result(artifact)
        except (ActionsError, ValueError, OSError, zipfile.BadZipFile):
            return None

    async def _call(self, call: Callable[..., _T], /, *args: Any) -> _T:
        """Make one blocking Actions API call without stalling every other Lane.

        The client is deliberately synchronous — it is a thin shell over ``gh``,
        and a synchronous shell is far easier to assert argv against than an
        async one. But this host supervises N concurrent Lane contributions on
        one event loop, so calling it inline would let a single slow GitHub
        request freeze every *other* Lane's polling, and the six-hour cap that
        is supposed to bound this contribution could not even fire.

        Offloading to a worker thread is the whole fix: the call still blocks,
        but it blocks a thread nobody else is waiting on.
        """
        return await asyncio.to_thread(call, *args)

    async def _await_completion(self, token: str) -> ActionsRun | None:
        """Poll coarse job and step status until the run completes or time runs out.

        Actions publishes no supported live log stream — its own CLI refuses to
        tail a running job — so this is the whole of what liveness can be: the
        run's status, its jobs' statuses, and their steps'. Each reading is
        republished on the handle, so the orchestrator's view of an in-flight
        remote contribution is never staler than the last poll.
        """
        started = self._clock()
        while True:
            handle = self._handles[token]
            if handle.database_id is None:
                sighted = await self._call(self._client.find_run, token)
                observed = (
                    None
                    if sighted is None
                    else await self._call(self._client.get_run, sighted.database_id)
                )
            else:
                observed = await self._call(self._client.get_run, handle.database_id)
            if observed is not None:
                handle = handle.observed_as(observed)
                self._handles[token] = handle
                if observed.status == "completed":
                    return observed
            if self._clock() - started >= self._timeout_seconds:
                return handle.liveness
            await self._sleep(self._poll_interval_seconds)


@dataclass(frozen=True)
class RemoteCompletion:
    """What a contribution's end-of-job artifact proves about it.

    The completion triple Integration materializes (``remote``, ``ref``,
    ``sha``), the Agent session's ending, and the contribution's complete —
    batched, late and backdated — Event stream.
    """

    remote: str
    ref: str
    sha: str
    ending: SessionOutcomeRecord
    events: tuple[Mapping[str, Any], ...]
    #: Whether the *runner* failed to Checkpoint the tree the agent left dirty.
    #: Carried beside the ending rather than folded into it, because it is a
    #: host failure and not a session one: the session can have ended perfectly
    #: well and still have its residue lost with the machine.
    checkpoint_failed: bool = False


def read_partial_result(
    artifact: ActionsArtifact,
) -> tuple[SessionOutcomeRecord, tuple[Mapping[str, Any], ...]] | None:
    """Read a *failed* job's result: what it observed, without a completion triple.

    The job writes its result and its Events independently of the push and
    uploads them on ``always()``, so this is what survives a run that failed
    after its session ended. ``None`` means the job could not account for
    itself at all --- which is a genuinely different fact from a session that
    ended badly, and the classification above depends on telling them apart.
    """
    with zipfile.ZipFile(io.BytesIO(artifact.archive)) as zipped:
        members = {name.rsplit("/", 1)[-1]: name for name in zipped.namelist()}
        if CONTRIBUTION_ARTIFACT_RESULT not in members:
            return None
        result_raw = zipped.read(members[CONTRIBUTION_ARTIFACT_RESULT])
        events_raw = (
            zipped.read(members[CONTRIBUTION_ARTIFACT_EVENTS])
            if CONTRIBUTION_ARTIFACT_EVENTS in members
            else b""
        )
    result = json.loads(result_raw)
    if not isinstance(result, Mapping):
        raise ValueError(f"{CONTRIBUTION_ARTIFACT_RESULT} is not a JSON object")
    return _parse_ending(result.get("ending")), _parse_events(events_raw)


def read_completion_artifact(artifact: ActionsArtifact) -> RemoteCompletion:
    """Read one contribution's completion artifact.

    Raises:
        ValueError: when the artifact is missing a member or any member is
            unusable. The artifact is the only remote-to-local channel there
            is, so a malformed one proves nothing and must not be smoothed
            over into a partial success.
    """
    with zipfile.ZipFile(io.BytesIO(artifact.archive)) as zipped:
        members = {name.rsplit("/", 1)[-1]: name for name in zipped.namelist()}
        for required in (CONTRIBUTION_ARTIFACT_COMPLETION, CONTRIBUTION_ARTIFACT_EVENTS):
            if required not in members:
                raise ValueError(f"artifact is missing {required}")
        completion_raw = zipped.read(members[CONTRIBUTION_ARTIFACT_COMPLETION])
        events_raw = zipped.read(members[CONTRIBUTION_ARTIFACT_EVENTS])
    try:
        completion = json.loads(completion_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{CONTRIBUTION_ARTIFACT_COMPLETION} is not JSON: {exc}") from exc
    if not isinstance(completion, Mapping):
        raise ValueError(f"{CONTRIBUTION_ARTIFACT_COMPLETION} is not a JSON object")
    remote = completion.get("remote")
    ref = completion.get("ref")
    sha = completion.get("sha")
    if not all(isinstance(value, str) and value for value in (remote, ref, sha)):
        raise ValueError("completion must name remote, ref and sha")
    return RemoteCompletion(
        remote=str(remote),
        ref=str(ref),
        sha=str(sha),
        ending=_parse_ending(completion.get("ending")),
        events=_parse_events(events_raw),
        checkpoint_failed=completion.get("checkpoint_failed") is True,
    )


def _parse_ending(raw: Any) -> SessionOutcomeRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("completion must carry the session ending")
    outcome = raw.get("outcome")
    termination = raw.get("termination")
    progressed = raw.get("progressed")
    if outcome is not None and not isinstance(outcome, str):
        raise ValueError("session ending outcome must be a string or null")
    if not isinstance(termination, str) or not isinstance(progressed, bool):
        raise ValueError("session ending must name termination and progressed")
    try:
        return SessionOutcomeRecord(
            outcome=None if outcome is None else SessionOutcome(outcome),
            progressed=progressed,
            termination=SessionTermination(termination),
            # Carried across the boundary rather than dropped: the structured
            # identity behind a remote failure is the one thing an operator
            # cannot recover by re-reading the job's own logs, because the
            # runner that held them is gone.
            error=_parse_error(raw.get("error")),
        )
    except ValueError as exc:
        raise ValueError(f"session ending is invalid: {exc}") from exc


def _parse_error(raw: Any) -> SessionError | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("session ending error must be an object")
    kind = raw.get("kind")
    origin = raw.get("origin")
    if not isinstance(kind, str) or not isinstance(origin, str):
        raise ValueError("session ending error must name kind and origin")
    status = raw.get("status_code")
    return SessionError(
        kind=SessionErrorKind(kind),
        origin=origin,
        error_type=_optional_text(raw.get("error_type")),
        error_code=_optional_text(raw.get("error_code")),
        status_code=status if isinstance(status, int) else None,
        message=_optional_text(raw.get("message")),
        model=_optional_text(raw.get("model")),
        service_request_id=_optional_text(raw.get("service_request_id")),
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_events(raw: bytes) -> tuple[Mapping[str, Any], ...]:
    """Decode the contribution's Events, stripping the monotonic observation.

    The envelopes are otherwise left exactly as the remote machine wrote them:
    their wall-clock ``ts`` is backdated by design and re-stamping it would
    destroy the only honest timing a batched, late stream still carries.
    """
    events: list[Mapping[str, Any]] = []
    try:
        # Split on the delimiter the writer actually uses, not on everything
        # Python calls a line boundary. `str.splitlines()` also breaks on
        # U+2028, U+2029 and U+0085 --- which the Event writer emits *raw*
        # inside JSON strings (it serializes with `ensure_ascii=False`), so an
        # agent that quoted one would have its record torn in half and the
        # whole artifact rejected as malformed.
        lines = raw.decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{CONTRIBUTION_ARTIFACT_EVENTS} is not UTF-8: {exc}") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{CONTRIBUTION_ARTIFACT_EVENTS} line is not JSON: {exc}"
            ) from exc
        if not isinstance(event, dict):
            raise ValueError(f"{CONTRIBUTION_ARTIFACT_EVENTS} line is not a JSON object")
        event.pop(MONOTONIC_OBSERVATION_FIELD, None)
        events.append(event)
    return tuple(events)


#: The job step whose status separates ADR-0050's two blameless classes: a run
#: that failed before it is ``never_started``, and one that failed after it
#: went dark with a session it cannot account for.
CONTRIBUTION_STEP_NAME = "Run contribution"


def _contribution_step_ran(run: ActionsRun) -> bool:
    return any(
        step.name == CONTRIBUTION_STEP_NAME
        and step.conclusion not in (None, "skipped")
        for job in run.jobs
        for step in job.steps
    )


def liveness_summary(run: ActionsRun) -> str:
    """Render the coarse job and step status a poll observed."""
    return "; ".join(
        f"{job.name}={job.status}/{job.conclusion or 'pending'}"
        + (
            " ["
            + ", ".join(
                f"{step.name}={step.status}/{step.conclusion or 'pending'}"
                for step in job.steps
            )
            + "]"
            if job.steps
            else ""
        )
        for job in run.jobs
    ) or "no jobs observed"


def session_budget(send_timeout_seconds: float) -> float:
    """Clamp a Run's send timeout to what one job can actually spend on a session.

    The operator's timeout is a Run-wide fact and knows nothing about the six
    hours this host's job gets. Transported unclamped, a longer one is a
    fiction --- the platform kills the job first --- and one equal to the cap
    is worse than a fiction, because it leaves no time to record the ending it
    just spent six hours producing.
    """
    return min(send_timeout_seconds, CONTRIBUTION_SESSION_BUDGET_SECONDS)


def _request_json(request: ContributionRequest, send_timeout_seconds: float) -> str:
    """Serialize the outcome contract's input for the dispatched job.

    Carries what the job needs to reproduce the contribution and nothing that
    would be a credential: the seam hands a host no secret, and this input
    crosses a machine boundary in the clear.

    The **Effective Skill policy** travels as its *disabled* half only. The
    enabled half needs no transport --- ADR-0025 fixes the Skill root to the
    catalog ``git-loopy init`` installs, and the job installs the same pinned
    revision --- but the disabled half cannot be re-derived remotely, and
    without it a Skill the operator switched off for this Run would stay usable
    on precisely the contributions that ran off their machine.
    """
    return json.dumps(
        {
            "issue_ref": request.issue_ref,
            "prompt": request.prompt,
            "base_revision": request.base_revision,
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "run_id": request.run_id,
            "disabled_skills": list(_disabled_skills(request.skill_policy)),
            "send_timeout_seconds": session_budget(send_timeout_seconds),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _disabled_skills(policy: Any) -> tuple[str, ...]:
    """The Skill names this Run switched off, or ``()`` when none is in force."""
    disabled = getattr(policy, "disabled_skills", ())
    return tuple(str(name) for name in disabled)
