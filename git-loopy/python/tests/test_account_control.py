"""Clock-controlled public account admission sequences, not classifier policy."""
import asyncio

import pytest

from git_loopy.account_control import AccountAdmissionClosed, AccountControl
from git_loopy.session_outcome import SessionError


def refusal(code="rate_limit_exceeded", **extra):
    return SessionError.from_event({
        "type": "session.error", "error_code": code, **extra,
    })


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinary", [False, True])
@pytest.mark.parametrize("code", [
    "rate_limit_exceeded", "insufficient_quota", "authentication_failed",
])
async def test_operator_stop_without_account_episode_admits_drain_sessions(ordinary, code):
    clock, events = Clock(), []
    control = AccountControl(
        emit=lambda type, **kw: events.append(kw), clock=clock, sleep=clock.sleep,
    )
    control.interrupt()

    assert control.admit(ordinary=ordinary) is False
    assert control.ordinary_available
    assert not await control.ready(may_start=True), "Stop must not start new work"
    assert control.end_payload() == {}
    assert events == []
    assert clock.waits == []

    # A refusal from that draining work must close admission, never probe.
    control.refuse(refusal(code))
    with pytest.raises(AccountAdmissionClosed):
        control.admit(ordinary=ordinary)
    assert not control.ordinary_available
    assert not await control.ready(may_start=True)
    assert control.end_payload()["account_error"]["error_code"] == code
    assert not any(event["admission_open"] for event in events)
    assert clock.waits == []


@pytest.mark.asyncio
async def test_successful_probe_finishing_after_operator_stop_cannot_reopen_admission():
    clock, events = Clock(), []
    control = AccountControl(
        emit=lambda type, **kw: events.append(kw), clock=clock, sleep=clock.sleep,
    )
    control.refuse(refusal())
    assert await control.ready(may_start=True)
    probe = control.admit(ordinary=True)
    control.interrupt()
    control.finished(probe, successful=True)

    assert not any(event["admission_open"] for event in events)
    assert control.state is not None
    assert not control.ordinary_available
    assert not await control.ready(may_start=True)
    for ordinary in (True, False):
        with pytest.raises(AccountAdmissionClosed):
            control.admit(ordinary=ordinary)
    assert control.end_payload()["account_error"]["kind"] == "rate_limited"
    assert clock.waits == [30]


@pytest.mark.asyncio
async def test_rate_retries_are_run_wide_and_success_does_not_reset_budget():
    clock, events = Clock(), []
    control = AccountControl(
        emit=lambda type, **kw: events.append(kw), clock=clock, sleep=clock.sleep,
    )
    for retry in range(2):
        control.refuse(refusal())
        assert control.state == "cooldown"
        with pytest.raises(AccountAdmissionClosed):
            control.admit(ordinary=True)
        assert await control.ready(may_start=True)
        with pytest.raises(AccountAdmissionClosed):
            control.admit(ordinary=False)
        probe = control.admit(ordinary=True)
        with pytest.raises(AccountAdmissionClosed):
            control.admit(ordinary=True)
        control.finished(probe, successful=True)
        assert control.state is None
    control.refuse(refusal())
    assert control.state == "stopping"
    assert control.cause == "retry_budget_exhausted"
    assert clock.waits == [30, 60]
    assert control.retries_remaining == 0
    assert control.wait_remaining == 30
    assert [e["state"] for e in events] == [
        "cooldown", "probing", "probing", "cooldown", "probing", "probing", "stopping",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("delay,wait,cause", [
    (None, 30, None), (45, 45, None), (5, 30, None),
    (121, None, "wait_budget_exhausted"),
    ("nonsense", None, "invalid_retry_delay"),
    (-1, None, "invalid_retry_delay"),
    (float("inf"), None, "invalid_retry_delay"),
    (True, None, "invalid_retry_delay"),
])
async def test_provider_delay_is_validated_and_never_retried_early(delay, wait, cause):
    clock = Clock()
    control = AccountControl(emit=lambda *a, **kw: None, clock=clock, sleep=clock.sleep)
    control.refuse(refusal(), retry_after=delay)
    ready = await control.ready(may_start=True)
    if wait is None:
        assert not ready
        assert control.cause == cause
        assert clock.waits == []
    else:
        assert ready
        assert clock.waits == [wait]


@pytest.mark.asyncio
async def test_coalesced_refusals_keep_one_clock_and_quota_dominates():
    clock, events = Clock(), []
    control = AccountControl(
        emit=lambda type, **kw: events.append(kw), clock=clock, sleep=clock.sleep,
    )
    control.refuse(refusal())
    clock.now = 10
    control.refuse(refusal())
    assert control.retries_remaining == 2
    assert control.wait_remaining == 90
    assert await control.ready(may_start=True)
    assert clock.waits == [20]
    probe = control.admit(ordinary=True)
    control.refuse(refusal("insufficient_quota"))
    control.refuse(refusal())
    control.finished(probe, successful=True)
    assert control.state == "stopping"
    assert control.error.kind.value == "quota_exhausted"
    assert len([e for e in events if e["state"] == "cooldown"]) == 1


@pytest.mark.asyncio
async def test_provider_minimum_beyond_remaining_wait_stops_second_probe():
    clock = Clock()
    control = AccountControl(emit=lambda *a, **kw: None, clock=clock, sleep=clock.sleep)
    control.refuse(refusal(), retry_after=80)
    await control.ready(may_start=True)
    control.admit(ordinary=True)
    control.refuse(refusal(), retry_after=50)
    assert control.cause == "wait_budget_exhausted"
    assert not await control.ready(may_start=True)
    assert clock.waits == [80]


@pytest.mark.asyncio
async def test_operator_stop_interrupts_cooldown_without_waiting_for_clock():
    sleeping = asyncio.Event()

    async def sleep(_seconds):
        sleeping.set()
        await asyncio.Future()

    control = AccountControl(emit=lambda *a, **kw: None, sleep=sleep)
    control.refuse(refusal())
    waiting = asyncio.create_task(control.ready(may_start=True))
    await sleeping.wait()
    control.interrupt()
    assert not await asyncio.wait_for(waiting, timeout=0.1)
    assert control.retries_remaining == 2
    for ordinary in (True, False):
        with pytest.raises(AccountAdmissionClosed):
            control.admit(ordinary=ordinary)
    assert not control.ordinary_available


@pytest.mark.asyncio
async def test_unconfirmed_probe_stops_instead_of_inventing_health():
    clock = Clock()
    control = AccountControl(emit=lambda *a, **kw: None, clock=clock, sleep=clock.sleep)
    control.refuse(refusal())
    await control.ready(may_start=True)
    probe = control.admit(ordinary=True)
    control.finished(probe, successful=False)
    assert control.state == "stopping"
    assert control.cause == "probe_unconfirmed"


@pytest.mark.asyncio
async def test_provider_minimum_arriving_during_wait_cannot_be_retried_early():
    clock = Clock()

    async def sleep(seconds):
        await clock.sleep(seconds)
        if len(clock.waits) == 1:
            control.refuse(refusal(), retry_after=40)

    control = AccountControl(emit=lambda *a, **kw: None, clock=clock, sleep=sleep)
    control.refuse(refusal())
    assert await control.ready(may_start=True)
    assert clock.waits == [30, 40]
    assert clock.now == 70
    assert control.wait_remaining == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["quota", "operator"])
async def test_stop_arriving_at_the_cooldown_completion_boundary_cannot_admit_a_probe(stop):
    clock = Clock()

    def interrupt():
        if stop == "quota":
            control.refuse(refusal("insufficient_quota"))
        else:
            control.interrupt()

    async def sleep(seconds):
        await clock.sleep(seconds)
        # Let the wait complete, then deliver the external stop while its
        # cancelled waiter is being drained, before admission may reopen.
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: loop.call_soon(lambda: loop.call_soon(interrupt)))

    control = AccountControl(emit=lambda *a, **kw: None, clock=clock, sleep=sleep)
    control.refuse(refusal())
    assert not await control.ready(may_start=True)
    with pytest.raises(AccountAdmissionClosed):
        control.admit(ordinary=True)
    assert control.retries_remaining == 2
    if stop == "quota":
        assert control.state == "stopping"
        assert control.error.kind.value == "quota_exhausted"
