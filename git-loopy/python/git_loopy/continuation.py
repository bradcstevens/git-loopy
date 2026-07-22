"""Native Continuation command framing for the Python distribution."""

from __future__ import annotations

import json
import hashlib
import re
import sys
import unicodedata
from datetime import datetime, timezone
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
_MAX_INTEGER = (1 << 53) - 1
_MAX_DEPTH = 16
_MAX_ARRAY_LENGTH = 256
_MAX_STRING_BYTES = 8 * 1024
_MAX_RECORD_BYTES = 48 * 1024
_MAX_CARRIER_BODY_BYTES = 64 * 1024
CANONICAL_JSON_PROFILE: dict[str, Any] = {
    "encoding": "UTF-8",
    "bom": False,
    "normalization": "NFC",
    "duplicate_keys": False,
    "floats": False,
    "integer_min": -_MAX_INTEGER,
    "integer_max": _MAX_INTEGER,
    "maximum_depth": _MAX_DEPTH,
    "maximum_array_length": _MAX_ARRAY_LENGTH,
    "maximum_string_bytes": _MAX_STRING_BYTES,
    "maximum_record_bytes": _MAX_RECORD_BYTES,
    "digest": "SHA-256",
}

PUBLICATIONS = frozenset({"ephemeral", "shared"})
DISPOSITIONS = frozenset({"continue", "no-guidance", "terminal"})
INTERACTION_CLASSIFICATIONS = frozenset({"AFK-safe", "HITL-required"})
HUMAN_BOUNDARY_REASONS = frozenset(
    {
        "consent-required",
        "credential-required",
        "human-decision",
        "physical-interaction",
        "privilege-expansion",
        "scope-ambiguity",
        "subjective-validation",
    }
)
_ANY_INTERACTION = INTERACTION_CLASSIFICATIONS
_HITL_ONLY = frozenset({"HITL-required"})
ACTION_KIND_SCHEMAS: dict[str, frozenset[str]] = {
    "Address review findings": _ANY_INTERACTION,
    "Authorize operation": _HITL_ONLY,
    "Chart workstream": _HITL_ONLY,
    "Close parent": _ANY_INTERACTION,
    "Decompose spec": _ANY_INTERACTION,
    "Implement ticket": _ANY_INTERACTION,
    "Perform manual validation": _HITL_ONLY,
    "Prototype evidence": _ANY_INTERACTION,
    "Provide information": _HITL_ONLY,
    "Publish head": _ANY_INTERACTION,
    "Publish spec": _ANY_INTERACTION,
    "Research fact": _ANY_INTERACTION,
    "Resolve conflict": _ANY_INTERACTION,
    "Resolve decision": _HITL_ONLY,
    "Review and merge PR": _HITL_ONLY,
    "Review head": _ANY_INTERACTION,
    "Triage item": _ANY_INTERACTION,
}
ACTION_KINDS = frozenset(ACTION_KIND_SCHEMAS)
INTERACTION_EVIDENCE_SCHEMAS: dict[str, dict[str, Any]] = {
    "human-boundary": {
        "classifications": _HITL_ONLY,
        "required_fields": frozenset({"kind", "reason", "resolution_condition"}),
        "optional_fields": frozenset({"advisory_extensions"}),
        "string_fields": frozenset(),
        "condition_fields": frozenset({"resolution_condition"}),
        "enum_fields": {"reason": HUMAN_BOUNDARY_REASONS},
    },
    "transition-owner-attestation": {
        "classifications": frozenset({"AFK-safe"}),
        "required_fields": frozenset({"kind", "owner"}),
        "optional_fields": frozenset({"advisory_extensions"}),
        "string_fields": frozenset({"owner"}),
        "condition_fields": frozenset(),
        "enum_fields": {},
    },
}
OUTCOME_KINDS = frozenset({"complete", "rejected", "abandoned", "superseded"})
NO_GUIDANCE_REASONS = frozenset({"no-successor-created", "ephemeral-only"})
_REFERENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "issue": ("repository", "number"),
    "pull-request": ("repository", "number"),
    "issue-comment": ("repository", "issue", "comment_id"),
    "pull-request-review": ("repository", "pull_request", "review_id"),
    "commit": ("repository", "sha"),
    "branch": ("repository", "name", "sha"),
}
_CONDITION_OPTIONAL_FIELDS = frozenset({"advisory_extensions"})
_TARGET_CONDITION_FIELDS = frozenset({"kind", "target"})
CONDITION_SCHEMAS: dict[str, dict[str, Any]] = {
    "action-completed": {
        "required_fields": frozenset({"kind", "action_key"}),
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset({"action_key"}),
        "local_reference_field": "action_key",
        "target_kinds": frozenset(),
        "enum_fields": {},
    },
    "artifact-exists": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset(_REFERENCE_FIELDS),
        "enum_fields": {},
    },
    "branch-head-equals": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"branch"}),
        "enum_fields": {},
    },
    "commit-exists": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"commit"}),
        "enum_fields": {},
    },
    "dependency-satisfied": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"issue"}),
        "enum_fields": {},
    },
    "issue-closed": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"issue"}),
        "enum_fields": {},
    },
    "issue-label-present": {
        "required_fields": frozenset({"kind", "target", "label"}),
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset({"label"}),
        "local_reference_field": None,
        "target_kinds": frozenset({"issue"}),
        "enum_fields": {},
    },
    "issue-open": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"issue"}),
        "enum_fields": {},
    },
    "pull-request-closed": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"pull-request"}),
        "enum_fields": {},
    },
    "pull-request-merged": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"pull-request"}),
        "enum_fields": {},
    },
    "pull-request-open": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"pull-request"}),
        "enum_fields": {},
    },
    "pull-request-review-state": {
        "required_fields": frozenset({"kind", "target", "state"}),
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"pull-request-review"}),
        "enum_fields": {
            "state": frozenset({"approved", "changes-requested", "commented"})
        },
    },
    "sub-issues-complete": {
        "required_fields": _TARGET_CONDITION_FIELDS,
        "optional_fields": _CONDITION_OPTIONAL_FIELDS,
        "string_fields": frozenset(),
        "local_reference_field": None,
        "target_kinds": frozenset({"issue"}),
        "enum_fields": {},
    },
}
CONDITION_KINDS = frozenset(CONDITION_SCHEMAS)
_EFFECT_KINDS = frozenset(
    {
        "external-write",
        "git-read",
        "git-write",
        "network-read",
        "repository-read",
        "repository-write",
        "tracker-read",
        "tracker-write",
    }
)
_REQUIREMENT_KINDS = frozenset(
    {"access", "capability", "command", "evaluator", "policy", "skill"}
)
_TRIGGER_KINDS = HUMAN_BOUNDARY_REASONS
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ContinuationError(ValueError):
    """A typed semantic rejection at the Continuation boundary."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContinuationError(f"request contains duplicate object key: {key}")
        result[key] = value
    return result


def _reject_float(_value: str) -> Any:
    raise ContinuationError("request must not contain floating-point values")


def _check_json_nesting(value: str, *, name: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_DEPTH:
                raise ContinuationError(
                    f"{name} exceeds maximum nesting depth {_MAX_DEPTH}"
                )
        elif character in "]}":
            depth = max(0, depth - 1)


def _portable_json(value: Any, *, name: str, depth: int = 1) -> None:
    if depth > _MAX_DEPTH:
        raise ContinuationError(f"{name} exceeds maximum nesting depth {_MAX_DEPTH}")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value < -_MAX_INTEGER or value > _MAX_INTEGER:
            raise ContinuationError(
                f"{name} integer exceeds interoperable signed 53-bit range"
            )
        return
    if isinstance(value, float):
        raise ContinuationError(f"{name} must not contain floating-point values")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ContinuationError(f"{name} strings must be NFC-normalized")
        if len(value.encode("utf-8")) > _MAX_STRING_BYTES:
            raise ContinuationError(
                f"{name} string exceeds maximum UTF-8 length {_MAX_STRING_BYTES}"
            )
        return
    if isinstance(value, list):
        if len(value) > _MAX_ARRAY_LENGTH:
            raise ContinuationError(
                f"{name} array exceeds maximum length {_MAX_ARRAY_LENGTH}"
            )
        for item in value:
            _portable_json(item, name=name, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _portable_json(key, name=name, depth=depth + 1)
            _portable_json(item, name=name, depth=depth + 1)
        return
    raise ContinuationError(f"{name} contains an unsupported JSON value")


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
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ContinuationError("request must be UTF-8 without a BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("request must be one UTF-8 JSON object") from exc
    _check_json_nesting(text, name="request")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("request must be one UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("request must be one UTF-8 JSON object")
    _portable_json(value, name="request")
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContinuationError(f"{name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuationError(f"{name} must be a non-empty string")
    return value


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ContinuationError(f"{name} must be a positive integer")
    return value


def _fields(
    value: dict[str, Any],
    name: str,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - value.keys())
    if missing:
        raise ContinuationError(f"{name} is missing required field: {missing[0]}")
    unknown = sorted(value.keys() - required - optional)
    if unknown:
        raise ContinuationError(f"{name} contains unknown field: {unknown[0]}")
    if "advisory_extensions" in value:
        _object(value["advisory_extensions"], f"{name}.advisory_extensions")


def _array(value: Any, name: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise ContinuationError(f"{name} must be a {qualifier}array")
    return value


def _repository(request: dict[str, Any]) -> str:
    repository = _string(request.get("repository"), "repository")
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ContinuationError("repository must use owner/name form")
    return repository


def _trusted_producers(
    request: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> frozenset[str]:
    raw = request.get("trusted_producers")
    if not isinstance(raw, list) or (not allow_empty and not raw):
        qualifier = "non-empty " if not allow_empty else ""
        raise ContinuationError(f"trusted_producers must be a {qualifier}array")
    producers = [_string(item, "trusted_producers item") for item in raw]
    if len(set(producers)) != len(producers):
        raise ContinuationError("trusted_producers must not contain duplicates")
    return frozenset(producers)


def _durable_reference(
    value: Any,
    name: str,
    repository: str,
    *,
    allowed_kinds: frozenset[str] | None = None,
) -> dict[str, Any]:
    reference = _object(value, name)
    kind = _string(reference.get("kind"), f"{name}.kind")
    if kind not in _REFERENCE_FIELDS:
        raise ContinuationError(f"{name}.kind is unsupported")
    if allowed_kinds is not None and kind not in allowed_kinds:
        allowed = ", ".join(sorted(allowed_kinds))
        raise ContinuationError(f"{name}.kind must be one of: {allowed}")
    expected_fields = frozenset({"kind", *_REFERENCE_FIELDS[kind]})
    _fields(reference, name, required=expected_fields)
    if reference.get("repository") != repository:
        raise ContinuationError(f"{name}.repository must match repository")
    for field in ("number", "issue", "comment_id", "pull_request", "review_id"):
        if field in reference:
            _positive_int(reference[field], f"{name}.{field}")
    if kind == "commit":
        sha = _string(reference.get("sha"), f"{name}.sha")
        if _SHA_RE.fullmatch(sha) is None:
            raise ContinuationError(f"{name}.sha must be a lowercase 40-character SHA")
    if kind == "branch":
        _string(reference.get("name"), f"{name}.name")
        sha = _string(reference.get("sha"), f"{name}.sha")
        if _SHA_RE.fullmatch(sha) is None:
            raise ContinuationError(f"{name}.sha must be a lowercase 40-character SHA")
    return reference


def _issue_locator(
    value: Any,
    name: str,
    repository: str,
) -> dict[str, Any]:
    return _durable_reference(
        value,
        name,
        repository,
        allowed_kinds=frozenset({"issue"}),
    )


def _condition(
    value: Any,
    name: str,
    *,
    repository: str,
    allow_local: bool = True,
) -> tuple[dict[str, Any], str | None]:
    condition = _object(value, name)
    kind = _string(condition.get("kind"), f"{name}.kind")
    schema = CONDITION_SCHEMAS.get(kind)
    if schema is None:
        raise ContinuationError(f"{name}.kind is unsupported")
    _fields(
        condition,
        name,
        required=schema["required_fields"],
        optional=schema["optional_fields"],
    )
    for field in schema["string_fields"]:
        _string(condition.get(field), f"{name}.{field}")
    for field, allowed_values in schema["enum_fields"].items():
        if condition.get(field) not in allowed_values:
            raise ContinuationError(f"{name}.{field} is unsupported")
    local_reference_field = schema["local_reference_field"]
    if local_reference_field is not None:
        if not allow_local:
            raise ContinuationError(f"{name}.kind requires a durable subject")
        return condition, str(condition[local_reference_field])
    _durable_reference(
        condition.get("target"),
        f"{name}.target",
        repository,
        allowed_kinds=schema["target_kinds"],
    )
    return condition, None


def _interaction(
    value: Any,
    *,
    repository: str,
) -> str:
    name = "completion.actions item.interaction"
    interaction = _object(value, name)
    _fields(
        interaction,
        name,
        required=frozenset({"classification", "evidence"}),
        optional=frozenset({"advisory_extensions"}),
    )
    classification = _string(
        interaction.get("classification"),
        f"{name}.classification",
    )
    if classification not in INTERACTION_CLASSIFICATIONS:
        raise ContinuationError(f"{name}.classification is unsupported")
    evidence_name = f"{name}.evidence"
    evidence = _object(interaction.get("evidence"), evidence_name)
    if "kind" not in evidence:
        raise ContinuationError(
            f"{evidence_name} is missing required field: kind"
        )
    evidence_kind = _string(evidence.get("kind"), f"{evidence_name}.kind")
    schema = INTERACTION_EVIDENCE_SCHEMAS.get(evidence_kind)
    if schema is None:
        raise ContinuationError(f"{evidence_name}.kind is unsupported")
    _fields(
        evidence,
        evidence_name,
        required=schema["required_fields"],
        optional=schema["optional_fields"],
    )
    if classification not in schema["classifications"]:
        raise ContinuationError(
            f"{evidence_name}.kind is incompatible with {classification}"
        )
    for field in schema["string_fields"]:
        _string(evidence.get(field), f"{evidence_name}.{field}")
    for field, allowed_values in schema["enum_fields"].items():
        if evidence.get(field) not in allowed_values:
            raise ContinuationError(f"{evidence_name}.{field} is unsupported")
    for field in schema["condition_fields"]:
        _condition(
            evidence.get(field),
            f"{evidence_name}.{field}",
            repository=repository,
            allow_local=False,
        )
    return classification


def _typed_semantics(
    value: Any,
    name: str,
    *,
    kinds: frozenset[str],
    second_field: str,
) -> list[dict[str, Any]]:
    entries = _array(value, name)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(entries):
        item_name = f"{name}[{index}]"
        entry = _object(item, item_name)
        _fields(
            entry,
            item_name,
            required=frozenset({"kind", second_field}),
            optional=frozenset({"advisory_extensions"}),
        )
        kind = _string(entry.get("kind"), f"{item_name}.kind")
        if kind not in kinds:
            raise ContinuationError(f"{item_name}.kind is unsupported")
        _string(entry.get(second_field), f"{item_name}.{second_field}")
        result.append(entry)
    return result


def _triggers(
    value: Any,
    name: str,
    *,
    repository: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    entries = _array(value, name)
    result: list[dict[str, Any]] = []
    local_references: list[str] = []
    for index, item in enumerate(entries):
        item_name = f"{name}[{index}]"
        entry = _object(item, item_name)
        _fields(
            entry,
            item_name,
            required=frozenset({"kind", "condition"}),
            optional=frozenset({"advisory_extensions"}),
        )
        kind = _string(entry.get("kind"), f"{item_name}.kind")
        if kind not in _TRIGGER_KINDS:
            raise ContinuationError(f"{item_name}.kind is unsupported")
        _validated, local_reference = _condition(
            entry.get("condition"),
            f"{item_name}.condition",
            repository=repository,
        )
        if local_reference is not None:
            local_references.append(local_reference)
        result.append(entry)
    return result, local_references


def _validate_action(
    value: Any,
    *,
    repository: str,
) -> tuple[dict[str, Any], list[str]]:
    action = _object(value, "completion.actions item")
    _fields(
        action,
        "completion.actions item",
        required=frozenset(
            {
                "key",
                "summary",
                "kind",
                "occurrence",
                "instruction",
                "target",
                "basis",
                "prerequisites",
                "interaction",
                "completion_condition",
            }
        ),
        optional=frozenset(
            {
                "context_references",
                "effects",
                "requirements",
                "triggers",
                "advisory_extensions",
            }
        ),
    )
    for field in ("key", "summary", "occurrence"):
        _string(action.get(field), f"completion.actions item.{field}")
    kind = _string(action.get("kind"), "completion.actions item.kind")
    if kind not in ACTION_KINDS:
        raise ContinuationError("completion.actions item.kind is unsupported")
    instruction = _object(
        action.get("instruction"),
        "completion.actions item.instruction",
    )
    _fields(
        instruction,
        "completion.actions item.instruction",
        required=frozenset({"mode", "value"}),
        optional=frozenset(
            {"behavior_version", "variant", "advisory_extensions"}
        ),
    )
    if instruction.get("mode") not in {"skill", "command", "manual"}:
        raise ContinuationError(
            "completion.actions item.instruction.mode is unsupported"
        )
    instruction_value = _string(
        instruction.get("value"),
        "completion.actions item.instruction.value",
    )
    if "\n" in instruction_value or "\r" in instruction_value:
        raise ContinuationError(
            "completion.actions item.instruction.value must be one line"
        )
    if instruction["mode"] == "skill" and not instruction_value.startswith("/"):
        raise ContinuationError(
            "completion.actions item.instruction.value must name a canonical Skill"
        )
    for field in ("behavior_version", "variant"):
        if field in instruction:
            _string(
                instruction[field],
                f"completion.actions item.instruction.{field}",
            )
    _durable_reference(
        action.get("target"),
        "completion.actions item.target",
        repository,
    )
    for item in _array(
        action.get("basis"),
        "completion.actions item.basis",
        nonempty=True,
    ):
        _durable_reference(
            item,
            "completion.actions item.basis item",
            repository,
        )
    local_references: list[str] = []
    for prerequisite in _array(
        action.get("prerequisites"),
        "completion.actions item.prerequisites",
    ):
        _validated, local_reference = _condition(
            prerequisite,
            "completion.actions item.prerequisites item",
            repository=repository,
        )
        if local_reference is not None:
            local_references.append(local_reference)
    classification = _interaction(
        action.get("interaction"),
        repository=repository,
    )
    if instruction["mode"] == "manual" and classification != "HITL-required":
        raise ContinuationError("manual Instructions must be HITL-required")
    if classification not in ACTION_KIND_SCHEMAS[kind]:
        raise ContinuationError(f"{kind} Actions must be HITL-required")
    _validated, completion_local_reference = _condition(
        action.get("completion_condition"),
        "completion.actions item.completion_condition",
        repository=repository,
    )
    if completion_local_reference is not None:
        local_references.append(completion_local_reference)
    for reference in _array(
        action.get("context_references", []),
        "completion.actions item.context_references",
    ):
        _durable_reference(
            reference,
            "completion.actions item.context_references item",
            repository,
        )
    _typed_semantics(
        action.get("effects", []),
        "completion.actions item.effects",
        kinds=_EFFECT_KINDS,
        second_field="scope",
    )
    _typed_semantics(
        action.get("requirements", []),
        "completion.actions item.requirements",
        kinds=_REQUIREMENT_KINDS,
        second_field="name",
    )
    _validated_triggers, trigger_local_references = _triggers(
        action.get("triggers", []),
        "completion.actions item.triggers",
        repository=repository,
    )
    local_references.extend(trigger_local_references)
    return action, local_references


def _validate_completion(
    request: dict[str, Any],
) -> tuple[str, frozenset[str], dict[str, Any], str]:
    _fields(
        request,
        "request",
        required=frozenset({"repository", "trusted_producers", "completion"}),
    )
    repository = _repository(request)
    completion = _object(request.get("completion"), "completion")
    _fields(
        completion,
        "completion",
        required=frozenset(
            {
                "continuation_contract_version",
                "record_format",
                "publication",
                "disposition",
                "workstream",
                "transition",
                "producer",
            }
        ),
        optional=frozenset(
            {
                "carrier",
                "actions",
                "outcome",
                "no_guidance",
                "advisory_extensions",
            }
        ),
    )
    if completion.get("continuation_contract_version") != CONTINUATION_CONTRACT_VERSION:
        raise ContinuationError("unsupported Continuation contract version")
    if completion.get("record_format") != RECORD_FORMAT:
        raise ContinuationError("unsupported Continuation record format")
    publication = completion.get("publication")
    if publication not in PUBLICATIONS:
        raise ContinuationError("completion.publication is unsupported")
    disposition = completion.get("disposition")
    if disposition not in DISPOSITIONS:
        raise ContinuationError("completion.disposition is unsupported")
    trusted_raw = request.get("trusted_producers")
    if not isinstance(trusted_raw, list):
        raise ContinuationError("trusted_producers must be an array")
    trusted = _trusted_producers(
        request,
        allow_empty=publication == "ephemeral",
    )
    workstream = _object(completion.get("workstream"), "completion.workstream")
    _fields(
        workstream,
        "completion.workstream",
        required=frozenset({"destination"})
        | (frozenset({"anchor"}) if publication == "shared" else frozenset()),
        optional=(
            frozenset({"advisory_extensions"})
            if publication == "shared"
            else frozenset({"anchor", "advisory_extensions"})
        ),
    )
    if "anchor" in workstream:
        _durable_reference(
            workstream.get("anchor"),
            "completion.workstream.anchor",
            repository,
        )
    _condition(
        workstream.get("destination"),
        "completion.workstream.destination",
        repository=repository,
        allow_local=False,
    )
    transition = _object(completion.get("transition"), "completion.transition")
    _fields(
        transition,
        "completion.transition",
        required=frozenset({"owner", "evidence"}),
        optional=frozenset({"advisory_extensions"}),
    )
    _string(transition.get("owner"), "completion.transition.owner")
    evidence = _array(
        transition.get("evidence"),
        "completion.transition.evidence",
    )
    if publication == "shared" and not evidence:
        raise ContinuationError("completion.transition.evidence must be non-empty")
    for item in evidence:
        _durable_reference(
            item,
            "completion.transition.evidence item",
            repository,
            allowed_kinds=frozenset({"issue-comment"}),
        )
    producer = _object(completion.get("producer"), "completion.producer")
    _fields(
        producer,
        "completion.producer",
        required=frozenset({"login", "role"}),
        optional=frozenset({"advisory_extensions"}),
    )
    login = _string(producer.get("login"), "completion.producer.login")
    if producer.get("role") != "planning":
        raise ContinuationError("completion.producer.role must be planning")
    if publication == "shared" and login not in trusted:
        raise ContinuationError("completion producer is not trusted")
    if publication == "shared":
        _durable_reference(
            completion.get("carrier"),
            "completion.carrier",
            repository,
            allowed_kinds=frozenset({"issue"}),
        )
    elif "carrier" in completion:
        raise ContinuationError("ephemeral completion must not contain a carrier")

    content_fields = {
        "continue": "actions",
        "terminal": "outcome",
        "no-guidance": "no_guidance",
    }
    expected_content = content_fields[disposition]
    present_content = {
        field for field in content_fields.values() if field in completion
    }
    if present_content != {expected_content}:
        raise ContinuationError(
            "completion must contain exactly one content branch matching disposition"
        )
    if disposition == "continue":
        actions = _array(
            completion.get("actions"),
            "completion.actions",
            nonempty=True,
        )
        keys: set[str] = set()
        local_references: list[tuple[str, str]] = []
        for item in actions:
            action, references = _validate_action(item, repository=repository)
            key = str(action["key"])
            if key in keys:
                raise ContinuationError(
                    f"completion.actions contains duplicate local key: {key}"
                )
            keys.add(key)
            local_references.extend((key, reference) for reference in references)
        for owner_key, reference in local_references:
            if reference not in keys:
                raise ContinuationError(
                    f"completion.actions contains broken local reference: {reference}"
                )
            if reference == owner_key:
                raise ContinuationError(
                    f"completion.actions contains self-reference: {reference}"
                )
    elif disposition == "terminal":
        if publication != "shared":
            raise ContinuationError("terminal completion must be shared")
        outcome = _object(completion.get("outcome"), "completion.outcome")
        _fields(
            outcome,
            "completion.outcome",
            required=frozenset(
                {
                    "kind",
                    "destination_satisfied",
                    "effective_at",
                    "evidence",
                    "summary",
                }
            ),
            optional=frozenset({"successor", "advisory_extensions"}),
        )
        outcome_kind = _string(outcome.get("kind"), "completion.outcome.kind")
        if outcome_kind not in OUTCOME_KINDS:
            raise ContinuationError("completion.outcome.kind is unsupported")
        destination_satisfied = outcome.get("destination_satisfied")
        if not isinstance(destination_satisfied, bool):
            raise ContinuationError(
                "completion.outcome.destination_satisfied must be a boolean"
            )
        if destination_satisfied is not (outcome_kind == "complete"):
            raise ContinuationError(
                "completion.outcome contradicts destination satisfaction"
            )
        effective_at = _string(
            outcome.get("effective_at"),
            "completion.outcome.effective_at",
        )
        try:
            parsed_effective_at = datetime.fromisoformat(
                effective_at.replace("Z", "+00:00")
            )
        except ValueError:
            parsed_effective_at = None
        if (
            "T" not in effective_at
            or not effective_at.endswith("Z")
            or parsed_effective_at is None
            or parsed_effective_at.utcoffset() != timezone.utc.utcoffset(None)
        ):
            raise ContinuationError(
                "completion.outcome.effective_at must be an RFC3339 UTC timestamp"
            )
        _string(outcome.get("summary"), "completion.outcome.summary")
        for item in _array(
            outcome.get("evidence"),
            "completion.outcome.evidence",
            nonempty=True,
        ):
            _durable_reference(
                item,
                "completion.outcome.evidence item",
                repository,
            )
        if outcome_kind == "superseded":
            _durable_reference(
                outcome.get("successor"),
                "completion.outcome.successor",
                repository,
            )
        elif "successor" in outcome:
            raise ContinuationError(
                "completion.outcome.successor is valid only for superseded"
            )
    else:
        no_guidance = _object(
            completion.get("no_guidance"),
            "completion.no_guidance",
        )
        _fields(
            no_guidance,
            "completion.no_guidance",
            required=frozenset({"reason", "summary", "references"}),
            optional=frozenset({"advisory_extensions"}),
        )
        reason = _string(
            no_guidance.get("reason"),
            "completion.no_guidance.reason",
        )
        if reason not in NO_GUIDANCE_REASONS:
            raise ContinuationError("completion.no_guidance.reason is unsupported")
        if (publication, reason) not in {
            ("shared", "no-successor-created"),
            ("ephemeral", "ephemeral-only"),
        }:
            raise ContinuationError(
                "completion publication contradicts no-guidance reason"
            )
        _string(no_guidance.get("summary"), "completion.no_guidance.summary")
        for item in _array(
            no_guidance.get("references"),
            "completion.no_guidance.references",
            nonempty=True,
        ):
            _durable_reference(
                item,
                "completion.no_guidance.references item",
                repository,
            )

    canonical_completion = _canonical_json(completion).encode("utf-8")
    if len(canonical_completion) > _MAX_RECORD_BYTES:
        raise ContinuationError(
            f"completion canonical JSON exceeds maximum record length {_MAX_RECORD_BYTES}"
        )
    return repository, trusted, completion, publication


def _without_advisory_extensions(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_advisory_extensions(item)
            for key, item in value.items()
            if key != "advisory_extensions"
        }
    if isinstance(value, list):
        return [_without_advisory_extensions(item) for item in value]
    return value


def _semantic_fingerprint(action: dict[str, Any]) -> str:
    semantics = {
        "instruction": action["instruction"],
        "prerequisites": action["prerequisites"],
        "interaction": action["interaction"],
        "completion_condition": action["completion_condition"],
        "effects": action.get("effects", []),
        "requirements": action.get("requirements", []),
        "triggers": action.get("triggers", []),
    }
    return hashlib.sha256(
        _canonical_json(_without_advisory_extensions(semantics)).encode("utf-8")
    ).hexdigest()


def _semantic_fingerprints(completion: dict[str, Any]) -> dict[str, str]:
    return {
        str(action["key"]): _semantic_fingerprint(action)
        for action in completion.get("actions", [])
    }


def _record_body(
    completion: dict[str, Any],
) -> tuple[str, dict[str, str], str]:
    revision_id = hashlib.sha256(
        _canonical_json(completion).encode("utf-8")
    ).hexdigest()
    fingerprints = _semantic_fingerprints(completion)
    record = {
        "revision_id": revision_id,
        "semantic_fingerprints": fingerprints,
        **completion,
    }
    canonical_record = _canonical_json(record)
    if len(canonical_record.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise ContinuationError(
            f"Producer revision exceeds maximum record length {_MAX_RECORD_BYTES}"
        )
    body = f"{_RECORD_MARKER}\n```json\n{canonical_record}\n```"
    if len(body.encode("utf-8")) > _MAX_CARRIER_BODY_BYTES:
        raise ContinuationError("Producer revision exceeds live carrier body limit")
    return revision_id, fingerprints, body


def _parse_record(comment: ContinuationComment) -> dict[str, Any] | None:
    prefix = f"{_RECORD_MARKER}\n```json\n"
    suffix = "\n```"
    if not comment.body.startswith(prefix) or not comment.body.endswith(suffix):
        return None
    raw = comment.body[len(prefix) : -len(suffix)]
    _check_json_nesting(raw, name=f"Producer revision comment {comment.id}")
    try:
        record = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ContinuationError(
            f"Producer revision comment {comment.id} contains invalid JSON"
        ) from exc
    if not isinstance(record, dict):
        raise ContinuationError(
            f"Producer revision comment {comment.id} must contain one JSON object"
        )
    _portable_json(record, name="Producer revision")
    if len(_canonical_json(record).encode("utf-8")) > _MAX_RECORD_BYTES:
        raise ContinuationError(
            f"Producer revision comment {comment.id} exceeds maximum record length"
        )
    revision_id = _string(record.get("revision_id"), "revision_id")
    stored_fingerprints = _object(
        record.get("semantic_fingerprints"),
        "semantic_fingerprints",
    )
    completion = {
        key: value
        for key, value in record.items()
        if key not in {"revision_id", "semantic_fingerprints"}
    }
    expected_id = hashlib.sha256(
        _canonical_json(completion).encode("utf-8")
    ).hexdigest()
    if revision_id != expected_id:
        raise ContinuationError(
            f"Producer revision comment {comment.id} has an invalid revision identity"
        )
    if stored_fingerprints != _semantic_fingerprints(completion):
        raise ContinuationError(
            f"Producer revision comment {comment.id} has invalid semantic fingerprints"
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
    repository, _trusted, completion, publication = _validate_completion(request)
    fingerprints = _semantic_fingerprints(completion)
    if publication == "ephemeral":
        return {
            "ok": True,
            "operation": "publish",
            "receipt": {
                "status": "unpublished",
                "publication": "ephemeral",
                "disposition": completion["disposition"],
                "semantic_fingerprints": fingerprints,
            },
        }
    carrier = completion["carrier"]
    carrier_number = int(carrier["number"])
    producer = completion["producer"]
    revision_id, fingerprints, body = _record_body(completion)
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
            "semantic_fingerprints": fingerprints,
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
    diagnostics: list[dict[str, Any]] = []
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
                    key: value
                    for key, value in record.items()
                    if key not in {"revision_id", "semantic_fingerprints"}
                },
            }
            _validate_completion(completion_request)
            revision_count += 1
            for action in record.get("actions", []):
                if (
                    action["target"]["kind"] != "issue"
                    or action["prerequisites"]
                    or action["completion_condition"]["kind"] != "issue-closed"
                    or action["completion_condition"]["target"]["kind"] != "issue"
                ):
                    diagnostics.append(
                        {
                            "code": "unsupported_reconciliation_semantics",
                            "revision_id": record["revision_id"],
                            "action_key": action["key"],
                        }
                    )
                    continue
                target = github.read_issue(
                    repository,
                    int(action["target"]["number"]),
                )
                if target.state != "OPEN":
                    continue
                actions.append(
                    {
                        "identity": _action_identity(record, action),
                        "semantic_fingerprint": record["semantic_fingerprints"][
                            action["key"]
                        ],
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
            "diagnostics": diagnostics,
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
