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
    Checkpoint is excluded from both the Summary commit tally and the Strike
    machine. Non-fatal: a failure warns and the loop carries on.
11. **Auto-push** (ADR-0004) via :meth:`_maybe_push`: whenever the iteration
    produced new commits — agent commits (step 8) and/or the Checkpoint from
    step 10 — the current branch is pushed to its upstream
    (``wrapper.push.recorded`` on success) so the work reaches the remote.
    Non-fatal: a missing remote/upstream, an auth failure, or a
    non-fast-forward warns and the loop carries on, so a local-only repo
    completes normally.
12. NMT strike accounting: progress (``commits>0`` or ``auto_closures>0``)
    resets strikes; no-progress increments, possibly tripping the
    abort threshold. Checkpoints and pushes are *not* progress.
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
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
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
    Sequence,
)

from copilot import CopilotClient
from rich.console import Console

from git_loopy import events as events_module
from git_loopy import gate as gate_module
from git_loopy import gh as gh_module
from git_loopy import git as git_module
from git_loopy import rolling_pressure
from git_loopy import rolling_scheduler
from git_loopy import worktree as worktree_module
from git_loopy.active_issue import ActiveIssueBinding
from git_loopy.config import RunConfig, resolve_iteration_model
from git_loopy.continuation import (
    CapabilityUnsupported as ContinuationCapabilityUnsupported,
)
from git_loopy import continuation_frontier
from git_loopy.continuation_report import ContinuationReporter
from git_loopy.copilot_client import make_copilot_client
from git_loopy.emit import EventEmitter
from git_loopy.persist import (
    IterationCounters,
    WritersBundle,
    create_writers,
)
from git_loopy.pricing import Pricing, PricingError, load_pricing
from git_loopy.prompt import PromptMetadataError, load_prompt
from git_loopy.release_version import ReleaseVersionError, read_runtime_release_version
from git_loopy.skill_install import (
    SkillInstallError,
    describe_refresh,
    installed_catalog_dir,
    refresh_installed_catalog,
)
from git_loopy.rolling_pool import RollingPool
from git_loopy.rollup import IterationRollupAccumulator
from git_loopy.session import IterationSession
from git_loopy.sinks import EventSink, SinkFanout
from git_loopy.sources import (
    AfkReadyItem,
    GitHubIssueSource,
    IssueSource,
    LABEL_PARALLEL_SAFE,
    PoolCollection,
    PrdsIssueSource,
    RollingIssueSource,
)
from git_loopy.skill_catalog import discover_skill_catalog as _discover_skill_catalog
from git_loopy.skill_exposure import SkillExposure, SkillExposureError
from git_loopy.skill_policy import SkillPolicyResolutionError
from git_loopy.skill_run_preflight import (
    RunSkillPreflight,
    resolve_run_skill_preflight,
)
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


def _resolve_continuation_authority(
    config: RunConfig, diag: logging.Logger
) -> dict[str, Any] | None:
    """Resolve this Run's §10 Continuation authority, or `None` for mode `off`.

    `None` is mode `off`: an unconfigured Run never resolves an authority and
    never touches the tracker on Continuation's behalf, so "preserve current Pool
    behavior, output, retries, Strikes, and exits without invoking Continuation"
    is a property of the wiring rather than a promise made by code that ran
    anyway. A configured mode that §10 then narrows away is also `None`, said out
    loud, because a Run that went quiet for a reason nobody printed looks exactly
    like one that is working.
    """
    sources = config.continuation.declared_sources()
    if not sources:
        return None

    from git_loopy import continuation as continuation_module

    authority = continuation_module.resolve_authority({"sources": sources})
    if not authority["participates"]:
        diag.info(
            "continuation authority resolved to off (declared %s): %s",
            authority["declared_mode"],
            ", ".join(
                f"{entry['axis']}/{entry['reason']}" for entry in authority["narrowed"]
            )
            or "no narrowing recorded",
        )
        return None
    return authority


def _make_continuation_reporter(
    config: RunConfig, diag: logging.Logger
) -> ContinuationReporter | None:
    """Build this Run's read-only Continuation observer, or `None`.

    `None` covers both mode `off` and mode `execute-frontier`: an executing Run
    reconciles on its own schedule around each Dispatch, and a reporter observing
    the same project on the Iteration boundary would reconcile the same records
    twice and print guidance the Run had already acted on.

    Raises :class:`~git_loopy.continuation.CapabilityUnsupported` when the
    configured mode is one this distribution does not advertise. The caller turns
    that into the Wrapper contract's `preflight_failed`.
    """
    authority = _resolve_continuation_authority(config, diag)
    if authority is None or authority["mode"] == continuation_frontier.MODE:
        return None

    from git_loopy import continuation as continuation_module

    client = continuation_module.make_github_client()
    return ContinuationReporter(
        authority,
        reconcile=lambda request: continuation_module.reconcile_records(
            request, client
        ),
        on_guidance=lambda line: print(line, file=sys.stderr),
    )


def _runner_satisfied_requirements(
    authority: Mapping[str, Any], exposure: SkillExposure
) -> tuple[tuple[str, str], ...]:
    """State, in §9's typed vocabulary, what this Run actually holds.

    Both halves are derived rather than asserted, because §9 reads the posture as
    a closed world: a claim made here that the host cannot honour turns into a
    session that fails at the Instruction instead of an Action §9 declines to
    authorize.

    - `skill/<name>` comes from the Skill policy this Run resolved. A Skill that
      was disabled or absent is one the session genuinely cannot invoke.
    - `access/<kind>` comes from the operator's own `effect_scopes` ceiling. §10
      is where an operator says which durable effects this Run may produce, so
      the access claim is theirs to grant and never widens past it.
    """
    ceilings = authority.get("ceilings") or {}
    return tuple(
        sorted(
            {("skill", name) for name in exposure.policy.enabled}
            | {("access", kind) for kind in (ceilings.get("effect_scopes") or ())}
        )
    )


def _make_continuation_plan(
    config: RunConfig, diag: logging.Logger, exposure: SkillExposure
) -> continuation_frontier.FrontierPlan | None:
    """Build this Run's execute-frontier posture, or `None` for every other mode.

    Preflight, deliberately: :func:`~git_loopy.continuation_frontier.plan_frontier`
    raises when the resolved authority names a ceiling this distribution cannot
    enforce or omits the actor an execute-frontier Run has to write Dispatch
    evidence as. A Run that discovered that at the moment it had to record a
    safety-case violation would lose the one record a human needs.
    """
    authority = _resolve_continuation_authority(config, diag)
    if authority is None or authority["mode"] != continuation_frontier.MODE:
        return None
    if config.parallel > 1:
        # `concurrent_dispatch` is advertised false. A Run that accepted the
        # Parallel flag and then dispatched serially anyway would have quietly
        # served a narrower thing than the operator asked for.
        raise ContinuationCapabilityUnsupported(
            "continuation mode execute-frontier does not support parallel dispatch"
        )
    return continuation_frontier.plan_frontier(
        authority,
        satisfied_requirements=_runner_satisfied_requirements(authority, exposure),
    )


def _dispatch_refs(dispatch: continuation_frontier.Dispatch) -> tuple[int | str, ...]:
    """The issue refs one Dispatch may bind as its Active issue.

    Only its own Target and workstream anchor. A session that could bind
    something else would be reporting work against an Action nobody authorized.
    """
    refs: list[int | str] = []
    for ref in (dispatch.target, dispatch.workstream_anchor):
        number = ref.get("number") if isinstance(ref, Mapping) else None
        if isinstance(number, int) and number not in refs:
            refs.append(number)
    return tuple(refs)


def _dispatch_prompt(dispatch: continuation_frontier.Dispatch) -> str:
    """Render the one Instruction this session runs, and nothing beyond it.

    The published Instruction is the whole task --- it is what a Producer wrote
    down as the exact thing to do --- so the preamble adds only the two facts the
    session cannot infer: when it is done, and that it must stop there. Naming the
    successor would hand a noninteractive session the chaining decision §9 keeps.
    """
    condition = json.dumps(dict(dispatch.completion_condition), sort_keys=True)
    return (
        "You are running one authorized Continuation Action, noninteractively.\n"
        f"Action: {dispatch.summary}\n"
        f"Completion condition: {condition}\n"
        "Run this Instruction and stop. Do not start follow-up work, do not ask "
        "questions, and do not wait for approval: nobody is watching this "
        "session. If the Instruction cannot be completed without a human "
        "decision, stop and say so.\n\n"
        f"{dispatch.instruction.get('value', '')}"
    )


def _frontier_exit_code(
    runs: Sequence[continuation_frontier.FrontierRun], *, diag: logging.Logger
) -> int:
    """Map every repository's typed stop onto the Wrapper contract's exit vocabulary.

    The contract has five exit reasons and Continuation adds none, so the mapping
    is by *disposition* rather than by reason: a boundary the Run was always going
    to reach is a clean stop, and anything a human has to look at is not.

    - `complete` and `expected-boundary` --- the Run may do no more, which is what
      `empty_pool` already means for a Pool Run.
    - `attention-required` --- a safety-case violation, an uncertain effect state
      or a guidance fault. Exit non-zero, like `stuck`.
    - an ordinary execution failure --- no §9 stop at all, and still not success.
    """
    attention = False
    for run in runs:
        if run.execution_failed:
            attention = True
            diag.error(
                "continuation frontier run for %s ended on an execution failure",
                run.repository,
            )
            continue
        stop = run.stop or {}
        if stop.get("disposition") == "attention-required":
            attention = True
        diag.info(
            "continuation frontier run for %s stopped: %s (%d dispatched)",
            run.repository,
            stop.get("reason", "unreported"),
            len(run.dispatches),
        )
    return exit_code_for("stuck" if attention else "empty_pool")


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
    """
    return gate_module.AgentsMdGateRunner()


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
            diag, gh=_make_github_client(), include_prs=include_prs
        )
    if config.issue_source == "prds":
        return PrdsIssueSource(repo_root, diag)
    raise ValueError(
        f"unknown issue_source {config.issue_source!r}; expected "
        f"'github' or 'prds'"
    )


# Matches the PR-surface flag ``/setup-agent-skills`` writes into
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
       exact flag ``/setup-agent-skills`` writes and ``/triage`` reads).
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

    Mirrors :func:`git_loopy.pricing._packaged_path`: the wheel ships a default
    prompt as package data (alongside ``pricing.toml``) so a bare run in a repo
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
        pricing: Pricing,
        writers: WritersBundle,
        sinks: SinkFanout,
        summary: RunSummary,
        client: CopilotClient,
        skill_preflight: RunSkillPreflight,
        source: IssueSource,
        diag: logging.Logger,
        include_prs: bool = False,
        usage_observer: _EventObserver | None = None,
        continuation: ContinuationReporter | None = None,
        frontier_plan: continuation_frontier.FrontierPlan | None = None,
    ) -> None:
        self._config = config
        self._release_version = release_version
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
        # Report mode's read-only observer, or `None` in mode `off` — which is
        # the default, and the reason an unconfigured Run cannot reach any of
        # this code at all rather than reaching a Continuation that declines.
        self._continuation = continuation
        # Execute-frontier's frozen posture, or `None` in every other mode. When
        # it is set the Run drives the frontier instead of the Pool; the two are
        # never both live, because §10 resolves exactly one mode.
        self._frontier_plan = frontier_plan
        self._rollup = IterationRollupAccumulator(pricing=pricing)
        if self._continuation is not None:
            self._continuation.bind_emit(self._emit)
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

    def _observe_continuation(self, iter_num: int, *, phase: str) -> None:
        """Render read-only Continuation guidance for one Iteration boundary.

        Guarded rather than trusted: an operator who adopted report mode agreed
        to *see* guidance, not to a new way for a Run to fail. A Reconciliation
        that cannot complete is a warning and nothing else.
        """
        if self._continuation is None:
            return
        try:
            self._continuation.observe(iter_num=iter_num, phase=phase)
        except Exception as exc:  # noqa: BLE001 - visibility is never fatal
            self._diag.warning(
                "continuation %s reconciliation failed: %s: %s",
                phase,
                type(exc).__name__,
                exc,
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
            # Report mode observes here and again after the Iteration's durable
            # changes. The Pool above is untouched by what it finds: report mode
            # grants visibility, never selection.
            self._observe_continuation(iter_num, phase="pre-iteration")
            if not pool:
                # Close the iteration cleanly so the snapshot lifecycle is
                # consistent even on the empty-pool path.
                self._finish_iteration(iter_num, outcome="empty_pool")
                return ("empty_pool", 0, 0)
            issue_binding = self._new_active_issue_binding(
                iter_num, allowed_refs=(item.ref for item in pool)
            )

            # 3) Build prompt (last-5 commits + AFK-ready item blocks + prompt body).
            try:
                recent = self._git.recent_commits(5)
            except git_module.GitError as exc:
                self._diag.warning("recent_commits failed: %s; using empty prefix", exc)
                recent = []
            commits_block = _format_recent_commits(recent)
            issues_block = "\n\n".join(item.rendered_block for item in pool)
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

            # 5) Run the SDK session.
            send_timeout = self._config.send_timeout_seconds
            with telemetry.span("git_loopy.session"):
                try:
                    async with IterationSession(
                        self._client,
                        config=self._config,
                        event_log=self._writers.event_log,
                        sinks=self._sinks,
                        run_id=self._writers.run_id,
                        iter_num=iter_num,
                        model=self._config.model,
                        reasoning_effort=self._config.reasoning_effort,
                        issue_binding=issue_binding,
                        skill_exposure=self._skill_exposure,
                        event_observer=self._session_observer,
                    ) as sdk_session:
                        try:
                            await sdk_session.send_and_wait(
                                prompt, timeout=send_timeout
                            )
                        except asyncio.TimeoutError:
                            self._diag.warning(
                                "SDK send_and_wait timed out after %ss; "
                                "treating iteration as no-progress",
                                send_timeout,
                            )
                        except Exception as exc:
                            # Treat any copilot failure as no-progress;
                            # bookkeeping below still runs.
                            self._diag.warning(
                                "SDK send_and_wait raised %s: %s; "
                                "treating iteration as no-progress",
                                type(exc).__name__, exc,
                            )
                except Exception as exc:
                    self._diag.error(
                        "IterationSession lifecycle failed: %s: %s; iteration aborted",
                        type(exc).__name__, exc,
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
            self._observe_continuation(iter_num, phase="post-iteration")

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
            outcome = self._strike_machine.tick(
                commits_in_iter=commits_in_iter,
                auto_closures_in_iter=auto_closures,
                checkpoints_in_iter=checkpoints_in_iter,
                pr_advances_in_iter=pr_advances,
            )
            if outcome == "aborted" or not made_progress:
                # Either we just hit the strike threshold OR this iteration
                # had no progress (a single strike). Either way emit the
                # wrapper.strike event so the renderer + persist see it.
                self._emit(
                    events_module.WRAPPER_STRIKE,
                    iter_num=iter_num,
                    strikes=self._strike_machine.strikes,
                    max_strikes=self._config.max_nmt_strikes,
                    outcome=("abort" if outcome == "aborted" else "warn"),
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

    def _infer_active_binding(
        self,
        pool: list[AfkReadyItem],
        completions: list[Any],
        new_commits: list[git_module.Commit],
    ) -> tuple[int | str, str] | None:
        """Select the strongest post-session Active-issue fallback."""
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

    # -- fixed-frontier Run ------------------------------------------------

    async def _perform_dispatch(
        self, dispatch: continuation_frontier.Dispatch, *, iter_num: int
    ) -> continuation_frontier.DispatchOutcome:
        """Run exactly one authorized Action in exactly one Performer session.

        The session is handed one Instruction and one completion condition and is
        never told what comes next, because what comes next is a decision §9 makes
        after observing what this Dispatch did. There is no loop here for the same
        reason: the successor is chosen by the next Reconciliation, not by this
        session and not by this method.
        """
        binding = self._new_active_issue_binding(
            iter_num, allowed_refs=_dispatch_refs(dispatch)
        )
        prompt = _dispatch_prompt(dispatch)
        send_timeout = self._config.send_timeout_seconds
        failure: str | None = None
        with telemetry.span("git_loopy.session"):
            try:
                async with IterationSession(
                    self._client,
                    config=self._config,
                    event_log=self._writers.event_log,
                    sinks=self._sinks,
                    run_id=self._writers.run_id,
                    iter_num=iter_num,
                    model=self._config.model,
                    reasoning_effort=self._config.reasoning_effort,
                    issue_binding=binding,
                    skill_exposure=self._skill_exposure,
                    event_observer=self._session_observer,
                ) as sdk_session:
                    try:
                        await sdk_session.send_and_wait(prompt, timeout=send_timeout)
                    except asyncio.TimeoutError:
                        failure = f"session timed out after {send_timeout}s"
                    except Exception as exc:  # noqa: BLE001 - reported as failure
                        failure = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # noqa: BLE001 - reported as failure
                failure = f"session lifecycle failed: {type(exc).__name__}: {exc}"
        if failure is not None:
            # An ordinary Runner failure, not a Continuation boundary: it never
            # becomes durable Dispatch evidence and never earns a typed §9 stop.
            self._diag.warning(
                "continuation dispatch %s failed: %s", dispatch.action_identity, failure
            )
            return continuation_frontier.DispatchOutcome(outcome="failed")
        return continuation_frontier.DispatchOutcome(outcome="complete")

    async def _drive_frontier(
        self,
        plan: continuation_frontier.FrontierPlan,
        *,
        client: Any | None = None,
    ) -> int:
        """Drive the frozen frontier instead of the Pool, then map its stop to an exit.

        The driver is synchronous and the Performer is a coroutine, so the Run is
        handed to a worker thread and each session is scheduled back onto this
        event loop. That keeps the sequencing rules --- one freeze, one Action per
        session, one Reconciliation after every Dispatch --- in one synchronous
        place that can be pinned without an event loop.
        """
        from git_loopy import continuation as continuation_module

        loop = asyncio.get_running_loop()
        dispatches = 0
        if client is None:
            client = continuation_module.make_github_client()

        def perform(
            dispatch: continuation_frontier.Dispatch,
        ) -> continuation_frontier.DispatchOutcome:
            nonlocal dispatches
            dispatches += 1
            return asyncio.run_coroutine_threadsafe(
                self._perform_dispatch(dispatch, iter_num=dispatches), loop
            ).result()

        def record(body: dict[str, Any]) -> Any:
            # The record is written onto the Producer carrier that published the
            # Action, so the carrier --- not the Run --- names the repository it
            # lands in. §9 validates that the two agree.
            return continuation_module.record_dispatch_result(
                {
                    "repository": body["carrier"]["repository"],
                    "trusted_producers": list(plan.trusted_producers),
                    "dispatch": body,
                },
                client,
            )

        driver = continuation_frontier.FrontierDriver(
            plan,
            reconcile=lambda request: continuation_module.reconcile_records(
                request, client
            ),
            perform=perform,
            record_evidence=record,
            emit=self._emit,
            on_guidance=lambda line: print(line, file=sys.stderr),
            diagnose=lambda line: self._diag.warning("%s", line),
        )
        runs = await asyncio.to_thread(driver.run_all)
        return _frontier_exit_code(runs, diag=self._diag)

    async def _drive_frontier_run(
        self, plan: continuation_frontier.FrontierPlan
    ) -> int:
        """Wrap one fixed-frontier Run in the same Run envelope the Pool emits.

        `iterations_run` is 0 rather than the Dispatch count: an Iteration is a
        Pool concept with its own rollup, Strike accounting and cap, and a
        Dispatch is none of those. The Dispatches are reported by their own three
        Events instead.
        """
        self._emit(
            events_module.WRAPPER_RUN_START,
            iter_num=None,
            issue_source=self._config.issue_source,
            release_version=self._release_version,
            schema_version=events_module.EVENT_SCHEMA_VERSION,
            insight_capabilities=dict(events_module.PYTHON_INSIGHT_CAPABILITIES),
            parallel_capabilities=dict(events_module.PYTHON_PARALLEL_CAPABILITIES),
            max_iterations=self._config.max_iterations,
            max_nmt_strikes=self._config.max_nmt_strikes,
        )
        outcome_label = "empty_pool"
        exit_code = 1
        try:
            exit_code = await self._drive_frontier(plan)
            outcome_label = (
                "empty_pool"
                if exit_code == exit_code_for("empty_pool")
                else "stuck"
            )
        except Exception as exc:
            outcome_label = "crashed"
            exit_code = 1
            self._diag.error(
                "git-loopy continuation frontier run crashed: %s: %s",
                type(exc).__name__,
                exc,
            )
            raise
        finally:
            try:
                self._emit(
                    events_module.WRAPPER_RUN_END,
                    iter_num=None,
                    outcome=outcome_label,
                    iterations_run=0,
                )
            except Exception as exc:  # pragma: no cover - defensive
                self._diag.warning("wrapper.run.end emit failed: %s", exc)
        return exit_code

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

        if self._frontier_plan is not None:
            return await self._drive_frontier_run(self._frontier_plan)

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
            insight_capabilities=dict(events_module.PYTHON_INSIGHT_CAPABILITIES),
            parallel_capabilities=dict(events_module.PYTHON_PARALLEL_CAPABILITIES),
            max_iterations=self._config.max_iterations,
            max_nmt_strikes=self._config.max_nmt_strikes,
        )

        exit_code = exit_code_for("iteration_cap")
        outcome_label = "iteration_cap"
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
                        iter_num
                        if outcome_label != "iteration_cap"
                        else iter_num - 1
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
        pricing: Pricing,
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
        continuation: ContinuationReporter | None = None,
    ) -> None:
        self._config = config
        self._release_version = release_version
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
        self._cost_meter = rolling_pressure.RunCostMeter(pricing=pricing)
        self._pressure = _make_pressure_monitor(
            lane_cap=config.parallel,
            diag=diag,
            credit_spent=self._cost_meter,
            rate_limits=rolling_pressure.rate_limit_reader(source),
        )
        if isinstance(source, RollingIssueSource):
            self._pool = RollingPool(diag=diag, source=source, clock=time.monotonic)
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
            pricing=pricing,
            writers=writers,
            sinks=sinks,
            summary=summary,
            client=client,
            skill_preflight=skill_preflight,
            source=source,
            diag=diag,
            include_prs=include_prs,
            usage_observer=self._cost_meter,
            continuation=continuation,
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
            insight_capabilities=dict(events_module.PYTHON_INSIGHT_CAPABILITIES),
            parallel_capabilities=dict(events_module.PYTHON_PARALLEL_CAPABILITIES),
            max_iterations=self._config.max_iterations,
            max_nmt_strikes=self._config.max_nmt_strikes,
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

        outcome_label = "iteration_cap"
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
        the moment it finds anything else.

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
        for item in collection.items:
            if isinstance(item.ref, int) and LABEL_PARALLEL_SAFE in item.labels:
                continue
            self._scheduler.request_serial(ref=item.ref, reason="not_parallel_safe")
            break
        return collection.complete

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
        if not isinstance(ref, int):
            # Rolling-eligible candidates are always int refs
            # (`is_parallel_safe` requires it); this only defends a future
            # non-int ref from ever reaching a worktree/branch-name helper
            # that assumes one.
            scheduler.release(reservation)
            return

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
            scheduler.release(reservation)
            return

        lane_work = _LaneWork(item=item, branch=branch, path=path, git=wt_git)
        try:
            lane_work.pre_sha = wt_git.head_sha()
        except git_module.GitError as exc:
            self._diag.warning("lane #%s pre head_sha failed: %s", ref, exc)

        # Resolve this Lane's (model, effort) ONCE at Active-issue pickup —
        # the structural per-Lane seam per-issue routing hangs off (#148). An
        # unknown / conflicting label warns on the existing per-issue
        # diagnostics channel; an unlabelled Lane (or routing-off) yields the
        # gated global default. `start_session` binds the pair onto the
        # Contribution, reused for this Lane's work AND its later
        # auto-resolution sessions.
        model, reasoning_effort = resolve_iteration_model(
            self._config,
            item.labels,
            warn=lambda message, _ref=ref: self._diag.warning(
                "lane #%s routing: %s", _ref, message
            ),
        )

        # Prepare the freshly created worktree before its agent session
        # starts (#65). Non-fatal: a broken environment still lets the agent
        # try, and one Lane's setup can never take down another's task.
        self._setup_lane_worktree(lane_work)

        contribution = scheduler.start_session(
            reservation, model=model, reasoning_effort=reasoning_effort
        )
        self._lane_work[contribution.contribution_id] = lane_work

        lane_binding = self._serial._new_active_issue_binding(
            None, allowed_refs=(ref,), lane_issue=ref
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

        await self._run_lane_session(contribution, lane_work, commits_block)

        changed, checkpoint_ok = self._account_lane(contribution, lane_work)

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
            self._apply_strike_reaction(contribution)
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

    async def _run_lane_session(
        self,
        contribution: rolling_scheduler.Contribution,
        lane_work: _LaneWork,
        commits_block: str,
    ) -> None:
        """Run one Lane contribution's SDK session, pinned to its worktree.

        Concurrent by construction with every other Lane's session and any
        in-flight Integration (#219, ADR-0020) — bulletproof like the retired
        Wave's ``_run_lane_session``: a timeout, a send failure, or a
        session-lifecycle error is logged and swallowed so one Lane can never
        abort another's task or the driver loop; the caller then accounts and
        finishes the contribution as no-progress.
        """
        prompt = (
            f"Previous commits: {commits_block} "
            f"Issues: {lane_work.item.rendered_block} {self._prompt_text}"
        )
        send_timeout = self._config.send_timeout_seconds
        try:
            # Deliberately no `event_observer=self._rollup`: unlike the serial
            # `_Loop` (which feeds every `IterationSession` into the single
            # `IterationRollupAccumulator` "current iteration" slot), Lane
            # sessions run concurrently and would corrupt that single slot.
            # Known, documented gap (#219/#306): Lane-derived token/cost usage
            # never reaches the Run summary's per-iteration rollup or a
            # `wrapper.iteration.end` event -- Lane contributions never emit
            # `wrapper.iteration.start`/`.end` at all. Commits, auto-closures,
            # and Strike accounting for Lane work are still fully tracked
            # (`wrapper.commit.recorded`, `wrapper.auto_close`,
            # `wrapper.strike` via `_apply_strike_reaction`) -- only the
            # summary-table cost/token rollup is not.
            #
            # The Run-scoped `_cost_meter` IS safe here and does go on (#309):
            # it holds no per-Iteration slot to corrupt, only a running total,
            # which is exactly the quantity AI-credit pressure is judged
            # against. Without it a Parallel Run could never price itself and
            # credit pressure would stay permanently unknown.
            async with IterationSession(
                self._client,
                config=self._config,
                event_log=self._writers.event_log,
                sinks=self._sinks,
                run_id=self._run_id,
                iter_num=self._alloc_iter_num(),
                model=contribution.model,
                reasoning_effort=contribution.reasoning_effort,
                working_directory=str(lane_work.git.root),
                issue_ref=lane_work.item.ref,
                skill_exposure=self._skill_exposure,
                event_observer=self._cost_meter,
            ) as sdk_session:
                try:
                    await sdk_session.send_and_wait(
                        prompt, timeout=send_timeout
                    )
                except asyncio.TimeoutError:
                    self._diag.warning(
                        "lane #%s send_and_wait timed out after %ss; "
                        "treating as no-progress",
                        lane_work.item.ref, send_timeout,
                    )
                except Exception as exc:
                    self._diag.warning(
                        "lane #%s send_and_wait raised %s: %s; "
                        "treating as no-progress",
                        lane_work.item.ref, type(exc).__name__, exc,
                    )
        except Exception as exc:
            self._diag.error(
                "lane #%s IterationSession lifecycle failed: %s: %s",
                lane_work.item.ref, type(exc).__name__, exc,
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
            self._apply_strike_reaction(contribution)
        # §4.4: this finalize freed an **Integration backlog** slot, lifting
        # backpressure, and each contribution it admitted from the parked FIFO
        # released the Lane that contribution had been retaining (§4.3).
        self._capacity_freed.set()
        for admitted in newly_admitted:
            await self._integrate_contribution(admitted)

    def _apply_strike_reaction(
        self, contribution: rolling_scheduler.Contribution
    ) -> None:
        """Tick the shared Strike machine once per finalized contribution.

        #219 §7.4, §7.6: the scheduler records ``STRIKE_RESET`` /
        ``STRIKE_ADD`` on the finalized row rather than ticking the machine
        itself — it belongs to the composed serial ``self._serial`` because a
        serial Iteration ticks the identical one. This replaces the retired
        Wave's once-per-round ``_tick_round``: under Rolling dispatch there
        is no round, so the reaction is applied the instant EACH contribution
        finalizes, whether that is a ``TERMINAL`` disposition straight out of
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.finish_work` or a
        post-Integration
        :meth:`~git_loopy.rolling_scheduler.RollingScheduler.finalize`.
        """
        assert self._scheduler is not None
        reset = contribution.strike_reaction == rolling_scheduler.STRIKE_RESET
        outcome = self._serial._strike_machine.tick(
            commits_in_iter=1 if reset else 0,
            auto_closures_in_iter=0,
        )
        if outcome == "aborted" or not reset:
            self._serial._emit(
                events_module.WRAPPER_STRIKE,
                iter_num=None,
                strikes=self._serial._strike_machine.strikes,
                max_strikes=self._config.max_nmt_strikes,
                outcome=("abort" if outcome == "aborted" else "warn"),
            )
        if outcome == "aborted":
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
            self._diag.warning(
                "integration #%s: %s gate failed on %r",
                ref, phase,
                result.failure.name if result.failure else "unknown",
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


async def run(config: RunConfig, *, driver: InteractiveDriver | None = None) -> int:
    """Drive one ``git-loopy`` invocation to completion.

    Constructs the long-lived per-run state (writers, summary, renderer,
    client, source), drives the iteration loop, and returns the
    appropriate process exit code.

    Args:
        config: The frozen :class:`RunConfig` composed by
            :func:`git_loopy.cli.main`.
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

    # 2) Pricing — bail out loudly on a malformed override (rubber-duck
    #    feedback: silent fallback hides operator intent).
    try:
        pricing = load_pricing(config.pricing_file)
    except PricingError as exc:
        print(f"git-loopy: pricing load failed: {exc}", file=sys.stderr)
        return 1

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
    summary = RunSummary(pricing=pricing)
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
        continuation_reporter = _make_continuation_reporter(config, diag)
        frontier_plan = _make_continuation_plan(
            config, diag, skill_preflight.exposure
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
    except ContinuationCapabilityUnsupported as exc:
        # Fail closed *before* the Pool. A Run that discovered mid-flight that it
        # could not serve its configured mode would have already behaved like
        # `off` while reporting success, which is the silent degradation the
        # capability manifest exists to prevent.
        diag.error("Continuation preflight failed: %s", exc)
        print(
            f"git-loopy: Continuation preflight failed: {exc}. "
            "Verify this distribution with `git-loopy continuation capabilities`.",
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
    loop: _Loop | _ParallelLoop
    if config.parallel > 1:
        loop = _ParallelLoop(
            config=config,
            release_version=release_version,
            git=git,
            prompt_text=prompt_text,
            pricing=pricing,
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
            continuation=continuation_reporter,
        )
    else:
        loop = _Loop(
            config=config,
            release_version=release_version,
            git=git,
            prompt_text=prompt_text,
            pricing=pricing,
            writers=writers,
            sinks=sinks,
            summary=summary,
            client=client,
            skill_preflight=skill_preflight,
            source=source,
            diag=diag,
            include_prs=include_prs,
            continuation=continuation_reporter,
            frontier_plan=frontier_plan,
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
