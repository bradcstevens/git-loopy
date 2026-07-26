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
**Integration** (#62) lands its branch on base in ascending issue-number
order (serialized across concurrently-admitted contributions via an internal
lock, never blocking any OTHER Lane's setup or session), gates it via the
injected :class:`~git_loopy.gate.GateRunner`, and closes the issue with the
serial closure semantics; a red gate reverts/aborts the Lane and hands it to
bounded auto-resolution before falling back to a serial Iteration.

**A single eligible ``parallel-safe`` issue starts a Lane immediately**
(#219 §1.4) — the retired Wave's ">= 2 eligible" threshold is gone; see
:func:`test_parallel_single_eligible_issue_starts_lane_immediately`.

**No barrier — continuous refill (#219 §1.3, criteria #2/#3).** A finished
Lane's slot becomes available for refill the instant its contribution is
admitted or terminates, without waiting for any other Lane; one Lane's
worktree setup may freely overlap another Lane's agent session. See
:func:`test_parallel_lane_refills_without_waiting_for_sibling` and
:func:`test_parallel_lane_cap_never_exceeded_under_bursty_refill`.

**Drain-everything (#67, ADR-0008).** A Parallel run interleaves Lane work for
the ``parallel-safe`` issues with serial Iterations for every other
``ready-for-agent`` issue, in one run, draining all eligible work. Under
Rolling dispatch the shared Strike machine reacts once per **finalized Lane
contribution** (not once per round, since there is no round) — see
:func:`test_parallel_run_drains_lanes_then_serial_in_one_run`.

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
    :class:`_ParallelFakeSession` bound to it.
    """

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
        session = _ParallelFakeSession(
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
    adds = fake_git.worktree_adds
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
    assert len(fake_git.worktree_removes) == 2
    assert set(fake_git.worktree_removes) == add_paths
    assert fake_git.active_worktrees == []

    # Integration (#62) landed both green Lanes on base — base advanced past the
    # prior commit — and closed both issues via the serial closure path, in
    # ascending issue-number order.
    assert fake_git.head_sha() != "0000000000000000000000000000000000000001"
    assert [n for (n, _c) in fake_gh.issue_close_calls] == [42, 43]
    # One wrapper.auto_close event per landed + closed Lane, same order.
    auto_closes = [e for e in events if e["type"] == "wrapper.auto_close"]
    assert [e["issue"] for e in auto_closes] == [42, 43]
    # Both integrated Lane branches deleted (breadcrumbs are for failures only).
    assert sorted(fake_git.branch_deletes) == sorted(branches)


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
    initial landing, so Integration reverts and runs a bounded auto-resolution
    agent for it; that attempt is green and lands. Asserts the auto-resolution
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
    "round", so it never emits ``wrapper.iteration.start``/``.end`` (that
    would corrupt the single-slot rollup accumulator if two Lanes were
    in flight at once) — a known, deliberate scope limitation of #306; this
    test also pins that gap so a future rollup slice has a clear before/after.
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
    # No "round" exists under Rolling dispatch, so a Lane contribution never
    # emits `wrapper.iteration.start`/`.end` (see this test's docstring).
    assert [
        e for e in events if e["type"] in ("wrapper.iteration.start", "wrapper.iteration.end")
    ] == []


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
    adds = fake_git.worktree_adds
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
        str(p) for (p, _b, _base) in fake_git.worktree_adds
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
    adds = fake_git.worktree_adds
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
    adds = fake_git.worktree_adds
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
    deleted = sorted(fake_git.branch_deletes)
    assert len(deleted) == 2
    assert deleted[0].endswith("/issue-42")
    assert deleted[1].endswith("/issue-43")

    # No worktrees left live once Integration has processed every contribution.
    assert fake_git.active_worktrees == []


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
    """Red-throughout Integration: revert keeps base green, falls back to serial.

    Evolves the #62 happy-path contract deliberately (#63, ADR-0009). With every
    Lane's gate red *and* every auto-resolution attempt red, each Lane: merges
    cleanly, gates red, is **reverted** so base stays green, runs the bounded
    K=3 auto-resolution agent (all red), then falls back to a serial Iteration
    with **exactly one** breadcrumb comment — its Lane branch **kept** (only the
    throwaway integration branch is deleted). Nothing lands, so each
    no-progress contribution's ``finalize`` records its own warn strike
    (#219 §7.9: the scheduler records the reaction per-contribution, not once
    per round) — two Lanes, two strikes. Both Lanes already spent their
    ``max_iterations`` unit at pickup, so — even though each fallback latches a
    serial request — the cap is already exhausted and the run ends via
    ``iteration_cap`` without a further serial Iteration ever running.
    Assertions are on observable effects only.
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

    # Base stayed green: each clean-but-red landing was reverted (one per Lane).
    assert len(fake_git.reverts) == 2

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
    # either fallback's latched serial request could be granted.
    assert [e for e in events if e["type"] == "wrapper.iteration.end"] == []
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "iteration_cap"
    assert run_end["iterations_run"] == 2


def test_parallel_integration_auto_resolves_red_lane_then_lands(
    tmp_path, monkeypatch
) -> None:
    """A red Lane is reverted, auto-resolved on a later attempt, and lands (#63).

    Issue 42's Lane merges cleanly but its gate goes red on the initial landing
    AND on the first auto-resolution attempt, then passes on the second attempt;
    issue 43 is green throughout. The scripted gate is a global call-ordered
    queue ``[42-postmerge=red, 42-att1=red, 42-att2=green]`` with the default
    (green) covering 43. Asserts (observable effects only): base is reverted
    once (stays green), the K-bounded auto-resolution agent runs exactly twice
    for 42 in its dedicated integration worktree, both issues end CLOSED with one
    ``auto_close`` each, no breadcrumb is posted, and — because two Integrations
    landed — the round records no strike.
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

    # Base stayed green: the clean-but-red landing of 42 was reverted exactly
    # once (43 was green on its first landing, so it was never reverted).
    assert fake_git.reverts == [git_module.lane_branch_name(_run_id(tmp_path), 42)]

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


def test_parallel_integration_aborts_conflicting_merge_then_auto_resolves(
    tmp_path, monkeypatch
) -> None:
    """A conflicting Lane merge is aborted (not reverted), then auto-resolved (#63).

    Issue 42's Lane branch is scripted to *conflict* on merge; issue 43 merges
    cleanly. A conflict leaves no merge to revert, so recovery must
    ``git merge --abort`` (not ``git revert``) to keep base green, then run the
    auto-resolution agent — which passes on its first attempt here (all-green
    gate) and lands 42. Asserts the abort fired exactly once, no revert
    happened, both issues closed, and the round made progress (no strike).
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

    # The conflict path aborts (base stays green) and never reverts.
    assert fake_git.merge_aborts == 1
    assert fake_git.reverts == []

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
    42 to closure. Asserts base stayed green (reverted), the auto-resolution
    agent ran exactly K=3 times, exactly ONE breadcrumb comment was posted on 42,
    42's Lane branch was KEPT (only its throwaway integration branch deleted),
    and the run drained the pool (both issues CLOSED, ``empty_pool``).
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

    # Base stayed green: 42's clean-but-red landing was reverted.
    assert git_module.lane_branch_name(_run_id(tmp_path), 42) in fake_git.reverts

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
    adds = fake_git.worktree_adds
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
    add_paths = {p for (p, _b, _base) in fake_git.worktree_adds}
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
    assert len(fake_git.worktree_adds) == 2



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
