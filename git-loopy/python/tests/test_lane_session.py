"""Lane budget behavior through a fake harness and a manually advanced clock."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from git_loopy.lane_session import run_lane_session


class Clock:
    def __init__(self):
        self.seconds = 0.0
        self.waiters = []
        self.timers = []

    def call_later(self, seconds, callback):
        timer = asyncio.TimerHandle(self.seconds + seconds, callback, (), asyncio.get_running_loop())
        self.timers.append(timer)
        return timer

    def monotonic(self):
        return self.seconds

    def local_deadline(self, seconds):
        return datetime(
            2026, 10, 20, 12, tzinfo=timezone(timedelta(hours=-6))
        ) + timedelta(seconds=seconds)

    async def sleep(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.seconds + seconds, future))
        await future

    async def advance(self, seconds):
        self.seconds = seconds
        for timer in list(self.timers):
            if timer.when() <= seconds and not timer.cancelled():
                timer._run()
                self.timers.remove(timer)
        for deadline, future in self.waiters:
            if deadline <= seconds and not future.done():
                future.set_result(None)
        await settle()


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


class Session:
    def __init__(self, clock):
        self.clock = clock
        self.messages = []
        self.calls = []
        self.complete = asyncio.Event()

    async def send_and_wait(self, prompt, *, timeout):
        self.calls.append((prompt, timeout, self.clock.monotonic()))
        completion = asyncio.create_task(self.complete.wait())
        expiry = asyncio.create_task(self.clock.sleep(timeout))
        try:
            done, _ = await asyncio.wait(
                (completion, expiry), return_when=asyncio.FIRST_COMPLETED
            )
            if expiry in done:
                raise TimeoutError
            return "finished"
        finally:
            completion.cancel()
            expiry.cancel()
            await asyncio.gather(completion, expiry, return_exceptions=True)

    async def send(self, prompt, *, mode):
        self.messages.append((prompt, mode, self.clock.monotonic()))
        return "message-id"


async def test_running_lane_gets_one_wrap_up_without_restarting_timeout():
    clock = Clock()
    session = Session(clock)
    task = asyncio.create_task(
        run_lane_session(session, "Lane brief", timeout=100, lead_time=10, clock=clock)
    )
    await settle()
    await clock.advance(89)
    assert session.messages == []
    await clock.advance(90)
    assert len(session.messages) == 1
    message, mode, sent_at = session.messages[0]
    assert mode == "immediate"
    assert sent_at == 90
    assert "normal agent commit" in message
    assert "uncommitted" in message and "Checkpoint" in message
    assert "issue" in message and "done" in message and "remains" in message
    await clock.advance(99)
    assert len(session.messages) == 1
    assert not task.done()
    await clock.advance(100)
    with pytest.raises(TimeoutError):
        await task
    assert len(session.calls) == 1
    assert session.calls[0][1:] == (100, 0)
    await clock.advance(150)
    assert len(session.messages) == 1


async def test_lane_brief_states_budget_and_local_deadline_with_offset():
    clock = Clock()
    session = Session(clock)
    session.complete.set()
    assert await run_lane_session(
        session, "Original Lane brief", timeout=100, lead_time=10, clock=clock
    ) == "finished"
    prompt = session.calls[0][0]
    assert "Original Lane brief" in prompt
    assert "100 seconds" in prompt
    assert "2026-10-20T12:01:40-06:00" in prompt


async def test_short_lane_gets_no_wrap_up_even_after_its_deadline():
    clock = Clock()
    session = Session(clock)
    task = asyncio.create_task(
        run_lane_session(session, "brief", timeout=100, lead_time=10, clock=clock)
    )
    await settle()
    await clock.advance(20)
    session.complete.set()
    assert await task == "finished"
    await clock.advance(150)
    assert session.messages == []


async def test_delayed_timer_never_sends_at_or_after_timeout():
    clock = Clock()
    session = Session(clock)
    task = asyncio.create_task(
        run_lane_session(session, "brief", timeout=100, lead_time=10, clock=clock)
    )
    await settle()
    await clock.advance(100)
    with pytest.raises(TimeoutError):
        await task
    assert session.messages == []


@pytest.mark.parametrize("timeout,warning_at", [(100, 90), (7200, 6480), (20000, 19100)])
async def test_default_lead_is_a_tenth_of_budget_capped_at_fifteen_minutes(
    timeout, warning_at
):
    clock = Clock()
    session = Session(clock)
    task = asyncio.create_task(
        run_lane_session(session, "brief", timeout=timeout, clock=clock)
    )
    await settle()
    await clock.advance(warning_at - 1)
    assert session.messages == []
    await clock.advance(warning_at)
    assert len(session.messages) == 1
    session.complete.set()
    await task


@pytest.mark.parametrize("delivery", ["fails", "blocks"])
async def test_warning_delivery_cannot_move_timeout_or_change_its_outcome(delivery):
    clock = Clock()
    stopped = asyncio.Event()

    class UnavailableSession(Session):
        async def send(self, prompt, *, mode):
            await super().send(prompt, mode=mode)
            try:
                if delivery == "fails":
                    raise OSError("disconnected")
                await asyncio.Event().wait()
            finally:
                stopped.set()

    session = UnavailableSession(clock)
    task = asyncio.create_task(
        run_lane_session(session, "brief", timeout=100, lead_time=10, clock=clock)
    )
    await settle()
    await clock.advance(90)
    await clock.advance(100)
    with pytest.raises(TimeoutError):
        await task
    assert stopped.is_set()
    assert len(session.messages) == 1


async def test_cancelling_lane_removes_pending_warning():
    clock = Clock()
    session = Session(clock)
    task = asyncio.create_task(
        run_lane_session(session, "brief", timeout=100, clock=clock)
    )
    await settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await clock.advance(95)
    assert session.messages == []
    assert all(future.done() for _, future in clock.waiters)
    assert all(timer.cancelled() for timer in clock.timers)
