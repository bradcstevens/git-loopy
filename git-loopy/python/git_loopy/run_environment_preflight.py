"""Resolve the environment preconditions a Run needs before it starts (#526)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import gate, labels
from .gh import GhError, GitHubClient, SubprocessGitHubClient, SubprocessLabelClient

__all__ = [
    "RunEnvironmentCheck",
    "RunEnvironmentPreflight",
    "resolve_run_environment_preflight",
]

ExecutableFinder = Callable[[str], str | None]
GitHubAuthStatus = Callable[[], bool]


@dataclass(frozen=True)
class RunEnvironmentCheck:
    """One reportable environment precondition for a Run."""

    name: str
    passed: bool
    detail: str
    remedy: str | None = None
    location: Path | None = None

    @property
    def message(self) -> str:
        """Render the operator-facing failure without repeating existing wording."""
        if self.remedy is None or self.remedy in self.detail:
            return self.detail
        return f"{self.detail}. {self.remedy}"


@dataclass(frozen=True)
class RunEnvironmentPreflight:
    """The complete environment-preflight result shared by Run and diagnostics."""

    checks: tuple[RunEnvironmentCheck, ...]

    @property
    def passed(self) -> bool:
        """Whether every applicable environment precondition passed."""
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[RunEnvironmentCheck, ...]:
        """Every failed precondition, in evaluation order."""
        return tuple(check for check in self.checks if not check.passed)


def resolve_run_environment_preflight(
    *,
    repo_root: Path,
    issue_source: str,
    executable_finder: ExecutableFinder = shutil.which,
    github_auth_status: GitHubAuthStatus | None = None,
    github_client: GitHubClient | None = None,
    label_client: labels.LabelReconcileClient | None = None,
) -> RunEnvironmentPreflight:
    """Evaluate every applicable environment precondition without starting a Run.

    The injected adapters let diagnostics and tests ask the exact question a Run
    asks without changing the host. GitHub authentication is only required by the
    GitHub source; local-markdown Runs deliberately do not depend on ``gh``.
    """
    checks = [_git_check(executable_finder), _copilot_check(executable_finder)]
    if issue_source == "github":
        if github_client is None and github_auth_status is not None:
            checks.append(_github_check(github_auth_status))
        else:
            client = github_client or SubprocessGitHubClient()
            checks.extend(
                (
                    _gh_check(executable_finder),
                    _tracker_check(client),
                )
            )
            if label_client is not None or github_client is None:
                checks.append(
                    _label_vocabulary_check(
                        repo_root,
                        SubprocessLabelClient()
                        if label_client is None
                        else label_client,
                    )
                )
    checks.append(_feedback_loops_check(repo_root))
    return RunEnvironmentPreflight(checks=tuple(checks))


def _git_check(executable_finder: ExecutableFinder) -> RunEnvironmentCheck:
    location = executable_finder("git")
    if location is None:
        return RunEnvironmentCheck(
            name="git",
            passed=False,
            detail="git is not on PATH",
            remedy="Install Git and re-run git-loopy.",
        )
    return RunEnvironmentCheck(
        name="git",
        passed=True,
        detail=f"git resolved at {location}",
        location=Path(location),
    )


def _copilot_check(executable_finder: ExecutableFinder) -> RunEnvironmentCheck:
    location = executable_finder("copilot")
    if location is None:
        return RunEnvironmentCheck(
            name="copilot",
            passed=False,
            detail="copilot is not on PATH",
            remedy="Install the GitHub Copilot CLI and re-run git-loopy.",
        )
    return RunEnvironmentCheck(
        name="copilot",
        passed=True,
        detail=f"copilot resolved at {location}",
        location=Path(location),
    )


def _gh_check(executable_finder: ExecutableFinder) -> RunEnvironmentCheck:
    location = executable_finder("gh")
    if location is None:
        return RunEnvironmentCheck(
            name="gh",
            passed=False,
            detail="gh is not on PATH",
            remedy="Install `gh` from https://cli.github.com/.",
        )
    return RunEnvironmentCheck(
        name="gh",
        passed=True,
        detail=f"gh resolved at {location}",
        location=Path(location),
    )


def _github_check(
    github_auth_status: GitHubAuthStatus | None,
) -> RunEnvironmentCheck:
    auth_status = (
        SubprocessGitHubClient().auth_status
        if github_auth_status is None
        else github_auth_status
    )
    try:
        authenticated = auth_status()
    except GhError as exc:
        return RunEnvironmentCheck(
            name="github",
            passed=False,
            detail=(
                f"gh preflight failed: {exc}. Install `gh` from "
                "https://cli.github.com/."
            ),
            remedy="Install `gh` from https://cli.github.com/.",
        )
    if not authenticated:
        return RunEnvironmentCheck(
            name="github",
            passed=False,
            detail="gh is not authenticated. Run `gh auth login` and re-run git_loopy.",
            remedy="Run `gh auth login` and re-run git_loopy.",
        )
    return RunEnvironmentCheck(
        name="github",
        passed=True,
        detail="gh is authenticated",
    )


def _tracker_check(github_client: GitHubClient) -> RunEnvironmentCheck:
    """Verify the current repository is reachable through the authenticated tracker."""
    auth = _github_check(github_client.auth_status)
    if not auth.passed:
        return auth
    try:
        repo = github_client.repo_view()
    except GhError as exc:
        return RunEnvironmentCheck(
            name="github",
            passed=False,
            detail=f"gh cannot reach this repository: {exc}",
            remedy=(
                "Grant the authenticated `gh` account access to this repository, "
                "then re-run git-loopy."
            ),
        )
    return RunEnvironmentCheck(
        name="github",
        passed=True,
        detail=f"gh is authenticated and can reach {repo.owner}/{repo.name}",
    )


def _label_vocabulary_check(
    repo_root: Path,
    client: labels.LabelReconcileClient,
) -> RunEnvironmentCheck:
    """Judge the complete Label vocabulary without asking the tracker to write."""
    result = labels.reconcile_labels(
        labels.read_tracker_vocabulary(repo_root),
        client,
    )
    if result.unavailable is not None:
        return RunEnvironmentCheck(
            name="label_vocabulary",
            passed=False,
            detail=f"could not read the Label vocabulary: {result.unavailable}",
            remedy=(
                "Restore tracker access, then run `git-loopy labels --apply` to "
                "reconcile the Label vocabulary."
            ),
        )
    if result.divergent:
        names = ", ".join(difference.spec.name for difference in result.divergent)
        return RunEnvironmentCheck(
            name="label_vocabulary",
            passed=False,
            detail=f"Label vocabulary differs: {names}",
            remedy="Run `git-loopy labels --apply` to reconcile the Label vocabulary.",
        )
    return RunEnvironmentCheck(
        name="label_vocabulary",
        passed=True,
        detail=f"{len(result.matched)} Label vocabulary entries match",
    )


def _feedback_loops_check(repo_root: Path) -> RunEnvironmentCheck:
    path = repo_root / "AGENTS.md"
    try:
        loops = gate.parse_feedback_loops(
            path.read_text(encoding="utf-8", errors="replace")
        )
    except OSError as exc:
        return RunEnvironmentCheck(
            name="feedback_loops",
            passed=False,
            detail=f"could not read AGENTS.md: {exc}",
            remedy=(
                "Add AGENTS.md with at least one runnable command in its "
                "`## Feedback loops` table."
            ),
        )
    runnable = [loop for loop in loops if loop.runnable]
    if not runnable:
        return RunEnvironmentCheck(
            name="feedback_loops",
            passed=False,
            detail="AGENTS.md declares no runnable feedback loop",
            remedy=(
                "Add at least one runnable command to AGENTS.md's "
                "`## Feedback loops` table."
            ),
        )
    return RunEnvironmentCheck(
        name="feedback_loops",
        passed=True,
        detail=f"AGENTS.md declares {len(runnable)} runnable feedback loop(s)",
    )
