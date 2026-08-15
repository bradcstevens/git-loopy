"""**Session outcome** — the ending one Agent's session reached, as data (#403).

The loop knew why a session ended and only ever said so in a ``diag`` line, so the
ending was unavailable to anything that would act on it. This module is where that
ending becomes a value: a closed five-member vocabulary, and — where the ending was
a failure — the structured **Session error** identity behind it.

Two axes, deliberately separate:

* :class:`SessionOutcome` is *what ended the session*. Its five members are the five
  rows the attempt lifecycle disposes of, so a disposition never has to fall through
  to a guess.
* :class:`SessionError` is *why a failing session failed*, drawn from the harness's
  own ``session.error`` and ``model.call_failure`` records rather than flattened into
  one string. It is what makes an exhausted quota, a rate limit and an expired
  credential three different facts instead of three ways of spelling "no progress".

**Recording is the whole of the job.** Nothing here decides to abort a Run, to back
off, or to leave an issue alone: an account-level condition is still attributed to
the individual issue whose Iteration met it, exactly as before. Whether quota
deserves a Run-level stop and rate limiting deserves a delay are undecided
questions, and answering them by accident inside a classifier would be the worst
possible place to answer them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from git_loopy import events

__all__ = [
    "SessionError",
    "SessionErrorKind",
    "SessionOutcome",
    "SessionOutcomeRecord",
    "SessionOutcomeWatch",
    "SessionTermination",
    "classify_error_kind",
    "resolve_session_outcome",
    "strongest_error",
]


class SessionOutcome(Enum):
    """The closed vocabulary of endings an Agent's session can reach.

    Exactly five, because these are the rows the per-issue attempt lifecycle
    disposes of. An ending outside the set would arrive at that table with no row,
    and the disposition it fell through to would be a guess about work nobody
    watched.

    ``NO_PROGRESS`` is the *silent* ending: the session ran to the end, said
    nothing about failing, and the Iteration observed no progress from it. It is
    the only member that is a joint fact about the session and the Iteration
    around it; the other four are facts about the session alone.
    """

    NO_PROGRESS = "no_progress"
    TIMEOUT = "timeout"
    CRASH = "crash"
    NO_MORE_TASKS = "no_more_tasks"
    CONTENT_FILTERED = "content_filtered"


class SessionErrorKind(Enum):
    """The closed identity vocabulary of a :class:`SessionError`.

    Named for the *condition*, never for a reaction to it. ``RATE_LIMITED`` says a
    limiter refused the call, not that the Run should wait; ``QUOTA_EXHAUSTED``
    says the account is spent, not that the Run should stop. Whether either
    deserves a reaction is undecided (#403), and a vocabulary that answered it
    would have decided it here by accident.

    ``UNKNOWN`` is a real member rather than a ``None``: the harness reported a
    failure git-loopy could not place, which is a different fact from no failure
    at all, and collapsing the two would hide exactly the failures worth reading.
    """

    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    BAD_REQUEST = "bad_request"
    SERVER_ERROR = "server_error"
    NETWORK = "network"
    UNKNOWN = "unknown"


#: Substrings that name an account that has spent what it was allotted. Matched
#: against the harness's own code, type and message, case-folded. Deliberately
#: consulted *before* the status code: a spent quota is reported as `429` by one
#: endpoint and `403` by another, and the status alone would file the first as
#: rate limiting and the second as an authentication failure.
_QUOTA_MARKERS = (
    "quota",
    "insufficient_credit",
    "out of credit",
    "credit limit",
    "premium request",
    "usage limit",
    "monthly limit",
    "billing",
    "payment required",
    "plan limit",
)


#: Substrings that name a limiter refusing a call the account is entitled to
#: make. Kept apart from the quota markers because the two arrive under the same
#: `429` and mean opposite things to an operator: one clears by waiting, the
#: other does not clear at all.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "throttl",
)

#: Substrings that name a credential the service would not accept. `token` is
#: deliberately absent: the harness spends it on context-window messages far more
#: often than on credentials, and a token-limit message filed as an
#: authentication failure would send an operator to re-authenticate a session
#: that was simply too long.
_AUTHENTICATION_MARKERS = (
    "unauthorized",
    "unauthenticated",
    "authenticat",
    "forbidden",
    "bad credentials",
    "invalid credentials",
    "permission denied",
    "not signed in",
)

#: Substrings that name a transport that never reached the service. A failure
#: here says nothing about the account or the work, which is exactly why it is
#: worth telling apart from both.
_NETWORK_MARKERS = (
    "econnreset",
    "econnrefused",
    "etimedout",
    "enotfound",
    "socket hang up",
    "fetch failed",
    "network error",
    "dns",
)


def classify_error_kind(
    *,
    error_type: str | None = None,
    error_code: str | None = None,
    status_code: int | None = None,
    message: str | None = None,
) -> SessionErrorKind:
    """Place one harness-reported failure in the closed identity vocabulary.

    Pure and total: every failure gets a member, and one it cannot place gets
    :attr:`SessionErrorKind.UNKNOWN` rather than nothing. The harness's free-text
    fields are consulted before its status code because the conditions that matter
    most here — a spent quota above all — are not distinguishable by status.
    """
    haystack = " ".join(
        part.casefold() for part in (error_code, error_type, message) if part
    )
    if any(marker in haystack for marker in _QUOTA_MARKERS):
        return SessionErrorKind.QUOTA_EXHAUSTED
    if any(marker in haystack for marker in _RATE_LIMIT_MARKERS):
        return SessionErrorKind.RATE_LIMITED
    if any(marker in haystack for marker in _AUTHENTICATION_MARKERS):
        return SessionErrorKind.AUTHENTICATION_FAILED
    if any(marker in haystack for marker in _NETWORK_MARKERS):
        return SessionErrorKind.NETWORK
    if status_code is not None:
        if status_code == 402:
            return SessionErrorKind.QUOTA_EXHAUSTED
        if status_code == 429:
            return SessionErrorKind.RATE_LIMITED
        if status_code in (401, 403, 407):
            return SessionErrorKind.AUTHENTICATION_FAILED
        if 500 <= status_code < 600:
            return SessionErrorKind.SERVER_ERROR
        if 400 <= status_code < 500:
            return SessionErrorKind.BAD_REQUEST
    return SessionErrorKind.UNKNOWN


#: The Event types that carry a harness-reported failure (#403). Both are read,
#: because they answer at two scopes: ``model.call_failure`` is per API call and
#: survives a turn the harness retried internally, while ``session.error`` is what
#: the session itself reported as having gone wrong.
_FAILURE_EVENT_TYPES = (events.SESSION_ERROR, events.MODEL_CALL_FAILURE)


@dataclass(frozen=True)
class SessionError:
    """Why a failing session failed, as an identity rather than a sentence.

    Structured on purpose. The flattened ``f"{type(exc).__name__}: {exc}"`` the
    loop used to log is unreadable by anything but a human, and even a human
    could not tell an exhausted quota from a rate limit from an expired
    credential without knowing the harness's message catalogue by heart.

    Attributes:
        kind: The condition, from the closed :class:`SessionErrorKind` vocabulary.
        origin: Which record this was read from — one of the two harness failure
            Event types, or ``runner`` for a failure the Orchestrator itself
            raised around the session. Kept because "the harness refused the call"
            and "our own session lifecycle fell over" are different problems that
            an operator fixes in different places.
        error_type: The harness's own type name, verbatim.
        error_code: The harness's own code, verbatim.
        status_code: The service's HTTP status, when the call reached one.
        message: The harness's own text, verbatim and unparsed.
        model: The model whose call failed, where the record named one.
        service_request_id: The service's request id, so a condition can be taken
            to whoever operates the service.
    """

    kind: SessionErrorKind
    origin: str
    error_type: str | None = None
    error_code: str | None = None
    status_code: int | None = None
    message: str | None = None
    model: str | None = None
    service_request_id: str | None = None

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> SessionError | None:
        """Read one failure identity off a mapped harness Event, or ``None``.

        A filter as much as a reader: it is offered the whole Event stream, and
        every type but the two failure records is not a failure at all.
        """
        origin = event.get("type")
        if origin not in _FAILURE_EVENT_TYPES:
            return None
        error_type = _text(event.get("error_type"))
        error_code = _text(event.get("error_code"))
        message = _text(event.get("message"))
        status_code = event.get("status_code")
        status = int(status_code) if isinstance(status_code, int) else None
        return cls(
            kind=classify_error_kind(
                error_type=error_type,
                error_code=error_code,
                status_code=status,
                message=message,
            ),
            origin=str(origin),
            error_type=error_type,
            error_code=error_code,
            status_code=status,
            message=message,
            model=_text(event.get("model")),
            service_request_id=_text(event.get("service_request_id")),
        )

    @classmethod
    def from_exception(cls, exc: BaseException, *, origin: str = "runner") -> SessionError:
        """Read one failure identity off an exception the Orchestrator caught.

        The exception's type name and text are all there is, so this is the one
        path where the identity is inferred from prose — but it is inferred once,
        here, instead of being left as prose for every reader to squint at.
        """
        error_type = type(exc).__name__
        message = str(exc)
        return cls(
            kind=classify_error_kind(error_type=error_type, message=message),
            origin=origin,
            error_type=error_type,
            message=message or None,
        )

    def as_payload(self) -> dict[str, Any]:
        """The identity as plain data, omitting what was never reported."""
        payload: dict[str, Any] = {"kind": self.kind.value, "origin": self.origin}
        for name in (
            "error_type",
            "error_code",
            "status_code",
            "message",
            "model",
            "service_request_id",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


def _text(value: Any) -> str | None:
    """A non-empty string, or ``None`` — the harness sends both spellings."""
    if isinstance(value, str) and value:
        return value
    return None


class SessionOutcomeWatch:
    """Folds one session's Event stream down to the failures it reported.

    An :class:`~git_loopy.session.EventObserver`, joined to the Run's existing
    observers rather than substituted for them. It exists because the loop's own
    ``except`` clause sees only what was raised at *its* boundary: a quota the
    service refused three tool calls ago never raises there, so a session that
    ends politely with nothing done is indistinguishable from one that spent the
    whole Iteration being told no — unless something reads the stream.

    It decides nothing. It never ends a session, never reacts to what it sees,
    and never touches the Run: it accumulates identities and answers when asked.
    """

    def __init__(self) -> None:
        self._errors: list[SessionError] = []

    def observe(self, event: Mapping[str, Any]) -> None:
        """Fold one raw Event, keeping the failure identities and nothing else."""
        error = SessionError.from_event(event)
        if error is not None:
            self._errors.append(error)

    @property
    def errors(self) -> tuple[SessionError, ...]:
        """Every failure identity observed, in the order the harness reported it."""
        return tuple(self._errors)

    @property
    def error(self) -> SessionError | None:
        """The failure nearest the ending — the last one — or ``None``.

        The last rather than the first: an ending is described by what was
        happening as it arrived, and an earlier transient the harness recovered
        from would name a condition the session had already survived.
        """
        return self._errors[-1] if self._errors else None


class SessionTermination(Enum):
    """How the Orchestrator's own wait on the session came back.

    Three, because three is all a caller can observe from outside the session:
    the send returned, the send timed out, or something raised. It is
    deliberately *not* the ending — an ending is this plus what the Iteration
    observed around it, which is why the two are separate types.
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CRASHED = "crashed"


@dataclass(frozen=True)
class SessionOutcomeRecord:
    """One session's ending, as the data the loop used to log and throw away.

    Attributes:
        outcome: The ending, or ``None`` when the session advanced its issue and
            so reached no ending at all. Five endings and that absence are the
            whole vocabulary; a productive Iteration is not a sixth row.
        progressed: Whether the Iteration observed durable progress from this
            session. Carried beside the ending rather than folded into it,
            because a crash after a commit is still a crash and the disposition
            that reads this record must be able to see both facts.
        termination: How the Orchestrator's wait came back.
        error: The structured identity behind a failing ending, where there was
            one. Present on a session that reported a failure without raising,
            which is exactly the case the flattened log line could never explain.
    """

    outcome: SessionOutcome | None
    progressed: bool
    termination: SessionTermination
    error: SessionError | None = None

    def as_payload(self) -> dict[str, Any]:
        """The ending as plain data, for a record or a diagnostic line."""
        payload: dict[str, Any] = {
            "outcome": self.outcome.value if self.outcome is not None else None,
            "termination": self.termination.value,
            "progressed": self.progressed,
        }
        if self.error is not None:
            payload["error"] = self.error.as_payload()
        return payload


def resolve_session_outcome(
    *,
    termination: SessionTermination,
    progressed: bool,
    error: SessionError | None = None,
    content_filtered: bool = False,
    no_more_tasks: bool = False,
) -> SessionOutcomeRecord:
    """Answer one session's ending from what was observed of it.

    Pure and total. The order below is the decision, and it is an order rather
    than a set because the observations overlap:

    1. A session that raised **crashed**, whatever else it did — the Orchestrator
       lost the session, and that is a harder fact than anything the stream said.
    2. A session that ran past its send timeout **timed out**, for the same
       reason: the wait ended before the work did.
    3. An Agent that said it had nothing left to do is taken at its word.
    4. A filtered turn is an ending **only** where the Iteration also got nowhere
       (#400): the harness reports filtering per API call, so a Run that
       committed after a refused call did not end in a refusal.
    5. Anything that advanced its issue reached no ending.
    6. Everything else is the silent one — ran to the end, said nothing, did
       nothing.

    ``error`` is carried through whatever the ending: a quota refused mid-turn is
    worth recording on a session that then ended politely, and that case is
    precisely the one the old flattened log line could not describe.
    """
    if termination is SessionTermination.CRASHED:
        outcome: SessionOutcome | None = SessionOutcome.CRASH
    elif termination is SessionTermination.TIMED_OUT:
        outcome = SessionOutcome.TIMEOUT
    elif no_more_tasks:
        outcome = SessionOutcome.NO_MORE_TASKS
    elif content_filtered and not progressed:
        outcome = SessionOutcome.CONTENT_FILTERED
    elif progressed:
        outcome = None
    else:
        outcome = SessionOutcome.NO_PROGRESS
    return SessionOutcomeRecord(
        outcome=outcome,
        progressed=progressed,
        termination=termination,
        error=error,
    )


def strongest_error(*candidates: SessionError | None) -> SessionError | None:
    """The candidate identity that actually names a condition, else the first one.

    An ending frequently has two witnesses: what the harness reported on the wire,
    and what was raised at the Orchestrator's own boundary. They are not rivals —
    the second is usually a consequence of the first — so neither "latest wins"
    nor "harness wins" is right. What matters is which of them places itself in
    the vocabulary an operator can act on.
    """
    named = [
        candidate
        for candidate in candidates
        if candidate is not None and candidate.kind is not SessionErrorKind.UNKNOWN
    ]
    if named:
        return named[0]
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None
