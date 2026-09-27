"""``git-loopy stop <run-id>`` — ask one live Run to wind down (#585, ADR-0058).

The public command over the request machinery any attached client already
uses (#461). It does not own a second lifecycle. It names one Run through
the shared resolver, writes one Stop request beside that Run's control
artifact, and returns success only when the Run itself has announced the
stage that request asks for. A write is not success. A finished drain is
not what it waits for. A timeout is unconfirmed.

One invocation without ``--request-id`` is one deliberate Stop. The first
distinct request asks for drain; a later one asks for cancellation; a
further one adds no harder stage. Passing the id a previous invocation
printed redelivers that one request and cannot escalate. A write that has
to be retried reuses the identity this invocation already chose, for the
same reason.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable
from pathlib import Path

from git_loopy.git import GitError, SubprocessGitClient
from git_loopy.run_discovery import (
    RunLiveness,
    RunTargetError,
    discover_runs,
    resolve_run,
)
from git_loopy.stop_request import (
    StopRequestError,
    StopSubmission,
    await_stop_acknowledgment,
    submit_stop,
)

__all__ = ["DEFAULT_STOP_ACK_TIMEOUT", "run_stop"]

#: How long the command waits for the Run to announce the asked stage.
#: The Run looks for a request many times a second; this bound is for a
#: Run that never announces, not for a drain to finish.
DEFAULT_STOP_ACK_TIMEOUT = 10.0

#: How many times one invocation retries a write before reporting that the
#: request was not submitted. Every attempt uses the same identity.
_SUBMIT_ATTEMPTS = 3


def run_stop(
    *,
    repo_root: Path,
    run_id: str,
    request_id: str | None = None,
    timeout: float = DEFAULT_STOP_ACK_TIMEOUT,
    output: Callable[[str], None] = print,
) -> int:
    """Request a Stop of one explicit Run and wait for the Run to acknowledge it.

    Args:
        repo_root: Any worktree of the clone whose Runs may be named.
        run_id: The Run identity, or an unambiguous leading part of one.
        request_id: The identity of one logical request. Omit it to start a
            new Stop. Pass the id a previous invocation printed to redeliver
            that request rather than escalate.
        timeout: Seconds to wait for the Run's announcement. Elapsing it is
            unconfirmed, not success.
        output: Where the acknowledgment goes. Refusals and an unconfirmed
            wait go to stderr, because they are not the success result.

    Returns:
        ``0`` only after the Run has announced the requested stage, or a
        stronger one. ``1`` for a refusal, a request that could not be
        submitted, or a wait that elapsed.
    """
    if timeout < 0:
        print(
            "git-loopy: acknowledgment timeout cannot be negative",
            file=sys.stderr,
        )
        return 1
    try:
        run = resolve_run(
            run_id,
            discover_runs(git=SubprocessGitClient(repo_root)),
            require_proof=True,
        )
    except GitError as exc:
        print(
            f"git-loopy: could not enumerate this clone's worktrees: {exc}",
            file=sys.stderr,
        )
        return 1
    except RunTargetError as exc:
        print(f"git-loopy: {exc}", file=sys.stderr)
        return 1
    if run.liveness is not RunLiveness.LIVE:
        print(
            f"git-loopy: Run {run.run_id} has ended, so there is nothing to stop. "
            "A Stop asks a live Run to wind down; it does not claim a finished "
            "Run stopped.",
            file=sys.stderr,
        )
        return 1

    identity = request_id if request_id is not None else _new_request_id()
    try:
        submission = _submit_preserving_identity(run.control_path, identity)
    except StopRequestError as exc:
        print(f"git-loopy: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"git-loopy: could not submit Stop request {identity} for Run "
            f"{run.run_id}: {exc}. The request was not acknowledged.",
            file=sys.stderr,
        )
        return 1

    output(_submitted(run.run_id, submission))
    acknowledgment = await_stop_acknowledgment(
        run.trace_path, stage=submission.stage, timeout=timeout
    )
    if acknowledgment.status != "acknowledged":
        print(
            f"git-loopy: Run {run.run_id} has not acknowledged {submission.stage} "
            f"within {timeout:g}s (request {submission.request_id}). The request "
            "is unconfirmed. This is not success and not a claim that the Run "
            "stopped or finished.",
            file=sys.stderr,
        )
        return 1
    output(_acknowledged(run.run_id, submission, announced=acknowledgment.stage))
    return 0


def _new_request_id() -> str:
    """A new logical Stop. A different id is a deliberate further request."""
    return "cli-" + secrets.token_hex(16)


def _submit_preserving_identity(control_path: Path, request_id: str) -> StopSubmission:
    """Submit ``request_id``, retrying a failed write as that same request.

    A retry must not allocate a new stage. ``StopRequestError`` is the
    operator's identity being refused, and retrying it cannot succeed.
    """
    last: OSError | None = None
    for _attempt in range(_SUBMIT_ATTEMPTS):
        try:
            return submit_stop(control_path, request_id)
        except OSError as exc:
            last = exc
    assert last is not None
    raise last


def _submitted(run_id: str, submission: StopSubmission) -> str:
    if submission.redelivered:
        return (
            f"Redelivered Stop request {submission.request_id} for Run {run_id}; "
            f"still asking for {submission.stage}, not a further Stop."
        )
    return (
        f"Submitted Stop request {submission.request_id} for Run {run_id}; "
        f"asking for {submission.stage}."
    )


def _acknowledged(
    run_id: str, submission: StopSubmission, *, announced: str | None
) -> str:
    stage = announced or submission.stage
    if submission.stage == "cancel":
        follow = (
            "Cancellation was requested, not awaited. Workers are not resumed, "
            "and a further Stop adds no harder stage."
        )
    else:
        follow = (
            "Started work may still be draining. A further `git-loopy stop` "
            "is a deliberate second Stop and asks for cancellation; it does "
            "not resume workers."
        )
    return (
        f"Run {run_id} acknowledged {stage} (request {submission.request_id}). "
        f"That is the latch, not a finished Run. {follow}"
    )
