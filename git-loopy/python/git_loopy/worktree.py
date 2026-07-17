"""``git_loopy.worktree`` — the per-Lane worktree setup seam (#65, ADR-0008).

In **Parallel mode** (ADR-0008) each **Lane** works its issue in its own git
worktree, created in a sibling directory outside the repo. A fresh worktree has
the source but not the *environment* the feedback loops need — no installed
dependencies, no virtualenv — so before a Lane's agent session starts the runner
**prepares** that worktree. This module is that seam.

The knob is ``GIT_LOOPY_WORKTREE_SETUP``: when set, its command is run in each
freshly created Lane worktree; when unset, a **best-effort auto-detect** picks a
common install command for the project type (a ``uv.lock`` -> ``uv sync``, a
``package.json`` -> ``npm install``, and so on). A setup failure is **reported**,
never silently swallowed — the caller (the Wave orchestrator) surfaces it.

Like :mod:`git_loopy.gate`, the setup is a **real, injectable seam**: callers hold
a :class:`WorktreeSetup` (a ``@runtime_checkable`` Protocol) rather than calling a
module function, so the Wave orchestrator (#61) and its tests substitute one
object — the production :class:`CommandWorktreeSetup` or a scripted fake — through
the ``git_loopy.loop._make_worktree_setup`` factory, exactly like the git / gh /
gate factories.

Public surface:

* :class:`SetupResult` — the outcome of preparing one worktree: which
  ``command`` ran (``None`` == nothing configured *and* nothing detected, a
  no-op), its ``returncode``, and a bounded ``output_tail`` for a breadcrumb.
  :attr:`~SetupResult.ran` / :attr:`~SetupResult.passed` are the two channels a
  caller screens.
* :class:`WorktreeSetup` — the injectable Protocol: ``run(worktree) -> SetupResult``.
* :class:`CommandWorktreeSetup` — the production adapter: run the configured
  command (or the auto-detected fallback) *in the worktree*, capturing the outcome.
* :func:`detect_setup_command` — the pure auto-detect (marker file -> command),
  separately tested.

Design notes:

* **Configured wins over detected.** An explicit ``GIT_LOOPY_WORKTREE_SETUP`` is a
  human assertion of exactly how to prepare the worktree; the auto-detect is only
  the zero-config fallback so typical projects work without extra configuration.
* **Best-effort, not exhaustive.** The detector recognises the common lockfiles /
  manifests and prefers the more specific lockfile (``uv.lock`` over
  ``pyproject.toml``, ``package-lock.json`` over ``package.json``). It is a
  convenience, not a build system; an unrecognised project just gets a no-op and
  the operator sets ``GIT_LOOPY_WORKTREE_SETUP`` explicitly.
* **Shell execution.** Commands run through the shell (``shell=True``) exactly as
  an operator would type them — the same trust model as :mod:`git_loopy.gate`
  running a repo's ``AGENTS.md`` feedback loops.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Protocol, runtime_checkable

__all__ = [
    "SetupResult",
    "WorktreeSetup",
    "CommandWorktreeSetup",
    "detect_setup_command",
]

_OUTPUT_TAIL_LIMIT: Final[int] = 2000

#: Ordered ``(marker filename, install command)`` rows for the zero-config
#: auto-detect. First match wins, so **more specific markers come first**: a
#: lockfile (reproducible install) is listed ahead of the manifest it locks, and
#: an npm lockfile ahead of a bare ``package.json``. Kept deliberately small and
#: common — this is a best-effort convenience, not a build-system detector.
_DETECTORS: Final[tuple[tuple[str, str], ...]] = (
    ("uv.lock", "uv sync"),
    ("poetry.lock", "poetry install"),
    ("Pipfile.lock", "pipenv install --dev"),
    ("package-lock.json", "npm ci"),
    ("yarn.lock", "yarn install --frozen-lockfile"),
    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
    ("package.json", "npm install"),
    ("requirements.txt", "pip install -r requirements.txt"),
    ("pyproject.toml", "pip install -e ."),
    ("Gemfile", "bundle install"),
    ("go.mod", "go mod download"),
    ("Cargo.toml", "cargo fetch"),
)


@dataclass(frozen=True)
class SetupResult:
    """The outcome of preparing one Lane worktree.

    Attributes:
        command: The command that ran, or ``None`` when neither a configured
            ``GIT_LOOPY_WORKTREE_SETUP`` nor the auto-detect produced one (a
            no-op — the worktree needed no preparation the runner knows of).
        returncode: The command's exit code (``0`` on success; a no-op is ``0``).
        output_tail: A bounded tail of the command's combined stdout+stderr, for
            a diagnostic breadcrumb without unbounded output. Empty for a no-op.
    """

    command: str | None
    returncode: int = 0
    output_tail: str = ""

    @property
    def ran(self) -> bool:
        """Whether a setup command was actually run (``False`` for a no-op)."""
        return self.command is not None

    @property
    def passed(self) -> bool:
        """Whether preparation succeeded — a no-op passes, a run must exit zero."""
        return self.command is None or self.returncode == 0


@runtime_checkable
class WorktreeSetup(Protocol):
    """Prepare one Lane worktree before its agent session starts.

    The injectable seam (ADR-0008). Production is :class:`CommandWorktreeSetup`;
    tests script a fake. ``@runtime_checkable`` so both satisfy it structurally
    (``isinstance(setup, WorktreeSetup)``).
    """

    def run(self, worktree: Path) -> SetupResult:
        """Prepare ``worktree`` and return the :class:`SetupResult`."""
        ...


def _tail(text: str, limit: int) -> str:
    """Return a bounded, stripped tail of ``text`` for a failure breadcrumb."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return "..." + stripped[-limit:]


def detect_setup_command(worktree: Path) -> str | None:
    """Best-effort auto-detect an install command for ``worktree``'s project type.

    Scans :data:`_DETECTORS` in order and returns the command for the first marker
    file present at the worktree root, or ``None`` when nothing recognised is
    found (or the path does not exist). Pure and never raises — a missing worktree
    simply detects nothing.

    Args:
        worktree: The Lane worktree root to inspect.

    Returns:
        The install command to run, or ``None`` for "nothing to auto-run".
    """
    root = Path(worktree)
    for marker, command in _DETECTORS:
        if (root / marker).is_file():
            return command
    return None


class CommandWorktreeSetup:
    """Production :class:`WorktreeSetup`: run a command inside a Lane worktree.

    Runs the configured ``GIT_LOOPY_WORKTREE_SETUP`` command when one is given,
    otherwise the :func:`detect_setup_command` fallback, through the shell with
    ``cwd`` set to the worktree. Captures the outcome in a :class:`SetupResult`
    — a non-zero exit is reported (``passed=False``), not raised, so the caller
    decides how loudly to surface it.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        detector: Callable[[Path], str | None] = detect_setup_command,
        output_tail_limit: int = _OUTPUT_TAIL_LIMIT,
    ) -> None:
        self._command = command
        self._detector = detector
        self._output_tail_limit = output_tail_limit

    def run(self, worktree: Path) -> SetupResult:
        """Prepare ``worktree`` by running its setup command.

        Args:
            worktree: The freshly created Lane worktree to prepare.

        Returns:
            A :class:`SetupResult`: ``command=None`` (a passing no-op) when
            neither a configured command nor the detector yields one; otherwise
            the command that ran with its ``returncode`` and a bounded
            ``output_tail``.
        """
        worktree = Path(worktree)
        command = self._command or self._detector(worktree)
        if not command:
            return SetupResult(command=None)

        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        combined = (completed.stdout or "") + (completed.stderr or "")
        return SetupResult(
            command=command,
            returncode=completed.returncode,
            output_tail=_tail(combined, self._output_tail_limit),
        )
