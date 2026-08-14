"""``git_loopy.task_type_session`` tests — the classifier's spending half (#377).

:mod:`git_loopy.task_type_classifier` is pure and decides; this module is the one
that constructs a session, so these tests ask only what *spending* commits to:
which pair the session runs on, that its **Consumption** reaches the Run's cost
meter, that it is never attributed to a **Run** as an **Iteration**, and that
every way it can fail is non-fatal.
"""

from __future__ import annotations

from typing import Any

import pytest

from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair
from git_loopy.task_type_session import SessionTaskTypeProposer


class _RecordingSession:
    """Stand-in for :class:`~git_loopy.session.IterationSession`.

    Records the construction kwargs — the only place "which pair, whose cost
    meter, which Iteration" is observable — and replays scripted assistant
    messages through the observer the session was built with.
    """

    instances: list["_RecordingSession"] = []

    def __init__(self, client: Any, **kwargs: Any) -> None:
        self.client = client
        self.kwargs = kwargs
        self.sent: list[tuple[str, float]] = []
        self.messages: list[str] = []
        self.raise_on_send: BaseException | None = None
        _RecordingSession.instances.append(self)

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def send_and_wait(self, prompt: str, *, timeout: float = 60.0) -> None:
        self.sent.append((prompt, timeout))
        if self.raise_on_send is not None:
            raise self.raise_on_send
        observer = self.kwargs.get("event_observer")
        for message in self.messages:
            if observer is not None:
                observer.observe(
                    {"type": "assistant.message", "content": message}
                )


class _RecordingObserver:
    """A stand-in for the Run's ``RunCostMeter``: it only has to be reached."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def observe(self, event: dict[str, Any]) -> None:
        self.seen.append(dict(event))


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    _RecordingSession.instances.clear()


def _item() -> AfkReadyItem:
    return AfkReadyItem(
        ref=7,
        title="A title",
        rendered_block="## What to build\nA docs overhaul.",
        labels=("ready-for-agent",),
    )


def _proposer(
    *, cost_meter: Any = None, messages: tuple[str, ...] = ()
) -> SessionTaskTypeProposer:
    def factory(client: Any, **kwargs: Any) -> _RecordingSession:
        session = _RecordingSession(client, **kwargs)
        session.messages = list(messages)
        return session

    return SessionTaskTypeProposer(
        client=object(),
        config=object(),
        event_log=object(),
        sinks=object(),
        run_id="01HXR0000000000000000000AA",
        working_directory="/tmp/repo",
        send_timeout_seconds=30.0,
        cost_meter=cost_meter,
        session_factory=factory,
    )


@pytest.mark.asyncio
async def test_the_session_runs_on_the_classifier_pair() -> None:
    """The pair the classifier resolved, not the run-wide default (ADR-0029).

    Asserted on the session's construction kwargs because that is where the
    choice is actually made — a proposer that resolved the right pair and then
    built the session on ``config.model`` would satisfy every other test here.
    """
    proposer = _proposer(messages=("<task-type>docs</task-type>",))

    await proposer(ClassifierPair(model="cheap", effort="low"), _item())

    (session,) = _RecordingSession.instances
    assert session.kwargs["model"] == "cheap"
    assert session.kwargs["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_the_classifier_answer_is_read_back_from_the_session() -> None:
    """The proposal reaches the caller — the seam's whole output."""
    proposer = _proposer(messages=("<task-type>docs</task-type>",))

    answer = await proposer(ClassifierPair(model="cheap", effort="low"), _item())

    assert answer is not None
    assert "<task-type>docs</task-type>" in answer


@pytest.mark.asyncio
async def test_consumption_is_folded_into_the_runs_cost() -> None:
    """A per-issue call whose credits never reach the Summary is ADR-0026's failure.

    Auto-resolution's precedent — constructing the session *without* an
    ``event_observer`` — is deliberately not followed (ADR-0029).
    """
    meter = _RecordingObserver()
    proposer = _proposer(cost_meter=meter, messages=("<task-type>docs</task-type>",))

    await proposer(ClassifierPair(model="cheap", effort="low"), _item())

    (session,) = _RecordingSession.instances
    assert session.kwargs["event_observer"] is not None
    assert meter.seen, "the Run's cost meter saw none of the classifier's events"


@pytest.mark.asyncio
async def test_the_classifier_is_never_attributed_to_a_run_as_an_iteration() -> None:
    """Run-scoped, like ``wrapper.run.start`` — never an Iteration number.

    A classifier that allocated an Iteration number would put a phantom row in
    the Run summary and, worse, hand the strike machine something to count.
    """
    proposer = _proposer(messages=("<task-type>docs</task-type>",))

    await proposer(ClassifierPair(model="cheap", effort="low"), _item())

    (session,) = _RecordingSession.instances
    assert session.kwargs["iter_num"] is None


@pytest.mark.asyncio
async def test_a_session_that_raises_yields_no_proposal_rather_than_an_error() -> None:
    """Non-fatal: a classification failure aborts neither Iteration nor Run."""

    def factory(client: Any, **kwargs: Any) -> _RecordingSession:
        session = _RecordingSession(client, **kwargs)
        session.raise_on_send = RuntimeError("the harness went away")
        return session

    proposer = SessionTaskTypeProposer(
        client=object(),
        config=object(),
        event_log=object(),
        sinks=object(),
        run_id="01HXR0000000000000000000AA",
        working_directory="/tmp/repo",
        send_timeout_seconds=30.0,
        session_factory=factory,
    )

    answer = await proposer(ClassifierPair(model="cheap", effort="low"), _item())

    assert answer is None


@pytest.mark.asyncio
async def test_the_prompt_sent_is_the_shared_classifier_prompt() -> None:
    """One prompt, built once — the session does not compose a second one."""
    from git_loopy.task_type_classifier import classifier_prompt

    item = _item()
    proposer = _proposer(messages=("<task-type>docs</task-type>",))

    await proposer(ClassifierPair(model="cheap", effort=None), item)

    (session,) = _RecordingSession.instances
    assert session.sent == [(classifier_prompt(item), 30.0)]
