"""Tests for ``git_loopy.execution_host`` (issue #447, spec #445 §A/§C).

Exercises the Execution host seam in isolation: the outcome contract's data
shapes, :class:`LocalExecutionHost`'s declared properties and its rejection
of a returned branch that still carries uncommitted or untracked work, a
substitutable fake host returning a prepared branch / a terminal failure /
a stall with no network involved, and the "never silently retries" refusal.

Acceptance criteria reference: issue #447.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from git_loopy.execution_host import (
    ContributionFailure,
    ContributionRequest,
    ContributionSuccess,
    ExecutionHost,
    LocalExecutionHost,
    LocalRunResult,
)


def _request(**overrides: object) -> ContributionRequest:
    defaults: dict[str, object] = dict(
        issue_ref=42,
        prompt="do the thing",
        base_revision="deadbeef",
        model="gpt-5",
        reasoning_effort="medium",
        skill_policy={"skills": ()},
        run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
    )
    defaults.update(overrides)
    return ContributionRequest(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ContributionRequest / outcome shapes                                        #
# --------------------------------------------------------------------------- #


def test_contribution_request_carries_exactly_the_spec_fields() -> None:
    request = _request()
    assert request.issue_ref == 42
    assert request.prompt == "do the thing"
    assert request.base_revision == "deadbeef"
    assert request.model == "gpt-5"
    assert request.reasoning_effort == "medium"
    assert request.skill_policy == {"skills": ()}
    assert request.run_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_contribution_request_is_frozen() -> None:
    request = _request()
    with pytest.raises(AttributeError):
        request.model = "gpt-6"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# LocalExecutionHost: declared properties                                     #
# --------------------------------------------------------------------------- #


def test_local_host_declares_placement_and_grade() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        raise AssertionError("not exercised")

    host = LocalExecutionHost(runner=runner)
    assert host.placement == "local"
    assert host.isolation_grade == "workspace separation only"


def test_local_host_capacity_derived_from_core_count(monkeypatch: pytest.MonkeyPatch) -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        raise AssertionError("not exercised")

    monkeypatch.setattr("os.cpu_count", lambda: 8)
    host = LocalExecutionHost(runner=runner)
    assert host.capacity == 8


def test_local_host_capacity_floors_at_one_when_core_count_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        raise AssertionError("not exercised")

    monkeypatch.setattr("os.cpu_count", lambda: None)
    host = LocalExecutionHost(runner=runner)
    assert host.capacity == 1


def test_local_host_capacity_explicit_override() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        raise AssertionError("not exercised")

    host = LocalExecutionHost(runner=runner, capacity=3)
    assert host.capacity == 3


def test_local_host_satisfies_execution_host_protocol() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        raise AssertionError("not exercised")

    host = LocalExecutionHost(runner=runner)
    assert isinstance(host, ExecutionHost)


# --------------------------------------------------------------------------- #
# LocalExecutionHost: outcome classification                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_local_host_returns_success_for_a_clean_branch() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        return LocalRunResult(
            branch="git-loopy/run/issue-42", sha="cafef00d", dirty=False, untracked=False
        )

    host = LocalExecutionHost(runner=runner)
    outcome = await host.run_contribution(_request())

    assert isinstance(outcome, ContributionSuccess)
    assert outcome.branch == "git-loopy/run/issue-42"
    assert outcome.sha == "cafef00d"
    assert outcome.events == ()
    assert outcome.placement == "local"
    assert outcome.isolation_grade == "workspace separation only"


@pytest.mark.asyncio
async def test_local_host_rejects_a_branch_carrying_uncommitted_work() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        return LocalRunResult(
            branch="git-loopy/run/issue-42", sha="cafef00d", dirty=True, untracked=False
        )

    host = LocalExecutionHost(runner=runner)
    outcome = await host.run_contribution(_request())

    assert isinstance(outcome, ContributionFailure)
    assert outcome.reason == "uncommitted_or_untracked_work"


@pytest.mark.asyncio
async def test_local_host_rejects_a_branch_carrying_untracked_work() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        return LocalRunResult(
            branch="git-loopy/run/issue-42", sha="cafef00d", dirty=False, untracked=True
        )

    host = LocalExecutionHost(runner=runner)
    outcome = await host.run_contribution(_request())

    assert isinstance(outcome, ContributionFailure)
    assert outcome.reason == "uncommitted_or_untracked_work"


@pytest.mark.asyncio
async def test_local_host_rejects_an_unresolved_sha() -> None:
    async def runner(request: ContributionRequest) -> LocalRunResult:
        return LocalRunResult(
            branch="git-loopy/run/issue-42", sha=None, dirty=False, untracked=False
        )

    host = LocalExecutionHost(runner=runner)
    outcome = await host.run_contribution(_request())

    assert isinstance(outcome, ContributionFailure)
    assert outcome.reason == "unresolved_branch_sha"


@pytest.mark.asyncio
async def test_local_host_never_retries_the_runner() -> None:
    """A runner called more than once for one outcome is a test failure."""
    calls = 0

    async def runner(request: ContributionRequest) -> LocalRunResult:
        nonlocal calls
        calls += 1
        return LocalRunResult(
            branch="git-loopy/run/issue-42", sha="cafef00d", dirty=False, untracked=False
        )

    host = LocalExecutionHost(runner=runner)
    await host.run_contribution(_request())

    assert calls == 1


@pytest.mark.asyncio
async def test_local_host_propagates_a_runner_crash_uncaught() -> None:
    """A crash is not a value the seam masks — it is left uncaught."""

    async def runner(request: ContributionRequest) -> LocalRunResult:
        raise RuntimeError("boom")

    host = LocalExecutionHost(runner=runner)
    with pytest.raises(RuntimeError, match="boom"):
        await host.run_contribution(_request())


# --------------------------------------------------------------------------- #
# A substitutable fake host: prepared branch, terminal failure, a stall       #
# --------------------------------------------------------------------------- #


@dataclass
class FakeExecutionHost:
    """An in-memory :class:`ExecutionHost` fake — no network involved.

    ``outcome`` is returned verbatim by :meth:`run_contribution` unless it is
    the sentinel ``"stall"``, in which case the call hangs forever (an
    ``asyncio.Event`` that never sets) so a caller can exercise its own
    timeout / cancellation path against a host that never answers.
    """

    outcome: object
    placement: str = "fake"
    isolation_grade: str = "workspace separation only"
    capacity: int = 4
    calls: list[ContributionRequest] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    async def run_contribution(self, request: ContributionRequest):
        assert self.calls is not None
        self.calls.append(request)
        if self.outcome == "stall":
            await asyncio.Event().wait()  # never set: simulates a stalled host
        return self.outcome


@pytest.mark.asyncio
async def test_fake_host_returns_a_prepared_branch_with_no_network() -> None:
    success = ContributionSuccess(
        branch="git-loopy/run/issue-7",
        sha="abc123",
        events=(),
        placement="fake",
        isolation_grade="workspace separation only",
    )
    host = FakeExecutionHost(outcome=success)
    request = _request(issue_ref=7)

    outcome = await host.run_contribution(request)

    assert outcome is success
    assert host.calls == [request]


@pytest.mark.asyncio
async def test_fake_host_returns_a_terminal_failure_with_no_network() -> None:
    failure = ContributionFailure(reason="environment_error", detail="toolchain missing")
    host = FakeExecutionHost(outcome=failure)

    outcome = await host.run_contribution(_request())

    assert outcome is failure


@pytest.mark.asyncio
async def test_fake_host_can_stall_with_no_network() -> None:
    """The Run — not the host — decides what happens next on a stall."""
    host = FakeExecutionHost(outcome="stall")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(host.run_contribution(_request()), timeout=0.05)


def test_fake_host_satisfies_execution_host_protocol() -> None:
    host = FakeExecutionHost(outcome=ContributionFailure(reason="x"))
    assert isinstance(host, ExecutionHost)
