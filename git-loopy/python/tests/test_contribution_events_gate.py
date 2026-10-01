"""A declared ``contribution_events: true`` is proved by what a Parallel Run emits.

ADR-0065 made the declaration an obligation. Before this gate the only check
behind it was a source grep that any one contribution-scoped constant could
satisfy (ADR-0049 calls that a mention, not a claim), which is how eight types
rode on one producer.

So this module drives faked Parallel **Runs** through the production loop with
the existing fakes, reads nothing but the JSONL Event log each one writes, and
holds the member to two things:

1. The scenarios' combined emitted types cover every type in the Event-schema
   fixture's ``contribution_identity.lifecycle_types`` and
   ``scheduler_scoped_types``, minus :data:`WAIVERS`.
2. Each contribution's emitted lifecycle, filtered to non-waived types, follows
   the per-contribution order in :func:`_lifecycle_sequences`.

A waiver names the ticket that owns the missing producer. A waived type any
scenario emits fails the gate, so each producer ticket deletes its own waiver.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from git_loopy import events as events_module
from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy import rolling_scheduler
from git_loopy.readiness import BlockedByRead, BlockerNode
from tests import test_loop_parallel as lp
from tests.test_loop_parallel import (  # noqa: F401 - autouse Run fixtures
    _declare_two_local_lane_slots,
    _stub_run_skill_catalog,
)
from tests.fakes import FakeGateRunner, FakeGitHubClient

_EVENT_SCHEMA = json.loads(
    (Path(__file__).parents[2] / "conformance" / "event-schema.json").read_text(
        encoding="utf-8"
    )
)

#: Declared types whose producer does not exist yet, each keyed to the ticket
#: that owns it. Deleting an entry is that ticket's job, and the gate forces it:
#: a waived type any scenario emits is a failure.
WAIVERS: dict[str, int] = {
    "wrapper.rolling.refill_turn": 686,
}

_START = "wrapper.contribution.start"
_WORK_FINISHED = "wrapper.contribution.work_finished"
_END = "wrapper.contribution.end"
_PARKED = "wrapper.integration.parked"
_ADMITTED = "wrapper.integration.admitted"
_STARTED = "wrapper.integration.started"
_BRANCH_OBSERVED = "wrapper.integration.branch_observed"
_RECOVERY_STARTED = "wrapper.integration.recovery_started"
_PUBLISHED = "wrapper.integration.published"
_AUTO_CLOSE = "wrapper.auto_close"
_RELEASE_ADVANCED = "wrapper.release.advanced"

#: The types the per-contribution order constrains. Every other stamped record
#: (assistant, tool, usage, commit, Checkpoint) interleaves freely.
_ORDERED_TYPES = frozenset({
    _START, _WORK_FINISHED, _END, _PARKED, _ADMITTED, _STARTED,
    _BRANCH_OBSERVED, _RECOVERY_STARTED, _PUBLISHED, _AUTO_CLOSE,
    _RELEASE_ADVANCED,
})

#: Run-exit reclamation may end any open contribution at any point with these.
_RECLAMATION_REASONS = frozenset({"operator_stop", "unchanged_branch"})

Token = str


def _end(reason: str) -> Token:
    return f"{_END}[{reason}]"


def _lifecycle_sequences() -> list[tuple[Token, ...]]:
    """Every complete lifecycle the order admits, before waivers.

    ::

        contribution.start
          ( work_finished
              ( end[unchanged_branch]
              | [parked] admitted started branch_observed recovery_started{0..3}
                  ( published auto_close [release.advanced] end[published]
                  | end[serial_fallback] ) )
          | end[checkpoint_failed | unchanged_branch | operator_stop] )

    The language is finite, so it is enumerated rather than parsed: that keeps
    waiver filtering and prefix checks trivially correct.
    """
    integration_tails = [
        (_PUBLISHED, _AUTO_CLOSE, _end("published")),
        (_PUBLISHED, _AUTO_CLOSE, _RELEASE_ADVANCED, _end("published")),
        (_end("serial_fallback"),),
    ]
    after_work: list[tuple[Token, ...]] = [(_end("unchanged_branch"),)]
    for parked, recoveries, tail in itertools.product(
        ((), (_PARKED,)), range(4), integration_tails
    ):
        after_work.append(
            parked
            + (_ADMITTED, _STARTED, _BRANCH_OBSERVED)
            + (_RECOVERY_STARTED,) * recoveries
            + tail
        )
    bodies = [(_WORK_FINISHED, *rest) for rest in after_work] + [
        (_end(reason),)
        for reason in ("checkpoint_failed", "unchanged_branch", "operator_stop")
    ]
    return [(_START, *body) for body in bodies]


def _token_type(token: Token) -> str:
    return token.split("[", 1)[0]


def _admitted_lifecycles(
    waived: Iterable[str],
) -> tuple[frozenset[tuple[Token, ...]], frozenset[tuple[Token, ...]]]:
    """The complete lifecycles and their proper prefixes, with waived types dropped."""
    skip = frozenset(waived)
    complete = frozenset(
        tuple(token for token in sequence if _token_type(token) not in skip)
        for sequence in _lifecycle_sequences()
    )
    prefixes = frozenset(
        sequence[:cut] for sequence in complete for cut in range(len(sequence))
    )
    return complete, prefixes


def _lifecycles(events: list[dict[str, Any]]) -> dict[str, list[Token]]:
    """Each contribution's ordered lifecycle tokens, read off one Event log.

    ``wrapper.release.advanced`` is Run-scoped on the wire (the fixture's own
    rolling stream carries it unstamped), so it is attributed to the open
    contribution whose issue it names. Every other ordered type must carry the
    contribution triple to count.
    """
    lifecycles: dict[str, list[Token]] = {}
    open_by_issue: dict[Any, str] = {}
    for event in events:
        kind = event["type"]
        if kind not in _ORDERED_TYPES:
            continue
        contribution = event.get("contribution_id")
        if contribution is None:
            if kind != _RELEASE_ADVANCED or event.get("iter") is not None:
                continue
            contribution = open_by_issue.get(event.get("issue"))
            if contribution is None:
                continue
        token = _end(str(event.get("reason"))) if kind == _END else kind
        lifecycles.setdefault(contribution, []).append(token)
        if kind == _START:
            open_by_issue[event.get("issue")] = contribution
        elif kind == _END:
            open_by_issue.pop(event.get("issue"), None)
    return lifecycles


def gate_findings(
    logs: Mapping[str, list[dict[str, Any]]],
    *,
    schema: Mapping[str, Any],
    waivers: Mapping[str, int],
) -> list[str]:
    """Every way ``logs`` fail the ``contribution_events: true`` obligation."""
    identity = schema["contribution_identity"]
    declared = set(identity["lifecycle_types"]) | set(
        identity["scheduler_scoped_types"]
    )
    emitted = {event["type"] for events in logs.values() for event in events}
    findings = [
        f"declared {kind} is never emitted by any scenario and has no waiver"
        for kind in sorted(declared - emitted - set(waivers))
    ]
    findings += [
        f"waived {kind} (#{waivers[kind]}) is emitted; delete its waiver"
        for kind in sorted(set(waivers) & emitted)
    ]
    findings += [
        f"waiver {kind} (#{waivers[kind]}) names no declared type"
        for kind in sorted(set(waivers) - declared)
    ]
    complete, prefixes = _admitted_lifecycles(waivers)
    for scenario, events in logs.items():
        for contribution, tokens in _lifecycles(events).items():
            sequence = tuple(
                token for token in tokens if _token_type(token) not in waivers
            )
            if sequence in complete:
                continue
            if (
                sequence
                and sequence[:-1] in prefixes
                and sequence[-1] in {_end(r) for r in _RECLAMATION_REASONS}
            ):
                continue
            findings.append(
                f"{scenario}/{contribution} lifecycle is out of order: "
                f"{list(sequence)}"
            )
    return findings


# ---------------------------------------------------------------------------
# Scenarios: faked Parallel Runs through the production loop.
# ---------------------------------------------------------------------------

_PARALLEL_SAFE = ["ready-for-agent", "parallel-safe"]
_SERIAL = ["ready-for-agent"]


def _wire(
    root: Path,
    mp: pytest.MonkeyPatch,
    issues: list[gh_module.Issue],
    gate: FakeGateRunner,
    *,
    serial_closes: bool = False,
    client_cls: type[lp._ParallelFakeClient] = lp._ParallelFakeClient,
    issue_view_errors: Mapping[int, Exception] | None = None,
) -> tuple[Any, FakeGitHubClient]:
    fake_git = lp._wire_repo(root)
    mp.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=issues,
        issue_view_errors=dict(issue_view_errors or {}),
    )
    mp.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    client = client_cls(
        fake_git=fake_git,
        scripted_events=[lp._usage_event("claude-opus-4.8-max")],
        serial_closes=serial_closes,
    )
    mp.setattr(loop_module, "_make_client", lambda: client)
    mp.setattr(loop_module, "_make_gate_runner", lambda: gate)
    return fake_git, fake_gh


def _config(max_iterations: int, **overrides: Any) -> Any:
    return lp.RunConfig(
        model="claude-opus-4.8-max",
        issue_source=overrides.pop("issue_source", "github"),
        max_iterations=max_iterations,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
        **overrides,
    )


def _two_lanes_park_against_a_full_backlog(root: Path, mp: pytest.MonkeyPatch) -> None:
    """Two Lanes, and a third finisher that parks against the full backlog.

    The timeline is forced, not raced: #42 gates red and its recovery session
    is held, so it keeps Integration; #44 takes the freed Lane and is admitted
    first, filling the backlog; only then is #43 released, so it parks.
    """
    hold_43, hold_44, hold_resolution = (asyncio.Event() for _ in range(3))
    resolution_started, admitted_44, parked_43 = (asyncio.Event() for _ in range(3))
    real_finish_work = rolling_scheduler.RollingScheduler.finish_work

    def spy_finish_work(self, contribution, **kwargs: Any) -> str:
        disposition = real_finish_work(self, contribution, **kwargs)
        if contribution.ref == 44 and disposition == rolling_scheduler.ADMITTED:
            admitted_44.set()
        if contribution.ref == 43 and disposition == rolling_scheduler.PARKED:
            parked_43.set()
        return disposition

    mp.setattr(rolling_scheduler.RollingScheduler, "finish_work", spy_finish_work)

    class _HeldClient(lp._ParallelFakeClient):
        async def create_session(self, **kwargs: Any) -> lp._ParallelFakeSession:
            session = await super().create_session(**kwargs)
            directory = str(kwargs.get("working_directory") or "")
            if "/integrate/" in directory:
                gate_on, announce = hold_resolution, resolution_started
            elif directory.endswith("issue-43"):
                gate_on, announce = hold_43, None
            elif directory.endswith("issue-44"):
                gate_on, announce = hold_44, None
            else:
                return session
            real = session.send_and_wait

            async def held(prompt: str, **extra: Any) -> Any:
                if announce is not None:
                    announce.set()
                await gate_on.wait()
                return await real(prompt, **extra)

            session.send_and_wait = held  # type: ignore[method-assign]
            return session

    _wire(
        root, mp,
        [lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43, 44)],
        FakeGateRunner(by_issue={42: [False]}),
        client_cls=_HeldClient,
    )

    async def scenario() -> int:
        run = asyncio.create_task(loop_module.run(_config(0)))
        await asyncio.wait_for(resolution_started.wait(), timeout=5)
        hold_44.set()
        await asyncio.wait_for(admitted_44.wait(), timeout=5)
        hold_43.set()
        await asyncio.wait_for(parked_43.wait(), timeout=5)
        hold_resolution.set()
        return await asyncio.wait_for(run, timeout=15)

    assert asyncio.run(scenario()) == 0


def _red_then_green_recovery(root: Path, mp: pytest.MonkeyPatch) -> None:
    """#42 gates red in its private stage, then green on its first Recovery."""
    _wire(
        root, mp,
        [lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43)],
        FakeGateRunner(outcomes=[False, True], default=True),
    )
    assert asyncio.run(loop_module.run(_config(2))) == 0


def _k_exhausted_recovery_handoff(root: Path, mp: pytest.MonkeyPatch) -> None:
    """#42 stays red through all K Recovery attempts and is handed to serial."""
    _wire(
        root, mp,
        [lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43)],
        FakeGateRunner(outcomes=[False] * 4, default=True),
        serial_closes=True,
    )
    assert asyncio.run(loop_module.run(_config(0))) == 0


def _unchanged_branch_lane(root: Path, mp: pytest.MonkeyPatch) -> None:
    """Both Lanes report no progress, so neither reaches Integration."""
    _wire(
        root, mp,
        [lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43)],
        FakeGateRunner(),
    )
    real = loop_module._ParallelLoop
    mp.setattr(
        loop_module,
        "_ParallelLoop",
        lambda *args, **kwargs: real(
            *args,
            **{**kwargs, "execution_host": lp._NoProgressEndingExecutionHost()},
        ),
    )
    assert asyncio.run(loop_module.run(_config(2))) == 0


def _serial_latch_then_an_empty_refill_turn(root: Path, mp: pytest.MonkeyPatch) -> None:
    """A Lane runs, serial-required work latches, and the refill reserves nothing.

    The Pool also carries a ``ready-for-agent`` issue the discriminator drops
    and a Blocked one a serial Pickup passes over, because a mixed real Pool
    does.
    """
    blocked = BlockedByRead(
        total_count=1, nodes=(BlockerNode(ref="x/y#7", state="open"),)
    )
    _wire(
        root, mp,
        [
            lp._make_issue(42, labels=_PARALLEL_SAFE),
            lp._make_issue(44, labels=_SERIAL, blocked_by=blocked),
            lp._make_issue(45, labels=_SERIAL),
            lp._make_issue(46, labels=_SERIAL, body="A PRD with neither section."),
        ],
        FakeGateRunner(),
        serial_closes=True,
    )
    asyncio.run(loop_module.run(_config(3)))
    # The Blocked issue outlives the Run, so it ends ``all_blocked``, not drained.
    (run_end,) = [
        e for e in lp._logged_events(root) if e["type"] == "wrapper.run.end"
    ]
    assert run_end["outcome"] == "all_blocked"


def _dynamic_routing_for_two_lanes(root: Path, mp: pytest.MonkeyPatch) -> None:
    """Two Lanes route through Dynamic routing while the tail is prepared ahead."""
    _client, _spied, exit_code = lp._rolling_dynamic_run(
        root,
        mp,
        issues=[lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43, 44, 45)],
        max_iterations=3,
    )
    assert exit_code == 0


def _rate_limited_lanes_narrow(root: Path, mp: pytest.MonkeyPatch) -> None:
    """A throttled Pool read narrows the Effective Lane limit."""
    mp.setattr(
        loop_module.execution_host_module, "local_execution_host_capacity", lambda: 6
    )
    _wire(
        root, mp,
        [lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43)]
        + [lp._make_issue(n, labels=_SERIAL) for n in (70, 71, 72)],
        FakeGateRunner(),
        issue_view_errors={n: lp._throttled_read(n) for n in (70, 71, 72)},
    )
    lp._wire_production_pressure(mp)
    asyncio.run(loop_module.run(_config(2)))


def _drift_timeline(root: Path, mp: pytest.MonkeyPatch) -> None:
    """#42 held in recovery until #44 is cut, #43 is admitted, and #44 parks.

    A full Integration backlog stops refill, so #44 has to be cut while #43
    is still working. #43 then fills the backlog and waits on the lock #42
    holds; #44 finishes into that full backlog and parks. Releasing #42
    publishes once, so #43 observes 1 and #44, admitted behind it, observes 2.
    """
    hold_43, hold_44, hold_resolution = (asyncio.Event() for _ in range(3))
    resolution_started, lane_44_started, admitted_43, parked_44 = (
        asyncio.Event() for _ in range(4)
    )
    real_finish_work = rolling_scheduler.RollingScheduler.finish_work

    def spy_finish_work(self, contribution, **kwargs: Any) -> str:
        disposition = real_finish_work(self, contribution, **kwargs)
        if contribution.ref == 43 and disposition == rolling_scheduler.ADMITTED:
            admitted_43.set()
        if contribution.ref == 44 and disposition == rolling_scheduler.PARKED:
            parked_44.set()
        return disposition

    mp.setattr(rolling_scheduler.RollingScheduler, "finish_work", spy_finish_work)

    class _HeldClient(lp._ParallelFakeClient):
        async def create_session(self, **kwargs: Any) -> lp._ParallelFakeSession:
            session = await super().create_session(**kwargs)
            directory = str(kwargs.get("working_directory") or "")
            if "/integrate/" in directory and directory.rstrip("/").endswith("issue-42"):
                gate_on, announce = hold_resolution, resolution_started
            elif directory.endswith("issue-43") and "/integrate/" not in directory:
                gate_on, announce = hold_43, None
            elif directory.endswith("issue-44") and "/integrate/" not in directory:
                gate_on, announce = hold_44, lane_44_started
            else:
                return session
            real = session.send_and_wait

            async def held(prompt: str, **extra: Any) -> Any:
                if announce is not None:
                    announce.set()
                await gate_on.wait()
                return await real(prompt, **extra)

            session.send_and_wait = held  # type: ignore[method-assign]
            return session

    _wire(
        root, mp,
        [lp._make_issue(n, labels=_PARALLEL_SAFE) for n in (42, 43, 44)],
        FakeGateRunner(by_issue={42: [False, True]}),
        client_cls=_HeldClient,
    )

    async def scenario() -> int:
        run = asyncio.create_task(loop_module.run(_config(0)))
        await asyncio.wait_for(resolution_started.wait(), timeout=5)
        await asyncio.wait_for(lane_44_started.wait(), timeout=5)
        hold_43.set()
        await asyncio.wait_for(admitted_43.wait(), timeout=5)
        hold_44.set()
        await asyncio.wait_for(parked_44.wait(), timeout=5)
        hold_resolution.set()
        return await asyncio.wait_for(run, timeout=15)

    assert asyncio.run(scenario()) == 0


def _branch_observations(events: list[dict[str, Any]]) -> dict[int, int]:
    return {
        int(event["issue"]): event["base_publications_since_cut"]
        for event in events
        if event["type"] == _BRANCH_OBSERVED
    }


def _started_once_per_integration(events: list[dict[str, Any]]) -> bool:
    started = [event["contribution_id"] for event in events if event["type"] == _STARTED]
    return len(started) == 3 and len(set(started)) == 3


def _refuse_the_stage_cut(
    root: Path, mp: pytest.MonkeyPatch
) -> dict[str, bool | int | None]:
    """Fail the Integration-stage cut and report what was already on disk."""
    fake_git, _fake_gh = _wire(
        root, mp,
        [lp._make_issue(42, labels=_PARALLEL_SAFE)],
        FakeGateRunner(),
    )
    seen: dict[str, bool | int | None] = {}
    real_add = fake_git.add_worktree

    def add_worktree(path: Path, *, branch: str, base: str) -> Any:
        if "/integrate/" in str(path):
            on_disk = lp._logged_events(root)
            started = [event for event in on_disk if event["type"] == _STARTED]
            observed = [
                event for event in on_disk if event["type"] == _BRANCH_OBSERVED
            ]
            seen["started"] = len(started) == 1
            seen["branch_observed"] = (
                observed[0]["base_publications_since_cut"] if observed else None
            )
            raise loop_module.git_module.GitError(
                ("git", "worktree", "add"), 1, "stage cut refused"
            )
        return real_add(path, branch=branch, base=base)

    fake_git.add_worktree = add_worktree  # type: ignore[method-assign]
    assert asyncio.run(loop_module.run(_config(1))) == 0
    return seen


def _parallel_over_a_non_rolling_source(root: Path, mp: pytest.MonkeyPatch) -> None:
    """A Parallel Run whose source cannot offer Lane work degrades, and says so."""
    fake_git = lp._wire_repo(root)
    mp.setattr(loop_module, "_make_git_client", lambda: fake_git)
    mp.setattr(
        loop_module,
        "_make_client",
        lambda: lp._ParallelFakeClient(fake_git=fake_git, scripted_events=[]),
    )
    mp.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    assert asyncio.run(loop_module.run(_config(1, issue_source="prds"))) == 0


SCENARIOS: dict[str, Callable[[Path, pytest.MonkeyPatch], None]] = {
    "two-lanes-park": _two_lanes_park_against_a_full_backlog,
    "red-then-green-recovery": _red_then_green_recovery,
    "k-exhausted-recovery-handoff": _k_exhausted_recovery_handoff,
    "unchanged-branch-lane": _unchanged_branch_lane,
    "serial-latch-empty-refill": _serial_latch_then_an_empty_refill_turn,
    "dynamic-routing-lanes": _dynamic_routing_for_two_lanes,
    "rate-limited-narrowing": _rate_limited_lanes_narrow,
    "non-rolling-degrade": _parallel_over_a_non_rolling_source,
}

_LOGS: dict[str, list[dict[str, Any]]] = {}


@pytest.fixture
def scenario_logs(tmp_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Run every scenario once per session and return its Event log.

    Each Run gets its own repository root and its own monkeypatch scope over
    the suite's Run fixtures, and is observed only through the JSONL it wrote.
    """
    if not _LOGS:
        agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        logs: dict[str, list[dict[str, Any]]] = {}
        for name, scenario in SCENARIOS.items():
            root = tmp_path / name
            root.mkdir()
            (root / "AGENTS.md").write_text(agents, encoding="utf-8")
            with pytest.MonkeyPatch.context() as mp:
                scenario(root, mp)
            logs[name] = lp._logged_events(root)
        _LOGS.update(logs)
    return _LOGS


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


def _members_declaring(value: bool) -> list[str]:
    return sorted(
        member
        for member, manifest in _EVENT_SCHEMA["parallel_capabilities"][
            "orchestrators"
        ].items()
        if manifest["contribution_events"] is value
    )


def test_only_the_python_runner_declares_contribution_events() -> None:
    """The gate binds members that declare ``true``; shell and PowerShell owe nothing.

    Only the Python Runner's production loop is driven here, so a second member
    declaring ``true`` must bring its own behavioural proof before this passes.
    """
    assert _members_declaring(True) == ["python"]
    assert _members_declaring(False) == ["powershell", "shell"]
    assert events_module.PYTHON_PARALLEL_CAPABILITIES["contribution_events"] is True


def test_the_initial_waivers_each_name_their_producer_ticket() -> None:
    assert WAIVERS == {
        "wrapper.rolling.refill_turn": 686,
    }


def _recovery_attempts(events: list[dict[str, Any]]) -> dict[Any, list[tuple[int, int]]]:
    by_issue: dict[Any, list[tuple[int, int]]] = {}
    for event in events:
        if event["type"] != _RECOVERY_STARTED:
            continue
        by_issue.setdefault(event["issue"], []).append(
            (event["attempt"], event["max_attempts"])
        )
    return by_issue


def test_recovery_started_is_one_record_per_attempt_and_never_more_than_three(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    """A green Recovery emits one; an exhausted one emits 1, 2, 3 then serial_fallback.

    ``max_attempts`` is the immutable K, not a count that grows with the
    attempt. The record belongs to the contribution whose gate was red, so a
    later Lane does not inherit the exhausted outcomes.
    """
    assert _recovery_attempts(scenario_logs["red-then-green-recovery"]) == {
        42: [(1, 3)]
    }

    exhausted = scenario_logs["k-exhausted-recovery-handoff"]
    assert _recovery_attempts(exhausted) == {42: [(1, 3), (2, 3), (3, 3)]}
    ended = next(
        event
        for event in exhausted
        if event["type"] == _END and event["issue"] == 42
    )
    assert ended["reason"] == "serial_fallback"
    assert ended["published"] is False
    # The rollup outcome keeps its underscore spelling; the issue row is the
    # Dashboard status. An unpublished, unclosed handoff is no-progress.
    assert ended["summary"]["closure_outcome"] == "no_progress"
    assert ended["issues"][0]["status"] == "no-progress"


def test_recovery_started_is_on_disk_before_the_agent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record precedes the session it names, not a summary written after."""
    root = tmp_path / "before-session"
    root.mkdir()
    (root / "AGENTS.md").write_text(
        (tmp_path / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    seen: list[dict[str, Any]] = []

    class _HeldClient(lp._ParallelFakeClient):
        async def create_session(self, **kwargs: Any) -> lp._ParallelFakeSession:
            session = await super().create_session(**kwargs)
            directory = str(kwargs.get("working_directory") or "")
            if "/integrate/" not in directory:
                return session
            real = session.send_and_wait

            async def held(prompt: str, **extra: Any) -> Any:
                recovery = [
                    event
                    for event in lp._logged_events(root)
                    if event["type"] == _RECOVERY_STARTED
                ]
                seen.extend(recovery)
                return await real(prompt, **extra)

            session.send_and_wait = held  # type: ignore[method-assign]
            return session

    _wire(
        root,
        monkeypatch,
        [lp._make_issue(42, labels=_PARALLEL_SAFE)],
        FakeGateRunner(outcomes=[False, True]),
        client_cls=_HeldClient,
    )
    assert asyncio.run(loop_module.run(_config(1))) == 0
    assert [(event["attempt"], event["max_attempts"]) for event in seen] == [(1, 3)]


def test_started_and_branch_drift_follow_the_lane_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``started`` once per Integration, then drift before the stage is cut.

    Holding #42 in recovery until a third Lane is cut is the rolling stream's
    shape: #42 observes 0 and publishes, #43 observes 1, #44 observes 2. A
    stage that cannot be cut still has both records on disk before the cut
    fails, and that observation is a count, not null — the Lane cut was seen.
    """
    root = tmp_path / "drift"
    root.mkdir()
    (root / "AGENTS.md").write_text(
        (tmp_path / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _drift_timeline(root, monkeypatch)
    observed = _branch_observations(lp._logged_events(root))
    assert observed == {42: 0, 43: 1, 44: 2}
    assert _started_once_per_integration(lp._logged_events(root))

    refused = tmp_path / "stage-refused"
    refused.mkdir()
    (refused / "AGENTS.md").write_text(
        (tmp_path / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    already_on_disk = _refuse_the_stage_cut(refused, monkeypatch)
    assert already_on_disk == {"started": True, "branch_observed": 0}
    end = next(
        event
        for event in lp._logged_events(refused)
        if event["type"] == _END
    )
    assert end["reason"] == "serial_fallback"


def test_python_parallel_runs_emit_every_declared_contribution_event(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    """ADR-0065: ``contribution_events: true`` holds by behaviour, not by grep."""
    assert gate_findings(scenario_logs, schema=_EVENT_SCHEMA, waivers=WAIVERS) == []


def test_every_scenario_ran_a_contribution_or_named_its_degrade(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    """A scenario that silently ran serially would satisfy nothing it claims."""
    for name, events in scenario_logs.items():
        types = {event["type"] for event in events}
        if name == "non-rolling-degrade":
            assert "wrapper.parallel.degraded" in types
        else:
            assert _START in types, f"{name} started no Lane contribution"


def test_declaring_an_unemitted_unwaived_type_turns_the_gate_red(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    schema = json.loads(json.dumps(_EVENT_SCHEMA))
    schema["contribution_identity"]["scheduler_scoped_types"].append(
        "wrapper.rolling.never_produced"
    )
    assert gate_findings(scenario_logs, schema=schema, waivers=WAIVERS) == [
        "declared wrapper.rolling.never_produced is never emitted by any "
        "scenario and has no waiver"
    ]


def test_a_waived_type_that_a_scenario_emits_turns_the_gate_red(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    waivers = {**WAIVERS, "wrapper.integration.published": 999}
    assert gate_findings(scenario_logs, schema=_EVENT_SCHEMA, waivers=waivers) == [
        "waived wrapper.integration.published (#999) is emitted; delete its waiver"
    ]


def test_reordering_two_lifecycle_emissions_turns_the_gate_red(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    events = scenario_logs["red-then-green-recovery"]
    published = next(
        index for index, event in enumerate(events) if event["type"] == _PUBLISHED
    )
    contribution = events[published]["contribution_id"]
    closed = next(
        index
        for index, event in enumerate(events)
        if event["type"] == _AUTO_CLOSE
        and event.get("contribution_id") == contribution
    )
    swapped = list(events)
    swapped[published], swapped[closed] = swapped[closed], swapped[published]

    findings = gate_findings(
        {**scenario_logs, "red-then-green-recovery": swapped},
        schema=_EVENT_SCHEMA,
        waivers=WAIVERS,
    )

    assert len(findings) == 1
    assert findings[0].startswith(
        f"red-then-green-recovery/{contribution} lifecycle is out of order"
    )


def test_the_order_admits_run_exit_reclamation_at_any_point() -> None:
    complete, prefixes = _admitted_lifecycles(())
    started = (_START, _WORK_FINISHED, _ADMITTED, _STARTED)
    assert started in prefixes
    logs = {
        "reclaimed": [
            {"type": token, "contribution_id": "c1", "issue": 1}
            for token in started
        ]
        + [{"type": _END, "contribution_id": "c1", "issue": 1, "reason": "operator_stop"}]
    }
    schema = {
        "contribution_identity": {"lifecycle_types": [], "scheduler_scoped_types": []}
    }
    assert gate_findings(logs, schema=schema, waivers={}) == []
    logs["reclaimed"][-1]["reason"] = "published"
    assert gate_findings(logs, schema=schema, waivers={}) != []
    assert all(sequence[-1].startswith(_END) for sequence in complete)


def test_a_full_backlog_parks_then_admits_from_the_fifo_after_the_freeing_end(
    scenario_logs: dict[str, list[dict[str, Any]]],
) -> None:
    """Parking keeps the Lane; FIFO admission follows the freeing contribution.end.

    #42 and #44 fill the backlog and are admitted directly. #43 parks, still
    holding its Lane, and is admitted only after the contribution whose
    finalization freed the slot has ended. Nothing refills that Lane in between.
    """
    events = scenario_logs["two-lanes-park"]
    parked = [event for event in events if event["type"] == _PARKED]
    admitted = [event for event in events if event["type"] == _ADMITTED]

    assert [event["issue"] for event in parked] == [43]
    assert {event["issue"] for event in admitted} == {42, 43, 44}
    for event in (*parked, *admitted):
        assert event["contribution_id"]
        assert event["lane_id"]
        assert event["iter"] is None

    parked_at = events.index(parked[0])
    admitted_43 = next(event for event in admitted if event["issue"] == 43)
    admitted_at = events.index(admitted_43)
    assert parked_at < admitted_at
    between = events[parked_at + 1 : admitted_at]
    assert all(
        event["type"] != _START or event.get("lane_id") != parked[0]["lane_id"]
        for event in between
    ), "the parked Lane is refilled before admission"
    freeing = [
        event
        for event in between
        if event["type"] == _END and event.get("issue") != 43
    ]
    assert freeing, "FIFO admission must follow the freeing contribution.end"
    assert events.index(freeing[-1]) < admitted_at

    for issue in (42, 44):
        ordered = [
            event["type"]
            for event in events
            if event.get("issue") == issue
            and event["type"] in (_WORK_FINISHED, _PARKED, _ADMITTED, _END)
        ]
        assert _PARKED not in ordered
        assert (
            ordered.index(_WORK_FINISHED)
            < ordered.index(_ADMITTED)
            < ordered.index(_END)
        )


def test_an_unstamped_release_advance_belongs_to_the_open_contribution() -> None:
    events = [
        {"type": _START, "contribution_id": "c1", "issue": 7},
        {"type": _PUBLISHED, "contribution_id": "c1", "issue": 7},
        {"type": _AUTO_CLOSE, "contribution_id": "c1", "issue": 7},
        {"type": _RELEASE_ADVANCED, "iter": None, "issue": 7},
        {"type": _END, "contribution_id": "c1", "issue": 7, "reason": "published"},
    ]
    assert _lifecycles(events) == {
        "c1": [_START, _PUBLISHED, _AUTO_CLOSE, _RELEASE_ADVANCED, _end("published")]
    }
