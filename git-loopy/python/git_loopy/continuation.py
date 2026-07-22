"""Native Continuation command framing for the Python distribution."""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from typing import Any, TextIO

from git_loopy.gh import (
    ContinuationComment,
    ContinuationGitHubClient,
    GhError,
    SubprocessContinuationGitHubClient,
)

CONTINUATION_CONTRACT_VERSION = "1.0"
RECORD_FORMAT = 1
WRAPPER_CONTRACT_VERSION = "1.2"
EVENT_SCHEMA_VERSION = "1.1"

CAPABILITY_MANIFEST: dict[str, Any] = {
    "continuation_contract_versions": [CONTINUATION_CONTRACT_VERSION],
    "record_formats": [RECORD_FORMAT],
    "wrapper_contract_version": WRAPPER_CONTRACT_VERSION,
    "event_schema_version": EVENT_SCHEMA_VERSION,
    "tracker_adapters": {"github": {"operations": ["publish", "reconcile"]}},
    "operations": {
        "capabilities": True,
        "publish": True,
        "reconcile": True,
        "record-dispatch-result": False,
        "repair-index": False,
    },
    "instruction_handlers": [],
    "instruction_modes": [],
    "evaluators": [],
    "effect_scopes": [],
    "optional_capabilities": {
        "terminal_rendering": False,
        "concurrent_dispatch": False,
    },
    "continuation_modes": {
        "default": "off",
        "off": True,
        "report": False,
        "execute-frontier": False,
    },
}

_INDEX_LABEL = "git-loopy-continuation"
_RECORD_MARKER = "<!-- git-loopy-continuation:1 -->"


class ContinuationError(ValueError):
    """A typed semantic rejection at the Continuation boundary."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _emit_json(value: dict[str, Any], stream: TextIO) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def _request_bytes(input_path: str | None, stdin: TextIO) -> bytes:
    if input_path is not None:
        return Path(input_path).read_bytes()
    binary = getattr(stdin, "buffer", None)
    if binary is not None:
        return binary.read()
    return stdin.read().encode("utf-8")


def _read_request(input_path: str | None, stdin: TextIO) -> dict[str, Any]:
    try:
        raw = _request_bytes(input_path, stdin)
    except OSError as exc:
        raise ValueError(f"could not read request: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request must be one UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("request must be one UTF-8 JSON object")
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContinuationError(f"{name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContinuationError(f"{name} must be a non-empty string")
    return value


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ContinuationError(f"{name} must be a positive integer")
    return value


def _repository(request: dict[str, Any]) -> str:
    repository = _string(request.get("repository"), "repository")
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ContinuationError("repository must use owner/name form")
    return repository


def _trusted_producers(request: dict[str, Any]) -> frozenset[str]:
    raw = request.get("trusted_producers")
    if not isinstance(raw, list) or not raw:
        raise ContinuationError("trusted_producers must be a non-empty array")
    producers = [_string(item, "trusted_producers item") for item in raw]
    if len(set(producers)) != len(producers):
        raise ContinuationError("trusted_producers must not contain duplicates")
    return frozenset(producers)


def _issue_locator(
    value: Any,
    name: str,
    repository: str,
) -> dict[str, Any]:
    locator = _object(value, name)
    if locator.get("kind") != "issue":
        raise ContinuationError(f"{name}.kind must be issue")
    if locator.get("repository") != repository:
        raise ContinuationError(f"{name}.repository must match repository")
    _positive_int(locator.get("number"), f"{name}.number")
    return locator


def _validate_action(
    value: Any,
    *,
    repository: str,
) -> dict[str, Any]:
    action = _object(value, "completion.actions item")
    for field in ("key", "summary", "kind", "occurrence"):
        _string(action.get(field), f"completion.actions item.{field}")
    instruction = _object(
        action.get("instruction"),
        "completion.actions item.instruction",
    )
    if instruction.get("mode") not in {"skill", "command", "manual"}:
        raise ContinuationError(
            "completion.actions item.instruction.mode is unsupported"
        )
    _string(
        instruction.get("value"),
        "completion.actions item.instruction.value",
    )
    _issue_locator(
        action.get("target"),
        "completion.actions item.target",
        repository,
    )
    basis = action.get("basis")
    if not isinstance(basis, list) or not basis:
        raise ContinuationError("completion.actions item.basis must be non-empty")
    for item in basis:
        _issue_locator(item, "completion.actions item.basis item", repository)
    prerequisites = action.get("prerequisites")
    if prerequisites != []:
        raise ContinuationError(
            "the Python tracer bullet supports only an empty Prerequisite set"
        )
    interaction = _object(
        action.get("interaction"),
        "completion.actions item.interaction",
    )
    _string(
        interaction.get("classification"),
        "completion.actions item.interaction.classification",
    )
    _object(
        interaction.get("evidence"),
        "completion.actions item.interaction.evidence",
    )
    completion = _object(
        action.get("completion_condition"),
        "completion.actions item.completion_condition",
    )
    if completion.get("kind") != "issue-closed":
        raise ContinuationError(
            "completion.actions item.completion_condition.kind is unsupported"
        )
    _issue_locator(
        completion.get("target"),
        "completion.actions item.completion_condition.target",
        repository,
    )
    return action


def _validate_completion(
    request: dict[str, Any],
) -> tuple[str, frozenset[str], dict[str, Any]]:
    repository = _repository(request)
    trusted = _trusted_producers(request)
    completion = _object(request.get("completion"), "completion")
    if completion.get("continuation_contract_version") != CONTINUATION_CONTRACT_VERSION:
        raise ContinuationError("unsupported Continuation contract version")
    if completion.get("record_format") != RECORD_FORMAT:
        raise ContinuationError("unsupported Continuation record format")
    if completion.get("disposition") != "continue":
        raise ContinuationError(
            "the Python tracer bullet supports only continue completion"
        )
    workstream = _object(completion.get("workstream"), "completion.workstream")
    _issue_locator(
        workstream.get("anchor"),
        "completion.workstream.anchor",
        repository,
    )
    destination = _object(
        workstream.get("destination"),
        "completion.workstream.destination",
    )
    if destination.get("kind") != "issue-closed":
        raise ContinuationError("completion.workstream.destination.kind is unsupported")
    _issue_locator(
        destination.get("target"),
        "completion.workstream.destination.target",
        repository,
    )
    transition = _object(completion.get("transition"), "completion.transition")
    _string(transition.get("owner"), "completion.transition.owner")
    evidence = transition.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ContinuationError("completion.transition.evidence must be non-empty")
    for item in evidence:
        reference = _object(item, "completion.transition.evidence item")
        if reference.get("kind") != "issue-comment":
            raise ContinuationError(
                "completion.transition.evidence item.kind is unsupported"
            )
        if reference.get("repository") != repository:
            raise ContinuationError(
                "completion.transition.evidence item.repository must match repository"
            )
        _positive_int(
            reference.get("issue"),
            "completion.transition.evidence item.issue",
        )
        _positive_int(
            reference.get("comment_id"),
            "completion.transition.evidence item.comment_id",
        )
    producer = _object(completion.get("producer"), "completion.producer")
    login = _string(producer.get("login"), "completion.producer.login")
    _string(producer.get("role"), "completion.producer.role")
    if login not in trusted:
        raise ContinuationError("completion producer is not trusted")
    _issue_locator(
        completion.get("carrier"),
        "completion.carrier",
        repository,
    )
    actions = completion.get("actions")
    if not isinstance(actions, list) or len(actions) != 1:
        raise ContinuationError(
            "the Python tracer bullet requires exactly one Continuation action"
        )
    _validate_action(actions[0], repository=repository)
    return repository, trusted, completion


def _record_body(completion: dict[str, Any]) -> tuple[str, str]:
    revision_id = hashlib.sha256(
        _canonical_json(completion).encode("utf-8")
    ).hexdigest()
    record = {"revision_id": revision_id, **completion}
    body = f"{_RECORD_MARKER}\n```json\n{_canonical_json(record)}\n```"
    return revision_id, body


def _parse_record(comment: ContinuationComment) -> dict[str, Any] | None:
    prefix = f"{_RECORD_MARKER}\n```json\n"
    suffix = "\n```"
    if not comment.body.startswith(prefix) or not comment.body.endswith(suffix):
        return None
    raw = comment.body[len(prefix) : -len(suffix)]
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContinuationError(
            f"Producer revision comment {comment.id} contains invalid JSON"
        ) from exc
    if not isinstance(record, dict):
        raise ContinuationError(
            f"Producer revision comment {comment.id} must contain one JSON object"
        )
    revision_id = _string(record.get("revision_id"), "revision_id")
    completion = {key: value for key, value in record.items() if key != "revision_id"}
    expected_id = hashlib.sha256(
        _canonical_json(completion).encode("utf-8")
    ).hexdigest()
    if revision_id != expected_id:
        raise ContinuationError(
            f"Producer revision comment {comment.id} has an invalid revision identity"
        )
    return record


def _action_identity(record: dict[str, Any], action: dict[str, Any]) -> str:
    source = {
        "anchor": record["workstream"]["anchor"],
        "kind": action["kind"],
        "target": action["target"],
        "occurrence": action["occurrence"],
    }
    return hashlib.sha256(_canonical_json(source).encode("utf-8")).hexdigest()


def _publish(
    request: dict[str, Any],
    github: ContinuationGitHubClient,
) -> dict[str, Any]:
    repository, _trusted, completion = _validate_completion(request)
    carrier = completion["carrier"]
    carrier_number = int(carrier["number"])
    producer = completion["producer"]
    revision_id, body = _record_body(completion)
    for evidence in completion["transition"]["evidence"]:
        github.read_issue_comment(repository, int(evidence["comment_id"]))
    github.ensure_issue_label(repository, carrier_number, _INDEX_LABEL)
    appended = github.append_issue_comment(repository, carrier_number, body)
    if appended.author != producer["login"]:
        raise ContinuationError(
            "authenticated comment author does not match completion producer"
        )
    committed = github.read_issue_comment(repository, appended.id)
    if committed.body != body or committed.author != producer["login"]:
        raise ContinuationError("Producer revision reread did not match the append")
    return {
        "ok": True,
        "operation": "publish",
        "receipt": {
            "status": "committed",
            "revision_id": revision_id,
            "carrier": carrier,
            "comment": {"id": committed.id, "url": committed.url},
            "index_label": _INDEX_LABEL,
        },
    }


def _reconcile(
    request: dict[str, Any],
    github: ContinuationGitHubClient,
) -> dict[str, Any]:
    repository = _repository(request)
    trusted = _trusted_producers(request)
    carriers = github.list_continuation_carriers(repository, _INDEX_LABEL)
    actions: list[dict[str, Any]] = []
    revision_count = 0
    for carrier in carriers:
        for comment in carrier.comments:
            if comment.author not in trusted:
                continue
            record = _parse_record(comment)
            if record is None:
                continue
            producer = _object(record.get("producer"), "producer")
            if producer.get("login") != comment.author:
                continue
            completion_request = {
                "repository": repository,
                "trusted_producers": sorted(trusted),
                "completion": {
                    key: value for key, value in record.items() if key != "revision_id"
                },
            }
            _validate_completion(completion_request)
            revision_count += 1
            for action in record["actions"]:
                target = github.read_issue(
                    repository,
                    int(action["target"]["number"]),
                )
                if target.state != "OPEN":
                    continue
                actions.append(
                    {
                        "identity": _action_identity(record, action),
                        "workstream_anchor": record["workstream"]["anchor"],
                        "summary": action["summary"],
                        "kind": action["kind"],
                        "readiness": "Ready",
                        "instruction": action["instruction"],
                        "target": action["target"],
                        "basis": action["basis"],
                        "producer": {
                            **producer,
                            "carrier": record["carrier"],
                            "revision_id": record["revision_id"],
                            "comment_id": comment.id,
                            "comment_url": comment.url,
                        },
                        "prerequisites": action["prerequisites"],
                        "interaction": action["interaction"],
                        "completion_condition": action["completion_condition"],
                    }
                )
    actions.sort(key=lambda action: action["identity"])
    return {
        "ok": True,
        "operation": "reconcile",
        "result": {
            "status": "guidance" if actions else "waiting",
            "observed": {
                "repository": repository,
                "indexed_carriers": len(carriers),
                "producer_revisions": revision_count,
            },
            "actions": actions,
            "diagnostics": [],
        },
    }


def _make_github_client() -> ContinuationGitHubClient:
    return SubprocessContinuationGitHubClient()


def run_command(
    operation: str,
    *,
    input_path: str | None = None,
    terminal: bool = False,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one public Continuation operation without entering the Run loop."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    if operation == "capabilities":
        _emit_json({"ok": True, "capabilities": CAPABILITY_MANIFEST}, stdout)
        return 0

    if terminal:
        message = "terminal rendering is not supported by this distribution"
        _emit_json(
            {
                "ok": False,
                "operation": operation,
                "error": {"code": "unsupported_operation", "message": message},
            },
            stdout,
        )
        print(f"git-loopy continuation: {message}", file=stderr)
        return 1

    try:
        request = _read_request(input_path, stdin)
        if operation == "publish":
            result = _publish(request, _make_github_client())
        elif operation == "reconcile":
            result = _reconcile(request, _make_github_client())
        else:
            result = None
    except (ValueError, ContinuationError) as exc:
        message = str(exc)
        _emit_json(
            {
                "ok": False,
                "operation": operation,
                "error": {"code": "invalid_request", "message": message},
            },
            stdout,
        )
        print(f"git-loopy continuation: {message}", file=stderr)
        return 1
    except GhError as exc:
        message = exc.stderr_tail
        _emit_json(
            {
                "ok": False,
                "operation": operation,
                "error": {"code": "github_error", "message": message},
            },
            stdout,
        )
        print(f"git-loopy continuation: GitHub operation failed: {message}", file=stderr)
        return 1

    if result is not None:
        _emit_json(result, stdout)
        return 0

    message = f"{operation} is not supported by this distribution"
    _emit_json(
        {
            "ok": False,
            "operation": operation,
            "error": {"code": "unsupported_operation", "message": message},
        },
        stdout,
    )
    print(f"git-loopy continuation: {message}", file=stderr)
    return 1
