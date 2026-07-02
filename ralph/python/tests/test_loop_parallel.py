"""End-to-end integration tests for Parallel mode (#61, ADR-0008).

Drives the opt-in Wave/Lane orchestrator through the public
:func:`ralph_afk.loop.run` seam with the SDK + git / gh / gate seams faked,
asserting the **observable effects** of concurrent isolated execution — one
worktree + branch per Lane created in a sibling directory, each session pinned
to its Lane's worktree via ``working_directory``, per-Lane commits landing on
Lane branches (never on base), and the worktrees torn down at the Wave barrier
— not internal call ordering.

The fakes here (unlike the serial ``test_iteration_end_to_end`` client) record
the per-session ``working_directory`` and route each Lane's simulated agent
commit to the *right* worktree's child :class:`~tests.fakes.FakeGitClient`, so
the test can prove per-Lane isolation. Landing green Lanes on base + closing
their issues is Integration (#62/#63), NOT this slice, so a Wave here makes no
base-branch closures.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from copilot.generated.session_events import (
    AssistantUsageData,
    SessionEvent,
    SessionEventType,
)

from ralph_afk import gh as gh_module
from ralph_afk import git as git_module
from ralph_afk import loop as loop_module
from ralph_afk.config import RunConfig
from tests.fakes import FakeGateRunner, FakeGitClient, FakeGitHubClient


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
    ) -> None:
        self._on_event = on_event
        self._working_directory = working_directory
        self._fake_git = fake_git
        self._scripted_events = scripted_events
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
        else:
            target = self._fake_git
        if target is not None:
            target.simulate_agent_commit(
                subject="feat(lane): implement issue",
                body="",
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
    ) -> None:
        self._fake_git = fake_git
        self._scripted_events = scripted_events
        self.create_calls: list[dict[str, Any]] = []
        self.created: list[_ParallelFakeSession] = []
        self.stop_call_count = 0

    async def create_session(
        self,
        *,
        on_permission_request: Any,
        on_event: Callable[[SessionEvent], None] | None = None,
        on_user_input_request: Any = None,
        model: str | None = None,
        working_directory: str | None = None,
        **_extra: Any,
    ) -> _ParallelFakeSession:
        self.create_calls.append(
            {"working_directory": working_directory, "model": model}
        )
        session = _ParallelFakeSession(
            on_event=on_event,
            working_directory=working_directory,
            fake_git=self._fake_git,
            scripted_events=self._scripted_events,
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
    logs_dir = tmp_path / ".ralph" / "logs"
    lines = (
        next(logs_dir.glob("*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return [json.loads(raw) for raw in lines]


def _wire_repo(tmp_path: Path) -> FakeGitClient:
    (tmp_path / "ralph").mkdir()
    (tmp_path / "ralph" / "prompt.md").write_text(
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
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parallel_run_dispatches_two_lane_wave(tmp_path, monkeypatch) -> None:
    """A two-Lane Wave: two isolated worktrees, two advanced Lane branches.

    Both issues carry ``ready-for-agent`` + ``parallel-safe``, so with
    ``parallel=2`` the round is a Wave. Asserts (observable effects only): one
    worktree + Lane branch per issue created in a sibling directory, each
    session pinned to its Lane's worktree via ``working_directory``, each
    Lane's commit landing on its *own* branch (base head unchanged), and the
    worktrees torn down at the barrier.
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
        max_iterations=1,
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
    # Deterministic ``copiloop/<run_id>/issue-<N>`` branch names, one run_id.
    assert branches[0].startswith("copiloop/")
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

    # Base head is UNCHANGED — this slice never lands Lanes on base (#62 does),
    # and no issues were closed (Integration is a later slice).
    assert fake_git.head_sha() == "0000000000000000000000000000000000000001"
    assert fake_gh.issue_close_calls == []

    # Worktrees torn down at the Wave barrier (branches kept as breadcrumbs).
    assert len(fake_git.worktree_removes) == 2
    assert set(fake_git.worktree_removes) == add_paths
    assert fake_git.active_worktrees == []


def test_parallel_run_falls_back_to_serial_when_under_two_eligible(
    tmp_path, monkeypatch
) -> None:
    """< 2 eligible parallel-safe issues: the round is one serial Iteration.

    The pool has a single ``parallel-safe`` issue plus a plain
    ``ready-for-agent`` issue. A Wave needs at least two eligible issues, so the
    round falls back to a normal serial Iteration — no worktrees, one
    unpinned session — and neither issue is stranded (eligibility is a human
    assertion, never inferred).
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
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    monkeypatch.setattr(
        loop_module, "_make_gate_runner", lambda: FakeGateRunner()
    )

    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        parallel=3,
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # No Wave: fewer than two eligible parallel-safe issues.
    assert fake_git.worktree_adds == [], "no worktrees when a Wave can't form"
    assert fake_git.worktree_removes == []

    # Exactly one serial session, NOT worktree-pinned (serial path).
    assert len(fake_client.created) == 1
    assert fake_client.create_calls[0]["working_directory"] is None

    # The serial Iteration works the whole pool — both issues appear in the
    # one prompt, so no eligible work is stranded by opting into Parallel mode.
    prompt, _timeout = fake_client.created[0].send_and_wait_calls[0]
    assert "Issue #42" in prompt
    assert "Issue #43" in prompt
