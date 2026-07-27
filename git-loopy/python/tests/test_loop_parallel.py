"""End-to-end integration tests for Parallel mode (#219, ADR-0008/0009/0020).

Drives the Rolling-dispatch orchestrator (retiring the Wave barrier, #306)
through the public :func:`git_loopy.loop.run` seam with the SDK + git / gh /
gate seams faked, asserting the **observable effects** of concurrent isolated
execution — one worktree + branch per Lane created in a sibling directory,
each session pinned to its Lane's worktree via ``working_directory``,
per-Lane commits landing on Lane branches, and a Lane's worktree torn down
the moment ITS OWN contribution finishes (never waiting on any other Lane) —
not internal call ordering.

The fakes here (unlike the serial ``test_iteration_end_to_end`` client) record
the per-session ``working_directory`` and route each Lane's simulated agent
commit to the *right* worktree's child :class:`~tests.fakes.FakeGitClient`, so
the test can prove per-Lane isolation. As each Lane contribution finishes,
**Integration** (#62, #307) merges its branch in a **private Integration
stage** worktree in ascending issue-number order (serialized across
concurrently-admitted contributions via an internal lock, never blocking any
OTHER Lane's setup or session), gates it *there* via the injected
:class:`~git_loopy.gate.GateRunner`, and only a green stage is published to
base and its issue closed with the serial closure semantics (ADR-0020); a red
or conflicting stage never touches base and is handed to bounded
auto-resolution before falling back to a serial Iteration.

**A single eligible ``parallel-safe`` issue starts a Lane immediately**
(#219 §1.4) — the retired Wave's ">= 2 eligible" threshold is gone; see
:func:`test_parallel_single_eligible_issue_starts_lane_immediately`.

**No barrier — continuous refill (#219 §1.3, criteria #2/#3).** A finished
Lane's slot becomes available for refill the instant its contribution is
admitted or terminates, without waiting for any other Lane; one Lane's
worktree setup may freely overlap another Lane's agent session. See
:func:`test_parallel_lane_refills_without_waiting_for_sibling` and
:func:`test_parallel_lane_cap_never_exceeded_under_bursty_refill`.

**Integration is serialized and costs no Lane (#219 §4.11, #307 criteria
#4/#8).** At most one contribution is ever being integrated — a private
**Integration stage** is cut from base and published back to it on the strength
of base not moving in between, so a second concurrent Integration would publish
a result verified against a base that no longer exists. Recovery, meanwhile,
occupies no **Lane**: a contribution's Lane frees at admission and refills while
its bounded auto-resolution sessions are still in flight. See
:func:`test_parallel_integration_never_overlaps_another_contribution` and
:func:`test_parallel_auto_resolution_does_not_consume_the_lane_cap`.

**Membership is never authority (#219 §2.10).** Candidate discovery reads the
cheap **Pool** membership cache and pays the authoritative per-issue read only
at pickup; a candidate that fails that read costs no **Lane** and no
``max_iterations`` unit. See
:func:`test_parallel_stale_candidate_never_consumes_a_lane`.

**Per-Lane Checkpoint (ADR-0004).** A Lane whose agent leaves the tree dirty is
Checkpointed in *its own* worktree, on its own branch, attributed to its own
issue — never on the shared main worktree. See
:func:`test_parallel_lane_checkpoint_commits_in_its_own_worktree`.

**Drain-everything (#67, ADR-0008).** A Parallel run interleaves Lane work for
the ``parallel-safe`` issues with serial Iterations for every other
``ready-for-agent`` issue, in one run, draining all eligible work. Under
Rolling dispatch the shared Strike machine reacts once per **finalized Lane
contribution** (not once per round, since there is no round) — see
:func:`test_parallel_run_drains_lanes_then_serial_in_one_run`. The other
direction — a **serial-required** issue's latch releasing so held-back
Parallel-safe work refills a Lane, with the serial Iteration owning base alone
while it runs — is
:func:`test_parallel_lanes_resume_refilling_after_an_interleaved_serial_iteration`.

**Truthful termination (#308, #219 §2.13, §7.7).** A **Pool** the Run could not
fully read is not an empty Pool, and the serial-required half is only ever
visible to the driver's own reading of it — see
:func:`test_parallel_never_ends_empty_on_a_partial_pool_read`. The Strike
machine is shared with serial Iterations, so a serial Iteration reaching its
limit latches the same drain-confirmed abort a Lane contribution does — see
:func:`test_parallel_serial_iteration_strike_abort_stops_the_run_stuck`.

**Bounded adaptive Lane concurrency (#309, #219 §6).** The configured **Lane
cap** is a safety ceiling, not a utilization promise: under sustained
**Integration** backpressure, API rate limiting, AI-credit burn, or host/setup
pressure the Run narrows its own **Effective Lane limit** and says which signal
governed, and an operator who turns adaptation off keeps the static-safe
``min(cap, 3)`` however hard the same telemetry is throttled. See
:func:`test_parallel_narrows_lane_concurrency_under_sustained_rate_limits` and
:func:`test_parallel_with_adaptation_disabled_never_moves_the_lane_limit`.

**Contribution-centric accounting (#310, #219 §7).** Rolling dispatch has no
round, so the accounting unit is the **Lane contribution**: each opens and
closes its own ``wrapper.contribution.start``/``.end`` pair — never the serial
Iteration pair, which a contribution's null ``iter`` and identity triple
deliberately exclude it from — and its **Consumption**, timing, and durable
Run-summary row are its own however many times its **Lane** slot is refilled
underneath it. See
:func:`test_parallel_accounts_consumption_per_contribution_end_to_end`. The row
is cut only at finalization, so work still parked, integrating, or recovering
has no partial row — see
:func:`test_parallel_summary_carries_no_row_for_an_in_flight_contribution` — and
the record carries everything the Dashboard shows, so replaying it rebuilds the
same Dashboard: see
:func:`test_a_rolling_run_replays_from_its_own_record_to_the_same_dashboard`.

**Per-Lane worktree setup (#65, ADR-0008).** Before a Lane's session starts the
runner prepares its worktree via the injected
:class:`~git_loopy.worktree.WorktreeSetup` (``GIT_LOOPY_WORKTREE_SETUP`` or a
best-effort auto-detect); the setup runs once per Lane creation, before that
Lane's session, and a failure is surfaced (in the diagnostics log) rather than
aborting the Lane: see the ``test_parallel_*worktree_setup*`` tests.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pytest
from copilot.generated.session_events import (
    AssistantUsageData,
    SessionEvent,
    SessionEventType,
)

from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import loop as loop_module
from git_loopy import rolling_pressure
from git_loopy.config import RunConfig
from git_loopy.skill_catalog import build_skill_catalog
from git_loopy.worktree import SetupResult
from tests.fakes import FakeGateRunner, FakeGitClient, FakeGitHubClient


EXPECTED_RELEASE_VERSION = json.loads(
    (
        Path(__file__).parents[2] / "conformance" / "release-version.json"
    ).read_text(encoding="utf-8")
)["expected_release_version"]


# ---------------------------------------------------------------------------
# Parallel-aware SDK fakes — record working_directory + route per-Lane commits.
# ---------------------------------------------------------------------------


class _ParallelFakeSession:
    """A per-Lane SDK session stub pinned to one worktree.

    ``send_and_wait`` models the Lane's agent committing *into its own
    worktree* — it looks the live child :class:`FakeGitClient` up on the parent
    fake by ``working_directory`` and advances that Lane's log — so per-Lane
    commit accounting sees exactly that Lane's commit and no other. A ``None``
    working directory (the serial-fallback path) commits on the main worktree.
    """

    def __init__(
        self,
        *,
        on_event: Callable[[SessionEvent], None] | None,
        working_directory: str | None,
        fake_git: FakeGitClient,
        scripted_events: list[SessionEvent],
        serial_closes: bool = False,
    ) -> None:
        self._on_event = on_event
        self._working_directory = working_directory
        self._fake_git = fake_git
        self._scripted_events = scripted_events
        self._serial_closes = serial_closes
        self.session_id = f"fake-session-{working_directory}"
        self.send_and_wait_calls: list[tuple[str, float]] = []

    async def send_and_wait(
        self, prompt: str, *, timeout: float = 60.0, **_extra: Any
    ) -> SessionEvent | None:
        self.send_and_wait_calls.append((prompt, timeout))
        if self._working_directory is not None:
            target = self._fake_git.worktree_client(
                Path(self._working_directory)
            )
            # The Lane's agent commit references its issue so the reused serial
            # closure path fires at Integration. The worktree dir is named
            # ``issue-<N>`` (see ``_lane_worktree_path``), so parse N from it.
            ref = Path(self._working_directory).name.removeprefix("issue-")
            body = f"Closes #{ref}"
        else:
            target = self._fake_git
            # The serial-fallback agent "picks one" issue and closes it. Parse
            # the pool from the rendered ``=== Issue #N:`` block HEADERS only
            # (never the Previous-commits block, which can carry a stale
            # ``Closes #N``), pick the lowest, and reference it so the reused
            # serial closure path fires — enough to drain a plain
            # ``ready-for-agent`` issue and let a multi-round run reach an empty
            # pool. Opt-in so the no-progress serial fakes keep their behaviour.
            body = ""
            if self._serial_closes:
                refs = [
                    int(n) for n in re.findall(r"=== Issue #(\d+):", prompt)
                ]
                if refs:
                    body = f"Closes #{min(refs)}"
        if target is not None:
            target.simulate_agent_commit(
                subject="feat(lane): implement issue",
                body=body,
            )
        last: SessionEvent | None = None
        for evt in self._scripted_events:
            if self._on_event is not None:
                self._on_event(evt)
            last = evt
        return last

    async def disconnect(self) -> None:
        return None


class _ParallelFakeClient:
    """One long-lived client hosting N concurrent Lane sessions (in-process).

    Records every ``create_session`` call's ``working_directory`` (the seam
    the loop pins each Lane to its worktree with) and hands back a
    :class:`_ParallelFakeSession` bound to it. Subclasses override
    :attr:`_session_cls` to change what an agent session *does* without
    restating this bookkeeping.
    """

    _session_cls: type[_ParallelFakeSession] = _ParallelFakeSession

    def __init__(
        self,
        *,
        fake_git: FakeGitClient,
        scripted_events: list[SessionEvent],
        serial_closes: bool = False,
    ) -> None:
        self._fake_git = fake_git
        self._scripted_events = scripted_events
        self._serial_closes = serial_closes
        self.create_calls: list[dict[str, Any]] = []
        self.created: list[_ParallelFakeSession] = []
        self.start_call_count = 0
        self.stop_call_count = 0

    async def start(self) -> None:
        self.start_call_count += 1

    async def create_session(
        self,
        *,
        on_permission_request: Any,
        on_event: Callable[[SessionEvent], None] | None = None,
        on_user_input_request: Any = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        working_directory: str | None = None,
        **extra: Any,
    ) -> _ParallelFakeSession:
        self.create_calls.append(
            {
                "working_directory": working_directory,
                "model": model,
                "reasoning_effort": reasoning_effort,
                **extra,
            }
        )
        session = self._session_cls(
            on_event=on_event,
            working_directory=working_directory,
            fake_git=self._fake_git,
            scripted_events=self._scripted_events,
            serial_closes=self._serial_closes,
        )
        self.created.append(session)
        return session

    async def stop(self) -> None:
        self.stop_call_count += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage_event(model: str) -> SessionEvent:
    return SessionEvent(
        data=AssistantUsageData(
            input_tokens=100, output_tokens=50, model=model
        ),
        id=uuid4(),
        timestamp=datetime(2026, 5, 16, tzinfo=timezone.utc),
        type=SessionEventType.ASSISTANT_USAGE,
    )


_AFK_BODY = (
    "## Parent\n#49\n\n## What to build\nthing\n\n## Acceptance criteria\nbar"
)


@pytest.fixture(autouse=True)
def _stub_run_skill_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    async def discover(_client: object, **kwargs: object):
        return build_skill_catalog(
            (),
            repo_root=Path(str(kwargs["repo_root"])),
            packaged_skills_dir=Path(str(kwargs["packaged_skills_dir"])),
        )

    monkeypatch.setattr(loop_module, "_discover_skill_catalog", discover)


def _make_issue(
    number: int, *, labels: list[str], body: str = _AFK_BODY
) -> gh_module.Issue:
    return gh_module.Issue(
        number=number,
        title=f"Test issue {number}",
        body=body,
        labels=labels,
        state="OPEN",
        url=f"https://github.com/x/y/issues/{number}",
        comments=(),
    )


def _logged_events(tmp_path: Path) -> list[dict[str, Any]]:
    logs_dir = tmp_path / ".git-loopy" / "logs"
    lines = (
        next(logs_dir.glob("*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return [json.loads(raw) for raw in lines]


def _run_id(tmp_path: Path) -> str:
    """Recover the run's ULID from the logged event envelopes.

    Every event carries ``run_id`` (see ``events._envelope``), so the Lane /
    integration branch names a test needs to assert on can be reconstructed via
    ``git.lane_branch_name`` / ``git.integration_branch_name`` without the test
    having to know the run id a priori.
    """
    return _logged_events(tmp_path)[0]["run_id"]


def _lane_worktree_adds(fake_git: FakeGitClient) -> list[tuple[Path, str, str]]:
    """The **Lane** worktree adds only, dropping private Integration stages.

    Under ADR-0020 every admitted **Lane contribution** also opens a private
    ``_IntegrationStage`` worktree (``.../integrate/issue-<N>``) to merge and
    gate in before base is published, so raw ``worktree_adds`` no longer counts
    Lanes. Filtering on the branch keeps these assertions about dispatch rather
    than about Integration's internals.
    """
    return [add for add in fake_git.worktree_adds if "/integrate/" not in add[1]]


def _lane_worktree_removes(fake_git: FakeGitClient) -> list[Path]:
    """The **Lane** worktree teardowns only (see :func:`_lane_worktree_adds`)."""
    return [p for p in fake_git.worktree_removes if "integrate" not in p.parts]


def _lane_branch_deletes(fake_git: FakeGitClient) -> list[str]:
    """The **Lane** branch deletions only (see :func:`_lane_worktree_adds`).

    Every contribution's throwaway private Integration branch is reaped on every
    path, so a Lane branch's fate -- deleted on publication, kept as a
    breadcrumb otherwise -- is only readable with those filtered out.
    """
    return [b for b in fake_git.branch_deletes if "/integrate/" not in b]


def _wire_repo(
    tmp_path: Path, *, merge_conflicts: Sequence[int] = ()
) -> FakeGitClient:
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text(
        "You are ralph. Implement the AFK-ready issues.\n", encoding="utf-8"
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    return FakeGitClient(
        tmp_path,
        commits=[
            git_module.Commit(
                sha="0000000000000000000000000000000000000001",
                subject="prior commit",
                body="",
                date="2026-05-16",
            )
        ],
        dirty=False,
        untracked=False,
        merge_conflicts=merge_conflicts,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parallel_run_dispatches_two_lanes(tmp_path, monkeypatch) -> None:
    """Two eligible Lanes run concurrently; Integration lands + closes both.

    Both issues carry ``ready-for-agent`` + ``parallel-safe``, so with
    ``parallel=2`` both start a Lane (#219 §1.4 — no "wait for a second
    issue" threshold; see also
    :func:`test_parallel_single_eligible_issue_starts_lane_immediately`).
    Asserts (observable effects only): one worktree + Lane branch per issue
    created in a sibling directory, each session pinned to its Lane's
    worktree via ``working_directory``, each Lane's commit landing on its own
    branch, the worktrees torn down once each Lane's own contribution
    finishes, then Integration (#62) merging both green Lanes onto base and
    closing their issues in ascending issue-number order with the integrated
    branches deleted.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    fake_gate = FakeGateRunner()
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: fake_gate
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Two Lanes dispatched concurrently, one session each.
    assert len(fake_client.created) == 2
    assert fake_client.stop_call_count == 1

    # One worktree + branch per Lane, created in a sibling ``.worktrees`` dir
    # OUTSIDE the repo, one directory per issue.
    adds = _lane_worktree_adds(fake_git)
    assert len(adds) == 2, f"expected two Lane worktrees, got {adds}"
    add_paths = {p for (p, _b, _base) in adds}
    branches = sorted(b for (_p, b, _base) in adds)
    bases = {base for (_p, _b, base) in adds}
    assert bases == {"main"}, "Lanes are cut from the base branch"
    for path in add_paths:
        assert path.parent.parent.name == f"{tmp_path.name}.worktrees"
        assert tmp_path not in path.parents, "worktrees live OUTSIDE the repo"
    # Deterministic ``git-loopy/<run_id>/issue-<N>`` branch names, one run_id.
    assert branches[0].startswith("git-loopy/")
    assert branches[0].endswith("/issue-42")
    assert branches[1].endswith("/issue-43")
    run_segs = {b.split("/issue-")[0] for b in branches}
    assert len(run_segs) == 1, "all Lanes share one run_id branch prefix"

    # Each session is pinned to its Lane's worktree via working_directory,
    # and the set of pinned dirs equals the set of created worktrees.
    pinned = {c["working_directory"] for c in fake_client.create_calls}
    assert None not in pinned, "every Lane session is worktree-pinned"
    assert {Path(p) for p in pinned} == add_paths

    # Each Lane's commit advanced its OWN branch: two commit.recorded events.
    events = _logged_events(tmp_path)
    commit_events = [e for e in events if e["type"] == "wrapper.commit.recorded"]
    assert len(commit_events) == 2, (
        f"expected one commit per Lane, got {len(commit_events)}"
    )

    # Each Lane's worktree is torn down once its OWN contribution finishes
    # (no barrier — see `_run_lane_lifecycle`), and none are left live.
    assert len(_lane_worktree_removes(fake_git)) == 2
    assert set(_lane_worktree_removes(fake_git)) == add_paths
    assert fake_git.active_worktrees == []

    # Integration (#62) landed both green Lanes on base — base advanced past the
    # prior commit — and closed both issues via the serial closure path, in
    # ascending issue-number order.
    assert fake_git.head_sha() != "0000000000000000000000000000000000000001"
    assert [n for (n, _c) in fake_gh.issue_close_calls] == [42, 43]
    # One wrapper.auto_close event per landed + closed Lane, same order.
    auto_closes = [e for e in events if e["type"] == "wrapper.auto_close"]
    assert [e["issue"] for e in auto_closes] == [42, 43]
    # Both integrated Lane branches deleted (breadcrumbs are for failures only),
    # alongside the throwaway private Integration branch each one was gated on.
    assert sorted(_lane_branch_deletes(fake_git)) == sorted(branches)


def test_parallel_lanes_open_sessions_with_per_issue_routed_model(
    tmp_path, monkeypatch
) -> None:
    """Each Lane resolves its own (model, effort) at Active-issue pickup (#148).

    A two-Lane Run where issue 42 carries ``task-type:docs`` — routed to
    ``gpt-5-mini @ medium`` by the run config's ``[routing]`` map — and issue 43
    is unlabelled (so it keeps the global default ``claude-opus-4.8 @ max``).
    Asserts, at the session-creation seam, that each Lane opens its session on
    ITS OWN resolved pair: Lanes resolve independently and never contend over a
    shared model choice.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(
                42,
                labels=["ready-for-agent", "parallel-safe", "task-type:docs"],
            ),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("gpt-5-mini")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    fake_gate = FakeGateRunner()
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: fake_gate)

    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Map each Lane's work-session create_session call by its worktree.
    by_dir = {
        Path(c["working_directory"]).name: c
        for c in fake_client.create_calls
        if c["working_directory"]
    }
    # The routed Lane (task-type:docs) opened on the routed (model, effort)...
    assert by_dir["issue-42"]["model"] == "gpt-5-mini"
    assert by_dir["issue-42"]["reasoning_effort"] == "medium"
    # ...while the unlabelled Lane opened on the global default.
    assert by_dir["issue-43"]["model"] == "claude-opus-4.8"
    assert by_dir["issue-43"]["reasoning_effort"] == "max"


def test_parallel_auto_resolution_session_reuses_lane_routed_model(
    tmp_path, monkeypatch
) -> None:
    """A Lane's auto-resolution session reuses that Lane's routed pair (#148).

    Issue 42 (``task-type:docs`` -> ``gpt-5-mini @ medium``) goes red on its
    private Integration gate, so Integration runs a bounded auto-resolution
    agent for it in that same stage; that attempt is green and publishes. Asserts the auto-resolution
    session opened in 42's dedicated integration worktree used the SAME resolved
    ``(model, effort)`` the Lane resolved once at pickup — not the global
    default — so a Lane resolves its route exactly once.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(
                42,
                labels=["ready-for-agent", "parallel-safe", "task-type:docs"],
            ),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("gpt-5-mini")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    # 42 is red on its initial landing, green on its first auto-resolution
    # attempt; 43 (default) is green on its first landing.
    monkeypatch.setattr(
        loop_module,
        "_make_gate_runner",
        lambda: FakeGateRunner(outcomes=[False, True], default=True),
    )

    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # The auto-resolution agent ran in 42's dedicated integration worktree on
    # the Lane's routed pair (never the global default).
    resolution_calls = [
        c
        for c in fake_client.create_calls
        if c["working_directory"] and "/integrate/" in c["working_directory"]
    ]
    assert resolution_calls, "expected an auto-resolution session for the Lane"
    for c in resolution_calls:
        assert c["working_directory"].endswith("/issue-42")
        assert c["model"] == "gpt-5-mini"
        assert c["reasoning_effort"] == "medium"

    # Both issues still landed and closed.
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43]


def test_parallel_lane_routing_warning_surfaces_per_issue(
    tmp_path, monkeypatch
) -> None:
    """An unknown task-type key warns per-issue on the diagnostics channel (#148).

    Issue 42 carries ``task-type:mystery`` — a key with no ``[routing]`` entry —
    while routing is active, so :func:`resolve_iteration_model` falls the Lane
    back to the global default AND warns. Asserts the advisory surfaces on the
    existing per-issue diagnostics channel, scoped to that Lane's issue, and
    that the Lane still opened on the gated global default.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(
                42,
                labels=["ready-for-agent", "parallel-safe", "task-type:mystery"],
            ),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # The routing advisory is attributed to Lane #42 on the diagnostics log.
    diag = _diag_log(tmp_path)
    assert "lane #42 routing:" in diag
    assert "task-type:mystery" in diag
    # ...and the unknown-key Lane fell back to the gated global default.
    by_dir = {
        Path(c["working_directory"]).name: c
        for c in fake_client.create_calls
        if c["working_directory"]
    }
    assert by_dir["issue-42"]["model"] == "claude-opus-4.8"
    assert by_dir["issue-42"]["reasoning_effort"] == "max"


def test_parallel_lanes_stamp_events_with_lane_issue(tmp_path, monkeypatch) -> None:
    """Each Lane's streamed events carry the deterministic ``lane_issue`` (#66).

    The multi-active Dashboard (ADR-0008) folds each Lane's output by an
    explicit runner stamp rather than the ``<working issue=N>`` marker: the Lane
    session stamps its recorded events (here the per-turn ``usage.tokens``), and
    the runner stamps the per-Lane ``commit.recorded`` / ``auto_close`` emits.
    This pins that end-to-end — every per-Lane event names its issue.

    Under Rolling dispatch (#219, ADR-0020) a Lane contribution has no
    "round", so it never emits ``wrapper.iteration.start``/``.end`` — those and
    their positive ``iter`` belong to serial work. Its accounting boundary is
    its own ``wrapper.contribution.start``/``.end`` pair, carrying the identity
    triple and a null ``iter`` so replay never needs a mutable Lane-to-issue
    lookup (#310).
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    events = _logged_events(tmp_path)

    activations = [
        e for e in events if e["type"] == "wrapper.issue.activated"
    ]
    assert {e["issue"] for e in activations} == {42, 43}
    assert {e["binding_source"] for e in activations} == {"lane_pickup"}
    assert all(e["lane_issue"] == e["issue"] for e in activations)
    assert all(e["activated_at"] == e["ts"] for e in activations)

    # Each Lane's per-turn usage is session-stamped with its own issue (proving
    # the Lane session was created with ``issue_ref``).
    usage_events = [e for e in events if e["type"] == "usage.tokens"]
    assert usage_events, "expected per-Lane usage events"
    assert {e["lane_issue"] for e in usage_events} == {42, 43}

    # Each per-Lane commit is runner-stamped with its Lane's issue.
    commit_events = [e for e in events if e["type"] == "wrapper.commit.recorded"]
    assert {e["lane_issue"] for e in commit_events} == {42, 43}

    # Each landed closure is stamped, and the stamp matches the closed issue.
    auto_closes = [e for e in events if e["type"] == "wrapper.auto_close"]
    assert auto_closes, "expected per-Lane closures"
    for e in auto_closes:
        assert e["lane_issue"] == e["issue"]

    # Run-scope envelopes stay unstamped.
    for e in events:
        if e["type"] in ("wrapper.run.start", "wrapper.run.end"):
            assert "lane_issue" not in e
    run_start = next(e for e in events if e["type"] == "wrapper.run.start")
    assert run_start["schema_version"] == 1
    assert run_start["release_version"] == EXPECTED_RELEASE_VERSION
    assert run_start["insight_capabilities"] == {
        "agent_output": True,
        "structured_agent_events": True,
        "token_usage": True,
        "context_window": True,
        "skill_consultation": True,
        "cost": True,
    }
    # No "round" exists under Rolling dispatch, so the Iteration pair is never
    # emitted; each contribution opens and closes its own instead (#310), and
    # every one of those carries the whole identity triple and a null `iter`.
    assert [
        e for e in events
        if e["type"] in ("wrapper.iteration.start", "wrapper.iteration.end")
    ] == []
    boundaries = [
        (e["type"], e["issue"], e["lane_id"], e["iter"])
        for e in events
        if e["type"].startswith("wrapper.contribution.")
    ]
    assert boundaries == [
        ("wrapper.contribution.start", 42, "L1", None),
        ("wrapper.contribution.end", 42, "L1", None),
        ("wrapper.contribution.start", 43, "L2", None),
        ("wrapper.contribution.end", 43, "L2", None),
    ]
    for event in events:
        if event["type"].startswith("wrapper.contribution."):
            assert event["contribution_id"]


def test_parallel_single_eligible_issue_starts_lane_immediately(
    tmp_path, monkeypatch
) -> None:
    """One eligible ``parallel-safe`` issue starts a Lane immediately (#219 §1.4).

    #306 retires the Wave's ">= 2 eligible" threshold entirely: with a single
    ``parallel-safe`` issue in the pool, that issue starts working in a Lane
    right away — no waiting for a second parallel-safe issue to appear. The
    pool also carries a plain ``ready-for-agent`` issue (no ``parallel-safe``
    label); it is never Lane work (eligibility is a human assertion, never
    inferred), so the scheduler's serial-latch protocol
    (:meth:`~git_loopy.rolling_scheduler.RollingScheduler.request_serial`)
    grants it exactly one serial Iteration, worked by the shared ``_Loop``.
    Neither issue is stranded.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
        serial_closes=True,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=3,
        max_iterations=0,  # unlimited: drive until the pool drains
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Exactly one Lane: the single eligible parallel-safe issue (42) started
    # working immediately, in its own worktree + branch.
    adds = _lane_worktree_adds(fake_git)
    assert len(adds) == 1, f"expected exactly one Lane worktree, got {adds}"
    (lane_path, lane_branch, _base) = adds[0]
    assert lane_branch.endswith("/issue-42")

    # Exactly one Lane session (pinned) and one serial session (unpinned) —
    # the plain issue (43) was never a Lane, but still worked and drained.
    working_dirs = [c["working_directory"] for c in fake_client.create_calls]
    assert working_dirs.count(None) == 1, "exactly one serial (unpinned) session"
    assert sum(wd is not None for wd in working_dirs) == 1, (
        "exactly one Lane (worktree-pinned) session"
    )

    # The serial session's prompt carries only the plain issue — the Lane
    # issue is worked (and closed) through Integration, not the serial path.
    serial_idx = working_dirs.index(None)
    serial_prompt, _timeout = fake_client.created[serial_idx].send_and_wait_calls[0]
    assert "Issue #43" in serial_prompt

    # Neither issue is stranded: both closed, and the run drained the pool.
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43]
    for n in (42, 43):
        assert fake_gh.issue_view(n).state == "CLOSED", f"#{n} was not closed"
    events = _logged_events(tmp_path)
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "empty_pool"


def test_parallel_lane_refills_without_waiting_for_sibling(
    tmp_path, monkeypatch
) -> None:
    """One Lane's worktree setup overlaps a sibling Lane's still-running session.

    Direct proof of #219 criteria #2/#3 (no barrier): issue 42's simulated agent
    session is gated on an :class:`asyncio.Event` that this test controls, so it
    cannot finish until told to. Both 42 and 43 are eligible ``parallel-safe``
    issues, so both are reserved and their Lane lifecycles are launched as
    concurrent tasks in the same turn. This test awaits, from OUTSIDE the run,
    a second event that only fires once issue 43's worktree setup actually
    runs -- proving the event loop reached Lane 43's setup while Lane 42's
    session was still blocked, unfinished, mid-flight. Only then does the test
    release Lane 42's session, letting the run complete normally. If the old
    Wave barrier were still in effect, Lane 43's setup could never observably
    run before Lane 42's session had already returned, since a barrier makes
    "session in flight" and "sibling's setup running" mutually exclusive.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    hold_42 = asyncio.Event()

    class _GatedClient(_ParallelFakeClient):
        """Blocks issue 42's session on ``hold_42`` until the test releases it."""

        async def create_session(self, **kwargs: Any) -> _ParallelFakeSession:
            session = await super().create_session(**kwargs)
            working_directory = kwargs.get("working_directory")
            if working_directory is not None and str(
                working_directory
            ).endswith("issue-42"):
                real_send_and_wait = session.send_and_wait

                async def gated_send_and_wait(
                    prompt: str, *, timeout: float = 60.0, **extra: Any
                ) -> SessionEvent | None:
                    await hold_42.wait()
                    return await real_send_and_wait(
                        prompt, timeout=timeout, **extra
                    )

                session.send_and_wait = gated_send_and_wait  # type: ignore[method-assign]
            return session

    fake_client = _GatedClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    setup_43_started = asyncio.Event()
    setup_calls: list[Path] = []

    class _SignalingSetup:
        def run(self, worktree: Path) -> SetupResult:
            setup_calls.append(Path(worktree))
            if str(worktree).endswith("issue-43"):
                setup_43_started.set()
            return SetupResult(command="echo prepared")

    monkeypatch.setattr(
        loop_module, "_make_worktree_setup", lambda: _SignalingSetup()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        # Lane 43's setup fires while Lane 42's session is still gated --
        # i.e. genuinely mid-flight, not merely "not yet started".
        await asyncio.wait_for(setup_43_started.wait(), timeout=5)
        assert not run_task.done(), (
            "the run must still be in flight (Lane 42's session gated) when "
            "Lane 43's setup runs -- this is the overlap criteria #2/#3 proves"
        )
        hold_42.set()
        return await asyncio.wait_for(run_task, timeout=5)

    exit_code = asyncio.run(scenario())

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert sorted(str(p) for p in setup_calls) == sorted(
        str(p) for (p, _b, _base) in _lane_worktree_adds(fake_git)
    )
    assert len(fake_client.created) == 2, "both Lane sessions still dispatched"


def test_parallel_lane_cap_never_exceeded_under_bursty_refill(
    tmp_path, monkeypatch
) -> None:
    """Concurrent Lane worktrees never exceed ``config.parallel``, even transiently.

    Direct proof of #219 criterion #5: four ``parallel-safe`` issues are all
    eligible at once (a "bursty" pool -- everything ready simultaneously) but
    ``config.parallel=2`` caps Lane concurrency at 2. Each Lane's simulated
    session is gated on its own :class:`asyncio.Event`, held until this test
    explicitly releases it, so genuine overlap (not just fast sequential
    completion) is forced and observable. This instruments
    :meth:`~tests.fakes.FakeGitClient.add_worktree` /
    :meth:`~tests.fakes.FakeGitClient.remove_worktree` (excluding auto-
    resolution's separate ``integrate/`` worktrees, which are not Lane slots)
    to record the live Lane-worktree count at every add/remove. The test
    drives the run turn by turn: once 2 Lanes are held open, it asserts a 3rd
    reservation has NOT happened yet (the cap -- enforced by
    :attr:`~git_loopy.rolling_scheduler.RollingScheduler.refillable` /
    ``effective_limit`` -- withholds refill while both slots are occupied);
    only after releasing one does the 3rd Lane open, proving refill happens
    promptly as slots free rather than waiting for every open Lane to drain.
    The live-worktree high-water mark never exceeds 2, and all four issues
    eventually get a Lane -- nothing is stranded behind the cap.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(n, labels=["ready-for-agent", "parallel-safe"])
            for n in (42, 43, 44, 45)
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    holds = {n: asyncio.Event() for n in (42, 43, 44, 45)}
    opened: list[int] = []

    class _GatedClient(_ParallelFakeClient):
        """Blocks every Lane's session on its own per-issue hold event."""

        async def create_session(self, **kwargs: Any) -> _ParallelFakeSession:
            session = await super().create_session(**kwargs)
            working_directory = kwargs.get("working_directory")
            if working_directory is not None:
                ref = int(Path(str(working_directory)).name.removeprefix("issue-"))
                opened.append(ref)
                real_send_and_wait = session.send_and_wait

                async def gated_send_and_wait(
                    prompt: str,
                    *,
                    timeout: float = 60.0,
                    _ref: int = ref,
                    **extra: Any,
                ) -> SessionEvent | None:
                    await holds[_ref].wait()
                    return await real_send_and_wait(
                        prompt, timeout=timeout, **extra
                    )

                session.send_and_wait = gated_send_and_wait  # type: ignore[method-assign]
            return session

    fake_client = _GatedClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    real_add_worktree = fake_git.add_worktree
    real_remove_worktree = fake_git.remove_worktree
    live_lane_worktrees: set[Path] = set()
    high_water_mark = 0

    def tracked_add_worktree(
        path: Path, *, branch: str, base: str
    ) -> FakeGitClient:
        child = real_add_worktree(path, branch=branch, base=base)
        if "/integrate/" not in str(path):
            live_lane_worktrees.add(Path(path))
            nonlocal high_water_mark
            high_water_mark = max(high_water_mark, len(live_lane_worktrees))
        return child

    def tracked_remove_worktree(path: Path, *, force: bool = False) -> None:
        real_remove_worktree(path, force=force)
        live_lane_worktrees.discard(Path(path))

    monkeypatch.setattr(fake_git, "add_worktree", tracked_add_worktree)
    monkeypatch.setattr(fake_git, "remove_worktree", tracked_remove_worktree)

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,  # unlimited: drive until the pool drains
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))

        # Exactly 2 Lanes open (the cap), both held mid-session.
        while len(opened) < 2:
            await asyncio.sleep(0)
        assert len(opened) == 2, f"expected exactly 2 Lanes open, got {opened}"
        assert len(live_lane_worktrees) == 2

        # Give the driver every chance to (wrongly) over-reserve a 3rd Lane
        # while both slots are occupied -- it must not.
        for _ in range(50):
            await asyncio.sleep(0)
        assert len(opened) == 2, (
            f"a 3rd Lane opened while the cap (2) was still fully occupied: "
            f"{opened}"
        )

        # Release one held Lane; its slot frees and refill promptly opens a
        # 3rd Lane without waiting for the other held Lane to finish too.
        first_ref = opened[0]
        holds[first_ref].set()
        while len(opened) < 3:
            await asyncio.sleep(0)
        assert len(opened) == 3

        # Release everything else and let the run finish.
        for ref in (43, 44, 45, 42):
            holds[ref].set()
        return await asyncio.wait_for(run_task, timeout=5)

    exit_code = asyncio.run(scenario())

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    # The cap was actually exercised (not trivially satisfied by underuse)...
    assert high_water_mark == 2, (
        f"expected the Lane cap (2) to actually bind at some point, "
        f"saw high water mark {high_water_mark}"
    )
    # ...and never exceeded, even transiently during refill.
    assert high_water_mark <= 2
    # All four eligible issues got a Lane -- refill drained the whole pool,
    # nothing stranded behind the cap.
    adds = _lane_worktree_adds(fake_git)
    lane_issues = sorted(int(b.split("/issue-")[1]) for (_p, b, _base) in adds)
    assert lane_issues == [42, 43, 44, 45]
    events = _logged_events(tmp_path)
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "empty_pool"


def test_parallel_contribution_survives_lane_reuse(
    tmp_path, monkeypatch
) -> None:
    """A contribution's accounting survives its Lane slot being reused (#219 §criterion #7).

    Three ``parallel-safe`` issues, ``config.parallel=2``: issues 42 and 43
    take the two Lane slots first; issue 44 can only start once one of them
    frees up. Issue 42's Lane worktree is torn down (and its slot freed for
    reuse) as soon as its own commit is checkpointed -- well before its
    Contribution is finalized by Integration (merge + gate + close) -- so
    issue 44's Lane can legitimately reuse that freed slot while issue 42's
    Contribution is still being carried through Integration. This asserts
    that despite the Lane slot moving on to issue 44, issue 42's own
    Contribution still lands and closes correctly: all three issues land and
    close, keyed by their own ``contribution_id`` / issue ref, not by which
    physical Lane slot they happened to run in.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(n, labels=["ready-for-agent", "parallel-safe"])
            for n in (42, 43, 44)
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,  # unlimited: drive until the pool drains
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Only two Lane worktrees ever live concurrently (the cap), but all three
    # issues eventually got their own Lane -- 44's reused a slot freed by
    # whichever of 42/43 checkpointed first.
    adds = _lane_worktree_adds(fake_git)
    assert len(adds) == 3
    laned = sorted(int(b.split("/issue-")[1]) for (_p, b, _base) in adds)
    assert laned == [42, 43, 44]

    # All three Contributions landed and closed -- the one whose Lane slot
    # was reused by #44 is still tracked correctly through to Integration.
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43, 44]
    for n in (42, 43, 44):
        assert fake_gh.issue_view(n).state == "CLOSED", f"#{n} was not closed"
    events = _logged_events(tmp_path)
    auto_closes = sorted(
        e["issue"] for e in events if e["type"] == "wrapper.auto_close"
    )
    assert auto_closes == [42, 43, 44]
    # No stranding, no strikes -- every Contribution made progress regardless
    # of which physical Lane slot it ran in.
    assert [e for e in events if e["type"] == "wrapper.strike"] == []
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "empty_pool"


class _StaleOnPickupGitHubClient(FakeGitHubClient):
    """A candidate a human closed between the membership read and pickup.

    Models the one divergence Rolling dispatch's two-read seam is built for
    (#219 §2.10): the cheap ``issue_list_membership`` snapshot that put a
    candidate in the **Pool** cache is never authority to start a **Lane
    contribution**, because the issue may have moved on since. ``issue_view``
    — the authoritative read :meth:`~git_loopy.sources.GitHubIssueSource.pickup`
    pays — reports the ``stale_refs`` ``CLOSED``, and once observed they stop
    appearing in later membership reads, exactly as ``gh`` behaves once the
    issue is really closed. Both halves matter: a fake that kept listing a
    permanently-closed issue would model a divergence real ``gh`` never
    produces, and would leave the Run with a candidate it can neither dispatch
    nor retire.
    """

    def __init__(self, *, stale_refs: Sequence[int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stale_refs = set(stale_refs)
        self._observed_stale: set[int] = set()

    def issue_view(self, number: int) -> gh_module.Issue:
        issue = super().issue_view(number)
        if number not in self._stale_refs:
            return issue
        self._observed_stale.add(number)
        return replace(issue, state="CLOSED")

    def issue_list(
        self, label: str, state: str = "open"
    ) -> list[gh_module.Issue]:
        return [
            issue
            for issue in super().issue_list(label, state)
            if issue.number not in self._observed_stale
        ]

    def issue_list_membership(
        self, label: str, state: str = "open"
    ) -> gh_module.IssueListPage:
        page = super().issue_list_membership(label, state)
        return replace(
            page,
            issues=tuple(
                issue
                for issue in page.issues
                if issue.number not in self._observed_stale
            ),
        )


def test_parallel_stale_candidate_never_consumes_a_lane(
    tmp_path, monkeypatch
) -> None:
    """A candidate that fails pickup costs no Lane and no iteration unit (#219 §2.10).

    Candidate discovery reads the shallow **Pool** membership cache and pays
    the authoritative per-issue read only at pickup. Issues 41 and 42 are in
    that cache carrying ``ready-for-agent`` + ``parallel-safe``, but a human
    closed them since — so pickup rejects both as *stale*. #306's criterion is
    that such a candidate is dropped "without consuming a Lane": with the whole
    ``max_iterations`` budget set to exactly one unit, if either stale candidate
    had consumed it the genuinely eligible issue 43 would never have run at all.
    It does — one worktree, one Lane session, one closure — and the two stale
    candidates leave no worktree, no branch, no session, and no closure behind.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = _StaleOnPickupGitHubClient(
        stale_refs=(41, 42),
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(n, labels=["ready-for-agent", "parallel-safe"])
            for n in (41, 42, 43)
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=3,
        # Exactly one unit for the whole Run: a stale candidate that consumed
        # one would strand issue 43 entirely.
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    asyncio.run(loop_module.run(cfg))

    # Both stale candidates were re-read authoritatively before any Lane was
    # committed to them -- membership alone never dispatched them.
    assert 41 in fake_gh.issue_view_calls
    assert 42 in fake_gh.issue_view_calls

    # ...and neither cost a Lane: exactly one worktree + branch exists, and it
    # belongs to the one candidate that actually validated.
    adds = _lane_worktree_adds(fake_git)
    assert len(adds) == 1, f"a stale candidate consumed a Lane: {adds}"
    (_path, lane_branch, _base) = adds[0]
    assert lane_branch.endswith("/issue-43")

    # ...nor an iteration unit: the single unit reached issue 43's session.
    working_dirs = [c["working_directory"] for c in fake_client.create_calls]
    assert len(working_dirs) == 1, f"expected one session, got {working_dirs}"
    assert str(working_dirs[0]).endswith("issue-43")

    # The runner closed only the issue it actually worked.
    assert [n for (n, _c) in fake_gh.issue_close_calls] == [43]


def test_parallel_lane_checkpoint_commits_in_its_own_worktree(
    tmp_path, monkeypatch
) -> None:
    """Uncommitted Lane work is Checkpointed on the Lane's own branch (ADR-0004).

    #306 preserves the per-Lane **Checkpoint**: a Lane whose agent leaves the
    tree dirty gets its work captured before the worktree is torn down, in
    *that Lane's* worktree and attributed to *that Lane's* issue -- never on the
    shared main worktree, where it would mix two Lanes' work into one commit.
    The Checkpoint commit's SHA carries the Lane worktree's own ``wt`` prefix
    (:class:`~tests.fakes.FakeGitClient` gives each child worktree a distinct
    one), which is the observable proof of where it was authored.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    class _DirtyingClient(_ParallelFakeClient):
        """Leaves the Lane's worktree dirty after its agent session."""

        async def create_session(self, **kwargs: Any) -> _ParallelFakeSession:
            session = await super().create_session(**kwargs)
            working_directory = kwargs.get("working_directory")
            if working_directory is None:
                return session
            real_send_and_wait = session.send_and_wait

            async def dirtying_send_and_wait(
                prompt: str, *, timeout: float = 60.0, **extra: Any
            ) -> SessionEvent | None:
                result = await real_send_and_wait(prompt, timeout=timeout, **extra)
                lane_git = fake_git.worktree_client(Path(working_directory))
                assert lane_git is not None, "Lane worktree must still be live"
                lane_git.dirty = True
                return result

            session.send_and_wait = dirtying_send_and_wait  # type: ignore[method-assign]
            return session

    fake_client = _DirtyingClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    asyncio.run(loop_module.run(cfg))

    checkpoints = [
        e
        for e in _logged_events(tmp_path)
        if e["type"] == "wrapper.checkpoint.recorded"
    ]
    assert len(checkpoints) == 1, f"expected one Lane Checkpoint, got {checkpoints}"
    (checkpoint,) = checkpoints
    # Attributed to the Lane's issue, on both the Active-issue and the
    # Lane-attribution channels.
    assert checkpoint["issue"] == 42
    assert checkpoint["lane_issue"] == 42
    # Authored in the Lane's OWN worktree: only a child worktree client mints
    # ``wt``-prefixed SHAs, and the main worktree wrote no commit at all.
    assert checkpoint["sha"].startswith("wt")
    assert fake_git.commit_messages == []


def test_parallel_integration_lands_and_closes_both_lanes(
    tmp_path, monkeypatch
) -> None:
    """Integration merges + closes every green Lane, each as it finishes.

    Rolling dispatch integrates each contribution the instant its own Lane
    session finishes (#219 §4) rather than batching a Wave's Lanes together
    and sorting them before Integration -- so, unlike the retired Wave
    barrier, there is no scheduler-wide "ascending issue order" guarantee to
    assert on; completion order follows each contribution's own finish time,
    not issue number. With an all-green gate both Lanes still land on base and
    both issues close, and both integrated branches are deleted -- assertions
    are on the (order-independent) set of observable effects, not call order.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    # All-green gate: every Lane's feedback loops pass, so every Lane lands.
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Both Lanes dispatched and both issues closed, order-independent.
    assert len(fake_client.created) == 2
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43]
    # Serial closure semantics: both issues actually flipped CLOSED in the store.
    assert fake_gh.issue_view(42).state == "CLOSED"
    assert fake_gh.issue_view(43).state == "CLOSED"

    # One wrapper.auto_close event per landed Lane.
    events = _logged_events(tmp_path)
    auto_closes = [e for e in events if e["type"] == "wrapper.auto_close"]
    assert sorted(e["issue"] for e in auto_closes) == [42, 43]

    # A successful Integration counts as Strike progress: both contributions
    # landed, so the shared Strike machine saw progress on each and recorded
    # no strike for either.
    assert [e for e in events if e["type"] == "wrapper.strike"] == []

    # Both green Lanes landed on base (base advanced past the prior commit) and
    # both integrated branches were deleted.
    assert fake_git.head_sha() != "0000000000000000000000000000000000000001"
    deleted = sorted(_lane_branch_deletes(fake_git))
    assert len(deleted) == 2
    assert deleted[0].endswith("/issue-42")
    assert deleted[1].endswith("/issue-43")

    # No worktrees left live once Integration has processed every contribution.
    assert fake_git.active_worktrees == []


class _BaseWatchingGateRunner(FakeGateRunner):
    """A gate that records the **base** head SHA at the moment it runs.

    #219 §4.7 / ADR-0020 require each candidate to be "prepared and fully gated
    in private Integration state based on the latest published green base", and
    the base to advance "only after the candidate passes the full relevant
    feedback loop". That is an assertion about *when* base moves relative to the
    gate, which no after-the-fact inspection of base can make — so the gate
    itself samples base as it runs.
    """

    def __init__(self, base: FakeGitClient, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base = base
        self.base_heads: list[str] = []

    def run(self, worktree: Path):  # type: ignore[no-untyped-def]
        self.base_heads.append(self._base.head_sha())
        return super().run(worktree)


def test_parallel_integration_gates_privately_before_publishing(
    tmp_path, monkeypatch
) -> None:
    """Base never carries an ungated merge (#219 §4.7-4.8, ADR-0020).

    ADR-0020 supersedes ADR-0009 precisely for "publishing an unverified merge
    before reverting it": a Lane branch is merged and gated in a **private**
    Integration worktree cut from the latest published green base, and base
    advances only once that gate is green. The load-bearing evidence is the base
    head *sampled by the gate itself* — it must still be the pre-run base — plus
    the worktree the gate was handed, which is the private Integration worktree
    and never the shared repository root that concurrent Lanes branch from.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    gate = _BaseWatchingGateRunner(fake_git)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: gate)

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(
                model="claude-opus-4.8-max",
                issue_source="github",
                parallel=2,
                max_iterations=1,
                max_nmt_strikes=3,
                verbosity=0,
                render_reasoning=False,
            )
        )
    )

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # The gate ran once, in the private Integration worktree -- never in the
    # shared repository root a concurrent Lane would branch from.
    int_path = loop_module._integration_worktree_path(
        Path(tmp_path), _run_id(tmp_path), 42
    )
    assert gate.calls == [int_path]
    # ...and base had not moved when it ran.
    assert gate.base_heads == ["0000000000000000000000000000000000000001"]

    # Only after green does base advance and the issue close.
    assert fake_git.head_sha() != "0000000000000000000000000000000000000001"
    assert [n for (n, _c) in fake_gh.issue_close_calls] == [42]
    # Base received exactly one merge: the verified private Integration result,
    # never the raw Lane branch.
    assert [b for b in fake_git.merge_calls if "/integrate/" not in b] == []


def test_parallel_rollup_distinguishes_published_unclosed_and_noop_contributions(
    tmp_path, monkeypatch
) -> None:
    """Integration tells "published, close failed" apart from "no progress".

    Two Lanes finalize with different dispositions: #42's merge genuinely
    advances base but its ``gh issue close`` call errors (published, stays
    open); #43's merge is a no-op (its branch is already fully merged), so
    Integration never even attempts a close (no base advancement, no
    progress). Under Rolling dispatch there is no single-slot
    ``wrapper.iteration.end`` rollup for Lane contributions (see the module
    docstring's documented gap), so this asserts the equivalent *observable*
    distinction directly: exactly one close is attempted (for #42, and it
    fails so #42 stays open), #43 is never attempted and also stays open, and
    Strike sees exactly one no-progress tick (#42's advance resets Strike,
    #43's no-op ticks it) — both consuming their ``max_iterations`` unit at
    pickup, with no extra serial-fallback round sneaking past the cap.
    """
    fake_git = _wire_repo(tmp_path)
    merge = fake_git.merge

    def merge_with_noop(branch: str) -> None:
        if branch.endswith("/issue-43"):
            return
        merge(branch)

    monkeypatch.setattr(fake_git, "merge", merge_with_noop)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
        issue_close_errors={
            42: gh_module.GhError(
                ["gh", "issue", "close", "42"],
                1,
                "closure failed",
            )
        },
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(
                model="claude-opus-4.8-max",
                issue_source="github",
                parallel=2,
                max_iterations=2,
                max_nmt_strikes=3,
                verbosity=0,
                render_reasoning=False,
            )
        )
    )

    assert exit_code == 0
    # #42 published (base advanced) but its close call failed -> stays open,
    # never retried. #43's merge was a no-op -> never even attempted.
    assert [n for (n, _msg) in fake_gh.issue_close_calls] == [42]
    assert fake_gh.issue_view(42).state == "OPEN"
    assert fake_gh.issue_view(43).state == "OPEN"

    events = _logged_events(tmp_path)
    strikes = [e for e in events if e["type"] == "wrapper.strike"]
    assert [s["strikes"] for s in strikes] == [1]
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "iteration_cap"
    # Exactly the two Lane sessions' units are spent -- no extra
    # serial-fallback round sneaks past the `max_iterations=2` cap even
    # though #43's disposition would otherwise latch one.
    assert run_end["iterations_run"] == 2


def test_parallel_integration_red_gate_keeps_branch_and_records_strike(
    tmp_path, monkeypatch
) -> None:
    """Red-throughout Integration: base is never touched, falls back to serial.

    Evolves the #62/#63 contract deliberately (#219, ADR-0020, criterion #5).
    With every Lane's gate red *and* every auto-resolution attempt red, each
    Lane: merges and gates **privately**, stays red, runs the bounded K=3
    auto-resolution agent in that same private stage (all red), then falls back
    to a serial Iteration with **exactly one** breadcrumb comment — its Lane
    branch **kept** (only the throwaway Integration branch is deleted). Because
    nothing unverified is ever published, base is not merged and there is no
    revert to make — which is precisely what ADR-0020 supersedes ADR-0009's
    publish-then-revert for. Nothing lands, so each no-progress contribution's
    ``finalize`` records its own warn strike (#219 §7.9: the scheduler records
    the reaction per-contribution, not once per round) — two Lanes, two strikes.
    Both Lanes already spent their ``max_iterations`` unit at pickup, so — even
    though each fallback latches a serial request — the cap is already exhausted
    and the run ends via ``iteration_cap`` without a further serial Iteration
    ever running. Assertions are on observable effects only.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    # All-red gate: every Lane's feedback loops fail on the initial landing AND
    # on every auto-resolution attempt, so no Lane ever lands.
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner(default=False)
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    # Two warn strikes (2 < 3) do not abort the run; the iteration cap ends it.
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Nothing landed: no issue closed and both remain OPEN for a later serial
    # round (never run here since both Lanes already spent the iteration cap).
    assert fake_gh.issue_close_calls == []
    assert fake_gh.issue_view(42).state == "OPEN"
    assert fake_gh.issue_view(43).state == "OPEN"

    # Base was never made to carry an unverified merge, so it never needed
    # unwinding: no merge reached base at all, and its head is still the
    # pre-run commit.
    assert fake_git.merge_calls == []
    assert fake_git.head_sha() == "0000000000000000000000000000000000000001"

    # Both Lane branches are KEPT as breadcrumbs; only the two throwaway
    # integration branches are deleted (a fallback deletes no Lane branch).
    assert len(fake_git.branch_deletes) == 2
    assert all("/integrate/" in b for b in fake_git.branch_deletes)

    # Exactly one breadcrumb comment per terminal fallback (one per Lane), and
    # the comment resolves nothing (both issues stay OPEN, asserted above).
    assert sorted(n for n, _ in fake_gh.issue_comment_calls) == [42, 43]

    # Each no-progress contribution finalizes its own warn strike -- two
    # Lanes, two ticks -- and Integration closed nothing.
    events = _logged_events(tmp_path)
    strikes = [e for e in events if e["type"] == "wrapper.strike"]
    assert len(strikes) == 2
    assert [s["outcome"] for s in strikes] == ["warn", "warn"]
    assert [s["strikes"] for s in strikes] == [1, 2]
    assert [e for e in events if e["type"] == "wrapper.auto_close"] == []
    # No serial Iteration ever ran: both Lane sessions already spent the
    # `max_iterations=2` budget, so the run ends via `iteration_cap` before
    # either fallback's latched serial request could be granted. The only rows
    # cut are the two terminal, unpublished Lane contributions' own (#310) —
    # honestly no-progress, because a Lane's commits are not progress until
    # Integration publishes them.
    assert [e for e in events if e["type"] == "wrapper.iteration.end"] == []
    ends = [e for e in events if e["type"] == "wrapper.contribution.end"]
    assert [e["issue"] for e in ends] == [42, 43]
    assert [e["published"] for e in ends] == [False, False]
    assert [e["summary"]["closure_outcome"] for e in ends] == [
        "no_progress", "no_progress"
    ]
    assert [e["summary"]["strike_reaction"] for e in ends] == ["+1", "+1"]
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "iteration_cap"
    assert run_end["iterations_run"] == 2


def test_parallel_integration_auto_resolves_red_lane_then_lands(
    tmp_path, monkeypatch
) -> None:
    """A red Lane is auto-resolved on a later attempt and lands (#63, ADR-0020).

    Issue 42's Lane merges cleanly into its **private** Integration stage but
    gates red there AND on the first auto-resolution attempt, then passes on the
    second; issue 43 is green throughout. The scripted gate is a global
    call-ordered queue ``[42-postmerge=red, 42-att1=red, 42-att2=green]`` with
    the default (green) covering 43. Asserts (observable effects only): base is
    never reverted -- it never carried the red result in the first place -- the
    K-bounded auto-resolution agent runs exactly twice for 42 in that same
    private stage, both issues end CLOSED with one ``auto_close`` each, no
    breadcrumb is posted, and -- because two Integrations landed -- no strike is
    recorded.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    # 42 is red on its initial landing and its first auto-resolution attempt,
    # green on the second; 43 (default) is green.
    monkeypatch.setattr(
        loop_module,
        "_make_gate_runner",
        lambda: FakeGateRunner(outcomes=[False, False, True], default=True),
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Base stayed green without any unwinding: 42's red result lived and died in
    # its private stage, so base only ever received the two verified Integration
    # branches.
    assert all("/integrate/" in b for b in fake_git.merge_calls)

    # The K-bounded auto-resolution agent ran exactly twice for 42, each session
    # pinned to 42's dedicated integration worktree (never 43's).
    resolution_dirs = [
        c["working_directory"]
        for c in fake_client.create_calls
        if c["working_directory"] and "/integrate/" in c["working_directory"]
    ]
    assert len(resolution_dirs) == 2
    assert all(wd.endswith("/issue-42") for wd in resolution_dirs)

    # Both issues landed and closed — 42 via auto-resolution, 43 via the happy
    # path — with exactly one auto_close each and no breadcrumb (no fallback).
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43]
    assert fake_gh.issue_view(42).state == "CLOSED"
    assert fake_gh.issue_view(43).state == "CLOSED"
    assert fake_gh.issue_comment_calls == []

    events = _logged_events(tmp_path)
    assert [
        e["issue"] for e in events if e["type"] == "wrapper.auto_close"
    ] == [42, 43]
    # Two Integrations landed = progress, so the round records no strike.
    assert [e for e in events if e["type"] == "wrapper.strike"] == []


def test_parallel_auto_resolution_does_not_consume_the_lane_cap(
    tmp_path, monkeypatch
) -> None:
    """Recovery never occupies a **Lane** slot (#219 §4.11, criterion #8).

    A contribution's Lane frees the instant it is admitted to **Integration**
    (§3.9), and its Integration cascade — up to K=3 bounded auto-resolution
    *agent sessions* — then runs without a Lane. So a freed slot must refill
    while recovery is still in flight; if it could not, recovery would silently
    consume the **Lane cap** it is defined not to, and a single red
    contribution would idle a Lane for three whole agent sessions.

    The scenario makes the freed slot the *only* thing that can start #44. Lane
    cap is 2, so #42 and #44 cannot both be dispatched up front; #43 holds the
    other Lane with a session this test gates shut, so no other Lane lifecycle
    can end and wake the driver. #42's private stage gates red once, parking it
    in auto-resolution, which this test also gates shut. From outside the run,
    the test then waits for #44's *worktree setup* — which only runs after the
    scheduler reserved a Lane for it — while both the resolution session and
    #43's session are provably still mid-flight.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(44, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    hold_43 = asyncio.Event()
    hold_resolution = asyncio.Event()
    resolution_started = asyncio.Event()

    class _GatedClient(_ParallelFakeClient):
        async def create_session(self, **kwargs: Any) -> _ParallelFakeSession:
            session = await super().create_session(**kwargs)
            working_directory = str(kwargs.get("working_directory") or "")
            if working_directory.endswith("issue-43"):
                gate_on, announce = hold_43, None
            elif "/integrate/" in working_directory:
                gate_on, announce = hold_resolution, resolution_started
            else:
                return session
            real_send_and_wait = session.send_and_wait

            async def gated_send_and_wait(
                prompt: str, *, timeout: float = 60.0, **extra: Any
            ) -> SessionEvent | None:
                if announce is not None:
                    announce.set()
                await gate_on.wait()
                return await real_send_and_wait(prompt, timeout=timeout, **extra)

            session.send_and_wait = gated_send_and_wait  # type: ignore[method-assign]
            return session

    fake_client = _GatedClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module,
        "_make_gate_runner",
        # #42's private stage gates red once, which is what puts it into bounded
        # auto-resolution; every other stage (including #42's recovery attempt)
        # is green.
        lambda: FakeGateRunner(by_issue={42: [False]}),
    )

    setup_44_started = asyncio.Event()

    class _SignalingSetup:
        def run(self, worktree: Path) -> SetupResult:
            if str(worktree).endswith("issue-44"):
                setup_44_started.set()
            return SetupResult(command="echo prepared")

    monkeypatch.setattr(
        loop_module, "_make_worktree_setup", lambda: _SignalingSetup()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,  # unbounded: drive until the pool drains
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        await asyncio.wait_for(resolution_started.wait(), timeout=5)
        # The load-bearing wait: #44 can only be here because #42's admission
        # freed its Lane, since #43's Lane is held and #42's own lifecycle task
        # has not returned.
        await asyncio.wait_for(setup_44_started.wait(), timeout=5)
        assert not run_task.done(), (
            "the run must still be in flight -- #42's recovery session and "
            "#43's Lane session are both gated shut"
        )
        hold_resolution.set()
        hold_43.set()
        return await asyncio.wait_for(run_task, timeout=10)

    exit_code = asyncio.run(scenario())

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # All three took a Lane, and the cap was still a cap: only two Lane
    # worktrees ever existed at once (see the bursty-refill test), which is
    # what makes "#44 started in #42's freed slot" the only reading.
    assert sorted(
        int(Path(p).name.removeprefix("issue-"))
        for (p, _b, _base) in _lane_worktree_adds(fake_git)
    ) == [42, 43, 44]
    assert fake_gh.issue_view(42).state == "CLOSED"
    assert fake_gh.issue_view(43).state == "CLOSED"
    assert fake_gh.issue_view(44).state == "CLOSED"


def test_parallel_integration_never_overlaps_another_contribution(
    tmp_path, monkeypatch
) -> None:
    """At most one contribution is being integrated at a time (criterion #4).

    Serialization is load-bearing well beyond "no two merges at once": a
    private **Integration stage** is cut from base and published back to it on
    the strength of base *not having moved in between* (:meth:`_publish_stage`),
    so a second concurrent Integration would publish a result verified against a
    base that no longer exists.

    Nothing pinned it before, because the green Integration path holds
    ``_integration_lock`` across only synchronous work and so cannot interleave
    even without the lock. Bounded auto-resolution is what makes the window
    real: it awaits *agent sessions* inside the lock. So #42's private stage
    gates red once and its recovery session is gated shut by this test, and #43
    is released to finish its whole Lane arc — worktree torn down, contribution
    admitted — while #42 still holds Integration. The timeline then reads the
    ordering directly: #43 was ready to integrate before #42's stage was reaped,
    yet #43's stage was not cut until after.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    timeline: list[tuple[str, str]] = []
    lane_43_finished = asyncio.Event()
    real_add_worktree = fake_git.add_worktree
    real_remove_worktree = fake_git.remove_worktree

    def _kind(path: Path) -> str:
        return "stage" if "integrate" in Path(path).parts else "lane"

    def tracked_add_worktree(path: Path, *, branch: str, base: str):
        timeline.append((f"{_kind(path)}-add", Path(path).name))
        return real_add_worktree(path, branch=branch, base=base)

    def tracked_remove_worktree(path: Path, *, force: bool = False) -> None:
        real_remove_worktree(path, force=force)
        timeline.append((f"{_kind(path)}-remove", Path(path).name))
        if _kind(path) == "lane" and Path(path).name == "issue-43":
            lane_43_finished.set()

    monkeypatch.setattr(fake_git, "add_worktree", tracked_add_worktree)
    monkeypatch.setattr(fake_git, "remove_worktree", tracked_remove_worktree)

    hold_43 = asyncio.Event()
    hold_resolution = asyncio.Event()
    resolution_started = asyncio.Event()

    class _GatedClient(_ParallelFakeClient):
        async def create_session(self, **kwargs: Any) -> _ParallelFakeSession:
            session = await super().create_session(**kwargs)
            working_directory = str(kwargs.get("working_directory") or "")
            if working_directory.endswith("issue-43"):
                gate_on, announce = hold_43, None
            elif "/integrate/" in working_directory:
                gate_on, announce = hold_resolution, resolution_started
            else:
                return session
            real_send_and_wait = session.send_and_wait

            async def gated_send_and_wait(
                prompt: str, *, timeout: float = 60.0, **extra: Any
            ) -> SessionEvent | None:
                if announce is not None:
                    announce.set()
                await gate_on.wait()
                return await real_send_and_wait(prompt, timeout=timeout, **extra)

            session.send_and_wait = gated_send_and_wait  # type: ignore[method-assign]
            return session

    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _GatedClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(
        loop_module,
        "_make_gate_runner",
        lambda: FakeGateRunner(by_issue={42: [False]}),
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        await asyncio.wait_for(resolution_started.wait(), timeout=5)
        hold_43.set()
        # #43's whole Lane arc completes while #42 still owns Integration, so
        # its own Integration is genuinely contending — not merely late.
        await asyncio.wait_for(lane_43_finished.wait(), timeout=5)
        hold_resolution.set()
        return await asyncio.wait_for(run_task, timeout=10)

    exit_code = asyncio.run(scenario())
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    stages = [entry for entry in timeline if entry[0].startswith("stage-")]
    assert stages == [
        ("stage-add", "issue-42"),
        ("stage-remove", "issue-42"),
        ("stage-add", "issue-43"),
        ("stage-remove", "issue-43"),
    ], f"Integration stages overlapped: {timeline}"
    # Non-vacuity: #43 was finished and waiting well before #42 let go.
    assert timeline.index(("lane-remove", "issue-43")) < timeline.index(
        ("stage-remove", "issue-42")
    ), f"#43 was not contending for Integration: {timeline}"

    assert fake_gh.issue_view(42).state == "CLOSED"
    assert fake_gh.issue_view(43).state == "CLOSED"


def test_parallel_integration_aborts_conflicting_merge_then_auto_resolves(
    tmp_path, monkeypatch
) -> None:
    """A conflicting Lane merge is aborted *privately*, then auto-resolved (#63).

    Issue 42's Lane branch is scripted to *conflict* on merge; issue 43 merges
    cleanly. Under ADR-0020 the conflicting merge is attempted in 42's private
    Integration stage, never on base — so the ``git merge --abort`` that unwinds
    it fires **in that stage**, base is never left mid-merge, and there is
    nothing to ``git revert``. Recovery then runs the auto-resolution agent in
    the same stage, which passes on its first attempt here (all-green gate) and
    publishes 42. Asserts where the abort happened, that base recorded none of
    it, that both issues closed, and that the Run made progress (no strike).
    """
    fake_git = _wire_repo(tmp_path, merge_conflicts=[42])
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    # All-green gate: the conflict is a *merge* failure, not a gate failure, so
    # 42's first auto-resolution attempt (and 43's landing) pass.
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner(default=True)
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # The conflict was aborted inside 42's private Integration stage -- base
    # never attempted the Lane merge, so it recorded no abort of its own.
    int_path = loop_module._integration_worktree_path(
        tmp_path, _run_id(tmp_path), 42
    )
    assert fake_git.repo_merge_aborts == [int_path]
    assert fake_git.merge_aborts == 0

    # 42 was recovered by exactly one auto-resolution attempt in its dedicated
    # integration worktree; 43 landed via the happy path.
    resolution_dirs = [
        c["working_directory"]
        for c in fake_client.create_calls
        if c["working_directory"] and "/integrate/" in c["working_directory"]
    ]
    assert resolution_dirs == [
        str(
            loop_module._integration_worktree_path(
                tmp_path, _run_id(tmp_path), 42
            )
        )
    ]

    # Both issues closed; no breadcrumb (no fallback); the round made progress.
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43]
    assert fake_gh.issue_view(42).state == "CLOSED"
    assert fake_gh.issue_view(43).state == "CLOSED"
    assert fake_gh.issue_comment_calls == []
    events = _logged_events(tmp_path)
    assert [e for e in events if e["type"] == "wrapper.strike"] == []


def test_parallel_integration_falls_back_to_serial_after_k_attempts(
    tmp_path, monkeypatch
) -> None:
    """K=3 terminal failure falls back to a serial Iteration with one breadcrumb (#63).

    Issue 42's gate is red on its initial landing AND on all K=3 auto-resolution
    attempts (four reds), so it terminally fails Integration; issue 43 is green.
    With ``max_iterations=0`` and ``serial_closes`` the run then drains: the Lane
    lands 43, 42 falls back to a serial Iteration, and a later serial round works
    42 to closure. Asserts base was never published red (nothing to revert), the
    auto-resolution agent ran exactly K=3 times, exactly ONE breadcrumb comment
    was posted on 42, 42's Lane branch was KEPT (only its throwaway private
    Integration branch deleted), and the run drained the pool (both issues
    CLOSED, ``empty_pool``).
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
        serial_closes=True,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    original_session = loop_module.IterationSession
    exposures: list[object] = []

    class RecordingIterationSession(original_session):
        def __init__(self, *args: object, **kwargs: object) -> None:
            exposures.append(kwargs["skill_exposure"])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(loop_module, "IterationSession", RecordingIterationSession)
    # 42 red on its landing + all K=3 attempts (four reds); 43 (default) green.
    monkeypatch.setattr(
        loop_module,
        "_make_gate_runner",
        lambda: FakeGateRunner(outcomes=[False, False, False, False], default=True),
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,  # unlimited: drive until the pool drains
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected a clean drain (exit 0), got {exit_code}"

    # Base stayed green with nothing to unwind: 42's red result never left its
    # private Integration stage, so base never merged the Lane branch.
    assert git_module.lane_branch_name(_run_id(tmp_path), 42) not in (
        fake_git.merge_calls
    )

    # The auto-resolution agent ran exactly K=3 times for 42 (the bound holds),
    # each session pinned to 42's dedicated integration worktree.
    resolution_dirs = [
        c["working_directory"]
        for c in fake_client.create_calls
        if c["working_directory"] and "/integrate/" in c["working_directory"]
    ]
    assert len(resolution_dirs) == loop_module._AUTO_RESOLUTION_MAX_ATTEMPTS
    assert all(wd.endswith("/issue-42") for wd in resolution_dirs)
    assert len(exposures) == len(fake_client.create_calls)
    assert all(exposure is exposures[0] for exposure in exposures)

    # Exactly ONE automated breadcrumb was posted on 42 for the fallback.
    assert [n for (n, _b) in fake_gh.issue_comment_calls] == [42]

    # 42's Lane branch is KEPT as a breadcrumb; only its throwaway integration
    # branch was deleted (the fallback deletes no Lane branch).
    lane_42 = git_module.lane_branch_name(_run_id(tmp_path), 42)
    assert lane_42 not in fake_git.branch_deletes
    assert (
        git_module.integration_branch_name(_run_id(tmp_path), 42)
        in fake_git.branch_deletes
    )

    # The run drained the pool: 42 closed via the serial fallback round, 43 via
    # Integration; the run ended on empty_pool, not the iteration cap.
    assert fake_gh.issue_view(42).state == "CLOSED"
    assert fake_gh.issue_view(43).state == "CLOSED"
    events = _logged_events(tmp_path)
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "empty_pool"


def test_parallel_run_drains_lanes_then_serial_in_one_run(
    tmp_path, monkeypatch
) -> None:
    """Drain-everything (#67, ADR-0008): Lanes for parallel-safe, serial for the rest.

    A Parallel run must never strand eligible work: it interleaves Rolling-
    dispatched **Lanes** for the ``parallel-safe`` issues with normal serial
    **Iterations** for every other ``ready-for-agent`` issue, in one run, until
    the pool is drained. The pool mixes two ``parallel-safe`` issues (42, 43)
    with one plain ``ready-for-agent`` issue (44). Driven through
    ``run(config)`` with ``max_iterations=0`` (run until the pool empties) and
    an all-green gate, this asserts (observable effects only):

    * **Only the parallel-safe issues become Lanes** — 42 and 43 (the
      human-asserted ``parallel-safe`` issues) each get their own worktree +
      branch; 44 never does.
    * **The plain issue is worked serially** — exactly one unpinned session,
      whose prompt carries the plain issue 44 and no longer carries the
      already-closed 42/43 (eligibility is a human assertion, so 44 is worked
      serially, not dropped).
    * **No stranding** — all three issues close (42/43 via Integration, 44 via the
      serial closure path) and the run terminates by draining the pool
      (``empty_pool``), not by hitting the iteration cap or a strike abort.
    * **Correct per-contribution Strike accounting** — both landed Lane
      contributions and the serial Iteration each made progress, so nothing
      records a strike.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(44, labels=["ready-for-agent"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    # serial_closes: the serial-fallback session "picks one" issue and closes it,
    # so a plain ``ready-for-agent`` issue actually drains and the run can reach
    # an empty pool rather than looping until a strike abort.
    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
        serial_closes=True,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    # All-green gate: every landed Lane's feedback loops pass, so both parallel-
    # safe Lanes land at Integration.
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,  # unlimited: drive until the pool drains
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0, f"expected a clean drain (exit 0), got {exit_code}"

    # --- Only the two parallel-safe issues became Lanes, each with its own
    #     worktree + branch. The plain issue 44 never did — eligibility is a
    #     human assertion, never inferred.
    adds = _lane_worktree_adds(fake_git)
    assert len(adds) == 2, f"expected exactly two Lane worktrees (42,43), got {adds}"
    laned = sorted(int(b.split("/issue-")[1]) for (_p, b, _base) in adds)
    assert laned == [42, 43], "only the parallel-safe issues become Lanes"

    # --- A later turn was serial: exactly one unpinned session; the two Lane
    #     sessions were worktree-pinned. Three sessions total across the run.
    working_dirs = [c["working_directory"] for c in fake_client.create_calls]
    assert working_dirs.count(None) == 1, "exactly one serial (unpinned) session"
    assert sum(wd is not None for wd in working_dirs) == 2, (
        "both Lane sessions were worktree-pinned"
    )

    # --- The serial turn worked the plain issue AFTER the Lanes closed 42/43:
    #     its prompt carries #44 and no longer carries the closed parallel-safe
    #     issues, so opting into Parallel mode strands nothing.
    serial_idx = working_dirs.index(None)
    serial_session = fake_client.created[serial_idx]
    serial_prompt, _timeout = serial_session.send_and_wait_calls[0]
    assert "=== Issue #44:" in serial_prompt
    assert "=== Issue #42:" not in serial_prompt
    assert "=== Issue #43:" not in serial_prompt

    # --- No stranding: all three issues closed — 42/43 via Integration, 44 via
    #     the serial closure path — and each actually flipped CLOSED in the store.
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [42, 43, 44]
    for n in (42, 43, 44):
        assert fake_gh.issue_view(n).state == "CLOSED", f"#{n} was not closed"

    events = _logged_events(tmp_path)

    # --- Correct per-contribution Strike accounting across BOTH kinds of work:
    #     the two landed Lanes and the serial Iteration (a commit + a closure)
    #     each made progress, so nothing recorded a strike.
    assert [e for e in events if e["type"] == "wrapper.strike"] == []

    # --- The run terminated by draining the pool, not by the iteration cap or a
    #     strike abort.
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "empty_pool"

    # --- One auto_close per issue: the parallel-safe pair first (Lanes /
    #     Integration, in this deterministic fake's completion order), then the
    #     plain issue (serial turn).
    auto_closes = [
        e["issue"] for e in events if e["type"] == "wrapper.auto_close"
    ]
    assert auto_closes == [42, 43, 44]


# ---------------------------------------------------------------------------
# Per-Lane worktree setup (#65, ADR-0008)
# ---------------------------------------------------------------------------


class _SpyWorktreeSetup:
    """A scripted :class:`~git_loopy.worktree.WorktreeSetup` for the Lane e2e.

    Records each ``run(worktree)`` call together with how many sessions the fake
    client had created *at that moment* — used to prove a given Lane's own setup
    ran before that same Lane's own session started (matched by working
    directory). Under Rolling dispatch there is no global ordering across
    Lanes: one Lane's setup may overlap a sibling Lane's already-running
    session (#219 criteria #2/#3), so the snapshot is only meaningful when
    compared against the matching Lane's own session index, not in aggregate.
    Returns a scripted :class:`~git_loopy.worktree.SetupResult` so a test can
    drive the green path or a surfaced-failure path without touching a real
    subprocess.
    """

    def __init__(
        self, client: _ParallelFakeClient, *, result: SetupResult | None = None
    ) -> None:
        self._client = client
        self._result = result or SetupResult(command="echo prepared")
        self.calls: list[tuple[Path, int]] = []

    def run(self, worktree: Path) -> SetupResult:
        self.calls.append((Path(worktree), len(self._client.created)))
        return self._result


def _diag_log(tmp_path: Path) -> str:
    """The run's human-readable diagnostics log (``.git-loopy/logs/<...>.log``)."""
    logs_dir = tmp_path / ".git-loopy" / "logs"
    return next(logs_dir.glob("*.log")).read_text(encoding="utf-8")


def _wire_two_lane_rolling(
    tmp_path: Path, monkeypatch
) -> tuple[FakeGitClient, FakeGitHubClient, _ParallelFakeClient, RunConfig]:
    """Wire a green two-Lane Run (issues 42/43, ``parallel-safe``) via ``run``.

    Returns the fakes so a test can assert on them and inject its own
    ``_make_worktree_setup`` seam (else the real factory's auto-detect no-op runs
    against the fake's on-disk-absent worktrees).
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )
    return fake_git, fake_gh, fake_client, cfg


def test_parallel_lane_runs_worktree_setup_before_own_session(
    tmp_path, monkeypatch
) -> None:
    """Each Lane's worktree is prepared once, strictly before ITS OWN session.

    Acceptance (#65, and #219 §criterion #2/#3): ``GIT_LOOPY_WORKTREE_SETUP`` runs
    in each newly created Lane worktree before that same Lane's agent session.
    Under Rolling dispatch there is no barrier forcing every Lane's setup to
    precede every other Lane's session — one Lane's setup may legitimately
    overlap a sibling Lane's already-running session (the retired Wave-barrier
    guarantee this test used to assert no longer holds and is not a bug) — so
    this asserts the narrower, still-true invariant: matched by working
    directory, a Lane's own setup call always sees strictly fewer sessions
    created than the index of that same Lane's own session.
    """
    fake_git, _fake_gh, fake_client, cfg = _wire_two_lane_rolling(
        tmp_path, monkeypatch
    )
    spy = _SpyWorktreeSetup(fake_client)
    monkeypatch.setattr(loop_module, "_make_worktree_setup", lambda: spy)

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # Setup ran exactly once per Lane worktree (once per Lane creation)...
    add_paths = {p for (p, _b, _base) in _lane_worktree_adds(fake_git)}
    assert len(add_paths) == 2
    setup_paths = [wt for (wt, _n) in spy.calls]
    assert sorted(setup_paths) == sorted(add_paths)
    assert len(fake_client.created) == 2, "both Lane sessions still dispatched"

    # ...and, matched by working directory, each Lane's OWN setup call ran
    # before that SAME Lane's OWN session was created (no barrier across
    # Lanes is asserted or required).
    create_dirs = [
        call["working_directory"] for call in fake_client.create_calls
    ]
    for worktree, sessions_at_setup in spy.calls:
        own_session_index = create_dirs.index(str(worktree))
        assert own_session_index >= sessions_at_setup


def test_parallel_lane_surfaces_worktree_setup_failure_and_continues(
    tmp_path, monkeypatch
) -> None:
    """A failed setup is surfaced (not swallowed) and never aborts the Run.

    Acceptance (#65): a setup failure is surfaced rather than silently ignored.
    The spy returns a red :class:`SetupResult`; the failure is written to the
    run's diagnostics log for each Lane, yet both Lanes are still dispatched (a
    broken environment does not stop other Lanes' concurrent refill).
    """
    fake_git, _fake_gh, fake_client, cfg = _wire_two_lane_rolling(tmp_path, monkeypatch)
    failing = _SpyWorktreeSetup(
        fake_client,
        result=SetupResult(command="./setup.sh", returncode=3, output_tail="boom"),
    )
    monkeypatch.setattr(loop_module, "_make_worktree_setup", lambda: failing)

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # The failure is surfaced in the diagnostics log for BOTH Lanes...
    diag = _diag_log(tmp_path)
    assert "worktree setup for issue #42 FAILED" in diag
    assert "worktree setup for issue #43 FAILED" in diag
    assert "./setup.sh" in diag and "boom" in diag

    # ...but the Run still dispatched both Lanes (setup failure is non-fatal).
    assert len(failing.calls) == 2
    assert len(fake_client.created) == 2
    assert len(_lane_worktree_adds(fake_git)) == 2



def test_make_worktree_setup_binds_env_command(tmp_path, monkeypatch) -> None:
    """The ``_make_worktree_setup`` factory binds ``GIT_LOOPY_WORKTREE_SETUP``.

    Proves the env-only knob (like ``GIT_LOOPY_SEND_TIMEOUT_SECONDS``) reaches the
    adapter: the configured command runs in the target worktree.
    """
    monkeypatch.setenv("GIT_LOOPY_WORKTREE_SETUP", "  touch fromenv.marker  ")
    setup = loop_module._make_worktree_setup()
    result = setup.run(tmp_path)

    assert (tmp_path / "fromenv.marker").exists()
    assert result.command == "touch fromenv.marker"
    assert result.passed is True


def test_make_worktree_setup_blank_env_treated_as_unset(tmp_path, monkeypatch) -> None:
    """A blank ``GIT_LOOPY_WORKTREE_SETUP`` falls back to auto-detect, not a stub.

    On an empty worktree the auto-detect finds nothing, so a blank env yields a
    passing no-op (``command is None``) — had the factory not treated blank as
    unset, a whitespace command would have been run and ``command`` set instead.
    """
    monkeypatch.setenv("GIT_LOOPY_WORKTREE_SETUP", "   ")
    setup = loop_module._make_worktree_setup()

    assert setup.run(tmp_path).command is None


# ---------------------------------------------------------------------------
# Parallel-mode visibility (#304): saying that Parallel mode is on, and saying
# when it fell back to a serial Iteration for want of eligible Parallel-safe
# work.
# ---------------------------------------------------------------------------


def test_parallel_run_start_reports_parallel_mode_and_lane_cap(
    tmp_path, monkeypatch
) -> None:
    """A Parallel-mode Run says so at Run start, with the resolved Lane cap (#304).

    Requesting Parallel mode used to produce output byte-identical to a serial
    Run, so an operator whose tracker carried no ``parallel-safe`` issue
    reasonably concluded the flag was broken. ``wrapper.run.start`` now carries
    the configured **Lane cap** and the effective Lane limit the Run actually
    resolved (#219 §6 starts at ``min(cap, 3)``), which is what the operator's
    own output is rendered from.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=5,
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    asyncio.run(loop_module.run(cfg))

    run_start = next(
        e for e in _logged_events(tmp_path) if e["type"] == "wrapper.run.start"
    )
    assert run_start["parallel_mode"] is True
    assert run_start["lane_cap"] == 5
    assert run_start["effective_lane_limit"] == 3


def test_serial_run_start_carries_no_parallel_mode_report(
    tmp_path, monkeypatch
) -> None:
    """A serial Run says nothing about Parallel mode (#304).

    The visibility slice is additive: a Run that did not request Parallel mode
    keeps the ``wrapper.run.start`` payload it always had, so nothing on the
    default path changed.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
            serial_closes=True,
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=1,
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    asyncio.run(loop_module.run(cfg))

    events = _logged_events(tmp_path)
    run_start = next(e for e in events if e["type"] == "wrapper.run.start")
    assert "parallel_mode" not in run_start
    assert "lane_cap" not in run_start
    assert "effective_lane_limit" not in run_start
    assert [
        e for e in events if e["type"] == "wrapper.parallel.serial_fallback"
    ] == []


def test_parallel_reports_serial_fallback_when_nothing_carries_parallel_safe(
    tmp_path, monkeypatch
) -> None:
    """Parallel mode requested, nothing eligible: the Run says so and why (#304).

    The whole pool is plain ``ready-for-agent`` work, so every round falls back
    to a serial **Iteration**. That used to be byte-identical to a serial Run.
    Now the Run names the fallback, the eligible count it found, and the cause
    an operator can act on — nobody applied ``parallel-safe``.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(43, labels=["ready-for-agent"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
            serial_closes=True,
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=3,
        max_iterations=0,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    assert asyncio.run(loop_module.run(cfg)) == 0

    events = _logged_events(tmp_path)
    fallbacks = [
        e for e in events if e["type"] == "wrapper.parallel.serial_fallback"
    ]
    assert len(fallbacks) == 1, f"expected one fallback report, got {fallbacks}"
    (fallback,) = fallbacks
    assert fallback["reason"] == "no_parallel_safe_candidates"
    assert fallback["eligible"] == 0
    assert fallback["unavailable"] == 0
    assert fallback["worked"] == 0
    # Run-scoped, not contribution-scoped: it names work that never became a
    # Lane contribution, so it carries no contribution identity.
    assert "contribution_id" not in fallback
    assert "lane_id" not in fallback
    # It precedes the serial Iteration it explains.
    types = [e["type"] for e in events]
    assert types.index("wrapper.parallel.serial_fallback") < types.index(
        "wrapper.iteration.start"
    )
    # Dispatch is unchanged: the plain issue was still worked and closed.
    assert [n for (n, _c) in fake_gh.issue_close_calls] == [43]


def test_parallel_serial_fallback_separates_already_worked_from_unlabelled(
    tmp_path, monkeypatch
) -> None:
    """"Already worked this Run" is a different situation from "none labelled".

    #304's second distinction. Issue 42 carries ``parallel-safe`` and IS worked
    in a Lane; by the time the plain issue 43 gets its serial Iteration the
    eligible count is zero again — but because the Run already worked it, not
    because nobody triaged one. Telling the operator to go label something
    would be wrong.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
            serial_closes=True,
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=3,
        max_iterations=0,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    assert asyncio.run(loop_module.run(cfg)) == 0

    fallbacks = [
        e
        for e in _logged_events(tmp_path)
        if e["type"] == "wrapper.parallel.serial_fallback"
    ]
    assert [f["reason"] for f in fallbacks] == ["all_parallel_safe_worked"]
    assert fallbacks[0]["eligible"] == 0
    assert fallbacks[0]["worked"] == 1


# ---------------------------------------------------------------------------
# Truthful termination (#308)
# ---------------------------------------------------------------------------


class _UnreadableOnceGitHubClient(FakeGitHubClient):
    """A tracker whose authoritative read of one issue fails exactly once.

    Models the ordinary transient ``gh issue view`` failure (a 502, a dropped
    connection) that :meth:`~git_loopy.sources.GitHubIssueSource.collect_pool`
    has always survived by *skipping* the candidate. Self-healing on the second
    ask is the point: a permanently unreadable issue would only prove the Run
    hangs, whereas a transient one proves the Run waits for evidence and then
    acts on it.
    """

    def __init__(self, *, unreadable: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._unreadable = unreadable
        self.refusals = 0

    def issue_view(self, number: int) -> gh_module.Issue:
        if number == self._unreadable and self.refusals == 0:
            self.refusals += 1
            self.issue_view_calls.append(number)
            raise gh_module.GhError(
                ["gh", "issue", "view", str(number)], 1, "HTTP 502"
            )
        return super().issue_view(number)


def test_parallel_never_ends_empty_on_a_partial_pool_read(
    tmp_path, monkeypatch
) -> None:
    """A Pool the Run could not fully read is not an empty Pool (#308, #219 §2.13).

    The **Pool** of a Parallel-mode Run has two halves, and only one of them is
    the **Rolling dispatch** membership cache. Plain (non-``parallel-safe``)
    serial-required work is invisible to that cache — ``RollingPool`` filters
    membership down to Parallel-safe candidates — so the driver's own
    ``collect_pool`` peek is the *only* thing that can see it. When a
    candidate's authoritative read fails, ``collect_pool`` skips it, and the
    resulting collection is byte-identical to the one a genuinely empty tracker
    produces.

    So the Run here holds exactly one eligible issue — plain ``ready-for-agent``
    #44 — and the very first read of it fails. Nothing is Parallel-safe, so no
    **Lane** is ever reserved and the driver reaches its terminal check
    immediately, on that one partial read. Ending there would exit 0 having
    silently abandoned #44.

    Instead the Run withholds the empty-Pool claim, re-reads, finds #44, and
    works it serially — then terminates ``empty_pool`` on evidence.
    """
    monkeypatch.setattr(loop_module, "_ROLLING_EMPTY_POLL_INTERVAL", 0.01)
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = _UnreadableOnceGitHubClient(
        unreadable=44,
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(44, labels=["ready-for-agent"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
            serial_closes=True,
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    async def _bounded() -> int:
        # A Run that never re-reads would spin forever rather than terminate
        # early, so bound it: hanging is a failure, not a pass.
        return await asyncio.wait_for(loop_module.run(cfg), timeout=60)

    exit_code = asyncio.run(_bounded())

    # --- Non-vacuity: the read the terminal decision would have rested on
    #     really did fail.
    assert fake_gh.refusals == 1, "the partial read never happened"

    # --- #44 was not abandoned. It is the whole Pool, so a Run that treated
    #     the partial read as empty would have exited 0 with it still open.
    assert [n for (n, _c) in fake_gh.issue_close_calls] == [44]
    assert fake_gh.issue_view(44).state == "CLOSED"

    # --- And the Run still terminates truthfully once it HAS read the Pool.
    assert exit_code == 0
    run_end = next(
        e for e in _logged_events(tmp_path) if e["type"] == "wrapper.run.end"
    )
    assert run_end["outcome"] == "empty_pool"


class _TimelineFakeClient(_ParallelFakeClient):
    """A client that snapshots the live worktrees as each session is created.

    Under **Rolling dispatch** "a serial **Iteration** ran exclusively" is an
    absence, and an absence is only assertable against a moment. Session
    creation is that moment: the runner creates a session immediately before
    running it, so the worktrees live *then* are the work genuinely overlapping
    it — Lane worktrees and private **Integration stages** alike.
    """

    def __init__(self, *, fake_git: FakeGitClient, **kwargs: Any) -> None:
        super().__init__(fake_git=fake_git, **kwargs)
        self._git = fake_git
        self.live_worktrees: list[tuple[str | None, tuple[str, ...]]] = []

    async def create_session(self, **kwargs: Any) -> _ParallelFakeSession:
        live = {path for (path, _b, _base) in self._git.worktree_adds}
        live -= set(self._git.worktree_removes)
        self.live_worktrees.append(
            (kwargs.get("working_directory"), tuple(sorted(str(p) for p in live)))
        )
        return await super().create_session(**kwargs)




def test_parallel_lanes_resume_refilling_after_an_interleaved_serial_iteration(
    tmp_path, monkeypatch
) -> None:
    """Neither class of work starves, and the serial turn owns base alone (#308).

    The existing drain-everything test proves the Lanes-then-serial direction.
    This pins the other one, which is where starvation would actually live: a
    latched serial demand stops **Rolling dispatch** refilling
    (:attr:`RollingScheduler.refillable`), so if the latch never released, every
    **Parallel-safe** issue behind the ones already in flight would sit eligible
    and unworked for the rest of the Run.

    The Pool is #41 (serial-required) plus #42, #43 and #44 (all
    ``parallel-safe``) with a **Lane cap** of 2 — so #44 has no Lane to start
    in when the latch goes on, and is *forced* behind the serial Iteration
    rather than merely happening to follow it. The sequence the Run must
    produce is Lane(#42) + Lane(#43) -> serial(#41) -> Lane(#44):

    * #44's Lane exists at all, which is #219 §5.9's granted refill turn — the
      thing that makes the serial latch a handoff rather than a stop.
    * The serial Iteration ran with **no** worktree live: no Lane, no private
      **Integration stage**. §5.5 grants ownership only at full Parallel
      quiescence, because that Iteration owns the base worktree.
    * The serial Iteration re-collected and rendered its *own* authoritative
      Pool — including #44, which Rolling dispatch had not yet dispatched — and
      chose its own Active issue from it, exactly as a serial Run does (§5.6).
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(41, labels=["ready-for-agent"]),
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(44, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    # `serial_closes` picks the LOWEST issue its prompt offers, so numbering the
    # serial-required issue #41 is what makes the serial turn work #41 rather
    # than reaching past Rolling dispatch for the still-undispatched #44.
    fake_client = _TimelineFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
        serial_closes=True,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,  # two Lanes: #44 has nowhere to start before the latch
        max_iterations=0,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    async def _bounded() -> int:
        return await asyncio.wait_for(loop_module.run(cfg), timeout=60)

    assert asyncio.run(_bounded()) == 0

    # --- Lane(#42) + Lane(#43), serial, Lane(#44): the Run refilled a Lane
    #     AFTER the interleaved serial Iteration, so neither class starved.
    def _issue_of(working_directory: str | None) -> int | None:
        if working_directory is None:
            return None
        return int(Path(working_directory).name.removeprefix("issue-"))

    order = [_issue_of(wd) for (wd, _live) in fake_client.live_worktrees]
    assert order == [42, 43, None, 44], (
        f"expected Lanes -> serial -> a refilled Lane, got {order}"
    )

    # --- The serial Iteration owned base alone: nothing was live beside it.
    #     Non-vacuous — the same snapshot sees each Lane's own worktree live
    #     during that Lane's own session, so an empty one is a genuine teardown
    #     rather than an instrument that never observes anything.
    (_serial_wd, live_during_serial) = fake_client.live_worktrees[2]
    assert live_during_serial == (), (
        f"serial Iteration overlapped live worktrees: {live_during_serial}"
    )
    for issue, live in (
        (issue, live)
        for issue, (_wd, live) in zip(order, fake_client.live_worktrees)
        if issue is not None
    ):
        assert any(f"issue-{issue}" in path for path in live), (
            f"expected #{issue}'s own Lane worktree live during its session, "
            f"got {live}"
        )

    # --- It rendered its OWN authoritative Pool and chose from it: #44 was
    #     still eligible and undispatched, so a serial Run would have offered
    #     it, and this one does too.
    serial_prompt, _timeout = fake_client.created[2].send_and_wait_calls[0]
    assert "=== Issue #41:" in serial_prompt
    assert "=== Issue #44:" in serial_prompt
    assert "=== Issue #42:" not in serial_prompt, "#42 was closed by its Lane"

    # --- Nothing stranded, and the Run ended on a drained Pool.
    assert sorted(n for (n, _c) in fake_gh.issue_close_calls) == [41, 42, 43, 44]
    run_end = next(
        e for e in _logged_events(tmp_path) if e["type"] == "wrapper.run.end"
    )
    assert run_end["outcome"] == "empty_pool"


class _NoProgressFakeSession(_ParallelFakeSession):
    """An agent session that ends without committing or closing anything.

    The **Strike** machine's whole input is progress, so a fake that always
    commits can never drive one. This one returns its scripted usage and
    nothing else — the "agent thought about it and stopped" shape.
    """

    async def send_and_wait(
        self, prompt: str, *, timeout: float = 60.0, **_extra: Any
    ) -> SessionEvent | None:
        self.send_and_wait_calls.append((prompt, timeout))
        last: SessionEvent | None = None
        for evt in self._scripted_events:
            if self._on_event is not None:
                self._on_event(evt)
            last = evt
        return last


class _NoProgressFakeClient(_ParallelFakeClient):
    _session_cls = _NoProgressFakeSession


def test_parallel_serial_iteration_strike_abort_stops_the_run_stuck(
    tmp_path, monkeypatch
) -> None:
    """A serial Iteration's **Strike** abort ends a Parallel Run stuck (#308).

    #219 §7.4 shares ONE Strike machine between **Lane contributions** and
    serial **Iterations**, and §7.7 makes reaching its limit latch a
    drain-confirmed abort: refill stops, started work drains, and the Run exits
    ``stuck``. A finalized Lane contribution already latches that abort
    (``_apply_strike_reaction``) — but a serial Iteration ticks the very same
    machine, and the rolling driver used to discard the outcome it returned. So
    a Parallel-mode Run whose serial work made no progress accumulated Strikes,
    emitted the abort **Event**, and then just kept granting itself serial
    Iterations forever.

    The Pool here is one serial-required issue and an agent that never commits
    or closes, with ``max_nmt_strikes=2``. The Run must reach its limit and stop
    on the Wrapper contract's ``stuck`` exit rather than the ``empty_pool``
    exit or no exit at all.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(44, labels=["ready-for-agent"])],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _NoProgressFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=0,  # unbounded: only the Strike limit can stop this Run
        max_nmt_strikes=2,
        verbosity=0,
        render_reasoning=False,
    )

    async def _bounded() -> int:
        return await asyncio.wait_for(loop_module.run(cfg), timeout=60)

    exit_code = asyncio.run(_bounded())

    events = _logged_events(tmp_path)
    strikes = [e for e in events if e["type"] == "wrapper.strike"]
    assert [s["outcome"] for s in strikes] == ["warn", "abort"], (
        f"expected one warn then the abort, got {strikes}"
    )

    # --- The abort ends the Run, and no further serial Iteration is granted
    #     after it: §7.7 drains started work, it does not start new work.
    starts = [e for e in events if e["type"] == "wrapper.iteration.start"]
    assert len(starts) == 2, f"expected exactly two Iterations, got {len(starts)}"

    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "stuck"
    assert exit_code == loop_module.exit_code_for("stuck")


# ---------------------------------------------------------------------------
# #309 — bounded adaptive Lane concurrency, end to end
# ---------------------------------------------------------------------------


class _SteppingClock:
    """A monotonic clock that advances one observation interval per read.

    The driver's turns are event-driven, so a real Run's observation cadence is
    a fact about wall time. Making every read land in a new interval turns each
    driver turn into exactly one observation, which is what lets a test pin the
    reaction table's arithmetic without sleeping.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += rolling_pressure.OBSERVATION_INTERVAL_SECONDS
        return self.now


@dataclass
class _ThrottledTelemetry:
    """Telemetry for a Run GitHub is rate-limiting hard."""

    rate_limited_calls: int = 0
    burst: int = 3

    def read(self) -> rolling_pressure.PressureReading:
        seen = self.rate_limited_calls
        # Every read after the baseline reports a fresh burst, so the window
        # carries the ">=3 observed 429s" the -2 reaction is stated over.
        self.rate_limited_calls += self.burst
        return rolling_pressure.PressureReading(
            rate_limited_calls=seen,
            credit_spent_usd=1.0,
            host_pressure=0.5,
        )


def _wire_pressure(
    monkeypatch: pytest.MonkeyPatch,
    telemetry: object,
    *,
    budgets: rolling_pressure.PressureBudgets | None = None,
) -> None:
    """Inject a deterministic clock + telemetry into the Run's monitor."""
    monkeypatch.setattr(
        loop_module,
        "_make_pressure_monitor",
        lambda *, lane_cap, diag, **_seams: rolling_pressure.PressureMonitor(
            budgets=budgets or rolling_pressure.PressureBudgets(),
            telemetry=telemetry,
            clock=_SteppingClock(),
            controller=rolling_pressure.adaptive_controller(
                budgets or rolling_pressure.PressureBudgets(), lane_cap=lane_cap
            ),
            diag=diag,
        ),
    )


def _run_under_pressure(tmp_path, monkeypatch) -> int:
    """One two-issue Parallel Run, with every seam but pressure left alone."""
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=6,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )
    return asyncio.run(loop_module.run(cfg))


def _throttled_read(number: int) -> gh_module.GhError:
    """The failure ``gh`` raises when GitHub is rate-limiting a read."""
    return gh_module.GhError(
        ["gh", "issue", "view", str(number)],
        1,
        "HTTP 403: API rate limit exceeded for user ID 1 "
        "(https://api.github.com/graphql)",
    )


def _wire_production_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject only the clock, leaving the real telemetry chain in place.

    The point of the end-to-end 429 test is that a throttle observed by the
    ``gh`` seam reaches the policy *through production code* — the source's
    relay, :class:`~git_loopy.rolling_pressure.RunPressureTelemetry`, and the
    cumulative-to-per-observation differencing. Only the cadence is faked,
    because the driver's turns are event-driven and a real Run's observation
    window is a claim about wall time (#219 §6).
    """
    real = loop_module._make_pressure_monitor
    monkeypatch.setattr(
        loop_module,
        "_make_pressure_monitor",
        lambda **kwargs: replace_clock(real(**kwargs)),
    )


def replace_clock(
    monitor: rolling_pressure.PressureMonitor,
) -> rolling_pressure.PressureMonitor:
    """Re-pace ``monitor`` on a clock that steps one interval per read."""
    monitor.clock = _SteppingClock()
    return monitor


def test_parallel_narrows_lane_concurrency_under_sustained_rate_limits(
    tmp_path, monkeypatch
) -> None:
    """#219 §6: a throttled Run gives Lanes back and says which signal did it.

    The whole point of the adaptive controller reaching production: a Run that
    GitHub is rate-limiting must stop asking for more Lanes, and an operator
    reading the replay must be able to see *why* concurrency moved. The
    configured **Lane cap** never moves — only the effective limit does.

    Driven through the ``gh`` seam rather than through injected telemetry,
    because the 429 **Pressure signal** has a production path or it has
    nothing: ``gh`` exits 1 for a throttle exactly as it does for a closed
    issue, so a Run that does not classify the failure can never observe the
    one signal #219 §6 contracts hardest on. Here the two **serial-required**
    candidates fail their authoritative ``issue_view`` on every **Pool** read,
    which is what a rate-limited Run really looks like — and leaves the
    ``parallel-safe`` half free to keep working, so the throttle is the only
    thing under test.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(70, labels=["ready-for-agent"]),
            _make_issue(71, labels=["ready-for-agent"]),
            _make_issue(72, labels=["ready-for-agent"]),
        ],
        # Three, because #219 §6's -2 reaction is ">=3 observed 429s in 6
        # observations" and one **Pool** validation pass is what a rate-limited
        # Run throttles on.
        issue_view_errors={
            number: _throttled_read(number) for number in (70, 71, 72)
        },
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    _wire_production_pressure(monkeypatch)

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=6,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )
    asyncio.run(loop_module.run(cfg))

    events = _logged_events(tmp_path)
    changes = [e for e in events if e["type"] == "wrapper.concurrency.changed"]
    assert changes, "expected the throttled Run to narrow its Lane concurrency"
    first = changes[0]
    assert first["pressure"] == "rate_limit"
    assert first["configured_lane_limit"] == 6
    # 429 is the -2 reaction, from the static-safe 3 a Run starts at.
    assert first["effective_lane_limit"] == 1
    assert first["rate_limit_state"] >= 3
    # Run-scoped, like every other scheduler-level record: it names the Run's
    # capacity, not any one contribution's work.
    assert first["iter"] is None
    assert "contribution_id" not in first


def test_parallel_concurrency_change_matches_the_pinned_wire_shape(
    tmp_path, monkeypatch
) -> None:
    """#219 §8: the emitted payload is exactly what the Wrapper contract pins.

    Read off ``event-schema.json`` rather than restated here, so a contract the
    two ported runner families also honour cannot drift away from what this
    runner actually writes.
    """
    _wire_pressure(monkeypatch, _ThrottledTelemetry())
    _run_under_pressure(tmp_path, monkeypatch)

    contract = json.loads(
        (
            Path(__file__).parents[2] / "conformance" / "event-schema.json"
        ).read_text(encoding="utf-8")
    )["payload_contracts"]["wrapper.concurrency.changed"]
    (change, *_rest) = [
        e
        for e in _logged_events(tmp_path)
        if e["type"] == "wrapper.concurrency.changed"
    ]

    for key in contract["required_when_present"]:
        assert key in change, f"{key} missing from {change}"
    assert change["pressure"] in contract["pressure_values"]


def test_parallel_with_adaptation_disabled_never_moves_the_lane_limit(
    tmp_path, monkeypatch
) -> None:
    """#219 §6: adaptation off is static **Lane cap** behaviour, not a failure.

    An operator who turns the controller off keeps exactly the Run they had
    before it existed — the static-safe ``min(cap, 3)`` and no concurrency
    Events at all — however hard the same telemetry is being throttled.
    """
    telemetry = _ThrottledTelemetry()
    _wire_pressure(
        monkeypatch,
        telemetry,
        budgets=rolling_pressure.PressureBudgets(adaptive=False),
    )

    assert _run_under_pressure(tmp_path, monkeypatch) == 0

    events = _logged_events(tmp_path)
    assert [e for e in events if e["type"] == "wrapper.concurrency.changed"] == []
    run_start = next(e for e in events if e["type"] == "wrapper.run.start")
    assert run_start["effective_lane_limit"] == 3
    # Disabled costs no telemetry read either: an unobserved controller cannot
    # move, so there is nothing to read *for*.
    assert telemetry.rate_limited_calls == 0


def _run_summary(tmp_path: Path) -> dict[str, Any]:
    """The durable per-Run accounting record (``.git-loopy/runs/*.json``)."""
    runs_dir = tmp_path / ".git-loopy" / "runs"
    return json.loads(
        next(runs_dir.glob("*.json")).read_text(encoding="utf-8")
    )


def test_parallel_accounts_consumption_per_contribution_end_to_end(
    tmp_path, monkeypatch
) -> None:
    """Every **Lane contribution** carries its own Consumption to the record (#310).

    A **Lane** slot is reused many times per Run while a Lane contribution
    outlives the slot that started it, so accounting keyed by the slot -- or by
    a single Run-wide "current Iteration" slot -- attributes a contribution's
    tokens to whichever issue happens to occupy that slot when the numbers are
    read. Asserts the contribution-centric shape end to end at the two durable
    seams a replaying reader has: one ``wrapper.iteration.end`` per finalized
    contribution in the replay JSONL, each naming its own issue and carrying
    only its own **Consumption**, and the same one row per contribution in the
    Run summary JSON. Three issues over a cap of two force at least one slot
    reuse, so a slot-keyed accumulator cannot pass by accident.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(44, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=3,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    assert asyncio.run(loop_module.run(cfg)) == 0

    events = _logged_events(tmp_path)
    ends = [e for e in events if e["type"] == "wrapper.contribution.end"]
    assert [e["issue"] for e in ends] == [42, 43, 44], (
        "one finalized row per Lane contribution, named by its own issue"
    )
    lanes = [e["lane_id"] for e in ends]
    assert len(set(lanes)) < len(lanes), (
        "non-vacuity: a Lane slot really was reused, so slot-keyed accounting "
        "could not have passed by accident"
    )
    for end in ends:
        assert [row["issue"] for row in end["issues"]] == [end["issue"]], (
            "a contribution accounts for its own issue and no sibling's"
        )
        assert end["issues"][0]["consumption"] == {
            "model": "claude-opus-4.8-max",
            "tokens_in": 100,
            "tokens_out": 50,
        }
        assert end["issues"][0]["status"] == "closed"
        assert end["summary"]["tokens_in"] == 100
        assert end["summary"]["commits"] == 1
        assert end["summary"]["closure_outcome"] == "closed"
        assert end["published"] is True
        assert end["reason"] == "published"

    summary = _run_summary(tmp_path)
    assert [
        row["issues"][0]["issue"] for row in summary["iterations"]
    ] == [42, 43, 44]
    assert [row["tokens_in"] for row in summary["iterations"]] == [100, 100, 100]


def test_parallel_summary_carries_no_row_for_an_in_flight_contribution(
    tmp_path, monkeypatch
) -> None:
    """The Summary reports finalized contributions only (#310).

    A contribution's row is cut at the one seam where it is finalized -- the
    shared Strike reaction -- so a contribution still parked, integrating, or
    recovering has no partial row to be read as finished. Gating #43's
    **Integration** open while #42 publishes proves the distinction: at the
    moment #42's row exists, #43 has started, consumed tokens, and committed,
    and still contributes no row.
    """
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = _ParallelFakeClient(
        fake_git=fake_git,
        scripted_events=[_usage_event("claude-opus-4.8-max")],
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    gate_open = asyncio.Event()
    rows_when_43_gated: list[Any] = []
    fake_gate = FakeGateRunner()
    inner_run = fake_gate.run

    def gated_run(cwd: Path, *args: Any, **kwargs: Any):
        if cwd.name == "issue-43":
            rows_when_43_gated.extend(
                _logged_events(tmp_path)
            )
        return inner_run(cwd, *args, **kwargs)

    fake_gate.run = gated_run  # type: ignore[method-assign]
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: fake_gate)

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=2,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    assert asyncio.run(loop_module.run(cfg)) == 0
    assert gate_open is not None

    mid_run_ends = [
        e for e in rows_when_43_gated if e["type"] == "wrapper.contribution.end"
    ]
    assert [e["issue"] for e in mid_run_ends] == [42], (
        "#43 is mid-Integration and has no Summary row yet, though its session "
        "has already run, consumed tokens, and committed"
    )
    assert any(
        e["type"] == "wrapper.commit.recorded" and e.get("lane_issue") == 43
        for e in rows_when_43_gated
    ), "non-vacuity: #43 really was in flight, not merely un-started"


# ---------------------------------------------------------------------------
# The durable record replays to the same Dashboard (#310)
# ---------------------------------------------------------------------------


class _ObservingDriver:
    """Attaches a real :class:`LiveRunState` as the Run's sink, then drives.

    The interactive seam (ADR-0001) with the Textual half removed: enough to
    capture the **Dashboard** a live operator would have watched, so it can be
    compared against the one a reader rebuilds from the durable record alone.
    """

    def __init__(self, state: Any) -> None:
        self.state = state

    def attach_panes(self, *, summary: Any, log_source: Any) -> None:
        return None

    def attach_detach(self, *, sinks: Any, line_printer: Any, console: Any) -> None:
        return None

    async def run(self, drive: Callable[[], Any]) -> int:
        return await drive()


def _dashboard_projection(state: Any) -> dict[str, Any]:
    """The issue-centric Dashboard facts a reader must be able to rebuild."""
    from git_loopy.interactive.state import issue_detail, queue_rows

    rows = queue_rows(state, now=10_000.0)
    return {
        "queue": [
            (
                row.ref,
                row.status,
                row.active_seconds,
                row.started_wall,
                row.closed_wall,
                row.usage.model,
                row.usage.tokens_in,
                row.usage.tokens_out,
                row.usage_observed,
                row.iteration_count,
                row.cost_usd,
            )
            for row in rows
        ],
        "contributions": {
            row.ref: [
                (c.kind, c.lane, c.iteration, c.status, c.outcome, c.active_seconds)
                for c in issue_detail(state, row.ref).contributions
            ]
            for row in rows
        },
        "logs": {
            row.ref: [line.text for line in state.log(row.ref)] for row in rows
        },
    }


def test_a_rolling_run_replays_from_its_own_record_to_the_same_dashboard(
    tmp_path, monkeypatch
) -> None:
    """Persistence round-trips a rolling Run (#310).

    The replay JSONL is the durable record, and a **Lane contribution** is only
    reconstructible from it if every fact the Dashboard shows about that
    contribution -- its issue, status, timing, **Consumption**, Cost, and Log --
    is carried *in the record*, attributed to the contribution rather than to
    the reusable **Lane** slot. Drives a real rolling Run with a live
    ``LiveRunState`` attached, then rebuilds a second one from the written log
    alone and asserts the two agree.
    """
    from git_loopy.interactive.state import LiveRunState

    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(43, labels=["ready-for-agent", "parallel-safe"]),
            _make_issue(44, labels=["ready-for-agent", "parallel-safe"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    live = LiveRunState()
    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=2,
        max_iterations=3,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    assert asyncio.run(loop_module.run(cfg, driver=_ObservingDriver(live))) == 0

    replayed = LiveRunState()
    for event in _logged_events(tmp_path):
        replayed.render(event)

    live_view = _dashboard_projection(live)
    assert [ref for (ref, *_rest) in live_view["queue"]] == [42, 43, 44]
    assert all(row[1] == "closed" for row in live_view["queue"])
    assert [
        (c[0], c[1], c[2], c[3], c[4])
        for c in live_view["contributions"][42]
    ] == [("lane", 42, None, "closed", "closed")]
    assert _dashboard_projection(replayed) == live_view
