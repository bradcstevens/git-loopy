"""Tests for the Run environment-preflight seam (#526)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from git_loopy.gh import GhError
from git_loopy.run_environment_preflight import resolve_run_environment_preflight


def test_environment_preflight_reports_an_all_clear_github_run(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        textwrap.dedent(
            """\
            ## Feedback loops

            | Loop | Command |
            | --- | --- |
            | Tests | `uv run pytest` |
            """
        ),
        encoding="utf-8",
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: "/tools/copilot" if name == "copilot" else None,
        github_auth_status=lambda: True,
    )

    assert result.passed
    assert [(check.name, check.passed, check.location) for check in result.checks] == [
        ("copilot", True, Path("/tools/copilot")),
        ("github", True, None),
        ("feedback_loops", True, None),
    ]


@pytest.mark.parametrize(
    ("executable", "authenticated", "agents_md", "failed_check", "remedy"),
    [
        (
            None,
            True,
            "| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
            "copilot",
            "Install the GitHub Copilot CLI",
        ),
        (
            "/tools/copilot",
            False,
            "| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
            "github",
            "`gh auth login`",
        ),
        (
            "/tools/copilot",
            True,
            "| Loop | Command |\n| --- | --- |\n| Placeholder | `<TEST_COMMAND>` |\n",
            "feedback_loops",
            "Add at least one runnable command",
        ),
    ],
)
def test_environment_preflight_reports_each_failed_condition(
    tmp_path: Path,
    executable: str | None,
    authenticated: bool,
    agents_md: str,
    failed_check: str,
    remedy: str,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n" + agents_md,
        encoding="utf-8",
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda _name: executable,
        github_auth_status=lambda: authenticated,
    )

    assert not result.passed
    assert [check.name for check in result.failures] == [failed_check]
    assert result.failures[0].remedy is not None
    assert remedy in result.failures[0].remedy


def test_environment_preflight_reports_missing_gh_with_its_existing_remedy(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )

    def missing_gh() -> bool:
        raise GhError(["gh", "auth", "status"], 127, "gh not found on PATH")

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda _name: "/tools/copilot",
        github_auth_status=missing_gh,
    )

    assert result.failures[0].name == "github"
    assert result.failures[0].detail.startswith("gh preflight failed:")
    assert result.failures[0].remedy == "Install `gh` from https://cli.github.com/."


def test_environment_preflight_continues_after_failures_to_report_every_check(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: calls.append(name) and None,
        github_auth_status=lambda: calls.append("github") and False,
    )

    assert calls == ["copilot", "github"]
    assert [check.name for check in result.failures] == [
        "copilot",
        "github",
        "feedback_loops",
    ]


def test_environment_preflight_uses_the_gate_decoder_for_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(
        b"\xff\n## Feedback loops\n\n"
        b"| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n"
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="prds",
        executable_finder=lambda _name: "/tools/copilot",
    )

    assert result.passed
