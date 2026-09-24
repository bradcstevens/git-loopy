"""Detached worker + terminal-client seams for TTY Runs."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard, TierPrices
from git_loopy.run_control import (
    advisory_locking_available,
    control_path_for_trace,
    is_run_alive,
)
from git_loopy.staircase import Candidate, PriceStaircase, StaircaseRefusal
from git_loopy.static_route import RoutePolicy
from git_loopy.ui import Renderer, RunSummary
from git_loopy.ui.console import get_console
from git_loopy.denomination import BilledCreditsDenomination
from git_loopy import no_work_notice, tui_release

__all__ = [
    "DetachedRunSpec",
    "FollowBatch",
    "TraceFollower",
    "control_path_for_trace",
    "encode_detached_run_spec",
    "decode_detached_run_spec",
    "spawn_detached_child",
    "wait_for_run_control",
    "run_terminal_client",
]


@dataclass(frozen=True)
class DetachedRunSpec:
    """The frozen Run inputs handed from the TTY parent to the child worker."""

    config: RunConfig
    run_id: str
    started_at_epoch_ms: int
    rate_card: RateCard | None = None
    staircase: PriceStaircase | None = None


@dataclass(frozen=True)
class FollowBatch:
    """One polling read from a replay trace."""

    lines: list[str]
    finished: bool


def _skill_input_to_payload(value: SkillPolicyInput) -> dict[str, Any]:
    return {"present": value.present, "names": list(value.names)}


def _skill_input_from_payload(payload: dict[str, Any]) -> SkillPolicyInput:
    return SkillPolicyInput(
        present=bool(payload.get("present", False)),
        names=tuple(str(name) for name in payload.get("names", [])),
    )


def _config_to_payload(config: RunConfig) -> dict[str, Any]:
    return {
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "issue_source": config.issue_source,
        "include_prs": config.include_prs,
        "max_iterations": config.max_iterations,
        "max_nmt_strikes": config.max_nmt_strikes,
        "demotion_threshold": config.demotion_threshold,
        "deny_tools": sorted(config.deny_tools),
        "deny_skills": sorted(config.deny_skills),
        "verbosity": config.verbosity,
        "render_reasoning": config.render_reasoning,
        "otel_enabled": config.otel_enabled,
        "execution_host": config.execution_host,
        "send_timeout_seconds": config.send_timeout_seconds,
        "routing": {
            key: [model, effort]
            for key, (model, effort) in sorted(config.routing.items())
        },
        "context_tier": config.context_tier,
        "context_tier_override": config.context_tier_override,
        "route_policy": config.route_policy.value,
        "saved_config_present": config.saved_config_present,
        "config_absent": config.config_absent,
        "route_policy_supplied": config.route_policy_supplied,
        # A Decimal is not a JSON scalar, and float() would round an allowance
        # the operator wrote exactly. The string round-trips both.
        "routing_deadline_seconds": config.routing_deadline_seconds,
        "routing_credit_allowance": (
            None
            if config.routing_credit_allowance is None
            else str(config.routing_credit_allowance)
        ),
        "selector_concurrency": config.selector_concurrency,
        "route_associations": dict(sorted(config.route_associations.items())),
        "swe_bench_associations": dict(
            sorted(config.swe_bench_associations.items())
        ),
        "routing_suppressed": config.routing_suppressed,
        "skill_policy": {
            "project": _skill_input_to_payload(config.skill_policy.project),
            "global_": _skill_input_to_payload(config.skill_policy.global_),
            "environment": _skill_input_to_payload(config.skill_policy.environment),
            "enable_skills": sorted(config.skill_policy.enable_skills),
            "disable_skills": sorted(config.skill_policy.disable_skills),
        },
        "classifier_model": config.classifier_model,
        "classifier_effort": config.classifier_effort,
        "issue_pin": config.issue_pin,
        "escalation_rung": (
            None
            if config.escalation_rung is None
            else [config.escalation_rung[0], config.escalation_rung[1]]
        ),
    }


def _config_from_payload(payload: dict[str, Any]) -> RunConfig:
    skill_policy = payload.get("skill_policy", {})
    routing = {
        str(key): (str(value[0]), None if value[1] is None else str(value[1]))
        for key, value in dict(payload.get("routing", {})).items()
    }
    escalation = payload.get("escalation_rung")
    return RunConfig(
        model=payload.get("model"),
        reasoning_effort=payload.get("reasoning_effort"),
        issue_source=payload.get("issue_source", "github"),
        include_prs=payload.get("include_prs"),
        max_iterations=int(payload.get("max_iterations", 0)),
        max_nmt_strikes=int(payload.get("max_nmt_strikes", 3)),
        demotion_threshold=int(payload.get("demotion_threshold", 3)),
        deny_tools=frozenset(str(item) for item in payload.get("deny_tools", [])),
        deny_skills=frozenset(str(item) for item in payload.get("deny_skills", [])),
        verbosity=int(payload.get("verbosity", 0)),
        render_reasoning=bool(payload.get("render_reasoning", True)),
        otel_enabled=bool(payload.get("otel_enabled", False)),
        execution_host=str(payload.get("execution_host", "local")),
        send_timeout_seconds=float(payload.get("send_timeout_seconds", 7200.0)),
        routing=routing,
        context_tier=str(payload.get("context_tier", "default")),
        context_tier_override=bool(payload.get("context_tier_override", False)),
        route_policy=RoutePolicy.parse(payload.get("route_policy")),
        saved_config_present=bool(payload.get("saved_config_present", False)),
        config_absent=bool(payload.get("config_absent", False)),
        route_policy_supplied=bool(payload.get("route_policy_supplied", False)),
        routing_deadline_seconds=(
            None
            if payload.get("routing_deadline_seconds") is None
            else float(payload["routing_deadline_seconds"])
        ),
        routing_credit_allowance=(
            None
            if payload.get("routing_credit_allowance") is None
            else Decimal(str(payload["routing_credit_allowance"]))
        ),
        selector_concurrency=(
            None
            if payload.get("selector_concurrency") is None
            else int(payload["selector_concurrency"])
        ),
        route_associations={
            str(key): str(value)
            for key, value in dict(payload.get("route_associations", {})).items()
        },
        swe_bench_associations={
            str(key): str(value)
            for key, value in dict(payload.get("swe_bench_associations", {})).items()
        },
        routing_suppressed=bool(payload.get("routing_suppressed", False)),
        skill_policy=SkillPolicyInputs(
            project=_skill_input_from_payload(dict(skill_policy.get("project", {}))),
            global_=_skill_input_from_payload(dict(skill_policy.get("global_", {}))),
            environment=_skill_input_from_payload(
                dict(skill_policy.get("environment", {}))
            ),
            enable_skills=frozenset(
                str(item) for item in skill_policy.get("enable_skills", [])
            ),
            disable_skills=frozenset(
                str(item) for item in skill_policy.get("disable_skills", [])
            ),
        ),
        classifier_model=payload.get("classifier_model"),
        classifier_effort=payload.get("classifier_effort"),
        issue_pin=payload.get("issue_pin"),
        escalation_rung=(
            None
            if escalation is None
            else (str(escalation[0]), str(escalation[1]))
        ),
    )


def _tier_to_payload(value: TierPrices | None) -> dict[str, Any] | None:
    return None if value is None else value.to_payload()


def _tier_from_payload(payload: dict[str, Any] | None) -> TierPrices | None:
    if payload is None:
        return None
    return TierPrices(
        input_price=payload.get("input_price"),
        output_price=payload.get("output_price"),
        cache_read_price=payload.get("cache_read_price"),
        cache_write_price=payload.get("cache_write_price"),
        max_prompt_tokens=payload.get("max_prompt_tokens"),
    )


def _card_to_payload(card: RateCard | None) -> dict[str, Any] | None:
    return None if card is None else card.to_payload()


def _card_from_payload(payload: dict[str, Any] | None) -> RateCard | None:
    if payload is None:
        return None
    models = {}
    for model, record in dict(payload.get("models", {})).items():
        prices_payload = record.get("prices")
        models[str(model)] = ModelRate(
            model=str(model),
            multiplier=record.get("multiplier"),
            discount_percent=record.get("discount_percent"),
            prices=(
                None
                if prices_payload is None
                else ModelPrices(
                    batch_size=prices_payload.get("batch_size"),
                    input_price=prices_payload.get("input_price"),
                    output_price=prices_payload.get("output_price"),
                    cache_read_price=prices_payload.get("cache_read_price"),
                    cache_write_price=prices_payload.get("cache_write_price"),
                    max_prompt_tokens=prices_payload.get("max_prompt_tokens"),
                    long_context=_tier_from_payload(
                        prices_payload.get("long_context")
                    ),
                )
            ),
        )
    return RateCard(models=models)


def _staircase_to_payload(staircase: PriceStaircase | None) -> dict[str, Any] | None:
    if staircase is None:
        return None
    return {
        "candidates": [
            {
                "model": candidate.model,
                "effort": candidate.effort,
                "multiplier": candidate.multiplier,
            }
            for candidate in staircase.candidates
        ],
        "refusal": None if staircase.refusal is None else staircase.refusal.value,
        "unpriced_models": list(staircase.unpriced_models),
    }


def _staircase_from_payload(payload: dict[str, Any] | None) -> PriceStaircase | None:
    if payload is None:
        return None
    refusal = payload.get("refusal")
    return PriceStaircase(
        candidates=tuple(
            Candidate(
                str(candidate["model"]),
                None if candidate.get("effort") is None else str(candidate["effort"]),
                float(candidate["multiplier"]),
            )
            for candidate in payload.get("candidates", [])
        ),
        refusal=None if refusal is None else StaircaseRefusal(str(refusal)),
        unpriced_models=tuple(
            str(model) for model in payload.get("unpriced_models", [])
        ),
    )


def encode_detached_run_spec(spec: DetachedRunSpec) -> str:
    payload = {
        "schema_version": 1,
        "config": _config_to_payload(spec.config),
        "run_id": spec.run_id,
        "started_at_epoch_ms": spec.started_at_epoch_ms,
        "rate_card": _card_to_payload(spec.rate_card),
        "staircase": _staircase_to_payload(spec.staircase),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_detached_run_spec(payload: str) -> DetachedRunSpec:
    raw = base64.urlsafe_b64decode(payload.encode("ascii"))
    document = json.loads(raw)
    return DetachedRunSpec(
        config=_config_from_payload(dict(document["config"])),
        run_id=str(document["run_id"]),
        started_at_epoch_ms=int(document["started_at_epoch_ms"]),
        rate_card=_card_from_payload(document.get("rate_card")),
        staircase=_staircase_from_payload(document.get("staircase")),
    )


def spawn_detached_child(
    spec: DetachedRunSpec,
    *,
    diagnostics_path: Path,
    cwd: Path,
) -> subprocess.Popen[Any]:
    """Start the worker in its own process session with stdout/stderr logged."""
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = diagnostics_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(  # noqa: S603 - this Runner owns the child module
            [
                sys.executable,
                "-m",
                "git_loopy.run_child",
                encode_detached_run_spec(spec),
            ],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
    except Exception:
        stdout.close()
        raise
    stdout.close()
    return process


def wait_for_run_control(
    control_path: Path,
    child: subprocess.Popen[Any],
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> bool:
    """Wait until the child owns its control lock, or exits before it can."""
    if not advisory_locking_available():
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = is_run_alive(control_path)
        if alive is True:
            return True
        if child.poll() is not None:
            return False
        time.sleep(poll_interval)
    return False


class TraceFollower:
    """Incrementally read a Run trace without mistaking temporary EOF for finish."""

    def __init__(
        self,
        trace_path: Path,
        control_path: Path,
        *,
        owner_alive: Callable[[], bool] | None = None,
    ) -> None:
        self._trace_path = trace_path
        self._control_path = control_path
        self._owner_alive = owner_alive
        self._offset = 0
        self._pending = ""
        self._saw_run_end = False

    @property
    def saw_run_end(self) -> bool:
        """Whether the trace itself said the Run ended.

        Distinguishes a Run that is over from a client that merely stopped
        following it — the difference between reporting the worker's result and
        walking away from a Run that is still working (ADR-0058).
        """
        return self._saw_run_end

    def poll(self) -> FollowBatch:
        lines: list[str] = []
        if self._trace_path.exists():
            with self._trace_path.open(encoding="utf-8") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
            if chunk:
                self._pending += chunk
                pieces = self._pending.splitlines(keepends=True)
                complete: list[str] = []
                pending = ""
                for piece in pieces:
                    if piece.endswith("\n"):
                        complete.append(piece[:-1])
                    else:
                        pending = piece
                self._pending = pending
                lines.extend(complete)
                self._saw_run_end = self._saw_run_end or any(
                    '"type":"wrapper.run.end"' in line.replace(" ", "")
                    or '"type": "wrapper.run.end"' in line
                    for line in complete
                )
        finished = self._saw_run_end or not self._run_is_alive()
        return FollowBatch(lines=lines, finished=finished)

    def _run_is_alive(self) -> bool:
        alive = is_run_alive(self._control_path)
        if alive is not None:
            if alive:
                return True
            if self._owner_alive is not None:
                return self._owner_alive()
            return False
        if self._owner_alive is not None:
            return self._owner_alive()
        return False


@dataclass(frozen=True)
class _WatchOutcome:
    """What a client saw before it stopped following the worker's trace."""

    #: Whether the trace carried a single decodable record.
    traced: bool
    #: Whether the trace said the Run itself ended, as opposed to this client
    #: merely stopping watching.
    run_ended: bool


def _follow_trace_with_renderer(
    trace_path: Path,
    control_path: Path,
    *,
    config: RunConfig,
    owner_alive: Callable[[], bool] | None = None,
    poll_interval: float = 0.05,
) -> _WatchOutcome:
    """Render the worker's trace to its end and report what watching established.

    ``traced`` is what tells a client apart from the Run it watches: a trace that
    never received a record means the worker ended before it could announce
    itself, so whatever went wrong was never rendered here and lives only in the
    worker's diagnostics (see :func:`_report_worker_result`).
    """
    summary = RunSummary(denomination=BilledCreditsDenomination())
    renderer = Renderer(
        console=get_console(),
        summary=summary,
        verbosity=config.verbosity,
        render_reasoning=config.render_reasoning,
    )
    follower = TraceFollower(trace_path, control_path, owner_alive=owner_alive)
    traced = False
    while True:
        batch = follower.poll()
        for line in batch.lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            traced = True
            renderer.render(event)
        if batch.finished:
            return _WatchOutcome(traced=traced, run_ended=follower.saw_run_end)
        time.sleep(poll_interval)


def _helper_args(
    helper: Path, trace_path: Path, control_path: Path, config: RunConfig
) -> list[str]:
    args = [
        str(helper),
        "--attach",
        str(trace_path),
        "--control",
        str(control_path),
    ]
    if config.issue_pin is not None:
        args.extend(["--issue", str(config.issue_pin)])
    if config.model is not None:
        args.extend(["--model", config.model])
    if config.reasoning_effort is not None:
        args.extend(["--reasoning-effort", config.reasoning_effort])
    return args


#: How long a client waits for a worker whose trace already ended to be reaped.
#: The Run is over by then, so this only spans the worker's own teardown; a
#: worker that outlives it is still running, which is not this client's failure.
_WORKER_REAP_GRACE = 10.0

#: How much of a never-traced startup a client echoes. Bounded because the file
#: is also the worker's stdout, and an operator needs the blocker, not a dump.
_STARTUP_DIAGNOSTIC_LINES = 40


def _startup_diagnostics(diagnostics_path: Path) -> list[str]:
    """The tail of a worker's diagnostics, or nothing if it wrote none."""
    try:
        text = diagnostics_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-_STARTUP_DIAGNOSTIC_LINES:]


def _report_worker_result(
    child: "subprocess.Popen[Any]",
    *,
    diagnostics_path: Path,
    watched: _WatchOutcome,
    warn: Callable[[str], None],
) -> int:
    """Return the worker's exit status, surfacing a startup it never traced.

    A client observes; the Run owns its own lifetime (ADR-0058). Two rules fall
    out of that, and this is the one place both are applied:

    * **A worker still running is not this client's failure.** Detaching, or
      quitting a Dashboard, ends the watching and nothing else, so it returns
      ``0``. The client waits for a worker only when the trace said the *Run*
      ended, which bounds the wait to that worker's own teardown; waiting on a
      working Run would make disconnecting look like stopping work.
    * **A worker that ended owns the result.** Returning the client's own
      success instead is how a blocked startup used to reach an operator as
      exit ``0``: Run preflight refuses *before* the first Event, so the trace
      stays empty, the renderer prints nothing, and a saved setup reads as Run
      readiness. When nothing was traced, the blocker and its remedy exist only
      in the worker's diagnostics, so they are echoed here rather than left in a
      file the operator has no reason to open. A Run that *did* trace its work
      already said what happened; repeating the log over the top would bury it.
    """
    status = child.poll()
    if status is None and watched.run_ended:
        try:
            status = child.wait(timeout=_WORKER_REAP_GRACE)
        except subprocess.TimeoutExpired:
            status = None
    if status is None or status == 0:
        return 0
    # A signalled worker reports a negative status, which is no exit code at all.
    code = status if status > 0 else 1
    if not watched.traced:
        warn(
            f"the Run worker exited {code} before it recorded any activity; "
            f"no issue work started. Its startup diagnostics follow "
            f"({diagnostics_path})."
        )
        for line in _startup_diagnostics(diagnostics_path):
            print(line, file=sys.stderr)
    return code


def run_terminal_client(
    *,
    repository_root: Path,
    config: RunConfig,
    trace_path: Path,
    control_path: Path,
    child: subprocess.Popen[Any],
    release_version: str,
    warn: Callable[[str], None],
    diagnostics_path: Path,
) -> int:
    """Attach a TTY parent to a detached child with the helper or a trace fallback."""
    def owner_alive() -> bool:
        return child.poll() is None

    helper: Path | None = None
    if not release_version:
        warn(
            "could not validate a git-loopy-tui Release identity; following the "
            "replay log with the line printer."
        )
    elif advisory_locking_available():
        helper = tui_release.resolve_runtime_helper(
            repository_root,
            release_version=release_version,
            warn=warn,
        )
    else:
        warn(
            "this platform has no advisory-lock control artifact; "
            "following the replay log with the line printer."
        )
    if helper is not None:
        if not wait_for_run_control(control_path, child):
            warn(
                "the detached Run never published its control artifact; "
                "following the replay log with the line printer."
            )
        else:
            try:
                result = subprocess.run(  # noqa: S603 - the helper path was validated
                    _helper_args(helper, trace_path, control_path, config),
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
            except OSError as exc:
                warn(
                    f"could not start git-loopy-tui ({type(exc).__name__}: {exc}); "
                    "following the replay log with the line printer."
                )
            else:
                if result.returncode == 0:
                    # The Dashboard left of its own accord. A Detach leaves the
                    # Run working, so this client claims no knowledge that it
                    # ended and never waits; a worker already gone by now is
                    # still the Run's own result and is reported as such.
                    status = _report_worker_result(
                        child,
                        diagnostics_path=diagnostics_path,
                        watched=_WatchOutcome(traced=True, run_ended=False),
                        warn=warn,
                    )
                    _print_no_work_notice(trace_path)
                    return status
                warn(
                    f"git-loopy-tui exited {result.returncode}; "
                    "following the replay log with the line printer."
                )
    watched = _follow_trace_with_renderer(
        trace_path,
        control_path,
        config=config,
        owner_alive=owner_alive,
    )
    status = _report_worker_result(
        child,
        diagnostics_path=diagnostics_path,
        watched=watched,
        warn=warn,
    )
    _print_no_work_notice(trace_path)
    return status


def _print_no_work_notice(trace_path: Path) -> None:
    """Say why a Run that found nothing it could work ended (#642).

    Such a Run ends seconds after it starts, and its exit status alone reads as
    a crash. Printed after the Dashboard or line printer has returned, so it is
    the last thing on the terminal the operator gets back. A Run still working
    -- a Detach -- has no ``wrapper.run.end`` yet, so it earns nothing here.
    """
    lines = no_work_notice.trace_notice(trace_path)
    if not lines:
        return
    for line in lines:
        print(f"git-loopy: {line}", file=sys.stderr)
