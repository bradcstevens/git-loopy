"""``git_loopy.ui.renderer`` — event-driven terminal renderer.

The :class:`Renderer` consumes one event dict at a time and prints (via
its injected :class:`rich.console.Console`) the corresponding terminal
output. Streaming deltas are filtered out upstream by
:func:`git_loopy.events.map_sdk_event` so each "print" the renderer
issues is final — no in-place re-draw, no scrollback duplication.

Verbosity ladder:

================  ============================================================
Level             Behaviour
================  ============================================================
0 (default)       Reasoning (if ``render_reasoning=True``), tool calls one-line,
                  wrapper outcomes (commits, auto-closures, strikes, denials).
                  Tool results are dropped. ``session.*`` and
                  ``tool.permission_requested`` events are silent.
1 (``-v``)        Adds tool-result lines: ``size`` for successes,
                  ``error.message`` for failures. No tool-result content
                  body is rendered (the events module does NOT carry it;
                  ``tool.result`` payloads only contain ``result_size_chars``
                  and ``error``).
2 (``-vv``)       Reasoning rendered without truncation cues (deltas are
                  already filtered upstream so this is the same as level 0
                  unless ``render_reasoning=False`` — in which case the
                  toggle wins).
3 (``-vvv``)      Every event gets a raw-dump line in addition to its
                  normal handler. Permission and session events that are
                  normally silent surface here.
================  ============================================================

``render_reasoning=False`` is an explicit operator opt-out and always
wins over the verbosity ladder — ``--no-reasoning -vv`` hides reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from rich.console import Console
from rich.text import Text

from git_loopy.events import (
    ASSISTANT_MESSAGE,
    ASSISTANT_REASONING,
    SESSION_CREATED,
    SESSION_DELETED,
    SESSION_IDLE,
    TOOL_CALL,
    TOOL_PERMISSION_DENIED,
    TOOL_PERMISSION_REQUESTED,
    TOOL_RESULT,
    USAGE_TOKENS,
    WRAPPER_AFK_READY_COLLECTED,
    WRAPPER_ASK_USER_ATTEMPTED,
    WRAPPER_AUTO_CLOSE,
    WRAPPER_CHECKPOINT_RECORDED,
    WRAPPER_COMMIT_RECORDED,
    WRAPPER_CONCURRENCY_CHANGED,
    WRAPPER_CONTRIBUTION_END,
    WRAPPER_CONTRIBUTION_START,
    WRAPPER_ITERATION_END,
    WRAPPER_ITERATION_START,
    WRAPPER_PARALLEL_SERIAL_FALLBACK,
    WRAPPER_POOL_EXCLUDED,
    WRAPPER_PR_ADVANCED,
    WRAPPER_PUSH_RECORDED,
    WRAPPER_RUN_END,
    WRAPPER_RUN_START,
    WRAPPER_SERIAL_REQUESTED,
    WRAPPER_STRIKE,
)

from git_loopy.usage import BillingSample

from .console import STYLES
from .summary import RunSummary

__all__ = ["Renderer"]


_THINKING_PREFIX: str = "✻ Thinking:"


@dataclass
class Renderer:
    """Event-driven renderer.

    Drives a :class:`RunSummary` for per-iteration state accumulation and
    prints to an injected :class:`rich.console.Console`. The loop slice
    (#10) owns the lifecycle: construct once, call :meth:`render` per
    event, read :attr:`summary.completed` after :data:`WRAPPER_RUN_END`.

    Attributes:
        console: The :class:`Console` to print to. Tests inject a
            :class:`io.StringIO`-backed console with
            ``force_terminal=False``; production uses
            :func:`get_console`.
        summary: The :class:`RunSummary` accumulator. Owned by the
            caller; the renderer is the writer.
        verbosity: 0-3, mapping to the ladder in the module docstring.
        render_reasoning: When ``False``, reasoning is always suppressed
            regardless of verbosity.
    """

    console: Console
    summary: RunSummary
    verbosity: int = 0
    render_reasoning: bool = True

    # -- live-streaming state ----------------------------------------------
    # The SDK emits ``*_delta`` events carrying incremental text. The
    # session module forwards their ``delta_content`` to :meth:`stream_reasoning`
    # / :meth:`stream_message` (bypassing :meth:`render`, since deltas are not
    # JSONL artefacts). ``_stream_open`` tracks whether the cursor is sitting
    # mid-line on an unterminated streamed chunk; ``_streamed_reasoning`` /
    # ``_streamed_message`` record that the *current* block was streamed so the
    # matching final event finalises instead of re-printing the whole block.
    _stream_open: bool = False
    _streamed_reasoning: bool = False
    _streamed_message: bool = False

    def render(self, event: dict[str, Any]) -> None:
        """Dispatch ``event`` to its per-type handler.

        Unknown event types are no-ops at default verbosity and raw-dumped
        at ``-vvv``. An event dict missing a ``type`` key is a no-op
        regardless of verbosity.
        """
        et = event.get("type")
        if not isinstance(et, str):
            return
        # Terminate any open streamed line before a full-line event prints,
        # so a tool call / panel / final message never glues onto a streamed
        # reasoning or message chunk.
        self._close_open_line()
        handler = _HANDLERS.get(et)
        if handler is None:
            if self.verbosity >= 3:
                self._raw_dump(event)
            return
        handler(self, event)
        if self.verbosity >= 3:
            self._raw_dump(event)

    # -- live streaming ----------------------------------------------------

    def stream_reasoning(
        self, delta: str, issue: int | str | None = None
    ) -> None:
        """Stream one incremental reasoning chunk to the terminal.

        Called directly by the session module for each
        ``assistant.reasoning_delta`` event (not via :meth:`render`, since
        deltas are UX-only and never written to JSONL). On the first chunk
        of a block the ``✻ Thinking:`` prefix is printed once; subsequent
        chunks append in place. ``soft_wrap=True`` is required: each
        ``end=""`` print wraps independently of cursor column, so Rich's
        own wrapping would mis-break a mid-stream line — we let the terminal
        wrap instead.

        ``render_reasoning=False`` suppresses streamed reasoning entirely,
        mirroring the final-event handler's opt-out. ``issue`` (the Parallel-
        mode Lane attribution, issue #66) is accepted for sink-protocol parity
        but ignored: the line printer renders one interleaved scrollback stream,
        so per-Lane attribution is the interactive Dashboard's concern.
        """
        if not self.render_reasoning or not delta:
            return
        if not self._streamed_reasoning:
            self._close_open_line()
            self.console.print(
                Text(f"{_THINKING_PREFIX} ", style=STYLES["reasoning"]),
                end="",
                soft_wrap=True,
            )
            self._streamed_reasoning = True
        self.console.print(
            Text(delta, style=STYLES["reasoning"]), end="", soft_wrap=True
        )
        self._stream_open = True

    def stream_message(
        self, delta: str, issue: int | str | None = None
    ) -> None:
        """Stream one incremental assistant-message chunk to the terminal.

        Called directly by the session module for each
        ``assistant.message_delta`` event. See :meth:`stream_reasoning` for
        the ``soft_wrap`` rationale and the ignored ``issue`` (#66) parameter.
        """
        if not delta:
            return
        if not self._streamed_message:
            self._close_open_line()
            self._streamed_message = True
        self.console.print(delta, end="", soft_wrap=True)
        self._stream_open = True

    def _close_open_line(self) -> None:
        """Terminate an open streamed line with a newline, if one is open."""
        if self._stream_open:
            self.console.print()
            self._stream_open = False

    # -- handler bodies ----------------------------------------------------

    def _on_run_start(self, event: dict[str, Any]) -> None:
        run_id = event.get("run_id", "")
        text = Text()
        text.append("▶ ", style=STYLES["success"])
        text.append("git-loopy run started", style=STYLES["panel_title"])
        if run_id:
            text.append(f"  (run_id: {run_id})", style=STYLES["meta"])
        self.console.print(text)
        self._print_parallel_mode(event)

    def _print_parallel_mode(self, event: dict[str, Any]) -> None:
        """Say that Parallel mode is on and what Lane cap resolved (#304).

        Requesting Parallel mode used to produce output byte-identical to a
        serial Run, so an operator whose tracker carried no ``parallel-safe``
        issue reasonably concluded the flag was broken. A serial Run carries
        none of these keys and this prints nothing.

        The line is scoped to **Lanes** and says so (#356). Its predecessor —
        "only issues labelled parallel-safe take a Lane" — was true about Lanes
        and read as a claim about the **Pool**, which is the opposite of
        ADR-0008's drain-everything promise: a Parallel Run works the rest of
        the Pool serially in the same Run.
        """
        if not event.get("parallel_mode"):
            return
        lane_cap = event.get("lane_cap")
        effective = event.get("effective_lane_limit")
        text = Text()
        text.append("⇉ ", style=STYLES["meta"])
        text.append("Parallel mode", style=STYLES["panel_title"])
        if effective is None:
            # Only a source with a `parallel-safe` label concept can supply
            # Lane work; anything else degrades entirely to the serial path,
            # which is the one case where the flag genuinely does nothing.
            text.append(
                "  (this issue source supplies no Lane work — running serially)",
                style=STYLES["meta"],
            )
            self.console.print(text)
            return
        text.append(f"  Lane cap {lane_cap}", style=STYLES["meta"])
        if effective != lane_cap:
            text.append(f", starting at {effective}", style=STYLES["meta"])
        text.append(
            "  •  issues labelled parallel-safe take a Lane; the rest are "
            "worked serially in this same run",
            style=STYLES["meta"],
        )
        self.console.print(text)

    def _on_parallel_serial_fallback(self, event: dict[str, Any]) -> None:
        """Name a serial Iteration a Parallel-mode Run fell back to (#304).

        Parallel-safe is a human assertion the runner never infers, so the
        overwhelmingly common cause is that nothing carries the label — and the
        operator is the only one who can fix that. Each reason gets its own
        sentence because the operator's next move differs for each.
        """
        eligible = event.get("eligible", 0)
        reason = event.get("reason", "")
        explanation = {
            "no_parallel_safe_candidates": (
                "no ready-for-agent issue carries parallel-safe"
            ),
            "all_parallel_safe_worked": (
                f"the {event.get('worked', 0)} parallel-safe "
                "issue(s) found were already worked this run"
            ),
            "parallel_safe_unavailable": (
                f"{event.get('unavailable', 0)} parallel-safe candidate(s) "
                "could not be read"
            ),
        }.get(reason, str(reason).replace("_", " "))
        text = Text()
        text.append("⇉ ", style=STYLES["meta"])
        text.append("serial iteration", style=STYLES["meta"])
        text.append(
            f"  ({eligible} eligible parallel-safe issues — {explanation})",
            style=STYLES["meta"],
        )
        self.console.print(text)

    def _on_serial_requested(self, event: dict[str, Any]) -> None:
        """Say that refill stopped and what is waiting behind the drain (#356).

        A Parallel Run's banner is scoped to **Lanes** — "only issues labelled
        parallel-safe take a Lane" — and until this line existed nothing
        contradicted the reading that everything else was excluded from the Run
        until the serial **Iteration** finally started, which can be hours
        later or (under an explicit ``max-iterations`` cap) never.

        The count is the whole point: "one more after this Lane" and "forty"
        are different situations. An **Integration** fallback latch counted
        nothing — the driver's peek is the only thing that can, and it is
        skipped once demand is latched — so it says how, and never guesses a
        number.
        """
        seen = event.get("serial_required")
        ref = event.get("issue")
        text = Text()
        text.append("⇉ ", style=STYLES["meta"])
        text.append("serial work queued", style=STYLES["meta"])
        if seen is None:
            detail = (
                f"#{ref} needs a serial iteration"
                if ref is not None
                else "a serial iteration is needed"
            )
        else:
            detail = (
                f"{seen} ready-for-agent issue(s) are not parallel-safe, "
                f"starting with #{ref}"
            )
        text.append(
            f"  ({detail} — no new Lane starts; they are worked after the "
            "open Lanes finish)",
            style=STYLES["meta"],
        )
        self.console.print(text)

    def _on_concurrency_changed(self, event: dict[str, Any]) -> None:
        """Name the signal that moved the effective **Lane** limit (#219 §6, #309).

        The configured **Lane cap** is a safety ceiling, not a utilization
        promise, so a Run narrowing itself is correct behaviour — but a Run
        that narrows *silently* is indistinguishable from Parallel mode being
        broken, which is the same reporting failure #304 fixed for a Run that
        never engaged a Lane at all. Each pressure gets its own words because
        the operator's next move differs: wait out a throttle, unblock
        Integration, raise a budget, or free the machine.
        """
        effective = event.get("effective_lane_limit")
        configured = event.get("configured_lane_limit")
        pressure = event.get("pressure")
        cause = {
            "rate_limit": "API rate limiting",
            "integration_backlog": "integration backlog",
            "credit": "AI-credit burn",
            "host": "host/setup pressure",
            None: "pressure cleared",
        }.get(pressure, str(pressure).replace("_", " "))
        text = Text()
        text.append("⇉ ", style=STYLES["meta"])
        text.append("Lane concurrency", style=STYLES["meta"])
        text.append(
            f"  {effective} of {configured}  ({cause})", style=STYLES["meta"]
        )
        self.console.print(text)

    def _on_run_end(self, event: dict[str, Any]) -> None:
        # Render the frozen run-end Table.
        self.console.print(self.summary.build_run_table())
        outcome = event.get("outcome")
        if outcome is not None:
            text = Text()
            text.append("✓ ", style=STYLES["success"])
            text.append(f"run end: {outcome}", style=STYLES["meta"])
            self.console.print(text)

    def _on_iteration_start(self, event: dict[str, Any]) -> None:
        iter_num = int(event.get("iter", 0) or 0)
        issue_num_raw = event.get("issue")
        issue_num: Optional[int]
        try:
            issue_num = int(issue_num_raw) if issue_num_raw is not None else None
        except (TypeError, ValueError):
            issue_num = None
        self.summary.on_iteration_start(iter_num=iter_num, issue_num=issue_num)
        text = Text()
        text.append("── ", style=STYLES["panel_rule"])
        text.append(f"Iteration {iter_num}", style=STYLES["panel_title"])
        if issue_num is not None:
            text.append(f"  •  Issue #{issue_num}", style=STYLES["meta"])
        text.append(" ──", style=STYLES["panel_rule"])
        self.console.print(text)

    def _on_iteration_end(self, event: dict[str, Any]) -> None:
        snap = self.summary.on_iteration_end(event)
        if snap is None:
            return
        self.console.print(self.summary.build_iteration_panel(snap))

    def _on_contribution_start(self, event: dict[str, Any]) -> None:
        """Announce one **Lane contribution** opening (#310).

        The Rolling-dispatch counterpart of the Iteration rule: a contribution
        is not an Iteration, so it gets its own banner naming the issue and the
        **Lane** it started in — a Lane slot is reused many times per Run, so
        the pair is what tells two occupancies apart.
        """
        snap = self.summary.on_contribution_start(event)
        if snap is None:
            return
        text = Text()
        text.append("── ", style=STYLES["panel_rule"])
        text.append(f"Contribution #{event.get('issue')}", style=STYLES["panel_title"])
        text.append(f"  •  Lane {event.get('lane_id')}", style=STYLES["meta"])
        text.append(" ──", style=STYLES["panel_rule"])
        self.console.print(text)

    def _on_contribution_end(self, event: dict[str, Any]) -> None:
        snap = self.summary.on_contribution_end(event)
        if snap is None:
            return
        self.console.print(self.summary.build_iteration_panel(snap))

    def _on_afk_ready_collected(self, event: dict[str, Any]) -> None:
        issues = event.get("issues") or []
        count = len(issues) if hasattr(issues, "__len__") else 0
        excluded = event.get("excluded") or 0
        text = Text()
        text.append("ⓘ ", style=STYLES["meta"])
        text.append("AFK-ready pool: ", style=STYLES["meta"])
        text.append(f"{count} issue", style=STYLES["panel_title"])
        if count != 1:
            text.append("s", style=STYLES["panel_title"])
        if count > 0:
            # GitHub refs are ints — render as "#42"; PRDs refs are str
            # file paths — render as the path (no leading "#").
            text.append(
                "  ("
                + ", ".join(
                    f"#{i}" if isinstance(i, int) else str(i) for i in issues
                )
                + ")",
                style=STYLES["meta"],
            )
        if excluded:
            # An all-excluded Pool is a different operator situation from a
            # tracker with no work (#303) — the first says "fix the issues you
            # already triaged", the second says "triage more". Saying so here
            # keeps the two apart on the one line that reports Pool size.
            text.append(
                f"  ({excluded} ready-for-agent candidate"
                f"{'' if excluded == 1 else 's'} excluded"
                + (" — nothing eligible remains" if count == 0 else "")
                + ")",
                style=STYLES["meta"],
            )
        self.console.print(text)

    def _on_pool_excluded(self, event: dict[str, Any]) -> None:
        # A ready-for-agent issue a human deliberately triaged that the
        # AFK-ready discriminator dropped (#303). Named on the console rather
        # than only in the replay log, because the operator is the only one who
        # can fix the issue's sections.
        ref = event.get("issue")
        reason = event.get("reason", "")
        title = event.get("title", "")
        text = Text()
        text.append("⊘ ", style=STYLES["meta"])
        text.append("excluded ", style=STYLES["meta"])
        text.append(f"#{ref}" if isinstance(ref, int) else str(ref))
        if title:
            text.append(f" {title}", style=STYLES["meta"])
        if reason:
            text.append(f" — {str(reason).replace('_', ' ')}", style=STYLES["meta"])
        self.console.print(text)

    def _on_checkpoint_recorded(self, event: dict[str, Any]) -> None:
        # A runner-authored Checkpoint (ADR-0004). Rendered DISTINCTLY from an
        # agent commit (different glyph, "checkpoint" label) and deliberately
        # NOT counted toward the Summary's commit tally — Checkpoints are
        # excluded from agent commit accounting.
        sha = event.get("sha", "")
        issue = event.get("issue")
        short = sha[:10] if isinstance(sha, str) else ""
        text = Text()
        text.append("⎘ ", style=STYLES["meta"])
        text.append("checkpoint ", style=STYLES["meta"])
        if short:
            text.append(short, style=STYLES["meta"])
        if issue is not None:
            label = f"#{issue}" if isinstance(issue, int) else str(issue)
            text.append(f"  ({label})", style=STYLES["meta"])
        self.console.print(text)

    def _on_push_recorded(self, event: dict[str, Any]) -> None:
        # The runner auto-push (ADR-0004) landed: the current branch reached its
        # upstream after this iteration's new commits. Like a Checkpoint, a push
        # is a runner action — NOT an agent commit — so it is rendered with its
        # own glyph and never touches the Summary commit tally.
        text = Text()
        text.append("⇡ ", style=STYLES["meta"])
        text.append("pushed to upstream", style=STYLES["meta"])
        self.console.print(text)

    def _on_commit_recorded(self, event: dict[str, Any]) -> None:
        sha = event.get("sha", "")
        subject = event.get("subject", "")
        short = sha[:10] if isinstance(sha, str) else ""
        text = Text()
        text.append("✓ ", style=STYLES["success"])
        text.append("commit ", style=STYLES["meta"])
        text.append(short, style=STYLES["success"])
        if subject:
            # Single-line: collapse any newlines in the subject for the
            # rendered line. The full message is intact in git/log.
            subject_line = str(subject).splitlines()[0] if str(subject).splitlines() else str(subject)
            text.append(f"  {subject_line}", style=STYLES["meta"])
        self.console.print(text)
        self.summary.record_commit()

    def _on_auto_close(self, event: dict[str, Any]) -> None:
        issue = event.get("issue")
        sha = event.get("sha", "")
        short = sha[:10] if isinstance(sha, str) else ""
        text = Text()
        text.append("✓ ", style=STYLES["success"])
        text.append("auto-closed ", style=STYLES["meta"])
        if issue is not None:
            text.append(f"#{issue}", style=STYLES["success"])
        if short:
            text.append(f"  ({short})", style=STYLES["meta"])
        self.console.print(text)
        self.summary.record_auto_close()

    def _on_pr_advanced(self, event: dict[str, Any]) -> None:
        pr = event.get("pr")
        sha = event.get("sha", "")
        short = sha[:10] if isinstance(sha, str) else ""
        text = Text()
        text.append("↑ ", style=STYLES["success"])
        text.append("advanced PR ", style=STYLES["meta"])
        if pr is not None:
            text.append(f"#{pr}", style=STYLES["success"])
        if short:
            text.append(f"  ({short})", style=STYLES["meta"])
        self.console.print(text)
        # PR advances count as wrapper-side completions for progress display,
        # sharing the auto-close tally (the loop already counts them toward
        # iteration progress alongside issue closures).
        self.summary.record_auto_close()

    def _on_strike(self, event: dict[str, Any]) -> None:
        strikes_raw = event.get("strikes")
        max_strikes = event.get("max_strikes")
        try:
            strikes_value: Optional[int] = (
                int(strikes_raw) if strikes_raw is not None else None
            )
        except (TypeError, ValueError):
            strikes_value = None
        self.summary.record_strike(strikes=strikes_value)
        snap = self.summary.current
        current_strikes = snap.strikes if snap is not None else (strikes_value or 0)
        text = Text()
        text.append("⚠ ", style=STYLES["warning"])
        text.append("strike ", style=STYLES["warning"])
        if max_strikes is not None:
            text.append(f"{current_strikes}/{max_strikes}", style=STYLES["warning"])
        else:
            text.append(str(current_strikes), style=STYLES["warning"])
        self.console.print(text)

    def _on_ask_user_attempted(self, event: dict[str, Any]) -> None:
        text = Text()
        text.append("⚠ ", style=STYLES["warning"])
        text.append(
            "agent attempted ask_user (disabled in AFK runs)",
            style=STYLES["warning"],
        )
        self.console.print(text)

    def _on_assistant_reasoning(self, event: dict[str, Any]) -> None:
        # If this block was streamed delta-by-delta, render() already closed
        # the open line; just reset the per-block flag so the next block's
        # first delta re-prints the prefix. No re-print — that would duplicate.
        if self._streamed_reasoning:
            self._streamed_reasoning = False
            return
        if not self.render_reasoning:
            return
        content = event.get("content", "")
        if not isinstance(content, str) or not content:
            return
        text = Text()
        text.append(f"{_THINKING_PREFIX} ", style=STYLES["reasoning"])
        text.append(content, style=STYLES["reasoning"])
        self.console.print(text)

    def _on_assistant_message(self, event: dict[str, Any]) -> None:
        content = event.get("content", "")
        if not isinstance(content, str):
            return
        # If the message was streamed delta-by-delta, render() already closed
        # the open line; finalise without re-printing (would duplicate).
        if self._streamed_message:
            self._streamed_message = False
            return
        # No in-place re-render: when deltas are absent this is the one and
        # only print for the final message.
        self.console.print(content)

    def _on_tool_call(self, event: dict[str, Any]) -> None:
        tool_name = event.get("tool_name", "")
        arguments = event.get("arguments")
        self.summary.record_tool_call(tool_name=str(tool_name), arguments=arguments)

        if tool_name == "skill":
            # Magenta highlight; pull the skill name out of arguments.
            skill_name = ""
            if isinstance(arguments, dict):
                raw = arguments.get("skill")
                if isinstance(raw, str):
                    skill_name = raw
            text = Text()
            text.append("◇ ", style=STYLES["skill"])
            text.append("skill ", style=STYLES["meta"])
            if skill_name:
                text.append(skill_name, style=STYLES["skill"])
            else:
                text.append("(unknown)", style=STYLES["meta"])
            self.console.print(text)
            return

        # Default tool-call: cyan one-liner with name + args.
        text = Text()
        text.append("» ", style=STYLES["tool"])
        text.append(str(tool_name), style=STYLES["tool"])
        text.append("  ", style=STYLES["meta"])
        text.append(_format_arguments(arguments), style=STYLES["meta"])
        self.console.print(text)

    def _on_tool_result(self, event: dict[str, Any]) -> None:
        if self.verbosity < 1:
            return  # Silent at default verbosity.
        success = bool(event.get("success", False))
        size = event.get("result_size_chars")
        err = event.get("error")
        text = Text()
        if success:
            text.append("← ", style=STYLES["success"])
            text.append("result", style=STYLES["meta"])
            if size is not None:
                text.append(f"  ({size} chars)", style=STYLES["meta"])
        else:
            text.append("← ", style=STYLES["error"])
            text.append("error", style=STYLES["error"])
            if isinstance(err, dict):
                msg = err.get("message")
                if msg:
                    text.append(f"  {msg}", style=STYLES["error"])
        self.console.print(text)

    def _on_usage_tokens(self, event: dict[str, Any]) -> None:
        model = event.get("model")
        tokens_in = int(event.get("input", 0) or 0)
        tokens_out = int(event.get("output", 0) or 0)
        self.summary.record_usage(
            model=model if isinstance(model, str) else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            billing=BillingSample.from_event(event),
        )
        # No live ticker — accumulated silently. The frozen iteration
        # Panel surfaces the totals at iteration end.

    def _on_tool_permission_requested(self, event: dict[str, Any]) -> None:
        # Silent at default; raw-dumped at -vvv via the main dispatcher.
        return

    def _on_tool_permission_denied(self, event: dict[str, Any]) -> None:
        tool_name = event.get("tool_name", "")
        reason = event.get("reason", "")
        text = Text()
        text.append("⊘ ", style=STYLES["warning"])
        text.append("denied ", style=STYLES["warning"])
        text.append(str(tool_name), style=STYLES["warning"])
        if reason:
            text.append(f"  ({reason})", style=STYLES["meta"])
        self.console.print(text)

    def _on_session_created(self, event: dict[str, Any]) -> None:
        # Silent at default; raw-dumped at -vvv.
        return

    def _on_session_idle(self, event: dict[str, Any]) -> None:
        # Silent at default; raw-dumped at -vvv.
        return

    def _on_session_deleted(self, event: dict[str, Any]) -> None:
        # Silent at default; raw-dumped at -vvv.
        return

    # -- internal helpers --------------------------------------------------

    def _raw_dump(self, event: dict[str, Any]) -> None:
        """Render an event as a single dim line for ``-vvv`` mode."""
        text = Text()
        et = event.get("type", "?")
        text.append("· ", style=STYLES["meta"])
        text.append(str(et), style=STYLES["meta"])
        # Append a compact representation of the remaining fields.
        remainder = {k: v for k, v in event.items() if k != "type"}
        if remainder:
            text.append("  ", style=STYLES["meta"])
            text.append(repr(remainder), style=STYLES["meta"])
        self.console.print(text)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


_MAX_ARG_DISPLAY: int = 200


def _format_arguments(arguments: Any) -> str:
    """Format ``arguments`` for the cyan tool-call one-liner.

    The scrubber has already truncated oversize argument bundles to the
    literal ``<truncated: N chars>`` string; the renderer just prints
    whatever it receives. A dict is rendered as ``key=value`` pairs
    (compact, scrollback-friendly). Strings, lists, and other types are
    coerced to ``str()``.
    """
    if isinstance(arguments, dict):
        if not arguments:
            return "(no args)"
        parts = [f"{k}={_short_repr(v)}" for k, v in arguments.items()]
        joined = "  ".join(parts)
        if len(joined) > _MAX_ARG_DISPLAY:
            joined = joined[: _MAX_ARG_DISPLAY] + "…"
        return joined
    if arguments is None:
        return "(no args)"
    return str(arguments)


def _short_repr(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


# ---------------------------------------------------------------------------
# Dispatch table — keyed on event-type literals from ``git_loopy.events``
# ---------------------------------------------------------------------------


# A plain dict beats ``match/case`` for forward-compat: unknown types
# slip through to the ``_raw_dump`` path at high verbosity.
_HANDLERS: dict[str, Callable[[Renderer, dict[str, Any]], None]] = {
    WRAPPER_RUN_START: Renderer._on_run_start,
    WRAPPER_RUN_END: Renderer._on_run_end,
    WRAPPER_ITERATION_START: Renderer._on_iteration_start,
    WRAPPER_ITERATION_END: Renderer._on_iteration_end,
    WRAPPER_CONTRIBUTION_START: Renderer._on_contribution_start,
    WRAPPER_CONTRIBUTION_END: Renderer._on_contribution_end,
    WRAPPER_AFK_READY_COLLECTED: Renderer._on_afk_ready_collected,
    WRAPPER_POOL_EXCLUDED: Renderer._on_pool_excluded,
    WRAPPER_PARALLEL_SERIAL_FALLBACK: Renderer._on_parallel_serial_fallback,
    WRAPPER_SERIAL_REQUESTED: Renderer._on_serial_requested,
    WRAPPER_CHECKPOINT_RECORDED: Renderer._on_checkpoint_recorded,
    WRAPPER_COMMIT_RECORDED: Renderer._on_commit_recorded,
    WRAPPER_CONCURRENCY_CHANGED: Renderer._on_concurrency_changed,
    WRAPPER_PUSH_RECORDED: Renderer._on_push_recorded,
    WRAPPER_AUTO_CLOSE: Renderer._on_auto_close,
    WRAPPER_PR_ADVANCED: Renderer._on_pr_advanced,
    WRAPPER_STRIKE: Renderer._on_strike,
    WRAPPER_ASK_USER_ATTEMPTED: Renderer._on_ask_user_attempted,
    ASSISTANT_REASONING: Renderer._on_assistant_reasoning,
    ASSISTANT_MESSAGE: Renderer._on_assistant_message,
    TOOL_CALL: Renderer._on_tool_call,
    TOOL_RESULT: Renderer._on_tool_result,
    USAGE_TOKENS: Renderer._on_usage_tokens,
    TOOL_PERMISSION_REQUESTED: Renderer._on_tool_permission_requested,
    TOOL_PERMISSION_DENIED: Renderer._on_tool_permission_denied,
    SESSION_CREATED: Renderer._on_session_created,
    SESSION_IDLE: Renderer._on_session_idle,
    SESSION_DELETED: Renderer._on_session_deleted,
}
