"""**Session outcome** — how one Agent's session ended, as data (#403).

The loop only ever *logged* why a session ended, so the ending was unavailable to
anything that would act on it. These are the tests for the record that replaces
those log lines: the closed five-member ending vocabulary, the structured
**Session error** identity that distinguishes an account-level condition from an
Agent that simply committed nothing, and the observer that folds the harness's
own error records into one.
"""

from __future__ import annotations

import pytest

from git_loopy.session_outcome import (
    SessionError,
    SessionErrorKind,
    SessionOutcome,
    SessionOutcomeWatch,
    SessionTermination,
    classify_error_kind,
    resolve_session_outcome,
    strongest_error,
)


def test_the_ending_vocabulary_is_the_five_the_lifecycle_disposes_of() -> None:
    """Closed and exactly five, because the dispositions are keyed off it.

    An ending outside this set would reach the attempt lifecycle with no row in
    its table, and the disposition it fell through to would be a guess.
    """
    assert {member.value for member in SessionOutcome} == {
        "no_progress",
        "timeout",
        "crash",
        "no_more_tasks",
        "content_filtered",
    }


def test_an_exhausted_quota_is_its_own_identity_not_a_flattened_message() -> None:
    """The condition the Run pays for most and could name least.

    A quota the account has spent is reported by the harness as an ordinary call
    failure; flattened to a string it reads exactly like every other failure, so
    an operator reading a Run that stopped delivering could not tell a hard
    problem from an empty wallet.
    """
    assert (
        classify_error_kind(
            error_code="insufficient_quota",
            message="Monthly premium request quota exhausted.",
            status_code=429,
        )
        is SessionErrorKind.QUOTA_EXHAUSTED
    )


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {"status_code": 429, "message": "Too many requests, please retry."},
            SessionErrorKind.RATE_LIMITED,
        ),
        (
            {"status_code": 401, "message": "Bad credentials."},
            SessionErrorKind.AUTHENTICATION_FAILED,
        ),
        (
            {"error_type": "TokenExpiredError", "message": "authentication failed"},
            SessionErrorKind.AUTHENTICATION_FAILED,
        ),
    ],
)
def test_the_account_level_conditions_are_three_separate_facts(
    fields: dict[str, object], expected: SessionErrorKind
) -> None:
    """Rate limiting and a refused credential are not each other, nor quota.

    The three share a shape — the account, not the work, is what refused — and
    they take opposite reactions from an operator: wait, re-authenticate, or buy
    more. One string for all three offers none of those.
    """
    assert classify_error_kind(**fields) is expected  # type: ignore[arg-type]


def test_a_session_error_is_read_off_the_harness_record_it_came_from() -> None:
    """One reader for both failure records, keyed on the type literal.

    `session.error` and `model.call_failure` describe the same trouble at two
    scopes — the session and the single API call — and an operator wants the same
    four answers from either: what condition, on which model, from which record,
    and with what the service said. Reading them into one identity is what makes
    the outcome carry a *fact* rather than whichever sentence arrived last.
    """
    error = SessionError.from_event(
        {
            "type": "model.call_failure",
            "source": "top_level",
            "model": "claude-opus-5",
            "error_code": "insufficient_quota",
            "message": "Monthly premium request quota exhausted.",
            "status_code": 429,
        }
    )
    assert error is not None
    assert error.kind is SessionErrorKind.QUOTA_EXHAUSTED
    assert error.origin == "model.call_failure"
    assert error.model == "claude-opus-5"
    assert error.status_code == 429
    assert error.error_code == "insufficient_quota"


def test_an_event_that_is_not_a_failure_record_yields_no_session_error() -> None:
    """The reader is a filter as well, because it is fed the whole Event stream."""
    assert SessionError.from_event({"type": "assistant.message", "content": "hi"}) is None


def test_the_watch_folds_the_failures_out_of_the_event_stream() -> None:
    """The stream is where the identity lives, so the stream is what is read.

    Nothing else in the Run sees the harness's failure records, and the loop's
    own `except` clause sees only what was raised at *its* boundary — which for a
    quota refused three tool calls ago is nothing at all. The watch is what makes
    the ending's identity available to a session that ended without raising.

    The last failure wins because it is the one nearest the ending being
    described; the earlier ones stay readable in order rather than being
    discarded.
    """
    watch = SessionOutcomeWatch()
    watch.observe({"type": "assistant.message", "content": "working"})
    assert watch.error is None

    watch.observe(
        {
            "type": "model.call_failure",
            "source": "top_level",
            "status_code": 500,
            "message": "upstream unavailable",
        }
    )
    watch.observe(
        {
            "type": "session.error",
            "error_type": "QuotaError",
            "message": "Monthly premium request quota exhausted.",
        }
    )

    assert watch.error is not None
    assert watch.error.kind is SessionErrorKind.QUOTA_EXHAUSTED
    assert [error.kind for error in watch.errors] == [
        SessionErrorKind.SERVER_ERROR,
        SessionErrorKind.QUOTA_EXHAUSTED,
    ]


def test_a_session_that_advanced_its_issue_reached_no_ending_at_all() -> None:
    """Five endings, and the absence of one, are the whole vocabulary.

    The five are the rows a disposition is owed for, and every one of them
    describes an Iteration that got nowhere. A session that ran to the end and
    committed is not a sixth row: there is nothing to dispose of, and giving it
    an ending would put every productive Iteration into the table the lifecycle
    reads.
    """
    advanced = resolve_session_outcome(
        termination=SessionTermination.COMPLETED, progressed=True
    )
    assert advanced.outcome is None
    assert advanced.progressed is True

    silent = resolve_session_outcome(
        termination=SessionTermination.COMPLETED, progressed=False
    )
    assert silent.outcome is SessionOutcome.NO_PROGRESS
    assert silent.error is None


def test_an_account_level_condition_is_not_a_session_that_merely_did_nothing() -> None:
    """The whole point of #403, at the seam the lifecycle will read.

    Both sessions below end the same way — politely, with nothing committed — and
    until now the Run recorded exactly that about both. One of them was refused
    for the whole Iteration because the account is out of quota. Same ending,
    different identity, and the identity is what an operator can act on.
    """
    watch = SessionOutcomeWatch()
    watch.observe(
        {
            "type": "model.call_failure",
            "source": "top_level",
            "error_code": "insufficient_quota",
            "message": "Monthly premium request quota exhausted.",
            "status_code": 429,
        }
    )
    refused = resolve_session_outcome(
        termination=SessionTermination.COMPLETED,
        progressed=False,
        error=watch.error,
    )
    idle = resolve_session_outcome(
        termination=SessionTermination.COMPLETED, progressed=False
    )

    assert refused.outcome is idle.outcome is SessionOutcome.NO_PROGRESS
    assert refused.error is not None
    assert refused.error.kind is SessionErrorKind.QUOTA_EXHAUSTED
    assert idle.error is None
    assert refused.as_payload()["error"]["kind"] == "quota_exhausted"


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        ({"termination": SessionTermination.CRASHED}, SessionOutcome.CRASH),
        ({"termination": SessionTermination.TIMED_OUT}, SessionOutcome.TIMEOUT),
        ({"no_more_tasks": True}, SessionOutcome.NO_MORE_TASKS),
        ({"content_filtered": True}, SessionOutcome.CONTENT_FILTERED),
        ({}, SessionOutcome.NO_PROGRESS),
    ],
)
def test_every_ending_the_lifecycle_disposes_of_is_reachable(
    observed: dict[str, object], expected: SessionOutcome
) -> None:
    """Each of the five, from what an Iteration can actually observe."""
    fields: dict[str, object] = {
        "termination": SessionTermination.COMPLETED,
        "progressed": False,
        **observed,
    }
    assert resolve_session_outcome(**fields).outcome is expected  # type: ignore[arg-type]


def test_a_crash_after_a_commit_is_still_a_crash() -> None:
    """Progress does not launder a lost session, and is not lost either.

    An Iteration that committed and *then* lost its session has two facts worth
    keeping, and the record keeps both rather than letting either overwrite the
    other. Only content-filtering is conditioned on progress, and only because
    the harness reports it per API call.
    """
    record = resolve_session_outcome(
        termination=SessionTermination.CRASHED, progressed=True
    )
    assert record.outcome is SessionOutcome.CRASH
    assert record.progressed is True


def test_a_filtered_call_on_an_iteration_that_committed_is_not_an_ending() -> None:
    """The naive detector's mistake, refused at the resolver (#400).

    ``content_filter_triggered`` rides the per-API-call usage record, so an
    Iteration that had one call refused and then went on to commit would report a
    refusal it plainly recovered from.
    """
    record = resolve_session_outcome(
        termination=SessionTermination.COMPLETED,
        progressed=True,
        content_filtered=True,
    )
    assert record.outcome is None


def test_a_named_condition_outranks_an_unplaceable_one() -> None:
    """Two candidates reach an ending, and only one of them explains it.

    A session refused for quota often *also* dies at the Orchestrator's own
    boundary, with a generic transport exception that names nothing. Taking the
    last thing that happened would report that exception and lose the condition
    that caused it, so the identity that places itself in the vocabulary wins.
    """
    quota = SessionError.from_event(
        {"type": "session.error", "message": "quota exhausted", "error_type": "Err"}
    )
    transport = SessionError.from_exception(RuntimeError("connection closed"))
    assert transport.kind is SessionErrorKind.UNKNOWN

    assert strongest_error(quota, transport) is quota
    assert strongest_error(None, transport) is transport
    assert strongest_error(None, None) is None
