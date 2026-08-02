"""Serial fixed-frontier Dispatch inside the Python Runner (#264).

§9 decides *whether* one Action may be dispatched: `reconcile` returns at most one
`DispatchAuthorization` or exactly one typed stop. It never executes one. This
module is the Runner half --- the Run whose selection is the frozen frontier
rather than the Pool --- and it is deliberately the only place in the family that
turns an authorization into a session.

Three properties shape everything here:

- **The freeze happens once, before dispatch.** One initial stable Reconciliation
  supplies the coverage, the grants, the Action identities and the semantic
  fingerprints, and every later Reconciliation in the Run replays them. A Run that
  recomputed authority from whatever arrived next would let work published
  mid-Run authorize itself.
- **One Action per session, and the session cannot chain.** The Performer is handed
  one Instruction and one safety case. It is never told what comes next, because
  what comes next is a decision §9 makes after observing what this Dispatch did.
- **Nothing here widens.** Every function may remove an Action from consideration
  and none may add one. That is why the preflight refuses a ceiling this
  distribution cannot enforce instead of accepting and ignoring it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from git_loopy.continuation import (
    AUTOMATION_STOP_REASONS,
    DISPATCH_EVIDENCE_CLASSES,
    HUMAN_BOUNDARY_REASONS,
    CapabilityUnsupported,
)
from git_loopy.events import (
    WRAPPER_CONTINUATION_DISPATCH_ENDED,
    WRAPPER_CONTINUATION_DISPATCH_STARTED,
    WRAPPER_CONTINUATION_STOPPED,
)

#: The one Continuation mode this module serves.
MODE = "execute-frontier"

#: The Instruction modes this distribution has a handler for. The Runner drives a
#: noninteractive Copilot session, so a canonical Skill is something it can
#: genuinely execute; `command` and `manual` are not. §9 reads the posture as a
#: closed world, so the claim is made narrowly and explicitly --- silence there is
#: read as universal competence.
HANDLED_INSTRUCTION_MODES: tuple[str, ...] = ("skill",)

#: §10 ceiling axes this distribution cannot enforce. §9 derives eligibility from
#: coverage, grants and Performer posture; it has no input for an Action-kind or
#: Target cap. An operator who capped kinds and got a Run that dispatched every
#: kind would have been told their authority was narrower than it was.
UNENFORCEABLE_CEILINGS: tuple[str, ...] = ("action_kinds", "targets")

#: What one Dispatch may report back. `complete` and `failed` are ordinary: they
#: stay in the Runner's own artifacts, Events, retry and Strike paths. The other
#: two are the *only* recordable Continuation boundaries, and they are neither
#: retried nor counted as Strikes --- they are written down for a human.
DISPATCH_OUTCOMES: frozenset[str] = frozenset(
    {"complete", "failed"} | set(DISPATCH_EVIDENCE_CLASSES)
)

#: Outcomes that produce durable Dispatch evidence rather than Runner accounting.
BOUNDARY_OUTCOMES: frozenset[str] = frozenset(DISPATCH_EVIDENCE_CLASSES)

#: The one stop reason that means the work is finished. Everything else is a
#: nonterminal boundary that has to say what it is waiting for.
TERMINAL_STOP_REASON = "workstreams-terminal"

#: The stop that says nothing in the frozen frontier is waiting on a Prerequisite
#: that could still become satisfied by the Dispatch this Run just performed.
_BLOCKED_STOP_REASON = "awaiting-prerequisites"

Reconcile = Callable[[dict[str, Any]], Mapping[str, Any]]
Perform = Callable[["Dispatch"], "DispatchOutcome"]
RecordEvidence = Callable[[dict[str, Any]], Any]
Emit = Callable[..., None]


@dataclass(frozen=True)
class Performer:
    """The one noninteractive identity this Run dispatches as.

    `satisfied_requirements` is what the Runner can honestly claim it already
    holds, in §9's typed `(kind, name)` vocabulary. It is supplied by the caller
    rather than assumed here, because the only thing this module knows about the
    host is what it was told.
    """

    id: str
    instruction_modes: tuple[str, ...]
    satisfied_requirements: tuple[tuple[str, str], ...] = ()

    def as_posture(self) -> dict[str, Any]:
        """Render the closed-world posture §9 validates."""
        return {
            "noninteractive": True,
            "satisfied_requirements": [
                {"kind": kind, "name": name}
                for kind, name in sorted(self.satisfied_requirements)
            ],
            "instruction_modes": list(self.instruction_modes),
        }


@dataclass(frozen=True)
class FrontierPlan:
    """One Run's resolved execute-frontier posture, fixed before any dispatch."""

    performer: Performer
    repositories: tuple[str, ...]
    trusted_producers: tuple[str, ...]
    effect_kinds: tuple[str, ...]


def plan_frontier(
    authority: Mapping[str, Any],
    *,
    satisfied_requirements: tuple[tuple[str, str], ...] = (),
) -> FrontierPlan:
    """Turn a resolved §10 authority into the posture this Run will dispatch with.

    Fails closed rather than mid-flight. A Run that discovered at the moment it had
    to record a safety-case violation that it had no actor to write as would lose
    the one record a human needs.
    """
    ceilings = authority.get("ceilings") or {}

    actor = authority.get("actor")
    if not actor:
        # Dispatch evidence is bound to its Performer at both ends:
        # `record-dispatch-result` requires the authenticated actor to be the
        # Performer the record names. §10 makes `actor` optional because report
        # mode never writes; an execute-frontier Run does.
        raise CapabilityUnsupported(
            "continuation mode execute-frontier requires a configured actor"
        )

    for axis in UNENFORCEABLE_CEILINGS:
        if ceilings.get(axis):
            raise CapabilityUnsupported(
                f"continuation ceiling {axis} cannot be enforced by this distribution"
            )

    declared_modes = tuple(ceilings.get("instruction_modes") or ())
    # A ceiling narrows the closed world; it never adds a handler to it.
    modes = tuple(
        mode
        for mode in HANDLED_INSTRUCTION_MODES
        if not declared_modes or mode in declared_modes
    )
    if not modes:
        raise CapabilityUnsupported(
            "continuation ceiling instruction_modes excludes every Instruction "
            "mode this distribution handles"
        )

    return FrontierPlan(
        performer=Performer(
            id=str(actor),
            instruction_modes=modes,
            satisfied_requirements=tuple(sorted(satisfied_requirements)),
        ),
        repositories=tuple(ceilings.get("repositories") or ()),
        trusted_producers=tuple(authority.get("trusted_producers") or ()),
        effect_kinds=tuple(ceilings.get("effect_scopes") or ()),
    )


# ---------------------------------------------------------------------------
# One Dispatch: what the Performer is handed, and what it may report back
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dispatch:
    """One authorization, bound to one noninteractive Performer session.

    There is no successor field, and that absence is the design: a session that
    could read what comes next could decide to run it, and what comes next is a
    decision §9 makes *after* observing what this Dispatch did.
    """

    repository: str
    action_identity: str
    semantic_fingerprint: str
    performer: str
    kind: str
    summary: str
    instruction: Mapping[str, Any]
    workstream_anchor: Mapping[str, Any]
    target: Mapping[str, Any]
    carrier: Mapping[str, Any]
    safety_case_version: str
    completion_condition: Mapping[str, Any]
    effects: tuple[tuple[str, str], ...]
    requirements: tuple[tuple[str, str], ...]
    retry: Mapping[str, Any]
    triggers: tuple[str, ...]
    #: Always true, and never a parameter. A Run that could be asked to become
    #: interactive is a Run that can block forever on a prompt nobody will see.
    noninteractive: bool = field(default=True, init=False)


@dataclass(frozen=True)
class DispatchOutcome:
    """What one Dispatch reports back, in the only four shapes there are.

    A boundary outcome is validated *here*, where it is built, rather than where
    it is written: an outcome that failed halfway through the durable write would
    leave the one record a human needs half-made.
    """

    outcome: str
    summary: str = ""
    evidence: tuple[Mapping[str, Any], ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in DISPATCH_OUTCOMES:
            raise ValueError(f"unknown Dispatch outcome: {self.outcome}")
        if self.outcome not in BOUNDARY_OUTCOMES:
            if self.reason is not None:
                raise ValueError(
                    "a Dispatch reason belongs only to a safety-case violation"
                )
            return
        if not self.summary or "\n" in self.summary or "\r" in self.summary:
            raise ValueError("a recordable Dispatch boundary needs a one-line summary")
        if not self.evidence:
            raise ValueError("a recordable Dispatch boundary needs durable evidence")
        if self.outcome == "safety-case-violation":
            if self.reason not in HUMAN_BOUNDARY_REASONS:
                raise ValueError(
                    "a safety-case violation needs a typed human-boundary reason"
                )
        elif self.reason is not None:
            raise ValueError(
                "a Dispatch reason belongs only to a safety-case violation"
            )

    @property
    def recordable(self) -> bool:
        return self.outcome in BOUNDARY_OUTCOMES


@dataclass(frozen=True)
class DispatchRecord:
    """What one Dispatch did, in the terms the Run reports rather than executes."""

    action_identity: str
    semantic_fingerprint: str
    outcome: str
    duration_ms: int
    evidence_recorded: bool


@dataclass(frozen=True)
class FrontierRun:
    """One repository's serial fixed-frontier Run, after it stopped.

    `stop` is `None` for exactly one reason: an ordinary execution failure is not
    a Continuation stop. It belongs to the Runner's own Strike and retry
    accounting, and inventing a typed §9 reason for it would put a Runner problem
    into the vocabulary an operator reads as a statement about their project.
    """

    repository: str
    frontier: tuple[Mapping[str, str], ...]
    dispatches: tuple[DispatchRecord, ...]
    stop: Mapping[str, Any] | None
    reconciliations: int
    refreshes: int
    execution_failed: bool

    @property
    def terminal(self) -> bool:
        return bool(self.stop) and self.stop.get("reason") == TERMINAL_STOP_REASON


class FrontierDriver:
    """Drive one repository's frozen frontier to a single typed stop.

    Every seam is injected --- the Reconciler, the Performer, the evidence writer,
    the Event fan-out --- because every property worth pinning about this class is
    about *sequencing and authority*, not about GitHub.
    """

    def __init__(
        self,
        plan: FrontierPlan,
        *,
        trusted_producers: Sequence[str] | None = None,
        reconcile: Reconcile,
        perform: Perform,
        record_evidence: RecordEvidence | None = None,
        emit: Emit = lambda *_args, **_kwargs: None,
        clock: Callable[[], float] = time.monotonic,
        on_guidance: Callable[[str], None] | None = None,
        diagnose: Callable[[str], None] | None = None,
        concurrency: int = 1,
    ) -> None:
        if concurrency != 1:
            # Serial Dispatch is not a step towards concurrency a caller may
            # extrapolate from, so an ask for it is refused rather than quietly
            # honoured one at a time: `concurrent_dispatch` is advertised false.
            raise CapabilityUnsupported(
                "continuation concurrent Dispatch is unsupported by this distribution"
            )
        self._plan = plan
        self._producers = tuple(
            trusted_producers
            if trusted_producers is not None
            else plan.trusted_producers
        )
        self._reconcile = reconcile
        self._perform = perform
        self._record_evidence = record_evidence
        self._emit = emit
        self._clock = clock
        self._on_guidance = on_guidance
        self._diagnose = diagnose
        self._revocations: set[tuple[str, str]] = set()
        self._satisfied: set[tuple[str, str]] = set(
            plan.performer.satisfied_requirements
        )

    # -- runtime narrowing --------------------------------------------------

    def revoke(self, kind: str, scope: str) -> None:
        """Withdraw one execution grant for the rest of the Run.

        Immediate by construction: the revocation rides on the *next*
        Reconciliation, which is the one that decides the next Dispatch. Effects
        already authorized are left alone --- a Run that tried to undo them would
        be performing an Action nobody published.
        """
        self._revocations.add((kind, scope))

    def withdraw_requirement(self, kind: str, name: str) -> None:
        """Stop claiming one capability or access this Run no longer holds."""
        self._satisfied.discard((kind, name))

    # -- the Run ------------------------------------------------------------

    def run_all(self, *, iter_num: int | None = None) -> tuple[FrontierRun, ...]:
        """Drive every repository in the frozen coverage, one at a time."""
        return tuple(
            self.run(repository, iter_num=iter_num)
            for repository in self._plan.repositories
        )

    def run(self, repository: str, *, iter_num: int | None = None) -> FrontierRun:
        """Freeze once, then dispatch serially until exactly one stop is reached."""
        observed = self._observe(repository)
        frontier = _freeze_frontier(observed, repositories=self._plan.repositories)
        grants = _freeze_grants(
            observed,
            repositories=self._plan.repositories,
            effect_kinds=self._plan.effect_kinds,
        )
        actions = {action["identity"]: action for action in _actions(observed)}

        dispatched: list[str] = []
        records: list[DispatchRecord] = []
        prior: Mapping[str, Any] | None = None
        reconciliations = 1
        refreshes = 0
        refreshed = False

        while True:
            answer = self._reconcile(
                self._request(
                    repository,
                    frontier=frontier,
                    grants=grants,
                    dispatched=dispatched,
                    prior=prior,
                )
            )
            reconciliations += 1
            automation = _automation(answer)
            prior = automation["scope"]
            actions.update({action["identity"]: action for action in _actions(answer)})

            authorization = automation.get("authorization")
            if authorization is not None:
                dispatch = _bind(
                    authorization,
                    action=actions[authorization["action_identity"]],
                    repository=repository,
                )
                record = self._dispatch(
                    dispatch, index=len(records) + 1, iter_num=iter_num
                )
                records.append(record)
                dispatched.append(dispatch.action_identity)
                refreshed = False
                if record.outcome == "failed":
                    # Not a Continuation stop. The Runner owns what happens to an
                    # ordinary execution failure, including whether to retry it.
                    return FrontierRun(
                        repository=repository,
                        frontier=frontier,
                        dispatches=tuple(records),
                        stop=None,
                        reconciliations=reconciliations,
                        refreshes=refreshes,
                        execution_failed=True,
                    )
                continue

            stop = automation["stop"]
            if stop["reason"] == _BLOCKED_STOP_REASON and not refreshed:
                # Only Blocked work is left, and the Dispatch that just ran may be
                # the very thing that satisfies one of those Prerequisites. One
                # more stable Reconciliation, then the Run believes the answer.
                refreshed = True
                refreshes += 1
                continue

            self._stopped(
                stop,
                repository=repository,
                dispatched=dispatched,
                iter_num=iter_num,
            )
            return FrontierRun(
                repository=repository,
                frontier=frontier,
                dispatches=tuple(records),
                stop=stop,
                reconciliations=reconciliations,
                refreshes=refreshes,
                execution_failed=False,
            )

    # -- one Reconciliation -------------------------------------------------

    def _observe(self, repository: str) -> Mapping[str, Any]:
        """The initial stable Reconciliation, taken before any authority exists.

        Deliberately read-only and `automation`-free. The grants a Run dispatches
        under are derived from what this observation *already* contains, so asking
        for an authorization here would be asking a question whose answer has not
        been computed yet.
        """
        return self._reconcile(
            {
                "repository": repository,
                "trusted_producers": list(self._producers),
            }
        )

    def _request(
        self,
        repository: str,
        *,
        frontier: tuple[Mapping[str, str], ...],
        grants: tuple[tuple[str, str], ...],
        dispatched: Sequence[str],
        prior: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        scope: dict[str, Any] = {
            # One already-narrowed ceiling: §10 resolved the operator's three
            # tiers into this Run's authority before the Runner ever reconciled,
            # and re-declaring the tiers here would give the family a second
            # narrowing implementation to disagree with.
            "ceilings": [
                {
                    "source": "project",
                    "coverage": {"repositories": list(self._plan.repositories)},
                    "grants": [
                        {"kind": kind, "scope": value} for kind, value in grants
                    ],
                    "denials": [],
                }
            ],
            "revocations": [
                {"kind": kind, "scope": value}
                for kind, value in sorted(self._revocations)
            ],
        }
        if prior is not None:
            scope["prior"] = {
                "coverage": prior["coverage"],
                "grants": prior["grants"],
                "denials": prior["denials"],
            }
        return {
            "repository": repository,
            "trusted_producers": list(self._producers),
            "automation": {
                "performer": {
                    "id": self._plan.performer.id,
                    "posture": Performer(
                        id=self._plan.performer.id,
                        instruction_modes=self._plan.performer.instruction_modes,
                        satisfied_requirements=tuple(sorted(self._satisfied)),
                    ).as_posture(),
                },
                "scope": scope,
                "frontier": {"actions": [dict(entry) for entry in frontier]},
                "dispatched": list(dispatched),
            },
        }

    # -- one Dispatch -------------------------------------------------------

    def _dispatch(
        self, dispatch: Dispatch, *, index: int, iter_num: int | None
    ) -> DispatchRecord:
        self._emit(
            WRAPPER_CONTINUATION_DISPATCH_STARTED,
            **_started_payload(dispatch, index=index, iter_num=iter_num),
        )
        started = self._clock()
        outcome = self._perform(dispatch)
        duration_ms = int((self._clock() - started) * 1000)
        recorded = self._record(dispatch, outcome)
        record = DispatchRecord(
            action_identity=dispatch.action_identity,
            semantic_fingerprint=dispatch.semantic_fingerprint,
            outcome=outcome.outcome,
            duration_ms=duration_ms,
            evidence_recorded=recorded,
        )
        self._emit(
            WRAPPER_CONTINUATION_DISPATCH_ENDED,
            **_ended_payload(
                dispatch, outcome, record, index=index, iter_num=iter_num
            ),
        )
        return record

    def _record(self, dispatch: Dispatch, outcome: DispatchOutcome) -> bool:
        """Write durable Dispatch evidence for the two exceptional classes only.

        A failure to write is reported and dropped. The boundary already happened;
        losing the record of it must not manufacture a second, different failure
        on top of the one a human still has to read about.
        """
        if not outcome.recordable or self._record_evidence is None:
            return False
        record = {
            "action_identity": dispatch.action_identity,
            "semantic_fingerprint": dispatch.semantic_fingerprint,
            "performer": dispatch.performer,
            "carrier": dict(dispatch.carrier),
            "class": outcome.outcome,
            "summary": outcome.summary,
            "evidence": [dict(entry) for entry in outcome.evidence],
        }
        if outcome.reason is not None:
            record["reason"] = outcome.reason
        try:
            self._record_evidence(record)
        except Exception as exc:  # noqa: BLE001 - a lost record is not a new failure
            if self._diagnose is not None:
                self._diagnose(
                    f"continuation dispatch evidence was not recorded: "
                    f"{type(exc).__name__}: {exc}"
                )
            return False
        return True

    # -- the stop -----------------------------------------------------------

    def _stopped(
        self,
        stop: Mapping[str, Any],
        *,
        repository: str,
        dispatched: Sequence[str],
        iter_num: int | None,
    ) -> None:
        self._emit(
            WRAPPER_CONTINUATION_STOPPED,
            **_stopped_payload(
                stop,
                repository=repository,
                performer=self._plan.performer.id,
                dispatched=dispatched,
                iter_num=iter_num,
            ),
        )
        if self._on_guidance is not None:
            self._on_guidance(_render(stop, repository=repository))


# ---------------------------------------------------------------------------
# The freeze
# ---------------------------------------------------------------------------


def _freeze_frontier(
    observed: Mapping[str, Any], *, repositories: Sequence[str]
) -> tuple[Mapping[str, str], ...]:
    """Freeze every in-coverage identity and fingerprint, Blocked ones included.

    A Blocked member left out of the freeze would look like a newcomer the moment
    its Prerequisites were satisfied, and a newcomer is never dispatched.
    """
    coverage = set(repositories)
    return tuple(
        {
            "identity": str(action["identity"]),
            "semantic_fingerprint": str(action["semantic_fingerprint"]),
        }
        for action in _actions(observed)
        if _repositories(action) <= coverage
    )


def _freeze_grants(
    observed: Mapping[str, Any],
    *,
    repositories: Sequence[str],
    effect_kinds: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Derive this Run's execution grants, once, from the frozen frontier.

    §10 caps effect *kinds*, because a scope is not enumerable in a configuration
    file; §9 authorizes bounded `(kind, scope)` pairs. The bridge is the frozen
    frontier itself: the Run grants exactly the pairs its own frozen, in-coverage
    safety cases already declared, keeping only the kinds the operator allowed.

    That is a narrowing in both directions. Nothing a Producer publishes after the
    freeze can add a scope, and no kind the operator withheld survives --- so an
    operator who allowed no effect kind at all grants nothing and dispatches
    nothing, which is the honest reading of an empty positive allowlist.
    """
    coverage = set(repositories)
    allowed = set(effect_kinds)
    grants = {
        (str(effect["kind"]), str(effect["scope"]))
        for action in _actions(observed)
        if _repositories(action) <= coverage
        for effect in _sequence((action.get("safety_case") or {}).get("effects"))
        if str(effect.get("kind")) in allowed
    }
    return tuple(sorted(grants))


def _bind(
    authorization: Mapping[str, Any],
    *,
    action: Mapping[str, Any],
    repository: str,
) -> Dispatch:
    """Bind one authorization and its Action to one noninteractive session."""
    producer = action.get("producer")
    producer = producer if isinstance(producer, Mapping) else {}
    return Dispatch(
        repository=repository,
        action_identity=str(authorization["action_identity"]),
        semantic_fingerprint=str(authorization["semantic_fingerprint"]),
        performer=str(authorization["performer"]),
        kind=str(action.get("kind", "")),
        summary=str(action.get("summary", "")),
        instruction=dict(action["instruction"]),
        workstream_anchor=dict(authorization["workstream_anchor"]),
        target=dict(authorization["target"]),
        # Dispatch evidence is written onto the Producer carrier that published
        # the Action, which is where a human looking for the Action will look.
        carrier=dict(producer.get("carrier") or authorization["workstream_anchor"]),
        safety_case_version=str(authorization["safety_case_version"]),
        completion_condition=dict(authorization["completion_condition"]),
        effects=tuple(
            (str(entry["kind"]), str(entry["scope"]))
            for entry in _sequence(authorization.get("effects"))
        ),
        requirements=tuple(
            (str(entry["kind"]), str(entry["name"]))
            for entry in _sequence(authorization.get("requirements"))
        ),
        retry=dict(authorization["retry"]),
        triggers=tuple(
            str(entry["kind"]) for entry in _sequence(authorization.get("triggers"))
        ),
    )


# ---------------------------------------------------------------------------
# Event projections
#
# Built by *naming* what survives rather than by removing what does not. An
# Event stream is archived, shipped to a Dashboard and read by people who hold
# none of the authority a runnable Instruction represents, so a field added to
# the contract tomorrow cannot leak through a redactor nobody remembered to
# update. The Instruction *mode* survives and its value never does.
# ---------------------------------------------------------------------------


def _started_payload(
    dispatch: Dispatch, *, index: int, iter_num: int | None = None
) -> dict[str, Any]:
    return {
        "iter_num": iter_num,
        "mode": MODE,
        "repository": dispatch.repository,
        "performer": dispatch.performer,
        "dispatch_index": index,
        "action_identity": dispatch.action_identity,
        "semantic_fingerprint": dispatch.semantic_fingerprint,
        "kind": dispatch.kind,
        "instruction_mode": str(dispatch.instruction.get("mode", "")),
        "safety_case_version": dispatch.safety_case_version,
        "retry": str(dispatch.retry.get("kind", "")),
        "effects": [
            {"kind": kind, "scope": scope} for kind, scope in dispatch.effects
        ],
        "requirements": [
            {"kind": kind, "name": name} for kind, name in dispatch.requirements
        ],
        "triggers": list(dispatch.triggers),
        "target": dict(dispatch.target),
        "workstream_anchor": dict(dispatch.workstream_anchor),
        "noninteractive": True,
    }


def _ended_payload(
    dispatch: Dispatch,
    outcome: DispatchOutcome,
    record: DispatchRecord,
    *,
    index: int,
    iter_num: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "iter_num": iter_num,
        "mode": MODE,
        "repository": dispatch.repository,
        "performer": dispatch.performer,
        "dispatch_index": index,
        "action_identity": dispatch.action_identity,
        "semantic_fingerprint": dispatch.semantic_fingerprint,
        "outcome": record.outcome,
        "boundary": outcome.recordable,
        "evidence_recorded": record.evidence_recorded,
        "duration_ms": record.duration_ms,
    }
    if outcome.reason is not None:
        payload["reason"] = outcome.reason
    return payload


def _stopped_payload(
    stop: Mapping[str, Any],
    *,
    repository: str,
    performer: str,
    dispatched: Sequence[str],
    iter_num: int | None = None,
) -> dict[str, Any]:
    """State the whole nonterminal account, in §9's own typed vocabulary."""
    reason = str(stop.get("reason", ""))
    payload: dict[str, Any] = {
        "iter_num": iter_num,
        "mode": MODE,
        "repository": repository,
        "performer": performer,
        "disposition": str(stop.get("disposition", "")),
        "reason": reason if reason in AUTOMATION_STOP_REASONS else "",
        "terminal": reason == TERMINAL_STOP_REASON,
        "nonterminal_status": str(stop.get("nonterminal_status", "")),
        "secondary_barriers": [
            {
                "identity": str(entry.get("identity", "")),
                "reasons": [str(item) for item in _strings(entry.get("reasons"))],
            }
            for entry in _sequence(stop.get("secondary_barriers"))
        ],
        "report_only_successors": [
            {
                "identity": str(entry.get("identity", "")),
                "semantic_fingerprint": str(entry.get("semantic_fingerprint", "")),
                "reason": str(entry.get("reason", "")),
            }
            for entry in _sequence(stop.get("report_only_successors"))
        ],
        "evidence": [dict(entry) for entry in _sequence(stop.get("evidence"))],
        "dispatched": list(dispatched),
        "successor_executed": False,
        "statement": str(stop.get("statement", "")),
    }
    following = stop.get("next")
    if isinstance(following, Mapping):
        # The one place prose survives: a stop exists to be acted on by a person,
        # and "the primary next Action" is not actionable as a bare digest.
        payload["next"] = {
            "identity": str(following.get("identity", "")),
            "summary": str(following.get("summary", "")),
            "readiness": str(following.get("readiness", "")),
        }
        condition = following.get("condition")
        if isinstance(condition, Mapping):
            payload["next"]["condition"] = str(condition.get("kind", ""))
    return payload


def _render(stop: Mapping[str, Any], *, repository: str) -> str:
    """One operator-facing line per stop, in the locked vocabulary."""
    line = (
        f"git-loopy continuation (execute-frontier, {repository}): "
        f"{stop.get('disposition', 'unknown')}; {stop.get('reason', 'unknown')}"
    )
    if stop.get("reason") != TERMINAL_STOP_REASON:
        line += f"; status {stop.get('nonterminal_status') or 'unknown'}"
        following = stop.get("next")
        if isinstance(following, Mapping):
            line += f"; next {following.get('summary') or following.get('identity')}"
        barriers = len(_sequence(stop.get("secondary_barriers")))
        successors = len(_sequence(stop.get("report_only_successors")))
        line += f"; {barriers} secondary barrier(s); {successors} report-only successor(s)"
    return line + ". No successor Action was executed."


# ---------------------------------------------------------------------------
# Reading one Reconciliation answer
# ---------------------------------------------------------------------------


def _result(answer: Mapping[str, Any]) -> Mapping[str, Any]:
    result = answer.get("result")
    if not isinstance(result, Mapping):
        raise CapabilityUnsupported("continuation reconcile returned no result")
    return result


def _automation(answer: Mapping[str, Any]) -> Mapping[str, Any]:
    automation = _result(answer).get("automation")
    if not isinstance(automation, Mapping):
        raise CapabilityUnsupported(
            "continuation reconcile returned no Automation projection"
        )
    return automation


def _actions(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _sequence(_result(answer).get("actions"))


def _repositories(action: Mapping[str, Any]) -> set[str]:
    return {
        str(reference["repository"])
        for reference in (action.get("workstream_anchor"), action.get("target"))
        if isinstance(reference, Mapping) and "repository" in reference
    }


def _sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [entry for entry in value if isinstance(entry, Mapping)]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [str(entry) for entry in value]
