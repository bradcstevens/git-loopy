"""Resolve the environment preconditions a Run needs before it starts (#526, #519)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import gate, labels
from .copilot_client import resolve_copilot_base_directory
from .gh import GhError, GitHubClient, SubprocessGitHubClient, SubprocessLabelClient

__all__ = [
    "RunEnvironmentCheck",
    "RunEnvironmentPreflight",
    "resolve_run_environment_preflight",
]

ExecutableFinder = Callable[[str], str | None]


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
    github_client: GitHubClient | None = None,
    label_client: labels.LabelReconcileClient | None = None,
    env: Mapping[str, str] | None = None,
) -> RunEnvironmentPreflight:
    """Evaluate every applicable environment precondition without starting a Run.

    Tracker rows are decided by ``issue_source``; the CA row by the operator's
    Copilot ``sandbox.auth.gh`` setting. Neither depends on injected adapters. The
    adapters only decide *how* a row is answered, and default to the real
    host-backed clients, so a caller that injects one — a Run handing over the
    ``gh`` it already built, a test handing over a double — can never move a row
    in or out. That is what keeps `doctor` honest: it injects nothing, so a row
    it clears is a row the Run would have cleared too (ADR-0055).

    ``gh`` is only asked for by the GitHub source; local-markdown Runs
    deliberately do not depend on it.
    """
    checks = [
        _tool_check("git", executable_finder),
        _tool_check("copilot", executable_finder),
    ]
    if issue_source == "github":
        checks.extend(
            (
                _tool_check("gh", executable_finder),
                _tracker_check(github_client or SubprocessGitHubClient()),
                _label_vocabulary_check(
                    repo_root,
                    label_client or SubprocessLabelClient(),
                ),
            )
        )
    checks.append(_feedback_loops_check(repo_root))
    ca_check = _copilot_proxy_ca_check(os.environ if env is None else env)
    if ca_check is not None:
        checks.append(ca_check)
    return RunEnvironmentPreflight(checks=tuple(checks))


# Documented by `copilot sandbox ca --help`; never infer trust from output text.
_CA_TRUSTED = 0
_CA_UNTRUSTED = 3
_CA_UNSUPPORTED = 4
_CA_ROTATION_REQUIRED = 5


def _copilot_proxy_ca_check(environment: Mapping[str, str]) -> RunEnvironmentCheck | None:
    """Inspect Copilot's own CA verdict; never create, rotate or trust it (ADR-0093)."""
    home = resolve_copilot_base_directory(environment)
    path = home / "settings.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(policy, dict):
            raise ValueError("settings must be an object")
        sandbox = policy.get("sandbox", {})
        if not isinstance(sandbox, dict):
            raise ValueError("sandbox must be an object")
        auth = sandbox.get("auth", {})
        if not isinstance(auth, dict) or not isinstance(auth.get("gh", False), bool):
            raise ValueError("sandbox.auth.gh must be a boolean")
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return RunEnvironmentCheck(
            name="Copilot sandbox settings",
            passed=False,
            detail="could not read sandbox.auth.gh from Copilot settings",
            remedy=f"Check {path} before checking the proxy certificate authority.",
        )
    if auth.get("gh") is not True:
        return None
    # Bind the probe to the same Copilot home as the policy and SDK. Retain
    # host tool/feed settings when the caller supplied only a scope overlay.
    remedy = "Inspect `copilot sandbox ca status` before changing OS trust."
    try:
        result = subprocess.run(
            ["copilot", "sandbox", "ca", "status"],
            env={**os.environ, **environment, "COPILOT_HOME": str(home)},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        detail = "could not verify OS trust (CA status unavailable or timed out)"
    else:
        # Linux uses the per-process bundle: unsupported OS trust is not a
        # broken trust store and must not become an impossible Run prerequisite.
        if result.returncode in {_CA_TRUSTED, _CA_UNSUPPORTED}:
            return None
        if result.returncode == _CA_UNTRUSTED:
            detail = "Copilot's proxy certificate authority is not trusted by the OS"
            remedy = (
                "Run `copilot sandbox ca trust` yourself; git-loopy never changes "
                "OS trust stores, even with `doctor --apply`."
            )
        elif result.returncode == _CA_ROTATION_REQUIRED:
            detail = "Copilot's proxy certificate authority requires rotation"
        else:
            detail = f"could not verify OS trust (CA status exit {result.returncode})"
    return RunEnvironmentCheck(
        name="Copilot proxy CA", passed=False, detail=detail, remedy=remedy,
    )


_TOOL_REMEDIES = {
    "git": "Install Git and re-run git-loopy.",
    "copilot": "Install the GitHub Copilot CLI and re-run git-loopy.",
    "gh": "Install `gh` from https://cli.github.com/.",
}


def _tool_check(name: str, executable_finder: ExecutableFinder) -> RunEnvironmentCheck:
    """Resolve one external tool through ``PATH`` and report where it landed.

    Every tool row goes through here, so "resolved through ``PATH``, reported by
    location" holds for all of them by construction rather than by three
    near-identical functions agreeing with each other.
    """
    location = executable_finder(name)
    if location is None:
        return RunEnvironmentCheck(
            name=name,
            passed=False,
            detail=f"{name} is not on PATH",
            remedy=_TOOL_REMEDIES[name],
        )
    return RunEnvironmentCheck(
        name=name,
        passed=True,
        detail=f"{name} resolved at {location}",
        location=Path(location),
    )


def _tracker_check(client: GitHubClient) -> RunEnvironmentCheck:
    """Judge the tracker the way a Run needs it: authenticated *for this repository*.

    Authentication alone clears a credential that can still not see the
    repository the Run is about to work, which fails later as an opaque `gh`
    error against the first issue query.
    """
    try:
        authenticated = client.auth_status()
    except GhError as exc:
        return RunEnvironmentCheck(
            name="github",
            passed=False,
            detail=f"gh preflight failed: {exc}",
            remedy=_TOOL_REMEDIES["gh"],
        )
    if not authenticated:
        return RunEnvironmentCheck(
            name="github",
            passed=False,
            detail="gh is not authenticated",
            remedy="Run `gh auth login` and re-run git-loopy.",
        )
    try:
        repo = client.repo_view()
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
    """Judge that the Labels a Run *reads* are present, and nothing more.

    Two narrowings, both so this row judges only what a Run actually needs
    (ADR-0055). It asks
    :func:`~git_loopy.labels.read_run_required_vocabulary` rather than the whole
    vocabulary — that function's docstring carries the reasons, which are not
    one reason: the ``task-type:`` and ``vX.Y.Z`` labels are created on the
    way in, while no Run needs a ``wayfinder:`` label to pre-exist — the one it
    reads, ``wayfinder:map``, only excludes a map from the **Pool**. And
    it fails on absence alone: a Run reads and writes Labels
    by name, so a drifted colour or description cannot stop one, and drift stays
    `git-loopy labels`' business.

    Reconciling is read-only by default, so this asks the tracker and writes
    nothing.
    """
    result = labels.reconcile_labels(
        labels.read_run_required_vocabulary(repo_root),
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
    if result.missing:
        names = ", ".join(difference.spec.name for difference in result.missing)
        return RunEnvironmentCheck(
            name="label_vocabulary",
            passed=False,
            detail=f"the tracker is missing Labels: {names}",
            remedy="Run `git-loopy labels --apply` to create the missing Labels.",
        )
    return RunEnvironmentCheck(
        name="label_vocabulary",
        passed=True,
        detail=f"the tracker carries all {len(result.differences)} Labels a Run reads",
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
