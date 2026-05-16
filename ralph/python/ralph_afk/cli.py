"""``ralph-afk`` console-script entry point.

This is the scaffold stub from issue #2. It parses the documented CLI/env
surface, resolves the repo root via ``git rev-parse --show-toplevel`` so the
runner is invokable from any cwd inside the repo, and exits cleanly. The
actual iteration driver (issue #10) is not wired yet — subsequent slices
fill in ``loop``, ``session``, ``cli`` proper, ``config``, and the deep and
shell modules they consume.

CLI surface — mirrors ``ralph/afk.sh`` rule-for-rule so that any wrapper
script around the bash runner ports directly:

* Positional ``<max-iterations>`` — ``0`` (or omitted) means unlimited.
* Env vars ``MODEL``, ``ISSUE_SOURCE``, ``MAX_NMT_STRIKES`` — recognised and
  echoed back; the loop slice acts on them.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_DEFAULT_MODEL = "claude-opus-4.7-xhigh"
_DEFAULT_ISSUE_SOURCE = "github"
_DEFAULT_MAX_NMT_STRIKES = "3"


def resolve_repo_root(start: Path | None = None) -> Path:
    """Resolve the enclosing git repository's top-level directory.

    Uses ``git rev-parse --show-toplevel`` so the runner works from any
    cwd inside the repo (not just the repo root). This is the parity
    requirement from the PRD: the bash runner is invoked as
    ``bash ralph/afk.sh`` from the repo root, and the Python variant must
    not impose the same constraint.

    Args:
        start: optional directory to run the ``git`` lookup from; defaults
            to the current working directory.

    Returns:
        Absolute ``Path`` to the repository root.

    Raises:
        RuntimeError: if ``git`` is not on PATH, or the current directory
            is not inside a git repository.
    """
    cwd = str(start) if start is not None else None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ralph-afk requires `git` on PATH (not found). "
            "Install git and re-run."
        ) from exc

    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "").strip().splitlines()[-1:]
        detail = stderr_tail[0] if stderr_tail else "(no stderr output)"
        raise RuntimeError(
            "ralph-afk must be invoked from inside a git repository "
            f"(`git rev-parse --show-toplevel` failed: {detail})."
        )

    return Path(completed.stdout.strip()).resolve()


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the ``ralph-afk`` console script."""
    parser = argparse.ArgumentParser(
        prog="ralph-afk",
        description=(
            "Autonomous AFK loop on the GitHub Copilot Python SDK. "
            "Peer variant of ralph/afk.sh — same wrapper contract, "
            "richer terminal UX."
        ),
        epilog=(
            "Environment variables (mirror ralph/afk.sh):\n"
            "  MODEL            Copilot CLI model id "
            f"(default: {_DEFAULT_MODEL}).\n"
            "  ISSUE_SOURCE     'github' (default) or 'prds' "
            "(legacy local-markdown).\n"
            "  MAX_NMT_STRIKES  Consecutive no-progress iterations before "
            f"aborting (default: {_DEFAULT_MAX_NMT_STRIKES}).\n"
            "\n"
            "Scaffold stub: this is the entry point shipped by issue #2. "
            "Subsequent slices wire the iteration driver."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "max_iterations",
        nargs="?",
        type=_parse_max_iterations,
        default=0,
        metavar="<max-iterations>",
        help=(
            "Cap the number of iterations (0 or omitted = unlimited; "
            "default: 0). Mirrors the positional arg accepted by "
            "ralph/afk.sh."
        ),
    )
    return parser


def _parse_max_iterations(raw: str) -> int:
    """Validate the positional ``<max-iterations>`` arg as a non-negative int."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"max_iterations must be a non-negative integer, got {raw!r}"
        ) from exc
    if value < 0:
        raise argparse.ArgumentTypeError(
            f"max_iterations must be non-negative, got {value}"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    """Entry point registered as the ``ralph-afk`` console script.

    Returns the process exit code. ``0`` on a successful scaffold-stub
    invocation (the iteration driver lands in issue #10).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        repo_root = resolve_repo_root()
    except RuntimeError as exc:
        print(f"ralph-afk: error: {exc}", file=sys.stderr)
        return 1

    model = os.environ.get("MODEL", _DEFAULT_MODEL)
    issue_source = os.environ.get("ISSUE_SOURCE", _DEFAULT_ISSUE_SOURCE)
    max_nmt_strikes = os.environ.get(
        "MAX_NMT_STRIKES", _DEFAULT_MAX_NMT_STRIKES
    )

    if issue_source not in {"github", "prds"}:
        print(
            "ralph-afk: error: ISSUE_SOURCE must be 'github' or 'prds' "
            f"(got {issue_source!r}).",
            file=sys.stderr,
        )
        return 2

    print(
        "ralph-afk scaffold stub (issue #2). "
        f"repo_root={repo_root} "
        f"max_iterations={args.max_iterations} "
        f"MODEL={model} "
        f"ISSUE_SOURCE={issue_source} "
        f"MAX_NMT_STRIKES={max_nmt_strikes}"
    )
    print(
        "Iteration driver not yet wired — see issues #3-#13 for the "
        "remaining slices.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - import-as-script convenience
    sys.exit(main())
