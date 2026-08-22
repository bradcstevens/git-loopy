"""``git_loopy.loop`` — async iteration driver for the AFK runner.

This is the orchestrator that ties every previously-merged module
together into a working ``git-loopy`` invocation. It owns:

* The long-running :class:`copilot.CopilotClient` (one per ``git-loopy``
  invocation; reused across iterations).
* The per-run :class:`~git_loopy.persist.WritersBundle`
  (:class:`~git_loopy.persist.EventLogWriter`,
  :class:`~git_loopy.persist.RunSummaryWriter`, and the diagnostics
  logger).
* The per-run :class:`~git_loopy.ui.RunSummary` and
  :class:`~git_loopy.ui.Renderer`.
* The :class:`~git_loopy.wrapper.NMTStrikeStateMachine`.
* The :class:`~git_loopy.sources.IssueSource` — the per-invocation
  backend that discovers AFK-ready work and applies the
  source-specific completion backstop. Constructed from
  :attr:`RunConfig.issue_source` via the module-level
  :func:`_make_issue_source` factory so the loop body is unaware
  whether it is feeding off GitHub issues or local-markdown PRDs.

The per-iteration :class:`~git_loopy.session.IterationSession` is opened
inside :func:`run` once per iteration.

Per-iteration sequence:

1. Cap check on ``max_iterations``.
2. Collect AFK-ready pool via :meth:`IssueSource.collect_afk_ready`.
3. Clean-exit on empty pool.
4. Build prompt: ``"Previous commits: <last5> Issues: <blocks> " + prompt_md``
   where each ``<block>`` is the source-rendered
   :attr:`AfkReadyItem.rendered_block`.
5. Capture ``pre_sha`` *immediately* before invoking the SDK — so a slow
   ``gh issue_view`` call before this point cannot affect the
   ``commits_between(pre_sha, head)`` accounting after.
6. Open :class:`~git_loopy.session.IterationSession`,
   ``await session.send_and_wait(prompt, timeout=long)``.
7. ``head_sha = git.head_sha()``; ``commits = git.commits_between(pre, head)``.
8. Emit one ``wrapper.commit.recorded`` per new commit so the renderer
   increments the iteration's commit count.
9. **Completion backstop** via
   :meth:`IssueSource.handle_completions`. Each returned
   :class:`~git_loopy.sources.Completion` produces one
   ``wrapper.auto_close`` event. The GitHub backend closes issues via
   ``gh issue close``; the PRDs backend returns an empty list (the
   agent owns ``git mv ... prds/<feat>/done/``).
10. **Runner Checkpoint** (ADR-0004) via :meth:`_maybe_checkpoint`: a dirty
    or untracked worktree is staged and captured in a single
    close-keyword-free ``wrapper.checkpoint.recorded`` commit attributed to
    the Active issue. Deliberately ordered *after* the agent-commit
    accounting (step 8) and *before* strike accounting (step 12), so the
    Checkpoint is excluded from both the Summary commit tally and the §6
    progress predicate. Non-fatal: a failure warns and the loop carries on.
11. **Auto-push** (ADR-0004) via :meth:`_maybe_push`: whenever the iteration
    produced new commits — agent commits (step 8) and/or the Checkpoint from
    step 10 — the current branch is pushed to its upstream
    (``wrapper.push.recorded`` on success) so the work reaches the remote.
    Non-fatal: a missing remote/upstream, an auth failure, or a
    non-fast-forward warns and the loop carries on, so a local-only repo
    completes normally.
12. **Strike accounting** (#413, ADR-0041): the **Strike** ceiling counts the
    issues this Run has *given up on*, so nothing is charged here. One Strike
    is charged per issue, at the ending that moves it into the **Attempt
    lifecycle**'s ``skipped`` position (:meth:`_Loop._observe_session_ending`
    in step 10's neighbourhood, the one seam a serial Iteration and a **Lane**
    share). An Iteration that made no progress charges nothing and progress
    resets nothing; Checkpoints and pushes are still *not* progress, which is
    what the §6 predicate reports on the Summary row.
13. Emit ``wrapper.iteration.end`` (renderer closes snapshot panel) and
    persist :class:`~git_loopy.persist.IterationCounters` from the
    closed snapshot.

Design notes:

* **Source-agnostic loop body.** The loop holds one
  :class:`IssueSource` and dispatches the three Protocol methods
  through it. Issue #11 lifts the PRDs backend; #10 introduced the
  GitHub backend. Adding a new backend (e.g. a remote API) means
  adding one ``IssueSource`` impl and one factory branch — the
  iteration body never changes.
* **Inter-module fan-out via the shared ``EventEmitter``.** Every
  wrapper-level event (``wrapper.run.start``, ``wrapper.iteration.start``,
  etc.) goes through :meth:`_emit`, a one-line delegator onto the shared
  :class:`~git_loopy.emit.EventEmitter` (issue #45). The emitter:
  1. Constructs an envelope via :func:`git_loopy.events.make_event`.
  2. Scrubs it **once**, then writes that scrubbed dict as the JSONL line via
     the event log writer — always-on and independent of which sinks are
     registered.
  3. Hands the *same scrubbed* dict to the :class:`~git_loopy.sinks.SinkFanout`,
     which dispatches to every registered sink (issue #22) — so the sinks
     receive an already-scrubbed envelope (the sink contract), closing the
     pre-#45 scrub gap. For the non-interactive path the sole sink is the
     line-printer :class:`~git_loopy.ui.renderer.Renderer`, which drives the
     Rich terminal output and RunSummary accumulator updates; the same fan-out
     is handed to each :class:`~git_loopy.session.IterationSession` so SDK
     events and streaming deltas flow through the identical seam.
* **SDK + source failure containment.** ``send_and_wait`` failures are
  caught and treated as no-progress. Per-issue ``gh.issue_close`` failures are
  logged via the diagnostics logger inside the source impl and the
  loop continues — losing one closure is preferable to skipping the
  rest of the iteration's bookkeeping.
* **One ``CopilotClient`` per invocation.** Constructed lazily inside
  :func:`run` via the module-level :func:`_make_client` factory (which
  tests monkeypatch). Disconnected via ``await client.stop()`` in a
  ``finally`` block so even an early-loop crash releases the SDK's
  subprocess.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import (
    Any,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
    Protocol,
)

from copilot import CopilotClient
from rich.console import Console

from git_loopy import events as events_module
from git_loopy import execution_host as execution_host_module
from git_loopy import gate as gate_module
from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import rolling_pressure
from git_loopy import rolling_scheduler
from git_loopy import session_outcome as session_outcome_module
from git_loopy.staircase import PriceStaircase, StaircaseRefusal
from git_loopy import rollup as rollup_module
from git_loopy import worktree as worktree_module
from git_loopy.active_issue import ActiveIssueBinding
from git_loopy.attempt_lifecycle import AttemptLedger, AttemptState
from git_loopy.config import (
    RoutingResolution,
    RunConfig,
    TaskTypeError,
    resolve_iteration_model,
)
from git_loopy.copilot_client import make_copilot_client
from git_loopy.emit import EventEmitter
from git_loopy.escalation import EscalationLedger
from git_loopy.persist import (
    IterationCounters,
    WritersBundle,
    create_writers,
)
from git_loopy.denomination import (
    BilledCreditsDenomination,
    CostDenomination,
)
from git_loopy.prompt import PromptMetadataError, load_prompt
from git_loopy.rate_card import RateCard
from git_loopy.release_version import ReleaseVersionError, read_runtime_release_version
from git_loopy.skill_install import (
    SkillInstallError,
    describe_refresh,
    installed_catalog_dir,
    refresh_installed_catalog,
)
from git_loopy.rolling_pool import RollingPool, is_parallel_safe
from git_loopy.rollup import IterationRollupAccumulator
from git_loopy.run_readback import run_start_payload
from git_loopy.serial_pickup import (
    SerialPickup,
    pick_serial,
    reason_for,
)
from git_loopy.session import IterationSession
from git_loopy.sinks import EventSink, SinkFanout
from git_loopy.sources import (
    AfkReadyItem,
    GitHubIssueSource,
    IssueSource,
    LABEL_PARALLEL_SAFE,
    PoolCandidate,
    PoolCollection,
    PrdsIssueSource,
    RollingIssueSource,
)
from git_loopy.skill_catalog import discover_skill_catalog as _discover_skill_catalog
from git_loopy.skill_exposure import SkillExposureError
from git_loopy.skill_policy import SkillPolicyResolutionError
from git_loopy.skill_run_preflight import (
    RunSkillPreflight,
    resolve_run_skill_preflight,
)
from git_loopy.task_type_classifier import ClassifierPair
from git_loopy.task_type_pickup import (
    PickupClassifier,
    resolve_pickup_classifier_pair,
)
from git_loopy.task_type_session import SessionTaskTypeProposer
from git_loopy.task_type_writer import TaskTypeLabelClient
from git_loopy.telemetry import otel as telemetry
from git_loopy.ui import Renderer, RunSummary, get_console
from git_loopy.wrapper import (
    NMTStrikeStateMachine,
    checkpoint_message,
    did_iteration_make_progress,
    exit_code_for,
    extract_close_refs,
    filter_to_pool,
)

__all__ = ["run"]


def _build_telemetry_config() -> dict[str, Any] | None:
    """Construct the SDK telemetry config used by :func:`_make_client`.

    Factored out so the OTel telemetry seam is the **single switch** —
    the loop body and the production :func:`_make_client` do not contain
    ``if otel_enabled`` branches. When OTel is disabled,
    :func:`telemetry.build_sdk_telemetry_config` returns ``None`` and
    the SDK skips its telemetry env-var setup; when enabled, the SDK
    sets ``COPILOT_OTEL_ENABLED=true`` and forwards ``OTLP_ENDPOINT``
    when present.

    Returns:
        A :class:`~copilot.client.TelemetryConfig`-shaped dict (or
        ``None`` when OTel is disabled) passed verbatim to the
        ``telemetry`` keyword of :class:`copilot.CopilotClient`. All
        other client knobs (``connection``, ``log_level``, etc.) are
        left at SDK defaults; operators who need custom values can set
        the SDK's documented env vars (e.g. ``COPILOT_CLI_PATH``) — the
        SDK reads them during subprocess setup.
    """
    return telemetry.build_sdk_telemetry_config()


def _make_client() -> CopilotClient:
    """Construct the per-invocation :class:`CopilotClient`.

    Factored to its own module-level function so tests can monkeypatch
    it (``monkeypatch.setattr("git_loopy.loop._make_client", ...)``) to
    return a fake. Production callers get the SDK's default
    construction with the telemetry config produced by
    :func:`_build_telemetry_config` — which is ``None`` (a no-op) when
    OTel is disabled.
    """
    return make_copilot_client(telemetry_config=_build_telemetry_config())


def _make_git_client() -> git_module.SubprocessGitClient:
    """Construct the per-invocation root-bound git client.

    Factored to its own module-level function — mirroring :func:`_make_client`
    — so tests can monkeypatch it
    (``monkeypatch.setattr("git_loopy.loop._make_git_client", ...)``) to inject
    a single fake object (``tests.fakes.FakeGitClient``) instead of
    monkeypatching a dozen ``git.*`` free functions. Production callers get a
    :class:`~git_loopy.git.SubprocessGitClient` discovered from the process cwd:
    it resolves the repository root once (``git rev-parse --show-toplevel``) and
    binds every subsequent git call to it.

    Returns the concrete :class:`~git_loopy.git.SubprocessGitClient` rather than
    the :class:`~git_loopy.git.GitClient` protocol so :func:`run` can read
    ``.root`` (a construction detail, not part of the injected seam) for the
    writers/prompt/source setup before injecting the client into :class:`_Loop`.

    Raises:
        git.GitError: If ``git`` is not on PATH or the cwd is not inside a git
            repository. :func:`run` catches this and exits 1 cleanly.
    """
    return git_module.SubprocessGitClient.discover()


def _make_github_client() -> gh_module.SubprocessGitHubClient:
    """Construct the per-invocation GitHub client.

    Factored to its own module-level function — mirroring :func:`_make_git_client`
    — so tests can monkeypatch it
    (``monkeypatch.setattr("git_loopy.loop._make_github_client", ...)``) to inject
    a single fake object (``tests.fakes.FakeGitHubClient``) instead of
    monkeypatching a handful of ``gh.*`` free functions. Production callers get a
    :class:`~git_loopy.gh.SubprocessGitHubClient`.

    Unlike :func:`_make_git_client` there is **no cwd binding** — ``gh`` runs in
    the process cwd — so the client is stateless and takes no construction
    arguments. Only the ``github`` :class:`IssueSource` backend needs it; the
    ``prds`` backend has no GitHub dependency.
    """
    return gh_module.SubprocessGitHubClient()


def _make_task_type_label_client() -> gh_module.SubprocessTaskTypeLabelClient:
    """Construct the per-invocation **Task-type classifier** label writer (#409).

    Its own factory rather than a reuse of :func:`_make_github_client`, for the
    reason :class:`~git_loopy.gh.SubprocessTaskTypeLabelClient` exists at all:
    the loop *reads* issues through the GitHub client, and every Pool-collecting
    fake in the suite is asserted against that seam. Widening it would make an
    irreversible tracker write a requirement of reading one. Monkeypatchable on
    the same terms as its neighbours, so a test that drives a whole **Run** can
    watch the one write the classifier makes without a tracker.
    """
    return gh_module.SubprocessTaskTypeLabelClient()


def _make_gate_runner() -> gate_module.AgentsMdGateRunner:
    """Construct the per-invocation runner-side Integration gate (#60, ADR-0009).

    Factored to its own module-level function — mirroring :func:`_make_git_client`
    / :func:`_make_github_client` — so the Parallel-mode orchestrator (#61,
    now Rolling-dispatch #219/ADR-0020) and Integration slices (#62/#63)
    inject it, and their tests monkeypatch it
    (``monkeypatch.setattr("git_loopy.loop._make_gate_runner", ...)``) to a scripted
    ``tests.fakes.FakeGateRunner``. Production callers get a
    :class:`~git_loopy.gate.AgentsMdGateRunner`, which runs a worktree's ``AGENTS.md``
    feedback loops as the load-bearing Integration gate.

    **Unused by the serial path.** Integration only exists in Parallel mode; the
    serial loop never gates from the runner side (the agent runs the loops inside its
    own session), so :func:`run` does not call this factory. It ships now purely as
    the injectable seam the parallel slices consume.

    The per-loop wall-clock bound (#374) is resolved here from
    ``GIT_LOOPY_GATE_TIMEOUT_SECONDS``, env-only and inside the factory exactly as
    :func:`_make_worktree_setup` resolves ``GIT_LOOPY_WORKTREE_SETUP`` — both are
    Parallel-mode-only seam knobs, and keeping the factory zero-argument is what
    lets every Integration test keep monkeypatching it with a bare ``lambda:``.
    """
    return gate_module.AgentsMdGateRunner(
        timeout_seconds=gate_module.resolve_gate_timeout_seconds(os.environ)
    )


def _make_worktree_setup() -> worktree_module.WorktreeSetup:
    """Construct the per-Lane worktree setup seam (#65, ADR-0008).

    Factored to its own module-level function — mirroring :func:`_make_gate_runner`
    — so the Parallel-mode orchestrator (#61, now Rolling-dispatch #219/ADR-0020)
    injects it and tests monkeypatch it
    (``monkeypatch.setattr("git_loopy.loop._make_worktree_setup", ...)``) to a
    scripted fake. Production callers get a
    :class:`~git_loopy.worktree.CommandWorktreeSetup` bound to the
    ``GIT_LOOPY_WORKTREE_SETUP`` command when one is set (env-only, like
    :attr:`~git_loopy.config.RunConfig.send_timeout_seconds`'s
    ``GIT_LOOPY_SEND_TIMEOUT_SECONDS``); when it is
    unset or blank the adapter falls back to
    :func:`~git_loopy.worktree.detect_setup_command`'s best-effort auto-detect.

    **Unused by the serial path.** Per-worktree setup only exists in Parallel mode
    (each **Lane** gets a fresh worktree that needs its environment prepared); the
    serial loop works in place on the repo worktree, so :func:`run` only wires this
    into the ``_ParallelLoop``.
    """
    raw = os.environ.get("GIT_LOOPY_WORKTREE_SETUP")
    command = raw.strip() if raw else None
    return worktree_module.CommandWorktreeSetup(command=command or None)


def _make_pressure_monitor(
    *,
    lane_cap: int,
    diag: logging.Logger,
    credit_spent: Callable[[], float | None],
    rate_limits: Callable[[], int | None],
) -> rolling_pressure.PressureMonitor:
    """Construct the bounded adaptive Lane-concurrency seam (#219 §6, #309).

    Factored to its own module-level function — mirroring
    :func:`_make_gate_runner` / :func:`_make_worktree_setup` — so tests
    monkeypatch it
    (``monkeypatch.setattr("git_loopy.loop._make_pressure_monitor", ...)``) and
    drive the reaction table with an injected clock and scripted telemetry
    instead of real time and a live API, which #219 §6 requires.

    Production wires the three injected halves the PRD names: budgets from the
    operator's environment (:meth:`~git_loopy.rolling_pressure.PressureBudgets.from_env`,
    where an unconfigured budget leaves its signal *unknown* rather than
    inventing a threshold), the process run queue for host/setup pressure, this
    Run's own priced Consumption for AI-credit burn, and the ``gh`` seam's
    count of the reads GitHub throttled for the 429 **Pressure signal**.
    """
    return rolling_pressure.PressureMonitor.for_run(
        budgets=rolling_pressure.PressureBudgets.from_env(os.environ),
        lane_cap=lane_cap,
        telemetry=rolling_pressure.RunPressureTelemetry(
            budgets=rolling_pressure.PressureBudgets.from_env(os.environ),
            credit_spent=credit_spent,
            rate_limits=rate_limits,
        ),
        clock=time.monotonic,
        diag=diag,
    )


def _make_issue_source(
    config: RunConfig,
    repo_root: Path,
    diag: logging.Logger,
    *,
    include_prs: bool = False,
) -> IssueSource:
    """Construct the per-invocation :class:`IssueSource`.

    Dispatches on :attr:`RunConfig.issue_source`. Factored to module
    scope so tests can monkeypatch it for end-to-end fakes. Returns a
    :class:`GitHubIssueSource` for ``"github"`` and a
    :class:`PrdsIssueSource` for ``"prds"``.

    Args:
        config: The frozen run configuration.
        repo_root: The resolved repository root (used by the PRDs backend).
        diag: Diagnostics logger handed to the source.
        include_prs: Whether the GitHub backend should also collect
            ``ready-for-agent`` PRs (see :func:`_resolve_include_prs`). The
            PRDs backend ignores it — local-markdown has no PRs.

    Raises:
        ValueError: If ``config.issue_source`` is neither known value.
            Should not happen in practice — :class:`RunConfig` rejects
            unknown values at construction time — but defence-in-depth
            in case the config grows new variants without a matching
            branch here.
    """
    if config.issue_source == "github":
        return GitHubIssueSource(
            diag,
            gh=_make_github_client(),
            include_prs=include_prs,
            pin=config.issue_pin,
            # A **Lane** Pool is `ready-for-agent` *and* `parallel-safe`, so a
            # Parallel invocation must refuse a pin lacking the second — else
            # the pinned issue never enters the Pool, the promotion finds
            # nothing to promote, and the Run silently works the head of the
            # order instead (#396).
            pin_requires_parallel_safe=config.parallel > 1,
        )
    if config.issue_source == "prds":
        return PrdsIssueSource(repo_root, diag)
    raise ValueError(
        f"unknown issue_source {config.issue_source!r}; expected "
        f"'github' or 'prds'"
    )


# Matches the PR-surface flag ``/setup-git-loopy-skills`` writes into
# ``docs/agents/issue-tracker.md`` — e.g. ``**PRs as a request surface: yes.**``.
_RE_PR_SURFACE: re.Pattern[str] = re.compile(
    r"PRs as a request surface:\s*(yes|no)", re.IGNORECASE
)


def _resolve_include_prs(config: RunConfig, repo_root: Path) -> bool:
    """Resolve whether ``ready-for-agent`` PRs join the AFK-ready pool.

    Precedence:

    1. :attr:`RunConfig.include_prs` when not ``None`` — the ``INCLUDE_PRS``
       env override resolved by the CLI.
    2. Otherwise auto-detect from ``docs/agents/issue-tracker.md``: PRs are
       included only when it carries ``PRs as a request surface: yes`` (the
       exact flag ``/setup-git-loopy-skills`` writes and ``/triage`` reads).
    3. ``False`` when the file is missing or the flag is absent / ``no`` — so
       PR support stays off unless a repo has explicitly opted in.

    Only ``issue_source == "github"`` can collect PRs; for ``"prds"`` the
    flag is meaningless (the factory hands it a PRDs source that ignores it).
    """
    if config.include_prs is not None:
        return config.include_prs
    tracker = repo_root / "docs" / "agents" / "issue-tracker.md"
    try:
        text = tracker.read_text(encoding="utf-8")
    except OSError:
        return False
    match = _RE_PR_SURFACE.search(text)
    if match is None:
        return False
    return match.group(1).lower() == "yes"


def _packaged_prompt_path() -> Path:
    """Resolve the packaged default ``PROMPT.md`` to a real filesystem path.

    The wheel ships a default prompt as package data so a bare run in a repo
    with no ``git-loopy/`` folder still has a working prompt — the "run from
    anywhere" story (ADR-0006).
    """
    return Path(str(files("git_loopy") / "PROMPT.md"))


def _installed_skills_path() -> Path:
    """Resolve the Skill catalog installed in the global config scope (ADR-0025)."""
    return installed_catalog_dir(os.environ)


def _read_prompt(repo_root: Path, env: Mapping[str, str]) -> str:
    """Load the runner's prompt, resolving **project > global > packaged** (ADR-0006).

    Resolution order (first hit wins):

    1. **project** — ``<repo>/git-loopy/prompt.md`` then ``<repo>/git-loopy/PROMPT.md``.
       The kit ships the uppercase variant; the lowercase probe keeps
       case-sensitive filesystems (most Linux setups) working while
       case-insensitive ones (HFS+/APFS on macOS) accept either.
    2. **global** — :func:`git_loopy.settings.global_prompt_path` (i.e.
       ``$XDG_CONFIG_HOME/git-loopy/PROMPT.md``, else
       ``$HOME/.config/git-loopy/PROMPT.md``).
    3. **packaged** — the default ``PROMPT.md`` shipped inside the wheel, so a
       fresh install runs anywhere with zero setup.

    ``env`` is injected (rather than read from ``os.environ`` here) so the
    resolver is fully unit-testable against tmp directories. Only raises
    :exc:`FileNotFoundError` if even the packaged default is absent — a
    defensive last resort, since the wheel always ships it.
    """
    return load_prompt(repo_root, env)


def _format_recent_commits(commits: Iterable[git_module.Commit]) -> str:
    """Render the last-5-commits block fed into the prompt prefix.

    One line per commit: sha, date, then the message body terminated by
    ``---``.
    """
    parts: list[str] = []
    for c in commits:
        parts.append(f"{c.sha}\n{c.date}\n{c.message}---")
    if not parts:
        return "No commits found"
    return "\n".join(parts)


def _lane_worktree_path(
    repo_root: Path, run_id: str, issue_number: int | str
) -> Path:
    """Compute a Lane's worktree path (ADR-0008: sibling, outside the repo).

    Lanes live in ``<repo_root>.worktrees/<run_id>/issue-<N>`` — a sibling
    directory of the repository, grouped by run so a run's worktrees are easy
    to find and reap, and one directory per issue so concurrent Lanes never
    share a tree. Kept *outside* the repo so a Lane's worktree is never itself
    picked up as untracked content by the main worktree's git status.
    """
    return (
        repo_root.parent
        / f"{repo_root.name}.worktrees"
        / run_id
        / f"issue-{issue_number}"
    )


_AUTO_RESOLUTION_MAX_ATTEMPTS = 3
"""K — the bound on auto-resolution attempts before serial fallback (#63)."""


_AUTO_RESOLUTION_FALLBACK_COMMENT = (
    "Automated Integration could not land this issue's parallel Lane after "
    f"{_AUTO_RESOLUTION_MAX_ATTEMPTS} auto-resolution attempts (merge conflict "
    "or feedback-loop failure). Base stayed green; falling back to a serial "
    "Iteration and keeping the Lane branch as a breadcrumb. -- git-loopy"
)
"""The single automated breadcrumb left on an issue that fell back to serial."""


def _integration_worktree_path(
    repo_root: Path, run_id: str, issue_number: int | str
) -> Path:
    """Compute a private **Integration stage** worktree path (#307, ADR-0020).

    *Every* Lane contribution is merged and gated in
    ``<repo_root>.worktrees/<run_id>/integrate/issue-<N>`` before anything
    reaches base — a sibling of the Lane worktrees under the same per-run
    directory but in an ``integrate/`` subgroup, so it never collides with the
    Lane's own ``issue-<N>`` worktree. The leaf stays ``issue-<N>`` (matching
    :func:`_lane_worktree_path`) so the worktree still addresses exactly one
    issue. Bounded auto-resolution for a red / conflicting contribution reuses
    the stage that contribution was already staged in, so recovery costs no
    extra worktree.
    """
    return (
        repo_root.parent
        / f"{repo_root.name}.worktrees"
        / run_id
        / "integrate"
        / f"issue-{issue_number}"
    )


@dataclass(frozen=True)
class _LaneSessionSignals:
    """The half of a Lane's ending only its session can see (#403, #405).

    A Lane's ending needs two facts and they are known in two places: how the
    wait on the session came back, and whether the Lane's branch ended up
    carrying anything. This carries the first back to the caller that learns the
    second, so neither has to guess the other.

    The stream's two detections ride here for the same reason: a filtered call
    and a declared end of work are both observed inside the session and mean
    nothing until the branch has been accounted.
    """

    termination: session_outcome_module.SessionTermination
    error: session_outcome_module.SessionError | None
    content_filtered: bool = False
    no_more_tasks: bool = False


def _report_session_outcome(
    diag: logging.Logger,
    *,
    ref: int | str,
    record: session_outcome_module.SessionOutcomeRecord,
) -> None:
    """Say one **Session outcome** once, in the operator's diagnostics.

    A session that advanced its issue reached no ending and says nothing here:
    its productivity is already the Run's own report, and a second line about it
    would be noise on the common good path. What does print is the ending
    together with the **Session error** identity behind it, which is precisely
    what the three flattened warnings this replaced could not say — they knew a
    session had produced nothing and never knew the account had been refused.
    """
    if record.outcome is None:
        return
    error = record.error
    detail = ""
    if error is not None:
        where = error.origin
        if error.status_code is not None:
            where = f"{where} {error.status_code}"
        detail = (
            f"; {error.kind.value} ({where}): "
            f"{error.message or error.error_type or 'no detail reported'}"
        )
    diag.warning(
        "session for #%s ended %s [termination=%s]%s",
        ref,
        record.outcome.value,
        record.termination.value,
        detail,
    )


class _EventObserver(Protocol):
    """Anything that folds raw Events into its own accounting."""

    def observe(self, event: Mapping[str, Any]) -> object:
        """Fold one raw, unscrubbed Event."""
        ...


@dataclass(frozen=True)
class _ChainedObserver:
    """Fan one raw Event out to several accumulators, in order.

    An :class:`~git_loopy.session.IterationSession` takes a single observer,
    and two different questions now need the same stream: the per-**Iteration**
    rollup that builds the Summary, and the Run-scoped Consumption meter
    AI-credit pressure is measured against (#309). Composing beats widening the
    session's seam to a list, which every other caller would then have to
    satisfy.
    """

    observers: tuple[_EventObserver, ...]

    def observe(self, event: Mapping[str, Any]) -> None:
        for observer in self.observers:
            observer.observe(event)


#: ``wrapper.run.end`` outcome for a Run that left through a
#: :class:`BaseException` — an operator's ``Ctrl+C``, the ``SIGHUP`` of a closed
#: terminal, or an :class:`asyncio.CancelledError` from the Dashboard driver.
#:
#: It exists because a driver's ``finally`` has to name an outcome for an exit
#: nobody decided, and every other value it could name is a claim about the Run
#: that is not true. ``iteration_cap`` was that value until #398: a Run
#: configured ``max_iterations=0`` — the cap disabled entirely — reported
#: ``iteration_cap`` when its terminal was closed, so the one outcome the
#: configuration made impossible was the one an interrupted Run recorded, and
#: an operator reading the log could not tell a Run that finished its work from
#: a Run that was killed mid-iteration.
#:
#: Deliberately **not** a :data:`~git_loopy.wrapper.ExitReason`. An interrupted
#: Run does not choose its exit status — the propagating ``KeyboardInterrupt``
#: does — so pinning an exit code to this name would invent a contract for a
#: path the process never returns through, and would owe every other member of
#: the Runner family a ``conformance/exit-codes.json`` case for it.
RUN_OUTCOME_INTERRUPTED = "interrupted"

#: Outcomes whose Run stopped *during* iteration ``iter_num`` rather than after
#: it, so the iteration in flight was never finished and must not be counted.
#: ``iteration_cap`` breaks the loop on the round that would have exceeded the
#: cap, before it runs; an interrupt lands inside one.
_RUN_OUTCOMES_MID_ITERATION = frozenset({"iteration_cap", RUN_OUTCOME_INTERRUPTED})


class _Loop:
    """Stateful orchestrator for one ``git-loopy`` invocation.

    Bundles the long-lived per-run state — writers, summary, sink
    fan-out, SDK client, source, strike state machine — so the public
    :func:`run` function stays small and the per-iteration helper
    methods can read self instead of threading every value through
    their signatures.
    """

    def __init__(
        self,
        *,
        config: RunConfig,
        release_version: str,
        git: git_module.GitClient,
        prompt_text: str,
        denomination: CostDenomination,
        writers: WritersBundle,
        sinks: SinkFanout,
        summary: RunSummary,
        client: CopilotClient,
        skill_preflight: RunSkillPreflight,
        source: IssueSource,
        diag: logging.Logger,
        include_prs: bool = False,
        usage_observer: _EventObserver | None = None,
        rate_card: RateCard | None = None,
        classifier_pair: ClassifierPair | None = None,
        task_type_client: TaskTypeLabelClient | None = None,
    ) -> None:
        self._config = config
        self._release_version = release_version
        # The **Rate card** resolved once at Run start and held fixed for the
        # whole Run (#331, ADR-0026), or `None` when the listing could not be
        # read. Nothing derives from it -- it is published so a replay can audit
        # the bill rather than merely total it.
        self._rate_card = rate_card
        self._git = git
        self._prompt_text = prompt_text
        self._writers = writers
        self._sinks = sinks
        self._client = client
        self._skill_preflight = skill_preflight
        self._skill_exposure = skill_preflight.exposure
        self._source = source
        self._diag = diag
        self._include_prs = include_prs
        self._rollup = IterationRollupAccumulator(denomination=denomination)
        # An extra, Run-scoped Consumption observer (#309). The rollup owns one
        # *current Iteration* slot, so it cannot answer "what has this whole
        # Run spent" — which is what AI-credit pressure is measured against.
        # Chained rather than replacing, so the serial Summary is untouched.
        self._session_observer: _EventObserver = (
            self._rollup
            if usage_observer is None
            else _ChainedObserver((self._rollup, usage_observer))
        )
        # Base branch to restore to after a PR iteration (captured in
        # ``drive`` only when PRs are in scope). ``None`` = unknown / detached
        # HEAD, which disables the defensive restore.
        self._base_branch: str | None = None
        self._strike_machine = NMTStrikeStateMachine(
            max_strikes=config.max_nmt_strikes
        )
        # The last Iteration's **Session outcome** (#403). Held as the record
        # rather than as the line it prints, because the per-issue attempt
        # lifecycle is keyed off the ending; recording it is all that happens
        # here, and no Run-level reaction reads it yet.
        self._last_session_outcome: (
            session_outcome_module.SessionOutcomeRecord | None
        ) = None
        # The **Routing resolution** each candidate reached at the last serial
        # **Pickup** (#394). Refreshed per Iteration by ``_pick_active_issue``:
        # a pair is a fact about one Pool's admission pass, and carrying one
        # across Iterations would run a session on a pair resolved from labels
        # the issue may no longer have. The whole record rather than the bare
        # pair since #407 — the Pickup publishes *why* it resolved what it did,
        # and provenance recomputed at the emit site is provenance that can
        # disagree with the pair the session was built with.
        self._routes: dict[int | str, RoutingResolution] = {}
        # Which issues this Run owes the **Escalation rung** (#408). Run-scoped
        # and in memory: nothing about an issue that stalled tonight is written
        # to the tracker or to a persisted artifact, so a fresh Run re-tests the
        # cheap pair on purpose — the repository has moved. Constructed from the
        # rung the Config resolved, which is ``None`` when escalation is off or
        # an explicit model pin suppressed it, so *"does this Run escalate?"* is
        # answered once here rather than at each Pickup.
        self._escalation = EscalationLedger(rung=config.escalation_rung)
        # How many attempts each issue has left this Run (#412). The second dial
        # one **Session outcome** turns: the ledger above decides whether the
        # *pair* changes, and this one decides whether the issue is worked at
        # all. Run-scoped and in memory for the same reason and with the same
        # consequence — a bad night must never permanently demote an issue, and
        # a **Status** the runner wrote back to the tracker would put it inside
        # the triage state machine it is only ever a consumer of.
        self._attempts = AttemptLedger()
        # The **Task-type classifier**, as this Run's Pickups call it (#409,
        # ADR-0029). Assembled here rather than injected whole because the one
        # thing it must not get wrong is where its **Consumption** goes: the
        # classifying session takes `self._session_observer` as its cost meter,
        # so its credits reach the Run's total exactly as an Iteration's do —
        # ADR-0026 forbids an unknown cost rendering as zero, and a per-issue
        # call billed to nobody is that failure. The observer only exists once
        # this constructor has run, which is why the caller supplies the *pair*
        # and the *tracker seam* and this class supplies the wiring.
        #
        # A `None` pair makes the whole object inert, so neither Pickup carries
        # a second copy of "does this Run classify?".
        self._classifier = PickupClassifier(
            pair=classifier_pair,
            propose=SessionTaskTypeProposer(
                client=self._client,
                config=self._config,
                event_log=self._writers.event_log,
                sinks=self._sinks,
                run_id=self._writers.run_id,
                # The repository root, in both modes. A Lane classifies *before*
                # its worktree exists — the Task type is what decides the pair
                # the Lane is then created for — so there is no Lane path to
                # read, and reading the issue's own content needs none.
                working_directory=None,
                send_timeout_seconds=config.send_timeout_seconds,
                skill_exposure=self._skill_exposure,
                cost_meter=self._session_observer,
                warn=self._diag.warning,
            ),
            client=(
                task_type_client
                if task_type_client is not None
                else _make_task_type_label_client()
            ),
            diag=self._diag,
        )
        # The one scrub-and-fan-out seam (issue #43): compose -> scrub once ->
        # write the replay JSONL + fan out to the sinks. Built here so ``_emit``
        # is a one-line delegator and the sinks receive the *scrubbed* envelope
        # by construction — #45 closed the loop's scrub gap (the pre-#45 inline
        # copy fanned the raw envelope out to the sinks). ``diag=self._diag``
        # preserves the loop's warn-and-continue policy on a write / sink failure.
        self._emitter = EventEmitter(
            run_id=self._writers.run_id,
            event_log=self._writers.event_log,
            sinks=self._sinks,
            diag=self._diag,
            observer=self._rollup,
        )

    @property
    def finalized_contributions(self) -> tuple[rolling_scheduler.Contribution, ...]:
        """Always ``()``: serial finalizes no **Lane contribution** (#366).

        Stated in code rather than left to a ``getattr`` default, so *"serial
        demotes nothing"* is a property of this class and not an accident of
        spelling: a later rename of the Parallel property would otherwise switch
        **Demotion** silently off there too, and the only symptom would be a
        tally that is always empty.

        Serial has nothing to offer even in principle, and since ADR-0037 that
        is a fact about the *record* rather than about the pair. A
        :class:`~git_loopy.rolling_scheduler.Contribution` is a **Lane
        contribution** — opened when a Lane's session starts and closed at its
        terminal disposition — and a serial Run opens none. The **Routed pair**
        a serial **Iteration** runs on is now genuinely the one its **Pickup**
        resolved, so a **Demotion** would have something true to measure; what
        it lacks is the row to measure it on. Building one is its own slice.
        """
        return ()

    # -- event fan-out ------------------------------------------------------

    def _emit(
        self,
        event_type: str,
        *,
        iter_num: int | None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Compose, scrub, persist, then fan out one wrapper-level event.

        Delegates to the shared :class:`~git_loopy.emit.EventEmitter` (issue
        #45): it composes the envelope via :func:`git_loopy.events.make_event`,
        scrubs it **once**, writes that scrubbed dict as the replay JSONL line
        (always-on, independent of the sink list), and fans the *same scrubbed*
        dict out to the :class:`~git_loopy.sinks.SinkFanout` — so the on-screen
        sinks receive an already-scrubbed envelope (the sink contract), not the
        raw one the pre-#45 inline copy leaked. The write and render are each
        individually guarded; on failure the emitter warns via the loop's
        ``diag`` (warn-and-continue). Returns the composed **pre-scrub** envelope
        so callers can still read the SHA / subject off their own events.
        """
        return self._emitter.emit(event_type, iter_num=iter_num, **payload)

    def _report_pool_exclusions(
        self, collection: PoolCollection, *, iter_num: int
    ) -> None:
        """Emit one Event per ``ready-for-agent`` candidate the Pool dropped.

        Issue #303. The AFK-ready discriminator has always made this decision;
        it just never said so, and a human who had deliberately triaged an
        issue had no way to learn the runner was ignoring it. The Event is the
        carrier rather than a log line so the Dashboard and replay see it too.
        """
        for exclusion in collection.exclusions:
            self._emit(
                events_module.WRAPPER_POOL_EXCLUDED,
                iter_num=iter_num,
                issue=exclusion.ref,
                title=exclusion.title,
                reason=exclusion.reason,
            )

    # -- Pickup records (#397) ---------------------------------------------

    def _emit_pickup_bound(
        self,
        *,
        iter_num: int | None,
        issue: int | str,
        reason: str,
        position: int,
        considered: int,
        resolution: RoutingResolution | None = None,
    ) -> None:
        """Record which issue a **Pickup** bound, why, and out of what (#397).

        The payload shape is declared here and at
        :meth:`_emit_pickup_skipped`, once each, because both call sites — a
        serial **Iteration** and a **Lane** — have to produce the same record
        for a replay to read them as one vocabulary.

        ``position`` and ``considered`` travel together on purpose. Position
        alone answers "was this the head of the order?", and the question an
        operator actually asks is "did the runner take the oldest, or was this
        the only one left?" — which needs both. Neither is recoverable later:
        afterwards the order that gave them meaning is gone.

        ``resolution`` carries the **Routing resolution** onto the same record
        (#407) rather than onto a second Event: the pair resolves *at* this
        instant, for *this* key, so a second record would be one Pickup
        described twice. It is optional here because the payload is
        optional-when-present on the wire — a Runner that implements no routing
        has nothing to say and stays conformant by saying nothing — and because
        an emit-site default of "no routing" is the only honest one for a
        caller that has not resolved a pair.
        """
        self._emit(
            events_module.WRAPPER_PICKUP_BOUND,
            iter_num=iter_num,
            issue=issue,
            reason=reason,
            position=position,
            considered=considered,
            **(resolution.as_pickup_payload() if resolution is not None else {}),
        )

    def _emit_pickup_skipped(
        self,
        *,
        iter_num: int | None,
        issue: int | str,
        reason: str,
        position: int,
        considered: int,
    ) -> None:
        """Record one candidate a **Pickup** passed over, and why (#397).

        A record rather than a log line, and that is the entire point: the
        starvation ADR-0032 fixes was invisible *precisely* because being
        passed over left no trace, so an issue could be skipped fifty times and
        the only evidence was that it was still in the backlog. Fifty log lines
        are not evidence a replay or the Dashboard can show.

        Emitted before the :meth:`_emit_pickup_bound` that ended the walk, the
        way an exclusion precedes the collection it explains — so a replay
        reads what was passed over and then what was taken instead.
        """
        self._emit(
            events_module.WRAPPER_PICKUP_SKIPPED,
            iter_num=iter_num,
            issue=issue,
            reason=reason,
            position=position,
            considered=considered,
        )

    def _emit_skill_policy_resolved(self) -> None:
        if self._skill_preflight.migration_warning:
            print(
                "git-loopy: active prompt has no required-skills metadata; "
                "inherited packaged Required Skills for this Run. Add "
                "required-skills frontmatter to complete migration.",
                file=sys.stderr,
            )
        self._emit(
            events_module.WRAPPER_SKILL_POLICY_RESOLVED,
            iter_num=None,
            **self._skill_preflight.event_payload,
        )

    def _new_active_issue_binding(
        self,
        iter_num: int | None,
        *,
        allowed_refs: Iterable[int | str],
        lane_issue: int | str | None = None,
    ) -> ActiveIssueBinding:
        """Create the immutable Active-issue publisher for one Iteration or Lane.

        ``iter_num`` is ``None`` for a Lane contribution under Rolling
        dispatch (#219, ADR-0020): a contribution outlives any single round
        or session number, so its ``wrapper.issue.activated`` carries
        ``iter: null`` like every other Lane-scoped event, distinguished
        instead by ``lane_issue``.
        """

        def publish(ref: int | str, source: str, at: datetime) -> None:
            envelope = events_module.make_event(
                events_module.WRAPPER_ISSUE_ACTIVATED,
                run_id=self._writers.run_id,
                iter=iter_num,
                ts=at,
                issue=ref,
                activated_at="",
                binding_source=source,
            )
            envelope["activated_at"] = envelope["ts"]
            if lane_issue is not None:
                envelope["lane_issue"] = lane_issue
            self._emitter.dispatch(envelope)

        return ActiveIssueBinding(
            publish=publish,
            warn=lambda message: self._diag.warning("%s", message),
            allowed_refs=allowed_refs,
        )

    # -- iteration body ----------------------------------------------------

    async def _run_one_iteration(
        self, iter_num: int
    ) -> tuple[str, int, int]:
        """Run a single AFK iteration.

        Returns:
            ``(outcome, commits_in_iter, auto_closures_in_iter)``.

            ``outcome`` is one of:

            * ``"continue"`` — iteration completed, loop should keep going.
            * ``"empty_pool"`` — AFK-ready pool was empty; clean exit 0.
            * ``"aborted"`` — NMT strike machine tripped; abort exit 1.

        OTel span tree: opens ``git_loopy.iteration`` for the entire body,
        with three children — ``git_loopy.collect_issues`` around the
        pool discovery, ``git_loopy.session`` around the SDK session
        lifecycle, and ``git_loopy.enforce_closures`` around the
        source-specific completion backstop. The empty-pool path emits
        only the partial subtree (no ``session`` / ``enforce_closures``
        spans); see
        ``tests/test_iteration_end_to_end.py::test_loop_emits_otel_span_tree_when_enabled``.
        """
        with telemetry.span(
            "git_loopy.iteration", iter=iter_num
        ) as iteration_span:
            iteration_started_at = datetime.now(timezone.utc)
            self._emit(
                events_module.WRAPPER_ITERATION_START,
                iter_num=iter_num,
                ts=iteration_started_at,
            )

            # 1) PR branch hygiene. A prior PR iteration may have run
            #     `gh pr checkout <N>` and left HEAD on the PR branch. The
            #     worktree is clean (the guard above just passed), so restore
            #     the captured base branch — otherwise this iteration's
            #     commits and `commits_between` accounting would land on the
            #     PR branch. Gated on `include_prs` so the default (issues-only)
            #     path is byte-for-byte unchanged and never touches branches.
            if self._include_prs and self._base_branch is not None:
                try:
                    on_branch = self._git.current_branch()
                except git_module.GitError as exc:
                    self._diag.warning(
                        "current_branch check failed: %s; skipping base "
                        "restore",
                        exc,
                    )
                    on_branch = None
                if on_branch is not None and on_branch != self._base_branch:
                    try:
                        self._git.switch(self._base_branch)
                        self._diag.info(
                            "restored base branch %s (iteration started on %s)",
                            self._base_branch,
                            on_branch,
                        )
                    except git_module.GitError as exc:
                        self._diag.warning(
                            "could not restore base branch %s: %s; "
                            "continuing on %s",
                            self._base_branch,
                            exc,
                            on_branch,
                        )

            # 2) Collect AFK-ready pool via the source.
            with telemetry.span("git_loopy.collect_issues"):
                collection = self._source.collect_pool()
            pool = list(collection.items)
            pool_refs: list[int | str] = [item.ref for item in pool]
            # Late-bind the iteration span's `issue` / `issues` attributes
            # now that we know the pool. `set_attribute` is no-op-safe so
            # this works whether OTel is enabled or not.
            if pool_refs:
                iteration_span.set_attribute("issue", pool_refs[0])
                iteration_span.set_attribute("issues", pool_refs)
            # Report what the discriminator dropped BEFORE the collection it
            # explains (#303), so a replay reads the exclusions and then the
            # Pool they were taken out of.
            self._report_pool_exclusions(collection, iter_num=iter_num)
            self._emit(
                events_module.WRAPPER_AFK_READY_COLLECTED,
                iter_num=iter_num,
                issues=pool_refs,
                excluded=len(collection.exclusions),
            )
            if not pool:
                # Close the iteration cleanly so the snapshot lifecycle is
                # consistent even on the empty-pool path.
                self._finish_iteration(iter_num, outcome="empty_pool")
                return ("empty_pool", 0, 0)

            # 2a) Serial **Pickup** (#394, ADR-0032). The runner binds one
            #     issue *before* any session exists, taking the head of the
            #     §3.2 order `collect_pool` already put the Pool in. Until this
            #     slice the whole Pool went into one prompt and `PROMPT.md` told
            #     the agent to self-select, so list position was a rendering
            #     hint competing with an instruction to ignore it — an issue
            #     could be passed over indefinitely and nothing noticed.
            pickup = await self._pick_active_issue(pool, iter_num=iter_num)
            if pickup.item is None:
                # Not the empty-Pool outcome, and it must not be reported as
                # one: there *was* work and none of it could be taken, which is
                # a Run going nowhere rather than a Run that is finished.
                self._diag.error(
                    "serial Pickup bound nothing: all %d candidate(s) in the "
                    "Pool were skipped; this Iteration worked no issue",
                    len(pool),
                )
                return self._finish_unworked_iteration(iter_num)
            active = pickup.item
            resolution = self._routes[active.ref]
            model, reasoning_effort = resolution.model, resolution.reasoning_effort
            iteration_span.set_attribute("issue", active.ref)
            issue_binding = self._new_active_issue_binding(
                iter_num, allowed_refs=(item.ref for item in pool)
            )
            issue_binding.bind(
                active.ref,
                source="serial_pickup",
                at=datetime.now(timezone.utc),
            )
            self._diag.info(
                "serial Pickup bound #%s (%s, position %d of %d)",
                active.ref,
                pickup.reason,
                pickup.position,
                len(pool),
            )

            # 3) Build prompt (last-5 commits + the bound issue's block +
            #    prompt body). Exactly one issue: the agent is told which issue
            #    it is working and is not offered a menu.
            try:
                recent = self._git.recent_commits(5)
            except git_module.GitError as exc:
                self._diag.warning("recent_commits failed: %s; using empty prefix", exc)
                recent = []
            commits_block = _format_recent_commits(recent)
            issues_block = active.rendered_block
            prompt = (
                f"Previous commits: {commits_block} "
                f"Issues: {issues_block} {self._prompt_text}"
            )

            # 4) Capture pre_sha *after* the slow source-collection step so
            #    any commit that landed while we were enriching the pool
            #    isn't incorrectly attributed to this iteration.
            try:
                pre_sha = self._git.head_sha()
            except git_module.GitError as exc:
                self._diag.error("git head_sha failed: %s; aborting iteration", exc)
                self._finish_iteration(iter_num, outcome="no_progress")
                return ("continue", 0, 0)

            # 5) Run the SDK session. How it ends is *data* (#403): the ending
            #    and, where there was a failure, its structured identity are
            #    resolved below rather than spent on a log line, because the
            #    attempt lifecycle is keyed off the ending and a sentence is not
            #    a key. Recording only — nothing here aborts the Run, waits, or
            #    leaves the issue alone, so an account-level condition is still
            #    attributed to the issue whose Iteration met it.
            send_timeout = self._config.send_timeout_seconds
            termination = session_outcome_module.SessionTermination.COMPLETED
            raised: session_outcome_module.SessionError | None = None
            # The harness reports a refused call on the Event stream and lets the
            # session finish politely, so the `except` clauses below see nothing
            # at all in exactly the case worth explaining. The watch is what
            # reads it, joined to the Run's own observer rather than replacing it.
            session_watch = session_outcome_module.SessionOutcomeWatch()
            with telemetry.span("git_loopy.session"):
                try:
                    async with IterationSession(
                        self._client,
                        config=self._config,
                        event_log=self._writers.event_log,
                        sinks=self._sinks,
                        run_id=self._writers.run_id,
                        iter_num=iter_num,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        issue_binding=issue_binding,
                        skill_exposure=self._skill_exposure,
                        event_observer=_ChainedObserver(
                            observers=(self._session_observer, session_watch)
                        ),
                    ) as sdk_session:
                        try:
                            await sdk_session.send_and_wait(
                                prompt, timeout=send_timeout
                            )
                        except asyncio.TimeoutError:
                            termination = (
                                session_outcome_module.SessionTermination.TIMED_OUT
                            )
                        except Exception as exc:
                            # Contained exactly as before: the bookkeeping below
                            # still runs and the Iteration is accounted as
                            # no-progress. What changed is that the ending is
                            # now recoverable rather than only readable.
                            termination = (
                                session_outcome_module.SessionTermination.CRASHED
                            )
                            raised = session_outcome_module.SessionError.from_exception(
                                exc, origin="send"
                            )
                except Exception as exc:
                    termination = session_outcome_module.SessionTermination.CRASHED
                    raised = session_outcome_module.SessionError.from_exception(
                        exc, origin="session_lifecycle"
                    )

            # 6) Post-iteration accounting.
            try:
                head = self._git.head_sha()
            except git_module.GitError as exc:
                self._diag.warning(
                    "post-iteration git head_sha failed: %s; "
                    "skipping commit accounting", exc,
                )
                head = pre_sha
            try:
                new_commits = self._git.commits_between(pre_sha, head)
            except git_module.GitError as exc:
                self._diag.warning(
                    "post-iteration commits_between failed: %s; "
                    "skipping commit accounting", exc,
                )
                new_commits = []

            # 7) Completion backstop — source-specific. The GitHub backend
            #    closes the issue via gh; the PRDs backend always returns
            #    [] (the agent owns the `git mv ... done/` step).
            with telemetry.span("git_loopy.enforce_closures"):
                completions = self._handle_completions_safely(pool, new_commits)

            if issue_binding.active_ref is None:
                fallback = self._infer_active_binding(
                    pool, completions, new_commits
                )
                if fallback is not None:
                    ref, source = fallback
                    issue_binding.bind(
                        ref,
                        source=source,
                        at=iteration_started_at,
                    )

            for c in new_commits:
                self._emit(
                    events_module.WRAPPER_COMMIT_RECORDED,
                    iter_num=iter_num,
                    sha=c.sha,
                    subject=c.subject,
                    date=c.date,
                )

            for completion in completions:
                if getattr(completion, "kind", "issue") == "pr":
                    # A PR advance (head SHA moved). Different event so the
                    # renderer says "advanced PR #N" rather than
                    # "auto-closed #N"; still counted toward progress below.
                    self._emit(
                        events_module.WRAPPER_PR_ADVANCED,
                        iter_num=iter_num,
                        pr=completion.ref,
                        sha=completion.sha,
                        shas=list(completion.shas),
                    )
                else:
                    self._emit(
                        events_module.WRAPPER_AUTO_CLOSE,
                        iter_num=iter_num,
                        issue=completion.ref,
                        sha=completion.sha,
                        shas=list(completion.shas),
                    )
            auto_closures = sum(
                1
                for completion in completions
                if getattr(completion, "kind", "issue") != "pr"
            )
            pr_advances = len(completions) - auto_closures
            completion_count = len(completions)

            # 8) Runner Checkpoint (ADR-0004). Capture any dirty / untracked
            #    work-in-progress in a single close-keyword-free Checkpoint
            #    commit so the next iteration starts from a clean tree and no
            #    work is ever lost. Deliberately AFTER the agent-commit
            #    accounting above (step 6) and BEFORE the Strike machine below,
            #    so the Checkpoint is structurally excluded from both: it never
            #    counts as a commit in the Summary (it emits
            #    ``wrapper.checkpoint.recorded``, not ``wrapper.commit.recorded``)
            #    and it never resets a Strike. Non-fatal — a failure warns and
            #    the loop carries on (a local-only repo still completes).
            checkpoint_sha = self._maybe_checkpoint(
                iter_num, issue_binding.active_ref
            )

            # 9) Auto-push (ADR-0004, second half). Whenever this iteration
            #    produced new commits — agent commits (step 6) and/or the
            #    Checkpoint just made (step 8) — push the current branch to its
            #    upstream so the work reaches the remote instead of piling up
            #    locally. Non-fatal: a missing remote/upstream, an auth failure,
            #    or a non-fast-forward warns and the loop carries on. Like the
            #    Checkpoint, a push is NOT Strike progress (it creates no commit).
            self._maybe_push(iter_num, new_commits, checkpoint_sha)

            # 10) Strike state machine + emit appropriate events.
            commits_in_iter = len(new_commits)
            checkpoints_in_iter = int(checkpoint_sha is not None)
            made_progress = did_iteration_make_progress(
                commits_in_iter,
                auto_closures,
                checkpoints_in_iter=checkpoints_in_iter,
                pr_advances_in_iter=pr_advances,
            )
            # The **Session outcome** (#403), resolved here because the ending is
            # a joint fact: how the session came back, and whether the Run
            # observed anything durable from it. `made_progress` is §6's own
            # predicate rather than a second notion of progress invented beside
            # it — a Run that scores an Iteration as productive must not describe
            # the same Iteration as having got nowhere.
            #
            # The watch's two detections (#405) are what make the remaining two
            # endings reachable: a turn with a filtered call, and an Agent that
            # declared its issue unworkable.
            session_ending = session_outcome_module.resolve_session_outcome(
                termination=termination,
                progressed=made_progress,
                error=session_outcome_module.strongest_error(
                    session_watch.error, raised
                ),
                content_filtered=session_watch.content_filtered,
                no_more_tasks=session_watch.no_more_tasks,
            )
            # Recorded *before* the Strike is read rather than after (#413): the
            # ending is now what charges the ceiling — through the **Attempt
            # lifecycle** it feeds — so a machine read first would report the
            # count as it stood before this Iteration's own defeat.
            self._record_session_outcome(
                active.ref, session_ending, iter_num=iter_num
            )
            outcome = self._strike_machine.tick(
                commits_in_iter=commits_in_iter,
                auto_closures_in_iter=auto_closures,
                checkpoints_in_iter=checkpoints_in_iter,
                pr_advances_in_iter=pr_advances,
            )

            # 11) Close the iteration snapshot, persist counters.
            self._finish_iteration(
                iter_num,
                outcome="aborted" if outcome == "aborted" else None,
            )

            if outcome == "aborted":
                return ("aborted", len(new_commits), completion_count)
            return ("continue", len(new_commits), completion_count)

    def _maybe_checkpoint(
        self,
        iter_num: int,
        active_ref: int | str | None,
    ) -> str | None:
        """Capture a dirty / untracked worktree in one Checkpoint commit.

        The runner-authored safety net of ADR-0004. If the worktree has any
        uncommitted tracked change (:func:`git.is_dirty`) or any untracked,
        non-ignored file (:func:`git.has_untracked`), stage everything
        (``git add -A``, honouring ``.gitignore``) and make a single
        **close-keyword-free** Checkpoint commit attributed to the Active issue
        (so the auto-close backstop never fires on it), then emit
        ``wrapper.checkpoint.recorded``.

        Every git interaction is wrapped: a missing remote, an empty index, or
        any other :exc:`git.GitError` warns and returns ``None`` rather than
        aborting the run, so a clean tree, a non-repo, and a local-only repo all
        complete normally.

        Returns:
            The new Checkpoint commit SHA, or ``None`` when nothing was
            captured (clean tree) or the Checkpoint could not be made.
        """
        try:
            dirty = self._git.is_dirty()
            untracked = self._git.has_untracked()
        except git_module.GitError as exc:
            self._diag.warning(
                "checkpoint dirty-check failed: %s; skipping checkpoint", exc
            )
            return None
        if not (dirty or untracked):
            return None

        try:
            self._git.add_all()
            sha = self._git.commit(checkpoint_message(active_ref))
        except git_module.GitError as exc:
            self._diag.warning(
                "checkpoint commit failed: %s; continuing without it", exc
            )
            return None

        self._emit(
            events_module.WRAPPER_CHECKPOINT_RECORDED,
            iter_num=iter_num,
            sha=sha,
            issue=active_ref,
        )
        self._diag.info(
            "recorded checkpoint %s (attributed to %s)", sha, active_ref
        )
        return sha

    # -- serial Pickup -----------------------------------------------------

    def _resolve_route(
        self, item: AfkReadyItem, *, warn: Callable[[str], None]
    ) -> RoutingResolution:
        """The **Routing resolution** one **Pickup** binds this candidate with.

        The single seam both Pickups — a serial **Iteration**'s and a **Lane**'s
        — resolve through, so an escalated issue is escalated wherever it is
        next picked up rather than only in the mode that stalled it. A silent
        no-progress **Lane** is never auto-resolved and its issue is only ever
        re-taken by a later serial round, so a per-mode rule would put the
        escalation on the side of the boundary that cannot act on it.

        Escalation is **one rung and one lookup** on top of ordinary routing,
        deliberately: the rung is resolved by re-running the same pure resolver
        with the ledger's pair, so a routed, a default and an escalated pair are
        gated by identical code (§14) instead of the escalated one taking a path
        of its own. The ordinary resolution runs first and keeps ``warn``, so an
        escalated Pickup still says its issue carries conflicting ``task-type:``
        labels; the escalated re-resolve takes no sink because a supplied rung
        skips label selection entirely and has no advisory to raise.

        **The no-op rule.** When the rung gates to the pair this issue would
        have run on anyway — ``planning`` already routes to the rung by design
        (ADR-0035) — nothing escalated, and the record says so: the **Routing
        source** stays the one that chose the pair. The comparison is between
        *gated* pairs rather than authored ones, because an effort the model
        rejects is dropped and two different authored pairs can arrive at one
        real pair. What still changes is the **lifecycle position**: this is the
        issue's second attempt whether or not there was anywhere to escalate
        to, and source and position are separate axes precisely so that a
        same-pair retry stays tellable from an escalated one.

        **The position comes from the Attempt lifecycle, not from the rung**
        (#412). Both ledgers read the same ending, but only the lifecycle sees
        every ending — a crash retries the issue on the pair it already had, and
        a position derived from the rung would report that second attempt as a
        first one. It is asked once, before either branch, so the escalated and
        the unescalated resolution state the same fact about the same issue.
        """
        routed = resolve_iteration_model(self._config, item.labels, warn=warn)
        position = self._attempts.lifecycle_position(item.ref)
        rung = self._escalation.owed(item.ref)
        if rung is None:
            if position is routed.lifecycle_position:
                return routed
            return dataclass_replace(routed, lifecycle_position=position)
        escalated = resolve_iteration_model(
            self._config,
            item.labels,
            lifecycle_position=position,
            escalated_pair=rung,
        )
        if (escalated.model, escalated.reasoning_effort) == (
            routed.model,
            routed.reasoning_effort,
        ):
            return dataclass_replace(routed, lifecycle_position=position)
        self._diag.info(
            "issue #%s escalated to %s @ %s after a no-progress session",
            item.ref,
            escalated.model,
            escalated.reasoning_effort,
        )
        return escalated

    async def _classify_at_pickup(
        self, item: AfkReadyItem, *, routed: RoutingResolution
    ) -> tuple[AfkReadyItem, RoutingResolution]:
        """Read ``item``'s **Task type** off its own content, then re-route on it.

        The **Task-type classifier**'s one production call site (#409, ADR-0029),
        shared by both Pickups for the reason :meth:`_resolve_route` is: an issue
        admitted after the last hand-labelling pass carries no ``task-type:``
        label in *either* mode, and a classifier wired to one of them would leave
        the other permanently on the **Default pair**.

        It runs **after** the candidate is bound, never over the Pool. Admission
        is what decides *which* issue is worked and it decides that from what the
        tracker says; classification only ever changes what the issue *costs*, so
        classifying a candidate the walk then passed over would spend an **AI
        Credit** on an issue this Iteration does not work. That ordering costs a
        second call to the pure resolver on the one issue that gained a label,
        and buys the guarantee that the Run pays for exactly the classifications
        it uses.

        The re-resolution goes through :meth:`_resolve_route` rather than
        patching the pair onto ``routed``, so an inferred label and a
        hand-written one are gated, escalated and provenanced by identical code —
        which is the whole of what "indistinguishable in effect" means once the
        label exists.

        Args:
            item: The bound candidate.
            routed: What :meth:`_resolve_route` already resolved for ``item`` as
                the tracker had it. Kept as the answer wherever classification
                changes nothing, so an inert or already-labelled Pickup is
                byte-for-byte what it was before this seam existed.

        Returns:
            The item to work and the **Routing resolution** to work it on. Never
            raises: a classification is not an **Iteration**, and no way of
            failing to acquire a label may cost the issue its Iteration or its
            **Strike** count.
        """
        labelled = await self._classifier.labelled(item)
        if labelled is item:
            return item, routed
        try:
            resolution = self._resolve_route(labelled, warn=lambda _message: None)
        except TaskTypeError as exc:
            # Unreachable while the classifier writes only closed-taxonomy keys,
            # and handled anyway: by this point the issue is *bound*, so the
            # refusal that would have been a skip at admission has nowhere to go
            # but the Iteration. Keeping the admitted pair loses the inference
            # and nothing else.
            self._diag.warning(
                "issue #%s: inferred task type did not re-route (%s); keeping "
                "the pair its Pickup admitted it on",
                item.ref,
                exc,
            )
            return item, routed
        self._diag.info(
            "issue #%s classified as %s; routed to %s @ %s",
            labelled.ref,
            ", ".join(resolution.task_type_keys) or "nothing",
            resolution.model,
            resolution.reasoning_effort,
        )
        return labelled, resolution

    async def _pick_active_issue(
        self, pool: list[AfkReadyItem], *, iter_num: int
    ) -> SerialPickup:
        """Bind one **Active issue** out of the ordered **Pool** (#394).

        The **Pickup** a serial **Iteration** did not have until ADR-0032. The
        Pool arrives in Wrapper contract §3.2 order — sequence is decided at the
        read (:func:`git_loopy.sources.in_selection_order`), never here — so
        this walks it front to back and takes the first candidate it can admit.

        **Admission is where the Routed pair resolves**, which is the only place
        it can: the pair is what the session below is constructed with, and a
        pair resolved after the session started would be a pair the session did
        not run on. It is also what makes a routing refusal a *skip* rather than
        a raise. A **Lane** may raise on one, because releasing its reservation
        leaves the candidate for another Lane to pick up; a serial Iteration has
        no other Lane, so raising would end a whole Run over a single
        mistyped ``task-type:`` label while other work sat eligible behind it.
        §7 settles it: selection is unattended and never blocks, so a candidate
        it cannot take, it passes over.

        The resolved pairs land in :attr:`_routes` rather than in the return
        value because :func:`~git_loopy.serial_pickup.pick_serial` is pinned
        pure — it decides *which* issue, and what that issue costs to run is the
        loop's business.

        **What the pair is allowed to be is ADR-0037's decision, and it is now
        the pair itself.** The resolution happens unconditionally, because that
        is what validates the candidate's ``task-type:`` labels and what turns a
        bad one into a skip; and what it resolves to is what the session below is
        constructed with, in serial exactly as in Parallel.
        :func:`~git_loopy.routing_scope.routing_in_force` is still where that is
        decided, and it now answers ``True`` in both — see that module for the
        reversal and why the reason it replaced had already been falsified.

        **Classification sits between the walk and the record** (#409). The
        walk admits from the labels the tracker has; :meth:`_classify_at_pickup`
        may then give the *bound* candidate the ``task-type:`` label it was
        missing, which re-resolves its pair. The Pickup Event is emitted after
        that, so the pair it publishes is the pair the session below is built
        with — a record written before the classification would name a model
        that never ran.
        """
        self._routes = {}

        def admit(item: AfkReadyItem) -> str | None:
            defeated = self._attempts.defeated_by(item.ref)
            if defeated is not None:
                # The **Attempt lifecycle** filter (#412), asked before routing
                # so a defeated candidate costs nothing to pass over. It sits in
                # `admit` rather than narrowing the Pool handed to `pick_serial`:
                # a candidate the runner *declines* is a **Pickup skip** with a
                # record, and one that silently vanished from consideration
                # would be exactly the indefinite passing-over ADR-0032 exists
                # to make visible.
                return f"already attempted this Run ({defeated.value})"
            try:
                resolution = self._resolve_route(
                    item,
                    warn=lambda message, _ref=item.ref: self._diag.warning(
                        "issue #%s routing: %s", _ref, message
                    ),
                )
            except TaskTypeError as exc:
                return f"routing refused: {exc}"
            self._routes[item.ref] = resolution
            return None

        pickup = pick_serial(pool, admit=admit, pin=self._config.issue_pin)
        considered = len(pickup.considered)
        for skip in pickup.skipped:
            self._emit_pickup_skipped(
                iter_num=iter_num,
                issue=skip.ref,
                reason=skip.reason,
                position=skip.position,
                considered=considered,
            )
            self._diag.warning(
                "serial Pickup skipped #%s at position %d of %d: %s",
                skip.ref,
                skip.position,
                considered,
                skip.reason,
            )
        if pickup.item is not None:
            assert pickup.position is not None and pickup.reason is not None
            bound, resolution = await self._classify_at_pickup(
                pickup.item, routed=self._routes[pickup.item.ref]
            )
            self._routes[bound.ref] = resolution
            pickup = dataclass_replace(pickup, item=bound)
            self._emit_pickup_bound(
                iter_num=iter_num,
                issue=bound.ref,
                reason=pickup.reason,
                position=pickup.position,
                considered=considered,
                resolution=resolution,
            )
        return pickup

    def _finish_unworked_iteration(self, iter_num: int) -> tuple[str, int, int]:
        """End the Run on an Iteration whose **Pickup** bound nothing (#413).

        Reached only when a non-empty Pool's every candidate was skipped. It is
        deliberately not the ``empty_pool`` outcome: that one exits the Run 0
        because there is no work, and reporting "I could not take any of it" the
        same way would end a Run cleanly over a repairable tracker state. So it
        has its own **Run outcome** and its own non-zero exit reason,
        ``all_skipped``.

        Terminating here rather than recording a **Strike** and carrying on is
        what stops the livelock #413 opened. Once the ceiling counts *skipped
        issues* instead of unproductive Iterations, an Iteration that binds
        nothing charges nothing — every candidate that could charge was charged
        at the ending that defeated it — so a Run whose Pool is entirely
        defeated would re-collect the same Pool, skip the same candidates and
        spin until the iteration cap or the operator stopped it. Nothing about
        the next Iteration could differ: the lifecycle is monotonic and the Pool
        is re-read from a tracker no session is touching.
        """
        self._finish_iteration(iter_num, outcome="all_skipped")
        return ("all_skipped", 0, 0)

    def _infer_active_binding(
        self,
        pool: list[AfkReadyItem],
        completions: list[Any],
        new_commits: list[git_module.Commit],
    ) -> tuple[int | str, str] | None:
        """Select the strongest post-session Active-issue fallback.

        A *degraded* path since #394, and only that. A serial **Pickup** binds
        before the session starts, so on the normal path
        :attr:`ActiveIssueBinding.active_ref` is already set by the time this
        could be reached and it is never called. What is left for it is the case
        Pickup itself could not bind — a Pool the runner never got to, or a
        binding that did not publish — where a closure or a close-keyword is
        still better attribution than none.

        Its ``single_member_pool`` branch is unreachable on the normal path for
        the same reason, and is kept rather than deleted because it is the only
        thing that would still attribute a **Checkpoint** correctly if Pickup
        ever stopped binding. It is deliberately *not* promoted into Pickup:
        inferring the Active issue from the Pool having one member is a guess
        that happens to be right, and Pickup does not have to guess.
        """
        if completions:
            return completions[0].ref, "closure"
        pool_ints = {item.ref for item in pool if isinstance(item.ref, int)}
        if pool_ints:
            joined = "\n".join(c.message for c in new_commits)
            refs = filter_to_pool(extract_close_refs(joined), pool_ints)
            if refs:
                return refs[0], "commit"
        if len(pool) == 1:
            return pool[0].ref, "single_member_pool"
        return None

    def _maybe_push(
        self,
        iter_num: int,
        new_commits: list[git_module.Commit],
        checkpoint_sha: str | None,
    ) -> bool:
        """Push the current branch to its upstream after an iteration's new commits.

        The remote half of ADR-0004's durability net. Whenever this iteration
        produced new commits — agent commits (``new_commits``) and/or the runner
        Checkpoint just authored (``checkpoint_sha``) — :func:`git.push` sends
        the current branch to its configured upstream so the work reaches the
        remote instead of accumulating locally. An iteration that produced
        neither (a clean tree with no agent commit, or a pure PR advance the
        agent pushed itself) skips the push entirely.

        Non-fatal by construction: a missing upstream, a missing/unreachable
        remote, an auth failure, or a non-fast-forward rejection raises
        :exc:`git.GitError`, which is caught and warned — a local-only repo
        completes normally. A successful push emits ``wrapper.push.recorded``;
        a failure emits nothing (it only warns), mirroring the failed-Checkpoint
        path so the JSONL records pushes that actually landed.

        Returns:
            ``True`` if a push was attempted and succeeded; ``False`` if there
            was nothing to push or the push failed non-fatally.
        """
        if not new_commits and checkpoint_sha is None:
            return False
        try:
            self._git.push()
        except git_module.GitError as exc:
            self._diag.warning(
                "auto-push failed: %s; continuing (work stays local)", exc
            )
            return False
        self._emit(events_module.WRAPPER_PUSH_RECORDED, iter_num=iter_num)
        self._diag.info("auto-pushed current branch after new commits")
        return True

    def _handle_completions_safely(
        self,
        pool: list[AfkReadyItem],
        new_commits: list[git_module.Commit],
    ) -> list[Any]:
        """Call ``source.handle_completions`` with crash containment.

        A source-level crash inside ``handle_completions`` must not
        abort the iteration — the commit accounting and strike
        bookkeeping still need to run. Returns an empty list on
        failure (logged at WARNING via the diagnostics logger).
        """
        try:
            return list(
                self._source.handle_completions(
                    pool=pool, new_commits=new_commits
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._diag.warning(
                "source.handle_completions raised %s: %s; "
                "continuing iteration with zero completions",
                type(exc).__name__, exc,
            )
            return []

    def _record_session_outcome(
        self,
        ref: int | str,
        record: session_outcome_module.SessionOutcomeRecord,
        *,
        iter_num: int | None = None,
    ) -> None:
        """Keep this Iteration's **Session outcome**, and say it once (#403).

        Kept as the record rather than as the sentence it prints: the attempt
        lifecycle is keyed off the ending, and a lifecycle that had to parse a
        log line back into a decision would be reading the Run's diagnostics as
        an API. :meth:`_observe_session_ending` is the first reader that keys off
        it for real.
        """
        self._last_session_outcome = record
        self._observe_session_ending(ref, record, iter_num=iter_num)
        _report_session_outcome(self._diag, ref=ref, record=record)

    def _observe_session_ending(
        self,
        ref: int | str,
        record: session_outcome_module.SessionOutcomeRecord,
        *,
        iter_num: int | None = None,
    ) -> None:
        """Offer one ending to the two ledgers that read one (#408, #412, #413).

        Called from a serial **Iteration** and from a **Lane** alike, because
        both ledgers are per issue rather than per mode: neither the pair a
        stalled issue is next picked up on nor whether it is picked up at all
        may depend on which mode stalled it.

        The ending is offered whole to each, and neither decision is duplicated
        here: escalation triggers on silent no-progress alone, the **Attempt
        lifecycle** disposes of all five endings, and a condition restated at
        this call site could disagree with the ones that matter.

        **This is also where the Run's one Strike is charged** (#413). The
        ceiling counts the issues a Run has given up on, and the moment an issue
        is given up on is the moment its lifecycle reaches **skipped** — so the
        transition is the charge, and "exactly one Strike per skipped issue"
        falls out of the lifecycle's own monotonicity rather than out of anyone
        counting carefully. Charging at an Iteration boundary instead would have
        had to answer *which* boundary in **Parallel mode**, where a Lane's
        ending and the accounting scope that finalizes it are different moments.
        """
        self._escalation.observe(ref, record.outcome)
        before = self._attempts.state(ref)
        after = self._attempts.observe(ref, record.outcome)
        if after is AttemptState.SKIPPED and before is not AttemptState.SKIPPED:
            self._charge_skip_strike(ref, iter_num=iter_num)

    def _charge_skip_strike(
        self, ref: int | str, *, iter_num: int | None = None
    ) -> None:
        """Spend one **Strike** on the issue this Run has just given up on.

        The Event goes out from here rather than from the Iteration boundary
        because a ``wrapper.strike`` announces that a Strike was *recorded*: an
        unproductive Iteration that defeated nobody now records none, and a
        record emitted anyway would show an operator a warning with an unchanged
        count beside it.
        """
        outcome = self._strike_machine.tick(
            commits_in_iter=0,
            auto_closures_in_iter=0,
            issues_skipped_in_iter=1,
        )
        self._diag.warning(
            "issue #%s is out of attempts this Run; strike %d of %d",
            ref,
            self._strike_machine.strikes,
            self._config.max_nmt_strikes,
        )
        self._emit(
            events_module.WRAPPER_STRIKE,
            iter_num=iter_num,
            strikes=self._strike_machine.strikes,
            max_strikes=self._config.max_nmt_strikes,
            outcome=("abort" if outcome == "aborted" else "warn"),
        )

    def _finish_iteration(
        self,
        iter_num: int,
        *,
        outcome: str | None = None,
        advanced_issues: Iterable[int | str] = (),
    ) -> dict[str, Any]:
        """Emit and persist one Orchestrator-owned normalized Iteration rollup."""
        payload = self._rollup.finish(
            iter_num=iter_num,
            strikes=self._strike_machine.strikes,
            outcome=outcome,
            advanced_issues=advanced_issues,
        )
        event = self._emit(
            events_module.WRAPPER_ITERATION_END,
            iter_num=iter_num,
            **payload,
        )
        try:
            self._writers.run_summary.record(
                IterationCounters.from_rollup(
                    iter_num=iter_num,
                    payload=payload,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._diag.warning(
                "RunSummaryWriter.record failed for iter %d: %s",
                iter_num,
                exc,
            )
        return event

    # -- public driver -----------------------------------------------------

    async def drive(self) -> int:
        """Drive the iteration loop to its terminal outcome."""
        self._emit_skill_policy_resolved()
        # Preflight via the source — GitHub validates gh + repo; PRDs
        # is a no-op (returns None) so an empty / missing prds/ dir is
        # not a preflight failure, just an empty pool.
        rc = self._source.preflight()
        if rc is not None:
            return rc

        # Capture the base branch once, before any iteration can run
        # `gh pr checkout`, so PR iterations can return to it (see the
        # branch-hygiene step in `_run_one_iteration`). Only when PRs are in
        # scope; a detached HEAD or git failure leaves it None, which simply
        # disables the defensive restore.
        if self._include_prs:
            try:
                self._base_branch = self._git.current_branch()
            except git_module.GitError as exc:
                self._diag.warning(
                    "could not determine base branch for PR restore: %s", exc
                )
                self._base_branch = None

        self._emit(
            events_module.WRAPPER_RUN_START,
            iter_num=None,
            issue_source=self._config.issue_source,
            release_version=self._release_version,
            schema_version=events_module.EVENT_SCHEMA_VERSION,
            insight_capabilities=events_module.python_insight_capabilities(
                rate_card=self._rate_card is not None
            ),
            rate_card=(
                None if self._rate_card is None else self._rate_card.to_payload()
            ),
            parallel_capabilities=dict(events_module.PYTHON_PARALLEL_CAPABILITIES),
            max_iterations=self._config.max_iterations,
            max_nmt_strikes=self._config.max_nmt_strikes,
            # #410: what this Run parsed, gate-checked.
            **run_start_payload(self._config),
        )

        exit_code = exit_code_for("iteration_cap")
        # Not `iteration_cap`: the only ways out of the loop below that skip
        # every assignment to this name are `KeyboardInterrupt` and
        # `CancelledError`, which are `BaseException` and so pass the
        # `except Exception` handler untouched. Whatever this is initialised to
        # is therefore exactly what an interrupted Run reports (#398).
        outcome_label = RUN_OUTCOME_INTERRUPTED
        iter_num = 0
        try:
            try:
                while True:
                    iter_num += 1
                    if (
                        self._config.max_iterations != 0
                        and iter_num > self._config.max_iterations
                    ):
                        outcome_label = "iteration_cap"
                        break

                    outcome, _commits, _closures = await self._run_one_iteration(
                        iter_num
                    )
                    if outcome == "empty_pool":
                        outcome_label = "empty_pool"
                        exit_code = exit_code_for("empty_pool")
                        break
                    if outcome == "all_skipped":
                        # #413: there *was* work and none of it could be taken.
                        # Distinct from `empty_pool` (which exits 0) because a
                        # Run that gave up is not a Run that finished, and
                        # distinct from `stuck` because the ceiling was never
                        # reached — a single defeated issue ends a Run whose
                        # Pool held only that one.
                        outcome_label = "all_skipped"
                        exit_code = exit_code_for("all_skipped")
                        break
                    if outcome == "aborted":
                        outcome_label = "stuck"
                        exit_code = exit_code_for("stuck")
                        break
            except Exception as exc:
                # An unhandled crash inside an iteration MUST surface in
                # the wrapper.run.end envelope so a replay-side reader
                # doesn't mistake the run for a clean cap-out. Re-raise
                # so the outer ``run()`` can log + return 1.
                outcome_label = "crashed"
                exit_code = 1
                self._diag.error(
                    "git-loopy iteration %d crashed: %s: %s",
                    iter_num, type(exc).__name__, exc,
                )
                raise
        finally:
            # Final wrapper.run.end always emits — even on early break or crash.
            try:
                self._emit(
                    events_module.WRAPPER_RUN_END,
                    iter_num=None,
                    outcome=outcome_label,
                    iterations_run=(
                        max(iter_num - 1, 0)
                        if outcome_label in _RUN_OUTCOMES_MID_ITERATION
                        else iter_num
                    ),
                )
            except Exception as exc:  # pragma: no cover - defensive
                self._diag.warning("wrapper.run.end emit failed: %s", exc)
        return exit_code


@dataclass
class _LaneWork:
    """Mutable per-Lane-contribution state: worktree, branch, and pre-SHA.

    Keyed by :attr:`~git_loopy.rolling_scheduler.Contribution.contribution_id`
    (never ``lane_id``) in ``_ParallelLoop._lane_work`` — #219 §7 / ADR-0020
    make a Lane contribution outlive the reusable Lane slot it started in:
    admission to **Integration** frees that slot for another issue while this
    state, and the contribution's own Integration handling, continue. The
    resolved ``(model, reasoning_effort)`` pair lives on the
    :class:`~git_loopy.rolling_scheduler.Contribution` itself, not here, since
    the scheduler is the authority that minted it at
    :meth:`~git_loopy.rolling_scheduler.RollingScheduler.start_session`.
    """

    item: AfkReadyItem
    branch: str
    path: Path
    git: git_module.GitClient
    pre_sha: str | None = None


@dataclass
class _ContributionAccounting:
    """One open **Lane contribution**'s accounting scope (#310).

    Keyed by ``contribution_id`` — never ``lane_id`` — for the same reason
    :class:`_LaneWork` is: a contribution outlives the reusable **Lane** slot
    it started in, so a slot-keyed row would be handed to whichever issue
    refilled that slot. Held here rather than on :class:`_LaneWork` because
    Lane state is released the moment the contribution is admitted to
    **Integration**, while its accounting stays open until the contribution
    finalizes and its Summary row is cut.

    Attributes:
        iter_num: The scope number the Lane's agent session and the durable
            Run summary row share.
        started_monotonic: When the contribution opened — the base for
            ``lifecycle_seconds``.
        agent_seconds: Time spent in the Lane's own agent session, which is a
            different quantity from the whole lifecycle: a contribution can
            wait behind the single Integrator long after its agent is done.
        recovery_attempts: Bounded auto-resolution **agent sessions** this
            contribution consumed during Integration.
    """

    iter_num: int
    started_monotonic: float
    agent_seconds: float = 0.0
    recovery_attempts: int = 0


@dataclass
class _IntegrationStage:
    """The **private** worktree one Lane contribution is integrated in.

    #219 §4.7-4.8 / ADR-0020: a candidate is "prepared and fully gated in
    private Integration state based on the latest published green base", and
    base advances "only after the candidate passes the full relevant feedback
    loop". That is what supersedes ADR-0009's publish-then-revert: base is never
    made to carry an unverified merge, so a Lane reserved mid-Integration
    branches from either the prior green base or the newly published green base
    and never from something in between.

    The same worktree carries the whole recovery arc — the initial merge, the
    gate, and every bounded auto-resolution attempt — so a resolution agent sees
    the exact tree that failed rather than a fresh one.

    Attributes:
        branch: The throwaway Integration branch, reaped on every path.
        path: Where that branch is checked out.
        git: A :class:`~git_loopy.git.GitClient` bound to :attr:`path`.
    """

    branch: str
    path: Path
    git: git_module.GitClient


_ROLLING_EMPTY_POLL_INTERVAL = 1.0
"""Seconds between idle ``confirm_empty`` retries under Rolling dispatch.

:meth:`~git_loopy.rolling_pool.RollingPool.confirm_empty` always forces an
immediate membership refresh, bypassing its own demand-gated backoff window
(#219 §2.14) — so a driver loop that called it in a tight spin would hammer
the source. This is the guard between retries while genuinely idle (no Lane
work in flight, nothing currently refillable, no serial turn granted).
"""


class _ParallelLoop:
    """Rolling-dispatch Parallel-mode orchestrator (#219, ADR-0020).

    Retires the **Wave** barrier (#61, ADR-0008): rather than grouping Lanes
    into a cohort, waiting for the slowest, and integrating the batch, a Run
    now owns reusable **Lane** slots that the
    :class:`~git_loopy.rolling_scheduler.RollingScheduler` reserves, fills,
    and releases continuously (:meth:`_drive_rolling`). A single eligible
    ``parallel-safe`` issue starts in a Lane immediately — there is no
    "wait for a second issue" threshold — and a finished Lane's slot refills
    the moment its **Lane contribution** is admitted or terminates, without
    waiting on any other Lane. One Lane's worktree setup may freely overlap
    another Lane's agent session, and both may overlap **Integration**.

    Eligibility is still a **human assertion**, never inferred: only pool
    items carrying ``parallel-safe`` (alongside ``ready-for-agent``) may ever
    become a Lane contribution — enforced by
    :func:`~git_loopy.rolling_pool.is_parallel_safe`, the
    :class:`~git_loopy.rolling_pool.RollingPool` cache's default eligibility
    predicate. Candidate discovery reads that cheap, continuously-refreshed
    Pool membership cache and pays the authoritative per-issue read only at
    pickup, immediately before reservation
    (:meth:`~git_loopy.sources.RollingIssueSource.pickup`) — both already
    handled internally by :meth:`~git_loopy.rolling_scheduler.RollingScheduler.reserve`.
    An issue whose agent session has started may never take a second Lane in
    this Run (the scheduler's own ``_worked`` guard, #219 §1.7).

    **Integration (#62, #63, #307, ADR-0020)** merges the Lane branch into a
    private **Integration stage** worktree, re-gates *that stage* from the
    runner side via the injected :class:`~git_loopy.gate.GateRunner`, and
    publishes the verified stage to base and closes on green (the same
    runner-driven closure as serial mode); a red or conflicting stage never
    reaches base and is handed to bounded auto-resolution in the same stage
    (:meth:`_auto_resolve_lane`) before falling back to a serial Iteration. It
    is serialized against every other contribution's Integration via
    ``self._integration_lock`` so the shared main worktree never sees two
    concurrent publishes, but it runs *inline*
    inside whichever Lane's lifecycle task got admitted
    (:meth:`_integrate_contribution`) rather than behind a barrier, so other
    Lanes' setup and sessions are never blocked on it. Because a contribution
    outlives its Lane slot, all of this is threaded by
    :attr:`~git_loopy.rolling_scheduler.Contribution.contribution_id`, never
    by ``lane_id`` (#219 §7).

    A source that cannot support Rolling dispatch (only
    :class:`~git_loopy.sources.GitHubIssueSource` implements
    :class:`~git_loopy.sources.RollingIssueSource` today — the PRDs backend
    has no ``parallel-safe`` label concept) degrades Parallel mode entirely to
    the serial path (:meth:`_drive_serial_only`), so opting into Parallel mode
    never strands eligible work.

    Composes a serial :class:`_Loop` (``self._serial``) both for serial
    Iterations (serial-required ``ready-for-agent`` work the scheduler's
    serial-latch / quiescence protocol grants exclusive ownership of base,
    :meth:`_service_serial_required_work`) and to share ONE Strike
    machine, event emitter, summary, and Checkpoint policy — so a Lane
    contribution finalizing and a serial Iteration tick the same Strike
    machine and write one consistent event / counter stream. The serial path
    is unaffected: :func:`run` only builds a ``_ParallelLoop`` when
    ``config.parallel > 1``.
    """

    def __init__(
        self,
        *,
        config: RunConfig,
        release_version: str,
        git: git_module.GitClient,
        prompt_text: str,
        denomination: CostDenomination,
        writers: WritersBundle,
        sinks: SinkFanout,
        summary: RunSummary,
        client: CopilotClient,
        skill_preflight: RunSkillPreflight,
        source: IssueSource,
        diag: logging.Logger,
        gate_runner: gate_module.GateRunner,
        worktree_setup: worktree_module.WorktreeSetup,
        include_prs: bool = False,
        rate_card: RateCard | None = None,
        classifier_pair: ClassifierPair | None = None,
        task_type_client: TaskTypeLabelClient | None = None,
        execution_host: execution_host_module.ExecutionHost | None = None,
    ) -> None:
        self._config = config
        self._release_version = release_version
        # Held fixed for the whole Run, exactly as the serial path holds it, so
        # a Parallel Summary is denominated by one card too (#331).
        self._rate_card = rate_card
        self._git = git
        self._prompt_text = prompt_text
        self._writers = writers
        self._sinks = sinks
        self._summary = summary
        self._client = client
        self._skill_exposure = skill_preflight.exposure
        self._source = source
        self._diag = diag
        # Injected runner-side Integration gate (#60, ADR-0009): re-runs the
        # feedback loops from the runner side as the load-bearing gate when
        # landing a Lane branch on base (:meth:`_integrate_lane`); the
        # conflict / red-gate recovery path is :meth:`_auto_resolve_lane`.
        self._gate_runner = gate_runner
        # Injected per-Lane worktree setup (#65, ADR-0008): after a Lane's
        # worktree is created and before its agent session starts, prepare its
        # environment (the configured ``GIT_LOOPY_WORKTREE_SETUP`` command, or a
        # best-effort auto-detected install) so the feedback loops can run there.
        self._worktree_setup = worktree_setup
        self._run_id = writers.run_id
        self._repo_root = git.root
        # The Execution host seam (#447, spec #445 §A/§C): every Lane
        # contribution's agent session now travels through a declared host
        # rather than the loop invoking `IterationSession` directly. Defaults
        # to `LocalExecutionHost`, putting today's local behaviour behind the
        # seam unchanged; `_run_local_contribution` supplies its mechanics.
        # A test injects a fake host here instead — no network involved.
        self._execution_host: execution_host_module.ExecutionHost = (
            execution_host
            if execution_host is not None
            else execution_host_module.LocalExecutionHost(
                runner=self._run_local_contribution
            )
        )
        # Per-in-flight-contribution context (`contribution`, `_LaneWork`)
        # keyed by `id(request)`, so `_run_local_contribution` can reach the
        # loop state its mechanics need without the seam's own
        # `ContributionRequest` carrying anything beyond what spec #445 §A
        # names (issue reference, prompt, base revision, model/reasoning
        # pair, Effective Skill policy, run_id) — never a working directory
        # or a callback. Populated immediately before, and popped
        # immediately after, each `self._execution_host.run_contribution`
        # call.
        self._contribution_context: dict[
            int, tuple[rolling_scheduler.Contribution, _LaneWork]
        ] = {}
        # Companion to `_contribution_context`: the session-outcome half of
        # `_run_local_contribution`'s work (`_LaneSessionSignals`, `changed`,
        # `checkpoint_ok`) that `_run_lane_lifecycle` still needs once the
        # host returns, but that is deliberately absent from the seam's own
        # `ContributionOutcome` — a host's outcome names a branch and
        # Events, never the Run's own Strike/Escalation bookkeeping.
        self._lane_outcome_signals: dict[
            int, tuple[_LaneSessionSignals, bool, bool]
        ] = {}

        # Rolling dispatch (#219, ADR-0020) needs the two extra Pool
        # operations `RollingIssueSource` defines. Only the GitHub backend
        # implements them today; a source that does not (the PRDs backend has
        # no `parallel-safe` label concept) can never offer Lane work, so
        # Parallel mode degrades entirely to the serial path (`drive`).
        self._pool: RollingPool | None = None
        self._scheduler: rolling_scheduler.RollingScheduler | None = None
        # Bounded adaptive Lane concurrency (#219 §6, #309). The **Lane cap**
        # is a safety ceiling, not a utilization promise: under sustained
        # **Integration** backpressure, 429s, AI-credit or host/setup pressure,
        # running at the cap wastes capacity and money. The monitor owns the
        # observation cadence and the operator's budgets; the policy it builds
        # is handed to the scheduler, which supplies the pipeline half of every
        # observation from its own state.
        self._cost_meter = rolling_pressure.RunCostMeter(denomination=denomination)
        self._pressure = _make_pressure_monitor(
            lane_cap=config.parallel,
            diag=diag,
            credit_spent=self._cost_meter,
            rate_limits=rolling_pressure.rate_limit_reader(source),
        )
        if isinstance(source, RollingIssueSource):
            self._pool = RollingPool(
                diag=diag,
                source=source,
                clock=time.monotonic,
                eligible=self._lane_candidate_eligible,
            )
            self._scheduler = rolling_scheduler.RollingScheduler(
                diag=diag,
                pool=self._pool,
                lane_cap=config.parallel,
                max_iterations=config.max_iterations,
                concurrency=self._pressure.controller,
            )
        self._rolling_capable = self._scheduler is not None
        # Per-Lane-contribution state, keyed by `contribution_id` (never
        # `lane_id`) so it survives the reusable Lane slot moving on to
        # another issue once this contribution is admitted (#219 §7).
        self._lane_work: dict[str, _LaneWork] = {}
        # The accounting-scope number each open **Lane contribution** was
        # opened with (#310), keyed the same way and for the same reason: a
        # contribution's Consumption, timing, and Summary row belong to the
        # issue that produced them, not to whichever issue its Lane slot went
        # on to work. Popped when the contribution's row is cut.
        self._contribution_iter: dict[str, _ContributionAccounting] = {}
        # Serializes Integration (merge / gate / land / auto-resolve) across
        # concurrently-admitted contributions, so the shared main worktree
        # never sees two merges at once — without blocking any OTHER Lane's
        # worktree setup or agent session (criteria #2/#3, ADR-0020).
        self._integration_lock = asyncio.Lock()
        # Every in-flight Lane lifecycle task (`_run_lane_lifecycle`), tracked
        # so the driver can `asyncio.wait(..., FIRST_COMPLETED)` on the first
        # one to finish and immediately reserve into the capacity it freed —
        # the continuously-refilling replacement for the Wave's
        # `asyncio.gather`-then-proceed barrier.
        self._pending: set[asyncio.Task[None]] = set()
        # Raised whenever a Lane lifecycle task frees Rolling-dispatch capacity
        # **without returning** — admission to Integration releases the
        # contribution's Lane slot (§3.9) and a `finalize` frees an
        # **Integration backlog** slot (§4.4), both from inside a task that then
        # carries on through the rest of its Integration cascade. Waiting only
        # on task completion would therefore hold a freed Lane idle for the
        # whole cascade, which is up to K bounded auto-resolution *agent
        # sessions* long — exactly the "auto-resolution capacity does not
        # consume the **Lane cap**" rule (#219 §4.11) inverted. The driver
        # clears it immediately before each `reserve()`, so a signal raised
        # after that decision always earns another one.
        self._capacity_freed = asyncio.Event()
        # Run-wide monotonic sequence for `IterationSession.iter_num` tagging
        # (raw JSONL / OTel tagging only — Lane contribution accounting is
        # keyed by `contribution_id`, per #219 §7, never by this number).
        # Rolling dispatch has no "round", so there is no natural iteration
        # number to reuse; this keeps every session's tag unique and
        # increasing, mirroring the retired Wave's per-round counter.
        self._iter_seq = 0
        # The first unhandled exception surfaced by a Lane lifecycle task.
        # A bare `asyncio.Task` swallows an exception silently (it only logs
        # "exception was never retrieved") — `_guarded_lane_lifecycle` stashes
        # it here so `_drive_rolling` can re-raise it on its own task and
        # preserve the "crashed" outcome / exit-code contract the retired
        # Wave's single `try/except` around the whole round loop gave.
        self._crash: BaseException | None = None

        # Compose a serial `_Loop` for serial Iterations AND to share its
        # Strike machine / event emitter / summary counters / Checkpoint
        # policy, so a Lane contribution finalizing and a serial Iteration
        # tick ONE Strike machine and write ONE consistent event + counter
        # stream.
        self._serial = _Loop(
            config=config,
            release_version=release_version,
            git=git,
            prompt_text=prompt_text,
            denomination=denomination,
            writers=writers,
            sinks=sinks,
            summary=summary,
            client=client,
            skill_preflight=skill_preflight,
            source=source,
            diag=diag,
            include_prs=include_prs,
            usage_observer=self._cost_meter,
            classifier_pair=classifier_pair,
            task_type_client=task_type_client,
        )

    def _lane_candidate_eligible(self, candidate: PoolCandidate) -> bool:
        """Is this candidate **Lane** work *and* still owed an attempt (#412)?

        The **Lane** half of the **Attempt lifecycle** skip. A Lane Pickup is a
        Pickup, so a defeated issue must not reach one — but the Lane path
        cannot express the refusal the serial ``admit`` closure does. Passing a
        reserved candidate over releases its reservation and leaves it eligible,
        so a defeated issue would be reserved, skipped and released once per
        turn for the rest of the Run. Refusing it *candidacy* says the same
        thing once, and the serial Pickup that defeated it has already left the
        record.

        Composed here rather than written into the scheduler's own collision
        guard (:attr:`~git_loopy.rolling_scheduler.RollingScheduler._worked`),
        which ADR-0040 keeps untouched: that guard answers "is one issue about
        to take two Lanes at once", a worktree question with its own lifetime,
        and a lifecycle answer smuggled into it would be indistinguishable from
        a collision afterwards. The scheduler composes its guard onto whatever
        predicate it is handed, so both hold.

        The guard alone would look sufficient — it latches at session start, so
        every Lane-defeated issue is already behind it. The order it does not
        cover is the other one: a **Parallel-safe** issue defeated by a *serial*
        Iteration of a Parallel Run (a serial fallback taken while Lane
        concurrency is throttled to nothing works whatever sits at the Pool's
        head) was never in the guard, and nothing else would stop a Lane
        reserving it once concurrency recovered.
        """
        return is_parallel_safe(candidate) and not self._serial._attempts.skipped(
            candidate.ref
        )

    def _alloc_iter_num(self) -> int:
        """Allocate the next Run-wide sequence number for session tagging.

        See :attr:`_iter_seq` — shared by Lane work sessions and
        auto-resolution sessions so the whole Run reads as one increasing
        timeline, same as the retired Wave's per-round counter.
        """
        self._iter_seq += 1
        return self._iter_seq

    async def drive(self) -> int:
        """Drive Rolling dispatch (#219, ADR-0020) to its terminal outcome.

        Mirrors :meth:`_Loop.drive` — same preflight, ``wrapper.run.start`` /
        ``wrapper.run.end`` envelope, and exit-code contract — but delegates
        the round loop itself to :meth:`_drive_rolling` when the source
        supports it, else :meth:`_drive_serial_only`. Both own their own
        ``max_iterations`` cap-check and outcome mapping; this method owns
        only the single ``wrapper.run.start``/``.end`` pair (calling
        ``self._serial.drive()`` instead would double-emit both, since it
        carries the identical pair).
        """
        self._serial._emit_skill_policy_resolved()
        rc = self._source.preflight()
        if rc is not None:
            return rc

        self._serial._emit(
            events_module.WRAPPER_RUN_START,
            iter_num=None,
            issue_source=self._config.issue_source,
            release_version=self._release_version,
            schema_version=events_module.EVENT_SCHEMA_VERSION,
            insight_capabilities=events_module.python_insight_capabilities(
                rate_card=self._rate_card is not None
            ),
            rate_card=(
                None if self._rate_card is None else self._rate_card.to_payload()
            ),
            parallel_capabilities=dict(events_module.PYTHON_PARALLEL_CAPABILITIES),
            max_iterations=self._config.max_iterations,
            max_nmt_strikes=self._config.max_nmt_strikes,
            # #410: what this Run parsed, gate-checked.
            **run_start_payload(self._config),
            # #304: only a Parallel-mode Run carries these, so a serial Run's
            # `wrapper.run.start` is byte-identical to what it always was.
            parallel_mode=True,
            lane_cap=self._config.parallel,
            effective_lane_limit=(
                self._scheduler.effective_limit
                if self._scheduler is not None
                else None
            ),
        )
        self._report_parallel_degraded()

        # Same reasoning as `_Loop.drive` (#398): an interrupt bypasses the
        # `except Exception` below and reports whatever this name holds, so it
        # must not hold a claim the Run never earned. `iterations_run` stays 0
        # because the completed count lives inside the `_drive_*` return this
        # path never reaches — 0 understates it, but it is not a Run outcome
        # the operator can act on wrongly.
        outcome_label = RUN_OUTCOME_INTERRUPTED
        exit_code = exit_code_for("iteration_cap")
        iterations_run = 0
        try:
            try:
                if self._rolling_capable:
                    outcome_label, exit_code, iterations_run = (
                        await self._drive_rolling()
                    )
                else:
                    outcome_label, exit_code, iterations_run = (
                        await self._drive_serial_only()
                    )
            except Exception as exc:
                outcome_label = "crashed"
                exit_code = 1
                self._diag.error(
                    "git-loopy parallel run crashed: %s: %s",
                    type(exc).__name__, exc,
                )
                raise
        finally:
            try:
                self._serial._emit(
                    events_module.WRAPPER_RUN_END,
                    iter_num=None,
                    outcome=outcome_label,
                    iterations_run=iterations_run,
                )
            except Exception as exc:  # pragma: no cover - defensive
                self._diag.warning("wrapper.run.end emit failed: %s", exc)
        return exit_code

    def _report_parallel_degraded(self) -> None:
        """Say that Parallel mode degraded entirely to the serial path (#414).

        The banner emitted immediately above this is three truthful claims
        about the **distribution** — ``parallel_mode``, the **Lane cap**, and
        the ``parallel_capabilities`` manifest — and under a source that cannot
        supply Lane work all three stand unqualified over a Run that is
        byte-identical to a serial one. That is the collapse
        ``parallel_capabilities`` exists to prevent (§12), reached through a
        **source** fact rather than a distribution one: the manifest is
        *correct*, so refusing the cap would be wrong (#348 §5 — the fallback
        reaches the same outcome and strands nothing). Only the report was
        missing.

        Emitted here rather than inside :meth:`_drive_serial_only` because the
        degrade is one fact about the Run, settled before any round: reporting
        it where the rounds are would repeat it, and repetition would read as
        something the Run kept re-deciding. Adjacent to the banner it qualifies
        for the same reason :meth:`_report_serial_fallback` sits immediately
        before the Iteration it explains — a claim and its correction separated
        by a Run's worth of output is not a correction.

        Silent on a Rolling-capable source whose **Pool** carries no
        ``parallel-safe`` issue: that is #304's **Serial fallback**, already
        reported per serial **Iteration** with a counted reason, and the
        operator's move there is triage. No move fixes this one.
        """
        if self._rolling_capable:
            return
        self._serial._emit(
            events_module.WRAPPER_PARALLEL_DEGRADED,
            iter_num=None,
            reason=events_module.PARALLEL_DEGRADE_SOURCE_NOT_ROLLING,
            lane_cap=self._config.parallel,
            issue_source=self._config.issue_source,
        )

    async def _drive_serial_only(self) -> tuple[str, int, int]:
        """Drive every round as a serial Iteration (source not Rolling-capable).

        A source that does not implement
        :class:`~git_loopy.sources.RollingIssueSource` (the PRDs backend) has
        no ``parallel-safe`` label concept and can never offer Lane work, so
        Parallel mode degrades entirely to the proven serial path — every
        round is a plain ``_Loop`` Iteration. Mirrors :meth:`_Loop.drive`'s
        own round loop (cap check, outcome mapping) exactly, but calls
        ``self._serial._run_one_iteration`` directly rather than
        ``self._serial.drive()`` so :meth:`drive` above owns the single
        ``wrapper.run.start``/``.end`` pair.
        """
        iter_num = 0
        while True:
            iter_num += 1
            if (
                self._config.max_iterations != 0
                and iter_num > self._config.max_iterations
            ):
                return "iteration_cap", exit_code_for("iteration_cap"), iter_num - 1
            outcome, _commits, _closures = await self._serial._run_one_iteration(
                iter_num
            )
            if outcome == "empty_pool":
                return "empty_pool", exit_code_for("empty_pool"), iter_num
            if outcome == "all_skipped":
                return "all_skipped", exit_code_for("all_skipped"), iter_num
            if outcome == "aborted":
                return "stuck", exit_code_for("stuck"), iter_num

    async def _drive_rolling(self) -> tuple[str, int, int]:
        """Drive Rolling dispatch to its terminal outcome (#219, ADR-0020).

        Each turn: reserve every currently refillable **Lane**
        (:meth:`~git_loopy.rolling_scheduler.RollingScheduler.reserve`) and
        spawn one lifecycle task per reservation (:meth:`_run_lane_lifecycle`)
        FIRST — so a single freshly eligible ``parallel-safe`` issue is never
        made to wait behind co-occurring **serial-required** work (#219 §1.4,
        criterion #9) — no barrier: a finished Lane's slot refills the instant
        its contribution is admitted or terminates, while every other Lane's
        worktree setup and session continue unblocked. THEN latch serial
        demand for any serial-required (non-``parallel-safe``)
        ``ready-for-agent`` work (:meth:`_service_serial_required_work` —
        skipped once already latched): this only ever withholds *new*
        reservations
        (:attr:`~git_loopy.rolling_scheduler.RollingScheduler.refillable`),
        never an already-open Lane, and the scheduler only grants the actual
        serial turn once every open Lane has drained
        (:attr:`~git_loopy.rolling_scheduler.RollingScheduler.quiescent`) — so
        latching it early (as soon as any serial-required work is seen) merely
        stops further refill while in-flight Lanes finish, rather than
        preempting them. Concurrency never exceeds the scheduler's own
        :attr:`~git_loopy.rolling_scheduler.RollingScheduler.effective_limit`,
        since ``reserve`` is the only place a Lane is ever claimed. Drains to
        completion via
        :attr:`~git_loopy.rolling_scheduler.RollingScheduler.quiescent` and
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.confirm_empty` —
        the latter authoritative about the **Parallel-safe** half of the Pool
        only, which is why the ``empty_pool`` claim also requires
        :meth:`_service_serial_required_work` to have seen the whole other half
        this turn (#219 §2.13, criteria #5/#6).
        """
        assert self._scheduler is not None  # guarded by `self._rolling_capable`
        scheduler = self._scheduler
        scheduler.start()
        self._crash = None

        try:
            while True:
                if self._crash is not None:
                    raise self._crash

                # Cleared before the decision it feeds, never after: a slot
                # freed after `reserve()` has read the bounds must still earn
                # its own turn.
                self._capacity_freed.clear()

                # Observe pressure BEFORE reserving, so a contraction this turn
                # bounds this turn's refill rather than the next one's. Paced
                # by the monitor's own clock, not by how often the driver wakes
                # (#219 §6) — driver turns are event-driven and would otherwise
                # pack a six-observation window into a fraction of a second.
                self._report_concurrency_change()

                for reservation in scheduler.reserve():
                    task = asyncio.create_task(
                        self._guarded_lane_lifecycle(reservation)
                    )
                    self._pending.add(task)

                serial_pool_seen = self._service_serial_required_work()

                # `serial_turn()` itself has neither `max_iterations` nor abort
                # awareness (it only gates on the serial latch + full
                # quiescence, #219 §5.5-5.6) — unlike `reserve()`, whose
                # `refillable` already folds both in. So the driver pre-checks
                # them itself, mirroring serial mode's own pre-check
                # (`_drive_serial_only`): once the cap is spent or §7.7's
                # drain-confirmed abort is latched, a newly latched serial
                # demand (e.g. a REASON_SERIAL_FALLBACK from an unpublished
                # Lane) is left latched but un-run — a serial Iteration is NEW
                # work, and a drain finishes started work rather than starting
                # more — and the very next idle-check below reports
                # `iteration_cap` / `stuck` instead.
                if (
                    scheduler.remaining_units != 0
                    and not scheduler.abort_latched
                    and scheduler.serial_turn()
                ):
                    self._report_serial_fallback(scheduler)
                    outcome, _commits, _closures = (
                        await self._serial._run_one_iteration(self._alloc_iter_num())
                    )
                    # Reconcile the shared `max_iterations` budget into the
                    # scheduler's own ledger: it only spends a unit at
                    # `start_session` (Lane sessions), so a serial
                    # Iteration's unit is folded in here rather than tracked
                    # by a second, divergeable counter.
                    scheduler._units_spent += 1
                    if outcome == "aborted":
                        # §7.4, §7.7: the Strike machine is shared, so a serial
                        # Iteration reaching its limit latches the same
                        # drain-confirmed abort a finalized Lane contribution
                        # does (`_apply_strike_reaction`). Discarding it here
                        # let a Parallel-mode Run emit the abort Event and then
                        # grant itself serial Iterations forever.
                        scheduler.strike_limit_reached()
                    if outcome == "all_skipped":
                        # #413, and terminal *here* rather than latched for the
                        # idle-check, because the scheduler grants a serial turn
                        # only once every Lane has drained (`quiescent`): a
                        # serial Pickup that binds nothing at that moment has
                        # walked the whole Pool — both halves — with nothing in
                        # flight behind it. Continuing would re-latch the same
                        # serial demand, run the same Iteration and skip the same
                        # candidates for as long as the Run has units, which is
                        # the livelock this outcome exists to end.
                        return (
                            "all_skipped",
                            exit_code_for("all_skipped"),
                            scheduler._units_spent,
                        )
                    # An `empty_pool` outcome is deliberately NOT terminal here:
                    # it is one Iteration's view of the Pool, and #219 §2.14
                    # ends a Run only on the final authoritative refresh the
                    # idle-check below performs.
                    scheduler.serial_finished()
                    continue

                if self._pending:
                    await self._await_capacity()
                    continue

                # Fully idle: nothing pending, nothing just reserved, no
                # serial turn granted. `self._pending` empty implies
                # `scheduler.quiescent` — a contribution's Lane task never
                # returns until its whole Integration cascade (including any
                # parked contribution it admits from the FIFO) has finished
                # (see `_integrate_contribution`) — so it is safe to ask the
                # scheduler for a terminal outcome here.
                if scheduler.abort_latched and scheduler.quiescent:
                    return "stuck", exit_code_for("stuck"), scheduler._units_spent
                if scheduler.remaining_units == 0 and scheduler.quiescent:
                    return (
                        "iteration_cap",
                        exit_code_for("iteration_cap"),
                        scheduler._units_spent,
                    )
                if serial_pool_seen and scheduler.confirm_empty():
                    return (
                        "empty_pool",
                        exit_code_for("empty_pool"),
                        scheduler._units_spent,
                    )
                if not serial_pool_seen:
                    # #219 §2.13: the **serial-required** half of the Pool was
                    # not fully read this turn, so nothing may claim it empty.
                    # Poll again rather than exit — the Run has no evidence yet,
                    # which is a different thing from evidence of no work.
                    self._diag.warning(
                        "serial-required Pool read was partial; "
                        "not claiming an empty Pool"
                    )
                await asyncio.sleep(_ROLLING_EMPTY_POLL_INTERVAL)
        finally:
            if self._pending:
                for task in self._pending:
                    task.cancel()
                await asyncio.gather(*self._pending, return_exceptions=True)
                self._pending.clear()

    @property
    def finalized_contributions(self) -> tuple[rolling_scheduler.Contribution, ...]:
        """Every **Lane contribution** this Run closed out, for **Demotion** (#366).

        The per-pair, per-progress record ADR-0030 needs and the Run summary
        cannot supply. Each row carries the **Routed pair** bound once at
        **Pickup** (``model`` / ``reasoning_effort``) and the terminal reason set
        at finalization, where ``published`` is the only progress — which is
        exactly the two facts Demotion counts, at the one moment they are both
        known.

        Read at the quiescent point after :meth:`drive` returns, so no Lane is
        still running and no row can arrive after the count was taken. ``()``
        when a Parallel Run never built a scheduler, because a Run must not fail
        at its last step over having had nothing to demote.
        """
        if self._scheduler is None:
            return ()
        return self._scheduler.finalized

    def _report_concurrency_change(self) -> None:
        """Announce an effective-Lane-limit transition, if this turn caused one.

        #219 §6 emits ``wrapper.concurrency.changed`` "only for authoritative
        state transitions, not every sample", which is precisely the monitor's
        ``None``/:class:`~git_loopy.rolling_concurrency.LimitChange` answer — a
        turn that was not due, a Run with adaptation disabled, and a sampled
        turn with nothing to announce all read the same way here.

        The payload is the change's own, so the wire shape the Wrapper contract
        pins for all three runner families has exactly one author.
        """
        assert self._scheduler is not None
        change = self._pressure.observe(self._scheduler)
        if change is None:
            return
        self._serial._emit(
            events_module.WRAPPER_CONCURRENCY_CHANGED,
            iter_num=None,
            **change.payload,
        )

    async def _await_capacity(self) -> None:
        """Block until Rolling dispatch could reserve differently (#219 §4.4).

        Two distinct things can change the refill decision, and waiting on only
        one of them starves the other. A Lane lifecycle task **returning** frees
        its Lane, which is what the retired Wave's ``gather`` barrier
        approximated. But a task also frees capacity *mid-flight* — admission
        releases the contribution's Lane slot while that same task carries on
        into Integration, and a ``finalize`` frees an **Integration backlog**
        slot, lifting backpressure — and that half can be several bounded
        auto-resolution agent sessions from returning. This waits on both, so
        recovery never quietly occupies the capacity it released.

        Completed lifecycle tasks are retired from :attr:`_pending` here, which
        keeps "``_pending`` is empty" a safe stand-in for full scheduler
        quiescence in :meth:`_drive_rolling`'s terminal checks.
        """
        freed = asyncio.ensure_future(self._capacity_freed.wait())
        try:
            done, _still_pending = await asyncio.wait(
                {*self._pending, freed}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            freed.cancel()
        self._pending -= done

    def _report_serial_fallback(
        self, scheduler: rolling_scheduler.RollingScheduler
    ) -> None:
        """Say why this serial **Iteration** is running instead of a **Lane** (#304).

        Emitted immediately before the serial Iteration it explains, so a
        replay — and the operator's own output, which the renderer builds from
        this Event — reads the reason and then the Iteration. A serial turn
        granted while eligible Parallel-safe work remains is #219 §5's
        drain-everything interleaving rather than a fallback, and the scheduler
        answers ``None`` for it, so nothing is emitted.

        Costs no tracker read: the counts come off the **Pool** membership
        cache and the Run-scoped worked guard the scheduler already holds, so
        this slice adds visibility and changes no dispatch decision.
        """
        fallback = scheduler.serial_fallback()
        if fallback is None:
            return
        self._serial._emit(
            events_module.WRAPPER_PARALLEL_SERIAL_FALLBACK,
            iter_num=None,
            eligible=fallback.eligible,
            unavailable=fallback.unavailable,
            worked=fallback.worked,
            reason=fallback.reason,
            lane_cap=self._config.parallel,
        )

    def _service_serial_required_work(self) -> bool:
        """Latch serial demand for **serial-required** work, and say what was seen.

        #219 §1.4 retires the Wave's ">= 2 eligible" threshold for
        ``parallel-safe`` issues entirely (see :meth:`_drive_rolling`) — but a
        plain ``ready-for-agent`` issue (no ``parallel-safe`` label), a pull
        request, or a PRDs-backend item is never Lane work
        (:func:`~git_loopy.rolling_pool.is_parallel_safe`), and Pool
        membership only ever surfaces Parallel-safe candidates to the
        scheduler — so nothing inside the scheduler can ever discover that
        demand on its own. The driver peeks the full AFK-ready pool once per
        turn (mirroring how often the retired Wave's own
        ``_collect_pool_safely`` ran) and asks the scheduler to latch serial
        ownership (:meth:`~git_loopy.rolling_scheduler.RollingScheduler.request_serial`)
        the moment it finds anything else — and reports that latch
        (:meth:`_report_serial_latch`), since this is the only place in the Run
        that ever knows how big that half of the Pool is (#356).

        Returns:
            Whether this turn saw the **whole** serial-required half of the
            Pool. This peek is the only thing that ever can: the **Rolling
            dispatch** membership cache filters down to Parallel-safe
            candidates, so
            :meth:`~git_loopy.rolling_scheduler.RollingScheduler.confirm_empty`
            is authoritative about one half of a Parallel-mode Pool and silent
            about the other. #219 §2.13 forbids a partial read from
            establishing emptiness, so a turn that skipped the peek (demand is
            already latched) or read it incompletely (a candidate's
            authoritative read failed) answers ``False`` — unknown, which is
            not the same as empty.
        """
        assert self._scheduler is not None
        if self._scheduler.serial_latched:
            # Already latched: the peek would change no decision, and paying a
            # full `collect_pool` per drain turn to learn that would cost real
            # API capacity. Nothing was seen, so nothing may be claimed.
            return False
        collection = self._collect_pool_safely()
        serial_required = [
            item
            for item in collection.items
            if not (isinstance(item.ref, int) and LABEL_PARALLEL_SAFE in item.labels)
        ]
        if serial_required:
            # The whole peek is counted before the latch, not just the first
            # hit: the ref is what the scheduler needs and the count is what
            # the operator needs. One latched issue number cannot tell "one
            # more thing to do after this Lane" from "forty" (#356).
            self._scheduler.request_serial(
                ref=serial_required[0].ref,
                reason=rolling_scheduler.SERIAL_LATCH_NOT_PARALLEL_SAFE,
            )
            self._report_serial_latch(
                ref=serial_required[0].ref,
                reason=rolling_scheduler.SERIAL_LATCH_NOT_PARALLEL_SAFE,
                serial_required=len(serial_required),
            )
        return collection.complete

    def _report_serial_latch(
        self,
        *,
        ref: int | str | None,
        reason: str,
        serial_required: int | None,
    ) -> None:
        """Say that refill just stopped, and what is waiting behind the drain (#356).

        Emitted where the latch happens rather than where the serial turn is
        finally granted. Those are not the same moment: in between sit every
        in-flight Lane's session, its **Integration** and any bounded
        auto-resolution, and an explicit ``max-iterations`` cap can end the Run
        before the grant ever arrives (:meth:`_drive_rolling`) — in which case
        the grant-time report (:meth:`_report_serial_fallback`) never runs at
        all and the latched half of the **Pool** is never mentioned. The
        Run-start banner is scoped to **Lanes**, so an operator watching that
        window has nothing to correct the impression that non-``parallel-safe``
        work is excluded from the Run.

        Args:
            ref: The issue whose demand latched this.
            reason: One of
                :data:`~git_loopy.rolling_scheduler.SERIAL_LATCH_REASONS`.
            serial_required: How many serial-required items the peek saw, or
                ``None`` when nothing counted them. The driver's peek is the
                only thing that ever can (Pool membership filters down to
                **Parallel-safe** candidates) and it is skipped once demand is
                latched, so an **Integration** fallback latch genuinely does
                not know — and an unknown count is reported as unknown rather
                than as a ``1`` nobody measured.
        """
        self._serial._emit(
            events_module.WRAPPER_SERIAL_REQUESTED,
            iter_num=None,
            issue=ref,
            reason=reason,
            serial_required=serial_required,
            refill_stopped=True,
        )

    def _collect_pool_safely(self) -> PoolCollection:
        """Peek the full AFK-ready pool for the serial-required demand check.

        A source failure degrades to an **incomplete** empty collection (a
        genuine serial Iteration re-collects the pool itself and owns the real
        error path), so a transient collection error never crashes the driver
        loop — and never passes itself off as an empty **Pool** either.

        Deliberately silent about **Pool** exclusions (#303): this is a peek,
        and the serial Iteration this peek may provoke re-collects and reports
        them itself. Reporting here would emit the same exclusion twice for one
        candidate.
        """
        try:
            return self._source.collect_pool()
        except Exception as exc:
            self._diag.warning(
                "parallel pool peek failed: %s: %s; treating as unread",
                type(exc).__name__, exc,
            )
            return PoolCollection(complete=False)

    async def _guarded_lane_lifecycle(
        self, reservation: rolling_scheduler.Reservation
    ) -> None:
        """Run one reservation's lifecycle, capturing any crash for the driver.

        A bare :class:`asyncio.Task` swallows an unhandled exception silently
        (it only logs "exception was never retrieved" once garbage
        collected), which would quietly stop driving that Lane without ever
        surfacing to :meth:`_drive_rolling` — this stashes the first crash in
        :attr:`_crash` for the driver to re-raise on its own task, preserving
        the retired Wave's "crashed" outcome / exit-code contract.
        """
        try:
            await self._run_lane_lifecycle(reservation)
        except Exception as exc:  # pragma: no cover - defensive
            if self._crash is None:
                self._crash = exc

    async def _run_lane_lifecycle(
        self, reservation: rolling_scheduler.Reservation
    ) -> None:
        """One reservation's full lifecycle: setup, session, finish, Integration.

        The concurrent unit of Rolling dispatch (#219, ADR-0020): worktree
        creation, per-Lane setup, and the agent session for THIS reservation
        overlap with every other Lane's lifecycle task and any in-flight
        Integration — there is no barrier (criteria #2, #3). A worktree
        creation failure releases the reservation with no trace
        (:meth:`~git_loopy.rolling_scheduler.RollingScheduler.release`, §3.3)
        — no Lane contribution, no Summary row, no consumed
        ``max_iterations`` unit, no Strike, and the candidate stays eligible.
        Everything past that point mints a **Lane contribution** via
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.start_session`
        and is tracked by ``contribution_id`` — never ``lane_id`` — in
        ``self._lane_work`` (#219 §7), so it survives its Lane slot being
        freed for reuse. A contribution admitted to Integration is finished
        inline, still inside this task, serialized against every other
        contribution's Integration via ``self._integration_lock``.
        """
        assert self._scheduler is not None
        scheduler = self._scheduler
        item = reservation.item
        ref = item.ref

        def passed_over(reason: str) -> None:
            """Record this candidate as passed over before releasing it (#397).

            §3.3 releases a failed setup "with no trace", which was defensible
            while the trace nobody kept was of a candidate that simply stayed
            eligible for the next Lane. It is not defensible as the *only*
            record: a candidate whose ``task-type:`` label never parses is
            refused by every Lane forever, which is precisely the
            indefinitely-passed-over shape ADR-0032 exists to make visible.
            The release itself is unchanged — this leaves the record, not a
            different scheduling decision.
            """
            self._serial._emit_pickup_skipped(
                iter_num=None,
                issue=ref,
                reason=reason,
                position=reservation.position,
                considered=reservation.considered,
            )

        if not isinstance(ref, int):
            # Rolling-eligible candidates are always int refs
            # (`is_parallel_safe` requires it); this only defends a future
            # non-int ref from ever reaching a worktree/branch-name helper
            # that assumes one.
            passed_over("not an issue number")
            scheduler.release(reservation)
            return

        # Resolve this Lane's (model, effort) ONCE at Active-issue pickup —
        # the structural per-Lane seam per-issue routing hangs off (#148). An
        # invalid label is refused before a worktree exists. An unlabelled Lane
        # (or routing-off) yields the gated global default. `start_session`
        # binds the pair onto the Contribution, reused for this Lane's work AND
        # its later auto-resolution sessions.
        #
        # Routed *through the serial Pickup's own seam* (#408) so a Lane reads
        # the same **Escalation rung** ledger a serial Iteration does: the
        # ledger is keyed by issue, not by mode, so an issue that stalled
        # silently on either path is retried at the rung on whichever path
        # takes it next.
        try:
            resolution = self._serial._resolve_route(
                item,
                warn=lambda message, _ref=ref: self._diag.warning(
                    "lane #%s routing: %s", _ref, message
                ),
            )
        except TaskTypeError as exc:
            self._diag.error("lane #%s routing refused: %s", ref, exc)
            passed_over(f"routing refused: {exc}")
            scheduler.release(reservation)
            raise

        # The **Task-type classifier**'s second call site (#409, ADR-0029),
        # through the same shared seam. Deliberately *after* the refusal above:
        # a candidate whose human labelling is already broken is passed over,
        # not spent on. It runs before the worktree exists because what it
        # decides is the pair this Lane is created for.
        item, resolution = await self._serial._classify_at_pickup(
            item, routed=resolution
        )

        model = resolution.model
        reasoning_effort = resolution.reasoning_effort
        base = self._resolve_base_ref()
        branch = git_module.lane_branch_name(self._run_id, ref)
        path = _lane_worktree_path(self._repo_root, self._run_id, ref)
        try:
            wt_git = self._git.add_worktree(path, branch=branch, base=base)
        except git_module.GitError as exc:
            self._diag.warning(
                "worktree add for issue #%s failed: %s; releasing reservation",
                ref, exc,
            )
            passed_over(f"worktree setup failed: {exc}")
            scheduler.release(reservation)
            return

        lane_work = _LaneWork(item=item, branch=branch, path=path, git=wt_git)
        try:
            lane_work.pre_sha = wt_git.head_sha()
        except git_module.GitError as exc:
            self._diag.warning("lane #%s pre head_sha failed: %s", ref, exc)

        # Prepare the freshly created worktree before its agent session
        # starts (#65). Non-fatal: a broken environment still lets the agent
        # try, and one Lane's setup can never take down another's task.
        self._setup_lane_worktree(lane_work)

        contribution = scheduler.start_session(
            reservation, model=model, reasoning_effort=reasoning_effort
        )
        self._lane_work[contribution.contribution_id] = lane_work
        self._open_contribution_accounting(contribution)

        lane_binding = self._serial._new_active_issue_binding(
            None, allowed_refs=(ref,), lane_issue=ref
        )
        # The **Pickup** record, written where the binding is published rather
        # than where routing resolved (#397). Everything between those two
        # points can still pass the candidate over, and a Lane that emitted
        # "bound" and then a skip for the same issue would be two facts where
        # there is one. So the binding record is emitted only once the Lane
        # genuinely holds the issue, and every skip precedes it — the
        # resolution resolved above rides along on it (#407), because the pair
        # a Lane runs on is the same fact whether it is being carried to the
        # session or to a reader.
        self._serial._emit_pickup_bound(
            iter_num=None,
            issue=ref,
            reason=reason_for(item.ref, item.labels, pin=self._config.issue_pin),
            position=reservation.position,
            considered=reservation.considered,
            resolution=resolution,
        )
        lane_binding.bind(ref, source="lane_pickup", at=datetime.now(timezone.utc))

        try:
            recent = self._git.recent_commits(5)
        except git_module.GitError as exc:
            self._diag.warning(
                "recent_commits failed: %s; using empty prefix", exc
            )
            recent = []
        commits_block = _format_recent_commits(recent)

        request = self._build_contribution_request(
            contribution, lane_work, commits_block
        )
        self._contribution_context[id(request)] = (contribution, lane_work)
        try:
            outcome = await self._execution_host.run_contribution(request)
        finally:
            self._contribution_context.pop(id(request), None)
        signals, changed, checkpoint_ok = self._lane_outcome_signals.pop(
            id(request)
        )
        if isinstance(outcome, execution_host_module.ContributionFailure):
            self._diag.warning(
                "lane #%s execution host (%s) refused contribution: %s (%s)",
                ref, self._execution_host.placement,
                outcome.reason, outcome.detail,
            )

        # The Lane's **Session outcome** (#403). `changed` is this mode's own
        # progress answer -- an agent commit or a Checkpoint on the Lane branch
        # -- so the ending agrees with what the scheduler was told about the
        # same contribution.
        lane_outcome = session_outcome_module.resolve_session_outcome(
            termination=signals.termination,
            progressed=changed,
            error=signals.error,
            content_filtered=signals.content_filtered,
            no_more_tasks=signals.no_more_tasks,
        )
        # Offered to the **Escalation rung**'s ledger before it is said out
        # loud (#408): a Lane that stalled silently is evidence about the
        # *issue*, and the Pickup that acts on it may well be a serial one.
        self._serial._observe_session_ending(lane_work.item.ref, lane_outcome)
        _report_session_outcome(
            self._diag, ref=lane_work.item.ref, record=lane_outcome
        )

        if checkpoint_ok:
            try:
                self._git.remove_worktree(lane_work.path, force=True)
            except git_module.GitError as exc:
                self._diag.warning(
                    "worktree remove for %s failed: %s", lane_work.path, exc
                )
        else:
            # §3.10: a Checkpoint failure preserves the dirty branch and
            # worktree for forensics / recovery rather than tearing it down.
            self._diag.warning(
                "lane #%s checkpoint failed; preserving worktree %s",
                ref, lane_work.path,
            )

        disposition = scheduler.finish_work(
            contribution, changed=changed, checkpoint_ok=checkpoint_ok
        )
        if disposition == rolling_scheduler.TERMINAL:
            self._lane_work.pop(contribution.contribution_id, None)
            self._finalize_contribution(contribution, published=False)
            return
        if disposition == rolling_scheduler.ADMITTED:
            # §3.9: the Lane slot is free again *now*, while this task carries
            # on through Integration (and possibly K auto-resolution sessions).
            self._capacity_freed.set()
            await self._integrate_contribution(contribution)
            return
        # PARKED (§3.9, §4.1): the H=2 Integration backlog is full, so this
        # contribution's Lane is retained and its state stays in
        # `self._lane_work`. It is finalized later, from inside whichever
        # OTHER contribution's `finalize()` drains the FIFO — see
        # `_integrate_contribution`'s recursive admission handling.

    def _setup_lane_worktree(self, lane_work: _LaneWork) -> None:
        """Prepare a Lane's freshly created worktree before its session (#65).

        Runs the injected :class:`~git_loopy.worktree.WorktreeSetup` — the
        configured ``GIT_LOOPY_WORKTREE_SETUP`` command or a best-effort
        auto-detected install — in ``lane_work.path``. A setup **failure is
        surfaced** (a warning, never silently ignored) but never aborts the
        Lane: a broken environment still lets the agent try (its own feedback
        loops then fail visibly), and one Lane's setup can never take down
        another Lane's concurrent task.
        """
        try:
            result = self._worktree_setup.run(lane_work.path)
        except Exception as exc:  # never let setup abort the Lane
            self._diag.warning(
                "worktree setup for issue #%s raised %s: %s; continuing",
                lane_work.item.ref, type(exc).__name__, exc,
            )
            return
        if not result.ran:
            return
        if result.passed:
            self._diag.info(
                "worktree setup for issue #%s ran %r",
                lane_work.item.ref, result.command,
            )
            return
        self._diag.warning(
            "worktree setup for issue #%s FAILED (exit %s): %r; continuing "
            "(agent will still run). Output tail: %s",
            lane_work.item.ref, result.returncode, result.command,
            result.output_tail,
        )

    def _build_contribution_request(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        commits_block: str,
    ) -> execution_host_module.ContributionRequest:
        """Build the Execution host seam's one input shape for this Lane.

        Carries exactly what spec #445 §A names — issue reference, rendered
        prompt, base revision, resolved model/reasoning pair, the Effective
        Skill policy, and the Run's ``run_id`` — and nothing about *how* the
        host should do the work.
        """
        prompt = (
            f"Previous commits: {commits_block} "
            f"Issues: {lane_work.item.rendered_block} {self._prompt_text}"
        )
        return execution_host_module.ContributionRequest(
            issue_ref=lane_work.item.ref,
            prompt=prompt,
            base_revision=self._resolve_base_ref(),
            model=contribution.model,
            reasoning_effort=contribution.reasoning_effort,
            skill_policy=self._skill_exposure,
            run_id=self._run_id,
        )

    async def _run_local_contribution(
        self, request: execution_host_module.ContributionRequest
    ) -> execution_host_module.LocalRunResult:
        """The ``local`` Execution host's runner (#447).

        This is exactly today's Lane-contribution mechanics — run the agent
        session (:meth:`_run_lane_session`), then account for it and
        Checkpoint a dirty tree (:meth:`_account_lane`) — reached through
        :class:`~git_loopy.execution_host.LocalExecutionHost` rather than
        called directly, so every Lane contribution now travels through the
        seam with today's behaviour otherwise unchanged. The session-outcome
        half the loop still needs (`_LaneSessionSignals`, `changed`,
        `checkpoint_ok`) is stashed in ``self._lane_outcome_signals`` for
        ``_run_lane_lifecycle`` to read back once the host returns — it is
        deliberately not part of the seam's own outcome contract.
        """
        contribution, lane_work = self._contribution_context[id(request)]
        signals = await self._run_lane_session(
            contribution, lane_work, request.prompt
        )
        changed, checkpoint_ok = self._account_lane(contribution, lane_work)
        self._lane_outcome_signals[id(request)] = (signals, changed, checkpoint_ok)
        try:
            sha = lane_work.git.head_sha()
        except git_module.GitError:
            sha = None
        try:
            dirty = lane_work.git.is_dirty()
            untracked = lane_work.git.has_untracked()
        except git_module.GitError:
            # A dirty-check failure is not itself evidence of dirt; today's
            # `_maybe_checkpoint_lane` treats the same failure as "skip,
            # don't block" and this mirrors it rather than manufacturing a
            # rejection the loop's own accounting never raised.
            dirty, untracked = False, False
        return execution_host_module.LocalRunResult(
            branch=lane_work.branch,
            sha=sha,
            dirty=dirty,
            untracked=untracked,
        )

    async def _run_lane_session(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        prompt: str,
    ) -> _LaneSessionSignals:
        """Run one Lane contribution's SDK session, pinned to its worktree.

        Concurrent by construction with every other Lane's session and any
        in-flight Integration (#219, ADR-0020) — bulletproof like the retired
        Wave's ``_run_lane_session``: a timeout, a send failure, or a
        session-lifecycle error is swallowed so one Lane can never abort
        another's task or the driver loop; the caller then accounts and finishes
        the contribution as no-progress.

        What it now returns instead of logging (#403): the two halves of the
        ending only this method can see — how the wait came back, and the
        structured identity of any failure the harness reported on the way. The
        ending itself is resolved by the caller, because the missing half is
        whether the Lane's branch carries anything, and that is not known until
        the Lane is accounted.

        ``prompt`` arrives fully rendered (#447): the Execution host seam's
        :class:`~git_loopy.execution_host.ContributionRequest` carries the
        already-assembled prompt
        (:meth:`_ParallelLoop._build_contribution_request`), so this method
        no longer reassembles it from a commit-log fragment.
        """
        send_timeout = self._config.send_timeout_seconds
        termination = session_outcome_module.SessionTermination.COMPLETED
        raised: session_outcome_module.SessionError | None = None
        watch = session_outcome_module.SessionOutcomeWatch()
        scope = self._contribution_iter.get(contribution.contribution_id)
        agent_started = time.monotonic()
        try:
            # `self._serial._session_observer` chains the rollup with the
            # Run-scoped `_cost_meter`. Feeding the rollup used to be unsafe
            # here — it held ONE "current Iteration" slot that concurrent Lane
            # sessions would have corrupted, so Lane Consumption reached
            # neither the Summary nor the durable Run summary (#219/#306). It
            # now holds one scope per **Lane contribution**, keyed by the
            # `lane_issue` every event this session records is stamped with
            # (#310), so each Lane's tokens land in its own contribution's row
            # however many other Lanes are running.
            #
            # The Run-scoped `_cost_meter` half is what AI-credit pressure is
            # judged against (#309): it holds no per-scope slot, only a running
            # total, so without it a Parallel Run could never price itself and
            # credit pressure would stay permanently unknown.
            async with IterationSession(
                self._client,
                config=self._config,
                event_log=self._writers.event_log,
                sinks=self._sinks,
                run_id=self._run_id,
                iter_num=scope.iter_num if scope is not None else 0,
                model=contribution.model,
                reasoning_effort=contribution.reasoning_effort,
                working_directory=str(lane_work.git.root),
                issue_ref=lane_work.item.ref,
                skill_exposure=self._skill_exposure,
                event_observer=_ChainedObserver(
                    observers=(self._serial._session_observer, watch)
                ),
            ) as sdk_session:
                try:
                    await sdk_session.send_and_wait(
                        prompt, timeout=send_timeout
                    )
                except asyncio.TimeoutError:
                    termination = session_outcome_module.SessionTermination.TIMED_OUT
                except Exception as exc:
                    termination = session_outcome_module.SessionTermination.CRASHED
                    raised = session_outcome_module.SessionError.from_exception(
                        exc, origin="send"
                    )
        except Exception as exc:
            termination = session_outcome_module.SessionTermination.CRASHED
            raised = session_outcome_module.SessionError.from_exception(
                exc, origin="session_lifecycle"
            )
        if scope is not None:
            scope.agent_seconds += max(0.0, time.monotonic() - agent_started)
        return _LaneSessionSignals(
            termination=termination,
            error=session_outcome_module.strongest_error(watch.error, raised),
            content_filtered=watch.content_filtered,
            no_more_tasks=watch.no_more_tasks,
        )

    def _account_lane(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
    ) -> tuple[bool, bool]:
        """Post-session per-Lane commit accounting + per-worktree Checkpoint.

        Reads the Lane branch's post-session head, emits one
        ``wrapper.commit.recorded`` per new commit (each Lane's Consumption
        attributed to its own issue), then captures any dirty / untracked
        work in a per-worktree Checkpoint on the Lane branch (ADR-0004).

        Returns:
            ``(changed, checkpoint_ok)`` for
            :meth:`~git_loopy.rolling_scheduler.RollingScheduler.finish_work`
            (#219 §3.7-3.10): ``changed`` is ``True`` iff the branch carries
            durable work — an agent commit or a successful Checkpoint commit
            both count (§3.9) — and ``checkpoint_ok`` is ``False`` only on an
            actual Checkpoint *failure*, never on "nothing to checkpoint".
        """
        wt_git = lane_work.git
        ref = lane_work.item.ref
        if lane_work.pre_sha is None:
            new_commits: list[git_module.Commit] = []
        else:
            try:
                head = wt_git.head_sha()
                new_commits = wt_git.commits_between(lane_work.pre_sha, head)
            except git_module.GitError as exc:
                self._diag.warning(
                    "lane #%s commit accounting failed: %s", ref, exc
                )
                new_commits = []

        for c in new_commits:
            self._serial._emit(
                events_module.WRAPPER_COMMIT_RECORDED,
                iter_num=None,
                sha=c.sha,
                subject=c.subject,
                date=c.date,
                lane_issue=ref,
            )

        checkpoint_sha, checkpoint_ok = self._maybe_checkpoint_lane(lane_work)
        changed = bool(new_commits) or checkpoint_sha is not None
        return changed, checkpoint_ok

    def _maybe_checkpoint_lane(
        self, lane_work: _LaneWork
    ) -> tuple[str | None, bool]:
        """Per-worktree Checkpoint on a Lane branch (ADR-0004, per-Lane).

        Mirrors :meth:`_Loop._maybe_checkpoint` but scoped to the Lane's own
        worktree and attributed to the Lane's issue.

        Returns:
            ``(sha, ok)`` — ``ok`` is ``False`` only when the dirty-check or
            the checkpoint commit itself failed, never when there was simply
            nothing to checkpoint, so :meth:`_run_lane_lifecycle` can tell
            "clean, nothing to do" apart from "a real failure" per §3.10 (a
            Checkpoint failure preserves the dirty worktree instead of
            tearing it down).
        """
        wt_git = lane_work.git
        ref = lane_work.item.ref
        try:
            dirty = wt_git.is_dirty()
            untracked = wt_git.has_untracked()
        except git_module.GitError as exc:
            self._diag.warning(
                "lane #%s checkpoint dirty-check failed: %s; skipping", ref, exc
            )
            return None, False
        if not (dirty or untracked):
            return None, True
        try:
            wt_git.add_all()
            sha = wt_git.commit(checkpoint_message(ref))
        except git_module.GitError as exc:
            self._diag.warning(
                "lane #%s checkpoint commit failed: %s; continuing without it",
                ref, exc,
            )
            return None, False
        self._serial._emit(
            events_module.WRAPPER_CHECKPOINT_RECORDED,
            iter_num=None,
            sha=sha,
            issue=ref,
            lane_issue=ref,
        )
        return sha, True

    async def _integrate_contribution(
        self, contribution: rolling_scheduler.Contribution
    ) -> None:
        """Integrate one admitted contribution, then drain whatever it frees.

        Runs from inside whichever Lane lifecycle task got ``ADMITTED`` (or
        recursively, from inside another contribution's own ``finalize()``,
        for one newly-admitted from the parked FIFO) — serialized against
        every other contribution's Integration via ``self._integration_lock``
        so the shared main worktree never sees two concurrent merges, while
        every OTHER Lane's worktree setup and session continue unblocked
        (#219, ADR-0020, criteria #2/#3). A contribution's Lane slot is
        already free the instant it was admitted
        (:meth:`~git_loopy.rolling_scheduler.RollingScheduler.finish_work`);
        this only finishes the contribution itself, tracked by
        ``contribution_id`` — never ``lane_id`` — so it correctly survives
        that Lane moving on to a new issue (#219 §7, criterion #7).
        """
        assert self._scheduler is not None
        async with self._integration_lock:
            latched_before = self._scheduler.serial_latched
            lane_work = self._lane_work.pop(contribution.contribution_id, None)
            if lane_work is None:  # pragma: no cover - defensive
                self._diag.error(
                    "integration #%s: missing lane state for contribution %s",
                    contribution.ref, contribution.contribution_id,
                )
                published = False
            else:
                published = await self._integrate_lane(contribution, lane_work)
            newly_admitted = self._scheduler.finalize(
                contribution, published=published
            )
            if latched_before != self._scheduler.serial_latched:
                # §5.2: an unpublished contribution requests serial service of
                # its own. Reported on the same terms as the peek's latch —
                # refill has stopped and the operator is owed the reason —
                # with no count, because nothing counted the serial half here.
                # On the transition only: a second failed contribution while
                # demand is already latched stops no refill that is not already
                # stopped, and its own `wrapper.contribution.end` carries the
                # same `serial_fallback` disposition. The peek obeys the same
                # rule by being skipped entirely once latched.
                self._report_serial_latch(
                    ref=contribution.ref,
                    reason=rolling_scheduler.REASON_SERIAL_FALLBACK,
                    serial_required=None,
                )
            # `_finalize_contribution` applies the strike reaction itself, so it
            # is not applied again here (#310).
            self._finalize_contribution(contribution, published=published)
        # §4.4: this finalize freed an **Integration backlog** slot, lifting
        # backpressure, and each contribution it admitted from the parked FIFO
        # released the Lane that contribution had been retaining (§4.3).
        self._capacity_freed.set()
        for admitted in newly_admitted:
            await self._integrate_contribution(admitted)

    def _open_contribution_accounting(
        self, contribution: rolling_scheduler.Contribution
    ) -> None:
        """Open one **Lane contribution**'s own accounting scope (#310).

        Rolling dispatch has no round, so there is no round boundary to hang
        accounting off: several contributions are open at once and each
        outlives the reusable **Lane** slot it started in. Each therefore opens
        its own scope, announced by a ``wrapper.contribution.start`` carrying
        the identity triple, so the rollup, the Dashboard, and a replaying
        reader all attribute this contribution's **Consumption** and timing to
        the issue that produced them rather than to whichever issue occupies
        that slot by the time the row is cut.

        Deliberately *not* a ``wrapper.iteration.start``: a contribution is not
        an Iteration (no barrier round), and the Wrapper contract reserves the
        Iteration pair — and its positive ``iter`` — for serial work.
        """
        self._contribution_iter[contribution.contribution_id] = (
            _ContributionAccounting(
                iter_num=self._alloc_iter_num(),
                started_monotonic=time.monotonic(),
            )
        )
        self._emit_contribution_event(
            contribution, events_module.WRAPPER_CONTRIBUTION_START
        )

    def _emit_contribution_event(
        self,
        contribution: rolling_scheduler.Contribution,
        event_type: str,
        **payload: Any,
    ) -> None:
        """Dispatch one contribution-scoped lifecycle Event.

        Routed through
        :func:`~git_loopy.events.make_contribution_event`, which is the only
        way a Parallel-mode record acquires its identity: it stamps the whole
        triple and forces ``iter`` to ``null``, so replay never needs a mutable
        Lane-to-issue lookup and never mistakes a contribution for an
        Iteration.
        """
        self._serial._emitter.dispatch(
            events_module.make_contribution_event(
                event_type,
                self._run_id,
                contribution_id=contribution.contribution_id,
                issue=contribution.ref,
                lane_id=contribution.lane_id,
                **payload,
            )
        )

    def _finalize_contribution(
        self,
        contribution: rolling_scheduler.Contribution,
        *,
        published: bool,
    ) -> None:
        """Close one finalized **Lane contribution**: Strike reaction + row.

        The single seam a contribution finishes at, whether that is a
        ``TERMINAL`` disposition straight out of
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.finish_work` or a
        post-Integration
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.finalize`. Cutting
        the **Summary** row here — and only here — is what keeps the Summary
        finalized-contributions-only (#310): a contribution still parked,
        integrating, or in bounded auto-resolution has no partial row for a
        reader to mistake for a finished one, and the recovery sessions it may
        still run are folded into the row when it does finish.

        ``published`` is the contribution's own progress test. A Lane's commits
        sit on a Lane branch until **Integration** publishes them, so an
        unpublished contribution is honestly *no-progress* however much it
        committed; only a published one is progress (``advanced``, or
        ``closed`` once its ``wrapper.auto_close`` lands).
        """
        self._apply_strike_reaction(contribution)
        scope = self._contribution_iter.pop(contribution.contribution_id, None)
        if scope is None:  # pragma: no cover - defensive
            return
        try:
            rollup = self._serial._rollup.finish(
                iter_num=scope.iter_num,
                strikes=self._serial._strike_machine.strikes,
                advanced_issues=(contribution.ref,) if published else (),
                lane_issue=contribution.ref,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._diag.warning(
                "contribution #%s accounting rollup failed: %s",
                contribution.ref, exc,
            )
            return
        self._emit_contribution_event(
            contribution,
            events_module.WRAPPER_CONTRIBUTION_END,
            **rollup_module.contribution_end_payload(
                rollup,
                published=published,
                reason=contribution.reason or rolling_scheduler.REASON_UNCHANGED_BRANCH,
                strike_reaction=(
                    contribution.strike_reaction or rolling_scheduler.STRIKE_ADD
                ),
                reasoning_effort=contribution.reasoning_effort,
                recovery_attempts=scope.recovery_attempts,
                agent_seconds=scope.agent_seconds,
            ),
        )
        try:
            self._serial._writers.run_summary.record(
                IterationCounters.from_rollup(
                    iter_num=scope.iter_num, payload=rollup
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._diag.warning(
                "RunSummaryWriter.record failed for contribution #%s: %s",
                contribution.ref, exc,
            )

    def _apply_strike_reaction(
        self, contribution: rolling_scheduler.Contribution
    ) -> None:
        """Latch the scheduler's abort if the shared **Strike** ceiling is spent.

        #219 §7.4, §7.6 had this *tick* the shared machine once per finalized
        contribution, because a contribution that terminated unpublished was
        itself a Strike. Since #413 the ceiling counts the issues the Run has
        **skipped**, and those are charged where they happen — at the ending
        that defeats the issue (:meth:`_Loop._observe_session_ending`), which a
        Lane reaches as surely as a serial Iteration does. So nothing is charged
        here and nothing is announced here; what is left is §7.7's
        drain-confirmed abort, which still belongs at contribution finalization
        because that is the moment the scheduler can act on it.

        Reading the machine rather than a return value is deliberate: the Lane
        whose ending crossed the ceiling and the contribution that finalizes it
        are different moments, and a latch keyed to a value passed between them
        would go missing whenever they were not the same one.
        """
        assert self._scheduler is not None
        if self._serial._strike_machine.outcome == "aborted":
            self._scheduler.strike_limit_reached()

    async def _integrate_lane(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
    ) -> bool:
        """Integrate one admitted Lane contribution; return whether it landed green.

        **Private until green** (#219 §4.7-4.8, ADR-0020, criterion #5). The
        Lane branch is merged and gated inside a dedicated
        :class:`_IntegrationStage` worktree cut from the latest published green
        base; base is published only once that gate passes, by merging the
        *verified* Integration branch. This is what ADR-0020 supersedes
        ADR-0009's "publish an unverified merge, then revert it" for: a Lane
        reserved while this runs branches from either the prior green base or
        the newly published green base, never from an unverified merge — so
        there is nothing to revert on a red gate, and base cannot be observed
        red even transiently.

        A conflicting merge, a red gate, or a gate that cannot run all hand the
        same stage to bounded auto-resolution (:meth:`_auto_resolve_lane`, #63),
        which reuses this worktree so the resolution agent sees the exact tree
        that failed. The stage is reaped on every path.
        """
        ref = contribution.ref
        stage = self._open_integration_stage(ref)
        if stage is None:
            self._fallback_lane_to_serial(lane_work)
            return False

        try:
            try:
                stage.git.merge(lane_work.branch)
            except git_module.GitError as exc:
                # A conflicting merge, unwound inside the private stage — base
                # was never touched, so there is nothing to undo there.
                self._diag.warning(
                    "integration #%s: private merge of %s conflicted: %s; "
                    "aborting and auto-resolving",
                    ref, lane_work.branch, exc,
                )
                self._abort_stage_merge_safely(stage, ref)
                return await self._auto_resolve_lane(
                    contribution, lane_work, stage, conflicted=True
                )

            if self._gate_green(ref, "post-merge", stage.path):
                return self._publish_stage(contribution, lane_work, stage)

            # A red / un-runnable gate on the private result: base still has
            # not moved, so recovery simply continues in the same stage.
            return await self._auto_resolve_lane(
                contribution, lane_work, stage, conflicted=False
            )
        finally:
            self._reap_integration_stage(stage, ref)

    def _open_integration_stage(self, ref: int | str) -> _IntegrationStage | None:
        """Cut a private Integration worktree from the published green base.

        Returns ``None`` when the worktree cannot be created — the contribution
        then has no private place to be verified in, and #219 §4.14's terminal
        unpublished serial handoff is the honest outcome rather than falling
        back to gating on the shared base.
        """
        if not isinstance(ref, int):
            # The stage's worktree and branch names derive from the issue
            # number; a non-int ref cannot be staged (or recovered).
            return None
        branch = git_module.integration_branch_name(self._run_id, ref)
        path = _integration_worktree_path(self._repo_root, self._run_id, ref)
        try:
            git = self._git.add_worktree(
                path, branch=branch, base=self._resolve_base_ref()
            )
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: could not create private integration "
                "worktree: %s; falling back to serial",
                ref, exc,
            )
            return None
        return _IntegrationStage(branch=branch, path=path, git=git)

    def _reap_integration_stage(
        self, stage: _IntegrationStage, ref: int | str
    ) -> None:
        """Tear down the private stage on every path (#219 §4.16, criterion #9)."""
        try:
            self._git.remove_worktree(stage.path, force=True)
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: integration worktree remove failed: %s",
                ref, exc,
            )
        self._delete_branch_safely(ref, stage.branch)

    def _publish_stage(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        stage: _IntegrationStage,
    ) -> bool:
        """Publish a verified-green stage onto base, then close its issue.

        The only place base advances under Parallel mode, and it advances by
        merging the Integration branch the gate just passed on — never the raw
        Lane branch (#219 §4.8). **Integration** is serialized by
        ``self._integration_lock``, so base cannot have moved since the stage
        was cut and this merge is the verified result exactly.
        """
        ref = contribution.ref
        try:
            pre_base = self._git.head_sha()
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: base head_sha failed: %s; not publishing",
                ref, exc,
            )
            return False
        try:
            self._git.merge(stage.branch)
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: publish of verified %s failed: %s; not "
                "publishing",
                ref, stage.branch, exc,
            )
            return False
        if not self._base_advanced(pre_base, ref):
            return False
        self._land_lane(contribution, lane_work, pre_base)
        return True

    def _base_advanced(self, pre_base: str, ref: int | str) -> bool:
        """Return whether Integration published a new base head."""
        try:
            advanced = self._git.head_sha() != pre_base
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: post-merge head_sha failed: %s; "
                "publication remains unverified",
                ref,
                exc,
            )
            return False
        if not advanced:
            self._diag.warning(
                "integration #%s: merge produced no base advancement; "
                "not counting publication",
                ref,
            )
        return advanced

    def _gate_green(
        self, ref: int | str, phase: str, worktree: Path | None = None
    ) -> bool:
        """Run the injected gate on ``worktree`` (default repo root); ``True`` == green.

        A **red** gate or a :exc:`~git_loopy.gate.GateError` (cannot gate at all)
        both return ``False`` — the contribution stays unpublished in its private
        **Integration stage** and drives auto-resolution there; the ``phase``
        label enriches the diagnostic.
        """
        target = worktree if worktree is not None else self._repo_root
        try:
            result = self._gate_runner.run(target)
        except gate_module.GateError as exc:
            self._diag.warning(
                "integration #%s: %s gate could not run: %s; treating as red",
                ref, phase, exc,
            )
            return False
        if not result.passed:
            failure = result.failure
            self._diag.warning(
                "integration #%s: %s gate failed on %r (%s)",
                ref, phase,
                failure.name if failure else "unknown",
                failure.summary if failure else "no detail",
            )
            return False
        return True

    def _abort_stage_merge_safely(
        self, stage: _IntegrationStage, ref: int | str
    ) -> None:
        """``git merge --abort`` a conflicted **private** merge; failure only warns.

        Runs inside the :class:`_IntegrationStage`, never on base: under
        ADR-0020 base never attempts the Lane merge at all, so there is no
        conflicted base state to unwind.
        """
        try:
            stage.git.abort_merge()
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: private merge --abort failed: %s", ref, exc
            )

    def _land_lane(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        pre_base: str,
    ) -> None:
        """Finish a green landing: close the issue + delete the integrated branch."""
        self._close_landed(lane_work.item, pre_base)
        self._delete_branch_safely(contribution.ref, lane_work.branch)

    def _close_landed(self, item: AfkReadyItem, pre_base: str) -> None:
        """Close a landed issue via the serial closure path + emit ``auto_close``.

        Reads the commits the landing added to base (``pre_base`` -> current
        head) and drives the same runner-side closure as serial mode
        (``source.handle_completions`` -> ``gh issue close`` + the ``Closes #N``
        backstop), emitting one ``wrapper.auto_close`` per closure. Shared by the
        happy-path landing and a successful auto-resolution landing.
        """
        try:
            post_base = self._git.head_sha()
            landed = self._git.commits_between(pre_base, post_base)
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: post-merge accounting failed: %s",
                item.ref, exc,
            )
            landed = []
        for completion in self._serial._handle_completions_safely(
            [item], landed
        ):
            self._serial._emit(
                events_module.WRAPPER_AUTO_CLOSE,
                iter_num=None,
                issue=completion.ref,
                sha=completion.sha,
                shas=list(completion.shas),
                lane_issue=completion.ref,
            )

    def _delete_branch_safely(self, ref: int | str, branch: str) -> None:
        """``git branch -D`` an integrated branch; a failure only warns."""
        try:
            self._git.delete_branch(branch)
        except git_module.GitError as exc:
            self._diag.warning(
                "integration #%s: delete of %s failed: %s", ref, branch, exc
            )

    async def _auto_resolve_lane(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        stage: _IntegrationStage,
        *,
        conflicted: bool,
    ) -> bool:
        """Bounded auto-resolution inside the private stage (#63, ADR-0009/0020).

        Up to K=:data:`_AUTO_RESOLUTION_MAX_ATTEMPTS` times, runs a fresh
        resolution agent session pinned to the same
        :class:`_IntegrationStage` the failed merge/gate happened in
        (:meth:`_run_resolution_session`) and re-gates it. The first **green**
        attempt publishes that verified stage onto base
        (:meth:`_publish_stage`), closes the issue, deletes the (now-landed)
        Lane branch, and returns ``True``. If all K attempts stay red the
        contribution falls back to a serial Iteration
        (:meth:`_fallback_lane_to_serial`) and returns ``False``, keeping its
        Lane branch as a breadcrumb.

        Recovery costs no **Lane cap** capacity (#219 §4.11, criterion #8): it
        runs inside the admitted contribution's own task and never reserves a
        Lane. The stage itself is owned and reaped by :meth:`_integrate_lane`.

        Args:
            conflicted: Whether the Lane branch failed to merge at all (as
                opposed to merging cleanly and gating red) — the two states
                leave very different trees, so the brief must say which.
        """
        ref = contribution.ref
        for attempt in range(1, _AUTO_RESOLUTION_MAX_ATTEMPTS + 1):
            scope = self._contribution_iter.get(contribution.contribution_id)
            if scope is not None:
                scope.recovery_attempts = attempt
            await self._run_resolution_session(
                contribution, lane_work, stage, attempt, conflicted=conflicted
            )
            if not self._gate_green(
                ref, f"auto-resolution attempt {attempt}", stage.path
            ):
                continue
            if self._publish_stage(contribution, lane_work, stage):
                return True
        self._fallback_lane_to_serial(lane_work)
        return False

    async def _run_resolution_session(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        stage: _IntegrationStage,
        attempt: int,
        *,
        conflicted: bool,
    ) -> None:
        """Run one auto-resolution agent session in the private stage (#63).

        A fresh :class:`IterationSession` pinned to the same
        :class:`_IntegrationStage` worktree the merge/gate failed in, tasked to
        get that tree green and commit. Runs on the SAME ``(model, effort)``
        the contribution resolved once at pickup (#148), so a Lane's route is
        bound once and reused for its work and its recovery sessions alike.
        Bulletproof like :meth:`_run_lane_session` — a timeout or error is
        logged and swallowed so the attempt just reads as still-red and the
        bound advances.
        """
        prompt = self._resolution_prompt(
            contribution, lane_work, attempt, conflicted=conflicted
        )
        send_timeout = self._config.send_timeout_seconds
        try:
            async with IterationSession(
                self._client,
                config=self._config,
                event_log=self._writers.event_log,
                sinks=self._sinks,
                run_id=self._run_id,
                iter_num=self._alloc_iter_num(),
                model=contribution.model,
                reasoning_effort=contribution.reasoning_effort,
                working_directory=str(stage.git.root),
                issue_ref=contribution.ref,
                skill_exposure=self._skill_exposure,
            ) as sdk_session:
                try:
                    await sdk_session.send_and_wait(prompt, timeout=send_timeout)
                except asyncio.TimeoutError:
                    self._diag.warning(
                        "integration #%s: auto-resolution attempt %s timed out "
                        "after %ss; treating as still-red",
                        contribution.ref, attempt, send_timeout,
                    )
                except Exception as exc:
                    self._diag.warning(
                        "integration #%s: auto-resolution attempt %s raised "
                        "%s: %s; treating as still-red",
                        contribution.ref, attempt, type(exc).__name__, exc,
                    )
        except Exception as exc:
            self._diag.error(
                "integration #%s: auto-resolution session lifecycle failed: "
                "%s: %s",
                contribution.ref, type(exc).__name__, exc,
            )

    def _resolution_prompt(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        attempt: int,
        *,
        conflicted: bool,
    ) -> str:
        """The dedicated auto-resolution brief (#63).

        Unlike a Lane / serial prompt this is not issue-collection work: it
        asks the agent to get the private Integration worktree green and commit
        — driving a clean Integration the runner can then publish.

        The brief states which of the two failure states the tree is actually
        in, because they need different work and a brief that misdescribes the
        tree wastes one of only K=3 attempts: a **conflicted** stage has had its
        merge aborted and still needs the Lane branch merged, while a **red**
        stage already carries the merge and needs the feedback loops fixed.
        """
        if conflicted:
            situation = (
                f"Branch {lane_work.branch} could not be merged into this "
                "integration worktree (the conflicting merge was aborted). "
                "Merge it, resolve every conflict, "
            )
        else:
            situation = (
                f"Branch {lane_work.branch} is already merged into this "
                "integration worktree, but its feedback loops are red. Fix "
                "them, "
            )
        return (
            f"Auto-resolution attempt {attempt} of "
            f"{_AUTO_RESOLUTION_MAX_ATTEMPTS} for issue #{contribution.ref}. "
            f"{situation}make all feedback loops in AGENTS.md pass, and commit "
            f"the result. {self._prompt_text}"
        )

    def _fallback_lane_to_serial(self, lane_work: _LaneWork) -> None:
        """Terminal auto-resolution failure -> fall back to a serial Iteration (#63).

        Posts exactly one automated breadcrumb comment on the issue and
        leaves it **OPEN** — and latched into the scheduler's own
        Run-scoped ``_worked`` guard (set at :meth:`~git_loopy.rolling_scheduler.RollingScheduler.start_session`),
        so it is never re-Laned — so a later serial Iteration (granted via
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.request_serial`,
        automatically requested by :meth:`~git_loopy.rolling_scheduler.RollingScheduler.finalize`
        on this exact fallback) re-collects the issue and works it. The
        failed Lane branch is intentionally **kept** (never deleted) as a
        breadcrumb.
        """
        self._source.comment(lane_work.item.ref, _AUTO_RESOLUTION_FALLBACK_COMMENT)

    def _resolve_base_ref(self) -> str:
        """The base ref new Lane branches are cut from.

        The current branch name when on one (the natural base per ADR-0008),
        else the current commit SHA (detached HEAD), else the literal ``HEAD``
        — so ``git worktree add -b <lane> <path> <base>`` always has a valid
        start point.
        """
        try:
            branch = self._git.current_branch()
        except git_module.GitError:
            branch = None
        if branch:
            return branch
        try:
            return self._git.head_sha()
        except git_module.GitError:
            return "HEAD"


class InteractiveDriver(Protocol):
    """Strategy that runs the loop as an *observed peer* of a Textual app.

    The concrete implementation is
    :class:`git_loopy.interactive.driver.InteractiveDriver`. It is referenced
    here only as a **structural Protocol** so :mod:`git_loopy.loop` never
    imports the interactive package — and therefore never imports Textual,
    keeping the import-guard convention (ADR-0001) intact on the loop side.

    The contract is deliberately tiny:

    * :attr:`state` is the Textual-agnostic
      :class:`~git_loopy.interactive.state.LiveRunState`, registered by
      :func:`run` as the primary sink on the interactive path.
    * :meth:`attach_panes` receives the loop-owned Summary/Log pane sources
      (issue #26) before :meth:`run` builds the app.
    * :meth:`attach_detach` receives the exit-model handoff (issue #28): the
      swappable :class:`~git_loopy.sinks.SinkFanout`, the parked line-printer
      Renderer to swap in on a **Detach**, the stdout console for the **Stop**
      scrollback record, and (#325) the **Run**'s durable record, into which a
      **Dashboard fault** is written.
    * :meth:`run` is handed the loop's ``drive`` coroutine-function and is
      responsible for launching it and the Textual app as **peer asyncio
      tasks** (not parent/child), returning the loop's process exit code. A
      user **Stop** (``q`` / ``Ctrl+C``) cancels the loop task; a **Detach**
      (``d``) swaps the sink to the line printer and lets the loop run on; a
      **Dashboard fault** does the same and records why (ADR-0024).
    """

    state: EventSink

    def attach_panes(
        self,
        *,
        summary: RunSummary | None,
        log_source: Callable[[], str] | None,
    ) -> None: ...

    def attach_detach(
        self,
        *,
        sinks: SinkFanout,
        line_printer: EventSink,
        console: Console,
        record: EventEmitter | None = ...,
    ) -> None: ...

    async def run(self, drive: Callable[[], Coroutine[object, object, int]]) -> int: ...


async def run(
    config: RunConfig,
    *,
    driver: InteractiveDriver | None = None,
    rate_card: RateCard | None = None,
    staircase: "PriceStaircase | None" = None,
) -> int:
    """Drive one ``git-loopy`` invocation to completion.

    Constructs the long-lived per-run state (writers, summary, renderer,
    client, source), drives the iteration loop, and returns the
    appropriate process exit code.

    Args:
        config: The frozen :class:`RunConfig` composed by
            :func:`git_loopy.cli.main`.
        rate_card: The **Rate card** :mod:`git_loopy.cli` resolved once at
            **Run** start from the Run's shared live model listing, or ``None``
            when that listing could not be read or carries no prices (#331,
            ADR-0026). Injected rather than fetched here for ADR-0019's reason:
            a card the loop went and got for itself would be a second round
            trip on the path where the picker already made the first. ``None``
            is not a failure — nothing derives from the card, so an absent one
            only makes the rate-card **Insight capability** declare ``false``.
        staircase: The **price staircase** built from the same listing and card,
            or ``None``. Read only at Run end, by **Demotion** (#366), which
            needs to know which pair sits one rung *above* a failing one.
            Injected for the same reason the card is: the listing it derives from
            belongs to the Run, and building a second one here would order one
            listing's rungs by another listing's prices (ADR-0019).
        driver: Optional interactive driver (ADR-0001 observer model). When
            ``None`` (the default, non-interactive path) the line-printer
            :class:`~git_loopy.ui.renderer.Renderer` is the sole sink and the
            loop is driven directly — **byte-for-byte unchanged**. When
            supplied, the driver's Textual-agnostic ``state``
            (:class:`~git_loopy.interactive.state.LiveRunState`) becomes the
            sole sink and :meth:`InteractiveDriver.run` launches the loop and a
            Textual app as **peer asyncio tasks**; ``q`` / ``Ctrl+C`` (Stop)
            cancels the loop task. The Renderer is still constructed but parked
            so issue #28's Detach can swap it back in via
            :meth:`~git_loopy.sinks.SinkFanout.set_sinks`.

    Returns:
        Process exit code:

        * ``0`` — clean termination (empty AFK-ready pool or
          ``max_iterations`` cap reached).
        * ``1`` — abort (NMT strike threshold or
          preflight / setup failure).
    """
    try:
        release_version = read_runtime_release_version()
    except ReleaseVersionError as exc:
        print(f"git-loopy: Release version error: {exc}", file=sys.stderr)
        return 1

    # 1) Git seam (root-bound) + prompt file. The client resolves and binds the
    #    repository root once; ``.root`` feeds the writers / prompt / source setup.
    try:
        git = _make_git_client()
    except git_module.GitError as exc:
        print(
            f"git-loopy: failed to resolve git repository root: {exc}",
            file=sys.stderr,
        )
        return 1
    repo_root = git.root

    try:
        prompt_text = _read_prompt(repo_root, os.environ)
    except FileNotFoundError as exc:
        print(f"git-loopy: {exc}", file=sys.stderr)
        return 1

    # 2) Cost denomination — resolved once per Run and threaded as one seam
    #    (#328), so every Cost-bearing surface denominates identically. Cost is
    #    the **AI Credits** the harness reported billing (ADR-0026, #329), which
    #    needs nothing loaded and can therefore never stop a Run from starting:
    #    the price-file preflight that could abort here is deleted with the table
    #    it validated (#330).
    denomination = BilledCreditsDenomination()

    # 3) Writers + diagnostics logger + renderer + sink fan-out.
    try:
        writers = create_writers(repo_root)
    except Exception as exc:
        print(
            f"git-loopy: failed to construct writers bundle: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    summary = RunSummary(
        denomination=denomination,
        # Read off the same declaration published in the Run-start capability
        # block, so "this Orchestrator cannot report Cost" and "no billing
        # telemetry reached us" cannot drift apart from what the stream says.
        cost_reportable=bool(
            events_module.PYTHON_INSIGHT_CAPABILITIES.get("cost", False)
        ),
    )
    console = get_console()
    renderer = Renderer(
        console=console,
        summary=summary,
        verbosity=config.verbosity,
        render_reasoning=config.render_reasoning,
    )
    # The line-printer Renderer is the sole sink on the non-interactive
    # path (issue #22); JSONL logging is written separately and stays
    # always-on regardless of which sinks are registered. On the interactive
    # path (issue #23, ADR-0001) the driver's Textual-agnostic LiveRunState
    # is the primary sink. For #26's Log + Summary tabs a SECOND sink is
    # registered: a Renderer writing the same line-printer output to an
    # in-memory buffer (safe while Textual owns the real terminal). It drives
    # the shared RunSummary (Summary tab) and its buffer feeds the Log tab.
    # The stdout Renderer above stays parked (not in the sink list) so #28's
    # Detach can swap it back in via set_sinks once the TUI tears down.
    if driver is None:
        sinks = SinkFanout([renderer])
    else:
        log_buffer = io.StringIO()
        log_renderer = Renderer(
            console=Console(file=log_buffer, force_terminal=False),
            summary=summary,
            verbosity=config.verbosity,
            render_reasoning=config.render_reasoning,
        )
        sinks = SinkFanout([driver.state, log_renderer])
        driver.attach_panes(summary=summary, log_source=log_buffer.getvalue)
        # Hand the driver the exit-model seam (issue #28): the swappable sink
        # list, the parked stdout Renderer to swap in on Detach, the real
        # console for the Stop / natural-completion scrollback summary, and
        # (#325) the run's own durable record, so a **Dashboard fault** is
        # written into the same always-on replay JSONL as every other event
        # rather than being discarded unread.
        driver.attach_detach(
            sinks=sinks,
            line_printer=renderer,
            console=console,
            record=EventEmitter(
                run_id=writers.run_id,
                event_log=writers.event_log,
                sinks=sinks,
                diag=writers.diagnostics,
            ),
        )
    diag = writers.diagnostics

    # 4) IssueSource (factory dispatches on config.issue_source). A
    #    ValueError here means the config carried a value the loop
    #    doesn't recognise — surface a clean exit 1 rather than letting
    #    the exception escape.
    include_prs = _resolve_include_prs(config, repo_root)
    try:
        source = _make_issue_source(
            config, repo_root, diag, include_prs=include_prs
        )
    except ValueError as exc:
        diag.error("issue source construction failed: %s", exc)
        print(f"git-loopy: {exc}", file=sys.stderr)
        try:
            writers.run_summary.flush()
        except Exception as flush_exc:
            diag.warning("RunSummaryWriter.flush() failed: %s", flush_exc)
        return 1

    # 5) Skill catalog. git-loopy ships no Skills; it installs them from the
    #    pinned external repository and refreshes that install at the start of
    #    every Run (ADR-0025), so a Run always executes the revision this
    #    distribution stands behind rather than whatever was left on disk. An
    #    unreachable upstream is a warning, not a failure — the Run continues on
    #    the installed catalog. Only a machine with *no* catalog at all stops
    #    here, because that Run has no Skills to expose and would otherwise
    #    discover it one Iteration later.
    try:
        skill_refresh = refresh_installed_catalog()
    except SkillInstallError as exc:
        diag.error("Skill catalog install failed: %s", exc)
        print(f"git-loopy: {exc}", file=sys.stderr)
        try:
            writers.run_summary.flush()
        except Exception as flush_exc:
            diag.warning("RunSummaryWriter.flush() failed: %s", flush_exc)
        return exit_code_for("preflight_failed")
    if skill_refresh.warning:
        diag.warning("Skill catalog refresh: %s", skill_refresh.warning)
        print(f"git-loopy: {skill_refresh.warning}", file=sys.stderr)
    if skill_refresh.changed:
        diag.info("%s", describe_refresh(skill_refresh))
        print(f"git-loopy: {describe_refresh(skill_refresh)}", file=sys.stderr)

    # 6) SDK client (lazy via the factory the tests monkeypatch). If
    #    construction itself raises (SDK install broken, port already
    #    held by another process, etc.) we must surface a clean error
    #    rather than letting the traceback escape ``asyncio.run``.
    client: CopilotClient | None = None
    try:
        client = _make_client()
    except Exception as exc:
        diag.error(
            "CopilotClient construction failed: %s: %s",
            type(exc).__name__, exc,
        )
        print(
            f"git-loopy: failed to construct CopilotClient: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        # Best-effort: still flush the writers so the operator gets at
        # least an empty run-summary JSON pointing at the failure.
        try:
            writers.run_summary.flush()
        except Exception as flush_exc:
            diag.warning("RunSummaryWriter.flush() failed: %s", flush_exc)
        return 1

    skill_workspace = TemporaryDirectory(prefix="git-loopy-run-skills-")
    try:
        await client.start()
        skill_preflight = await resolve_run_skill_preflight(
            client,
            config=config,
            git=git,
            prompt_text=prompt_text,
            repo_root=repo_root,
            installed_skills_dir=_installed_skills_path(),
            workspace=Path(skill_workspace.name),
            discoverer=_discover_skill_catalog,
        )
    except (
        OSError,
        PromptMetadataError,
        SkillExposureError,
        SkillPolicyResolutionError,
    ) as exc:
        diag.error("Skill policy preflight failed: %s: %s", type(exc).__name__, exc)
        print(
            f"git-loopy: Skill policy preflight failed: {exc}. "
            "Inspect the catalog and configured policy with `git-loopy skills`.",
            file=sys.stderr,
        )
        try:
            writers.run_summary.flush()
        except Exception as flush_exc:
            diag.warning("RunSummaryWriter.flush() failed: %s", flush_exc)
        try:
            await client.stop()
        except Exception as stop_exc:
            diag.warning("CopilotClient.stop() failed: %s", stop_exc)
        skill_workspace.cleanup()
        # Skill preflight is preflight: it answers to the Wrapper contract's
        # `preflight_failed` reason rather than to a literal that merely
        # happens to equal it today.
        return exit_code_for("preflight_failed")
    except RuntimeError as exc:
        diag.error(
            "CopilotClient start or Skill catalog preflight failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        print(
            "git-loopy: failed to start Copilot or discover the Skill catalog: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        try:
            writers.run_summary.flush()
        except Exception as flush_exc:
            diag.warning("RunSummaryWriter.flush() failed: %s", flush_exc)
        try:
            await client.stop()
        except Exception as stop_exc:
            diag.warning("CopilotClient.stop() failed: %s", stop_exc)
        skill_workspace.cleanup()
        # Skill preflight is preflight: it answers to the Wrapper contract's
        # `preflight_failed` reason rather than to a literal that merely
        # happens to equal it today.
        return exit_code_for("preflight_failed")

    # Dispatch: Parallel mode (opt-in, config.parallel > 1) drives the
    # Rolling-dispatch orchestrator (#219, ADR-0020) with the injected
    # runner-side Integration gate (#60); serial (the default, parallel == 1)
    # drives the existing loop byte-for-byte unchanged. Both expose the same
    # ``drive()`` contract.
    #
    # The **Task-type classifier**'s pair (#409, ADR-0029) is resolved once here,
    # from the same staircase **Demotion** steps, and handed to whichever
    # orchestrator is built: an issue's Task type is a fact about the issue, so
    # the two modes must not be able to infer it on different models. Absent —
    # no roster, no **Rate card**, no configured pair — leaves the classifier
    # inert and every issue exactly where it was before this slice.
    classifier_pair = resolve_pickup_classifier_pair(
        config, staircase, warn=lambda message: diag.warning("%s", message)
    )
    task_type_client = _make_task_type_label_client()
    loop: _Loop | _ParallelLoop
    if config.parallel > 1:
        loop = _ParallelLoop(
            config=config,
            release_version=release_version,
            git=git,
            prompt_text=prompt_text,
            denomination=denomination,
            writers=writers,
            sinks=sinks,
            summary=summary,
            client=client,
            skill_preflight=skill_preflight,
            source=source,
            diag=diag,
            gate_runner=_make_gate_runner(),
            worktree_setup=_make_worktree_setup(),
            include_prs=include_prs,
            rate_card=rate_card,
            classifier_pair=classifier_pair,
            task_type_client=task_type_client,
        )
    else:
        loop = _Loop(
            config=config,
            release_version=release_version,
            git=git,
            prompt_text=prompt_text,
            denomination=denomination,
            writers=writers,
            sinks=sinks,
            summary=summary,
            client=client,
            skill_preflight=skill_preflight,
            source=source,
            diag=diag,
            include_prs=include_prs,
            rate_card=rate_card,
            classifier_pair=classifier_pair,
            task_type_client=task_type_client,
        )

    exit_code = 1
    try:
        try:
            with writers.event_log, writers.run_summary:
                # Root OTel span for the entire iteration loop. The
                # SDK's subprocess telemetry (configured via
                # _build_telemetry_config) nests under this span's
                # W3C trace context — see git_loopy.telemetry.otel
                # module docstring for the propagation contract.
                with telemetry.span("git_loopy.run"):
                    try:
                        if driver is None:
                            exit_code = await loop.drive()
                        else:
                            # ADR-0001: the app and the loop run as peer
                            # asyncio tasks; the driver owns the peering and
                            # Stop-cancels the loop task.
                            exit_code = await driver.run(loop.drive)
                    except Exception as exc:
                        diag.error(
                            "git-loopy loop crashed: %s: %s",
                            type(exc).__name__, exc,
                        )
                        exit_code = 1
        except Exception as exc:
            # Writer __exit__ raised (disk full, perm denied flushing
            # the run-summary JSON, etc.). The body already ran; we
            # just couldn't persist. Don't let this turn into a
            # tracebacked exit.
            diag.error(
                "writers __exit__ failed: %s: %s",
                type(exc).__name__, exc,
            )
            print(
                f"git-loopy: writers __exit__ failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            exit_code = 1
    finally:
        # **Demotion** (#366, ADR-0030), at the one genuinely quiescent point a
        # Run has: `drive()` has returned, every Lane has finalized, the run
        # summary is flushed, and nothing is in flight to race over the single
        # tracked artifact. In a `finally` so a crashed or Stop-cancelled Run
        # still records what its completed contributions established — the rows
        # are already final, and discarding them would make a Run that ended
        # badly the one Run whose experience is thrown away.
        _demote_after_run(config, git, loop, staircase, diag)
        # Always release the SDK subprocess, even on a body-level crash.
        if client is not None:
            try:
                await client.stop()
            except Exception as exc:
                diag.warning("CopilotClient.stop() failed: %s", exc)
        skill_workspace.cleanup()
        # Drain OTel exporters AFTER the root `git_loopy.run` span has
        # closed. BatchSpanProcessor buffers — without an explicit
        # flush, spans queued near the end of the run could be dropped
        # on process exit. No-op when OTel is disabled.
        try:
            telemetry.force_flush()
        except Exception as exc:  # pragma: no cover - defensive
            diag.warning("telemetry.force_flush() failed: %s", exc)

    return exit_code


def _demote_after_run(
    config: RunConfig,
    git: git_module.Git,
    loop: "_Loop | _ParallelLoop",
    staircase: "PriceStaircase | None",
    diag: logging.Logger,
) -> None:
    """Hand this Run's **Lane contributions** to **Demotion** (#366, ADR-0030).

    A thin adapter and nothing more: the rule, the threshold, the write and the
    commit all live in :mod:`git_loopy.demotion`, which imports no **Trial**, no
    search and no dispatcher — so *"Demotion notifies and starts no search"*
    holds by where the code sits rather than by anything this function
    remembers to do.

    Every failure is swallowed and logged. Rewriting a routing table is never a
    precondition for the work a Run has already finished, and this runs after the
    exit code has been decided, so nothing here can change it.
    """
    contributions = loop.finalized_contributions
    if not contributions:
        return
    try:
        from git_loopy import demotion as demotion_module

        demotion_module.demote_after_run(
            repo_root=git.root,
            config=config,
            staircase=(
                staircase
                if staircase is not None
                else PriceStaircase(refusal=StaircaseRefusal.NO_RATE_CARD)
            ),
            contributions=contributions,
            git=git,
            warn=lambda message: print(
                f"git-loopy: warning: {message}", file=sys.stderr
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        diag.warning(
            "Demotion could not run (%s: %s); the Run is unaffected.",
            type(exc).__name__,
            exc,
        )
