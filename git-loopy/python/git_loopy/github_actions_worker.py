"""Actions-job entry point for one dispatched Lane contribution (#460)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping

from copilot.generated.session_events import SessionEvent

from git_loopy.copilot_client import make_copilot_client
from git_loopy.events import make_event, map_sdk_event, to_jsonl_line
from git_loopy.git import SubprocessGitClient
from git_loopy.session import build_permission_handler
from git_loopy.session_outcome import (
    SessionTermination,
    resolve_session_outcome,
)
from git_loopy.skill_catalog import SkillCatalogError, build_skill_catalog
from git_loopy.skill_exposure import SkillExposure, SkillExposureError, build_skill_exposure
from git_loopy.skill_install import installed_catalog_dir
from git_loopy.skill_policy import (
    SKILL_SOURCE_KINDS,
    EffectiveSkillPolicy,
    SkillPolicyFallback,
    SkillPolicyScope,
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


def _skill_exposure(
    request: Mapping[str, Any], directory: Path
) -> SkillExposure:
    raw = request.get("skill_policy")
    if not isinstance(raw, Mapping):
        raise WorkerRequestError("request.skill_policy must be an object")
    required_fields = {
        "enabled",
        "required",
        "legacy_denied",
        "source_kinds",
        "base_scope",
        "fallback",
    }
    if set(raw) != required_fields:
        raise WorkerRequestError("request.skill_policy has unexpected fields")

    def names(field: str) -> tuple[str, ...]:
        value = raw[field]
        if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
            raise WorkerRequestError(f"request.skill_policy.{field} must be a string list")
        return tuple(value)

    enabled = names("enabled")
    required = names("required")
    legacy_denied = names("legacy_denied")
    source_kinds = raw["source_kinds"]
    if not isinstance(source_kinds, Mapping) or any(
        not isinstance(name, str) or not isinstance(kind, str)
        for name, kind in source_kinds.items()
    ):
        raise WorkerRequestError("request.skill_policy.source_kinds must be a string map")
    if set(source_kinds) != set(enabled):
        raise WorkerRequestError("request.skill_policy.source_kinds must name enabled Skills")
    if not set(required).issubset(enabled):
        raise WorkerRequestError("request.skill_policy.required must be enabled")
    if any(kind not in SKILL_SOURCE_KINDS for kind in source_kinds.values()):
        raise WorkerRequestError("request.skill_policy.source_kinds contains an unknown kind")
    if any(kind != "packaged" for kind in source_kinds.values()):
        raise WorkerRequestError(
            "Actions contributions support only installed-catalog Skills"
        )
    try:
        base_scope = SkillPolicyScope(raw["base_scope"])
        fallback_value = raw["fallback"]
        fallback = (
            None if fallback_value is None else SkillPolicyFallback(fallback_value)
        )
    except (TypeError, ValueError) as exc:
        raise WorkerRequestError("request.skill_policy contains an invalid enum") from exc
    policy = EffectiveSkillPolicy(
        enabled=enabled,
        required=required,
        legacy_denied=legacy_denied,
        source_kinds=dict(source_kinds),
        base_scope=base_scope,
        fallback=fallback,
    )
    try:
        catalog = build_skill_catalog(
            (),
            repo_root=Path.cwd(),
            installed_skills_dir=installed_catalog_dir(os.environ),
        )
        return build_skill_exposure(policy, catalog, directory=directory)
    except (SkillCatalogError, SkillExposureError, KeyError) as exc:
        raise WorkerRequestError(f"cannot build Actions Skill exposure: {exc}") from exc


async def _run(request: dict[str, Any], events_path: Path) -> dict[str, Any]:
    client = make_copilot_client(working_directory=Path.cwd())
    git = SubprocessGitClient.discover()
    before = git.head_sha()
    records: list[str] = []
    exposure = _skill_exposure(request, events_path.parent / "skill-exposure")

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

    def record_permission_event(envelope: dict[str, Any]) -> None:
        records.append(to_jsonl_line(envelope))

    approve = build_permission_handler(
        skill_policy=exposure.policy,
        skill_catalog=exposure.catalog,
        record_event=record_permission_event,
        run_id=request["run_id"],
        iter_provider=lambda: None,
    )

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
            skill_directories=list(exposure.skill_directories),
            disabled_skills=list(exposure.disabled_skills),
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
    ending = asyncio.run(_run(request, Path(runner_temp) / "events.jsonl"))
    (Path(runner_temp) / "ending.json").write_text(
        json.dumps(ending), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
