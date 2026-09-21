# PROTOTYPE — THROWAWAY. Not production, not gated, not imported by anything.
#
# Question (ADR-0033 slice 5): is a Python asyncio Lease renewer actually
# independent of agent progress?
#
# The Bash risk is orphaning. Python's risk is the opposite and subtler: the
# renewer shares the event loop with the session, so ONE blocking call inside a
# coroutine starves the heartbeat. The Lease then expires while its owner is
# alive and healthy -- a false steal, the exact failure ADR-0033 says the design
# must survive, arriving through the one mechanism it trusts to prevent it.

import asyncio
import time

INTERVAL = 0.1
SESSION = 1.0
TTL_BEATS = 5  # a renewer this far behind would be judged expired


async def renewer(beats: list[float], stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(INTERVAL)
        beats.append(time.monotonic())


async def session_blocking() -> None:
    """A sync call awaited from the loop -- e.g. subprocess.run, requests."""
    time.sleep(SESSION)


async def session_cooperative() -> None:
    await asyncio.sleep(SESSION)


async def drive(session, label: str) -> None:
    beats: list[float] = []
    stop = asyncio.Event()
    task = asyncio.create_task(renewer(beats, stop))
    await session()
    stop.set()
    task.cancel()
    expected = int(SESSION / INTERVAL)
    got = len(beats)
    gap = max(
        (b - a for a, b in zip(beats, beats[1:])), default=SESSION
    )
    starved = got < expected - TTL_BEATS
    print(
        f"{label:<24} beats={got:<3} expected~{expected:<3} "
        f"max_gap={gap:.2f}s  "
        + ("STARVED: Lease expires under a live owner" if starved else "OK: renewed throughout")
    )


async def main() -> None:
    await drive(session_cooperative, "awaited session")
    await drive(session_blocking, "blocking call in loop")


asyncio.run(main())
