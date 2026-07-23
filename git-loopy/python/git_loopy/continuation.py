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
    ContinuationCarrier,
    ContinuationComment,
    ContinuationGitHubClient,
    GhError,
    SubprocessContinuationGitHubClient,
)
from git_loopy.release_version import read_runtime_release_version

CONTINUATION_CONTRACT_VERSION = "1.0"
RECORD_FORMAT = 1
WRAPPER_CONTRACT_VERSION = "1.3"
EVENT_SCHEMA_VERSION = "1.1"

CAPABILITY_MANIFEST: dict[str, Any] = {
    "continuation_contract_versions": [CONTINUATION_CONTRACT_VERSION],
    "record_formats": [RECORD_FORMAT],
    "wrapper_contract_version": WRAPPER_CONTRACT_VERSION,
    "event_schema_version": EVENT_SCHEMA_VERSION,
    "tracker_adapters": {
        "github": {"operations": ["publish", "reconcile", "repair-index"]}
    },
    "operations": {
        "capabilities": True,
        "publish": True,
        "reconcile": True,
        "record-dispatch-result": False,
        "repair-index": True,
    },
    "instruction_handlers": [],
    "instruction_modes": [],
    "evaluators": [],
    "effect_scopes": [],
    "optional_capabilities": {
        "immutable_producer_revisions": True,
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


def _capability_manifest() -> dict[str, Any]:
    return {
        "release_version": read_runtime_release_version(),
        **CAPABILITY_MANIFEST,
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
        "bound_fields": {},
        "enum_fields": {"reason": HUMAN_BOUNDARY_REASONS},
    },
    "transition-owner-attestation": {
        "classifications": frozenset({"AFK-safe"}),
        "required_fields": frozenset({"kind", "noninteractive", "owner"}),
        "optional_fields": frozenset({"advisory_extensions"}),
        "string_fields": frozenset({"owner"}),
        "condition_fields": frozenset(),
        "bound_fields": {"owner": "completion.transition.owner"},
        "enum_fields": {"noninteractive": frozenset({True})},
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
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_WRITE_PERMISSIONS = frozenset({"ADMIN", "MAINTAIN", "WRITE"})
_RECORD_METADATA_FIELDS = frozenset(
    {"revision_id", "semantic_fingerprints", "parents", "reattestation"}
)


class ContinuationError(ValueError):
    """A typed semantic rejection at the Continuation boundary."""


class PublicationRepairRequired(ContinuationError):
    """A durable transition exists but its Producer revision needs repair."""


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


def _portable_json(value: Any, *, name: str, depth: int = 0) -> None:
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
        container_depth = depth + 1
        if container_depth > _MAX_DEPTH:
            raise ContinuationError(
                f"{name} exceeds maximum nesting depth {_MAX_DEPTH}"
            )
        if len(value) > _MAX_ARRAY_LENGTH:
            raise ContinuationError(
                f"{name} array exceeds maximum length {_MAX_ARRAY_LENGTH}"
            )
        for item in value:
            _portable_json(item, name=name, depth=container_depth)
        return
    if isinstance(value, dict):
        container_depth = depth + 1
        if container_depth > _MAX_DEPTH:
            raise ContinuationError(
                f"{name} exceeds maximum nesting depth {_MAX_DEPTH}"
            )
        for key, item in value.items():
            _portable_json(key, name=name, depth=container_depth)
            _portable_json(item, name=name, depth=container_depth)
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


def _trusted_apps(request: dict[str, Any]) -> frozenset[str]:
    raw = request.get("trusted_apps", [])
    if not isinstance(raw, list):
        raise ContinuationError("trusted_apps must be an array")
    apps = [_string(item, "trusted_apps item") for item in raw]
    if len(set(apps)) != len(apps):
        raise ContinuationError("trusted_apps must not contain duplicates")
    return frozenset(apps)


def _trusted_reattesters(request: dict[str, Any]) -> frozenset[str]:
    raw = request.get("trusted_reattesters", [])
    if not isinstance(raw, list):
        raise ContinuationError("trusted_reattesters must be an array")
    reattesters = [_string(item, "trusted_reattesters item") for item in raw]
    if len(set(reattesters)) != len(reattesters):
        raise ContinuationError("trusted_reattesters must not contain duplicates")
    return frozenset(reattesters)


def _validate_reattestation(
    request: dict[str, Any],
    producer: str,
) -> dict[str, Any] | None:
    raw = request.get("reattestation")
    if raw is None:
        return None
    reattestation = _object(raw, "reattestation")
    _fields(
        reattestation,
        "reattestation",
        required=frozenset({"affected_heads", "authorized_by", "mode"}),
    )
    affected = _array(
        reattestation["affected_heads"],
        "reattestation.affected_heads",
        nonempty=True,
    )
    for revision_id in affected:
        if (
            not isinstance(revision_id, str)
            or _DIGEST_RE.fullmatch(revision_id) is None
        ):
            raise ContinuationError(
                "reattestation.affected_heads must contain lowercase SHA-256 digests"
            )
    if len(set(affected)) != len(affected):
        raise ContinuationError(
            "reattestation.affected_heads must not contain duplicates"
        )
    authorized_by = _string(
        reattestation["authorized_by"], "reattestation.authorized_by"
    )
    if authorized_by != producer:
        raise ContinuationError(
            "reattestation.authorized_by must match the authenticated producer"
        )
    if authorized_by not in _trusted_reattesters(request):
        raise ContinuationError("reattestation actor is not separately authorized")
    if reattestation["mode"] not in {"copy", "replace", "retire"}:
        raise ContinuationError("reattestation.mode is unsupported")
    return reattestation


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_observation(
    request: dict[str, Any],
    repository: str,
) -> tuple[dict[str, Any], list[str]]:
    observation = _object(request.get("observation"), "observation")
    _fields(
        observation,
        "observation",
        required=frozenset({"heads", "token", "validators"}),
    )
    heads = _array(observation["heads"], "observation.heads")
    validators = _array(observation["validators"], "observation.validators")
    parent_ids: list[str] = []
    for item in heads:
        head = _object(item, "observation.heads item")
        _fields(
            head,
            "observation.heads item",
            required=frozenset(
                {"carrier", "producer", "revision_id", "workstream_anchor"}
            ),
        )
        _positive_int(head["carrier"], "observation.heads item.carrier")
        _string(head["producer"], "observation.heads item.producer")
        revision_id = _string(head["revision_id"], "observation.heads item.revision_id")
        if _DIGEST_RE.fullmatch(revision_id) is None:
            raise ContinuationError(
                "observation.heads item.revision_id must be a lowercase SHA-256 digest"
            )
        _durable_reference(
            head["workstream_anchor"],
            "observation.heads item.workstream_anchor",
            repository,
        )
        parent_ids.append(revision_id)
    for item in validators:
        validator = _object(item, "observation.validators item")
        _fields(
            validator,
            "observation.validators item",
            required=frozenset({"comment_id", "sha256"}),
        )
        _positive_int(validator["comment_id"], "observation.validators item.comment_id")
        digest = _string(validator["sha256"], "observation.validators item.sha256")
        if _DIGEST_RE.fullmatch(digest) is None:
            raise ContinuationError(
                "observation.validators item.sha256 must be a lowercase SHA-256 digest"
            )
    if len(set(parent_ids)) != len(parent_ids):
        raise ContinuationError("observation.heads must not contain duplicates")
    expected_token = "sha256:" + _digest(
        {
            "repository": repository,
            "heads": heads,
            "validators": validators,
        }
    )
    if observation["token"] != expected_token:
        raise ContinuationError("observation token does not match its bound state")
    parents = _array(request.get("parents"), "parents")
    if parents != parent_ids:
        raise ContinuationError("parents must name the observed heads in order")
    return observation, parent_ids


def _authorize_actor(
    request: dict[str, Any],
    repository: str,
    producer: str,
    github: ContinuationGitHubClient,
) -> None:
    login, account_type = github.authenticated_actor()
    if login != producer:
        raise ContinuationError(
            "authenticated actor does not match completion producer"
        )
    if account_type in {"Bot", "App"}:
        if login not in _trusted_apps(request):
            raise ContinuationError("authenticated App producer is not allowlisted")
        return
    if login not in _trusted_producers(request):
        raise ContinuationError("authenticated human producer is not trusted")
    permission = github.repository_permission(repository, login)
    if permission not in _WRITE_PERMISSIONS:
        raise ContinuationError(
            "authenticated human producer lacks current write permission"
        )


def _authorize_policy_actor(
    request: dict[str, Any],
    repository: str,
    github: ContinuationGitHubClient,
) -> tuple[str, str]:
    login, account_type = github.authenticated_actor()
    if account_type in {"Bot", "App"}:
        if login not in _trusted_apps(request):
            raise ContinuationError("authenticated App actor is not allowlisted")
        return login, account_type
    if login not in _trusted_producers(request):
        raise ContinuationError("authenticated human actor is not trusted")
    if github.repository_permission(repository, login) not in _WRITE_PERMISSIONS:
        raise ContinuationError(
            "authenticated human actor lacks current write permission"
        )
    return login, account_type


def _verify_observation_validators(
    observation: dict[str, Any],
    carriers: list[ContinuationCarrier],
) -> None:
    comments = {
        comment.id: comment for carrier in carriers for comment in carrier.comments
    }
    for validator in observation["validators"]:
        comment_id = int(validator["comment_id"])
        comment = comments.get(comment_id)
        if comment is None:
            raise PublicationRepairRequired(
                "observed Producer revision was deleted; repair required"
            )
        actual = hashlib.sha256(comment.body.encode("utf-8")).hexdigest()
        if actual != validator["sha256"]:
            raise PublicationRepairRequired(
                "observed Producer revision was mutated; repair required"
            )


def _verify_observed_heads(
    observation: dict[str, Any],
    completion: dict[str, Any],
    carriers: list[ContinuationCarrier],
) -> None:
    carrier_number = int(completion["carrier"]["number"])
    producer = str(completion["producer"]["login"])
    anchor = completion["workstream"]["anchor"]
    comments = [
        comment
        for carrier in carriers
        if carrier.number == carrier_number
        for comment in carrier.comments
        if comment.author == producer
    ]
    for head in observation["heads"]:
        if (
            head["carrier"] != carrier_number
            or head["producer"] != producer
            or head["workstream_anchor"] != anchor
        ):
            raise ContinuationError(
                "observed heads must belong to the completion Producer lineage"
            )
        matched = False
        for comment in comments:
            try:
                record = _parse_record(comment)
            except ContinuationError:
                continue
            if record is not None and record["revision_id"] == head["revision_id"]:
                matched = True
                break
        if not matched:
            raise PublicationRepairRequired(
                "observed Producer predecessor is missing or unauthorized; "
                "repair required"
            )


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
    transition_owner: str,
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
        raise ContinuationError(f"{evidence_name} is missing required field: kind")
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
    for field, binding in schema["bound_fields"].items():
        if binding == "completion.transition.owner":
            expected = transition_owner
        else:
            raise AssertionError(f"unsupported interaction evidence binding: {binding}")
        if evidence.get(field) != expected:
            raise ContinuationError(f"{evidence_name}.{field} must match {binding}")
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
    transition_owner: str,
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
        optional=frozenset({"behavior_version", "variant", "advisory_extensions"}),
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
        transition_owner=transition_owner,
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
        optional=frozenset(
            {
                "trusted_apps",
                "trusted_reattesters",
                "observation",
                "parents",
                "reattestation",
            }
        ),
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
    trusted_apps = _trusted_apps(request)
    trusted = _trusted_producers(
        request,
        allow_empty=publication == "ephemeral" or bool(trusted_apps),
    )
    trusted_identities = trusted | trusted_apps
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
    transition_owner = _string(
        transition.get("owner"),
        "completion.transition.owner",
    )
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
    if publication == "shared" and login not in trusted_identities:
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
            action, references = _validate_action(
                item,
                repository=repository,
                transition_owner=transition_owner,
            )
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
    return repository, trusted_identities, completion, publication


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
    *,
    parents: list[str] | None = None,
    reattestation: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str], str]:
    identity_source: Any = completion
    if parents or reattestation is not None:
        identity_source = {
            "completion": completion,
            "parents": parents or [],
            **({"reattestation": reattestation} if reattestation is not None else {}),
        }
    revision_id = _digest(identity_source)
    fingerprints = _semantic_fingerprints(completion)
    record = {
        "revision_id": revision_id,
        "semantic_fingerprints": fingerprints,
        **({"parents": parents} if parents is not None else {}),
        **({"reattestation": reattestation} if reattestation is not None else {}),
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
        if key not in _RECORD_METADATA_FIELDS
    }
    parents = record.get("parents", [])
    reattestation = record.get("reattestation")
    identity_source: Any = completion
    if parents or reattestation is not None:
        identity_source = {
            "completion": completion,
            "parents": parents,
            **({"reattestation": reattestation} if reattestation is not None else {}),
        }
    expected_id = _digest(identity_source)
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


def _record_completion(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in _RECORD_METADATA_FIELDS
    }


def _lineage_key(
    carrier_number: int,
    record: dict[str, Any],
) -> tuple[int, str, str]:
    return (
        carrier_number,
        str(record["producer"]["login"]),
        _canonical_json(record["workstream"]["anchor"]),
    )


def _revision_semantics(record: dict[str, Any]) -> str:
    return _canonical_json(
        {
            "disposition": record["disposition"],
            "actions": sorted(record["semantic_fingerprints"].items()),
            "outcome": record.get("outcome"),
            "no_guidance": record.get("no_guidance"),
        }
    )


def _live_revision_entries(
    entries: list[tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]],
) -> list[tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]]:
    referenced = {
        parent
        for _carrier, _comment, record in entries
        for parent in record.get("parents", [])
    }
    return [entry for entry in entries if entry[2]["revision_id"] not in referenced]


def _comment_taint_identity(carrier_number: int, comment_id: int) -> str:
    return _digest(
        {
            "carrier": carrier_number,
            "comment_id": comment_id,
            "kind": "invalid-producer-comment",
        }
    )


def _tainted_lineage_heads(
    completion: dict[str, Any],
    carriers: list[ContinuationCarrier],
) -> set[str]:
    carrier_number = int(completion["carrier"]["number"])
    producer = str(completion["producer"]["login"])
    lineage = (
        carrier_number,
        producer,
        _canonical_json(completion["workstream"]["anchor"]),
    )
    records: dict[str, dict[str, Any]] = {}
    tainted: set[str] = set()
    for carrier in carriers:
        if carrier.number != carrier_number:
            continue
        for comment in carrier.comments:
            if comment.author != producer or _RECORD_MARKER not in comment.body:
                continue
            try:
                record = _parse_record(comment)
            except ContinuationError:
                tainted.add(_comment_taint_identity(carrier_number, comment.id))
                continue
            if record is None or _lineage_key(carrier_number, record) != lineage:
                continue
            revision_id = str(record["revision_id"])
            records[revision_id] = record
            if (
                comment.created_at is not None
                and comment.updated_at is not None
                and comment.created_at != comment.updated_at
            ):
                tainted.add(revision_id)
            try:
                _validate_completion(
                    {
                        "repository": completion["carrier"]["repository"],
                        "trusted_producers": [producer],
                        "completion": _record_completion(record),
                    }
                )
            except ContinuationError:
                tainted.add(revision_id)
    for revision_id, record in records.items():
        if any(parent not in records for parent in record.get("parents", [])):
            tainted.add(revision_id)
    changed = True
    while changed:
        changed = False
        for revision_id, record in records.items():
            if revision_id not in tainted and any(
                parent in tainted for parent in record.get("parents", [])
            ):
                tainted.add(revision_id)
                changed = True
    referenced_tainted = {
        parent
        for revision_id, record in records.items()
        if revision_id in tainted
        for parent in record.get("parents", [])
        if parent in tainted
    }
    return tainted - referenced_tainted


def _authorized_comment(
    comment: ContinuationComment,
    *,
    repository: str,
    trusted_humans: frozenset[str],
    trusted_apps: frozenset[str],
    github: ContinuationGitHubClient,
    permissions: dict[str, str],
) -> tuple[bool, str | None]:
    if comment.author_type in {"Bot", "App"}:
        if comment.author in trusted_apps:
            return True, None
        return False, "untrusted_marker_ignored"
    if comment.author not in trusted_humans:
        return False, "untrusted_marker_ignored"
    if comment.author not in permissions:
        permissions[comment.author] = github.repository_permission(
            repository, comment.author
        )
    if permissions[comment.author] not in _WRITE_PERMISSIONS:
        return False, "producer_permission_revoked"
    return True, None


_STABLE_READ_ATTEMPTS = 3
_UNAVAILABLE = object()
_NOT_FOUND_PHRASES = ("404", "not found", "could not resolve")
_REVIEW_STATE_LABELS = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes-requested",
    "COMMENTED": "commented",
}


def _is_not_found_error(exc: GhError) -> bool:
    """Return whether ``exc`` is a definitive absence, not a transient outage.

    A definitive 404-shaped failure is itself a stable durable fact (the
    Target does not currently exist); anything else is genuine
    unavailability and must never be treated as a negative result.
    """
    message = exc.stderr_tail.lower()
    return any(phrase in message for phrase in _NOT_FOUND_PHRASES)


def _stable_read(reader: Any, *, is_not_found: Any = _is_not_found_error) -> tuple[Any, bool]:
    """Read a durable fact, retrying only when the read is genuinely in doubt.

    A clean first read is accepted immediately as authoritative (so the
    common case costs exactly one GitHub call, matching every previously
    published command shape). Retries are reserved for reads that are
    *not* immediately trustworthy: a failed attempt is retried up to
    ``_STABLE_READ_ATTEMPTS`` times in total, and a value is only accepted
    once two consecutive attempts agree (including two consecutive
    unavailable attempts, which are themselves stable evidence of
    persistent failure). Returns ``(value, stable)``; ``value`` is
    ``None`` for a definitive absence, or ``_UNAVAILABLE`` when the read
    could not be trusted.
    """
    try:
        first: Any = reader()
    except GhError as exc:
        first = None if is_not_found(exc) else _UNAVAILABLE
    else:
        return first, True
    if first is None:
        return None, True
    previous = first
    for _attempt in range(_STABLE_READ_ATTEMPTS - 1):
        try:
            current: Any = reader()
        except GhError as exc:
            current = None if is_not_found(exc) else _UNAVAILABLE
        if current == previous:
            return current, True
        previous = current
    return previous, False


def _artifact_read_plan(
    target_kind: str,
    target: dict[str, Any],
    github: ContinuationGitHubClient,
    repository: str,
) -> tuple[tuple[Any, ...], Any]:
    """Return the shared cache key and reader for one reference Target.

    Reference kinds reuse the identical cache key used by their dedicated
    condition kind (for example ``issue``) so that an ``artifact-exists``
    check and an ``issue-open`` check against the same issue within one
    reconcile call are evaluated against exactly one stable read.
    """
    if target_kind == "issue":
        number = int(target["number"])
        return ("issue", repository, number), (
            lambda: github.read_issue(repository, number)
        )
    if target_kind == "pull-request":
        number = int(target["number"])
        return ("pull-request", repository, number), (
            lambda: github.read_pull_request(repository, number)
        )
    if target_kind == "commit":
        sha = str(target["sha"])
        return ("commit", repository, sha), (
            lambda: github.read_commit(repository, sha)
        )
    if target_kind == "branch":
        name = str(target["name"])
        return ("branch", repository, name), (
            lambda: github.read_branch(repository, name)
        )
    if target_kind == "issue-comment":
        comment_id = int(target["comment_id"])
        return ("issue-comment", repository, comment_id), (
            lambda: github.read_issue_comment(repository, comment_id)
        )
    if target_kind == "pull-request-review":
        pull_request = int(target["pull_request"])
        review_id = int(target["review_id"])
        return ("pull-request-review", repository, pull_request, review_id), (
            lambda: github.read_pull_request_review(
                repository, pull_request, review_id
            )
        )
    raise AssertionError(f"unsupported reference target kind: {target_kind}")


def _predicate_issue_open(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.state == "OPEN"


def _predicate_issue_closed(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.state == "CLOSED"


def _predicate_pull_request_open(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.state == "OPEN"


def _predicate_pull_request_closed(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.state in {"CLOSED", "MERGED"}


def _predicate_pull_request_merged(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.state == "MERGED"


def _predicate_issue_label_present(condition: dict[str, Any], value: Any) -> bool:
    return value is not None and condition["label"] in value.labels


def _predicate_sub_issues_complete(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.completed >= value.total


def _predicate_existence(_condition: dict[str, Any], value: Any) -> bool:
    return value is not None


def _predicate_branch_head_equals(condition: dict[str, Any], value: Any) -> bool:
    return value is not None and value.sha == condition["target"]["sha"]


def _predicate_pull_request_review_state(condition: dict[str, Any], value: Any) -> bool:
    if value is None:
        return False
    return _REVIEW_STATE_LABELS.get(value.state) == condition["state"]


_CONDITION_PREDICATES: dict[str, Any] = {
    "issue-open": _predicate_issue_open,
    "issue-closed": _predicate_issue_closed,
    "dependency-satisfied": _predicate_issue_closed,
    "pull-request-open": _predicate_pull_request_open,
    "pull-request-closed": _predicate_pull_request_closed,
    "pull-request-merged": _predicate_pull_request_merged,
    "issue-label-present": _predicate_issue_label_present,
    "sub-issues-complete": _predicate_sub_issues_complete,
    "commit-exists": _predicate_existence,
    "artifact-exists": _predicate_existence,
    "branch-head-equals": _predicate_branch_head_equals,
    "pull-request-review-state": _predicate_pull_request_review_state,
}


def _reference_read_plan(
    kind: str,
    condition: dict[str, Any],
    github: ContinuationGitHubClient,
    repository: str,
) -> tuple[tuple[Any, ...], Any]:
    target = condition["target"]
    if kind == "artifact-exists":
        return _artifact_read_plan(target["kind"], target, github, repository)
    if kind == "issue-label-present":
        number = int(target["number"])
        return ("issue-labels", repository, number), (
            lambda: github.read_issue_labels(repository, number)
        )
    if kind == "sub-issues-complete":
        number = int(target["number"])
        return ("issue-sub-issues", repository, number), (
            lambda: github.read_issue_sub_issues(repository, number)
        )
    return _artifact_read_plan(target["kind"], target, github, repository)


def _evaluate_condition(
    condition: dict[str, Any],
    *,
    github: ContinuationGitHubClient,
    repository: str,
    fact_cache: dict[tuple[Any, ...], tuple[Any, bool]],
    resolve_local: Any,
) -> str:
    """Evaluate one typed condition against the shared stable fact set.

    Returns ``"satisfied"``, ``"unsatisfied"``, or ``"unverified"``. Every
    reference read is served from ``fact_cache`` so the same durable Target
    is read at most once per reconcile call regardless of how many
    conditions across however many fragments reference it.
    """
    kind = condition["kind"]
    if kind == "action-completed":
        return resolve_local(str(condition["action_key"]))
    cache_key, reader = _reference_read_plan(kind, condition, github, repository)
    if cache_key not in fact_cache:
        fact_cache[cache_key] = _stable_read(reader)
    value, stable = fact_cache[cache_key]
    if not stable or value is _UNAVAILABLE:
        return "unverified"
    return "satisfied" if _CONDITION_PREDICATES[kind](condition, value) else "unsatisfied"


def _evaluate_fragment(
    record: dict[str, Any],
    *,
    github: ContinuationGitHubClient,
    repository: str,
    fact_cache: dict[tuple[Any, ...], tuple[Any, bool]],
) -> tuple[
    list[tuple[dict[str, Any], str, list[dict[str, Any]]]],
    list[dict[str, Any]],
]:
    """Derive Ready/Blocked outstanding Actions for one Producer revision.

    Completed, Unverified, and cyclic Actions are excluded from the
    returned outstanding-action list and reported only via diagnostics,
    since Unverified evidence must never surface as an optimistic
    Ready/Blocked/completion classification.
    """
    actions_by_key = {action["key"]: action for action in record["actions"]}
    status_cache: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    revision_id = record["revision_id"]

    def resolve_completion(key: str, stack: tuple[str, ...]) -> str:
        if key in status_cache:
            return status_cache[key]
        if key in stack:
            cycle = list(stack[stack.index(key) :]) + [key]
            diagnostics.append(
                {
                    "code": "prerequisite_cycle",
                    "revision_id": revision_id,
                    "actions": cycle,
                }
            )
            for cycle_key in cycle:
                status_cache[cycle_key] = "conflict"
            return "conflict"
        action = actions_by_key.get(key)
        if action is None:
            status_cache[key] = "unverified"
            return "unverified"
        status = _evaluate_condition(
            action["completion_condition"],
            github=github,
            repository=repository,
            fact_cache=fact_cache,
            resolve_local=lambda referenced: resolve_completion(
                referenced, stack + (key,)
            ),
        )
        status_cache[key] = status
        return status

    results: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    for action in record["actions"]:
        key = action["key"]
        completion_status = resolve_completion(key, ())
        if completion_status in {"conflict", "satisfied"}:
            continue
        if completion_status == "unverified":
            diagnostics.append(
                {
                    "code": "unverified_completion",
                    "revision_id": revision_id,
                    "action_key": key,
                }
            )
            continue

        unsatisfied: list[dict[str, Any]] = []
        prerequisite_unverified = False
        conflicted = False
        for prerequisite in action["prerequisites"]:
            status = _evaluate_condition(
                prerequisite,
                github=github,
                repository=repository,
                fact_cache=fact_cache,
                resolve_local=lambda referenced: resolve_completion(
                    referenced, (key,)
                ),
            )
            if status == "conflict":
                conflicted = True
                break
            if status == "unverified":
                prerequisite_unverified = True
            elif status == "unsatisfied":
                unsatisfied.append(prerequisite)
        if conflicted:
            continue
        if prerequisite_unverified:
            diagnostics.append(
                {
                    "code": "unverified_prerequisite",
                    "revision_id": revision_id,
                    "action_key": key,
                }
            )
            continue
        readiness = "Blocked" if unsatisfied else "Ready"
        results.append((action, readiness, unsatisfied))
    return results, diagnostics


def _union_basis(basis_lists: Any) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for basis_list in basis_lists:
        for item in basis_list:
            seen[_canonical_json(item)] = item
    return [seen[key] for key in sorted(seen)]


def _union_provenance(contributed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in contributed:
        record = entry["record"]
        comment = entry["comment"]
        producer = entry["producer"]
        key = (record["carrier"]["number"], record["revision_id"], comment.id)
        seen[key] = {
            "login": producer["login"],
            "role": producer["role"],
            "carrier": record["carrier"],
            "revision_id": record["revision_id"],
            "comment_id": comment.id,
            "comment_url": comment.url,
        }
    return [seen[key] for key in sorted(seen)]


_READINESS_RANK = {"Ready": 0, "Blocked": 1}


def _frontier_order_key(action: dict[str, Any]) -> tuple[int, str, str]:
    """Deterministic prospective-frontier order for verified guidance.

    Orders Ready Actions ahead of Blocked ones, then breaks ties by canonical
    Workstream Anchor and canonical Action identity. Identity already fixes a
    total order, so the same durable facts always yield the same human-facing
    frontier rather than the incidental identity-hash order.
    """
    return (
        _READINESS_RANK[action["readiness"]],
        _canonical_json(action["workstream_anchor"]),
        action["identity"],
    )


def _derive_actions(
    guidance_entries: list[tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]],
    *,
    github: ContinuationGitHubClient,
    repository: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive deduplicated, conflict-checked outstanding Actions on demand.

    Evaluates every guidance-selected fragment against one shared stable
    fact set, then groups equivalent live claims under one Action identity:
    matching semantics union their Basis/Producer provenance, while
    incompatible semantics under the same identity are reported as a
    Continuation conflict and quarantined (excluded entirely) rather than
    resolved by recency or discovery order.
    """
    fact_cache: dict[tuple[Any, ...], tuple[Any, bool]] = {}
    diagnostics: list[dict[str, Any]] = []
    contributions: dict[str, list[dict[str, Any]]] = {}

    for carrier, comment, record in guidance_entries:
        results, fragment_diagnostics = _evaluate_fragment(
            record, github=github, repository=repository, fact_cache=fact_cache
        )
        diagnostics.extend(fragment_diagnostics)
        producer = record["producer"]
        for action, readiness, unsatisfied in results:
            identity = _action_identity(record, action)
            contributions.setdefault(identity, []).append(
                {
                    "carrier": carrier,
                    "comment": comment,
                    "record": record,
                    "producer": producer,
                    "action": action,
                    "readiness": readiness,
                    "unsatisfied": unsatisfied,
                    "semantic_fingerprint": record["semantic_fingerprints"][
                        action["key"]
                    ],
                }
            )

    actions: list[dict[str, Any]] = []
    for identity, contributed in contributions.items():
        fingerprints = {entry["semantic_fingerprint"] for entry in contributed}
        if len(fingerprints) > 1:
            diagnostics.append(
                {
                    "code": "action_conflict",
                    "identity": identity,
                    "revision_ids": sorted(
                        entry["record"]["revision_id"] for entry in contributed
                    ),
                    "semantic_fingerprints": sorted(fingerprints),
                }
            )
            continue
        contributed.sort(
            key=lambda entry: (entry["record"]["revision_id"], entry["comment"].id)
        )
        canonical = contributed[0]
        action = canonical["action"]
        item = {
            "identity": identity,
            "semantic_fingerprint": canonical["semantic_fingerprint"],
            "workstream_anchor": canonical["record"]["workstream"]["anchor"],
            "summary": action["summary"],
            "kind": action["kind"],
            "readiness": canonical["readiness"],
            "instruction": action["instruction"],
            "target": action["target"],
            "basis": _union_basis(entry["action"]["basis"] for entry in contributed),
            "producer": {
                **canonical["producer"],
                "carrier": canonical["record"]["carrier"],
                "revision_id": canonical["record"]["revision_id"],
                "comment_id": canonical["comment"].id,
                "comment_url": canonical["comment"].url,
            },
            "prerequisites": action["prerequisites"],
            "interaction": action["interaction"],
            "completion_condition": action["completion_condition"],
        }
        if len(contributed) > 1:
            # Only surface provenance once a live claim was actually merged
            # under this identity, preserving the exact single-source
            # command framing otherwise.
            item["provenance"] = _union_provenance(contributed)
        if canonical["unsatisfied"]:
            # Only surface unsatisfied prerequisites for Blocked Actions,
            # preserving the exact existing Ready-only command framing.
            item["unsatisfied_prerequisites"] = canonical["unsatisfied"]
        actions.append(item)
    actions.sort(key=_frontier_order_key)
    return actions, diagnostics


def _reconcile_revision_protocol(
    request: dict[str, Any],
    github: ContinuationGitHubClient,
) -> dict[str, Any]:
    repository = _repository(request)
    trusted_humans = _trusted_producers(request)
    trusted_apps = _trusted_apps(request)
    carriers = github.list_all_continuation_carriers(repository)
    permissions: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    entries: list[tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]] = []
    carriers_with_records: set[int] = set()
    carriers_with_trusted_markers: set[int] = set()

    for carrier in carriers:
        for comment in carrier.comments:
            authorized, rejection = _authorized_comment(
                comment,
                repository=repository,
                trusted_humans=trusted_humans,
                trusted_apps=trusted_apps,
                github=github,
                permissions=permissions,
            )
            if not authorized:
                if _RECORD_MARKER in comment.body:
                    diagnostics.append(
                        {
                            "code": rejection,
                            "carrier": carrier.number,
                            "comment_id": comment.id,
                            "author": comment.author,
                        }
                    )
                continue
            if _RECORD_MARKER in comment.body:
                carriers_with_trusted_markers.add(carrier.number)
            if (
                comment.created_at is not None
                and comment.updated_at is not None
                and comment.created_at != comment.updated_at
            ):
                diagnostics.append(
                    {
                        "code": "mutated_revision",
                        "carrier": carrier.number,
                        "comment_id": comment.id,
                    }
                )
                continue
            try:
                record = _parse_record(comment)
                if record is None:
                    continue
                producer = _object(record.get("producer"), "producer")
                if producer.get("login") != comment.author:
                    raise ContinuationError(
                        "embedded Producer does not match authenticated comment author"
                    )
                _validate_completion(
                    {
                        "repository": repository,
                        "trusted_producers": sorted(trusted_humans | trusted_apps),
                        "completion": _record_completion(record),
                    }
                )
                parents = record.get("parents", [])
                if not isinstance(parents, list) or any(
                    not isinstance(parent, str) or _DIGEST_RE.fullmatch(parent) is None
                    for parent in parents
                ):
                    raise ContinuationError("revision parents are malformed")
                if len(set(parents)) != len(parents):
                    raise ContinuationError("revision parents contain duplicates")
            except ContinuationError as exc:
                diagnostics.append(
                    {
                        "code": "invalid_revision",
                        "carrier": carrier.number,
                        "comment_id": comment.id,
                        "affected_head": _comment_taint_identity(
                            carrier.number, comment.id
                        ),
                        "message": str(exc),
                    }
                )
                continue
            entries.append((carrier, comment, record))
            carriers_with_records.add(carrier.number)

    indexed_numbers = {
        carrier.number for carrier in carriers if _INDEX_LABEL in carrier.labels
    }
    for number in sorted(carriers_with_records - indexed_numbers):
        diagnostics.append({"code": "index_label_missing", "carrier": number})
    for number in sorted(indexed_numbers - carriers_with_trusted_markers):
        diagnostics.append({"code": "index_label_stale", "carrier": number})

    lineages: dict[
        tuple[int, str, str],
        list[tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]],
    ] = {}
    for entry in entries:
        carrier, _comment, record = entry
        lineage = _lineage_key(carrier.number, record)
        lineages.setdefault(lineage, []).append(entry)

    observed_head_entries: list[
        tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]
    ] = []
    guidance_entries: list[
        tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]
    ] = []
    for lineage_entries in lineages.values():
        by_id = {entry[2]["revision_id"]: entry for entry in lineage_entries}
        tainted: set[str] = set()
        for _carrier, comment, record in lineage_entries:
            missing = [
                parent for parent in record.get("parents", []) if parent not in by_id
            ]
            if missing:
                tainted.add(str(record["revision_id"]))
                diagnostics.append(
                    {
                        "code": "missing_predecessor",
                        "comment_id": comment.id,
                        "revision_id": record["revision_id"],
                        "missing": sorted(missing),
                    }
                )
        changed = True
        while changed:
            changed = False
            for _carrier, _comment, record in lineage_entries:
                revision_id = str(record["revision_id"])
                if revision_id not in tainted and any(
                    parent in tainted for parent in record.get("parents", [])
                ):
                    tainted.add(revision_id)
                    changed = True
        usable_entries = [
            entry for entry in lineage_entries if entry[2]["revision_id"] not in tainted
        ]
        live_entries = _live_revision_entries(usable_entries)
        observed_head_entries.extend(live_entries)
        semantics = {_revision_semantics(entry[2]) for entry in live_entries}
        if len(semantics) > 1:
            diagnostics.append(
                {
                    "code": "revision_fork",
                    "carrier": live_entries[0][0].number,
                    "heads": sorted(entry[2]["revision_id"] for entry in live_entries),
                }
            )
        elif live_entries:
            guidance_entries.append(
                min(live_entries, key=lambda entry: entry[2]["revision_id"])
            )

    observed_head_entries.sort(
        key=lambda entry: (entry[0].number, entry[2]["revision_id"])
    )
    actions, action_diagnostics = _derive_actions(
        guidance_entries, github=github, repository=repository
    )
    diagnostics.extend(action_diagnostics)

    heads = [
        {
            "carrier": carrier.number,
            "producer": record["producer"]["login"],
            "revision_id": record["revision_id"],
            "workstream_anchor": record["workstream"]["anchor"],
        }
        for carrier, _comment, record in observed_head_entries
    ]
    validators = [
        {
            "comment_id": comment.id,
            "sha256": hashlib.sha256(comment.body.encode("utf-8")).hexdigest(),
        }
        for _carrier, comment, _record in sorted(entries, key=lambda entry: entry[1].id)
    ]
    observation_source = {
        "repository": repository,
        "heads": heads,
        "validators": validators,
    }
    return {
        "ok": True,
        "operation": "reconcile",
        "result": {
            "status": "guidance" if actions else "waiting",
            "observed": {
                "repository": repository,
                "indexed_carriers": len(indexed_numbers),
                "producer_revisions": len(entries),
            },
            "actions": actions,
            "diagnostics": diagnostics,
            "observation": {
                "heads": heads,
                "token": "sha256:" + _digest(observation_source),
                "validators": validators,
            },
        },
    }


def _publish(
    request: dict[str, Any],
    github: ContinuationGitHubClient,
) -> dict[str, Any]:
    repository, _trusted, completion, publication = _validate_completion(request)
    fingerprints = _semantic_fingerprints(completion)
    if "observation" not in request and any(
        field in request for field in ("parents", "reattestation")
    ):
        raise ContinuationError(
            "observation is required when parents or reattestation is present"
        )
    if publication == "ephemeral" and any(
        field in request for field in ("observation", "parents", "reattestation")
    ):
        raise ContinuationError(
            "immutable revision fields require shared publication"
        )
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
    protocol = "observation" in request
    parents: list[str] | None = None
    reattestation: dict[str, Any] | None = None
    protocol_carriers: list[ContinuationCarrier] = []
    if protocol:
        observation, parents = _validate_observation(request, repository)
        _authorize_actor(
            request,
            repository,
            str(producer["login"]),
            github,
        )
        reattestation = _validate_reattestation(
            request,
            str(producer["login"]),
        )
        protocol_carriers = github.list_all_continuation_carriers(repository)
        _verify_observation_validators(observation, protocol_carriers)
        _verify_observed_heads(observation, completion, protocol_carriers)
        tainted_heads = _tainted_lineage_heads(completion, protocol_carriers)
        if tainted_heads:
            if reattestation is None:
                raise PublicationRepairRequired(
                    "tainted Producer lineage requires authorized re-attestation; "
                    "repair required"
                )
            if tainted_heads != set(reattestation["affected_heads"]):
                raise ContinuationError(
                    "reattestation.affected_heads must name every tainted lineage head"
                )
    revision_id, fingerprints, body = _record_body(
        completion,
        parents=parents,
        reattestation=reattestation,
    )
    if protocol:
        for observed_carrier in protocol_carriers:
            if observed_carrier.number != carrier_number:
                continue
            for comment in observed_carrier.comments:
                if comment.author != producer["login"] or (
                    comment.author_type in {"Bot", "App"}
                    and comment.author not in _trusted_apps(request)
                ):
                    continue
                try:
                    existing = _parse_record(comment)
                except ContinuationError:
                    continue
                if (
                    existing is not None
                    and existing["revision_id"] == revision_id
                    and comment.body == body
                ):
                    return {
                        "ok": True,
                        "operation": "publish",
                        "receipt": {
                            "status": "idempotent",
                            "revision_id": revision_id,
                            "carrier": carrier,
                            "comment": {
                                "id": comment.id,
                                "url": comment.url,
                            },
                            "index_label": _INDEX_LABEL,
                            "semantic_fingerprints": fingerprints,
                            "parents": parents,
                            **(
                                {"reattestation": reattestation}
                                if reattestation is not None
                                else {}
                            ),
                        },
                    }
    for evidence in completion["transition"]["evidence"]:
        github.read_issue_comment(repository, int(evidence["comment_id"]))
    try:
        github.ensure_issue_label(repository, carrier_number, _INDEX_LABEL)
        appended = github.append_issue_comment(repository, carrier_number, body)
        committed = github.read_issue_comment(repository, appended.id)
    except GhError as exc:
        raise PublicationRepairRequired(
            "publication failed after durable transition: "
            f"{exc.stderr_tail}; repair required"
        ) from exc
    if appended.author != producer["login"]:
        raise PublicationRepairRequired(
            "published Producer revision author does not match completion producer; "
            "repair required"
        )
    if committed.body != body or committed.author != producer["login"]:
        raise PublicationRepairRequired(
            "Producer revision reread did not match the append; repair required"
        )
    status = "committed"
    conflicting_heads: list[str] = []
    if protocol:
        committed_record = _parse_record(committed)
        assert committed_record is not None
        lineage_entries: list[
            tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]
        ] = [
            (
                ContinuationCarrier(
                    number=carrier_number,
                    state="OPEN",
                    url=str(carrier.get("url", "")),
                    comments=(committed,),
                ),
                committed,
                committed_record,
            )
        ]
        for observed_carrier in protocol_carriers:
            if observed_carrier.number != carrier_number:
                continue
            for comment in observed_carrier.comments:
                if comment.author != producer["login"]:
                    continue
                try:
                    record = _parse_record(comment)
                except ContinuationError:
                    continue
                if record is not None and _lineage_key(
                    carrier_number, record
                ) == _lineage_key(carrier_number, committed_record):
                    lineage_entries.append((observed_carrier, comment, record))
        if reattestation is not None:
            affected_heads = set(reattestation["affected_heads"])
            lineage_entries = [
                entry
                for entry in lineage_entries
                if entry[2]["revision_id"] not in affected_heads
            ]
        live_entries = _live_revision_entries(lineage_entries)
        if len({_revision_semantics(entry[2]) for entry in live_entries}) > 1:
            status = "conflict"
            conflicting_heads = sorted(
                entry[2]["revision_id"] for entry in live_entries
            )
    return {
        "ok": True,
        "operation": "publish",
        "receipt": {
            "status": status,
            "revision_id": revision_id,
            "carrier": carrier,
            "comment": {"id": committed.id, "url": committed.url},
            "index_label": _INDEX_LABEL,
            "semantic_fingerprints": fingerprints,
            **({"parents": parents} if parents is not None else {}),
            **({"reattestation": reattestation} if reattestation is not None else {}),
            **({"conflicting_heads": conflicting_heads} if conflicting_heads else {}),
        },
    }


def _reconcile(
    request: dict[str, Any],
    github: ContinuationGitHubClient,
) -> dict[str, Any]:
    repository = _repository(request)
    trusted = _trusted_producers(request)
    revision_protocol = request.get("revision_protocol", False)
    if not isinstance(revision_protocol, bool):
        raise ContinuationError("revision_protocol must be a boolean")
    if revision_protocol:
        return _reconcile_revision_protocol(request, github)
    _trusted_apps(request)
    carriers = (
        github.list_all_continuation_carriers(repository)
        if revision_protocol
        else github.list_continuation_carriers(repository, _INDEX_LABEL)
    )
    diagnostics: list[dict[str, Any]] = []
    revision_count = 0
    guidance_entries: list[
        tuple[ContinuationCarrier, ContinuationComment, dict[str, Any]]
    ] = []
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
                    if key not in _RECORD_METADATA_FIELDS
                },
            }
            _validate_completion(completion_request)
            revision_count += 1
            guidance_entries.append((carrier, comment, record))
    actions, action_diagnostics = _derive_actions(
        guidance_entries, github=github, repository=repository
    )
    diagnostics.extend(action_diagnostics)
    result = {
        "status": "guidance" if actions else "waiting",
        "observed": {
            "repository": repository,
            "indexed_carriers": len(carriers),
            "producer_revisions": revision_count,
        },
        "actions": actions,
        "diagnostics": diagnostics,
    }
    return {
        "ok": True,
        "operation": "reconcile",
        "result": result,
    }


def _repair_index(
    request: dict[str, Any],
    github: ContinuationGitHubClient,
) -> dict[str, Any]:
    _fields(
        request,
        "request",
        required=frozenset({"repository", "trusted_producers"}),
        optional=frozenset({"trusted_apps"}),
    )
    repository = _repository(request)
    trusted_humans = _trusted_producers(request)
    trusted_apps = _trusted_apps(request)
    _authorize_policy_actor(request, repository, github)
    carriers = github.list_all_continuation_carriers(repository)
    permissions: dict[str, str] = {}
    added: list[int] = []
    removed: list[int] = []
    for carrier in carriers:
        has_record = False
        has_trusted_marker = False
        for comment in carrier.comments:
            authorized, _rejection = _authorized_comment(
                comment,
                repository=repository,
                trusted_humans=trusted_humans,
                trusted_apps=trusted_apps,
                github=github,
                permissions=permissions,
            )
            if not authorized:
                continue
            if _RECORD_MARKER in comment.body:
                has_trusted_marker = True
            try:
                record = _parse_record(comment)
            except ContinuationError:
                continue
            if record is not None:
                producer = _object(record.get("producer"), "producer")
                if producer.get("login") != comment.author:
                    continue
                try:
                    _validate_completion(
                        {
                            "repository": repository,
                            "trusted_producers": sorted(trusted_humans | trusted_apps),
                            "completion": _record_completion(record),
                        }
                    )
                except ContinuationError:
                    continue
                has_record = True
        indexed = _INDEX_LABEL in carrier.labels
        if has_record and not indexed:
            github.ensure_issue_label(repository, carrier.number, _INDEX_LABEL)
            added.append(carrier.number)
        elif indexed and not has_trusted_marker:
            github.remove_issue_label(repository, carrier.number, _INDEX_LABEL)
            removed.append(carrier.number)
    return {
        "ok": True,
        "operation": "repair-index",
        "result": {
            "status": "repaired",
            "index_label": _INDEX_LABEL,
            "added": sorted(added),
            "removed": sorted(removed),
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
        _emit_json({"ok": True, "capabilities": _capability_manifest()}, stdout)
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
        elif operation == "repair-index":
            result = _repair_index(request, _make_github_client())
        else:
            result = None
    except PublicationRepairRequired as exc:
        message = str(exc)
        _emit_json(
            {
                "ok": False,
                "operation": operation,
                "error": {"code": "repair_required", "message": message},
            },
            stdout,
        )
        print(f"git-loopy continuation: {message}", file=stderr)
        return 1
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
        print(
            f"git-loopy continuation: GitHub operation failed: {message}", file=stderr
        )
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
