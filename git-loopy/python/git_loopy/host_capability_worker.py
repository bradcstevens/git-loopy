"""Observe the executing host's harness and write a capability report (#567).

This is the GitHub Actions half of :mod:`git_loopy.host_capability`. The
workflow checks this module out on the runner and invokes it. The listing is
read with the same Copilot client construction a contribution session uses, so
the report describes the installation that would run the work. No Agent
session starts, no tracker write occurs, and no credential is read from the
environment beyond what that client construction already uses.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence

from git_loopy.github_actions_host import GITHUB_ACTIONS_PLACEMENT
from git_loopy.host_capability import (
    CAPABILITY_REPORT_FILENAME,
    HostCapabilityReport,
)

__all__ = [
    "RUNNER_TEMP_ENV",
    "CapabilityObservationError",
    "main",
    "observe_report",
    "write_report",
]

#: The Actions-provided scratch directory the artifact is assembled in.
RUNNER_TEMP_ENV = "RUNNER_TEMP"

ListingFetch = Callable[[], Awaitable[Sequence[object]]]


class CapabilityObservationError(RuntimeError):
    """The runner could not observe its own harness."""


async def observe_report(
    *,
    fetch: ListingFetch | None = None,
    placement: str = GITHUB_ACTIONS_PLACEMENT,
) -> HostCapabilityReport:
    """List models on this machine and project them into a capability report."""
    listing = await (fetch if fetch is not None else _fetch_with_work_client)()
    if listing is None:
        raise CapabilityObservationError("the harness listing did not complete")
    return HostCapabilityReport.from_listing(placement, listing)


def write_report(directory: Path, report: HostCapabilityReport) -> Path:
    """Write the report the host reads out of the artifact zip."""
    path = directory / CAPABILITY_REPORT_FILENAME
    path.write_text(report.to_json(), encoding="utf-8")
    return path


async def _fetch_with_work_client() -> Sequence[object]:
    """List models with the contribution session's client construction.

    A bare ``CopilotClient`` would observe a different data root than the
    session this host is about to open. The factory is the shared one.
    """
    from git_loopy.copilot_client import make_copilot_client
    from git_loopy.model_listing import fetch_live_models

    return await fetch_live_models(
        client_factory=lambda: make_copilot_client(working_directory=Path.cwd())
    )


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Entry point the capability workflow invokes."""
    del argv
    environment = os.environ if env is None else env
    runner_temp = environment.get(RUNNER_TEMP_ENV)
    if not runner_temp:
        raise CapabilityObservationError(f"{RUNNER_TEMP_ENV} is required")
    report = asyncio.run(observe_report())
    write_report(Path(runner_temp), report)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
