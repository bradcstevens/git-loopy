"""The GitHub Actions worker: one Lane contribution, inside one job (#460).

This is the *other half* of :mod:`git_loopy.github_actions_host` — the part
that runs on GitHub's runner rather than on the operator's machine. The host
dispatches a workflow; that workflow runs this module; this module produces the
**completion artifact** the host then reads back.

It is deliberately a thin, self-contained entry point:

* it reads the contribution request from the job's environment, never from a
  credential store — nothing secret crosses the machine boundary;
* it runs exactly one Agent session, writing that session's Events to a JSONL
  file as it goes, so a job killed by the six-hour cap still leaves a partial
  but parseable stream on disk;
* it **Checkpoints** a dirty tree (ADR-0004) — the one commit a host may author
  on its own initiative, carrying the Checkpoint trailer and free of closing
  keywords;
* it writes the session's ending as JSON beside the Events, and the workflow
  turns that plus the pushed branch into ``completion.json``.

It decides nothing. Classification, retry, Strike accounting and Integration
all belong to the orchestrator, which is still supervising from the operator's
machine.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from git_loopy import events as events_module
from git_loopy.git import GitClient, GitError, SubprocessGitClient
from git_loopy.session_outcome import (
    SessionError,
    SessionErrorKind,
    SessionOutcomeRecord,
    SessionTermination,
    SessionOutcomeWatch,
    resolve_session_outcome,
    strongest_error,
)
from git_loopy.wrapper import checkpoint_message

__all__ = [
    "RESULT_FILENAME",
    "EVENTS_FILENAME",
    "REQUEST_ENV",
    "RUNNER_TEMP_ENV",
    "ContributionRequestError",
    "checkpoint_dirty_tree",
    "main",
    "parse_request",
    "write_completion_inputs",
]

#: The dispatched request, handed in as a workflow input rather than a file so
#: the job never has to fetch anything before it can start.
REQUEST_ENV = "REQUEST"

#: The Actions-provided scratch directory the artifact is assembled in.
RUNNER_TEMP_ENV = "RUNNER_TEMP"

#: The three files the job uploads. ``EVENTS_FILENAME`` is the base name the host
#: reads out of the artifact zip, so the two must agree — the name is defined
#: once, here, and imported by the host's reader.
EVENTS_FILENAME = "events.jsonl"
RESULT_FILENAME = "result.json"


class ContributionRequestError(ValueError):
    """The dispatched job did not receive a usable contribution request."""


def parse_request(env: Mapping[str, str]) -> dict[str, Any]:
    """Read and validate the contribution request from the job environment.

    Raises:
        ContributionRequestError: the request is absent, unparseable, or is
            missing a field the session cannot be run without. A job that
            cannot name its own contribution must fail loudly rather than run
            a degenerate session the orchestrator would then have to classify.
    """
    raw = env.get(REQUEST_ENV)
    if raw is None:
        raise ContributionRequestError(f"{REQUEST_ENV} is required")
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContributionRequestError(f"{REQUEST_ENV} must be JSON: {exc}") from exc
    if not isinstance(request, dict):
        raise ContributionRequestError(f"{REQUEST_ENV} must be a JSON object")
    timeout = request.get("send_timeout_seconds")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ContributionRequestError(
            f"{REQUEST_ENV}.send_timeout_seconds must be a positive number"
        )
    for field, kind in (
        ("prompt", str),
        ("run_id", str),
        ("base_revision", str),
    ):
        if not isinstance(request.get(field), kind) or not request[field]:
            raise ContributionRequestError(f"{REQUEST_ENV}.{field} must be a non-empty string")
    if not isinstance(request.get("issue_ref"), (int, str)) or request["issue_ref"] == "":
        raise ContributionRequestError(f"{REQUEST_ENV}.issue_ref must name an issue")
    return request


def checkpoint_dirty_tree(git: GitClient, issue_ref: Any) -> bool:
    """**Checkpoint** whatever the agent left uncommitted (ADR-0004).

    The one commit a host may author on its own initiative, and it carries the
    Checkpoint trailer and no closing keyword — the same message the local
    placement uses, so a remote contribution's residue is indistinguishable
    from a local one's to every reader downstream.

    Returns:
        ``True`` when the tree was clean or was successfully captured, and
        ``False`` when the Checkpoint failed — the job then has no durable
        branch to hand back.
    """
    try:
        if not (git.is_dirty() or git.has_untracked()):
            return True
        git.add_all()
        git.commit(checkpoint_message(issue_ref))
    except GitError:
        return False
    return True


def write_completion_inputs(
    directory: Path, ending: SessionOutcomeRecord, *, checkpoint_failed: bool
) -> Path:
    """Write what the workflow folds into ``completion.json``.

    Two facts, kept apart because they are answerable by different things. The
    *ending* is what the Agent session did, and only this job can observe it.
    ``checkpoint_failed`` is what the **runner** did with the residue, and it is
    a host failure rather than a session one --- ADR-0004's Checkpoint is the
    one commit a host authors on its own initiative, so failing it is the
    host's breach and the orchestrator has to be able to say so without
    blaming the session that ended perfectly well.
    """
    path = directory / RESULT_FILENAME
    path.write_text(
        json.dumps(
            {"ending": ending.as_payload(), "checkpoint_failed": checkpoint_failed},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


class _ArtifactEventLog:
    """Append every Event this contribution produced to the artifact's JSONL.

    Flushed per record on purpose: the six-hour job cap can kill this process
    at any moment, and a partial-but-parseable stream is worth far more to an
    operator than a buffer that never reached disk.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # Created empty, before the session starts, rather than on first write.
        # A contribution that produced no Event at all is a real outcome --- a
        # session that never got going --- and it still has a durable branch
        # and an ending to hand back. If the member were only created lazily,
        # the artifact would arrive missing it, the host would reject the whole
        # archive, and an explicable failure would be reported as a blameless
        # stall instead.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def write(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(events_module.to_jsonl_line(event))
            handle.flush()


@dataclass(frozen=True)
class SessionObservation:
    """Everything a job learns about one session, from both of its witnesses.

    Two things witness an ending and neither sees the other's half: the
    ``await`` boundary sees what was *raised*, and the Event stream sees what
    the harness *reported* --- a quota refused three tool calls ago, a content
    filter, the Agent declaring its issue unworkable. A runner that returned
    only the termination would leave the stream's half unread, and every one
    of those endings would arrive at the orchestrator as a generic silence
    that charges the wrong Strike.
    """

    termination: SessionTermination
    error: SessionError | None = None
    content_filtered: bool = False
    no_more_tasks: bool = False


async def run_contribution_job(
    request: Mapping[str, Any],
    *,
    directory: Path,
    git: GitClient,
    session_runner: Any,
) -> SessionOutcomeRecord:
    """Run one contribution inside the job and resolve its ending.

    ``session_runner`` is the injected "run the Agent session" seam: it is
    handed the request and the Event log and reports how the session
    terminated. Injecting it is what keeps this module testable without a
    Copilot backend — the job's real runner is
    :func:`_run_agent_session`.
    """
    log = _ArtifactEventLog(directory / EVENTS_FILENAME)
    before = _head_sha(git)
    observed = await session_runner(request, log)
    if isinstance(observed, SessionTermination):
        observed = SessionObservation(termination=observed)
    checkpoint_ok = checkpoint_dirty_tree(git, request["issue_ref"])
    after = _head_sha(git)
    # Progress is *the branch moved*, and nothing else. A Checkpoint that fails
    # cannot retract commits the agent already made, so folding the two
    # together would discard a whole contribution's real work because a residue
    # of uncommitted scratch could not be captured beside it.
    progressed = before != after
    checkpoint_error = (
        None
        if checkpoint_ok
        else SessionError(
            kind=SessionErrorKind.UNKNOWN,
            origin="checkpoint",
            error_type="CheckpointFailed",
            message=(
                "the runner could not Checkpoint the worktree the agent left "
                "dirty; any uncommitted work on it is lost with the runner"
            ),
        )
    )
    ending = resolve_session_outcome(
        termination=observed.termination,
        progressed=progressed,
        # The stream's identity and the boundary's are not rivals --- the
        # second is usually a consequence of the first --- so the one that
        # names a condition an operator can act on wins, exactly as a local
        # Iteration resolves it.
        error=strongest_error(observed.error, checkpoint_error),
        content_filtered=observed.content_filtered,
        no_more_tasks=observed.no_more_tasks,
    )
    write_completion_inputs(directory, ending, checkpoint_failed=not checkpoint_ok)
    return ending


def _head_sha(git: GitClient) -> str | None:
    try:
        return git.head_sha()
    except GitError:
        return None


@dataclass(frozen=True)
class _TransportedSkillExposure:
    """The half of a Run's **Effective Skill policy** that can cross a boundary.

    ``IterationSession`` reads four things off an exposure. Two of them ---
    the resolved policy object and the catalog --- are local artefacts of the
    orchestrator's own discovery and mean nothing on a runner. The other two
    are exactly what has to survive the trip:

    * ``skill_directories`` is the catalog root ADR-0025 pins, which the job's
      setup step populates from the same revision this repository pins, so it
      is *derived* here rather than transported.
    * ``disabled_skills`` cannot be re-derived remotely, and it has to reach
      the SDK and not merely the permission gate: denying an invocation still
      leaves the Skill visible in the session's catalog, and a closed world
      that advertises what it will refuse is not closed.
    """

    disabled: tuple[str, ...]
    root: str
    policy: None = None
    catalog: None = None

    @property
    def skill_directories(self) -> tuple[str, ...]:
        return (self.root,)

    @property
    def disabled_skills(self) -> tuple[str, ...]:
        return self.disabled


@dataclass(frozen=True)
class _JobSessionConfig:
    """The :class:`~git_loopy.session.SessionConfig` this job's session reads.

    ``deny_skills`` is where the Run's **Effective Skill policy** lands. The
    orchestrator resolved that policy once, against the pinned catalog, and a
    remote Lane has to honour the same closed world --- otherwise a Skill the
    operator disabled for this Run would be usable on exactly the contributions
    that ran off their machine. The *enabled* half needs no transport: ADR-0025
    already fixes the Skill root to the catalog ``git-loopy init`` installs, and
    the job's setup step installs the very same pinned revision.
    """

    deny_skills: frozenset[str]
    deny_tools: frozenset[str] = frozenset()
    verbosity: int = 0
    render_reasoning: bool = False


async def _run_agent_session(
    request: Mapping[str, Any], log: _ArtifactEventLog
) -> SessionObservation:
    """The job's production session runner (imported lazily, never in tests).

    Runs the contribution through the *same* :class:`IterationSession` a local
    Lane uses rather than a hand-rolled ``create_session``. That is the whole
    point of the reuse: the permission gate, the SDK-event mapping, the
    scrubber, the Skill root and the disabled-Skill gate are then the Run's
    own, not a second implementation that drifts from it --- and the Events
    this job uploads are the same records the orchestrator would have written
    had the contribution run at home.
    """
    from git_loopy.copilot_client import make_copilot_client
    from git_loopy.persist import EventLogWriter
    from git_loopy.session import IterationSession
    from git_loopy.sinks import SinkFanout
    from git_loopy.skill_install import installed_catalog_dir

    working_directory = Path.cwd()
    client = make_copilot_client(working_directory=working_directory)
    disabled = tuple(request.get("disabled_skills") or ())
    config = _JobSessionConfig(deny_skills=frozenset(disabled))
    exposure = _TransportedSkillExposure(
        disabled=disabled, root=str(installed_catalog_dir(os.environ))
    )
    termination = SessionTermination.COMPLETED
    raised: SessionError | None = None
    watch = SessionOutcomeWatch()
    await client.start()
    try:
        async with IterationSession(
            client,
            config=config,
            event_log=EventLogWriter(log.path),
            sinks=SinkFanout(()),
            run_id=request["run_id"],
            iter_num=None,
            model=request.get("model"),
            reasoning_effort=request.get("reasoning_effort"),
            working_directory=str(working_directory),
            issue_ref=request["issue_ref"],
            skill_exposure=exposure,
            event_observer=watch,
        ) as session:
            try:
                # Explicit, because the SDK's own default is 60 seconds --- two
                # orders of magnitude below the six-hour allowance this job was
                # dispatched to spend, so leaving it implicit would terminate
                # nearly every real contribution after one minute.
                await session.send_and_wait(
                    request["prompt"], timeout=request["send_timeout_seconds"]
                )
            except (TimeoutError, asyncio.TimeoutError):
                termination = SessionTermination.TIMED_OUT
            except Exception as exc:
                termination = SessionTermination.CRASHED
                raised = SessionError.from_exception(exc)
    except Exception as exc:
        termination = SessionTermination.CRASHED
        raised = SessionError.from_exception(exc)
    finally:
        await client.stop()
    return SessionObservation(
        termination=termination,
        error=strongest_error(watch.error, raised),
        content_filtered=watch.content_filtered,
        no_more_tasks=watch.no_more_tasks,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point the dispatched workflow invokes."""
    del argv
    request = parse_request(os.environ)
    runner_temp = os.environ.get(RUNNER_TEMP_ENV)
    if not runner_temp:
        raise ContributionRequestError(f"{RUNNER_TEMP_ENV} is required")
    asyncio.run(
        run_contribution_job(
            request,
            directory=Path(runner_temp),
            git=SubprocessGitClient.discover(),
            session_runner=_run_agent_session,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
