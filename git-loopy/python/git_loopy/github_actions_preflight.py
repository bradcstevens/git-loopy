"""Actions-job entry point for one remote green-base preflight (#462)."""

from __future__ import annotations

import sys
from pathlib import Path

from git_loopy.gate import AgentsMdGateRunner, GateError


def main() -> int:
    """Run the target repository's declared feedback loops from clean base."""
    try:
        result = AgentsMdGateRunner().run(Path.cwd())
    except GateError as exc:
        print(f"git-loopy: green-base preflight cannot run: {exc}", file=sys.stderr)
        return 1
    if result.passed:
        return 0
    assert result.failure is not None
    print(
        f"git-loopy: green-base preflight {result.failure.name!r} "
        f"{result.failure.summary}: {result.failure.output_tail}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
