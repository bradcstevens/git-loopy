"""Tests for the GitHub Actions Execution host (#460).

All Actions interactions are supplied by an in-memory client.  No test
dispatches a real workflow.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from git_loopy import execution_host
from git_loopy.execution_host import (
    ContributionFailure,
    ContributionRequest,
    ContributionSuccess,
    HostPreflightRequest,
)
from git_loopy import github_actions_host
from git_loopy.github_actions_host import (
    ActionsArtifact,
    ActionsJob,
    ActionsRun,
    ActionsStep,
    GitHubActionsExecutionHost,
)
from git_loopy.skill_policy import EffectiveSkillPolicy, SkillPolicyScope


def _request(**overrides: object) -> ContributionRequest:
    defaults: dict[str, object] = {
        "issue_ref": 42,
        "prompt": "implement the issue",
        "base_revision": "a" * 40,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "skill_policy": EffectiveSkillPolicy(
            enabled=("code-review",),
            required=("code-review",),
            legacy_denied=(),
            source_kinds={"code-review": "packaged"},
            base_scope=SkillPolicyScope.GLOBAL,
        ),
        "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    }
    defaults.update(overrides)
    return ContributionRequest(**defaults)  # type: ignore[arg-type]


def _artifact() -> bytes:
    completion = {
        "remote": "https://github.com/octo/example.git",
        "ref": "refs/heads/git-loopy/01ARZ3NDEKTSV4RRFFQ69G5FAV/issue-42",
        "sha": "b" * 40,
        "ending": {
            "outcome": None,
            "progressed": True,
            "termination": "completed",
        },
    }
    event = {
        "ts": "2026-09-09T20:00:00.000Z",
        "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "iter": None,
        "type": "assistant.message",
        "content": "done",
        "observed_monotonic": 123.0,
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("completion.json", json.dumps(completion))
        zipped.writestr("ending.json", json.dumps(completion["ending"]))
        zipped.writestr("events.jsonl", json.dumps(event) + "\n")
    return archive.getvalue()


@dataclass
class _FakeActionsClient:
    workflow_ref: str = "main"
    dispatched: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    get_run_calls: list[int] = field(default_factory=list)
    runs: list[ActionsRun] = field(
        default_factory=lambda: [
            ActionsRun(
                database_id=17,
                display_title="git-loopy 01ARZ3NDEKTSV4RRFFQ69G5FAV issue 42",
                status="completed",
                conclusion="success",
                jobs=(
                    ActionsJob(
                        name="contribution",
                        status="completed",
                        conclusion="success",
                        steps=(
                            ActionsStep(
                                name="Run contribution",
                                status="completed",
                                conclusion="success",
                            ),
                        ),
                    ),
                ),
            )
        ]
    )
    artifact: bytes = field(default_factory=_artifact)
    preflight_runs: list[ActionsRun] = field(
        default_factory=lambda: [
            ActionsRun(
                database_id=18,
                display_title="git-loopy preflight 01ARZ3NDEKTSV4RRFFQ69G5FAV",
                status="completed",
                conclusion="success",
            )
        ]
    )

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self.dispatched.append((workflow, ref, inputs))

    def find_run(self, display_title: str) -> ActionsRun | None:
        return next(
            (
                run
                for run in (*self.runs, *self.preflight_runs)
                if run.display_title == display_title
            ),
            None,
        )

    def get_run(self, database_id: int) -> ActionsRun:
        self.get_run_calls.append(database_id)
        return next(
            run
            for run in (*self.runs, *self.preflight_runs)
            if run.database_id == database_id
        )

    def get_artifact(self, database_id: int, name: str) -> ActionsArtifact:
        assert database_id == 17
        assert name == "git-loopy-01ARZ3NDEKTSV4RRFFQ69G5FAV-issue-42"
        return ActionsArtifact(name=name, archive=self.artifact)


def test_actions_host_declares_remote_machine_capacity() -> None:
    host = GitHubActionsExecutionHost(client=_FakeActionsClient(), capacity=6)

    assert host.placement == "github-actions"
    assert host.isolation_grade == "machine boundary"
    assert host.capacity == 6


def test_actions_host_runs_the_green_base_preflight_before_contributions() -> None:
    client = _FakeActionsClient()
    host = GitHubActionsExecutionHost(client=client, capacity=2)

    result = asyncio.run(
        host.preflight(
            HostPreflightRequest(
                base_revision="a" * 40,
                run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            )
        )
    )

    assert result.passed is True
    assert client.dispatched == [
        (
            "lane-preflight.yml",
            "main",
            {
                "preflight": json.dumps(
                    {
                        "base_revision": "a" * 40,
                        "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    },
                    separators=(",", ":"),
                )
            },
        )
    ]
    assert client.get_run_calls == [18]


def test_actions_host_reports_a_red_green_base_preflight() -> None:
    client = _FakeActionsClient()
    client.preflight_runs[0] = replace(
        client.preflight_runs[0],
        conclusion="failure",
    )
    host = GitHubActionsExecutionHost(client=client, capacity=2)

    result = asyncio.run(
        host.preflight(
            HostPreflightRequest(
                base_revision="a" * 40,
                run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            )
        )
    )

    assert result.passed is False
    assert "concluded 'failure'" in result.detail


def test_actions_host_dispatches_one_named_workflow_for_the_reserved_issue() -> None:
    client = _FakeActionsClient()
    host = GitHubActionsExecutionHost(client=client, capacity=2)

    outcome = asyncio.run(host.run_contribution(_request()))

    assert isinstance(outcome, ContributionSuccess)
    assert client.dispatched == [
        (
            "lane-contribution.yml",
            "main",
            {
                "request": json.dumps(
                    {
                        "issue_ref": 42,
                        "contribution_group": "git-loopy-lane-42",
                        "prompt": "implement the issue",
                        "base_revision": "a" * 40,
                        "model": "gpt-5.6-terra",
                        "reasoning_effort": "high",
                        "skill_policy": {
                            "enabled": ["code-review"],
                            "required": ["code-review"],
                            "legacy_denied": [],
                            "source_kinds": {"code-review": "packaged"},
                            "base_scope": "global",
                            "fallback": None,
                        },
                        "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    },
                    separators=(",", ":"),
                )
            },
        )
    ]
    assert (outcome.remote, outcome.ref, outcome.sha) == (
        "https://github.com/octo/example.git",
        "refs/heads/git-loopy/01ARZ3NDEKTSV4RRFFQ69G5FAV/issue-42",
        "b" * 40,
    )
    assert client.get_run_calls == [17]


def test_actions_host_keeps_backdated_events_but_strips_remote_monotonic_time() -> None:
    host = GitHubActionsExecutionHost(client=_FakeActionsClient(), capacity=1)

    outcome = asyncio.run(host.run_contribution(_request()))

    assert isinstance(outcome, ContributionSuccess)
    assert outcome.events == (
        {
            "ts": "2026-09-09T20:00:00.000Z",
            "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "iter": None,
            "type": "assistant.message",
            "content": "done",
        },
    )


def test_actions_host_returns_a_breach_when_a_post_session_step_fails() -> None:
    client = _FakeActionsClient()
    client.runs[0] = ActionsRun(
        database_id=17,
        display_title=client.runs[0].display_title,
        status="completed",
        conclusion="failure",
    )
    host = GitHubActionsExecutionHost(client=client, capacity=1)

    outcome = asyncio.run(host.run_contribution(_request()))

    assert isinstance(outcome, ContributionFailure)
    assert outcome.reason == "workflow_failed"
    assert outcome.classification == "breach"
    assert outcome.ending is not None
    assert outcome.ending.progressed is True
    assert outcome.events == (
        {
            "ts": "2026-09-09T20:00:00.000Z",
            "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "iter": None,
            "type": "assistant.message",
            "content": "done",
        },
    )


def test_subprocess_actions_client_dispatches_through_gh_api(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(github_actions_host.subprocess, "run", fake_run)
    client = github_actions_host.SubprocessActionsClient("octo/example")

    client.dispatch(
        "lane-contribution.yml",
        "a" * 40,
        {"request": '{"issue_ref":42}'},
    )

    assert commands == [
        [
            "gh",
            "api",
            "--method",
            "POST",
            "repos/octo/example/actions/workflows/lane-contribution.yml/dispatches",
            "-f",
            f"ref={'a' * 40}",
            "-f",
            'inputs[request]={"issue_ref":42}',
        ]
    ]


def test_actions_host_supersedes_by_the_seams_contribution_group() -> None:
    """The host transports the seam's group; it never invents one (#462).

    Spec #445 §D: at most one live contribution per issue is an obligation on
    every host, and Actions satisfies it with a concurrency group that cancels
    in progress. Sending :func:`~git_loopy.execution_host.contribution_group`
    as an input — rather than letting the workflow build a name out of the
    request — is what keeps the guarantee the seam's: the group cannot drift to
    something Run-scoped, so a restarted Run supersedes an orphaned contribution
    for the same issue instead of racing it.
    """
    client = _FakeActionsClient()
    host = GitHubActionsExecutionHost(client=client, capacity=2)

    asyncio.run(host.run_contribution(_request()))

    (_workflow, _ref, inputs) = client.dispatched[0]
    assert json.loads(inputs["request"])["contribution_group"] == (
        execution_host.contribution_group(42)
    )


def test_lane_workflow_uses_the_job_token_and_uploads_only_completion_artifacts() -> None:
    workflow = (
        Path(__file__).parents[3] / ".github/workflows/lane-contribution.yml"
    ).read_text(encoding="utf-8")

    assert "copilot-requests: write" in workflow
    assert "COPILOT_GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "timeout-minutes: 360" in workflow
    assert (
        "concurrency:\n"
        "  group: ${{ fromJSON(inputs.request).contribution_group }}\n"
        "  cancel-in-progress: true"
    ) in workflow
    assert 'git config user.name "github-actions[bot]"' in workflow
    assert (
        'git config user.email "41898282+github-actions[bot]@users.noreply.github.com"'
        in workflow
    )
    assert "./.github/actions/setup-lane-contribution" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "if: always()" in workflow
    assert "${{ runner.temp }}/ending.json" in workflow
    assert "${{ runner.temp }}/events.jsonl" in workflow
    assert "secrets." not in workflow


def test_green_base_workflow_checks_out_base_and_runs_declared_feedback_loops() -> None:
    workflow = (
        Path(__file__).parents[3] / ".github/workflows/lane-preflight.yml"
    ).read_text(encoding="utf-8")

    assert "ref: ${{ fromJSON(inputs.preflight).base_revision }}" in workflow
    assert "./.github/actions/setup-lane-contribution" in workflow
    assert "python -m git_loopy.github_actions_preflight" in workflow
