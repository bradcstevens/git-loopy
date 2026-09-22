"""Project final Routing resolutions onto tracker issues.

The local Routing resolution remains authoritative.  This module only records
and delivers its observational projection, so tracker state is never read as
route input.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar, Iterator, Protocol, Sequence, runtime_checkable

from git_loopy.config import RoutingResolution, RoutingSource
from git_loopy.route_identity import (
    ROUTE_LABEL_PREFIX,
    format_context_capacity,
    is_route_label,
    is_route_projection_comment,
    project_route_dimensions,
    route_comment_marker,
    route_label,
)

__all__ = [
    "ROUTE_LABEL_PREFIX",
    "RouteDeliveryError",
    "RouteDeliveryResult",
    "RouteDeliveryStatus",
    "RoutePublicationStore",
    "RoutePublisher",
    "RouteTracker",
    "is_route_label",
    "is_route_projection_comment",
    "project_route_dimensions",
    "route_label",
]


class RouteDeliveryError(RuntimeError):
    """A tracker operation that prevented a Route projection."""


class RouteDeliveryStatus(Enum):
    """The observable state of one Route projection."""

    PUBLISHED = "published"
    PENDING = "pending"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True)
class RouteDeliveryResult:
    """The result of projecting one final Routing resolution."""

    issue: int
    status: RouteDeliveryStatus
    identity: str
    label: str
    detail: str | None = None
    labels: tuple[str, ...] = ()
    incomplete: tuple[str, ...] = ()

    @property
    def published(self) -> bool:
        return self.status is RouteDeliveryStatus.PUBLISHED


@runtime_checkable
class RouteTracker(Protocol):
    """The narrow tracker mechanics a Route projection needs."""

    def issue_comments(self, issue: int) -> Sequence[str]:
        """Return current issue-comment bodies."""
        ...

    def issue_labels(self, issue: int) -> Sequence[str]:
        """Return current issue-label names."""
        ...

    def ensure_label(self, label: str) -> None:
        """Ensure the owned Route label exists without altering other labels."""
        ...

    def replace_route_label(
        self, issue: int, *, remove: Sequence[str], add: Sequence[str]
    ) -> None:
        """Replace only this issue's owned Route-label associations."""
        ...

    def post_issue_comment(self, issue: int, body: str) -> None:
        """Append one issue comment."""
        ...


@dataclass(frozen=True)
class _RouteAssignment:
    issue: int
    model: str | None
    effort: str | None
    context_tier: str
    source: str
    effort_configurable: bool | None = None
    context_capacity: int | None = None

    @classmethod
    def from_resolution(
        cls,
        issue: int,
        resolution: RoutingResolution,
        *,
        context_capacity: int | None = None,
    ) -> "_RouteAssignment":
        if issue < 1:
            raise ValueError("Route publication issue must be a positive number")
        if context_capacity is not None and (
            isinstance(context_capacity, bool)
            or not isinstance(context_capacity, int)
            or context_capacity < 0
        ):
            raise ValueError("context capacity must be a non-negative integer")
        return cls(
            issue=issue,
            model=resolution.model,
            effort=resolution.reasoning_effort,
            context_tier=resolution.context_tier,
            source=resolution.source.value,
            effort_configurable=resolution.effort_configurable,
            context_capacity=context_capacity,
        )

    @property
    def identity(self) -> str:
        payload = json.dumps(
            {
                "context_tier": self.context_tier,
                "effort": self.effort,
                "issue": self.issue,
                "model": self.model,
                "source": self.source,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def route_identity(self) -> str:
        """Identify the Route triple independently of its observational source."""
        payload = json.dumps(
            {
                "context_tier": self.context_tier,
                "effort": self.effort,
                "issue": self.issue,
                "model": self.model,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def projection(self):
        """Exact dimensional labels. Capacity is not part of the route identity."""
        return project_route_dimensions(
            model=self.model,
            effort=self.effort,
            effort_configurable=self.effort_configurable,
            context_capacity=self.context_capacity,
        )

    @property
    def label(self) -> str:
        return self.projection.label

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "context_tier": self.context_tier,
            "effort": self.effort,
            "identity": self.identity,
            "incomplete": list(self.projection.incomplete),
            "issue": self.issue,
            "label": self.label,
            "labels": list(self.projection.labels),
            "model": self.model,
            "source": self.source,
        }
        if self.effort_configurable is not None:
            payload["effort_configurable"] = self.effort_configurable
        if self.context_capacity is not None:
            payload["context_capacity"] = self.context_capacity
        return payload


class RoutePublicationStore:
    """Durable, atomic delivery state derived from final local route records."""

    _locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _process_locks: ClassVar[dict[Path, threading.RLock]] = {}

    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize Route projection state and tracker delivery across Runs."""
        lock = self._process_lock()
        with lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._path.with_name(f".{self._path.name}.lock")
            with lock_path.open("a+", encoding="utf-8") as handle:
                try:
                    import fcntl
                except ModuleNotFoundError:  # pragma: no cover - Windows fallback
                    yield
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _process_lock(self) -> threading.RLock:
        key = self._path.resolve()
        with self._locks_guard:
            return self._process_locks.setdefault(key, threading.RLock())

    def record(self, assignment: _RouteAssignment) -> dict[str, object]:
        """Durably record a new assignment before any tracker operation."""
        state = self._read()
        key = str(assignment.issue)
        existing = state["assignments"].get(key)
        if (
            isinstance(existing, dict)
            and existing.get("identity") == assignment.identity
        ):
            return existing
        entry: dict[str, object] = {
            **assignment.as_dict(),
            "comment": "pending",
            "delivery_attempts": 0,
            "label_delivery": "pending",
            "last_error": None,
            "terminal": False,
        }
        state["assignments"][key] = entry
        self._write(state)
        return entry

    def current(self, issue: int) -> dict[str, object] | None:
        """Return the latest locally recorded assignment for one issue."""
        entry = self._read()["assignments"].get(str(issue))
        return entry if isinstance(entry, dict) else None

    def trusted(self, issue: int) -> _RouteAssignment | None:
        """Return the assignment only when its stored identity still matches.

        A record whose identity does not match its Route is not a reconstruction
        source. Absence is ``None``, not an invented assignment.
        """
        entry = self.current(issue)
        if entry is None:
            return None
        return _assignment_from_record(entry)

    def pending(self) -> tuple[dict[str, object], ...]:
        """Return deliveries that remain incomplete after a prior tracker failure."""
        return tuple(
            entry
            for entry in self._read()["assignments"].values()
            if isinstance(entry, dict)
            and entry.get("terminal") is not True
            and (
                entry.get("comment") != "published"
                or entry.get("label_delivery") != "published"
            )
        )

    def update(
        self,
        issue: int,
        identity: str,
        *,
        comment: str | None = None,
        label_delivery: str | None = None,
        last_error: str | None = None,
    ) -> dict[str, object] | None:
        """Update delivery state only if this remains the latest assignment."""
        state = self._read()
        entry = state["assignments"].get(str(issue))
        if not isinstance(entry, dict) or entry.get("identity") != identity:
            return None
        if comment is not None:
            entry["comment"] = comment
        if label_delivery is not None:
            entry["label_delivery"] = label_delivery
        entry["last_error"] = last_error
        self._write(state)
        return entry

    def failure(
        self,
        issue: int,
        identity: str,
        *,
        comment: str | None = None,
        label_delivery: str | None = None,
        last_error: str,
        max_attempts: int,
    ) -> tuple[dict[str, object] | None, bool]:
        """Record one failed tracker attempt and whether retries are exhausted."""
        state = self._read()
        entry = state["assignments"].get(str(issue))
        if not isinstance(entry, dict) or entry.get("identity") != identity:
            return None, False
        attempts = entry.get("delivery_attempts", 0)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise ValueError("invalid Route publication attempt count")
        attempts += 1
        terminal = attempts >= max_attempts
        entry["delivery_attempts"] = attempts
        entry["terminal"] = terminal
        if comment is not None:
            entry["comment"] = comment
        if label_delivery is not None:
            entry["label_delivery"] = label_delivery
        entry["last_error"] = last_error
        self._write(state)
        return entry, terminal

    def _read(self) -> dict[str, dict[str, dict[str, object]]]:
        if not self._path.exists():
            return {"assignments": {}}
        raw = self._path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("assignments"), dict):
            raise ValueError(f"invalid Route publication store at {self._path}")
        return {"assignments": parsed["assignments"]}

    def _write(self, state: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(state, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, self._path)


class RoutePublisher:
    """Deep output adapter for safe, idempotent Route publication."""

    def __init__(
        self,
        *,
        store: RoutePublicationStore,
        tracker: RouteTracker,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("Route publication max_attempts must be positive")
        self._store = store
        self._tracker = tracker
        self._max_attempts = max_attempts

    def publish(
        self,
        *,
        issue: int,
        resolution: RoutingResolution,
        context_capacity: int | None = None,
    ) -> RouteDeliveryResult:
        """Record then project a final resolution without affecting routing.

        ``context_capacity`` is verified full capacity for this model and tier.
        It is not part of the route identity: learning it later must not look
        like a new assignment. Absence is unverified, not zero.
        """
        assignment = _RouteAssignment.from_resolution(
            issue, resolution, context_capacity=context_capacity
        )
        with self._store.locked():
            entry = self._store.record(assignment)
            return self._deliver(assignment, entry)

    def retry_pending(self) -> tuple[RouteDeliveryResult, ...]:
        """Resume each durable pending delivery without re-deciding any Route."""
        with self._store.locked():
            results: list[RouteDeliveryResult] = []
            for entry in self._store.pending():
                assignment = _assignment_from_record(entry)
                results.append(self._deliver(assignment, entry))
            return tuple(results)

    def _deliver(
        self, assignment: _RouteAssignment, entry: dict[str, object]
    ) -> RouteDeliveryResult:
        if entry.get("identity") != assignment.identity:
            return _delivery_result(assignment, RouteDeliveryStatus.STALE)
        if entry.get("terminal") is True:
            return _delivery_result(
                assignment,
                RouteDeliveryStatus.FAILED,
                detail=str(entry["last_error"]),
            )

        comment_status = str(entry.get("comment"))
        if comment_status != "published":
            marker = route_comment_marker(assignment.identity)
            try:
                comments = self._tracker.issue_comments(assignment.issue)
                if not any(marker in comment for comment in comments):
                    self._tracker.post_issue_comment(
                        assignment.issue, _comment(assignment)
                    )
            except RouteDeliveryError as exc:
                _entry, terminal = self._store.failure(
                    assignment.issue,
                    assignment.identity,
                    comment="failed",
                    last_error=str(exc),
                    max_attempts=self._max_attempts,
                )
                return _delivery_result(
                    assignment,
                    (
                        RouteDeliveryStatus.FAILED
                        if terminal
                        else RouteDeliveryStatus.PENDING
                    ),
                    detail=str(exc),
                )
            updated = self._store.update(
                assignment.issue,
                assignment.identity,
                comment="published",
            )
            if updated is None:
                return _delivery_result(assignment, RouteDeliveryStatus.STALE)
            entry = updated

        if entry.get("label_delivery") != "published":
            try:
                projected = assignment.projection.labels
                # The dimensional set is one association. Remove every owned
                # label, including one the new projection will put back, so a
                # remove that lands and an add that does not cannot leave a
                # single surviving dimension looking like the current route.
                owned = tuple(
                    label
                    for label in self._tracker.issue_labels(assignment.issue)
                    if is_route_label(label)
                )
                for label in projected:
                    self._tracker.ensure_label(label)
                self._tracker.replace_route_label(
                    assignment.issue, remove=owned, add=projected
                )
            except RouteDeliveryError as exc:
                _entry, terminal = self._store.failure(
                    assignment.issue,
                    assignment.identity,
                    label_delivery="failed",
                    last_error=str(exc),
                    max_attempts=self._max_attempts,
                )
                return _delivery_result(
                    assignment,
                    (
                        RouteDeliveryStatus.FAILED
                        if terminal
                        else RouteDeliveryStatus.PARTIAL
                    ),
                    detail=str(exc),
                )
            updated = self._store.update(
                assignment.issue,
                assignment.identity,
                label_delivery="published",
            )
            if updated is None:
                return _delivery_result(assignment, RouteDeliveryStatus.STALE)
            entry = updated

        return _delivery_result(assignment, RouteDeliveryStatus.PUBLISHED)


def _comment(assignment: _RouteAssignment) -> str:
    source = (
        "live-evidence Dynamic route"
        if assignment.source == RoutingSource.DYNAMIC.value
        else "final configured route"
    )
    marker = route_comment_marker(assignment.identity)
    lines = [
        marker,
        "git-loopy recorded a final Routing resolution for this issue.",
        "",
        f"Selected by: {source}.",
        f"- Model: `{_display_value(assignment.model)}`",
        f"- Reasoning effort: `{_display_value(assignment.effort)}`",
        f"- Context tier: `{_display_value(assignment.context_tier)}`",
        "",
        "Provenance: the local `wrapper.pickup.bound` record is canonical.",
    ]
    if assignment.context_capacity is not None:
        lines.insert(
            -2,
            "- Context capacity: "
            f"`{_display_value(format_context_capacity(assignment.context_capacity))}`",
        )
    if assignment.source == RoutingSource.DYNAMIC.value:
        lines.append(
            "Evidence: [Artificial Analysis](https://artificialanalysis.ai/) "
            "is retained in the local `wrapper.routing.resolved` record."
        )
    return "\n".join(lines)


def _display_value(value: str | None) -> str:
    """Keep exact scalar values from opening Markdown or HTML constructs."""
    return (
        json.dumps(value, ensure_ascii=True)
        .replace("`", "\\u0060")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _assignment_from_record(record: dict[str, object]) -> _RouteAssignment:
    """Rebuild a Route projection solely from its durable local record."""
    issue = record.get("issue")
    model = record.get("model")
    effort = record.get("effort")
    context_tier = record.get("context_tier")
    source = record.get("source")
    if (
        isinstance(issue, bool)
        or not isinstance(issue, int)
        or issue < 1
        or (model is not None and not isinstance(model, str))
        or (effort is not None and not isinstance(effort, str))
        or not isinstance(context_tier, str)
        or not isinstance(source, str)
    ):
        raise ValueError("invalid Route publication record")
    effort_configurable = record.get("effort_configurable", None)
    context_capacity = record.get("context_capacity", None)
    if effort_configurable is not None and not isinstance(effort_configurable, bool):
        raise ValueError("invalid Route publication record")
    if context_capacity is not None and (
        isinstance(context_capacity, bool)
        or not isinstance(context_capacity, int)
        or context_capacity < 0
    ):
        raise ValueError("invalid Route publication record")
    assignment = _RouteAssignment(
        issue=issue,
        model=model,
        effort=effort,
        context_tier=context_tier,
        source=source,
        effort_configurable=effort_configurable,
        context_capacity=context_capacity,
    )
    if record.get("identity") != assignment.identity:
        raise ValueError("Route publication record identity does not match its Route")
    return assignment


def _delivery_result(
    assignment: _RouteAssignment,
    status: RouteDeliveryStatus,
    *,
    detail: str | None = None,
) -> RouteDeliveryResult:
    """One delivery observation, including an incomplete but landed projection."""
    projection = assignment.projection
    if detail is None and projection.incomplete and status is RouteDeliveryStatus.PUBLISHED:
        detail = "incomplete projection: " + ", ".join(projection.incomplete)
    return RouteDeliveryResult(
        assignment.issue,
        status,
        assignment.identity,
        projection.label,
        detail,
        projection.labels,
        projection.incomplete,
    )
