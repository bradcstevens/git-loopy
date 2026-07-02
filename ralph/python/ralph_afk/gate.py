"""``ralph_afk.gate`` — the runner-side Integration gate seam (ADR-0009).

In **Parallel mode** (ADR-0008) each **Lane** finishes on its own branch and
**Integration** (ADR-0009) merges those branches into base one at a time,
re-running the target repo's own ``AGENTS.md`` **feedback loops** after each merge
as the *load-bearing quality gate*. The serial loop never runs the feedback loops
itself — the agent runs them inside its own session — but Integration has no agent
in the loop, so the **runner** must run them and decide green/red. This module is
that seam.

Like ``ralph_afk.git`` / ``ralph_afk.gh``, the gate is a **real seam**: callers
hold a :class:`GateRunner` (an injectable ``@runtime_checkable`` Protocol) rather
than calling a module function, so later Integration slices (#62/#63) and their
tests substitute one object — the production :class:`AgentsMdGateRunner` or the
scripted ``tests.fakes.FakeGateRunner`` — through the ``ralph_afk.loop._make_gate_runner``
factory, exactly like the git/gh/client factories.

Public surface:

* :exc:`GateError` — the gate could not be *run at all* (no ``AGENTS.md`` in the
  worktree, or it declares no runnable feedback loops). Distinct from a *red* gate
  (a loop that ran and failed), which is reported as a :class:`GateResult`, never
  raised — so ``passed``/``failure`` is the loop-outcome channel and :exc:`GateError`
  is the "cannot gate" channel.
* :class:`FeedbackLoop` — a frozen ``(name, command)`` row parsed from the table.
  :attr:`~FeedbackLoop.runnable` screens out empty and still-``<PLACEHOLDER>`` rows
  from the ``AGENTS.template.md`` starting point.
* :class:`LoopFailure` — the detail of the first loop that went red
  (``name`` / ``command`` / ``returncode`` / bounded ``output_tail``).
* :class:`GateResult` — ``passed`` plus the loop names that ``ran`` and, when red,
  the :class:`LoopFailure`. Build with :meth:`GateResult.green` / :meth:`GateResult.red`.
* :class:`GateRunner` — the injectable Protocol: ``run(worktree) -> GateResult``.
* :class:`AgentsMdGateRunner` — the production adapter: read ``<worktree>/AGENTS.md``,
  parse its ``## Feedback loops`` table, and run the runnable commands *in that
  worktree*, **fail-fast** on the first non-zero exit.
* :func:`parse_feedback_loops` — the pure table parser, separately tested.

Design notes:

* **``AGENTS.md`` is the single source of truth for the commands.** The runner does
  not carry its own hard-coded loop list; it reads the same table the human keeps in
  sync with CI (``templates/AGENTS.template.md`` "Feedback loops"). Commands are run
  through the shell (``shell=True``) exactly as an operator/agent would type them —
  the operator already owns and trusts their repo's ``AGENTS.md``, the same trust
  model as the agent running those commands inside its own session.
* **Fail-fast.** The gate stops at the first red loop — a gate needs one red to fail,
  and stopping early avoids paying for slower downstream loops once the merge is known
  bad. :attr:`GateResult.ran` records exactly the loops attempted (ending at the red).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Protocol, Sequence, runtime_checkable

__all__ = [
    "GateError",
    "FeedbackLoop",
    "LoopFailure",
    "GateResult",
    "GateRunner",
    "AgentsMdGateRunner",
    "parse_feedback_loops",
]

_AGENTS_FILENAME: Final[str] = "AGENTS.md"
_OUTPUT_TAIL_LIMIT: Final[int] = 2000

# The `## Feedback loops` heading (any heading level, case-insensitive).
_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^#{1,6}\s+feedback loops\s*$", re.IGNORECASE
)
# Any markdown heading — ends the feedback-loops section.
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s")
# A still-unfilled `AGENTS.template.md` placeholder, e.g. `<PM>` / `<IAC_WHAT_IF_COMMAND>`.
# Deliberately UPPER_SNAKE-only so a real command containing `<html>` or a `< input`
# redirect is not mistaken for a placeholder.
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"<[A-Z][A-Z0-9_]*>")
# Split a markdown table row on unescaped pipes (a literal pipe in a cell is `\|`).
_UNESCAPED_PIPE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\\)\|")
# A separator row cell: dashes with optional alignment colons.
_SEPARATOR_CELL_RE: Final[re.Pattern[str]] = re.compile(r":?-+:?")


class GateError(RuntimeError):
    """Raised when the gate cannot be run at all.

    Two causes: the worktree has no ``AGENTS.md``, or the ``AGENTS.md`` declares no
    *runnable* feedback loops (no ``## Feedback loops`` table, or every row is empty
    or a still-``<PLACEHOLDER>`` template stub). This is distinct from a **red** gate
    — a loop that ran and exited non-zero — which is a :class:`GateResult` with
    ``passed=False``, never an exception. Callers (Integration, #62/#63) decide how to
    treat a :exc:`GateError`; the safe default is "cannot gate, so do not land".
    """


@dataclass(frozen=True)
class FeedbackLoop:
    """One ``(name, command)`` row of the ``AGENTS.md`` ``## Feedback loops`` table.

    Attributes:
        name: The loop's label (the table's "Loop" column), e.g. ``"Unit tests"``.
        command: The shell command to run (the "Command" column), backticks stripped.
    """

    name: str
    command: str

    @property
    def runnable(self) -> bool:
        """Whether this loop has a concrete command the runner can execute.

        Screens out empty commands and rows still carrying an ``AGENTS.template.md``
        ``<PLACEHOLDER>`` stub (a fresh repo that has not filled the table in yet).
        """
        return bool(self.command) and _PLACEHOLDER_RE.search(self.command) is None


@dataclass(frozen=True)
class LoopFailure:
    """The detail of the first feedback loop that went red.

    Attributes:
        name: The failing loop's label.
        command: The command that was run.
        returncode: The command's non-zero exit code (``127`` when the command
            itself was not found).
        output_tail: A bounded tail of the command's combined stdout+stderr, for a
            breadcrumb comment / Log without unbounded output.
    """

    name: str
    command: str
    returncode: int
    output_tail: str


@dataclass(frozen=True)
class GateResult:
    """The outcome of running the feedback loops in one worktree.

    Attributes:
        passed: ``True`` iff every runnable loop exited zero.
        ran: The loop names actually attempted, in order. On a red gate this ends at
            the first failing loop (fail-fast); on green it is every runnable loop.
        failure: The first red loop's detail, or ``None`` on green. Invariant:
            ``passed`` is ``True`` exactly when ``failure`` is ``None``.
    """

    passed: bool
    ran: tuple[str, ...] = ()
    failure: LoopFailure | None = None

    @classmethod
    def green(cls, ran: Iterable[str]) -> GateResult:
        """A passing gate over the given loop names."""
        return cls(passed=True, ran=tuple(ran), failure=None)

    @classmethod
    def red(cls, ran: Iterable[str], failure: LoopFailure) -> GateResult:
        """A failing gate: ``ran`` ends at ``failure``'s loop (fail-fast)."""
        return cls(passed=False, ran=tuple(ran), failure=failure)


@runtime_checkable
class GateRunner(Protocol):
    """Runner-side Integration gate: run a worktree's feedback loops, report pass/fail.

    The injectable seam (ADR-0009). Production is :class:`AgentsMdGateRunner`; tests
    script ``tests.fakes.FakeGateRunner``. ``@runtime_checkable`` so both satisfy it
    structurally (``isinstance(runner, GateRunner)``).
    """

    def run(self, worktree: Path) -> GateResult:
        """Run the feedback loops for ``worktree`` and return the :class:`GateResult`."""
        ...


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells, honouring escaped ``\\|``."""
    parts = _UNESCAPED_PIPE_RE.split(line.strip())
    # Drop the empty cells produced by the leading/trailing border pipes.
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [part.replace("\\|", "|").strip() for part in parts]


def _is_separator(cells: Sequence[str]) -> bool:
    """Whether a split row is a table separator (``| --- | :--: |``)."""
    return bool(cells) and all(
        _SEPARATOR_CELL_RE.fullmatch(cell) is not None for cell in cells
    )


def _strip_backticks(cell: str) -> str:
    """Strip the ``` `command` ``` backtick wrapper the convention uses."""
    return cell.strip("`").strip()


def parse_feedback_loops(markdown: str) -> list[FeedbackLoop]:
    """Parse the ``## Feedback loops`` table out of an ``AGENTS.md`` body.

    Finds the (case-insensitive) ``Feedback loops`` heading, then the first table in
    that section, locates its "Command" and "Loop" columns *by header name* (so a
    reordered table still parses), and returns one :class:`FeedbackLoop` per data row.
    Scoped to the section — a table under a later heading is ignored. Returns ``[]``
    when there is no such section or table; the caller screens
    :attr:`FeedbackLoop.runnable`.

    Args:
        markdown: The full text of an ``AGENTS.md`` file.

    Returns:
        The parsed rows in document order (including any non-runnable placeholder rows).
    """
    lines = markdown.splitlines()
    index = 0
    count = len(lines)
    while index < count and _SECTION_RE.match(lines[index]) is None:
        index += 1
    if index >= count:
        return []
    index += 1  # step past the heading into the section body

    loops: list[FeedbackLoop] = []
    header_seen = False
    name_idx = 0
    cmd_idx: int | None = None
    while index < count:
        line = lines[index]
        if _HEADING_RE.match(line) is not None:
            break  # the next heading ends the section
        if "|" in line:
            cells = _split_row(line)
            if not header_seen:
                lowered = [cell.lower() for cell in cells]
                if "command" in lowered:
                    cmd_idx = lowered.index("command")
                    name_idx = lowered.index("loop") if "loop" in lowered else 0
                    header_seen = True
                index += 1
                continue
            if _is_separator(cells):
                index += 1
                continue
            if cmd_idx is not None and len(cells) > cmd_idx:
                name = cells[name_idx] if len(cells) > name_idx else ""
                loops.append(
                    FeedbackLoop(name=name, command=_strip_backticks(cells[cmd_idx]))
                )
            index += 1
            continue
        # A non-table line: prose before the table is skipped; a blank/prose line
        # after the table ends it.
        if header_seen:
            break
        index += 1
    return loops


def _tail(text: str, limit: int) -> str:
    """Return a bounded, stripped tail of ``text`` for a failure breadcrumb."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return "..." + stripped[-limit:]


class AgentsMdGateRunner:
    """Production :class:`GateRunner`: run a worktree's ``AGENTS.md`` feedback loops.

    Reads ``<worktree>/AGENTS.md``, parses its ``## Feedback loops`` table, and runs
    each **runnable** loop's command through the shell with ``cwd`` set to the
    worktree, **fail-fast** on the first non-zero exit. This is the load-bearing gate
    the runner runs at **Integration** (ADR-0009) and the "loops green" success check
    the later auto-resolution agent (#63) uses.
    """

    def __init__(
        self,
        *,
        agents_filename: str = _AGENTS_FILENAME,
        output_tail_limit: int = _OUTPUT_TAIL_LIMIT,
    ) -> None:
        self._agents_filename = agents_filename
        self._output_tail_limit = output_tail_limit

    def run(self, worktree: Path) -> GateResult:
        """Run the worktree's feedback loops and report pass/fail.

        Args:
            worktree: The directory to gate — during Integration this is the base
                worktree with the Lane's branch merged in.

        Returns:
            A green :class:`GateResult` when every runnable loop exits zero, or a red
            one carrying the first :class:`LoopFailure`.

        Raises:
            GateError: If the worktree has no ``AGENTS.md`` or it declares no runnable
                feedback loops (a "cannot gate" condition, distinct from a red gate).
        """
        worktree = Path(worktree)
        agents_path = worktree / self._agents_filename
        try:
            text = agents_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise GateError(
                f"no {self._agents_filename} in worktree {worktree}"
            ) from exc

        loops = [loop for loop in parse_feedback_loops(text) if loop.runnable]
        if not loops:
            raise GateError(
                f"no runnable feedback loops declared in {agents_path}"
            )

        ran: list[str] = []
        for loop in loops:
            ran.append(loop.name)
            completed = subprocess.run(
                loop.command,
                shell=True,
                cwd=str(worktree),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if completed.returncode != 0:
                combined = (completed.stdout or "") + (completed.stderr or "")
                return GateResult.red(
                    ran,
                    LoopFailure(
                        name=loop.name,
                        command=loop.command,
                        returncode=completed.returncode,
                        output_tail=_tail(combined, self._output_tail_limit),
                    ),
                )
        return GateResult.green(ran)
