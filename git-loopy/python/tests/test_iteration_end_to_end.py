"""End-to-end integration test for :mod:`git_loopy.loop`.

Drives one full ``git-loopy`` iteration with the SDK and git/gh seams
mocked, asserts the canonical artefacts land on disk with the
documented schema, and exercises the auto-close backstop (commit
referencing ``Closes #42`` triggers ``gh.issue_close(42, ...)``).

The test does **not** spin up a real Copilot session or hit the GitHub
API; it monkeypatches:

* ``git_loopy.loop._make_client`` → a :class:`FakeCopilotClient`
  (reused from :mod:`tests.test_session`) that scripts a single
  iteration's SDK event flow.
* ``git_loopy.loop._make_github_client`` → a single
  :class:`~tests.fakes.FakeGitHubClient` (issue #47) injected through the
  loop's ``gh`` seam, replacing the old per-function ``git_loopy.gh.*``
  monkeypatches. Its issue store keeps ``issue_list`` / ``issue_view``
  consistent, and its ``issue_close`` records the call AND flips the issue to
  ``CLOSED`` by construction (modelling the auto-close backstop's re-verify).
* ``git_loopy.loop._make_git_client`` → a single
  :class:`~tests.fakes.FakeGitClient` (issue #46) injected through the loop's
  git seam, replacing the old per-function ``git_loopy.git.*`` monkeypatches.
  Its stateful linear commit log keeps ``head_sha`` / ``commits_between`` /
  ``recent_commits`` consistent, and :meth:`FakeGitClient.simulate_agent_commit`
  (driven from the SDK stub's ``on_send`` hook) models the agent's commit
  landing between the pre- and post-iteration head reads.

After ``loop.run`` returns, the test asserts:

* Return code 0.
* ``.git-loopy/logs/<stem>.jsonl`` exists and every line is envelope-
  conformant JSON.
* ``.git-loopy/runs/<stem>.json`` exists and matches the persist schema
  (one iteration row, expected counts).
* ``.gitignore`` contains ``.git-loopy/`` (the persist factory touches it).
* The fake client's ``issue_close`` was called exactly once with the right
  arguments (auto-close backstop fired).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast
from uuid import uuid4

import pytest
from copilot import CopilotClient
from copilot.generated.session_events import (
    AssistantMessageData,
    AssistantUsageData,
    PermissionRequestCustomTool,
    SessionErrorData,
    SessionEvent,
    SessionEventType,
    SessionUsageInfoData,
    ToolExecutionStartData,
)

from git_loopy.denomination import BilledCreditsDenomination
from git_loopy import cli
from git_loopy import events as events_module
from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import loop as loop_module
from git_loopy import routing_scope
from git_loopy import settings
from git_loopy import skill_install
from git_loopy import sources as sources_module
from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.emit import EventEmitter
from git_loopy.events import REDACTED_SECRET
from git_loopy.persist import WritersBundle, create_writers
from git_loopy.session import SKILL_TOOL_NAME
from git_loopy.sinks import SinkFanout
from git_loopy.skill_catalog import build_skill_catalog
from git_loopy.ui import RunSummary
from git_loopy.wrapper import is_checkpoint_message
from tests.fakes import FakeGitClient, FakeGitHubClient


EXPECTED_RELEASE_VERSION = json.loads(
    (
        Path(__file__).parents[2] / "conformance" / "release-version.json"
    ).read_text(encoding="utf-8")
)["expected_release_version"]


# ---------------------------------------------------------------------------
# Fakes — minimal stand-ins for the SDK + git/gh surface the loop touches.
# ---------------------------------------------------------------------------


class FakeCopilotSession:
    """Stub for :class:`copilot.CopilotSession`.

    Holds the registered ``on_event`` callback. ``send_and_wait`` drives
    a scripted SDK event flow against the callback then returns.
    """

    def __init__(
        self,
        *,
        on_event: Callable[[SessionEvent], None] | None,
        scripted_events: list[SessionEvent],
        on_send: Callable[[], None] | None = None,
    ) -> None:
        self._on_event = on_event
        self._scripted_events = scripted_events
        self._on_send = on_send
        self.session_id = "fake-session-id"
        self.send_and_wait_calls: list[tuple[str, float]] = []

    async def send_and_wait(
        self,
        prompt: str,
        *,
        timeout: float = 60.0,
        **_extra: Any,
    ) -> SessionEvent | None:
        self.send_and_wait_calls.append((prompt, timeout))
        # Model the agent doing its work *during* the session — between the
        # loop's pre- and post-iteration ``head_sha`` reads — so an injected
        # commit advances the fake git log while the SDK "runs".
        if self._on_send is not None:
            self._on_send()
        last: SessionEvent | None = None
        for evt in self._scripted_events:
            if self._on_event is not None:
                self._on_event(evt)
            last = evt
        return last

    async def disconnect(self) -> None:
        return None


class FakeCopilotClient:
    """Stub for :class:`copilot.CopilotClient` shaped for the loop.

    The loop calls ``create_session(...)`` per iteration and ``stop()``
    once at the end. ``create_session`` returns a :class:`FakeCopilotSession`
    pre-loaded with the test's scripted events.
    """

    def __init__(
        self,
        scripted_events: list[SessionEvent],
        *,
        on_send: Callable[[], None] | None = None,
    ) -> None:
        self._scripted_events = scripted_events
        self.on_send = on_send
        self.created: list[FakeCopilotSession] = []
        self.create_calls: list[dict[str, Any]] = []
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
        **extra: Any,
    ) -> FakeCopilotSession:
        self.create_calls.append({"model": model, **extra})
        session = FakeCopilotSession(
            on_event=on_event,
            scripted_events=self._scripted_events,
            on_send=self.on_send,
        )
        self.created.append(session)
        return session

    async def stop(self) -> None:
        self.stop_call_count += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sdk_event(
    et: SessionEventType,
    data: Any,
    *,
    ts: datetime | None = None,
) -> SessionEvent:
    return SessionEvent(
        data=data,
        id=uuid4(),
        timestamp=ts if ts is not None else datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc),
        type=et,
    )


def _make_issue(
    number: int,
    *,
    body: str = "## Parent\nfoo\n\n## What to build\nthing\n\n## Acceptance criteria\nbar",
    state: str = "OPEN",
) -> gh_module.Issue:
    return gh_module.Issue(
        number=number,
        title=f"Test issue {number}",
        body=body,
        labels=["ready-for-agent"],
        state=state,
        url=f"https://github.com/x/y/issues/{number}",
        comments=(),
    )


def _log_lines(tmp_path: Path) -> list[str]:
    """Return the raw JSONL lines the run logged, in order."""
    logs_dir = tmp_path / ".git-loopy" / "logs"
    return next(logs_dir.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()


def _logged_types(tmp_path: Path) -> list[str]:
    """Return the ordered ``type`` of every JSONL event the run logged."""
    return [json.loads(raw)["type"] for raw in _log_lines(tmp_path)]


@pytest.fixture(autouse=True)
def _stub_run_skill_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    async def discover(_client: object, **kwargs: object):
        return build_skill_catalog(
            (),
            repo_root=Path(str(kwargs["repo_root"])),
            installed_skills_dir=Path(str(kwargs["installed_skills_dir"])),
        )

    monkeypatch.setattr(loop_module, "_discover_skill_catalog", discover)


# ---------------------------------------------------------------------------
# The end-to-end test
# ---------------------------------------------------------------------------


def test_loop_runs_one_iteration_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    """One iteration: SDK fires events; loop persists JSONL + run summary; auto-close fires.

    Wires:

    * tmp_path as the repo root with a ``git-loopy/prompt.md`` and a
      pre-existing ``.gitignore``.
    * Two issues in the AFK-ready pool (#42 OPEN with discriminator;
      #43 OPEN without — should be filtered out at the body-discriminator
      step).
    * A scripted SDK event flow: ``session.created`` → ``tool.execution.start``
      → ``assistant.message`` → ``assistant.usage`` →
      ``session.idle``.
    * Mocked git: one new commit between pre and post HEAD; commit
      message references ``Closes #42`` so the auto-close backstop
      should fire.
    """
    # -- 1) Fake repo on disk ---------------------------------------------
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text(
        "You are the agent. Implement the AFK-ready issues.\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    # -- 2) git stubs ------------------------------------------------------
    # -- 2) git seam: FakeGitClient seeded with the prior commit ----------
    # The agent's commit (with ``Closes #42``) is appended *during* the SDK
    # session via the client's ``on_send`` hook (wired below), so the
    # post-iteration head advances past the pre-iteration head and
    # ``commits_between`` yields exactly that agent commit.
    fake_git = FakeGitClient(
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
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    # -- 3) gh seam: FakeGitHubClient seeded with the AFK-ready pool ------
    # #42 is AFK-ready (carries the discriminator); #43 lacks it and is
    # filtered at the list stage before any ``issue_view``. The fake's
    # ``issue_close`` records the call AND flips #42 OPEN -> CLOSED by
    # construction, modelling the transition the auto-close re-verify relies
    # on (no ``issue_42_state`` bookkeeping needed).
    issue_42 = _make_issue(42)
    issue_43_no_discrim = _make_issue(
        43, body="no parent here, no AC here, just words"
    )
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[issue_42, issue_43_no_discrim],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    # -- 4) SDK stub: one tool call + one assistant message + usage -------
    scripted = [
        _sdk_event(
            SessionEventType.TOOL_EXECUTION_START,
            ToolExecutionStartData(
                tool_call_id="call-1",
                tool_name="view",
                arguments={
                    "path": ".copilot/skills/tdd/SKILL.md",
                    "padding": "x" * 2_000,
                },
            ),
        ),
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(
                content="Implementing #42.",
                message_id="m1",
            ),
        ),
        _sdk_event(
            SessionEventType.ASSISTANT_USAGE,
            AssistantUsageData(
                input_tokens=1234,
                output_tokens=567,
                model="claude-opus-4.7-xhigh",
            ),
        ),
        _sdk_event(
            SessionEventType.SESSION_USAGE_INFO,
            SessionUsageInfoData(
                current_tokens=12_000,
                messages_length=3,
                token_limit=32_000,
            ),
        ),
    ]

    fake_client = FakeCopilotClient(scripted_events=scripted)
    # The agent authors its commit (referencing ``Closes #42``) mid-session.
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        sha="abcdef1234567890abcdef1234567890abcdef12",
        subject="feat(thing): implement",
        body="Closes #42",
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    # -- 5) Run loop with max_iterations=1 -------------------------------
    cfg = RunConfig(
        model="claude-opus-4.7-xhigh",
        issue_source="github",
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )

    exit_code = asyncio.run(loop_module.run(cfg))

    # -- 6) Assertions ---------------------------------------------------
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # SDK lifecycle.
    assert len(fake_client.created) == 1, "expected exactly one SDK session"
    assert fake_client.start_call_count == 1
    assert fake_client.stop_call_count == 1, "client.stop() must be called once at end"
    assert fake_client.create_calls[0]["enable_skills"] is True
    disabled_skills = fake_client.create_calls[0]["disabled_skills"]
    assert disabled_skills == sorted(disabled_skills)
    assert len(fake_client.create_calls[0]["skill_directories"]) == 1

    # send_and_wait got the prompt.
    sdk_session = fake_client.created[0]
    assert len(sdk_session.send_and_wait_calls) == 1
    prompt, timeout = sdk_session.send_and_wait_calls[0]
    assert "Previous commits:" in prompt
    assert "Issue #42" in prompt
    # #43 lacks the discriminator and must be filtered out.
    assert "Issue #43" not in prompt
    assert "You are the agent" in prompt
    assert timeout > 60.0, f"send_and_wait timeout must exceed SDK default; got {timeout}"

    # Auto-close fired for #42, not for any other issue.
    assert len(fake_gh.issue_close_calls) == 1, (
        f"expected exactly one close call for #42; got {fake_gh.issue_close_calls}"
    )
    assert fake_gh.issue_close_calls[0][0] == 42
    assert "abcdef1234" in fake_gh.issue_close_calls[0][1], (
        f"close comment should reference the closing commit SHA; "
        f"got {fake_gh.issue_close_calls[0][1]!r}"
    )

    # .gitignore touched.
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".git-loopy/" in gitignore.splitlines()

    # JSONL log present + envelope conformant.
    logs_dir = tmp_path / ".git-loopy" / "logs"
    jsonl_files = list(logs_dir.glob("*.jsonl"))
    assert len(jsonl_files) == 1, (
        f"expected exactly one JSONL log; got {jsonl_files}"
    )
    log_lines = jsonl_files[0].read_text(encoding="utf-8").splitlines()
    assert log_lines, "JSONL log must not be empty"
    events_seen: list[dict[str, Any]] = []
    types_seen: list[str] = []
    for raw in log_lines:
        evt = json.loads(raw)
        assert set(evt.keys()) >= {"ts", "run_id", "iter", "type"}, (
            f"event missing envelope keys: {evt!r}"
        )
        events_seen.append(evt)
        types_seen.append(evt["type"])
    # Must have seen at minimum: run.start, iteration.start, afk_ready,
    # iteration.end, commit.recorded, auto_close, run.end.
    for expected_type in (
        "wrapper.skill_policy.resolved",
        "wrapper.run.start",
        "wrapper.iteration.start",
        "wrapper.afk_ready.collected",
        "wrapper.issue.activated",
        "wrapper.commit.recorded",
        "wrapper.auto_close",
        "wrapper.iteration.end",
        "wrapper.run.end",
    ):
        assert expected_type in types_seen, (
            f"expected to see {expected_type} in JSONL log; "
            f"saw types: {types_seen}"
        )
    assert types_seen.index("wrapper.skill_policy.resolved") < types_seen.index(
        "wrapper.run.start"
    )
    activation = next(
        event
        for event in events_seen
        if event["type"] == "wrapper.issue.activated"
    )
    assert activation["issue"] == 42
    # Bound at the serial **Pickup**, before the session — not inferred from
    # the closure afterwards (#394). The retroactive fallbacks survive for the
    # case Pickup could not bind; they are no longer the normal path.
    assert activation["binding_source"] == "serial_pickup"
    assert activation["activated_at"] == activation["ts"]
    iteration_start = next(
        event
        for event in events_seen
        if event["type"] == "wrapper.iteration.start"
    )
    # A **Pickup** binds at the instant it selects, which is inside the
    # Iteration and after the Pool read — not retroactively at the Iteration's
    # own start, which is what a fallback binding had to claim.
    assert activation["activated_at"] >= iteration_start["ts"]
    assert types_seen.index("wrapper.issue.activated") < types_seen.index(
        "wrapper.commit.recorded"
    )
    run_start = next(
        json.loads(raw)
        for raw in log_lines
        if json.loads(raw)["type"] == "wrapper.run.start"
    )
    assert run_start["schema_version"] == 1
    assert run_start["release_version"] == EXPECTED_RELEASE_VERSION
    assert run_start["insight_capabilities"] == {
        "agent_output": True,
        "structured_agent_events": True,
        "token_usage": True,
        "context_window": True,
        "skill_consultation": True,
        "cost": True,
        # Run-scoped (#331): this Run reached no live model listing, so it holds
        # no Rate card. Declared `false` rather than omitted -- and beside a
        # `cost: True` it does not disturb, because nothing derives from a card.
        "rate_card": False,
    }
    # #311 AC3: a serial Run declares its scheduling capabilities too. The
    # manifest describes the distribution, not the Run: an operator reading a
    # serial trace still learns whether asking for Lanes would have worked.
    assert run_start["parallel_capabilities"] == {
        "parallel_mode": True,
        "rolling_dispatch": True,
        "integration_backlog": True,
        "adaptive_lane_limit": True,
        "contribution_events": True,
    }
    iteration_end = next(
        event
        for event in events_seen
        if event["type"] == "wrapper.iteration.end"
    )
    assert iteration_end["outcome"] == "closed"
    assert iteration_end["duration_seconds"] >= 0
    assert iteration_end["summary"] == {
        "model": "claude-opus-4.7-xhigh",
        "tokens_in": 1234,
        "tokens_out": 567,
        "observed_tokens": 1801,
        "credits": None,
        "premium_requests": None,
        "cache_read": None,
        "cache_write": None,
        "tool_count": 1,
        "skill_call_count": 0,
        "skills_consulted": ["tdd"],
        "commits": 1,
        "auto_closures": 1,
        "pr_advances": 0,
        "strikes": 0,
        "peak_context_window": {
            "current_tokens": 12_000,
            "token_limit": 32_000,
            "effective_target_tokens": 16_000,
            "effective_ceiling_tokens": 24_000,
        },
    }
    assert len(iteration_end["issues"]) == 1
    issue_contribution = iteration_end["issues"][0]
    assert issue_contribution["issue"] == 42
    assert issue_contribution["status"] == "closed"
    assert issue_contribution["closed_at"] is not None
    assert issue_contribution["issue_elapsed_seconds"] is not None
    assert issue_contribution["consumption"] == {
        "model": "claude-opus-4.7-xhigh",
        "tokens_in": 1234,
        "tokens_out": 567,
        "credits": None,
        "premium_requests": None,
        "cache_read": None,
        "cache_write": None,
    }
    assert issue_contribution["peak_context_window"] == iteration_end["summary"][
        "peak_context_window"
    ]
    resolved = next(
        json.loads(raw)
        for raw in log_lines
        if json.loads(raw)["type"] == "wrapper.skill_policy.resolved"
    )
    assert resolved["base_scope"] == "minimal"
    assert resolved["fallback"] == "minimal"
    assert resolved["migration_warning"] is True
    assert sorted(resolved["enabled"]) == sorted(resolved["required"])
    assert set(disabled_skills).isdisjoint(resolved["enabled"])
    assert str(tmp_path) not in json.dumps(resolved)
    warning_lines = [
        line
        for line in capsys.readouterr().err.splitlines()
        if "required-skills" in line
    ]
    assert len(warning_lines) == 1

    # run-summary JSON present with documented schema.
    runs_dir = tmp_path / ".git-loopy" / "runs"
    json_files = list(runs_dir.glob("*.json"))
    assert len(json_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert "run_id" in payload
    assert "started_at" in payload
    assert "iterations" in payload
    assert len(payload["iterations"]) == 1
    iter_row = payload["iterations"][0]
    assert iter_row["iter"] == 1
    assert iter_row["commits"] == 1
    assert iter_row["auto_closures"] == 1
    assert iter_row["model"] == "claude-opus-4.7-xhigh"
    assert iter_row["tokens_in"] == 1234
    assert iter_row["tokens_out"] == 567
    assert iter_row["tool_count"] == 1
    assert iter_row["strikes"] == 0  # progress was made (1 commit + 1 close)
    assert iter_row["outcome"] == iteration_end["outcome"]
    assert iter_row["duration_seconds"] == iteration_end["duration_seconds"]
    assert iter_row["context_used"] == iteration_end["summary"]["observed_tokens"]
    assert iter_row["skill_count"] == iteration_end["summary"]["skill_call_count"]
    assert iter_row["skills_consulted"] == ["tdd"]
    assert iter_row["pr_advances"] == iteration_end["summary"]["pr_advances"]
    assert iter_row["peak_context_window"] == iteration_end["summary"][
        "peak_context_window"
    ]
    assert iter_row["issues"] == iteration_end["issues"]


def test_skill_policy_failure_stops_before_source_collection_or_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text(
        "---\nrequired-skills: []\n---\n",
        encoding="utf-8",
    )
    fake_git = FakeGitClient(tmp_path)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(
                skill_policy=SkillPolicyInputs(
                    project=SkillPolicyInput(
                        present=True,
                        names=("missing-skill",),
                    )
                )
            )
        )
    )

    assert exit_code == 1
    assert fake_client.start_call_count == 1
    assert fake_client.stop_call_count == 1
    assert fake_client.created == []
    assert fake_gh.issue_list_calls == []
    stderr = capsys.readouterr().err
    assert "missing-skill" in stderr
    assert "has no required-skills metadata" not in stderr
    logs = list((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    assert logs == [] or logs[0].read_text(encoding="utf-8") == ""


def test_loop_empty_pool_exits_zero(tmp_path, monkeypatch) -> None:
    """An empty AFK-ready pool short-circuits with exit code 0 — no SDK call."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"), issues=[]
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    # No SDK session created on empty-pool fast path.
    assert len(fake_client.created) == 0, (
        f"expected no SDK session on empty pool; got {len(fake_client.created)}"
    )
    # client.stop() still ran in the loop's finally.
    assert fake_client.stop_call_count == 1


def _read_events(tmp_path: Path) -> list[dict[str, Any]]:
    """Every replay envelope this Run wrote, in order."""
    logs = sorted((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    assert logs, "expected a replay log"
    return [
        json.loads(line)
        for line in logs[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_loop_reports_pool_exclusions_as_events(tmp_path, monkeypatch) -> None:
    """A ``ready-for-agent`` issue the discriminator drops is named, with a reason.

    Issue #303: before this, such an issue never entered the Pool, never
    reached the Dashboard, and produced no diagnostic — a human who had
    deliberately triaged it had no way to learn the runner was ignoring it.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(7, body="## Parent\n- #1\n\n## Acceptance criteria\n- x"),
            _make_issue(8, body="A PRD with neither section."),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    monkeypatch.setattr(
        loop_module, "_make_client", lambda: FakeCopilotClient(scripted_events=[])
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=1))
    )

    assert exit_code == 0
    events = _read_events(tmp_path)
    excluded = [
        e for e in events if e["type"] == events_module.WRAPPER_POOL_EXCLUDED
    ]
    assert [(e["issue"], e["reason"]) for e in excluded] == [
        (7, sources_module.EXCLUSION_MISSING_WHAT_TO_BUILD),
        (8, sources_module.EXCLUSION_MISSING_BOTH_SECTIONS),
    ]
    assert excluded[0]["title"] == "Test issue 7"

    collected = next(
        e for e in events if e["type"] == events_module.WRAPPER_AFK_READY_COLLECTED
    )
    # The count travels with the collection so a replay can tell an empty
    # tracker apart from a tracker whose every candidate was dropped.
    assert collected["issues"] == []
    assert collected["excluded"] == 2

    # Every exclusion is reported before the collection it explains.
    types = [e["type"] for e in events]
    assert types.index(events_module.WRAPPER_POOL_EXCLUDED) < types.index(
        events_module.WRAPPER_AFK_READY_COLLECTED
    )


def test_loop_emits_no_exclusion_events_when_nothing_is_dropped(
    tmp_path, monkeypatch
) -> None:
    """A clean Pool's output is unchanged — this slice only adds visibility."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    monkeypatch.setattr(loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path))
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"), issues=[]
        ),
    )
    monkeypatch.setattr(
        loop_module, "_make_client", lambda: FakeCopilotClient(scripted_events=[])
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    events = _read_events(tmp_path)
    assert not [
        e for e in events if e["type"] == events_module.WRAPPER_POOL_EXCLUDED
    ]
    collected = next(
        e for e in events if e["type"] == events_module.WRAPPER_AFK_READY_COLLECTED
    )
    assert collected["excluded"] == 0


def test_loop_reports_an_all_excluded_pool_distinctly(
    tmp_path, monkeypatch, capsys
) -> None:
    """An all-excluded Pool reads differently from a Pool with no work.

    Same clean exit 0 — the two situations differ in what the operator should
    do next, not in whether the Run failed.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    monkeypatch.setattr(loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path))
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_make_issue(8, body="A PRD with neither section.")],
        ),
    )
    monkeypatch.setattr(
        loop_module, "_make_client", lambda: FakeCopilotClient(scripted_events=[])
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=1))
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "#8" in out
    assert sources_module.EXCLUSION_MISSING_BOTH_SECTIONS.replace("_", " ") in out


def test_loop_reports_prds_pool_exclusions(tmp_path, monkeypatch) -> None:
    """The local-markdown backend shares the discriminator, so it reports too."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    slice_dir = tmp_path / "prds" / "featA"
    slice_dir.mkdir(parents=True)
    (slice_dir / "001-incomplete.md").write_text(
        "## What to build\nthing", encoding="utf-8"
    )

    monkeypatch.setattr(loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path))
    monkeypatch.setattr(
        loop_module, "_make_client", lambda: FakeCopilotClient(scripted_events=[])
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="prds", max_iterations=1))
    )

    assert exit_code == 0
    excluded = [
        e
        for e in _read_events(tmp_path)
        if e["type"] == events_module.WRAPPER_POOL_EXCLUDED
    ]
    assert [(e["issue"], e["reason"]) for e in excluded] == [
        (
            "prds/featA/001-incomplete.md",
            sources_module.EXCLUSION_MISSING_ACCEPTANCE_CRITERIA,
        )
    ]


def _wire_single_issue_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    issue_number: int = 42,
    dirty: bool = False,
    untracked: bool = False,
    commit_error: git_module.GitError | None = None,
    push_error: git_module.GitError | None = None,
) -> tuple[FakeCopilotClient, FakeGitClient]:
    """Minimal github wiring for a one-issue run with no agent commits.

    Sets up the repo on disk, injects a :class:`~tests.fakes.FakeGitHubClient`
    (one AFK-ready issue that is never closed) through the loop's ``gh`` seam,
    and injects a single :class:`~tests.fakes.FakeGitClient` through the loop's
    git seam. By default the worktree is clean and the agent makes no commit, so
    ``head_sha`` is constant across the iteration (``commits_between`` is empty);
    pass ``dirty=True`` / ``untracked=True`` / ``commit_error`` / ``push_error``
    to script the Checkpoint / push path a test wants. Returns
    ``(fake_client, fake_git)`` so the caller can drive the SDK ``on_send`` hook
    and inspect the ``add_all`` / ``commit`` / ``push`` spies.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    issue = _make_issue(issue_number)
    fake_git = FakeGitClient(
        tmp_path,
        dirty=dirty,
        untracked=untracked,
        commit_error=commit_error,
        push_error=push_error,
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[issue],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    return fake_client, fake_git


def test_loop_dirty_worktree_checkpoints_and_continues(
    tmp_path, monkeypatch
) -> None:
    """A dirty worktree no longer aborts: it produces one Checkpoint and runs on.

    The stale_worktree abort (ADR-0004) is gone. At the iteration boundary a
    dirty tree is staged (``add_all``) and captured in a single
    close-keyword-free Checkpoint commit attributed to the Active issue, then
    the run completes normally (exit 0). The Checkpoint emits
    ``wrapper.checkpoint.recorded`` — NOT ``wrapper.commit.recorded`` — so it is
    not counted as agent progress.
    """
    fake_client, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, dirty=True, untracked=False
    )

    cfg = RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    # The dirty tree did NOT abort the run.
    assert exit_code == 0
    assert len(fake_client.created) == 1, "the SDK session still ran"

    # Exactly one Checkpoint: stage everything, then one commit.
    assert fake_git.add_all_calls, (
        "the worktree must be staged before the Checkpoint"
    )
    assert len(fake_git.commit_messages) == 1, (
        f"expected exactly one Checkpoint commit; got {fake_git.commit_messages}"
    )
    msg = fake_git.commit_messages[0]
    assert is_checkpoint_message(msg), "Checkpoint must carry the trailer"
    assert "42" in msg, "Checkpoint is attributed to the Active issue #42"

    # The Checkpoint surfaced as wrapper.checkpoint.recorded, never as a commit.
    logs_dir = tmp_path / ".git-loopy" / "logs"
    log_lines = next(logs_dir.glob("*.jsonl")).read_text(
        encoding="utf-8"
    ).splitlines()
    types_seen = [json.loads(raw)["type"] for raw in log_lines]
    assert "wrapper.checkpoint.recorded" in types_seen
    assert "wrapper.commit.recorded" not in types_seen
    assert "wrapper.stale_worktree.aborted" not in types_seen
    activation = next(
        json.loads(raw)
        for raw in log_lines
        if json.loads(raw)["type"] == "wrapper.issue.activated"
    )
    assert activation["issue"] == 42
    assert activation["binding_source"] == "serial_pickup"

    # The persisted iteration counts no agent commits for the Checkpoint.
    runs_dir = tmp_path / ".git-loopy" / "runs"
    payload = json.loads(next(runs_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["iterations"][0]["commits"] == 0


def test_loop_clean_worktree_makes_no_checkpoint(tmp_path, monkeypatch) -> None:
    """A clean (neither dirty nor untracked) worktree never authors a Checkpoint."""
    _, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, dirty=False, untracked=False
    )

    cfg = RunConfig(issue_source="github", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    assert fake_git.commit_messages == [], (
        "a clean worktree must not be checkpointed"
    )
    logs_dir = tmp_path / ".git-loopy" / "logs"
    log_lines = next(logs_dir.glob("*.jsonl")).read_text(
        encoding="utf-8"
    ).splitlines()
    types_seen = [json.loads(raw)["type"] for raw in log_lines]
    assert "wrapper.checkpoint.recorded" not in types_seen


def test_checkpoint_is_excluded_from_strikes_abort_after_n_still_fires(
    tmp_path, monkeypatch
) -> None:
    """Checkpoints never reset strikes: a stuck agent still aborts after N.

    Every iteration the agent makes no commit (no progress) but leaves a dirty
    tree, so the runner Checkpoints each time. Because Checkpoints are excluded
    from Strike progress, the no-progress strikes still accumulate and the
    abort-after-N protection fires (exit 1) — the durability net did not mask a
    genuinely stuck agent.
    """
    fake_client, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, dirty=True, untracked=False
    )

    cfg = RunConfig(
        issue_source="github", max_iterations=10, max_nmt_strikes=2
    )
    exit_code = asyncio.run(loop_module.run(cfg))

    # Abort-after-N still fires despite every iteration being Checkpointed.
    assert exit_code == 1
    # Two iterations to reach the 2-strike threshold, one Checkpoint each.
    assert len(fake_git.commit_messages) == 2, (
        f"expected one Checkpoint per stuck iteration; "
        f"got {fake_git.commit_messages}"
    )
    assert len(fake_client.created) == 2


def test_checkpoint_failure_is_non_fatal(tmp_path, monkeypatch) -> None:
    """A Checkpoint commit failure warns but never aborts the run.

    A local-only repo (no remote) or a transient git error during the
    Checkpoint must not take down the loop — the iteration completes and the
    run exits normally.
    """
    _wire_single_issue_github(
        tmp_path,
        monkeypatch,
        dirty=True,
        untracked=False,
        commit_error=git_module.GitError(
            ["git", "commit"], 1, "nothing to commit"
        ),
    )

    cfg = RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    # The failed Checkpoint did not abort the run.
    assert exit_code == 0
    logs_dir = tmp_path / ".git-loopy" / "logs"
    log_lines = next(logs_dir.glob("*.jsonl")).read_text(
        encoding="utf-8"
    ).splitlines()
    types_seen = [json.loads(raw)["type"] for raw in log_lines]
    # No checkpoint event was emitted (the commit failed before emit).
    assert "wrapper.checkpoint.recorded" not in types_seen
    # And crucially the run reached its clean end.
    assert "wrapper.run.end" in types_seen


# ---------------------------------------------------------------------------
# Auto-push (issue #35 — ADR-0004 durability net, second half)
# ---------------------------------------------------------------------------


def test_loop_pushes_after_agent_commit(tmp_path, monkeypatch) -> None:
    """A new agent commit triggers the auto-push; ``wrapper.push.recorded`` is logged.

    The clean-tree, one-agent-commit case: no Checkpoint is made, but the
    iteration still produced a new commit, so the current branch is pushed to
    its upstream after accounting.
    """
    fake_client, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, dirty=False, untracked=False
    )
    # One agent commit, no close keyword -> pure progress, no auto-closure.
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        sha="a" * 40, subject="feat: real work", body="Refs #42"
    )

    cfg = RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    assert fake_git.push_calls == 1, (
        "a new agent commit must trigger exactly one push"
    )
    types_seen = _logged_types(tmp_path)
    assert "wrapper.commit.recorded" in types_seen
    assert "wrapper.push.recorded" in types_seen


def test_loop_pushes_after_checkpoint(tmp_path, monkeypatch) -> None:
    """A Checkpoint (no agent commit) still triggers the auto-push.

    The dirty-tree, zero-agent-commit case: the only new commit this iteration
    is the runner Checkpoint, which is enough to push the branch to the remote.
    """
    _, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, dirty=True, untracked=False
    )

    cfg = RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    assert fake_git.push_calls == 1, "the Checkpoint must trigger exactly one push"
    types_seen = _logged_types(tmp_path)
    assert "wrapper.checkpoint.recorded" in types_seen
    assert "wrapper.push.recorded" in types_seen


def test_loop_no_push_when_clean_and_no_new_commits(tmp_path, monkeypatch) -> None:
    """A clean tree with no agent commit and no Checkpoint never pushes."""
    _, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, dirty=False, untracked=False
    )

    cfg = RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    assert fake_git.push_calls == 0, "nothing new to push -> no push attempt"
    assert "wrapper.push.recorded" not in _logged_types(tmp_path)


def test_loop_push_failure_is_non_fatal(tmp_path, monkeypatch) -> None:
    """A push failure (no remote / auth / non-fast-forward) warns but never aborts.

    A local-only repo (no upstream) must keep working: the push raises
    :exc:`git.GitError`, the loop swallows it with a warning, the run exits 0,
    and — mirroring the failed-Checkpoint path — no ``wrapper.push.recorded``
    event is emitted (the push never landed).
    """
    _wire_single_issue_github(
        tmp_path,
        monkeypatch,
        dirty=True,
        untracked=False,
        push_error=git_module.GitError(
            ["git", "push"], 128, "no upstream configured"
        ),
    )

    cfg = RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    # The failed push did not abort the run.
    assert exit_code == 0
    types_seen = _logged_types(tmp_path)
    # The Checkpoint landed, but the push failed -> no push event, clean end.
    assert "wrapper.checkpoint.recorded" in types_seen
    assert "wrapper.push.recorded" not in types_seen
    assert "wrapper.run.end" in types_seen


def test_loop_prds_end_to_end_one_iteration(tmp_path, monkeypatch) -> None:
    """One PRDs iteration end-to-end: discovery → SDK → commit → no auto-close.

    Drives the local-markdown collector against a fixture tree:

    * ``prds/featA/001-ready.md`` — AFK-ready discriminator present.
    * ``prds/featA/002-not-ready.md`` — missing discriminator (filter).
    * ``prds/featA/done/000-archived.md`` — under ``done/`` (filter).
    * ``prds/featA/prd.md`` — no NNN prefix (filter).

    Asserts:

    * Exit code 0.
    * SDK saw a prompt containing the ready file's path + body but NOT
      the filtered files.
    * One commit recorded, ``auto_closures == 0`` (PRDs is detection-only).
    * Run-summary JSON shape matches the github variant.
    * ``gh.issue_close`` was never called (gh isn't touched in PRDs mode).
    * The worktree (``prds/`` tree) is unchanged after the run —
      detection-only completion semantics.
    """
    # -- 1) Fake repo on disk with a PRDs fixture tree --------------------
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    ready_md = tmp_path / "prds" / "featA" / "001-ready.md"
    not_ready_md = tmp_path / "prds" / "featA" / "002-not-ready.md"
    archived_md = tmp_path / "prds" / "featA" / "done" / "000-archived.md"
    prd_md = tmp_path / "prds" / "featA" / "prd.md"
    for p in (ready_md, not_ready_md, archived_md, prd_md):
        p.parent.mkdir(parents=True, exist_ok=True)

    afk_body = (
        "# 001 — Ready\n\n## Parent\nfeatA\n\n## What to build\nthing\n\n"
        "## Acceptance criteria\n- impl\n"
    )
    ready_md.write_text(afk_body, encoding="utf-8")
    not_ready_md.write_text("Just words, no sections.\n", encoding="utf-8")
    archived_md.write_text(afk_body, encoding="utf-8")
    prd_md.write_text(afk_body, encoding="utf-8")

    # Snapshot the prds tree BEFORE the run for the post-run no-mutation
    # assertion (detection-only semantics).
    files_before = {
        p.relative_to(tmp_path).as_posix()
        for p in (tmp_path / "prds").rglob("*")
        if p.is_file()
    }

    # -- 2) git seam: FakeGitClient (agent commit appended mid-session) ---
    fake_git = FakeGitClient(
        tmp_path,
        commits=[
            git_module.Commit(
                sha="0" * 40, subject="prior", body="", date="2026-05-16"
            )
        ],
        dirty=False,
        untracked=False,
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    # -- 3) gh MUST NOT be reached in PRDs mode ---------------------------
    # The loop only touches GitHub through the client from
    # ``_make_github_client``; PRDs mode uses PrdsIssueSource and must never
    # construct one. Record any attempt to build the client so a regression
    # that reaches for gh in PRDs mode fails loudly.
    gh_calls: list[str] = []

    def _forbidden_github_client() -> gh_module.GitHubClient:
        gh_calls.append("_make_github_client")
        raise AssertionError("gh must not be constructed in PRDs mode")

    monkeypatch.setattr(loop_module, "_make_github_client", _forbidden_github_client)

    # -- 4) SDK stub: minimal scripted flow --------------------------------
    scripted = [
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(content="working on it", message_id="m1"),
        ),
        _sdk_event(
            SessionEventType.ASSISTANT_USAGE,
            AssistantUsageData(
                input_tokens=100,
                output_tokens=50,
                model="claude-opus-4.7-xhigh",
            ),
        ),
    ]
    fake_client = FakeCopilotClient(scripted_events=scripted)
    # The agent authors one commit mid-session (PRDs mode never auto-closes:
    # PrdsIssueSource.handle_completions returns [] — the agent owns the git mv).
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        sha="a" * 40,
        subject="feat(featA/001): implement",
        body="Refs prds/featA/001-ready.md",
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    # -- 5) Run loop with issue_source=prds --------------------------------
    cfg = RunConfig(
        model="claude-opus-4.7-xhigh",
        issue_source="prds",
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )
    exit_code = asyncio.run(loop_module.run(cfg))

    # -- 6) Assertions -----------------------------------------------------
    assert exit_code == 0, f"expected exit 0; got {exit_code}"
    assert gh_calls == [], (
        f"PRDs mode must not touch gh; got calls: {gh_calls}"
    )

    # SDK lifecycle: one session, one prompt, no further calls.
    assert len(fake_client.created) == 1, (
        "expected exactly one SDK session in PRDs mode"
    )
    sdk_session = fake_client.created[0]
    assert len(sdk_session.send_and_wait_calls) == 1
    prompt, _timeout = sdk_session.send_and_wait_calls[0]

    # The ready file must be in the prompt; the filtered files must NOT.
    assert "prds/featA/001-ready.md" in prompt, (
        "AFK-ready PRDs file should appear in the prompt"
    )
    assert "002-not-ready" not in prompt, (
        "non-AFK PRDs file should be filtered out"
    )
    assert "000-archived" not in prompt, (
        "done/* PRDs files should be filtered out"
    )
    assert "prd.md" not in prompt or "001-ready" in prompt, (
        "loose prd.md should be filtered out by NNN-prefix discriminator"
    )

    # Worktree untouched (detection-only semantics).
    files_after = {
        p.relative_to(tmp_path).as_posix()
        for p in (tmp_path / "prds").rglob("*")
        if p.is_file()
    }
    assert files_before == files_after, (
        "PRDs handle_completions must not move/delete files; "
        f"before={files_before} after={files_after}"
    )

    # JSONL log contains the expected wrapper events but NO auto_close.
    logs_dir = tmp_path / ".git-loopy" / "logs"
    log_files = list(logs_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    types_seen = [
        json.loads(line)["type"]
        for line in log_files[0].read_text(encoding="utf-8").splitlines()
    ]
    for expected in (
        "wrapper.run.start",
        "wrapper.iteration.start",
        "wrapper.afk_ready.collected",
        "wrapper.commit.recorded",
        "wrapper.iteration.end",
        "wrapper.run.end",
    ):
        assert expected in types_seen, (
            f"expected {expected} in JSONL; saw: {types_seen}"
        )
    assert "wrapper.auto_close" not in types_seen, (
        "PRDs mode must not emit wrapper.auto_close — handle_completions returns []"
    )

    # Run-summary JSON.
    json_files = list((tmp_path / ".git-loopy" / "runs").glob("*.json"))
    assert len(json_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert len(payload["iterations"]) == 1
    iter_row = payload["iterations"][0]
    assert iter_row["iter"] == 1
    assert iter_row["commits"] == 1
    assert iter_row["auto_closures"] == 0, (
        "PRDs mode must report zero auto-closures"
    )


def test_loop_prds_empty_pool_exits_zero(tmp_path, monkeypatch) -> None:
    """An absent ``prds/`` directory short-circuits with exit 0 — no SDK call."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    # NB: no `prds/` directory created.

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    # PRDs mode must not construct a GitHubClient.
    def _forbidden_github_client() -> Any:
        raise AssertionError("gh must not be constructed in PRDs mode")

    monkeypatch.setattr(loop_module, "_make_github_client", _forbidden_github_client)

    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="prds", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    # No SDK session created on empty-pool fast path.
    assert len(fake_client.created) == 0
    assert fake_client.stop_call_count == 1


def test_loop_preflight_failure_when_gh_not_authed(tmp_path, monkeypatch) -> None:
    """If ``gh auth status`` is not authenticated, the loop aborts with exit 1."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module, "_make_github_client", lambda: FakeGitHubClient(authed=False)
    )

    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 1
    assert len(fake_client.created) == 0


def test_loop_aborts_after_max_nmt_strikes(tmp_path, monkeypatch) -> None:
    """Three consecutive no-progress iterations abort the loop with exit 1.

    The SDK is mocked to produce no commits and no auto-closures, so
    every iteration is a strike. With ``max_nmt_strikes=3`` the loop
    aborts on iteration 3.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(
        issue_source="github", max_iterations=0, max_nmt_strikes=3
    )
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 1, "loop must abort after max strikes"
    # 3 sessions = 3 iterations until strike machine fires.
    assert len(fake_client.created) == 3, (
        f"expected 3 SDK sessions before abort; got {len(fake_client.created)}"
    )


# ---------------------------------------------------------------------------
# Additional rubber-duck-recommended coverage
# ---------------------------------------------------------------------------


def test_loop_send_and_wait_exception_is_no_progress(tmp_path, monkeypatch) -> None:
    """If ``send_and_wait`` raises, the iteration is treated as no-progress.

    The post-iteration accounting (commits_between, auto-close backstop,
    strike tick, iteration.end emit, counters persist) still runs — the
    SDK failure is contained to "no progress" semantics.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    class RaisingSession(FakeCopilotSession):
        async def send_and_wait(self, prompt: str, *, timeout: float = 60.0, **_: Any) -> SessionEvent | None:
            raise RuntimeError("simulated SDK exception")

    class RaisingClient(FakeCopilotClient):
        async def create_session(self, **kwargs: Any) -> FakeCopilotSession:
            session = RaisingSession(on_event=None, scripted_events=[])
            self.created.append(session)
            return session

    fake_client = RaisingClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    # No progress + 1 iteration cap = clean exit 0 (we didn't hit the
    # strike threshold).
    assert exit_code == 0
    # Post-iteration accounting still ran -> JSONL still includes
    # iteration.start, iteration.end, strike, run.end.
    log_files = list((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    assert len(log_files) == 1
    types_seen = {
        json.loads(line)["type"]
        for line in log_files[0].read_text(encoding="utf-8").splitlines()
    }
    assert "wrapper.iteration.end" in types_seen
    assert "wrapper.strike" in types_seen
    assert "wrapper.run.end" in types_seen


def test_loop_auto_close_failure_does_not_abort_iteration(tmp_path, monkeypatch) -> None:
    """A failing ``gh issue close`` is logged and the iteration continues.

    Verifies the per-issue try/except inside ``_try_auto_close``: one
    failing close must not prevent commits from being recorded or the
    strike machine from running.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    # The auto-close attempt fails for #42, but the iteration must not abort.
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
        issue_close_errors={
            42: gh_module.GhError(
                ["gh", "issue", "close", "42"], 1, "simulated close failure"
            )
        },
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = FakeCopilotClient(scripted_events=[])
    # The agent authors a commit referencing ``Closes #42`` mid-session; the
    # subsequent auto-close attempt fails (issue_close_errors) but must not abort.
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        sha="deadbeef", subject="x", body="Closes #42"
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0  # one iteration cap, no abort
    # JSONL should still contain commit.recorded for the one commit, but
    # NO auto_close events.
    log_files = list((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    events_seen = [
        json.loads(line)
        for line in log_files[0].read_text(encoding="utf-8").splitlines()
    ]
    types_seen = [event["type"] for event in events_seen]
    assert "wrapper.commit.recorded" in types_seen
    assert "wrapper.auto_close" not in types_seen
    activation = next(
        event
        for event in events_seen
        if event["type"] == "wrapper.issue.activated"
    )
    assert activation["issue"] == 42
    assert activation["binding_source"] == "serial_pickup"


def test_loop_make_client_failure_returns_exit_one(tmp_path, monkeypatch) -> None:
    """If ``_make_client()`` raises, ``run()`` returns 1 with no traceback escape."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    monkeypatch.setattr(
        loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path)
    )
    monkeypatch.setattr(
        loop_module, "_make_github_client", lambda: FakeGitHubClient()
    )

    def _exploding_factory() -> Any:
        raise RuntimeError("simulated CopilotClient construction failure")

    monkeypatch.setattr(loop_module, "_make_client", _exploding_factory)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 1


def test_loop_multiple_iterations_until_cap(tmp_path, monkeypatch) -> None:
    """Loop runs ``max_iterations`` iterations and exits 0 at the cap.

    Each iteration is mocked to produce one commit (progress -> no
    strikes), so the cap is the only stopping condition.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(99)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    fake_client = FakeCopilotClient(scripted_events=[])
    # Each iteration the agent lands one fresh commit (progress -> no strikes),
    # so head advances every session and the only stop condition is the cap.
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        subject="progress"
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=3, max_nmt_strikes=3)
    exit_code = asyncio.run(loop_module.run(cfg))

    assert exit_code == 0
    assert len(fake_client.created) == 3, (
        f"expected exactly 3 SDK sessions; got {len(fake_client.created)}"
    )
    # Run-summary JSON should carry 3 iteration rows.
    json_files = list((tmp_path / ".git-loopy" / "runs").glob("*.json"))
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert len(payload["iterations"]) == 3


# ---------------------------------------------------------------------------
# OpenTelemetry span tree (issue #12)
# ---------------------------------------------------------------------------


def test_loop_emits_otel_span_tree_when_enabled(tmp_path, monkeypatch) -> None:
    """OTel-on: one iteration emits the documented span tree.

    Expected shape::

        git_loopy.run
        └─ git_loopy.iteration  (attrs: iter, issue, issues)
           ├─ git_loopy.collect_issues
           ├─ git_loopy.session
           └─ git_loopy.enforce_closures

    Skips if the ``[otel]`` extra is not installed so the suite stays
    green on the base install.
    """
    # -- 0) Install OTel in-memory exporter BEFORE the loop opens any
    #       spans. The seam reuses an externally-installed
    #       TracerProvider on first init (see telemetry.otel docstring).
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
        from opentelemetry.util._once import Once
    except ImportError:  # pragma: no cover
        pytest.skip("opentelemetry not installed (run with --extra otel)")

    from git_loopy.telemetry import otel as telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Bypass `set_tracer_provider`'s set-once guard (same pattern as
    # the in_memory_exporter fixture in test_telemetry_otel.py).
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider, raising=False)
    monkeypatch.setattr(
        trace, "_TRACER_PROVIDER_SET_ONCE", Once(), raising=False
    )

    # Wipe sticky-enable cache + flip GIT_LOOPY_OTEL_ENABLED so the seam
    # picks up the externally-installed provider on its first init().
    telemetry.reset_for_tests()
    monkeypatch.setenv("GIT_LOOPY_OTEL_ENABLED", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    # -- 1) Fake repo on disk ---------------------------------------------
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text(
        "You are the agent.\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    # -- 2) git stubs ------------------------------------------------------
    # -- 2) git seam: FakeGitClient (agent commit appended mid-session) ---
    fake_git = FakeGitClient(
        tmp_path,
        commits=[
            git_module.Commit(
                sha="0" * 40, subject="prior commit", body="", date="2026-05-16"
            )
        ],
        dirty=False,
        untracked=False,
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    # -- 3) gh seam: FakeGitHubClient (issue_close flips #42 to CLOSED) ----
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    # -- 4) SDK stub (minimal: empty event flow) ---------------------------
    fake_client = FakeCopilotClient(scripted_events=[])
    # The agent authors its ``Closes #42`` commit mid-session so the closure
    # (and its git_loopy.enforce_closures span) fires as before.
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        sha="abcdef1234567890abcdef1234567890abcdef12",
        subject="feat: stuff",
        body="Closes #42",
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    # -- 5) Run loop with max_iterations=1 ---------------------------------
    cfg = RunConfig(
        issue_source="github",
        max_iterations=1,
        max_nmt_strikes=3,
        otel_enabled=True,
    )

    exit_code = asyncio.run(loop_module.run(cfg))
    assert exit_code == 0, f"expected exit 0, got {exit_code}"

    # -- 6) Drain & inspect spans -----------------------------------------
    telemetry.force_flush()
    spans = exporter.get_finished_spans()

    by_name: dict[str, list[Any]] = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)

    # Expected five spans, one of each documented name.
    expected_names = {
        "git_loopy.run",
        "git_loopy.iteration",
        "git_loopy.collect_issues",
        "git_loopy.session",
        "git_loopy.enforce_closures",
    }
    seen_names = set(by_name)
    assert expected_names <= seen_names, (
        f"missing expected spans; "
        f"expected {expected_names}, got {seen_names}"
    )

    # Exactly one of each.
    for name in expected_names:
        assert len(by_name[name]) == 1, (
            f"expected exactly one {name!r} span; got {len(by_name[name])}"
        )

    run_span = by_name["git_loopy.run"][0]
    iter_span = by_name["git_loopy.iteration"][0]
    collect_span = by_name["git_loopy.collect_issues"][0]
    session_span = by_name["git_loopy.session"][0]
    closures_span = by_name["git_loopy.enforce_closures"][0]

    # Parent relationships: run → iteration → {collect, session, closures}.
    assert run_span.parent is None, "git_loopy.run is the root span"
    assert iter_span.parent is not None
    assert iter_span.parent.span_id == run_span.context.span_id, (
        "git_loopy.iteration must nest under git_loopy.run"
    )
    for child in (collect_span, session_span, closures_span):
        assert child.parent is not None
        assert child.parent.span_id == iter_span.context.span_id, (
            f"{child.name!r} must nest under git_loopy.iteration; "
            f"saw parent span_id {child.parent.span_id!r}"
        )

    # Iteration attrs: iter + issue + issues set after pool collect.
    attrs = dict(iter_span.attributes or {})
    assert attrs.get("iter") == 1, f"iter attr: {attrs!r}"
    assert attrs.get("issue") == 42, f"issue attr: {attrs!r}"
    issues_attr = attrs.get("issues")
    assert issues_attr is not None, f"issues attr missing: {attrs!r}"
    # `issues` is stored as a tuple/list of ints — OTel normalises to
    # a sequence.
    assert list(issues_attr) == [42], f"issues attr: {issues_attr!r}"


# ---------------------------------------------------------------------------
# PR-advance integration test (include_prs=True)
# ---------------------------------------------------------------------------


def _make_pr_view(
    number: int,
    *,
    head_sha: str,
    state: str = "OPEN",
    head_branch: str = "feature/pr-work",
    comments: tuple[gh_module.Comment, ...] = (),
) -> gh_module.PullRequest:
    return gh_module.PullRequest(
        number=number,
        title=f"Test PR {number}",
        body="",
        labels=["ready-for-agent"],
        state=state,
        url=f"https://github.com/x/y/pull/{number}",
        head_sha=head_sha,
        head_branch=head_branch,
        comments=comments,
    )


def test_loop_pr_advance_emits_pr_advanced_event(tmp_path, monkeypatch) -> None:
    """With include_prs=True, a PR whose head SHA advances emits wrapper.pr.advanced.

    No base-branch commit lands (PR work happens on the PR branch), so the
    only progress signal is the head-SHA advance. Asserts:

    * exit 0,
    * the PR block reaches the prompt,
    * ``wrapper.pr.advanced`` is logged and ``wrapper.auto_close`` is not,
    * the iteration row counts the PR advance separately from auto-closures,
      with 0 commits and 0 strikes,
    * the base branch is never switched (HEAD already on base).
    """
    # -- repo on disk -----------------------------------------------------
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text(
        "You are the agent. Advance the AFK-ready PRs.\n", encoding="utf-8"
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    # -- git seam: clean tree on the base branch, no base-branch commit ---
    # (PR work happens on the PR branch; the only progress signal is the PR
    # head-SHA advance.) HEAD stays on the base branch, so no switch/restore.
    fake_git = FakeGitClient(tmp_path, dirty=False, untracked=False, branch="main")
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)

    # -- gh seam: FakeGitHubClient (PR head advances mid-session) ---------
    brief = gh_module.Comment(
        author="triage-bot",
        body="## Agent Brief\nFinish the caching change.",
        created_at="2026-05-16T00:00:00Z",
    )
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[],
        prs=[_make_pr_view(7, head_sha="prsha-old", comments=(brief,))],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    # -- SDK stub ---------------------------------------------------------
    scripted = [
        _sdk_event(
            SessionEventType.TOOL_EXECUTION_START,
            ToolExecutionStartData(
                tool_call_id="call-1",
                tool_name="edit",
                arguments={"path": "cache.py"},
            ),
        ),
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(
                content="<working issue=7>\nAdvancing PR #7.",
                message_id="m1",
            ),
        ),
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(
                content="<working issue=8>\nThis must not rebind.",
                message_id="m2",
            ),
        ),
        _sdk_event(
            SessionEventType.ASSISTANT_USAGE,
            AssistantUsageData(
                input_tokens=100, output_tokens=50, model="claude-opus-4.7-xhigh"
            ),
        ),
    ]
    fake_client = FakeCopilotClient(scripted_events=scripted)
    # The agent pushes to the PR branch mid-session: the head advances between
    # the collection-time pr_view (baseline "prsha-old") and the post-iteration
    # advance-check pr_view, so _detect_pr_advances records the advance.
    fake_client.on_send = lambda: fake_gh.set_pr_head(7, "prsha-new")
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    # -- run --------------------------------------------------------------
    cfg = RunConfig(
        model="claude-opus-4.7-xhigh",
        issue_source="github",
        include_prs=True,
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
    )
    exit_code = asyncio.run(loop_module.run(cfg))

    # -- assertions -------------------------------------------------------
    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert fake_git.switch_calls == [], (
        "base branch must not be switched when HEAD is on base"
    )

    # PR block reached the prompt.
    sdk_session = fake_client.created[0]
    prompt, _timeout = sdk_session.send_and_wait_calls[0]
    assert "PR #7" in prompt
    assert "(branch: feature/pr-work)" in prompt

    # Event log: pr.advanced present, auto_close absent.
    jsonl_files = list((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    assert len(jsonl_files) == 1
    types_seen: list[str] = []
    pr_advanced_payloads: list[dict[str, Any]] = []
    activations: list[dict[str, Any]] = []
    for raw in jsonl_files[0].read_text(encoding="utf-8").splitlines():
        evt = json.loads(raw)
        types_seen.append(evt["type"])
        if evt["type"] == "wrapper.pr.advanced":
            pr_advanced_payloads.append(evt)
        if evt["type"] == "wrapper.issue.activated":
            activations.append(evt)
    assert "wrapper.pr.advanced" in types_seen, f"saw: {types_seen}"
    assert "wrapper.auto_close" not in types_seen, f"saw: {types_seen}"
    assert "wrapper.commit.recorded" not in types_seen, (
        "no base-branch commit landed this iteration"
    )
    assert pr_advanced_payloads[0].get("pr") == 7
    assert len(activations) == 1
    assert activations[0]["issue"] == 7
    assert activations[0]["binding_source"] == "serial_pickup"
    assert activations[0]["activated_at"] == activations[0]["ts"]
    # §8: the Pickup is in the stream *before* the session it bound for. The
    # marker that used to establish this binding now only disagrees with it.
    assert types_seen.index("wrapper.issue.activated") < types_seen.index(
        "assistant.message"
    ), types_seen
    diagnostics = next(
        (tmp_path / ".git-loopy" / "logs").glob("*.log")
    ).read_text(encoding="utf-8")
    assert (
        "Working marker disagreement: the agent named #8 but this "
        "Iteration is bound to #7"
    ) in diagnostics

    # Run-summary: the advance is progress, but not an issue auto-closure.
    json_files = list((tmp_path / ".git-loopy" / "runs").glob("*.json"))
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    iter_row = payload["iterations"][0]
    assert iter_row["commits"] == 0
    assert iter_row["auto_closures"] == 0
    assert iter_row["pr_advances"] == 1
    assert iter_row["strikes"] == 0


# ---------------------------------------------------------------------------
# Loop event fan-out through the shared EventEmitter (issue #45)
# ---------------------------------------------------------------------------


class _NoopSource:
    """Inert :class:`~git_loopy.sources.IssueSource` stand-in.

    ``_Loop.__init__`` merely stores the source; ``_emit`` never reaches it, so
    a no-op is enough to construct a ``_Loop`` in isolation for a focused
    fan-out test.
    """

    def preflight(self) -> int | None:
        return None

    def collect_pool(self) -> Any:
        from git_loopy.sources import PoolCollection

        return PoolCollection()

    def handle_completions(
        self, *, pool: list[Any], new_commits: list[Any]
    ) -> list[Any]:
        return []

    def comment(self, ref: int | str, body: str) -> None:
        return None


class _RecordingSink:
    """Records each envelope handed to ``render`` (the sink contract surface)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def render(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def stream_reasoning(self, delta: str) -> None:  # pragma: no cover
        pass

    def stream_message(self, delta: str) -> None:  # pragma: no cover
        pass


def _make_loop(
    repo_root: Path, sinks: SinkFanout
) -> tuple[loop_module._Loop, WritersBundle]:
    """Construct a real ``_Loop`` wired to ``sinks``.

    Only the collaborators ``_emit`` reaches — ``writers`` (its ``run_id`` /
    ``event_log``), ``sinks``, and ``diag`` — are meaningful here; the rest are
    inert stand-ins the constructor merely stores.
    """
    writers = create_writers(repo_root)
    denomination = BilledCreditsDenomination()
    loop = loop_module._Loop(
        config=RunConfig(),
        release_version=EXPECTED_RELEASE_VERSION,
        git=FakeGitClient(repo_root),
        prompt_text="",
        denomination=denomination,
        writers=writers,
        sinks=sinks,
        summary=RunSummary(denomination=denomination),
        client=cast(CopilotClient, None),
        skill_preflight=cast(
            Any,
            SimpleNamespace(
                exposure=None,
                migration_warning=False,
                event_payload={},
            ),
        ),
        source=_NoopSource(),
        diag=writers.diagnostics,
    )
    return loop, writers


def test_loop_emit_fans_scrubbed_envelope_to_sink_via_emitter(tmp_path) -> None:
    """``_Loop._emit`` fans the *scrubbed* envelope out to the sinks (issue #45).

    #45 shrinks ``_emit`` to ``self._emitter.emit(...)`` with the emitter built
    in ``__init__`` (``diag=self._diag``). This pins two things the pre-#45
    inline ``_emit`` violated:

    * the loop composes its fan-out on a shared :class:`EventEmitter` — the
      ``_emitter`` assertion fails against the pre-#45 ``_Loop`` (which had no
      emitter);
    * the sink contract that ``render`` only ever sees an *already-scrubbed*
      envelope — a secret on a wrapper event reaches the sink **redacted**,
      closing the loop's scrub gap (the pre-#45 ``_emit`` fanned the *unscrubbed*
      envelope out to the sinks). ``emit`` still returns the *pre-scrub* envelope
      the loop reads its SHA / subject off, and the JSONL writer + sink agree on
      the same scrubbed bytes.
    """
    secret = "ghp_" + "A" * 36
    sink = _RecordingSink()
    loop, writers = _make_loop(tmp_path, SinkFanout([sink]))

    # The loop composes its fan-out on the shared EventEmitter (diag=self._diag).
    assert isinstance(loop._emitter, EventEmitter)

    with writers.event_log:
        returned = loop._emit(
            "wrapper.commit.recorded", iter_num=1, subject=f"landed {secret}"
        )

    # The sink saw the *scrubbed* envelope — the loop's scrub gap is closed.
    assert sink.events, "sink never received the emitted envelope"
    received = sink.events[0]
    assert secret not in json.dumps(received)
    assert REDACTED_SECRET in received["subject"]
    # ``emit`` returns the pre-scrub envelope the loop inspects (SHA / subject).
    assert returned["subject"] == f"landed {secret}"
    assert returned is not received
    # Writer and sink agree — both got the same scrubbed bytes.
    log_lines = [
        json.loads(ln)
        for ln in writers.event_log.path.read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    assert log_lines[-1] == received


# ---------------------------------------------------------------------------
# Persisted closed-world Skill policy, end to end (issue #227)
# ---------------------------------------------------------------------------


class _SkillExposureRecordingClient(FakeCopilotClient):
    """A client that observes the Run-scoped Skill exposure while it is live.

    The exposure is materialized into a Run-scoped temporary workspace that is
    torn down before :func:`git_loopy.loop.run` returns, so a test that only
    inspects ``create_calls`` afterwards can prove nothing about what the SDK
    was actually handed. This records the live directory and the permission
    handler at session creation instead.
    """

    def __init__(self, scripted_events: list[SessionEvent]) -> None:
        super().__init__(scripted_events)
        self.exposure_dirs: list[Path] = []
        self.exposed_names: list[list[str]] = []
        self.permission_handlers: list[Any] = []

    async def create_session(self, **kwargs: Any) -> FakeCopilotSession:
        directory = Path(kwargs["skill_directories"][0])
        self.exposure_dirs.append(directory)
        self.exposed_names.append(sorted(path.name for path in directory.iterdir()))
        self.permission_handlers.append(kwargs["on_permission_request"])
        return await super().create_session(**kwargs)


def _write_project_skill(skills_root: Path, name: str) -> Path:
    """Write one Skill document into a catalog root and return its directory."""
    skill = skills_root / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n# {name}\n",
        encoding="utf-8",
    )
    return skill


def _persisted_policy_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Lay out a repo whose Config enables one of two installed Skills.

    Both live in the installed catalog, because that is the only Skill root a
    Run reads (ADR-0025). What is under test is the persisted ``enabled_skills``
    key, not where a Skill came from.
    """
    skills_root = skill_install.installed_catalog_dir(os.environ)
    enabled = _write_project_skill(skills_root, "team-review")
    withheld = _write_project_skill(skills_root, "team-deploy")
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text(
        "---\nrequired-skills:\n  - tdd\n---\nYou are the agent.\n",
        encoding="utf-8",
    )
    (tmp_path / "git-loopy" / "config.toml").write_text(
        'model = "claude-opus-4.7-xhigh"\n'
        'issue_source = "github"\n'
        'enabled_skills = ["team-review", "tdd"]\n',
        encoding="utf-8",
    )
    return enabled, withheld


def _wire_persisted_policy_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tracked: tuple[Path, ...],
) -> _SkillExposureRecordingClient:
    """Wire the git / gh / SDK seams for a Run driven from a persisted Config."""
    fake_git = FakeGitClient(
        tmp_path,
        commits=[
            git_module.Commit(
                sha="0000000000000000000000000000000000000001",
                subject="prior commit",
                body="",
                date="2026-05-16",
            )
        ],
        tracked_paths=tracked,
    )
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_make_issue(42)],
        ),
    )
    fake_client = _SkillExposureRecordingClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    return fake_client


def _run_from_persisted_config(tmp_path: Path) -> tuple[int, RunConfig]:
    """Resolve the Config exactly as ``cli.main`` does, then drive one Run."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg-empty")}
    tables = settings.load_configs(tmp_path, env)
    resolved = cli.resolve_config(
        cli.build_parser().parse_args(["1"]),
        env,
        project=tables.project,
        global_=tables.global_,
    )
    return asyncio.run(loop_module.run(resolved.run)), resolved.run


def _skill_permission(skill: str) -> PermissionRequestCustomTool:
    return PermissionRequestCustomTool(
        tool_description="invoke a Skill",
        tool_name=SKILL_TOOL_NAME,
        args={"skill": skill},
        tool_call_id="call-skill",
    )


def test_persisted_skill_policy_reaches_exposure_and_permission_enforcement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_skill_catalog: object,
) -> None:
    """An ``enabled_skills`` key on disk is the Run's whole capability boundary.

    Issue #227's AC8: the *persisted* policy — not a hand-built
    :class:`SkillPolicyInputs` — must flow through the real Config resolver into
    Skill preflight, from there into the SDK session's Skill exposure and its
    permission enforcement, and stay auditable in the replay log without
    disturbing the preflight order. ``team-deploy`` is a tracked project winner
    the catalog *offers* and the Config withholds, so this fails if any hop
    widens the boundary back to the open world.
    """
    enabled, withheld = _persisted_policy_repo(tmp_path)
    fake_client = _wire_persisted_policy_run(
        tmp_path, monkeypatch, tracked=(enabled, withheld)
    )

    exit_code, config = _run_from_persisted_config(tmp_path)

    assert exit_code == 0
    # The policy under test came out of the resolver, not out of the test.
    assert config.skill_policy.project.present is True
    assert config.skill_policy.project.names == ("tdd", "team-review")

    # One session, exposed exactly the enabled winners.
    assert len(fake_client.create_calls) == 1
    assert len(fake_client.create_calls[0]["skill_directories"]) == 1
    assert fake_client.exposed_names == [["tdd", "team-review"]]
    assert "team-deploy" in fake_client.create_calls[0]["disabled_skills"]

    # The same frozen policy denies the withheld winner at the permission seam.
    handler = fake_client.permission_handlers[0]
    assert handler(_skill_permission("team-deploy"), {}).kind == "reject"
    assert handler(_skill_permission("team-review"), {}).kind != "reject"

    # Auditable, path-free, and still ahead of wrapper.run.start.
    logged = [json.loads(raw) for raw in _log_lines(tmp_path)]
    resolved_event = next(
        event for event in logged if event["type"] == "wrapper.skill_policy.resolved"
    )
    assert resolved_event["enabled"] == ["tdd", "team-review"]
    assert resolved_event["base_scope"] == "project"
    assert resolved_event["fallback"] is None
    assert str(tmp_path) not in json.dumps(resolved_event)
    types_seen = [event["type"] for event in logged]
    assert types_seen.index("wrapper.skill_policy.resolved") < types_seen.index(
        "wrapper.run.start"
    )


def test_frozen_skill_policy_survives_a_catalog_change_mid_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_skill_catalog: object,
) -> None:
    """A catalog input that changes after preflight cannot move the boundary.

    Issue #227's AC6 requires the identical immutable policy to reach every
    session "even if catalog inputs change later". Here the agent deletes an
    enabled project Skill from the working tree mid-session: the Run must keep
    serving the exposure it froze at preflight rather than re-reading the tree.
    """
    enabled, withheld = _persisted_policy_repo(tmp_path)
    fake_client = _wire_persisted_policy_run(
        tmp_path, monkeypatch, tracked=(enabled, withheld)
    )
    observed_after_change: list[tuple[list[str], str]] = []

    def delete_the_enabled_skill_then_observe() -> None:
        shutil.rmtree(enabled)
        exposure = fake_client.exposure_dirs[0]
        observed_after_change.append(
            (
                sorted(path.name for path in exposure.iterdir()),
                # Read *through* the exposure: a name that survives while its
                # content does not would be a reference to the live tree, not
                # the frozen copy the session was promised.
                (exposure / "team-review" / "SKILL.md").read_text(encoding="utf-8"),
            )
        )

    fake_client.on_send = delete_the_enabled_skill_then_observe

    exit_code, _config = _run_from_persisted_config(tmp_path)

    assert exit_code == 0
    assert not enabled.exists(), "the test must actually mutate the catalog input"
    assert observed_after_change == [
        (
            ["tdd", "team-review"],
            "---\nname: team-review\ndescription: team-review description\n---\n"
            "# team-review\n",
        )
    ]


def test_persisted_policy_enabling_an_absent_skill_fails_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A policy the installed catalog cannot honour fails closed, run-wide.

    Issue #227's AC3 fails a configured policy closed *before* any Iteration or
    Lane starts. Since ADR-0025 the only way a persisted ``enabled_skills`` key
    can be unhonourable is that the installed catalog does not offer the name —
    the project layer, and with it the tracking-evidence branch this test used
    to cover, is gone. This is the branch that depends on the catalog seam
    reaching preflight, so it regresses silently if that wiring is dropped.
    """
    enabled, _withheld = _persisted_policy_repo(tmp_path)
    # Enabled by the persisted Config, then absent from the installed catalog:
    # a refresh that drops a Skill must not silently narrow the boundary.
    shutil.rmtree(enabled)
    fake_client = _wire_persisted_policy_run(tmp_path, monkeypatch, tracked=())

    exit_code, _config = _run_from_persisted_config(tmp_path)

    assert exit_code == 1
    assert fake_client.create_calls == [], "no session may start behind a failed policy"
    assert fake_client.start_call_count == 1
    assert fake_client.stop_call_count == 1


def test_persisted_skill_policy_bounds_the_replay_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_skill_catalog: object,
) -> None:
    """AC8, replay half: the frozen boundary and the consultation share one log.

    Exposure and the permission handler are in-process facts that vanish with
    the Run. Replay output is what an operator — or the Dashboard — actually
    reads afterwards, so the persisted policy has only really "flowed through"
    if the durable artifacts let a reader check the consultation against the
    boundary that permitted it, with no access to the Run.
    """
    enabled, withheld = _persisted_policy_repo(tmp_path)
    fake_client = _wire_persisted_policy_run(
        tmp_path, monkeypatch, tracked=(enabled, withheld)
    )
    fake_client._scripted_events = [
        _sdk_event(
            SessionEventType.TOOL_EXECUTION_START,
            ToolExecutionStartData(
                tool_call_id="call-skill",
                tool_name=SKILL_TOOL_NAME,
                arguments={"skill": "team-review"},
            ),
        ),
    ]

    exit_code, _ = _run_from_persisted_config(tmp_path)
    assert exit_code == 0

    payload = json.loads(
        next((tmp_path / ".git-loopy" / "runs").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    row = payload["iterations"][0]
    assert row["skill_count"] == 1
    assert row["skills_consulted"] == ["team-review"]
    assert payload["skill_adoption"]["skills"] == ["team-review"]

    resolved = [
        json.loads(line)
        for line in _log_lines(tmp_path)
        if json.loads(line)["type"] == "wrapper.skill_policy.resolved"
    ]
    assert len(resolved) == 1
    boundary = resolved[0]["enabled"]
    assert boundary == ["tdd", "team-review"]

    # The two artifacts are checkable against each other with nothing else in
    # hand: every Skill the Run consulted is inside the policy the same Run
    # recorded, and the withheld project Skill appears in neither.
    assert set(payload["skill_adoption"]["skills"]) <= set(boundary)
    assert "team-deploy" not in boundary
    assert "team-deploy" not in payload["skill_adoption"]["skills"]


# --------------------------------------------------------------------------- #
# Serial Pickup (#394, ADR-0032)                                              #
# --------------------------------------------------------------------------- #


def _wire_multi_issue_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issues: list[gh_module.Issue],
) -> tuple[FakeCopilotClient, FakeGitHubClient]:
    """Wire a Run whose Pool holds several candidates, so selection is visible."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    monkeypatch.setattr(
        loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path)
    )
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=issues,
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    return fake_client, fake_gh


def _dated(number: int, created_at: str, *, labels: list[str] | None = None):
    issue = _make_issue(number)
    return gh_module.Issue(
        number=issue.number,
        title=issue.title,
        body=issue.body,
        labels=labels if labels is not None else list(issue.labels),
        state=issue.state,
        url=issue.url,
        created_at=created_at,
        comments=(),
    )


def test_serial_pickup_binds_the_oldest_eligible_issue(tmp_path, monkeypatch) -> None:
    """The runner selects; the agent is told. This is the whole ticket (#394)."""
    fake_client, _ = _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(31, "2026-05-01T00:00:00Z"),
            _dated(7, "2026-01-01T00:00:00Z"),
        ],
    )

    assert asyncio.run(loop_module.run(RunConfig(issue_source="github",
                                                 max_iterations=1))) == 0

    activation = next(
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.issue.activated"
    )
    assert activation["issue"] == 7
    assert activation["binding_source"] == "serial_pickup"


def test_the_prompt_carries_exactly_the_bound_issue(tmp_path, monkeypatch) -> None:
    """Not a menu. The self-selection the whole Pool used to invite is gone."""
    fake_client, _ = _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(31, "2026-05-01T00:00:00Z"),
            _dated(7, "2026-01-01T00:00:00Z"),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    prompt, _timeout = fake_client.created[0].send_and_wait_calls[0]
    assert "=== Issue #7:" in prompt
    assert "=== Issue #31:" not in prompt


def test_a_priority_issue_is_bound_ahead_of_older_ones(tmp_path, monkeypatch) -> None:
    fake_client, _ = _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z"),
            _dated(
                31,
                "2026-05-01T00:00:00Z",
                labels=["ready-for-agent", "priority"],
            ),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    prompt, _timeout = fake_client.created[0].send_and_wait_calls[0]
    assert "=== Issue #31:" in prompt
    assert "=== Issue #7:" not in prompt


def test_the_pickup_precedes_the_session_it_bound_for(tmp_path, monkeypatch) -> None:
    """§8: an operator replaying the stream sees the binding, then the work."""
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    types_seen = _logged_types(tmp_path)
    assert types_seen.index("wrapper.afk_ready.collected") < types_seen.index(
        "wrapper.issue.activated"
    )
    assert types_seen.index("wrapper.issue.activated") < types_seen.index(
        "wrapper.iteration.end"
    )


def test_a_candidate_whose_routing_is_refused_is_skipped_not_fatal(
    tmp_path, monkeypatch
) -> None:
    """§7: selection is unattended, so a candidate it cannot take it passes over.

    An unknown ``task-type:`` key is the one refusal a serial **Pickup** can
    actually hit, and a **Lane** *raises* on it — releasing its reservation
    leaves the candidate for another Lane. A serial Iteration has no other Lane,
    so raising would end the Run over one bad label while eligible work sat
    behind it.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:not-a-real-key"],
            ),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    assert asyncio.run(loop_module.run(RunConfig(issue_source="github",
                                                 max_iterations=1))) == 0

    prompt, _timeout = fake_client.created[0].send_and_wait_calls[0]
    assert "=== Issue #31:" in prompt
    diagnostics = next(
        (tmp_path / ".git-loopy" / "logs").glob("*.log")
    ).read_text(encoding="utf-8")
    assert "serial Pickup skipped #7 at position 1 of 2" in diagnostics


def test_a_pool_whose_every_candidate_is_skipped_strikes_rather_than_exits(
    tmp_path, monkeypatch
) -> None:
    """"I could not take any of it" must not be reported as "there is no work"."""
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:nope"],
            )
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    types_seen = _logged_types(tmp_path)
    assert "wrapper.strike" in types_seen
    assert "wrapper.issue.activated" not in types_seen
    run_end = next(
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.run.end"
    )
    assert run_end["outcome"] != "empty_pool"


def _pickup_events(tmp_path: Path) -> list[dict[str, Any]]:
    """Every Pickup record this Run wrote, bindings and skips alike, in order."""
    return [
        event
        for raw in _log_lines(tmp_path)
        if (event := json.loads(raw))["type"]
        in ("wrapper.pickup.bound", "wrapper.pickup.skipped")
    ]


def test_a_serial_pickup_records_what_it_bound_and_why(tmp_path, monkeypatch) -> None:
    """#397: a decision nobody can see is a decision nobody can audit.

    ``position`` and ``considered`` travel together because position 1 alone
    cannot tell "the runner took the oldest" from "the runner took the only one
    left" — which is the question the Event exists to answer.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(31, "2026-05-01T00:00:00Z"),
            _dated(7, "2026-01-01T00:00:00Z"),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    assert _pickup_events(tmp_path) == [
        {
            "ts": _pickup_events(tmp_path)[0]["ts"],
            "run_id": _pickup_events(tmp_path)[0]["run_id"],
            "iter": 1,
            "type": "wrapper.pickup.bound",
            "issue": 7,
            "reason": "order",
            "position": 1,
            "considered": 2,
        }
    ]


def test_a_priority_binding_says_the_label_is_why(tmp_path, monkeypatch) -> None:
    """"Is the backlog draining oldest-first?" and "did my label do anything?"
    are different operator questions, so they get different reasons."""
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z"),
            _dated(
                31, "2026-05-01T00:00:00Z", labels=["ready-for-agent", "priority"]
            ),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    bound = _pickup_events(tmp_path)
    assert [(e["issue"], e["reason"], e["position"]) for e in bound] == [
        (31, "priority", 1)
    ]


def test_a_passed_over_issue_leaves_a_record_not_only_a_log_line(
    tmp_path, monkeypatch
) -> None:
    """The starvation ADR-0032 fixes was invisible *for want of this record*.

    An issue could be passed over fifty times and the only evidence was that it
    was still there. The skip carries its position because being skipped at the
    head of the order and being skipped behind ten other candidates are
    different facts about a backlog.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:not-a-real-key"],
            ),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    records = _pickup_events(tmp_path)
    assert [e["type"] for e in records] == [
        "wrapper.pickup.skipped",
        "wrapper.pickup.bound",
    ]
    skipped, bound = records
    assert (skipped["issue"], skipped["position"], skipped["considered"]) == (7, 1, 2)
    assert "not-a-real-key" in skipped["reason"]
    assert (bound["issue"], bound["position"], bound["considered"]) == (31, 2, 2)


def test_a_walk_that_binds_nothing_still_records_every_skip(
    tmp_path, monkeypatch
) -> None:
    """The Run going nowhere is exactly the one whose reasons matter most."""
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7, "2026-01-01T00:00:00Z", labels=["ready-for-agent", "task-type:nope"]
            ),
            _dated(
                31, "2026-05-01T00:00:00Z", labels=["ready-for-agent", "task-type:nah"]
            ),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    records = _pickup_events(tmp_path)
    assert [e["type"] for e in records] == ["wrapper.pickup.skipped"] * 2
    assert [e["issue"] for e in records] == [7, 31]


def test_the_binding_record_precedes_the_session_it_bound_for(
    tmp_path, monkeypatch
) -> None:
    """Replay order is the claim: the runner decided before the agent spoke."""
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    types_seen = _logged_types(tmp_path)
    assert types_seen.index("wrapper.afk_ready.collected") < types_seen.index(
        "wrapper.pickup.bound"
    )
    assert types_seen.index("wrapper.pickup.bound") < types_seen.index(
        "wrapper.issue.activated"
    )


def test_a_clean_pool_records_a_binding_and_no_skip(tmp_path, monkeypatch) -> None:
    """This slice adds visibility; it must not invent a skip that never was."""
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    assert [e["type"] for e in _pickup_events(tmp_path)] == ["wrapper.pickup.bound"]


def test_the_routed_pair_is_resolved_at_pickup(tmp_path, monkeypatch) -> None:
    """The pair the session is built with comes from the Pickup, not the config.

    ``CONTEXT.md``: *"Because the issue is known first, pickup is where its
    Routed pair resolves."* Until #394 a serial Iteration had no per-issue pair
    to resolve, because it had no per-issue anything.

    And since ADR-0037 the resolved pair is also the pair the session **runs
    on** in serial, not merely the pair the Pickup computed and discarded. The
    issue carries ``task-type:docs``, the ``[routing]`` table sends that key to
    ``gpt-5-mini``, and the run-wide ``gpt-5.4`` is what the routed pair
    replaces — which is what an operator who configures a table and types
    ``git-loopy`` with no flags has always expected to happen.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:docs"],
            )
        ],
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=1,
                model="gpt-5.4",
                routing={"docs": ("gpt-5-mini", None)},
            )
        )
    )

    assert fake_client.create_calls[0]["model"] == "gpt-5-mini"
    assert routing_scope.routing_in_force(1)


def test_a_working_marker_naming_another_issue_does_not_rebind(
    tmp_path, monkeypatch
) -> None:
    """The marker confirms a binding it no longer creates (ADR-0032)."""
    fake_client, _ = _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z"),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )
    fake_client._scripted_events = [
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(content="<working issue=31>", message_id="m1"),
        )
    ]

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    activations = [
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.issue.activated"
    ]
    assert [a["issue"] for a in activations] == [7]
    diagnostics = next(
        (tmp_path / ".git-loopy" / "logs").glob("*.log")
    ).read_text(encoding="utf-8")
    assert "Working marker disagreement: the agent named #31" in diagnostics


def test_the_retained_fallback_still_binds_when_pickup_did_not(
    tmp_path, monkeypatch
) -> None:
    """``_infer_active_binding`` survives as a *degraded* path, and is tested as one.

    Its three branches used to be reached by the normal serial flow, so the
    end-to-end suite covered them incidentally. Pickup binds first now, which
    would have retired that coverage silently — leaving a fallback nothing
    exercises until the day it is the only thing standing between a Checkpoint
    and an unattributed commit.
    """
    loop_obj = object.__new__(loop_module._Loop)
    pool = [
        sources_module.AfkReadyItem(ref=7, title="a", rendered_block="", labels=()),
        sources_module.AfkReadyItem(ref=31, title="b", rendered_block="", labels=()),
    ]
    completion = sources_module.Completion(ref=31, sha="deadbeef")

    assert loop_obj._infer_active_binding(pool, [completion], []) == (31, "closure")
    assert loop_obj._infer_active_binding(
        pool, [], [git_module.Commit(sha="s", subject="x", body="Closes #7",
                                    date="2026-01-01")]
    ) == (7, "commit")
    assert loop_obj._infer_active_binding(pool[:1], [], []) == (
        7,
        "single_member_pool",
    )
    assert loop_obj._infer_active_binding(pool, [], []) is None


def test_a_refused_iteration_ends_content_filtered_not_merely_silent(
    tmp_path, monkeypatch
) -> None:
    """The **Content-filtered** ending, from the stream to the record (#405).

    The harness reports the refusal on the per-call usage record and lets the
    session finish politely, so a whole Iteration spent being filtered reached
    the Run as "no progress" — the same ending as an Agent that read the issue
    and did nothing. The detector is what tells them apart, and this pins the
    whole path: the mapped Event carries the harness's verdict, and the
    Iteration's diagnostic names the ending it produced.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    filtered = _sdk_event(
        SessionEventType.ASSISTANT_USAGE,
        AssistantUsageData(
            model="claude-haiku-4.5",
            input_tokens=1200.0,
            output_tokens=0.0,
            content_filter_triggered=True,
            finish_reason="content_filter",
        ),
    )
    fake_client = FakeCopilotClient(scripted_events=[filtered])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    assert asyncio.run(loop_module.run(cfg)) == 0

    logs = tmp_path / ".git-loopy" / "logs"
    jsonl = next(iter(logs.glob("*.jsonl")))
    usage = [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["type"] == events_module.USAGE_TOKENS
    ]
    assert usage and usage[0]["content_filtered"] is True
    assert usage[0]["finish_reason"] == "content_filter"

    diagnostics = next(iter(logs.glob("*.log"))).read_text(encoding="utf-8")
    assert "ended content_filtered" in diagnostics


def test_an_agent_that_declares_no_more_tasks_is_believed(
    tmp_path, monkeypatch
) -> None:
    """The sentinel's detector, at the Run seam (#405).

    ``<promise>NO MORE TASKS</promise>`` had been a parameter two functions
    accepted and discarded, passed by no production call site, so an Agent
    reporting an unworkable issue was recorded as one that silently produced
    nothing. The declaration now reaches the **Session outcome** — and only the
    outcome: no Strike, abort or refill decision is taken from it here.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    declaration = _sdk_event(
        SessionEventType.ASSISTANT_MESSAGE,
        AssistantMessageData(
            content=(
                "#42 is already done on main; I commented and stopped.\n"
                "<promise>NO MORE TASKS</promise>"
            ),
            message_id="m1",
        ),
    )
    fake_client = FakeCopilotClient(scripted_events=[declaration])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    assert asyncio.run(loop_module.run(cfg)) == 0

    diagnostics = next(
        iter((tmp_path / ".git-loopy" / "logs").glob("*.log"))
    ).read_text(encoding="utf-8")
    assert "ended no_more_tasks" in diagnostics


def test_loop_records_the_session_ending_and_its_error_identity(
    tmp_path, monkeypatch
) -> None:
    """A Run refused by the account records the refusal, not just the silence (#403).

    The harness reports an exhausted quota as a ``session.error`` and then lets
    the session finish politely, so an Iteration spent entirely being refused
    used to look exactly like an Agent that read the issue and did nothing: the
    record dropped the harness's failure, and the diagnostic said "no progress".

    Both halves are pinned here because either alone leaves the ending
    unreadable: the replay log now carries the harness's own structured fields,
    and the Iteration's diagnostic names the **Session outcome** together with
    the **Session error** identity behind it.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    refusal = _sdk_event(
        SessionEventType.SESSION_ERROR,
        SessionErrorData(
            error_type="QuotaExceededError",
            message="Monthly premium request quota exhausted.",
            error_code="insufficient_quota",
            status_code=429,
        ),
    )
    fake_client = FakeCopilotClient(scripted_events=[refusal])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    cfg = RunConfig(issue_source="github", max_iterations=1)
    assert asyncio.run(loop_module.run(cfg)) == 0

    logs = tmp_path / ".git-loopy" / "logs"
    jsonl = next(iter(logs.glob("*.jsonl")))
    recorded = [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["type"] == events_module.SESSION_ERROR
    ]
    assert len(recorded) == 1
    assert recorded[0]["error_code"] == "insufficient_quota"
    assert recorded[0]["status_code"] == 429

    diagnostics = next(iter(logs.glob("*.log"))).read_text(encoding="utf-8")
    assert "no_progress" in diagnostics
    assert "quota_exhausted" in diagnostics
