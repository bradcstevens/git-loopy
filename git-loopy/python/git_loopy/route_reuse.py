"""Derive **Reusable routes** from this clone's canonical Run event history.

The reading half of ADR-0057's cross-Run reuse (#565).  The decision itself
lives in :mod:`git_loopy.dynamic_route`; what lives here is where a
:class:`~git_loopy.dynamic_route.ReusableRoute` *comes from*, which the ADR
settles and this module makes checkable: the append-only Run event logs under
``.git-loopy/logs/``, and nothing else.  Not a tracker label, not a comment,
not a committed table, not a second store the Runner would then have to keep
agreeing with the logs.

Separate from :mod:`git_loopy.loop` for the reason :mod:`git_loopy.routing_input`
is: *where a reusable route may come from* is a rule, and a rule that only
exists inside a 6,000-line orchestrator is a rule nobody can check.  The two
modules are the same shape — each projects an outside artifact into a
``dynamic_route`` type — and this one is deliberately stdlib-only beside that
single import, because a derivation that reached for Config or a tracker could
quietly acquire a second source of truth.

Nothing here validates anything.  A record read back is a *claim* that some
earlier Run elected a route under some verified input identity; it becomes a
route only after :meth:`~git_loopy.dynamic_route.DynamicRouter.rebind` re-reads
both live sources and finds them still saying so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from git_loopy.dynamic_route import ReusableRoute, WorkRoute

__all__ = [
    "MAX_HISTORY_LOGS",
    "MAX_ROUTES_PER_ISSUE",
    "ROUTING_RESOLVED_TYPE",
    "ReusableRouteHistory",
    "read_reusable_routes",
    "reusable_route",
]

#: The Event a reusable route is derived from.  Spelled here rather than
#: imported from :mod:`git_loopy.events` to keep this module's import surface
#: the single ``dynamic_route`` edge its docstring claims; the literal is
#: pinned against ``events.WRAPPER_ROUTING_RESOLVED`` by this module's tests.
ROUTING_RESOLVED_TYPE = "wrapper.routing.resolved"

#: How many Run logs one derivation reads, newest first.  A clone accumulates
#: one log per Run forever, and a Pickup that had to scan all of them would get
#: slower for every Run that ever happened — which is a strange way to pay for
#: an optimisation.  Newest first because a route elected long ago is the one
#: least likely to still match current evidence.
MAX_HISTORY_LOGS = 64

#: How many records one issue keeps, newest first.  More than one because an
#: issue worked twice in a Run leaves two records with two verified input
#: identities and the *newest* is the retry — which a later Run's first
#: **Pickup**, whose attempt history is empty again, can never match.  Keeping
#: only the last one would therefore switch reuse off for exactly the issues
#: that have been worked most.
MAX_ROUTES_PER_ISSUE = 8

#: The envelope key every Event carries, and the payload keys a projection
#: needs.  A record missing any of them is *unusable* rather than fatal: the
#: Event schema is append-only and a log written before #565 carries no
#: ``relevant_input_identity``, which is an older Runner rather than a broken
#: one.  Unusable means "decide afresh", which is always available.
_REQUIRED_KEYS = (
    "issue",
    "proposal_id",
    "model",
    "context_tier",
    "summary",
    "selector_model",
    "selector_context_tier",
    "relevant_input_identity",
    "validated_at",
)


@dataclass(frozen=True)
class ReusableRouteHistory:
    """What this clone's Run logs say about routes already elected.

    Carries its own failures rather than raising them, because a history that
    could not be read is not an error a **Pickup** should die of — it is the
    ordinary case of having nothing to reuse, which every Run before this one
    was already in.  It is still *reported*: AC4 asks corruption to be
    diagnosed, and a silent empty history and a silently unreadable one are the
    same value with very different causes.
    """

    routes: Mapping[str, tuple[ReusableRoute, ...]] = field(default_factory=dict)
    unreadable: tuple[str, ...] = ()
    unusable: tuple[str, ...] = ()

    def for_issue(self, ref: int | str) -> tuple[ReusableRoute, ...]:
        """Every reusable record for ``ref``, newest first."""
        return self.routes.get(_issue_key(ref), ())

    @property
    def diagnosis(self) -> str | None:
        """What went wrong reading the history, or ``None`` when nothing did."""
        parts: list[str] = []
        if self.unusable:
            # Named, not merely counted: a diagnosis that says a record could
            # not be read back without saying which log holds it leaves the
            # operator paying for an assessment every Run with nothing to go
            # and look at.
            parts.append(
                f"{len(self.unusable)} routing record(s) could not be read back "
                f"for reuse, in: {', '.join(sorted(set(self.unusable)))}"
            )
        if self.unreadable:
            parts.append(f"unreadable Run log(s): {', '.join(self.unreadable)}")
        return "; ".join(parts) if parts else None


def read_reusable_routes(
    logs_dir: Path, *, exclude: Path | None = None
) -> ReusableRouteHistory:
    """Project this clone's Run logs into the routes a later Run may revalidate.

    Args:
        logs_dir: ``.git-loopy/logs``. An absent directory is an empty history,
            not a failure: a first Run in a fresh clone has nothing to reuse.
        exclude: This Run's own log, when it has one. Left out because a Run
            that read its own half-flushed log would be deriving "reusable
            state" from a record it is in the middle of writing, and because
            what *this* Run decided is already in front of it — in memory, at
            full fidelity, without a round trip through JSON.

    Returns:
        The history, with anything it could not read reported rather than
        raised.
    """
    collected: dict[str, list[tuple[tuple[int, int], ReusableRoute]]] = {}
    unreadable: list[str] = []
    unusable: list[str] = []
    for order, path in enumerate(_history_logs(logs_dir, exclude)):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            unreadable.append(path.name)
            continue
        for line_number, line in enumerate(lines):
            if ROUTING_RESOLVED_TYPE not in line:
                continue
            record = _decoded(line)
            if record is _UNPARSEABLE:
                unusable.append(path.name)
                continue
            if record is None:
                continue
            assert isinstance(record, Mapping)
            reusable = reusable_route(record)
            if reusable is None:
                unusable.append(path.name)
                continue
            issue_ref = reusable.issue_ref
            assert issue_ref is not None
            collected.setdefault(_issue_key(issue_ref), []).append(
                ((order, -line_number), reusable)
            )
    routes = {
        key: tuple(
            reusable for _, reusable in sorted(rows, key=lambda row: row[0])
        )[:MAX_ROUTES_PER_ISSUE]
        for key, rows in collected.items()
    }
    return ReusableRouteHistory(
        routes=routes, unreadable=tuple(unreadable), unusable=tuple(unusable)
    )


def _history_logs(logs_dir: Path, exclude: Path | None) -> Iterable[Path]:
    """The newest :data:`MAX_HISTORY_LOGS` Run logs, newest first.

    Ordered by *name* rather than by mtime, because
    :func:`git_loopy.persist.create_writers` stems every artifact with the
    Run's own UTC start instant — so the name is the Run's order, and an mtime
    is merely when the filesystem last touched it.
    """
    try:
        candidates = sorted(logs_dir.glob("*.jsonl"), key=lambda path: path.name)
    except OSError:
        return ()
    excluded = exclude.resolve() if exclude is not None else None
    kept = [
        path
        for path in reversed(candidates)
        if excluded is None or _resolved(path) != excluded
    ]
    return kept[:MAX_HISTORY_LOGS]


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - resolution failure is not a match
        return path


#: A line that named the routing Event and then did not parse.  Distinct from
#: ``None`` — "this line is some other Event" — because the two have opposite
#: diagnoses: one is an ordinary log, the other is the torn tail a killed Run
#: leaves, and AC4 asks corruption to be reported rather than stepped over.
_UNPARSEABLE = object()


def _decoded(line: str) -> Mapping[str, object] | None | object:
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return _UNPARSEABLE
    if not isinstance(record, dict):
        return _UNPARSEABLE
    if record.get("type") != ROUTING_RESOLVED_TYPE:
        return None
    return record


def reusable_route(record: Mapping[str, object]) -> ReusableRoute | None:
    """Project one record, or answer ``None`` where it cannot be trusted whole.

    Whole or not at all: a projection that filled a missing field with a
    plausible default would be inventing the very thing reuse is checked
    against, and the cost of refusing is one ordinary selector call.
    """
    if any(record.get(key) is None for key in _REQUIRED_KEYS):
        return None
    issue = record["issue"]
    if not isinstance(issue, (int, str)) or isinstance(issue, bool):
        return None
    # A record that is itself a revalidation names the decision it descends
    # from, and *that* is what the next Run must point at: chaining each reuse
    # to the one before it would turn "the original decision" (AC3) into a
    # trail to be walked, and would lose the original the moment its log aged
    # out of the window.
    origin_id = record.get("reused_proposal_id")
    origin_at = record.get("reused_validated_at")
    if (origin_id is None) != (origin_at is None):
        return None
    if origin_id is not None and not isinstance(origin_id, str):
        return None
    validated_at = _instant(
        record["validated_at"] if origin_at is None else origin_at
    )
    if validated_at is None:
        return None
    effort = record.get("effort")
    selector_effort = record.get("selector_effort")
    justification = record.get("repeat_justification")
    if not _optional_text(effort) or not _optional_text(selector_effort):
        return None
    if not _optional_text(justification):
        return None
    if not all(
        isinstance(record[key], str)
        for key in (
            "proposal_id",
            "model",
            "context_tier",
            "summary",
            "selector_model",
            "selector_context_tier",
            "relevant_input_identity",
        )
    ):
        return None
    return ReusableRoute(
        issue_ref=issue,
        proposal_id=str(record["proposal_id"] if origin_id is None else origin_id),
        route=WorkRoute(
            model=str(record["model"]),
            reasoning_effort=None if effort is None else str(effort),
            context_tier=str(record["context_tier"]),
        ),
        summary=str(record["summary"]),
        selector_model=str(record["selector_model"]),
        selector_reasoning_effort=(
            None if selector_effort is None else str(selector_effort)
        ),
        selector_context_tier=str(record["selector_context_tier"]),
        relevant_input_identity=str(record["relevant_input_identity"]),
        validated_at=validated_at,
        repeat_justification=(
            None if justification is None else str(justification)
        ),
    )


def _optional_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _instant(value: object) -> datetime | None:
    """Read back an Event timestamp, or refuse a shape nothing here writes."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _issue_key(ref: int | str) -> str:
    """One key for an issue however the Pool names it.

    A GitHub reference is a number and a local-markdown one is a path, so
    ``561`` and ``"561"`` are the same issue arriving through a JSON round trip
    rather than two — the Pool that wrote the record and the Pool reading it
    back are the same Pool.
    """
    return str(ref)
