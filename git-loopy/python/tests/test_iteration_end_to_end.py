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
import inspect
import itertools
import json
import os
import shutil
from decimal import Decimal
from dataclasses import replace as dataclass_replace
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
from git_loopy import dynamic_route
from git_loopy import events as events_module
from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import loop as loop_module
from git_loopy import routing_scope
from git_loopy import settings
from git_loopy import skill_install
from git_loopy import sources as sources_module
from git_loopy import static_route
from git_loopy.static_route import RoutePolicy
from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.emit import EventEmitter
from git_loopy.events import REDACTED_SECRET
from git_loopy import persist as persist_module
from git_loopy.persist import WritersBundle, create_writers
from git_loopy.readiness import BlockedByRead, BlockerNode
from git_loopy.route_publication import RouteDeliveryError
from git_loopy.run_control import is_run_alive
from git_loopy.session import SKILL_TOOL_NAME
from git_loopy.sinks import SinkFanout
from git_loopy.skill_catalog import build_skill_catalog
from git_loopy.staircase import Candidate, PriceStaircase
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
            sent = self._on_send()
            if inspect.isawaitable(sent):
                await sent
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
    labels: list[str] | None = None,
) -> gh_module.Issue:
    return gh_module.Issue(
        number=number,
        title=f"Test issue {number}",
        body=body,
        labels=["ready-for-agent"] if labels is None else labels,
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
    control_path = jsonl_files[0].with_suffix(".control")
    assert control_path.exists()
    assert is_run_alive(control_path) is False
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
        # Per-distribution (#411): this binary always publishes a Routing
        # resolution on a bound Pickup -- a `--model` pin makes the source
        # `defaulted_explicit_override` rather than silencing it -- so unlike
        # the card, the answer cannot differ between two Runs of it.
        "routing": True,
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
        "execution_hosts": ["local", "github-actions"],
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


def _write_runnable_feedback_loop(repo_root: Path) -> None:
    """Declare the minimum valid Integration gate for a synthetic Run repository."""
    (repo_root / "AGENTS.md").write_text(
        "## Feedback loops\n\n"
        "| Loop | Command |\n"
        "| --- | --- |\n"
        "| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )


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
    labels: list[str] | None = None,
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
    (tmp_path / "git-loopy").mkdir(exist_ok=True)
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    issue = _make_issue(issue_number, labels=labels)
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


def test_loop_refuses_a_repository_without_runnable_feedback_loops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Run stops before its first session when Integration could never gate."""
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n"
        "| Loop | Command |\n"
        "| --- | --- |\n"
        "| Placeholder | `<TEST_COMMAND>` |\n",
        encoding="utf-8",
    )
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main")
        ),
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    assert asyncio.run(loop_module.run(RunConfig(issue_source="github"))) == 1
    assert fake_client.start_call_count == 0
    assert fake_client.created == []
    assert "Add at least one runnable command" in capsys.readouterr().err


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
    # Two sessions, not three: the **Attempt lifecycle** (#412) defeats issue 42
    # after its second silent stall, so the third Iteration has no candidate left
    # to bind and works no issue. It still ticks the strike that aborts the Run —
    # an unworked Iteration always did — so what the Run *charges* is unchanged
    # and what it *spends* is one fewer session.
    assert len(fake_client.created) == 2, (
        f"expected 2 SDK sessions before abort; got {len(fake_client.created)}"
    )


# ---------------------------------------------------------------------------
# Additional rubber-duck-recommended coverage
# ---------------------------------------------------------------------------


def test_loop_send_and_wait_exception_is_no_progress(tmp_path, monkeypatch) -> None:
    """If ``send_and_wait`` raises, the iteration is treated as no-progress.

    The post-iteration accounting (commits_between, auto-close backstop,
    strike tick, iteration.end emit, counters persist) still runs — the
    SDK failure is contained to "no progress" semantics.

    Since contract 1.27 a **Strike** is charged per issue the Run gives up
    on, not per unproductive Iteration, so a first crash spends the issue's
    first attempt and charges nothing.
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
    # iteration.start, iteration.end, run.end.
    log_files = list((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    assert len(log_files) == 1
    events = [
        json.loads(line)
        for line in log_files[0].read_text(encoding="utf-8").splitlines()
    ]
    types_seen = {event["type"] for event in events}
    assert "wrapper.iteration.end" in types_seen
    assert "wrapper.run.end" in types_seen
    # ...and the crash spent the issue's first attempt without defeating it,
    # so nothing was given up on and no Strike was charged.
    assert "wrapper.strike" not in types_seen
    iteration_end = next(e for e in events if e["type"] == "wrapper.iteration.end")
    assert iteration_end["summary"]["strikes"] == 0


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
    events = _read_events(tmp_path)
    assert [
        (event["cause"], event["stage"], event["draining"])
        for event in events
        if event["type"] == "wrapper.stop.requested"
    ] == [("iteration_cap", "drain", 0)]


# ---------------------------------------------------------------------------
# The two Stop stages on a serial Iteration (#454, ADR-0043)
# ---------------------------------------------------------------------------


class _HeldSerialClient(FakeCopilotClient):
    """A client whose session parks until the test lets it go.

    A serial **Iteration** has no **Execution host** at all, so the two Stop
    stages have to reach it through the session itself: the first must let the
    Iteration in flight run to completion, the second must cancel it.
    """

    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(scripted_events=[])
        self._started = started
        self._release = release

    async def create_session(self, **kwargs: Any) -> FakeCopilotSession:
        session = await super().create_session(**kwargs)
        work = session.send_and_wait

        async def hold_until_stopped(
            prompt: str, *, timeout: float = 60.0, **extra: Any
        ) -> SessionEvent | None:
            self._started.set()
            await self._release.wait()
            return await work(prompt, timeout=timeout, **extra)

        session.send_and_wait = hold_until_stopped  # type: ignore[method-assign]
        return session


def _capture_serial_loops(monkeypatch) -> list["loop_module._Loop"]:
    """Hand a test the live serial loop, so it can gesture a **Stop** at it."""
    built: list[loop_module._Loop] = []
    real_loop = loop_module._Loop

    def capture(*args: Any, **kwargs: Any) -> loop_module._Loop:
        instance = real_loop(*args, **kwargs)
        built.append(instance)
        return instance

    monkeypatch.setattr(loop_module, "_Loop", capture)
    return built


def _wire_serial_stop_run(
    tmp_path: Path, monkeypatch, *, issues: list[int]
) -> tuple[FakeGitClient, asyncio.Event, asyncio.Event, list["loop_module._Loop"]]:
    """Wire a serial Run whose one live session parks on demand."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(n) for n in issues],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)

    started, release = asyncio.Event(), asyncio.Event()
    fake_client = _HeldSerialClient(started, release)
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(subject="progress")
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    return fake_git, started, release, _capture_serial_loops(monkeypatch)


def test_the_first_stop_finishes_the_serial_iteration_and_starts_no_more(
    tmp_path, monkeypatch
) -> None:
    """Stage one on the path that has no Lane at all (#454, ADR-0043).

    The Iteration in flight runs to completion — its commit still counts — and
    the round after it never opens, even though the cap left room for two more.
    """
    fake_git, started, release, built = _wire_serial_stop_run(
        tmp_path, monkeypatch, issues=[42, 43]
    )
    cfg = RunConfig(issue_source="github", max_iterations=3, max_nmt_strikes=3)

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        await asyncio.wait_for(started.wait(), timeout=5)
        assert built
        built[0].request_stop_drain()
        release.set()
        return await asyncio.wait_for(run_task, timeout=5)

    assert asyncio.run(scenario()) == 1

    events = _read_events(tmp_path)
    assert [
        (event["cause"], event["stage"], event["draining"])
        for event in events
        if event["type"] == "wrapper.stop.requested"
    ] == [("operator_stop", "drain", 0)]
    iteration_ends = [e for e in events if e["type"] == "wrapper.iteration.end"]
    assert len(iteration_ends) == 1, "the started Iteration ran to completion"
    assert [e["type"] for e in events].count("wrapper.commit.recorded") == 1
    (run_end,) = [e for e in events if e["type"] == "wrapper.run.end"]
    assert run_end["outcome"] == "operator_stop"
    assert run_end["iterations_run"] == 1


def test_the_second_stop_cancels_the_serial_session_and_charges_no_strike(
    tmp_path, monkeypatch
) -> None:
    """Stage two on a serial Iteration: cancelled, recorded, and blameless.

    The Iteration is ended by a human, so it is not evidence that the Run was
    getting nowhere: the **Strike** counter must not move, exactly as a stopped
    **Lane contribution**'s does not.
    """
    _fake_git, started, _release, built = _wire_serial_stop_run(
        tmp_path, monkeypatch, issues=[42]
    )
    cfg = RunConfig(issue_source="github", max_iterations=3, max_nmt_strikes=3)

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        await asyncio.wait_for(started.wait(), timeout=5)
        assert built
        built[0].request_stop_drain()
        await asyncio.sleep(0)
        assert not run_task.done(), "the first Stop cancels nothing"
        built[0].request_stop_cancel()
        return await asyncio.wait_for(run_task, timeout=5)

    assert asyncio.run(scenario()) == 1

    events = _read_events(tmp_path)
    assert [
        (event["cause"], event["stage"], event["draining"])
        for event in events
        if event["type"] == "wrapper.stop.requested"
    ] == [
        ("operator_stop", "drain", 0),
        ("operator_stop", "cancel", 0),
    ]
    assert [e for e in events if e["type"] == "wrapper.strike"] == []
    (run_end,) = [e for e in events if e["type"] == "wrapper.run.end"]
    assert run_end["outcome"] == "operator_stop"


def test_second_stop_before_serial_send_starts_no_agent_session(
    tmp_path, monkeypatch
) -> None:
    """A cancellation request that wins setup must prevent the next agent turn."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_make_issue(42)],
        ),
    )
    built = _capture_serial_loops(monkeypatch)

    class _StoppingBeforeSendClient(FakeCopilotClient):
        async def create_session(self, **kwargs: Any) -> FakeCopilotSession:
            session = await super().create_session(**kwargs)
            assert built
            built[0].request_stop_drain()
            built[0].request_stop_cancel()
            return session

    fake_client = _StoppingBeforeSendClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
        )
    )

    assert exit_code == 1
    assert fake_client.created[0].send_and_wait_calls == []
    events = _read_events(tmp_path)
    assert [event for event in events if event["type"] == "wrapper.strike"] == []
    (run_end,) = [event for event in events if event["type"] == "wrapper.run.end"]
    assert run_end["outcome"] == "operator_stop"


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
    _write_runnable_feedback_loop(tmp_path)
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


def _dated(
    number: int,
    created_at: str,
    *,
    labels: list[str] | None = None,
    blocked_by: BlockedByRead | None = None,
):
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
        blocked_by=blocked_by if blocked_by is not None else BlockedByRead(total_count=0, nodes=()),
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
    fake_client, _ = _wire_multi_issue_github(
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


def test_a_pool_whose_every_candidate_is_skipped_ends_the_run_all_skipped(
    tmp_path, monkeypatch
) -> None:
    """"I could not take any of it" must not be reported as "there is no work".

    Since contract 1.27 the Iteration that binds nothing is terminal under its
    own reason rather than charging a **Strike** and coming round again: with
    no-progress no longer charging the ceiling, an Iteration that spends no
    session could otherwise spin until the Iteration cap.
    """
    fake_client, _ = _wire_multi_issue_github(
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

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=0))
    )

    assert exit_code == 1
    types_seen = _logged_types(tmp_path)
    assert "wrapper.issue.activated" not in types_seen
    # Nothing was *given up on* -- a refused route never reached a session, so
    # no attempt was spent and the Strike ledger stayed empty.
    assert "wrapper.strike" not in types_seen
    run_end = next(
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.run.end"
    )
    assert run_end["outcome"] == "all_skipped"


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

    Pinned as a whole payload rather than field by field, so a key that arrives
    on this record without a contract has to arrive here too (#407 added seven
    of them, and this is what made it say so).
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
            "model": None,
            "effort": None,
            "context_tier": "default",
            "routing_source": "defaulted_no_task_type_label",
            "task_type_keys": [],
            "gate_warnings": [],
            "lifecycle_position": "fresh",
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
    assert routing_scope.routing_in_force()


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


def test_a_serial_binding_publishes_the_pair_it_resolved_and_why(
    tmp_path, monkeypatch
) -> None:
    """#407: an operator could not tell which model worked an issue.

    The Pickup is where the pair resolves, so the Pickup is the one record that
    can say what it resolved to — extended rather than joined by a second event,
    which would put two records with the same key on the wire at the same
    instant. The trace is the audit surface: after the fact, this is what names
    the model that worked the issue and whether a label chose it.
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
                routing={"docs": ("gpt-5-mini", "medium")},
                context_tier="long_context",
            )
        )
    )

    (bound,) = _pickup_events(tmp_path)
    assert bound["type"] == "wrapper.pickup.bound"
    assert bound["model"] == "gpt-5-mini"
    assert bound["effort"] == "medium"
    assert bound["routing_source"] == "routed"
    assert bound["task_type_keys"] == ["docs"]
    assert bound["gate_warnings"] == []
    assert bound["context_tier"] == "long_context"
    assert bound["lifecycle_position"] == "fresh"
    assert fake_client.create_calls[0]["model"] == "gpt-5-mini"
    assert fake_client.create_calls[0]["reasoning_effort"] == "medium"
    assert fake_client.create_calls[0]["context_tier"] == "long_context"


def test_an_unlabelled_binding_says_the_fallback_rather_than_staying_silent(
    tmp_path, monkeypatch
) -> None:
    """A silent fallback and a deliberate route are the two things to tell apart.

    An unlabelled issue is the overwhelmingly common case while the corpus
    carries no **Task type**, and it is precisely the case an operator suspects:
    the record states the fallback rather than omitting the provenance, because
    an absent field would read as a Runner that does not route at all.
    """
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    (bound,) = _pickup_events(tmp_path)
    assert bound["routing_source"] == "defaulted_no_task_type_label"
    assert bound["task_type_keys"] == []


# ---------------------------------------------------------------------------
# The **Escalation rung** (#408): a stalled issue is retried once, immediately,
# on one higher pair — and every other ending leaves the pair alone.
# ---------------------------------------------------------------------------


def _bound_pickups(tmp_path: Path) -> list[dict[str, Any]]:
    """Every binding this Run made, in order."""
    return [e for e in _pickup_events(tmp_path) if e["type"] == "wrapper.pickup.bound"]


def test_a_stalled_issue_is_re_picked_at_the_rung(tmp_path, monkeypatch) -> None:
    """The whole ticket (#408), through the record an operator reads.

    The first Iteration ends in silent no-progress — the session ran to the end,
    claimed no failure, and left no commit — which is exactly how a pair too
    cheap for the work fails. The second **Pickup** takes *the same issue*,
    immediately rather than after the rest of the Pool, at the configured rung,
    and says so: the pair changed, and the **Routing source** is ``escalated``
    rather than the fallback it resolved with the first time.
    """
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=2,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [
        (e["issue"], e["model"], e["effort"], e["routing_source"])
        for e in _bound_pickups(tmp_path)
    ] == [
        (7, "claude-sonnet-5", "low", "defaulted_no_task_type_label"),
        (7, "claude-opus-5", "max", "escalated"),
    ]


def test_the_escalated_session_runs_on_the_escalated_pair(
    tmp_path, monkeypatch
) -> None:
    """A rung the Pickup resolved and the session did not use is no rung at all.

    ADR-0037's rule, applied to escalation: the pair a **Pickup** resolves is
    the pair its session runs on, in serial exactly as in a **Lane**.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=2,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [
        (call["model"], call["reasoning_effort"]) for call in fake_client.create_calls
    ] == [("claude-sonnet-5", "low"), ("claude-opus-5", "max")]


def test_the_rung_is_sticky_for_the_rest_of_the_run(tmp_path, monkeypatch) -> None:
    """Escalation happens once, and never drops back down.

    Re-testing the cheap pair on an issue the last Iteration just proved it
    cannot work pays to relearn what was already learned, and the oscillation it
    admits spends half its sessions at the ceiling without ever tripping a
    **Strike**. Here the escalated Iteration *lands* — so the issue keeps its
    attempts (#412) and is picked up a third time, which is the one shape in
    which stickiness is still observable inside a Run: the ledger only grows, so
    a productive Iteration at the rung does not hand the issue back to the pair
    that stalled on it.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    sends = itertools.count(1)
    fake_client.on_send = lambda: (
        fake_git.simulate_agent_commit(subject="chore: the rung got somewhere")
        if next(sends) == 2
        else None
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=3,
                max_nmt_strikes=9,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [(e["model"], e["routing_source"]) for e in _bound_pickups(tmp_path)] == [
        ("claude-sonnet-5", "defaulted_no_task_type_label"),
        ("claude-opus-5", "escalated"),
        ("claude-opus-5", "escalated"),
    ]


def test_escalation_is_a_no_op_when_the_routed_pair_is_already_the_rung(
    tmp_path, monkeypatch
) -> None:
    """There is nowhere to escalate to, so nothing is claimed to have happened.

    ``planning`` routes to the rung by design (ADR-0035), so it gets one shot
    and the retry is the same pair. Reporting that as ``escalated`` would state
    a change that did not occur — the source stays the one that chose the pair,
    while the **lifecycle position** records that this is a second attempt.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:planning"],
            )
        ],
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=2,
                routing={"planning": ("claude-opus-5", "max")},
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [
        (e["model"], e["effort"], e["routing_source"], e["lifecycle_position"])
        for e in _bound_pickups(tmp_path)
    ] == [
        ("claude-opus-5", "max", "routed", "fresh"),
        ("claude-opus-5", "max", "routed", "retrying"),
    ]


def test_a_run_with_no_rung_re_picks_the_same_issue_on_the_same_pair(
    tmp_path, monkeypatch
) -> None:
    """Escalation off is the pre-#408 behaviour, unchanged and still available."""
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=2,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=None,
            )
        )
    )

    assert [(e["model"], e["routing_source"]) for e in _bound_pickups(tmp_path)] == [
        ("claude-sonnet-5", "defaulted_no_task_type_label"),
        ("claude-sonnet-5", "defaulted_no_task_type_label"),
    ]


def test_an_agent_that_declares_no_more_tasks_does_not_escalate(
    tmp_path, monkeypatch
) -> None:
    """The Agent's own stated conclusion is not evidence the pair was too cheap.

    The declaration is the one ending most easily said by accident, and
    answering it with the ceiling would spend the Run's most expensive pair on
    an issue whose worker said there was nothing to do. It buys no second
    attempt either (#412): the issue is out of contention on the strength of the
    same declaration, so the second Iteration passes it over rather than
    re-picking it at either pair.
    """
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    fake_client = loop_module._make_client()
    fake_client._scripted_events = [
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(
                content="nothing to do here.\n<promise>NO MORE TASKS</promise>",
                message_id="m1",
            ),
        )
    ]

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=2,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [
        (e["type"], e.get("routing_source")) for e in _pickup_events(tmp_path)
    ] == [
        ("wrapper.pickup.bound", "defaulted_no_task_type_label"),
        ("wrapper.pickup.skipped", None),
    ]


def test_escalation_ticks_no_strike_of_its_own(tmp_path, monkeypatch) -> None:
    """Trying harder is never punished by the mechanism that aborts the Run.

    Escalation adds no **Strike** and clears none: an escalating Run charges
    exactly what the same two unproductive Iterations charge without a rung. The
    re-definition of what a Strike *counts* is a separate change (#413) to a
    phase-1 contract section every Runner implements; this one must not
    anticipate it, in either direction.
    """
    def strikes_for(rung: tuple[str, str] | None, at: Path) -> list[int]:
        _wire_multi_issue_github(at, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")])
        asyncio.run(
            loop_module.run(
                RunConfig(
                    issue_source="github",
                    max_iterations=2,
                    max_nmt_strikes=9,
                    model="claude-sonnet-5",
                    reasoning_effort="low",
                    escalation_rung=rung,
                )
            )
        )
        return [
            json.loads(raw)["strikes"]
            for raw in _log_lines(at)
            if json.loads(raw)["type"] == "wrapper.strike"
        ]

    escalating = tmp_path / "escalating"
    flat = tmp_path / "flat"
    for path in (escalating, flat):
        path.mkdir()

    assert strikes_for(("claude-opus-5", "max"), escalating) == strikes_for(None, flat)


# ---------------------------------------------------------------------------
# The **Attempt lifecycle** (#412): a defeated issue is skipped for the rest of
# the Run. A **Pickup** filter, never a Pool one.
# ---------------------------------------------------------------------------


def test_a_twice_stalled_issue_is_skipped_and_the_run_moves_on(
    tmp_path, monkeypatch
) -> None:
    """The whole ticket (#412), through the records an operator reads.

    Issue 7 is the head of the order and stalls silently twice — once on the
    pair it routed to and once on the **Escalation rung** that stall bought it.
    That is every attempt the Run has to offer, so the third **Pickup** passes
    it over with a **Pickup skip** and binds the work behind it. Before this,
    the same issue came back on the same pair every Iteration until the
    **Strike** ceiling ended the Run, and the issues behind it were never
    reached at all.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z"),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=3,
                max_nmt_strikes=9,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [(e["type"], e["issue"]) for e in _pickup_events(tmp_path)] == [
        ("wrapper.pickup.bound", 7),
        ("wrapper.pickup.bound", 7),
        ("wrapper.pickup.skipped", 7),
        ("wrapper.pickup.bound", 31),
    ]


def test_a_defeated_issue_leaves_the_pool_whole(tmp_path, monkeypatch) -> None:
    """Skipping narrows the candidate list, never the Pool it is drawn from.

    Filtering at collection would repeal the Pool-wide closure whitelist as a
    side effect: an Iteration working issue 31 could no longer close issue 7 by
    commit keyword, and the emptiness test would end a Run that still has open
    work in front of it. So the collection Event still names both issues on
    every Iteration, and only the walk over them declines one.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z"),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=3,
                max_nmt_strikes=9,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    collected = [
        event["issues"]
        for raw in _log_lines(tmp_path)
        if (event := json.loads(raw))["type"] == "wrapper.afk_ready.collected"
    ]
    assert collected == [[7, 31], [7, 31], [7, 31]]


# ---------------------------------------------------------------------------
# **Readiness** (#438, ADR-0047, Wrapper contract §3.3.1): a candidate carrying
# an open native `blocked_by` dependency is not admissible at **Pickup**. The
# runner passes it over and binds the next candidate instead -- in milliseconds,
# never spending an agent session discovering the blocker for itself.
# ---------------------------------------------------------------------------


def test_a_blocked_candidate_is_passed_over_for_the_next_admissible_one(
    tmp_path, monkeypatch
) -> None:
    """The whole ticket, end to end: #438's first property, demonstrated.

    Issue 7 is the head of the order and carries an open native blocker.
    **Pickup** passes it over -- costing no agent session at all -- and binds
    issue 31 instead, the next candidate in §3.2's order.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                blocked_by=BlockedByRead(
                    total_count=1,
                    nodes=(BlockerNode(ref="acme/widgets#93", state="open"),),
                ),
            ),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=1))
    )

    assert exit_code == 0
    assert [(e["type"], e["issue"]) for e in _pickup_events(tmp_path)] == [
        ("wrapper.pickup.skipped", 7),
        ("wrapper.pickup.bound", 31),
    ]
    skip = next(e for e in _pickup_events(tmp_path) if e["type"] == "wrapper.pickup.skipped")
    assert skip["reason"] == "blocked_by_open_dependency: acme/widgets#93"


def test_readiness_is_asked_before_routing_resolves(tmp_path, monkeypatch) -> None:
    """#438: a candidate about to be passed over must not first pay to route.

    Issue 7 carries both an open blocker *and* a ``task-type:`` label that
    would refuse to resolve a **Routed pair** were routing ever asked. If
    readiness were checked after routing, the walk would pay for (and report)
    the routing refusal instead; asked first, it never reaches routing at all,
    and the skip names the blocker rather than a routing complaint.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:not-a-real-key"],
                blocked_by=BlockedByRead(
                    total_count=1,
                    nodes=(BlockerNode(ref="acme/widgets#93", state="open"),),
                ),
            ),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    skip = next(e for e in _pickup_events(tmp_path) if e["type"] == "wrapper.pickup.skipped")
    assert skip["issue"] == 7
    assert skip["reason"] == "blocked_by_open_dependency: acme/widgets#93"
    assert "routing refused" not in skip["reason"]


def test_a_blocked_candidate_leaves_the_pool_whole_and_charges_no_strike(
    tmp_path, monkeypatch
) -> None:
    """#438's second property: never attempted, so never charged.

    Mirrors :func:`test_a_defeated_issue_leaves_the_pool_whole` for the
    **Attempt lifecycle**: a blocked candidate stays in the **Pool** (the
    closure whitelist, the collection Event and the emptiness test all still
    see it), and -- unlike an Attempt-lifecycle skip, which follows a real
    stall -- a readiness skip charges no **Strike** at all, because the issue
    was never attempted.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                blocked_by=BlockedByRead(
                    total_count=1,
                    nodes=(BlockerNode(ref="acme/widgets#93", state="open"),),
                ),
            ),
            _dated(31, "2026-05-01T00:00:00Z"),
        ],
    )

    asyncio.run(
        loop_module.run(
            RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=9)
        )
    )

    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    types_seen = {event["type"] for event in events}
    # Pool retention: the collection Event still names the blocked issue.
    collected = [e["issues"] for e in events if e["type"] == "wrapper.afk_ready.collected"]
    assert collected == [[7, 31]]
    # No Strike: a readiness skip was never an attempt.
    assert "wrapper.strike" not in types_seen
    iteration_end = next(e for e in events if e["type"] == "wrapper.iteration.end")
    assert iteration_end["summary"]["strikes"] == 0


def test_an_all_blocked_pool_ends_waiting_on_blockers(tmp_path, monkeypatch) -> None:
    """A non-empty Pool with only proven open blockers is not all-skipped."""
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                blocked_by=BlockedByRead(
                    total_count=1,
                    nodes=(BlockerNode(ref="acme/widgets#93", state="open"),),
                ),
            )
        ],
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=5))
    )

    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    assert exit_code == loop_module.exit_code_for("all_blocked")
    assert [event["type"] for event in events].count("wrapper.pickup.skipped") == 1
    assert not any(event["type"] == "wrapper.pickup.bound" for event in events)
    assert not any(event["type"] == "wrapper.strike" for event in events)
    assert next(
        event for event in events if event["type"] == "wrapper.iteration.end"
    )["outcome"] == "all_blocked"
    run_end = next(event for event in events if event["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "all_blocked"
    assert run_end["iterations_run"] == 1


def test_a_blocked_and_unroutable_pool_remains_all_skipped(
    tmp_path, monkeypatch
) -> None:
    """Waiting must not hide a refusal an operator can repair."""
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                blocked_by=BlockedByRead(
                    total_count=1,
                    nodes=(BlockerNode(ref="acme/widgets#93", state="open"),),
                ),
            ),
            _dated(
                31,
                "2026-05-01T00:00:00Z",
                labels=["ready-for-agent", "task-type:not-a-real-key"],
            ),
        ],
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=5))
    )

    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    assert exit_code == loop_module.exit_code_for("all_skipped")
    skip_reasons = [
        event["reason"]
        for event in events
        if event["type"] == "wrapper.pickup.skipped"
    ]
    assert skip_reasons[0] == "blocked_by_open_dependency: acme/widgets#93"
    assert skip_reasons[1].startswith("routing refused:")
    assert next(
        event for event in events if event["type"] == "wrapper.run.end"
    )["outcome"] == "all_skipped"


def test_an_all_unprovable_pool_never_ends_the_run_all_skipped(
    tmp_path, monkeypatch
) -> None:
    """The #542 report: a read that failed proves no refusal (ADR-0047, §3.3.1).

    ``readiness_unprovable`` reports that *no assertion could be read*, so a
    Pool whose every candidate reached it has established nothing about the
    work in it. Ending under ``all_skipped`` would assert the one thing the
    read failed to establish -- contract §10 spends that reason on "I could not
    take any of what there is" -- and would send an operator to repair a
    labelling mistake that may not exist, with ``Readiness.blockers``
    deliberately empty so nothing is even named.

    It ends under ``preflight_failed`` for the reason #541's unreadable Pool
    does: a tracker this Run cannot read is a precondition an operator can
    repair, and the candidates whose readiness could not be read are named.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z", blocked_by=BlockedByRead.unprovable()),
            _dated(31, "2026-05-01T00:00:00Z", blocked_by=BlockedByRead.unprovable()),
        ],
    )

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=5))
    )

    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    # Non-vacuity: the Pool really was non-empty and really did bind nothing.
    assert [e for e in events if e["type"] == "wrapper.afk_ready.collected"][0][
        "issues"
    ] == [7, 31]
    assert not any(e["type"] == "wrapper.pickup.bound" for e in events)
    skip_reasons = [
        e["reason"] for e in events if e["type"] == "wrapper.pickup.skipped"
    ]
    # §3.3.1's reason vocabulary is untouched: the skip still reports the fact.
    assert skip_reasons == ["readiness_unprovable", "readiness_unprovable"]
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] != "all_skipped"
    assert run_end["outcome"] == "preflight_failed"
    assert exit_code == loop_module.exit_code_for("preflight_failed")
    # Terminal on the spot, for the reason every other unbound Pool is.
    assert len([e for e in events if e["type"] == "wrapper.iteration.start"]) == 1


def test_a_candidate_whose_blockers_all_closed_is_admitted_normally(
    tmp_path, monkeypatch
) -> None:
    """Readiness clears itself: nobody edited the issue, its blocker closed."""
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(
                7,
                "2026-01-01T00:00:00Z",
                blocked_by=BlockedByRead(
                    total_count=1,
                    nodes=(BlockerNode(ref="acme/widgets#90", state="closed"),),
                ),
            )
        ],
    )

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    assert [(e["type"], e["issue"]) for e in _pickup_events(tmp_path)] == [
        ("wrapper.pickup.bound", 7),
    ]


def test_the_skip_names_the_ending_that_defeated_the_issue(
    tmp_path, monkeypatch
) -> None:
    """A candidate passed over silently is the starvation ADR-0032 exists to end.

    The skip reason is what tells an operator that the runner *declined* work it
    could have reached, and which ending made it decline — so the record names
    the **Session outcome** rather than merely saying the issue is unavailable.
    """
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    fake_client = loop_module._make_client()
    fake_client._scripted_events = [
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(
                content="nothing to do here.\n<promise>NO MORE TASKS</promise>",
                message_id="m1",
            ),
        )
    ]

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=2,
                max_nmt_strikes=9,
                model="claude-sonnet-5",
                reasoning_effort="low",
            )
        )
    )

    (skip,) = [
        e for e in _pickup_events(tmp_path) if e["type"] == "wrapper.pickup.skipped"
    ]
    assert skip["issue"] == 7
    assert skip["reason"] == "already attempted this Run (no_more_tasks)"


def test_an_advancing_iteration_spends_no_attempt(tmp_path, monkeypatch) -> None:
    """Work that lands is not a failure to advance, however many Iterations it takes.

    The ledger counts *failures to advance*, so an issue that commits every
    Iteration stays fresh and is worked as long as it keeps committing. A
    lifecycle that stepped on every ending would defeat any issue whose work
    honestly takes three Iterations.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_client.on_send = lambda: fake_git.simulate_agent_commit(
        subject="chore: a step forward"
    )

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=3,
                model="claude-sonnet-5",
                reasoning_effort="low",
            )
        )
    )

    assert [
        (e["issue"], e["lifecycle_position"]) for e in _bound_pickups(tmp_path)
    ] == [(7, "fresh"), (7, "fresh"), (7, "fresh")]


def _raise_a_transport_failure() -> None:
    """The harness losing the session — a **Session outcome** of ``crash``.

    Raised from the SDK stub's ``on_send`` hook, which is where a real transport
    failure surfaces: the loop contains it and accounts the Iteration as
    no-progress, and :func:`~git_loopy.session_outcome.resolve_session_outcome`
    reads the crashed termination off it.
    """
    raise ConnectionError("the harness went away mid-send")


def test_a_same_pair_crash_retry_says_it_is_a_retry(tmp_path, monkeypatch) -> None:
    """The two axes, on the ending that moves only one of them (contract §14).

    A crash is evidence about the harness, not about the pair, so the **Routing
    source** stays what it routed with and the pair does not move. What does
    move is the **lifecycle position**: something was spent, and this is the
    second attempt. Reading the position off the **Escalation rung**'s ledger
    could only ever have reported the escalated half of that.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    fake_client.on_send = _raise_a_transport_failure

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=3,
                max_nmt_strikes=9,
                model="claude-sonnet-5",
                reasoning_effort="low",
                escalation_rung=("claude-opus-5", "max"),
            )
        )
    )

    assert [
        (e["issue"], e["model"], e["routing_source"], e["lifecycle_position"])
        for e in _bound_pickups(tmp_path)
    ] == [
        (7, "claude-sonnet-5", "defaulted_no_task_type_label", "fresh"),
        (7, "claude-sonnet-5", "defaulted_no_task_type_label", "retrying"),
    ]


# ---------------------------------------------------------------------------
# The Strike counts issues given up on (#413, ADR-0041)
# ---------------------------------------------------------------------------


def _strikes(tmp_path: Path) -> list[dict[str, Any]]:
    """Every ``wrapper.strike`` this Run charged, in order."""
    return [
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.strike"
    ]


def test_a_no_progress_iteration_charges_no_strike(tmp_path, monkeypatch) -> None:
    """An Iteration is not a thing a Run can give up on (#413, contract §6).

    The issue's first silent stall spends its first attempt and leaves it
    **retrying** — still work the Run is willing to take — so the ceiling is
    untouched. Under the old accounting this Iteration was a Strike on its own.
    """
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    assert (
        asyncio.run(
            loop_module.run(
                RunConfig(issue_source="github", max_iterations=1, max_nmt_strikes=3)
            )
        )
        == 0
    )

    assert _strikes(tmp_path) == []
    assert [
        (e["issue"], e["lifecycle_position"]) for e in _bound_pickups(tmp_path)
    ] == [(7, "fresh")]


def test_one_strike_is_charged_per_issue_given_up_on(tmp_path, monkeypatch) -> None:
    """Exactly one, at the ending that skips the issue — never one per Iteration.

    Two silent stalls defeat #7 (**fresh** → **retrying** → **skipped**), and the
    Strike lands on the second. Every later Iteration passes over the same issue
    as a **Pickup skip** and charges nothing more, because a skipped issue is
    given up on once and stays given up on.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [_dated(7, "2026-01-01T00:00:00Z"), _dated(31, "2026-05-01T00:00:00Z")],
    )

    asyncio.run(
        loop_module.run(
            RunConfig(issue_source="github", max_iterations=4, max_nmt_strikes=9)
        )
    )

    charged = _strikes(tmp_path)
    assert [(s["strikes"], s["outcome"]) for s in charged] == [
        (1, "warn"),
        (2, "warn"),
    ], f"expected one Strike per defeated issue, got {charged}"
    # ...and the Pickups that earned them: each issue worked twice, then skipped.
    assert [
        (e["issue"], e["lifecycle_position"]) for e in _bound_pickups(tmp_path)
    ] == [(7, "fresh"), (7, "retrying"), (31, "fresh"), (31, "retrying")]


def test_a_run_that_gives_up_on_enough_issues_is_stuck(tmp_path, monkeypatch) -> None:
    """`max_nmt_strikes` is how many issues this Run may abandon (#413).

    Two issues, a ceiling of two, and an Agent that declares the **NMT
    sentinel** — an ending taken at its word, so each issue is defeated on its
    first attempt. The second defeat spends the ceiling and the Run ends
    ``stuck`` with the third issue's Iteration never run.
    """
    _wire_multi_issue_github(
        tmp_path,
        monkeypatch,
        [
            _dated(7, "2026-01-01T00:00:00Z"),
            _dated(31, "2026-05-01T00:00:00Z"),
            _dated(44, "2026-06-01T00:00:00Z"),
        ],
    )
    fake_client = loop_module._make_client()
    fake_client._scripted_events = [
        _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(
                content="nothing workable.\n<promise>NO MORE TASKS</promise>",
                message_id="m1",
            ),
        )
    ]

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(issue_source="github", max_iterations=0, max_nmt_strikes=2)
        )
    )

    assert exit_code == loop_module.exit_code_for("stuck")
    assert [(s["strikes"], s["outcome"]) for s in _strikes(tmp_path)] == [
        (1, "warn"),
        (2, "abort"),
    ]
    assert [e["issue"] for e in _bound_pickups(tmp_path)] == [7, 31]
    run_end = next(
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.run.end"
    )
    assert run_end["outcome"] == "stuck"


def test_a_run_whose_every_issue_is_defeated_ends_all_skipped(
    tmp_path, monkeypatch
) -> None:
    """The livelock this outcome exists to end (#413, ADR-0041).

    One issue, an unbounded Iteration cap and a ceiling it never reaches. The
    issue stalls twice and is **skipped**; the very next Iteration walks a Pool
    that still holds it, binds nothing, and — charging nothing, since #413 —
    would re-walk the same Pool forever. It ends the Run instead, under a reason
    that is neither the empty Pool (which would be exit ``0`` and a lie) nor the
    spent ceiling.
    """
    _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(issue_source="github", max_iterations=0, max_nmt_strikes=9)
        )
    )

    assert exit_code == 1
    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] == "all_skipped"
    # Three Iterations: two that worked #7, and the one that could take nothing.
    assert len([e for e in events if e["type"] == "wrapper.iteration.start"]) == 3
    assert [e["issue"] for e in _bound_pickups(tmp_path)] == [7, 7]
    assert [(s["strikes"], s["outcome"]) for s in _strikes(tmp_path)] == [(1, "warn")]


def test_an_empty_pool_still_ends_clean(tmp_path, monkeypatch) -> None:
    """"There is nothing to do" keeps its own exit (#413, contract §10).

    The companion to the case above: ``all_skipped`` must not swallow the
    empty-Pool exit, because a Run that finished the queue succeeded and a
    supervising script reads that off exit ``0``.
    """
    _wire_multi_issue_github(tmp_path, monkeypatch, [])

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=0))
    )

    assert exit_code == 0
    run_end = next(
        json.loads(raw)
        for raw in _log_lines(tmp_path)
        if json.loads(raw)["type"] == "wrapper.run.end"
    )
    assert run_end["outcome"] == "empty_pool"


def test_a_failed_pool_read_never_ends_a_run_as_an_empty_pool(
    tmp_path, monkeypatch
) -> None:
    """A Pool nobody could read is unknown, not empty (#541, contract §2.1).

    A failed Pool read degrades the collection to zero items, which is
    byte-identical to a genuinely finished backlog. Reporting it as one ends an
    unattended Run at exit ``0`` — "there is no work" — over a repository whose
    work the runner simply failed to look at.

    Driven through the PRDs backend because that is the source whose Run *is* a
    plain serial one: its collection is the Run's whole view of the Pool, with
    no Rolling membership cache holding separate evidence, so the Iteration's
    verdict is the Run's verdict and the exit code is readable straight off it.
    The shape under test is the same either way — a failed ``gh issue list`` and
    this unreadable ``prds/`` root both yield ``PoolCollection(complete=False)``
    with no items, which is exactly why the rule lives on the collection rather
    than in one backend.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    # A `prds/` the source refuses to walk: present, but not the directory it
    # resolves to. `PrdsIssueSource.collect_pool` reports that as an incomplete
    # collection rather than raising, so the loop sees zero items and no claim.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "prds").symlink_to(elsewhere, target_is_directory=True)

    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_client = FakeCopilotClient(scripted_events=[])
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="prds", max_iterations=3))
    )

    assert exit_code != 0
    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] != "empty_pool"
    # `preflight_failed` is the reason #438 already spends on "the tracker
    # cannot be read well enough to start"; a read that gives out later takes
    # the same operator action, so it takes the same reason.
    assert run_end["outcome"] == "preflight_failed"
    assert exit_code == 1
    # Terminal on the spot: re-reading a source that just refused would spend
    # the Iteration cap and then exit 0 under `iteration_cap`.
    assert len([e for e in events if e["type"] == "wrapper.iteration.start"]) == 1
    # And no session was ever started for a Pool the Run could not read.
    assert fake_client.created == []


def test_a_failed_gh_issue_list_never_ends_a_serial_run_as_an_empty_pool(
    tmp_path, monkeypatch
) -> None:
    """The #541 report, driven through the read it names (contract §2.1).

    The sibling above proves the rule on the collection; this proves it on the
    exact failure the issue was filed about — ``gh issue list`` refusing inside
    the serial driver's own Pool collection. The distinction matters because
    that refusal is the one an operator actually meets: a ``gh`` that preflight
    cleared, against a host that then rejects the query (a GHES without issue
    dependencies, an expired token, a 502), degrading the collection to zero
    items.

    Preflight passes here on purpose — ``gh repo view`` and ``gh --version``
    both answer. This pins precisely the half of the hazard #438's capability
    gate cannot see, because it lives on the *server* rather than in
    ``gh --version``: a Run reaches its first Iteration, reads nothing, and
    before the fix reported a repository full of triaged work as a finished
    backlog at exit ``0``.
    """
    writers = create_writers(tmp_path)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        # The backlog is *not* empty — #7 is triaged, ready, and waiting. Only
        # the read of it fails, which is the whole point: a Run that reported
        # this as an empty Pool would abandon work it never looked at.
        issues=[_make_issue(7)],
        issue_list_error=gh_module.GhError(
            ["gh", "issue", "list"], 1, "GraphQL: Field 'blockedBy' doesn't exist"
        ),
    )
    denomination = BilledCreditsDenomination()
    serial = loop_module._Loop(
        config=RunConfig(issue_source="github", max_iterations=3),
        release_version=EXPECTED_RELEASE_VERSION,
        git=FakeGitClient(tmp_path),
        prompt_text="be the agent",
        denomination=denomination,
        writers=writers,
        sinks=SinkFanout([]),
        summary=RunSummary(denomination=denomination),
        client=cast(CopilotClient, None),
        skill_preflight=cast(
            Any,
            SimpleNamespace(exposure=None, migration_warning=False, event_payload={}),
        ),
        source=sources_module.GitHubIssueSource(diag=writers.diagnostics, gh=fake_gh),
        diag=writers.diagnostics,
    )

    exit_code = asyncio.run(serial.drive())

    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    # Non-vacuity: preflight really did pass, and the Iteration really did try
    # to read the Pool and come back with nothing.
    collected = [e for e in events if e["type"] == "wrapper.afk_ready.collected"]
    assert collected and collected[0]["issues"] == []
    run_end = next(e for e in events if e["type"] == "wrapper.run.end")
    assert run_end["outcome"] != "empty_pool"
    assert run_end["outcome"] == "preflight_failed"
    assert exit_code != 0
    assert exit_code == 1
    # Terminal on the spot rather than re-asked until the cap: an Iteration cap
    # spent on a refusing source ends at `iteration_cap`, which is exit 0 again.
    assert len([e for e in events if e["type"] == "wrapper.iteration.start"]) == 1


# ---------------------------------------------------------------------------
# The Task-type classifier at Pickup (#409, ADR-0029)
# ---------------------------------------------------------------------------


class _RecordingTaskTypeLabelClient:
    """The tracker seam the classifier writes through, without a tracker."""

    def __init__(self, *, fail: bool = False) -> None:
        self.applied: list[tuple[int, str]] = []
        self.reads: list[int] = []
        self._fail = fail

    def read_issue_labels(self, number: int) -> list[str]:
        self.reads.append(number)
        return ["ready-for-agent"]

    def apply_issue_label(self, number: int, spec: Any) -> None:
        if self._fail:
            raise RuntimeError("gh: issue edit failed")
        self.applied.append((number, spec.name))


class _ClassifyingCopilotClient(FakeCopilotClient):
    """A harness that answers the classifier's prompt and stays quiet otherwise.

    Branching on ``model`` rather than on call order is the point: the
    classifying session is the one created on the **classifier pair**, so a test
    that gets its answer has already proved the pair reached the SDK.
    """

    def __init__(self, *, answer: str, classifier_model: str) -> None:
        super().__init__(scripted_events=[])
        self._answer = answer
        self._classifier_model = classifier_model
        self.models: list[str | None] = []

    async def create_session(self, **kwargs: Any) -> FakeCopilotSession:
        model = kwargs.get("model")
        self.models.append(model)
        self._scripted_events = (
            [
                _sdk_event(
                    SessionEventType.ASSISTANT_MESSAGE,
                    AssistantMessageData(content=self._answer, message_id="c1"),
                )
            ]
            if model == self._classifier_model
            else []
        )
        return await super().create_session(**kwargs)


def _wire_classifier_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answer: str = "<task-type>bugfix</task-type>\n<bump-class>none</bump-class>",
    classifier_model: str = "gpt-5-mini",
    labels: list[str] | None = None,
    label_client: _RecordingTaskTypeLabelClient | None = None,
) -> tuple[_ClassifyingCopilotClient, _RecordingTaskTypeLabelClient]:
    """One unlabelled issue, one scriptable harness, one watchable tracker write."""
    _write_runnable_feedback_loop(tmp_path)
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    monkeypatch.setattr(
        loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path)
    )
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_dated(7, "2026-01-01T00:00:00Z", labels=labels)],
        ),
    )
    fake_client = _ClassifyingCopilotClient(
        answer=answer, classifier_model=classifier_model
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    tracker = label_client if label_client is not None else _RecordingTaskTypeLabelClient()
    monkeypatch.setattr(
        loop_module, "_make_task_type_label_client", lambda: tracker
    )
    return fake_client, tracker


def _cheap_staircase(model: str = "gpt-5-mini") -> PriceStaircase:
    """A staircase whose cheapest rung is ``model`` and whose next one is dear."""
    return PriceStaircase(
        candidates=(
            Candidate(model=model, effort=None, multiplier=0.33),
            Candidate(model="claude-opus-5", effort="max", multiplier=10.0),
        )
    )


def _classifier_config(**overrides: Any) -> RunConfig:
    settings: dict[str, Any] = {
        "issue_source": "github",
        "max_iterations": 1,
        "model": "claude-sonnet-5",
        "reasoning_effort": "low",
        "routing": {"bugfix": ("claude-opus-4.7", "high")},
    }
    settings.update(overrides)
    return RunConfig(**settings)


def test_an_unlabelled_issue_is_classified_and_labelled_at_pickup(
    tmp_path, monkeypatch
) -> None:
    """The whole ticket (#409): no human labelled #7, and it routes as a bugfix."""
    fake_client, tracker = _wire_classifier_run(tmp_path, monkeypatch)

    exit_code = asyncio.run(
        loop_module.run(_classifier_config(), staircase=_cheap_staircase())
    )

    assert exit_code == 0
    assert tracker.applied == [(7, "task-type:bugfix"), (7, "semver:none")]
    assert [
        (e["task_type_keys"], e["model"], e["effort"], e["routing_source"])
        for e in _bound_pickups(tmp_path)
    ] == [(["bugfix"], "claude-opus-4.7", "high", "routed")]


def test_an_unclassified_bump_class_is_inferred_and_written_at_pickup(
    tmp_path, monkeypatch
) -> None:
    """The Bump class is inferred by the unattended Pickup before work starts."""
    fake_client, tracker = _wire_classifier_run(
        tmp_path,
        monkeypatch,
        answer="<task-type>bugfix</task-type>\n<bump-class>minor</bump-class>",
    )

    exit_code = asyncio.run(
        loop_module.run(_classifier_config(), staircase=_cheap_staircase())
    )

    assert exit_code == 0
    assert (7, "semver:minor") in tracker.applied
    assert fake_client.models.count("gpt-5-mini") == 2


def test_the_iteration_runs_on_the_pair_the_inferred_type_routed_to(
    tmp_path, monkeypatch
) -> None:
    """A Task type that changed no session is a Task type that changed nothing."""
    fake_client, _ = _wire_classifier_run(tmp_path, monkeypatch)

    asyncio.run(loop_module.run(_classifier_config(), staircase=_cheap_staircase()))

    # The classifying session first, on the cheapest rung; then the work, on the
    # pair `[routing]` names for `bugfix` — never the run-wide `claude-sonnet-5`.
    assert fake_client.models == ["gpt-5-mini", "gpt-5-mini", "claude-opus-4.7"]


def test_the_classifier_never_borrows_the_run_wide_default(
    tmp_path, monkeypatch
) -> None:
    """ADR-0029's central refusal, asserted where it would actually be broken."""
    fake_client, tracker = _wire_classifier_run(
        tmp_path, monkeypatch, classifier_model="claude-sonnet-5"
    )

    # No staircase and no configured classifier pair: there is no measured
    # cheapest rung, so nothing is classified. The run-wide default is *right
    # there* and is not reached for.
    asyncio.run(loop_module.run(_classifier_config()))

    assert tracker.applied == []
    assert fake_client.models == ["claude-sonnet-5"]
    assert [e["routing_source"] for e in _bound_pickups(tmp_path)] == [
        "defaulted_no_task_type_label"
    ]


def test_an_already_labelled_issue_spends_nothing_at_pickup(
    tmp_path, monkeypatch
) -> None:
    """Inference is a one-off because the label persists, not because of a cache."""
    fake_client, tracker = _wire_classifier_run(
        tmp_path,
        monkeypatch,
        labels=["ready-for-agent", "task-type:bugfix", "semver:none"],
    )

    asyncio.run(loop_module.run(_classifier_config(), staircase=_cheap_staircase()))

    assert fake_client.models == ["claude-opus-4.7"]
    assert tracker.applied == []
    assert tracker.reads == []


def test_a_refused_tracker_write_still_routes_this_iteration(
    tmp_path, monkeypatch
) -> None:
    """The write saves the *next* Run the inference; losing it loses nothing else."""
    fake_client, _ = _wire_classifier_run(
        tmp_path,
        monkeypatch,
        label_client=_RecordingTaskTypeLabelClient(fail=True),
    )

    exit_code = asyncio.run(
        loop_module.run(_classifier_config(), staircase=_cheap_staircase())
    )

    assert exit_code == 0
    assert fake_client.models == ["gpt-5-mini", "gpt-5-mini", "claude-opus-4.7"]


def test_a_classifier_written_label_is_indistinguishable_from_a_hand_written_one(
    tmp_path, monkeypatch
) -> None:
    """ADR-0029 spent label provenance; this is the property it bought.

    The two Runs differ only in *who* put ``task-type:bugfix`` on #7 — a human
    before the Run, or the classifier during its Pickup. Everything the Pickup
    publishes about the pair is identical, because by the time routing reads the
    label there is nothing left that could tell them apart.
    """

    def pickup_for(at: Path, *, prelabelled: bool) -> dict[str, Any]:
        at.mkdir()
        _wire_classifier_run(
            at,
            monkeypatch,
            labels=["ready-for-agent", "task-type:bugfix"] if prelabelled else None,
        )
        asyncio.run(
            loop_module.run(_classifier_config(), staircase=_cheap_staircase())
        )
        bound = _bound_pickups(at)[0]
        return {
            key: value
            for key, value in bound.items()
            if key not in ("ts", "run_id")
        }

    assert pickup_for(tmp_path / "human", prelabelled=True) == pickup_for(
        tmp_path / "inferred", prelabelled=False
    )


def test_a_proposal_outside_the_taxonomy_is_refused_and_costs_the_iteration_nothing(
    tmp_path, monkeypatch
) -> None:
    """An unattended writer plus ``gh label create`` makes an invented key permanent."""
    fake_client, tracker = _wire_classifier_run(
        tmp_path, monkeypatch, answer="<task-type>refactor</task-type>"
    )

    exit_code = asyncio.run(
        loop_module.run(_classifier_config(), staircase=_cheap_staircase())
    )

    assert exit_code == 0
    assert tracker.applied == []
    assert [e["routing_source"] for e in _bound_pickups(tmp_path)] == [
        "defaulted_no_task_type_label"
    ]


def test_a_classifier_that_answers_nothing_leaves_the_run_where_it_was(
    tmp_path, monkeypatch
) -> None:
    fake_client, tracker = _wire_classifier_run(
        tmp_path, monkeypatch, answer="I had a look and I am not sure."
    )

    exit_code = asyncio.run(
        loop_module.run(_classifier_config(), staircase=_cheap_staircase())
    )

    assert exit_code == 0
    assert tracker.applied == []
    assert [e["model"] for e in _bound_pickups(tmp_path)] == ["claude-sonnet-5"]


def test_classification_ticks_no_strike_of_its_own(tmp_path, monkeypatch) -> None:
    """A classification is not an **Iteration**, so it allocates none and strikes none.

    Two Runs over the same two unproductive Iterations, one classifying and one
    inert: the Strike ledger is identical. A classifier that could strike out
    would end an unattended overnight Run without doing any work.
    """

    def strikes_for(at: Path, *, staircase: PriceStaircase | None) -> list[int]:
        at.mkdir()
        _wire_classifier_run(at, monkeypatch)
        asyncio.run(
            loop_module.run(
                _classifier_config(max_iterations=2, max_nmt_strikes=9),
                staircase=staircase,
            )
        )
        return [
            json.loads(raw)["strikes"]
            for raw in _log_lines(at)
            if json.loads(raw)["type"] == "wrapper.strike"
        ]

    assert strikes_for(tmp_path / "classifying", staircase=_cheap_staircase()) == (
        strikes_for(tmp_path / "inert", staircase=None)
    )


def test_the_classifying_session_occupies_no_iteration_row(
    tmp_path, monkeypatch
) -> None:
    """One Iteration is one summary row, whatever the Run spent alongside it."""
    _wire_classifier_run(tmp_path, monkeypatch)

    asyncio.run(loop_module.run(_classifier_config(), staircase=_cheap_staircase()))

    summary = json.loads(
        next((tmp_path / ".git-loopy" / "runs").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert len(summary["iterations"]) == 1


def test_a_configured_classifier_pair_needs_no_staircase(tmp_path, monkeypatch) -> None:
    """The operator's own knob, which is what makes the prior overridable."""
    fake_client, tracker = _wire_classifier_run(
        tmp_path, monkeypatch, classifier_model="gemini-3.5-flash"
    )

    asyncio.run(
        loop_module.run(
            _classifier_config(
                classifier_model="gemini-3.5-flash", classifier_effort="low"
            )
        )
    )

    assert tracker.applied == [(7, "task-type:bugfix"), (7, "semver:none")]
    assert fake_client.models == [
        "gemini-3.5-flash",
        "gemini-3.5-flash",
        "claude-opus-4.7",
    ]


# ---------------------------------------------------------------------------
# The Run readback (#410) — driven through a real Run, not a new seam
# ---------------------------------------------------------------------------


def _run_start_event(tmp_path: Path) -> dict[str, Any]:
    """The one ``wrapper.run.start`` record this Run wrote."""
    (start,) = [
        event
        for raw in _log_lines(tmp_path)
        if (event := json.loads(raw))["type"] == "wrapper.run.start"
    ]
    return start


def test_run_start_reads_back_the_routing_table_this_run_parsed(
    tmp_path, monkeypatch
) -> None:
    """No validator for ``[routing]`` can exist, so the readback is the validation.

    The keys travel themselves, never a count of them: a count cannot reveal
    that a table an operator believes covers seven **Task types** covers two.
    """
    _wire_multi_issue_github(tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")])

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=1,
                routing={
                    "planning": ("claude-opus-5", "max"),
                    "test": ("gpt-5-mini", "max"),
                },
            )
        )
    )

    start = _run_start_event(tmp_path)
    assert [route["key"] for route in start["routes"]] == ["planning", "test"]
    assert start["unconfigured_task_type_keys"] == [
        "review",
        "implementation",
        "docs",
        "chore",
        "bugfix",
    ]


def test_run_start_gate_checks_a_route_the_run_never_exercises(
    tmp_path, monkeypatch
) -> None:
    """The point of gating at Run start: this Run works one unlabelled issue.

    ``task-type:test`` is never picked up, so nothing else in the Run ever
    resolves that route — and ``gpt-5-mini`` accepting no ``max`` would stay
    unknown until the first issue that carried the label.
    """
    _wire_multi_issue_github(tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")])

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=1,
                routing={"test": ("gpt-5-mini", "max")},
            )
        )
    )

    (route,) = _run_start_event(tmp_path)["routes"]
    assert route["configured_effort"] == "max"
    assert route["effort"] is None
    assert route["gate_warnings"] == ["dropped_effort"]


def test_run_start_names_the_escalation_rung_and_gate_checks_it(
    tmp_path, monkeypatch
) -> None:
    """The rung is otherwise first gated at a stalled issue's next Pickup."""
    _wire_multi_issue_github(tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")])

    asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=1,
                escalation_rung=("claude-haiku-4.5", "max"),
            )
        )
    )

    rung = _run_start_event(tmp_path)["escalation_rung"]
    assert rung["model"] == "claude-haiku-4.5"
    assert rung["configured_effort"] == "max"
    assert rung["gate_warnings"] == ["incapable_model"]


def test_run_start_names_the_harness_the_roster_describes(
    tmp_path, monkeypatch
) -> None:
    """ADR-0019: the divergence is reported at Run start, or it is reported nowhere."""
    from git_loopy.config import MODEL_ROSTER_CLI_VERSION
    from git_loopy.run_readback import spawned_harness_version

    _wire_multi_issue_github(tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")])

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    start = _run_start_event(tmp_path)
    assert start["harness_version"] == spawned_harness_version()
    assert start["roster_cli_version"] == MODEL_ROSTER_CLI_VERSION


def test_a_run_that_configured_nothing_still_reads_its_defaults_back(
    tmp_path, monkeypatch
) -> None:
    """The block is unconditional, so the ordinary invocation is described too.

    An operator whose Run "did nothing different" has to be able to see the
    triple it ran on; a block that appeared only when something was configured
    would be silent in exactly the case that puzzles them.
    """
    _wire_multi_issue_github(tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")])

    asyncio.run(loop_module.run(RunConfig(issue_source="github", max_iterations=1)))

    start = _run_start_event(tmp_path)
    assert start["routes"] == []
    assert start["escalation_rung"] is None
    assert start["context_tier"] == "default"
    assert start["routing_suppressed"] is False


# ---------------------------------------------------------------------------
# A complete Static route, through actual execution (#560, ADR-0057).
# ---------------------------------------------------------------------------


def _harness(monkeypatch, *models) -> None:
    """Answer the Run's capability refresh with a scripted harness listing.

    ``models`` are ``(id, efforts, long_context)`` triples, where ``efforts`` of
    ``None`` is the harness's own spelling of *no effort dial*. Passing no
    models at all scripts an unreadable listing.
    """
    listing = [
        SimpleNamespace(
            id=identifier,
            name=identifier,
            policy=SimpleNamespace(state="enabled", terms=""),
            billing=SimpleNamespace(
                multiplier=1.0,
                token_prices=SimpleNamespace(
                    long_context=SimpleNamespace(max_prompt_tokens=400_000)
                    if long_context
                    else None
                ),
            ),
            supported_reasoning_efforts=efforts,
            default_reasoning_effort=(efforts or [None])[0],
        )
        for identifier, efforts, long_context in models
    ]

    async def _refresh(**_kwargs: Any) -> Any:
        if not listing:
            return None
        return static_route.HarnessCapabilities.from_listing(listing)

    monkeypatch.setattr(loop_module, "_refresh_harness_capabilities", _refresh)


def _static_config(**overrides: Any) -> RunConfig:
    base: dict[str, Any] = dict(
        issue_source="github",
        max_iterations=1,
        route_policy=RoutePolicy.STATIC,
        model="gpt-5.6-terra",
        reasoning_effort="high",
        context_tier="long_context",
        verbosity=0,
        render_reasoning=False,
    )
    base.update(overrides)
    return RunConfig(**base)


def test_a_static_route_reaches_the_serial_work_sessions_own_arguments(
    tmp_path, monkeypatch
) -> None:
    """The selected triple is what ``create_session`` is actually called with.

    Not the resolution, not the readback, not the Queue cell: the request the
    issue-owning session is opened with. A route that agrees everywhere except
    here is a route that did not take effect.
    """
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))

    exit_code = asyncio.run(loop_module.run(_static_config()))

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert fake_client.create_calls, "no work session was opened"
    call = fake_client.create_calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["reasoning_effort"] == "high"
    assert call["context_tier"] == "long_context"


def test_an_effort_not_configurable_model_is_sent_no_effort_argument(
    tmp_path, monkeypatch
) -> None:
    """``None`` is *no argument*; the SDK omits ``reasoningEffort`` for it.

    Distinct from the effort **value** ``none``, which is an argument the dial
    accepts and which the next test sends.
    """
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("no-dial", None, False))

    exit_code = asyncio.run(
        loop_module.run(
            _static_config(
                model="no-dial", reasoning_effort=None, context_tier="default"
            )
        )
    )

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert fake_client.create_calls[0]["reasoning_effort"] is None


def test_the_effort_value_none_is_sent_as_a_value(tmp_path, monkeypatch) -> None:
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("dialled", ["none", "high"], False))

    exit_code = asyncio.run(
        loop_module.run(
            _static_config(
                model="dialled", reasoning_effort="none", context_tier="default"
            )
        )
    )

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert fake_client.create_calls[0]["reasoning_effort"] == "none"


def test_an_effort_the_harness_refuses_stops_the_run_before_any_session(
    tmp_path, monkeypatch, capsys
) -> None:
    """Refusal, not rescue: the legacy gate would have dropped this effort."""
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("no-dial", None, False))

    exit_code = asyncio.run(
        loop_module.run(
            _static_config(
                model="no-dial", reasoning_effort="high", context_tier="default"
            )
        )
    )

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == [], "work started on a refused route"
    assert "no-dial" in capsys.readouterr().err


def test_a_tier_the_harness_does_not_offer_stops_the_run_before_any_session(
    tmp_path, monkeypatch, capsys
) -> None:
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["high"], False))

    exit_code = asyncio.run(loop_module.run(_static_config()))

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == []
    assert "long_context" in capsys.readouterr().err


def test_an_unreadable_harness_listing_stops_the_run_rather_than_guessing(
    tmp_path, monkeypatch, capsys
) -> None:
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch)

    exit_code = asyncio.run(loop_module.run(_static_config()))

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == []
    assert "could not be asked" in capsys.readouterr().err


def test_an_unreadable_listing_records_why_it_could_not_be_read(
    tmp_path, monkeypatch, capsys
) -> None:
    """The refusal names the likeliest cause; the diagnostics name the real one.

    ``unverifiable`` is one verdict over many causes — an unauthenticated CLI,
    an SDK schema change, a bad call signature — and the refusal can only
    suggest the first. The Run refuses before ``wrapper.run.start``, so its
    diagnostics are the only place the observed failure can still be found.
    """
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)

    async def _fetch() -> Any:
        raise RuntimeError("copilot server never answered")

    monkeypatch.setattr(static_route, "default_capability_fetch", lambda: _fetch)

    exit_code = asyncio.run(loop_module.run(_static_config()))

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == []
    err = capsys.readouterr().err
    assert "copilot server never answered" in err, (
        "the observed capability-read failure reached no diagnostic"
    )
    assert "could not be asked" in err


def test_a_static_route_refuses_a_placement_whose_harness_is_not_this_one(
    monkeypatch,
) -> None:
    """A remote **Execution host** authenticates as itself, so this read is not its read.

    ADR-0057 requires the verdict to come from *the authenticated harness the
    Run actually uses*, and names "another CLI installation" as one of the
    things that is explicitly not it. A ``github-actions`` contribution opens
    its session on a GitHub-hosted runner under the built-in token — a
    different installation, a different account, a different ``policy.state``.
    Verifying the operator's local listing and calling it that placement's
    answer is the one outcome the criterion rules out, so the combination is
    refused before work rather than approved against the wrong harness.

    Driven at the preflight seam rather than through ``run()`` because the
    Actions host is unpreparable on a bare fixture repository and would refuse
    for its *own* reason first, which would let this rule be absent and the
    test still pass.
    """
    asked: list[int] = []

    async def _refresh(**_kwargs: Any) -> Any:
        asked.append(1)
        return static_route.HarnessCapabilities(models={})

    monkeypatch.setattr(loop_module, "_refresh_harness_capabilities", _refresh)

    refusal = asyncio.run(
        loop_module._static_route_preflight(
            _static_config(execution_host="github-actions"), warn=lambda _message: None
        )
    )

    assert refusal is not None, "a remote placement verified against the local harness"
    assert "github-actions" in refusal
    assert asked == [], "the orchestrator's own harness was read for a remote placement"


def test_a_local_placement_is_the_one_a_static_route_can_verify(monkeypatch) -> None:
    """The rule above refuses a *placement*, not the policy — ``local`` still runs."""

    async def _refresh(**_kwargs: Any) -> Any:
        return static_route.HarnessCapabilities.from_listing(
            [
                SimpleNamespace(
                    id="gpt-5.6-terra",
                    name="gpt-5.6-terra",
                    policy=SimpleNamespace(state="enabled", terms=""),
                    billing=SimpleNamespace(
                        multiplier=1.0,
                        token_prices=SimpleNamespace(
                            long_context=SimpleNamespace(max_prompt_tokens=400_000)
                        ),
                    ),
                    supported_reasoning_efforts=["high"],
                    default_reasoning_effort="high",
                )
            ]
        )

    monkeypatch.setattr(loop_module, "_refresh_harness_capabilities", _refresh)

    assert (
        asyncio.run(
            loop_module._static_route_preflight(
                _static_config(), warn=lambda _message: None
            )
        )
        is None
    )


def test_every_configured_static_route_is_checked_not_just_the_default(
    tmp_path, monkeypatch, capsys
) -> None:
    """A ``[routing]`` entry no issue in this Pool carries is still refused.

    Checking only the route this Pickup resolved would leave a broken entry to
    be discovered by the Iteration that finally picks a ``docs`` issue up — the
    exact "dead config that costs an Iteration to discover" the **Run readback**
    exists to avoid, except with work already spent.
    """
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))

    exit_code = asyncio.run(
        loop_module.run(_static_config(routing={"docs": ("ghost-model", "low")}))
    )

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == []
    err = capsys.readouterr().err
    assert "ghost-model" in err and "docs" in err


def test_an_unselected_policy_asks_the_harness_nothing(tmp_path, monkeypatch) -> None:
    """The legacy Run pays for no capability round trip and refuses nothing."""
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    asked: list[int] = []

    async def _refresh(**_kwargs: Any) -> Any:
        asked.append(1)
        return None

    monkeypatch.setattr(loop_module, "_refresh_harness_capabilities", _refresh)

    exit_code = asyncio.run(
        loop_module.run(
            RunConfig(
                issue_source="github",
                max_iterations=1,
                model="claude-haiku-4.5",
                reasoning_effort="high",
                verbosity=0,
                render_reasoning=False,
            )
        )
    )

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert asked == [], "an unselected policy reached for harness capabilities"
    assert fake_client.create_calls, "no work session was opened"


def test_the_record_the_dashboard_reads_names_the_static_route_it_verified(
    tmp_path, monkeypatch
) -> None:
    """One resolution feeds the session, the Event, and the Dashboard (#560).

    The **Dashboard** renders its Route cell from ``wrapper.pickup.bound``'s own
    ``model``/``effort``/``context_tier``, so the readback agreeing with the
    session is not a second projection to keep in step — it is the same
    **Routing resolution** arriving in two places. What this pins is that the
    resolution reaching the wire is the *selected* triple: the roster would have
    dropped ``max`` from this model and downgraded the tier, and under a Static
    route it was never asked.
    """
    _write_runnable_feedback_loop(tmp_path)
    _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5-mini", ["low", "medium", "max"], True))

    assert (
        asyncio.run(
            loop_module.run(
                _static_config(
                    model="gpt-5-mini",
                    reasoning_effort="max",
                    context_tier="long_context",
                )
            )
        )
        == 0
    )

    (bound,) = _bound_pickups(tmp_path)
    assert bound["model"] == "gpt-5-mini"
    assert bound["effort"] == "max"
    assert bound["context_tier"] == "long_context"
    assert bound["gate_warnings"] == []


def test_a_static_route_stays_put_when_an_iteration_stalls(
    tmp_path, monkeypatch
) -> None:
    """A Static route is fixed for the Agent, and a shipped rung is not consent.

    ADR-0057: the route stays fixed across every permitted retry unless the
    *operator* configured an escalation rung. Whether the kit's own default rung
    counts as consent is decided where Config is resolved, and is pinned there
    (``test_config_resolver``); what this pins is the execution half — that a
    Run holding no rung promotes nothing on a stall. The attempt and **Strike**
    accounting is untouched either way: the same issue is picked up twice, on
    the same pair, and the second Pickup is the retry it always was.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))

    asyncio.run(
        loop_module.run(_static_config(max_iterations=2, verbosity=0))
    )

    assert [
        (e["issue"], e["model"], e["effort"], e["routing_source"])
        for e in _bound_pickups(tmp_path)
    ] == [
        (7, "gpt-5.6-terra", "high", "defaulted_no_task_type_label"),
        (7, "gpt-5.6-terra", "high", "defaulted_no_task_type_label"),
    ]
    assert [
        (call["model"], call["reasoning_effort"], call["context_tier"])
        for call in fake_client.create_calls
    ] == [
        ("gpt-5.6-terra", "high", "long_context"),
        ("gpt-5.6-terra", "high", "long_context"),
    ]


def test_a_static_route_still_escalates_where_the_operator_asked_for_it(
    tmp_path, monkeypatch
) -> None:
    """Explicit consent is honoured, and the rung is verified like any route.

    The refusal-before-work rule reaches the rung too: a rung nothing checked
    until an issue stalled would refuse mid-Run, which is the one moment the
    operator is least able to act on it.
    """
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    _harness(
        monkeypatch,
        ("gpt-5.6-terra", ["low", "high"], True),
        ("claude-opus-5", ["high", "max"], True),
    )

    asyncio.run(
        loop_module.run(
            _static_config(
                max_iterations=2, escalation_rung=("claude-opus-5", "max")
            )
        )
    )

    assert [
        (call["model"], call["reasoning_effort"]) for call in fake_client.create_calls
    ] == [("gpt-5.6-terra", "high"), ("claude-opus-5", "max")]


def test_a_rung_the_harness_refuses_stops_the_run_before_any_work(
    tmp_path, monkeypatch
) -> None:
    fake_client, _ = _wire_multi_issue_github(
        tmp_path, monkeypatch, [_dated(7, "2026-01-01T00:00:00Z")]
    )
    _harness(
        monkeypatch,
        ("gpt-5.6-terra", ["low", "high"], True),
        ("claude-opus-5", ["high"], True),
    )

    exit_code = asyncio.run(
        loop_module.run(_static_config(escalation_rung=("claude-opus-5", "max")))
    )

    assert exit_code == 1
    assert fake_client.create_calls == []


# --- Dynamic routing: the Run boundary (#561, ADR-0057) ---------------------


def _dynamic_config(**overrides: Any) -> RunConfig:
    base: dict[str, Any] = dict(
        issue_source="github",
        max_iterations=1,
        route_policy=RoutePolicy.DYNAMIC,
        model="gpt-5.6-terra",
        reasoning_effort="high",
        routing_deadline_seconds=30.0,
        routing_credit_allowance=Decimal("2.5"),
        selector_concurrency=1,
        route_associations={"aa-terra": "gpt-5.6-terra@high"},
        verbosity=0,
        render_reasoning=False,
    )
    base.update(overrides)
    return RunConfig(**base)


def test_dynamic_routing_refuses_before_work_when_a_prerequisite_is_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    """"Missing prerequisites start no dynamic work" is a preflight, not a Pickup.

    ADR-0057 makes the deadline, the routing-credit allowance, the selector
    concurrency and the operator's own Artificial Analysis authorization
    *prerequisites*: bounds the operator agreed to rather than defaults the
    Runner may invent. Discovering a missing one at the first Pickup would mean
    the Run had already opened a session under a route nobody could have
    elected, so the whole configuration is checked before any work — the same
    place and for the same reason a Static route is (#560).
    """
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")

    exit_code = asyncio.run(
        loop_module.run(_dynamic_config(routing_deadline_seconds=None))
    )

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == [], "dynamic work started without its bounds"
    err = capsys.readouterr().err
    assert "routing_deadline_seconds" in err


def test_dynamic_routing_never_echoes_the_key_it_refuses_for(
    tmp_path, monkeypatch, capsys
) -> None:
    """An absent authorization is named by its variable, never by its value."""
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)

    assert asyncio.run(loop_module.run(_dynamic_config())) == 1
    assert fake_client.create_calls == []
    err = capsys.readouterr().err
    assert dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV in err


def test_dynamic_routing_refuses_a_placement_whose_harness_is_not_this_one(
    monkeypatch,
) -> None:
    """The selector would *elect* from a listing the runner never sees.

    The Static route's placement rule (#560) applies to this policy for a
    sharper reason: a mis-verified Static route at least ran the pair the
    operator wrote down, while a Dynamic route elected from the wrong
    installation's listing is a model the GitHub-hosted runner may have no
    access to at all.

    Driven at the preflight seam rather than through ``run()`` because the
    Actions host is unpreparable on a bare fixture repository and would refuse
    for its *own* reason first.
    """
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")

    _prerequisites, refusal = loop_module._dynamic_route_preflight(
        _dynamic_config(execution_host="github-actions"), os.environ
    )

    assert refusal is not None, "a remote placement elected from the local listing"
    assert "github-actions" in refusal


def test_an_unselected_policy_resolves_no_dynamic_prerequisites(monkeypatch) -> None:
    """The legacy Run pays nothing for a policy it did not select."""
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)

    assert loop_module._dynamic_route_preflight(
        RunConfig(issue_source="github", max_iterations=1), os.environ
    ) == (None, None)


def test_a_dynamic_run_verifies_the_routing_entries_that_still_win(
    tmp_path, monkeypatch, capsys
) -> None:
    """A ``[routing]`` entry outranks the selector, so a dead one still refuses.

    AC5 keeps a Static route ahead of the **Route selector**, which makes a
    ``[routing]`` entry the harness refuses exactly as dead under this policy as
    it is under a Static one — and dead in a way no amount of live evidence can
    rescue, because the selector is never asked about that Task type.
    """
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")

    exit_code = asyncio.run(
        loop_module.run(_dynamic_config(routing={"docs": ("ghost-model", "low")}))
    )

    assert exit_code == 1, f"expected exit 1, got {exit_code}"
    assert fake_client.create_calls == []
    err = capsys.readouterr().err
    assert "ghost-model" in err and "docs" in err


def test_a_dynamic_run_does_not_refuse_a_default_the_selector_replaces(
    monkeypatch,
) -> None:
    """The run-wide default is not a route this Run can resolve to.

    Every Task type the ``[routing]`` table does not cover goes to the
    selector, and an unavailable selector refuses rather than falling back — so
    the default never runs. Verifying it would refuse the whole Run over a pair
    the operator never asked to use, most sharply for the kit's own built-in
    default on an account that does not carry it.
    """
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))

    named = {
        name for name, _route in loop_module._configured_static_routes(
            _dynamic_config(model="not-in-any-listing", reasoning_effort=None)
        )
    }

    assert named == set(), f"a dynamic Run gated a route it cannot take: {named}"


def test_an_explicit_pin_is_still_verified_under_a_dynamic_policy(
    monkeypatch,
) -> None:
    """A flag or env pin suppresses routing, so the default *is* the route."""
    named = {
        name for name, _route in loop_module._configured_static_routes(
            _dynamic_config(routing_suppressed=True)
        )
    }

    assert named == {"the run-wide default"}


def _aa_payload(*rows: dict[str, Any]) -> bytes:
    return json.dumps(
        {"data": list(rows), "prompt_options": {"parallel_queries": 1}}
    ).encode("utf-8")


def _aa_row(identifier: str, index: float, speed: float) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": identifier,
        "slug": identifier,
        "evaluations": {"artificial_analysis_intelligence_index": index},
        "median_output_tokens_per_second": speed,
    }


def _wire_dynamic_ports(
    monkeypatch,
    *,
    rows: tuple[dict[str, Any], ...],
    answer: Callable[[Any], Any] | None,
    listing: tuple[SimpleNamespace, ...],
    evidence_delay: float = 0.0,
) -> dict[str, list[Any]]:
    """Substitute the two ports that reach the network, and nothing else.

    AC13's "injected external ports": the **Route selector** is a real
    ``DynamicRouter`` making real decisions over scripted inputs, so what the
    test exercises is the Run's own boundary rather than a stand-in for it.
    """
    spied: dict[str, list[Any]] = {"assessments": [], "evidence": 0}

    async def _fetch(method: str, url: str, headers: dict[str, str]) -> object:
        spied["evidence"] += 1
        if evidence_delay:
            # A real round-trip suspends, which is the only condition under
            # which two concurrent reads *can* be shared. A port that answers
            # without ever yielding makes every caller look sequential.
            await asyncio.sleep(evidence_delay)
        return _aa_payload(*rows)

    async def _capabilities() -> Any:
        return dynamic_route.FreshHarnessCapabilities(
            retrieved_at=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc),
            capabilities=static_route.HarnessCapabilities.from_listing(listing),
            tier_capacities={
                (model.id, "default"): 400_000 for model in listing
            },
        )

    async def _assess(selector: Any, request: Any) -> Any:
        spied["assessments"].append((selector, request))
        output = None if answer is None else answer(request)
        if inspect.isawaitable(output):
            output = await output
        return dynamic_route.SelectorCallResult(
            output=output,
            routing_credits=Decimal("0.25"),
        )

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", _fetch)
    monkeypatch.setattr(loop_module, "_fetch_harness_evidence", _capabilities)

    # Unwrap any factory a previous call in this test already installed, so a
    # second Run's assessments are counted against its own spy rather than
    # against the first Run's closure (#565's multi-Run tests).
    real_factory = getattr(
        loop_module._make_dynamic_router, "_real_factory", loop_module._make_dynamic_router
    )

    def _factory(prerequisites, *, selector_assess, recorder):
        return real_factory(
            prerequisites, selector_assess=_assess, recorder=recorder
        )

    _factory._real_factory = real_factory  # type: ignore[attr-defined]
    monkeypatch.setattr(loop_module, "_make_dynamic_router", _factory)
    return spied


def _elects(model: str) -> Callable[[Any], str]:
    """Answer as a selector that picked ``model`` off the list it was handed.

    The candidate identity is a digest the router mints, so a scripted answer
    has to read it out of the request rather than spell it — which is the same
    constraint a real selector is under, and exactly why the identity is a
    digest.

    It also obeys the one rule a later attempt adds (#562): re-electing a
    configuration a previous attempt ran to the end and solved nothing on
    carries a ``repeat_justification``, and electing anything else does not.
    Scripting an answer that ignored the rule would test the parse seam's
    refusal rather than the Run's behaviour on a well-formed one.
    """

    def _answer(request: Any) -> str:
        (chosen,) = [
            candidate
            for candidate in request.candidates
            if candidate.model == model
        ]
        answer = {
            "candidate_identity": chosen.stable_identity,
            "summary": "strongest verified index for this work",
        }
        if any(
            attempt.capability_evidence
            and attempt.configuration
            == (chosen.model, chosen.reasoning_effort, chosen.context_tier)
            for attempt in request.prior_attempts
        ):
            answer["repeat_justification"] = (
                "still the strongest evidenced eligible configuration"
            )
        return json.dumps(answer)

    return _answer


def _listed_model(identifier: str, efforts: list[str] | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        name=identifier,
        policy=SimpleNamespace(state="enabled", terms=""),
        billing=SimpleNamespace(
            multiplier=1.0,
            token_prices=SimpleNamespace(
                max_prompt_tokens=400_000, long_context=None
            ),
        ),
        supported_reasoning_efforts=efforts,
        default_reasoning_effort=(efforts or [None])[0],
    )


def _dynamic_run(tmp_path, monkeypatch, **overrides):
    """A one-issue dynamic Run with both external ports scripted."""
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, labels=overrides.pop("issue_labels", None)
    )
    on_send = overrides.pop("on_send", None)
    if on_send is not None:
        fake_client.on_send = on_send
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=overrides.pop(
            "rows",
            (_aa_row("aa-opus", 70.0, 90.0), _aa_row("aa-terra", 40.0, 200.0)),
        ),
        answer=overrides.pop("answer", _elects("claude-opus-5")),
        listing=overrides.pop(
            "listing",
            (
                _listed_model("claude-opus-5", ["high"]),
                _listed_model("gpt-5.6-terra", ["low", "high"]),
            ),
        ),
    )
    config = _dynamic_config(
        route_associations=overrides.pop("route_associations", {
            "aa-opus": "claude-opus-5@high",
            "aa-terra": "gpt-5.6-terra@high",
        }),
        **overrides,
    )
    return fake_client, spied, asyncio.run(loop_module.run(config))


def _dynamic_pool_run(tmp_path, monkeypatch, *, issues, **overrides):
    """A multi-issue dynamic Run, for the **Routing preparation** slice (#566).

    ``_dynamic_run``'s Pool is one issue, which is exactly the shape that
    cannot show preparation: there is nothing behind the **Pickup** to prepare.
    This wires a Pool the Runner can work its way down and returns the spies
    both halves are read off.
    """
    _write_runnable_feedback_loop(tmp_path)
    (tmp_path / "git-loopy").mkdir(exist_ok=True)
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    fake_git = FakeGitClient(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    fake_gh = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=list(issues),
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: fake_gh)
    fake_client = FakeCopilotClient(scripted_events=[])
    close_after_send = overrides.pop("close_after_send", None)
    edit_after_send = overrides.pop("edit_after_send", None)
    context_after_send = overrides.pop("context_after_send", None)
    wait_for_prepared = set(overrides.pop("wait_for_prepared", ()))

    async def _after_send() -> None:
        async def prepared() -> None:
            while not wait_for_prepared.issubset(
                record["issue"] for record in _prepared_records(tmp_path)
            ):
                await asyncio.sleep(0)

        if wait_for_prepared:
            await asyncio.wait_for(prepared(), timeout=2)
        if close_after_send is not None:
            fake_gh.issue_close(close_after_send, "worked")
        if edit_after_send is not None:
            number, body = edit_after_send
            fake_gh.seed_issue(
                dataclass_replace(fake_gh.issue_view(number), body=body)
            )
        if context_after_send is not None:
            (tmp_path / "AGENTS.md").write_text(context_after_send, encoding="utf-8")

    if (
        close_after_send is not None or edit_after_send is not None
        or context_after_send is not None or wait_for_prepared
    ):
        fake_client.on_send = _after_send
    monkeypatch.setattr(loop_module, "_make_client", lambda: fake_client)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    answer = overrides.pop("answer", _elects("claude-opus-5"))
    on_assess = overrides.pop("on_assess", None)

    async def assess(request):
        if on_assess is not None:
            on_assess(request, fake_gh)
        output = None if answer is None else answer(request)
        return await output if inspect.isawaitable(output) else output

    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=overrides.pop(
            "rows",
            (_aa_row("aa-opus", 70.0, 90.0), _aa_row("aa-terra", 40.0, 200.0)),
        ),
        answer=assess,
        listing=overrides.pop(
            "listing",
            (
                _listed_model("claude-opus-5", ["high"]),
                _listed_model("gpt-5.6-terra", ["low", "high"]),
            ),
        ),
        evidence_delay=overrides.pop("evidence_delay", 0.0),
    )
    config = _dynamic_config(
        route_associations={
            "aa-opus": "claude-opus-5@high",
            "aa-terra": "gpt-5.6-terra@high",
        },
        **overrides,
    )
    return fake_client, fake_gh, spied, asyncio.run(loop_module.run(config))


def _prepared_records(tmp_path: Path) -> list[dict[str, Any]]:
    """Every ``wrapper.routing.prepared`` this Run logged, in order."""
    return [
        event
        for event in (json.loads(raw) for raw in _log_lines(tmp_path))
        if event["type"] == "wrapper.routing.prepared"
    ]


def test_the_pool_behind_the_pickup_is_prepared_within_the_allowance(
    tmp_path, monkeypatch
) -> None:
    """AC1: the other eligible candidates are assessed, and the Pickup is not delayed.

    The whole of "prepares proposals for candidates currently established as
    eligible, prioritizing the next Pickup". The Pickup binds first and its own
    selector call is bought first; the rest of the Pool is then prepared beside
    the Agent session under the operator's configured concurrency. A run with
    one Iteration is deliberate — preparation has to happen *during* work, not
    as a side effect of the Run reaching the next issue.
    """
    _fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[_make_issue(number) for number in (42, 43, 44)],
        wait_for_prepared=(43, 44),
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    prepared = _prepared_records(tmp_path)
    assert [record["issue"] for record in prepared] == [43, 44], (
        "the Pool behind the Pickup was not prepared"
    )
    assert all(record["state"] == "proposed" for record in prepared), prepared
    assert all(record["model"] == "claude-opus-5" for record in prepared), prepared
    assert all(record["selector_model"] == "claude-opus-5" for record in prepared)
    assert all(
        record["evidence_source"] == "https://artificialanalysis.ai/api/v2/data/llms/models"
        for record in prepared
    )
    assert all(record["evidence_retrieved_at"] for record in prepared)
    assert all(record["measurement_at"] is None for record in prepared)
    # One for the bound Pickup, one for each prepared candidate. Nothing is
    # assessed twice and nothing eligible is skipped.
    assert len(spied["assessments"]) == 3, spied["assessments"]


def test_preparation_never_precedes_the_pickup_it_runs_beside(
    tmp_path, monkeypatch
) -> None:
    """AC7: background preparation cannot reorder work or get in front of it.

    Pinned as an *ordering* over the canonical log rather than as a timing,
    because "does not delay the Pickup" is only checkable as a fact about what
    happened first. The bound Pickup's own resolution is recorded before any
    proposal for a candidate behind it, and the issue the session is opened on
    is still the head of the Pool's order.
    """
    fake_client, _fake_gh, _spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[_make_issue(number) for number in (42, 43, 44)],
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0
    order = [json.loads(raw)["type"] for raw in _log_lines(tmp_path)]
    assert order.index("wrapper.routing.resolved") < order.index(
        "wrapper.routing.prepared"
    ), "a proposal was prepared before the Pickup it runs beside was routed"
    assert order.index("wrapper.pickup.bound") < order.index(
        "wrapper.routing.prepared"
    ), "preparation ran in front of the bound Pickup"
    (bound,) = _bound_pickups(tmp_path)
    assert bound["issue"] == 42, "preparation reordered the Pool"
    assert fake_client.create_calls[0]["model"] == "claude-opus-5"


def test_a_delayed_preparation_cannot_hold_the_next_static_pickup(
    tmp_path, monkeypatch
) -> None:
    tail_started = asyncio.Event()
    next_pickup = asyncio.Event()
    timed_out = False
    send = FakeCopilotSession.send_and_wait

    async def answer(request):
        nonlocal timed_out
        if "#44:" in request.issue:
            tail_started.set()
            try:
                await asyncio.wait_for(next_pickup.wait(), timeout=0.5)
            except TimeoutError:
                timed_out = True
                raise
            except asyncio.CancelledError:
                raise dynamic_route.RoutingCallCancelled(Decimal("0.30")) from None
        return _elects("claude-opus-5")(request)

    async def work(session, prompt, **kwargs):
        if "=== Issue #42:" in prompt:
            await asyncio.wait_for(tail_started.wait(), timeout=1)
        else:
            next_pickup.set()
        return await send(session, prompt, **kwargs)

    monkeypatch.setattr(FakeCopilotSession, "send_and_wait", work)
    fake_client, _, _, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[
            _make_issue(42),
            _make_issue(43, labels=["ready-for-agent", "task-type:docs"]),
            _make_issue(44),
        ],
        answer=answer,
        close_after_send=42,
        max_iterations=2,
        routing={"docs": ("gpt-5.6-terra", "low")},
        routing_credit_allowance=Decimal("0.50"),
    )

    assert exit_code == 0
    assert next_pickup.is_set()
    assert not timed_out, "the next Pickup waited for unrelated preparation"
    assert [call["model"] for call in fake_client.create_calls] == [
        "claude-opus-5", "gpt-5.6-terra"
    ]
    interrupted = next(record for record in _prepared_records(tmp_path) if record["issue"] == 44)
    assert interrupted["state"] == "unavailable"
    assert interrupted["routing_credits"] == "0.55"
    assert interrupted["routing_overshot"] is True


def test_a_blocked_candidate_is_left_pending_without_being_assessed(
    tmp_path, monkeypatch
) -> None:
    """AC2: ineligible candidates stay visibly pending and cost no selector call.

    A blocked issue is one the Run may not work *now*, which makes an
    assessment of it a **Routing credit** spent on an outcome that cannot be
    used. It is left alone rather than recorded as refused: preparation has no
    verdict to give about eligibility, and saying one would be a second opinion
    competing with the **Pickup**'s.
    """
    blocked = _dated(
        43,
        "2026-01-02T00:00:00Z",
        blocked_by=BlockedByRead(
            total_count=1,
            nodes=(BlockerNode(ref="acme/widgets#93", state="open"),),
        ),
    )
    _fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[
            _dated(42, "2026-01-01T00:00:00Z"),
            blocked,
            _dated(44, "2026-01-03T00:00:00Z"),
        ],
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0
    prepared_refs = [record["issue"] for record in _prepared_records(tmp_path)]
    assert prepared_refs == [44], (
        f"a blocked candidate bought an assessment: {prepared_refs}"
    )
    # The Pickup's own call plus the one eligible candidate behind it.
    assert len(spied["assessments"]) == 2, spied["assessments"]


@pytest.mark.parametrize("change", ["blocked", "unreadable", "closed", "unlabelled"])
def test_queued_eligibility_is_reread_before_any_preparation_spend(
    tmp_path, monkeypatch, change
) -> None:
    classified = []

    async def classify(_proposer, _pair, item):
        classified.append(item.ref)
        return "<task-type>implementation</task-type>"

    def change_tail(request, tracker):
        if "#43:" not in request.issue:
            return
        item = tracker.issue_view(44)
        changes = {
            "blocked": {"blocked_by": BlockedByRead(
                total_count=1, nodes=(BlockerNode(ref="x/y#99", state="open"),)
            )},
            "unreadable": {"blocked_by": BlockedByRead.unprovable()},
            "closed": {"state": "CLOSED"},
            "unlabelled": {"labels": ()},
        }
        tracker.seed_issue(dataclass_replace(item, **changes[change]))

    monkeypatch.setattr(loop_module.SessionTaskTypeProposer, "__call__", classify)
    monkeypatch.setattr(
        loop_module, "_make_task_type_label_client", _RecordingTaskTypeLabelClient
    )
    _, _, spied, exit_code = _dynamic_pool_run(
        tmp_path, monkeypatch,
        issues=[
            _make_issue(42, labels=["ready-for-agent", "task-type:implementation", "semver:none"]),
            _make_issue(43, labels=["ready-for-agent", "task-type:implementation", "semver:none"]),
            _make_issue(44, labels=["ready-for-agent"]),
        ],
        classifier_model="gpt-5.6-terra",
        classifier_effort="high",
        on_assess=change_tail,
        wait_for_prepared=(43, 44),
    )
    assert exit_code == 0
    assert classified == []
    assert len(spied["assessments"]) == 2
    assert _prepared_records(tmp_path)[-1]["state"] == "unavailable"


def test_a_static_route_is_prepared_without_asking_the_selector(
    tmp_path, monkeypatch
) -> None:
    """AC3: classification first, then static applicability, then the selector.

    A ``[routing]`` entry is the operator's own instruction, so the candidate
    it covers reaches its **Pickup** already routed and preparation must not
    buy an assessment for it. Saying so out loud — ``state: static`` rather
    than silence — is what keeps an operator from reading an unprepared issue
    as an unreachable one.
    """
    _fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[
            _make_issue(42),
            _make_issue(43, labels=["ready-for-agent", "task-type:docs"]),
        ],
        routing={"docs": ("gpt-5.6-terra", "low")},
        wait_for_prepared=(43,),
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0
    (record,) = _prepared_records(tmp_path)
    assert record["issue"] == 43
    assert record["state"] == "static", record
    assert record["proposal_id"] is None, "a static route minted a proposal"
    # The Pickup's own call, and no second one for the statically routed issue.
    assert len(spied["assessments"]) == 1, spied["assessments"]


def test_a_prepared_proposal_binds_at_its_pickup_without_a_second_call(
    tmp_path, monkeypatch
) -> None:
    """AC6: unchanged verified inputs do not rerun the **Route selector**.

    The payoff the whole slice exists for, and the only assertion that can show
    it: over two Iterations the second issue is prepared during the first and
    *bound* in the second, so the Run buys two assessments for two issues
    rather than three for two. A third call would mean preparation cost a
    credit and saved nothing.

    The first issue is closed from inside its own session, because an issue the
    Run re-picks carries a **Prior attempt** the second time — which is a
    changed verified input and *should* be reassessed. Testing the saving over
    an issue that legitimately reassesses would measure nothing.
    """
    issues = [_make_issue(42), _make_issue(43)]

    _fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=issues,
        wait_for_prepared=(43,),
        close_after_send=42,
        max_iterations=2,
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0
    assert len(spied["assessments"]) == 2, spied["assessments"]
    resolved = [
        event
        for event in (json.loads(raw) for raw in _log_lines(tmp_path))
        if event["type"] == "wrapper.routing.resolved"
    ]
    assert [record["issue"] for record in resolved] == [42, 43]
    assert all(record["model"] == "claude-opus-5" for record in resolved), resolved
    # The ledger's Run-wide count: two admitted calls for two worked issues.
    assert [record["selector_attempts"] for record in resolved] == [1, 2], resolved


def test_a_changed_issue_invalidates_its_prepared_proposal(
    tmp_path, monkeypatch
) -> None:
    """AC6: changed relevant input buys another selection rather than binding a stale one.

    The strongest thing preparation can get wrong is to hand a **Pickup** an
    assessment of an issue that no longer says what it said. Rewriting the
    queued issue's body between the two Iterations moves the verified input
    identity, so the second Pickup reassesses — three calls for two issues,
    which is the *correct* number here and the wrong one in the test above.
    """
    issues = [_make_issue(42), _make_issue(43)]

    _fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=issues,
        wait_for_prepared=(43,),
        close_after_send=42,
        edit_after_send=(
            43,
            "## Parent\nfoo\n\n## What to build\nsomething else entirely\n\n"
            "## Acceptance criteria\nbar",
        ),
        max_iterations=2,
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0
    assert len(spied["assessments"]) == 3, spied["assessments"]
    resolved = [
        event
        for event in (json.loads(raw) for raw in _log_lines(tmp_path))
        if event["type"] == "wrapper.routing.resolved"
    ]
    assert [record["issue"] for record in resolved] == [42, 43]
    assert resolved[1]["selector_attempts"] == 3, (
        "a changed issue bound the proposal prepared for its older text"
    )


def test_changed_feedback_loop_commands_invalidate_a_prepared_proposal(
    tmp_path, monkeypatch
) -> None:
    _, _, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[_make_issue(42), _make_issue(43)],
        wait_for_prepared=(43,),
        close_after_send=42,
        context_after_send=(
            "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n"
            "| Unit | `pytest -q changed_suite` |\n"
        ),
        max_iterations=2,
        routing_credit_allowance=Decimal("5"),
    )

    assert exit_code == 0
    assert len(spied["assessments"]) == 3
    assert any(
        "pytest -q changed_suite" in context
        for context in spied["assessments"][-1][1].repository_context
    )


def test_an_exhausted_allowance_stops_preparing_without_stopping_work(
    tmp_path, monkeypatch
) -> None:
    """AC8: exhaustion leaves an explicit outcome and strands no speculative loop.

    The allowance here pays for the **Pickup**'s own assessment and no more, so
    preparation meets the exhausted ledger on its first candidate. What must
    *not* happen is the Run failing: the bound issue was routed before the
    allowance ran out and its session is entitled to run to the end.
    """
    fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[_make_issue(number) for number in (42, 43, 44)],
        wait_for_prepared=(43,),
        routing_credit_allowance=Decimal("0.25"),
    )

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert fake_client.create_calls, "an exhausted allowance stopped the work"
    assert fake_client.create_calls[0]["model"] == "claude-opus-5"
    assert len(spied["assessments"]) == 1, spied["assessments"]
    states = [record["state"] for record in _prepared_records(tmp_path)]
    assert states and set(states) == {"unavailable"}, states
    # Latched, not retried per candidate: one refusal, then silence.
    assert len(states) == 1, "an exhausted desk kept asking"


def test_preparation_cannot_buy_classification_after_routing_allowance_exhaustion(
    tmp_path, monkeypatch
) -> None:
    classifications = []

    async def classify(_proposer, _pair, item):
        classifications.append(item.ref)
        return "<task-type>docs</task-type>"

    monkeypatch.setattr(loop_module.SessionTaskTypeProposer, "__call__", classify)
    monkeypatch.setattr(
        loop_module, "_make_task_type_label_client", _RecordingTaskTypeLabelClient
    )
    fake_client, _, _, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[
            _make_issue(42, labels=["ready-for-agent", "task-type:implementation", "semver:none"]),
            _make_issue(43, labels=["ready-for-agent"]),
        ],
        classifier_model="gpt-5.6-terra",
        classifier_effort="high",
        routing_credit_allowance=Decimal("0.25"),
        wait_for_prepared=(43,),
    )

    assert exit_code == 0
    assert classifications == []
    assert fake_client.create_calls[0]["model"] == "claude-opus-5"
    assert _prepared_records(tmp_path)[0]["reason"] == "quota_exhausted"


def test_unavailable_dynamic_routing_leaves_the_next_static_pickup_useful(
    tmp_path, monkeypatch
) -> None:
    fake_client, _, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[
            _make_issue(42),
            _make_issue(43, labels=["ready-for-agent", "task-type:docs"]),
        ],
        routing={"docs": ("gpt-5.6-terra", "low")},
        routing_credit_allowance=Decimal("0"),
    )

    assert exit_code == 0
    assert spied["assessments"] == []
    assert [entry["issue"] for entry in _bound_pickups(tmp_path)] == [43]
    assert fake_client.create_calls[0]["model"] == "gpt-5.6-terra"
    assert fake_client.create_calls[0]["reasoning_effort"] == "low"


def test_concurrent_preparation_shares_one_live_evidence_read(
    tmp_path, monkeypatch
) -> None:
    """AC5: concurrent checks may share an in-flight request.

    Five live reads are *asked for* here — the **Pickup**'s ``prepare`` and its
    ``bind``, then one per prepared candidate — and only four are bought,
    because the two preparations the configured concurrency lets overlap are
    asking the identical question of the identical source at the same instant.
    Answering it twice would spend a second round-trip against ADR-0057's
    thousand-a-day evidence budget for bytes the Run already has in flight.

    Not a cache, and the count says so: the Pickup's ``bind`` re-reads rather
    than reusing what its own ``prepare`` read a moment earlier, because a
    binding's authority is that its evidence is current.
    """
    assessed = asyncio.Event()
    send = FakeCopilotSession.send_and_wait

    def answer(request):
        if "#45:" in request.issue:
            assessed.set()
        return _elects("claude-opus-5")(request)

    async def work(session, prompt, **kwargs):
        await asyncio.wait_for(assessed.wait(), timeout=1)
        return await send(session, prompt, **kwargs)

    monkeypatch.setattr(FakeCopilotSession, "send_and_wait", work)
    _fake_client, _fake_gh, spied, exit_code = _dynamic_pool_run(
        tmp_path,
        monkeypatch,
        issues=[_make_issue(number) for number in (42, 43, 44, 45)],
        selector_concurrency=2,
        evidence_delay=0.01,
        answer=answer,
        routing_credit_allowance=Decimal("10"),
    )

    assert exit_code == 0
    assert len(spied["assessments"]) == 4, spied["assessments"]
    assert spied["evidence"] == 4, (
        f"{spied['evidence']} evidence reads; 5 were asked for and the two "
        "overlapping preparations should have shared one"
    )


def test_a_dynamic_route_reaches_the_serial_work_sessions_own_arguments(
    tmp_path, monkeypatch
) -> None:
    """The elected route is what ``create_session`` is actually called with.

    The whole of AC9's "actually supplies the work session's settings": not the
    proposal, not the readback, not the Queue cell — the request the
    issue-owning session is opened with. A route that agrees everywhere except
    here is a route that did not take effect.
    """
    fake_client, spied, exit_code = _dynamic_run(tmp_path, monkeypatch)

    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert fake_client.create_calls, "no work session was opened"
    call = fake_client.create_calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["reasoning_effort"] == "high"
    assert spied["assessments"], "the Route selector was never asked"


def test_the_dashboard_reads_the_dynamic_route_from_the_pickup_it_bound(
    tmp_path, monkeypatch
) -> None:
    """One resolution feeds the session, the Event and the **Dashboard**.

    The readback agreeing with the session is the same **Routing resolution**
    arriving in two places, under a **Routing source** that says a selector
    decided it — which is what stops a routing decision being quoted back as a
    human instruction.
    """
    _fake_client, _spied, exit_code = _dynamic_run(tmp_path, monkeypatch)

    assert exit_code == 0
    (bound,) = _bound_pickups(tmp_path)
    assert bound["model"] == "claude-opus-5"
    assert bound["effort"] == "high"
    assert bound["routing_source"] == "dynamic"


def test_the_decisions_provenance_is_persisted_before_the_work_starts(
    tmp_path, monkeypatch
) -> None:
    """AC9's local decision provenance, and it lands ahead of the session.

    Provenance written afterwards is provenance that is missing exactly when
    the Run died mid-decision, so the ordering is what is pinned rather than
    merely the record's presence.

    The single selector call is AC8's other half: ``bind`` re-read both live
    sources at Pickup and found the verified inputs unchanged, so it reused the
    proposal's assessment instead of buying a second one. One admitted call for
    one issue is the whole point of charging the assessment to routing credits.
    """
    _fake_client, _spied, exit_code = _dynamic_run(tmp_path, monkeypatch)

    assert exit_code == 0
    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    resolved = [
        event for event in events if event["type"] == "wrapper.routing.resolved"
    ]
    assert len(resolved) == 1, "the dynamic decision left no provenance"
    record = resolved[0]
    assert record["issue"] == 42
    assert record["model"] == "claude-opus-5"
    assert record["evidence_source"].startswith("https://artificialanalysis.ai/")
    assert record["evidence_retrieved_at"]
    assert record["capabilities_retrieved_at"]
    assert record["routing_credits"] == "0.25"
    assert record["selector_attempts"] == 1

    order = [event["type"] for event in events]
    assert order.index("wrapper.routing.resolved") < order.index(
        "wrapper.pickup.bound"
    )


def test_an_invalid_selector_answer_refuses_rather_than_falling_back(
    tmp_path, monkeypatch
) -> None:
    """AC11: no stale, default or cheaper-selector fallback — an explicit refusal.

    Naming a configuration that is not on the candidate list is the classic
    injected answer, and the failure mode that matters is not that it is
    refused but *what happens next*: taking the Run's default pair would run
    the issue on a route nobody elected and report it as routed.
    """
    fake_client, _spied, exit_code = _dynamic_run(
        tmp_path,
        monkeypatch,
        answer=lambda _request: json.dumps(
            {"candidate_identity": "nine", "summary": "not on the list"}
        ),
    )

    assert fake_client.create_calls == [], "a refused route still opened a session"
    assert exit_code != 0
    skipped = [
        event
        for event in _pickup_events(tmp_path)
        if event["type"] == "wrapper.pickup.skipped"
    ]
    assert skipped and "invalid_selector_output" in skipped[-1]["reason"]


def test_an_unreachable_evidence_source_refuses_rather_than_guessing(
    tmp_path, monkeypatch
) -> None:
    """A required source that failed is unavailable, never "assume the default"."""
    _write_runnable_feedback_loop(tmp_path)
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    _harness(monkeypatch, ("gpt-5.6-terra", ["low", "high"], True))
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "aa-token")
    _wire_dynamic_ports(
        monkeypatch,
        rows=(_aa_row("aa-terra", 40.0, 200.0),),
        answer=None,
        listing=(_listed_model("gpt-5.6-terra", ["low", "high"]),),
    )

    async def _unreachable(*_args: Any, **_kwargs: Any) -> object:
        raise RuntimeError("artificialanalysis.ai refused the connection")

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", _unreachable)

    exit_code = asyncio.run(
        loop_module.run(
            _dynamic_config(route_associations={"aa-terra": "gpt-5.6-terra@high"})
        )
    )

    assert fake_client.create_calls == []
    assert exit_code != 0
    skipped = [
        event
        for event in _pickup_events(tmp_path)
        if event["type"] == "wrapper.pickup.skipped"
    ]
    assert skipped and "source_unavailable" in skipped[-1]["reason"]


def test_a_configured_routing_entry_keeps_the_selector_out_of_it(
    tmp_path, monkeypatch
) -> None:
    """AC5: avoid the **Route selector** where a Static route applies.

    The operator wrote this pair down for this **Task type**; spending a
    selector call to be told something else would override an instruction with
    an inference, which is the precedence ADR-0057 settles the other way.
    """
    fake_client, spied, exit_code = _dynamic_run(
        tmp_path,
        monkeypatch,
        issue_labels=["ready-for-agent", "task-type:implementation"],
        routing={"implementation": ("gpt-5.6-terra", "low")},
    )

    assert exit_code == 0
    assert spied["assessments"] == [], "a Static route still bought a selector call"
    assert fake_client.create_calls[0]["model"] == "gpt-5.6-terra"
    assert fake_client.create_calls[0]["reasoning_effort"] == "low"


def test_the_assessment_sees_the_issue_and_the_gates_it_must_pass(
    tmp_path, monkeypatch
) -> None:
    """AC6's inputs arrive at the real selector, off the real checkout."""
    _fake_client, spied, exit_code = _dynamic_run(tmp_path, monkeypatch)

    assert exit_code == 0
    (_selector, request) = spied["assessments"][0]
    assert "#42" in request.issue
    assert request.task_type
    assert any("feedback loop" in entry for entry in request.repository_context)


def test_a_classification_counts_toward_this_runs_routing_usage(
    tmp_path, monkeypatch
) -> None:
    """AC10: classification attempts count toward routing usage.

    The **Task-type classifier** runs *because* this Run routes dynamically —
    AC5 settles the Task type before applicability is resolved — so a Run that
    counted only the selector would under-report the spend its own routing
    caused, and the allowance meant to bound that spend would bound half of it.
    """
    _fake_client, _spied, exit_code = _dynamic_run(
        tmp_path,
        monkeypatch,
        classifier_model="gpt-5.6-terra",
        classifier_effort="high",
    )

    assert exit_code == 0
    events = [json.loads(raw) for raw in _log_lines(tmp_path)]
    (record,) = [
        event for event in events if event["type"] == "wrapper.routing.resolved"
    ]
    assert record["classification_attempts"] == 1


# ---------------------------------------------------------------------------
# Later dynamic attempts reselect from outcome evidence (#562, ADR-0057)
# ---------------------------------------------------------------------------


def _routing_records(tmp_path: Path) -> list[dict[str, Any]]:
    """Every **Dynamic route** decision this Run recorded, in order."""
    return [
        event
        for event in (json.loads(raw) for raw in _log_lines(tmp_path))
        if event["type"] == "wrapper.routing.resolved"
    ]


@pytest.mark.parametrize("retry_model", ["claude-opus-5", "gpt-5.6-terra"])
def test_a_permitted_retry_reassesses_with_the_previous_outcome(
    tmp_path, monkeypatch, retry_model
) -> None:
    """The whole ticket, through the record an operator reads (AC1, AC3, AC6).

    The first Iteration ends in silent no-progress, which the **Attempt
    lifecycle** answers with one more attempt. Under the **Dynamic route** that
    second **Pickup** does not inherit a fixed **Escalation rung** — there is
    none, and ADR-0057 reserves none — it reassesses, and the assessment it buys
    is handed what the first attempt ran on and what its ending was evidence of.

    Both axes are on the record and neither is derived from the other: the
    decision names the configuration, and ``lifecycle_position``/``attempt``
    name where the issue sits. A record carrying only the first could not tell a
    reassessed retry from a first election that happened to agree.
    """
    fake_client, spied, exit_code = _dynamic_run(
        tmp_path, monkeypatch, max_iterations=2, max_nmt_strikes=9,
        answer=lambda request: _elects(
            retry_model if request.prior_attempts else "claude-opus-5"
        )(request),
    )

    assert exit_code == 0
    first, second = (request for _selector, request in spied["assessments"])
    assert first.prior_attempts == ()
    (evidence,) = second.prior_attempts
    assert evidence.model == "claude-opus-5"
    assert evidence.reasoning_effort == "high"
    assert evidence.outcome is dynamic_route.PriorOutcome.DID_NOT_SOLVE
    assert evidence.detail == "no_progress"
    assert evidence.capability_evidence is True

    opening, retry = _routing_records(tmp_path)
    assert (opening["lifecycle_position"], opening["attempt"]) == ("fresh", 1)
    assert opening["prior_attempts"] == []
    assert (retry["lifecycle_position"], retry["attempt"]) == ("retrying", 2)
    assert retry["prior_attempts"] == [
        {
            "model": "claude-opus-5",
            "effort": "high",
            "context_tier": "default",
            "outcome": "did_not_solve",
            "detail": "no_progress",
            "capability_evidence": True,
        }
    ]
    assert (retry["repeat_justification"] is not None) is (
        retry_model == "claude-opus-5"
    )

    assert [
        (e["issue"], e["model"], e["routing_source"], e["lifecycle_position"])
        for e in _bound_pickups(tmp_path)
    ] == [
        (42, "claude-opus-5", "dynamic", "fresh"),
        (42, retry_model, "dynamic", "retrying"),
    ]
    assert [call["model"] for call in fake_client.create_calls] == [
        "claude-opus-5", retry_model
    ]
    assert [call["reasoning_effort"] for call in fake_client.create_calls] == ["high", "high"]


def test_a_crashed_attempt_is_reassessed_without_becoming_capability_evidence(
    tmp_path, monkeypatch
) -> None:
    """AC2: the harness falling over says nothing about the route it fell on.

    The retry still reassesses — current evidence and eligibility are re-read
    either way — but the configuration that crashed is offered back with no
    case to answer, so the selector may simply choose it again. A Run that
    demoted a route for a transport failure would spend the rest of its life
    avoiding whatever was running when the network blinked.
    """
    fake_client, spied, exit_code = _dynamic_run(
        tmp_path,
        monkeypatch,
        max_iterations=2,
        max_nmt_strikes=9,
        on_send=_raise_a_transport_failure,
    )

    assert exit_code == 0
    _first, second = (request for _selector, request in spied["assessments"])
    (evidence,) = second.prior_attempts
    assert evidence.outcome is dynamic_route.PriorOutcome.INFRASTRUCTURE_FAILURE
    assert evidence.detail == "crash"
    assert evidence.capability_evidence is False

    _opening, retry = _routing_records(tmp_path)
    assert retry["attempt"] == 2
    assert retry["repeat_justification"] is None
    assert [call["model"] for call in fake_client.create_calls] == [
        "claude-opus-5",
        "claude-opus-5",
    ]


@pytest.mark.parametrize("default_effort", ["low", "high"])
def test_an_explicitly_configured_rung_still_outranks_the_selector(
    tmp_path, monkeypatch, default_effort
) -> None:
    """AC5: explicit static escalation is an instruction, not a starting point.

    An operator who wrote an ``[escalation]`` block under the dynamic policy has
    named the pair a stalled issue is retried at, and ADR-0057 keeps the
    selector away from it exactly as it keeps it away from a ``[routing]`` entry
    or a run-wide pin. So the retry runs on the rung, reports ``escalated``, and
    buys no second assessment at all — a credit spent to contradict an
    instruction is a credit spent for nothing.

    A rung equal to the run-wide default is still explicit authority: that
    default is only a placeholder until the dynamic election supplies a route.
    """
    _fake_client, spied, exit_code = _dynamic_run(
        tmp_path,
        monkeypatch,
        max_iterations=2,
        max_nmt_strikes=9,
        reasoning_effort=default_effort,
        escalation_rung=("gpt-5.6-terra", "high"),
    )

    assert exit_code == 0
    assert len(spied["assessments"]) == 1, "a Static route bought a selector call"
    assert [
        (e["model"], e["effort"], e["routing_source"])
        for e in _bound_pickups(tmp_path)
    ] == [
        ("claude-opus-5", "high", "dynamic"),
        ("gpt-5.6-terra", "high", "escalated"),
    ]
    assert len(_routing_records(tmp_path)) == 1


@pytest.mark.parametrize("refusal", ["invalid_output", "allowance", "eligibility"])
def test_an_unavailable_later_route_blocks_the_retry_without_spending_it(
    tmp_path, monkeypatch, refusal
) -> None:
    """AC4/AC8: a routing refusal is not an attempt, and not a **Strike**.

    The first attempt stalls and the lifecycle grants a second; the second
    election cannot be made. Nothing may be invented to fill the gap — no stale
    decision, no built-in default — and nothing may be charged for the session
    that never opened: the issue is left where the lifecycle put it rather than
    driven to ``skipped`` by a decision it never got.

    The Run ends non-zero because the last Iteration bound nothing, which is the
    honest report: blocking the affected work *is* the required behaviour, and a
    Run that reported success while silently declining to work its one issue
    would be indistinguishable from one that had nothing to do.
    """
    answers = iter((_elects("claude-opus-5"), None))

    def _answer(request: Any) -> Any:
        chosen = next(answers)
        return None if chosen is None else chosen(request)

    listing = (
        _listed_model("claude-opus-5", ["high"]),
        _listed_model("gpt-5.6-terra", ["high"]),
    )

    def after_send() -> None:
        if refusal == "eligibility":
            for model in listing:
                model.policy.state = "disabled"

    fake_client, _spied, exit_code = _dynamic_run(
        tmp_path,
        monkeypatch,
        max_iterations=2,
        max_nmt_strikes=9,
        answer=_answer,
        listing=listing,
        on_send=after_send,
        routing_credit_allowance=Decimal("0.25") if refusal == "allowance" else Decimal("5"),
    )

    assert exit_code != 0
    assert len(fake_client.create_calls) == 1, "a refused route still opened a session"
    assert len(_routing_records(tmp_path)) == 1
    assert _strikes(tmp_path) == []
    skipped = [
        event
        for event in (json.loads(raw) for raw in _log_lines(tmp_path))
        if event["type"] == "wrapper.pickup.skipped"
    ]
    assert skipped and "dynamic route unavailable" in skipped[-1]["reason"]


@pytest.mark.parametrize("ending", ["no_progress", "crash"])
def test_dynamic_attempt_exhaustion_admits_no_further_assessment(
    tmp_path, monkeypatch, ending
) -> None:
    fake_client, spied, exit_code = _dynamic_run(
        tmp_path, monkeypatch, max_iterations=5, max_nmt_strikes=1,
        on_send=_raise_a_transport_failure if ending == "crash" else None,
    )

    assert exit_code == 1
    assert len(fake_client.create_calls) == 2
    assert len(spied["assessments"]) == 2
    assert len(_strikes(tmp_path)) == 1
    assert [record["attempt"] for record in _routing_records(tmp_path)] == [1, 2]


def test_the_initial_dynamic_pickup_may_already_spend_max(
    tmp_path, monkeypatch
) -> None:
    fake_client, _spied, exit_code = _dynamic_run(
        tmp_path, monkeypatch,
        listing=(_listed_model("claude-opus-5", ["high", "max"]),),
        route_associations={"aa-opus": "claude-opus-5@max"},
    )

    assert exit_code == 0
    (call,) = fake_client.create_calls
    assert (call["model"], call["reasoning_effort"]) == ("claude-opus-5", "max")
    (record,) = _routing_records(tmp_path)
    assert (record["lifecycle_position"], record["effort"]) == ("fresh", "max")


@pytest.mark.parametrize("authority", ["task_route", "run_override"])
def test_static_authority_suppresses_dynamic_selection_across_attempts(
    tmp_path, monkeypatch, authority
) -> None:
    fake_client, spied, exit_code = _dynamic_run(
        tmp_path, monkeypatch, max_iterations=2, max_nmt_strikes=9,
        issue_labels=["ready-for-agent", "task-type:docs"],
        routing={"docs": ("gpt-5.6-terra", "high")} if authority == "task_route" else {},
        routing_suppressed=authority == "run_override",
    )

    assert exit_code == 0
    assert spied["assessments"] == []
    assert [call["model"] for call in fake_client.create_calls] == [
        "gpt-5.6-terra", "gpt-5.6-terra"
    ]
    assert [bound["lifecycle_position"] for bound in _bound_pickups(tmp_path)] == [
        "fresh", "retrying"
    ]
    assert _routing_records(tmp_path) == []


# ---------------------------------------------------------------------------
# Route publication — the final Routing resolution, projected (#563, ADR-0057)
# ---------------------------------------------------------------------------


def _delivery_events(tmp_path: Path) -> list[dict[str, Any]]:
    """Every Route-delivery envelope written under this repo, oldest Run first.

    Deliberately not :func:`_read_events`, which reads one Run's log: a pending
    delivery is resumed by a *later* Run, so the property under test only
    exists across two of them.
    """
    logs = sorted((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    return [
        event
        for log in logs
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for event in [json.loads(line)]
        if event["type"] == "wrapper.routing.delivery"
    ]


def test_a_pickup_projects_its_final_route_onto_the_issue(
    tmp_path, monkeypatch
) -> None:
    """The Pickup's own resolution reaches the tracker as one comment and label.

    The projection is an *output adapter* of a record that already exists: the
    comment names the exact triple, and the single owned Route label sits
    beside the issue's own labels rather than replacing any of them.
    """
    _wire_single_issue_github(tmp_path, monkeypatch)
    fake_gh = loop_module._make_github_client()

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=1))
    )

    assert exit_code == 0
    assert len(fake_gh.route_comment_calls) == 1
    _number, body = fake_gh.route_comment_calls[0]
    assert "<!-- git-loopy-route:v1:" in body
    route_labels = [
        label
        for label in fake_gh.issue_labels(42)
        if label.startswith("git-loopy-route:")
    ]
    assert len(route_labels) == 1
    assert "ready-for-agent" in fake_gh.issue_labels(42)
    assert [event["status"] for event in _delivery_events(tmp_path)] == ["published"]


def test_an_unchanged_route_is_not_published_a_second_time(
    tmp_path, monkeypatch
) -> None:
    """A revalidation that re-elects the same triple is not news for the issue.

    The durable delivery record is keyed on the assignment, so a second Run
    over the same issue recognises its own projection instead of appending a
    duplicate explanation of a route that never changed.
    """
    _wire_single_issue_github(tmp_path, monkeypatch)
    fake_gh = loop_module._make_github_client()

    for _ in range(2):
        assert (
            asyncio.run(
                loop_module.run(RunConfig(issue_source="github", max_iterations=1))
            )
            == 0
        )

    assert len(fake_gh.route_comment_calls) == 1


def test_a_refusing_tracker_leaves_the_recorded_work_to_proceed(
    tmp_path, monkeypatch
) -> None:
    """Delivery is non-blocking once the canonical local record exists (AC5/AC6).

    A permission failure on the comment is retained as pending local delivery
    and reported as pending — never as published — while the Agent session the
    already-recorded route authorises still runs.
    """
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    fake_gh = loop_module._make_github_client()
    fake_gh._route_comment_errors[42] = RouteDeliveryError("HTTP 403")

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=1))
    )

    assert exit_code == 0
    assert len(fake_client.created) == 1, "the recorded route still ran its session"
    assert [event["status"] for event in _delivery_events(tmp_path)] == ["pending"]
    assert fake_gh.route_comment_calls == []


def test_a_pending_delivery_is_resumed_by_a_later_run(tmp_path, monkeypatch) -> None:
    """Pending delivery survives the Run that could not complete it (AC7).

    The retry reads the durable local record rather than re-deciding anything,
    so a tracker that comes back accepts the projection of the route that was
    already final when it failed.
    """
    _wire_single_issue_github(tmp_path, monkeypatch)
    fake_gh = loop_module._make_github_client()
    fake_gh._route_comment_errors[42] = RouteDeliveryError("HTTP 429")

    assert (
        asyncio.run(
            loop_module.run(RunConfig(issue_source="github", max_iterations=1))
        )
        == 0
    )
    fake_gh._route_comment_errors.clear()
    assert (
        asyncio.run(
            loop_module.run(RunConfig(issue_source="github", max_iterations=1))
        )
        == 0
    )

    assert len(fake_gh.route_comment_calls) == 1
    assert "published" in [event["status"] for event in _delivery_events(tmp_path)]


def test_a_final_route_that_cannot_be_recorded_locally_starts_no_work(
    tmp_path, monkeypatch
) -> None:
    """Canonical local persistence precedes work *and* publication (AC5).

    An event log that refuses the binding leaves no record of what the Agent
    would have run on, so the iteration must not run one — and must not tell
    the tracker about a route it could not write down.
    """
    fake_client, _fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
    fake_gh = loop_module._make_github_client()
    original = persist_module.EventLogWriter.write

    def refuse_the_binding(self, envelope: dict[str, Any]) -> None:
        if envelope.get("type") == "wrapper.pickup.bound":
            raise OSError("event log is unwritable")
        original(self, envelope)

    monkeypatch.setattr(persist_module.EventLogWriter, "write", refuse_the_binding)

    exit_code = asyncio.run(
        loop_module.run(RunConfig(issue_source="github", max_iterations=1))
    )

    assert exit_code != 0
    assert fake_client.created == []
    assert fake_gh.route_comment_calls == []


# --- Dynamic routing: reuse across Runs (#565, ADR-0057) --------------------


def _routing_records(tmp_path: Path) -> list[dict[str, Any]]:
    """Every ``wrapper.routing.resolved`` record in this clone, oldest Run first."""
    logs = sorted(
        (tmp_path / ".git-loopy" / "logs").glob("*.jsonl"), key=lambda p: p.name
    )
    return [
        event
        for path in logs
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (event := json.loads(raw))["type"] == "wrapper.routing.resolved"
    ]


def test_a_later_run_revalidates_the_route_its_own_history_already_records(
    tmp_path, monkeypatch
) -> None:
    """#565 AC1/AC3/AC9: fresh checks, no second selector call, both modes' serial half.

    The whole slice end to end. The first Run elects and records; the second
    Run derives a **Reusable route** from that record alone, re-reads both live
    sources, finds the verified inputs unchanged, and opens its work session on
    the same configuration without buying a selector call. What is pinned is
    every link in that chain — the sources *were* read, the selector was *not*
    asked, the new record points at the old decision, and the session actually
    ran on the reused pair. A route that agrees everywhere except the session
    is a route that did not take effect.
    """
    _fake, first, first_exit = _dynamic_run(tmp_path, monkeypatch)
    assert first_exit == 0, f"the first Run failed: {first_exit}"
    assert len(first["assessments"]) == 1

    fake_client, second, second_exit = _dynamic_run(tmp_path, monkeypatch)

    assert second_exit == 0, f"the reusing Run failed: {second_exit}"
    assert second["assessments"] == [], "a matching reusable route still paid a selector"
    assert second["evidence"] >= 1, "reuse skipped the freshness check it may not skip"
    assert fake_client.create_calls, "the reusing Run opened no work session"
    call = fake_client.create_calls[0]
    assert (call["model"], call["reasoning_effort"]) == ("claude-opus-5", "high")

    elected, revalidated = _routing_records(tmp_path)
    assert elected["routing_reuse"] == "elected"
    assert revalidated["routing_reuse"] == "revalidated"
    assert revalidated["reused_proposal_id"] == elected["proposal_id"]
    assert revalidated["reused_validated_at"] == elected["validated_at"]
    assert revalidated["validated_at"] != elected["validated_at"]
    assert revalidated["selector_attempts"] == 0
    assert revalidated["routing_credits"] == "0"
    assert revalidated["model"] == "claude-opus-5"
    assert revalidated["relevant_input_identity"] == elected["relevant_input_identity"]


def test_routing_never_feeds_its_own_inputs_so_reuse_does_not_decay(
    tmp_path, monkeypatch
) -> None:
    """#565 AC6: the second reuse is as valid as the first.

    The invalidation loop this forbids is the one #563 already had to close
    once on the publishing side: routing writes something an input reader then
    sees, so the next Run's inputs differ, so it reassesses, so it writes
    again. Reuse adds two fresh candidates for that — the revalidation record
    itself, and its ``validated_at`` timestamp. Neither may reach the verified
    input identity, and a third Run that still revalidates against the *first*
    Run's decision is the only assertion that says so: had either leaked, the
    identity would have moved and the third Run would have paid a selector.
    """
    _dynamic_run(tmp_path, monkeypatch)
    _dynamic_run(tmp_path, monkeypatch)

    _fake, third, third_exit = _dynamic_run(tmp_path, monkeypatch)

    assert third_exit == 0, f"the third Run failed: {third_exit}"
    assert third["assessments"] == [], "reuse decayed into a fresh assessment"
    elected, second, latest = _routing_records(tmp_path)
    assert [r["routing_reuse"] for r in (second, latest)] == [
        "revalidated",
        "revalidated",
    ]
    assert latest["reused_proposal_id"] == elected["proposal_id"], (
        "a revalidation was reused as if it were the original decision"
    )
    assert latest["relevant_input_identity"] == elected["relevant_input_identity"]


def test_an_edited_task_type_decides_again_rather_than_reusing(
    tmp_path, monkeypatch
) -> None:
    """#565 AC2/AC6: a relevant edit still invalidates.

    The Task type is one of the things the assessment is *told* rather than
    left to guess, so re-labelling the issue changes what the selector was
    asked — and a cache that answered anyway would be answering a question
    nobody asked. The new decision is a real one, bought inside the allowance,
    and it names the record it supersedes so the history stays followable.
    """
    _dynamic_run(tmp_path, monkeypatch)

    _fake, second, second_exit = _dynamic_run(
        tmp_path,
        monkeypatch,
        issue_labels=["ready-for-agent", "task-type:docs"],
    )

    assert second_exit == 0, f"the reassessing Run failed: {second_exit}"
    assert len(second["assessments"]) == 1, "a changed input was served from history"
    elected, reassessed = _routing_records(tmp_path)
    assert reassessed["routing_reuse"] == "elected"
    assert reassessed["reused_proposal_id"] is None
    assert reassessed["reassessed"] is True
    assert reassessed["superseded_proposal_id"] == elected["proposal_id"]
    assert (
        reassessed["relevant_input_identity"] != elected["relevant_input_identity"]
    ), "the identity did not notice the edit it exists to notice"


def test_a_withdrawn_model_is_not_replayed_past_the_harness_it_left(
    tmp_path, monkeypatch
) -> None:
    """#565 AC7: history cannot carry a model past a current capability check.

    The sharpest thing a cache can get wrong. Between the two Runs the elected
    model leaves the harness listing entirely; the recorded route still names
    it, and replaying it would open a session on a model this account cannot
    run. The second Run must elect from what the harness offers *now*, and the
    session must be opened on that.
    """
    _dynamic_run(tmp_path, monkeypatch)

    fake_client, second, second_exit = _dynamic_run(
        tmp_path,
        monkeypatch,
        listing=(_listed_model("gpt-5.6-terra", ["low", "high"]),),
        answer=_elects("gpt-5.6-terra"),
    )

    assert second_exit == 0, f"the re-electing Run failed: {second_exit}"
    assert len(second["assessments"]) == 1, "a withdrawn model was replayed"
    call = fake_client.create_calls[0]
    assert call["model"] == "gpt-5.6-terra", "the work session ran on the stale route"
    _elected, reassessed = _routing_records(tmp_path)
    assert reassessed["routing_reuse"] == "elected"
    assert reassessed["model"] == "gpt-5.6-terra"


def test_a_run_wide_pin_outranks_a_reusable_route_it_never_consults(
    tmp_path, monkeypatch
) -> None:
    """#565 AC5: a cached result is not authority, and an operator's pin is.

    ADR-0057 keeps an explicit instruction ahead of any inference, and history
    does not promote one inference into an instruction. A pinned Run therefore
    opens its session on the pin, and — because it never even reaches the
    router — leaves no routing record of its own for a later Run to mistake for
    one.
    """
    _dynamic_run(tmp_path, monkeypatch)

    fake_client, second, second_exit = _dynamic_run(
        tmp_path,
        monkeypatch,
        routing_suppressed=True,
        model="gpt-5.6-terra",
        reasoning_effort="low",
    )

    assert second_exit == 0, f"the pinned Run failed: {second_exit}"
    assert second["assessments"] == [], "a pinned Run assessed anyway"
    call = fake_client.create_calls[0]
    assert (call["model"], call["reasoning_effort"]) == ("gpt-5.6-terra", "low")
    assert len(_routing_records(tmp_path)) == 1, "a pinned Run recorded a route"


def test_an_unusable_routing_history_is_diagnosed_and_elected_afresh(
    tmp_path, monkeypatch, capsys
) -> None:
    """#565 AC4: a torn record is not a cached route, and not a dead Run either.

    Reuse is an optimisation over something every Run before it already did,
    so a history that cannot be read costs a selector call and a diagnostic —
    not a refused **Pickup**. What it must never do is pass silently: an
    operator who is quietly paying for every assessment has no way to discover
    the corrupt log causing it.
    """
    _dynamic_run(tmp_path, monkeypatch)
    log = next((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
    log.write_text(
        "".join(
            (raw[: len(raw) // 2] if '"wrapper.routing.resolved"' in raw else raw) + "\n"
            for raw in log.read_text(encoding="utf-8").splitlines()
        ),
        encoding="utf-8",
    )

    _fake, second, second_exit = _dynamic_run(tmp_path, monkeypatch)

    assert second_exit == 0, "a torn routing record refused the whole Pickup"
    assert len(second["assessments"]) == 1, "a torn record was served as a cached route"
    diagnostics = capsys.readouterr().err
    assert "reusable routing history" in diagnostics
    assert log.name in diagnostics, "the diagnosis did not name the unusable log"
