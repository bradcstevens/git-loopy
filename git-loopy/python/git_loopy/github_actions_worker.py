"""Actions-job entry point for one dispatched Lane contribution (#460)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from copilot.generated.rpc import PermissionDecisionApproveOnce
from copilot.generated.session_events import SessionEvent

from git_loopy.copilot_client import make_copilot_client
from git_loopy.events import make_event, map_sdk_event, to_jsonl_line
from git_loopy.git import SubprocessGitClient
from git_loopy.session_outcome import (
    SessionTermination,
    resolve_session_outcome,
)

_REQUEST_ENV = "REQUEST"
_EVENTS_PATH_ENV = "RUNNER_TEMP"
_SEND_TIMEOUT_SECONDS = 6 * 60 * 60


class WorkerRequestError(ValueError):
    """The dispatched workflow did not receive a valid contribution request."""


def _request() -> dict[str, Any]:
    raw = os.environ.get(_REQUEST_ENV)
    if raw is None:
        raise WorkerRequestError(f"{_REQUEST_ENV} is required")
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkerRequestError(f"{_REQUEST_ENV} must be JSON") from exc
    if not isinstance(request, dict):
        raise WorkerRequestError(f"{_REQUEST_ENV} must be a JSON object")
    if not isinstance(request.get("prompt"), str):
        raise WorkerRequestError("request.prompt must be a string")
    if not isinstance(request.get("run_id"), str):
        raise WorkerRequestError("request.run_id must be a string")
    return request


async def _run(request: dict[str, Any], events_path: Path) -> dict[str, Any]:
    client = make_copilot_client(working_directory=Path.cwd())
    git = SubprocessGitClient.discover()
    before = git.head_sha()
    records: list[str] = []

    def on_event(event: SessionEvent) -> None:
        payload = map_sdk_event(event)
        if payload is None:
            return
        records.append(
            to_jsonl_line(
                make_event(
                    payload.pop("type"),
                    run_id=request["run_id"],
                    iter=None,
                    ts=event.timestamp,
                    **payload,
                )
            )
        )

    def approve(*_args: object, **_kwargs: object) -> PermissionDecisionApproveOnce:
        return PermissionDecisionApproveOnce()

    termination = SessionTermination.COMPLETED
    try:
        await client.start()
        session = await client.create_session(
            on_permission_request=approve,
            on_event=on_event,
            model=request.get("model"),
            reasoning_effort=request.get("reasoning_effort"),
            working_directory=str(Path.cwd()),
            enable_skills=True,
        )
        try:
            await session.send_and_wait(
                request["prompt"], timeout=_SEND_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            termination = SessionTermination.TIMED_OUT
            del exc
        except Exception as exc:
            termination = SessionTermination.CRASHED
            del exc
        finally:
            await session.disconnect()
    finally:
        await client.stop()

    if git.is_dirty() or git.has_untracked():
        git.add_all()
        git.commit(
            f"Checkpoint: capture Actions contribution for issue {request['issue_ref']}"
        )
    after = git.head_sha()
    events_path.write_text("".join(records), encoding="utf-8")
    return resolve_session_outcome(
        termination=termination,
        progressed=before != after,
    ).as_payload()


def main() -> int:
    request = _request()
    runner_temp = os.environ.get(_EVENTS_PATH_ENV)
    if runner_temp is None:
        raise WorkerRequestError(f"{_EVENTS_PATH_ENV} is required")
    ending = asyncio.run(_run(request, Path(runner_temp) / "git-loopy-events.jsonl"))
    (Path(runner_temp) / "git-loopy-ending.json").write_text(
        json.dumps(ending), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
