"""Report the next ADR number from freshly fetched main and open pull requests."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Mapping, Sequence

from git_loopy.gh import GhError, open_pr_added_files
from git_loopy.git import GitError, fetched_main_paths

_ADR_PATH = re.compile(r"docs/adr/([0-9]{4})-[^/]+\.md")


def main(
    argv: Sequence[str] | None = None,
    *,
    read_main: Callable[[], Sequence[str]] | None = None,
    read_pull_requests: Callable[[], Mapping[int, Sequence[str]]] | None = None,
) -> int:
    """Print the next free number and the remote evidence behind it."""
    argparse.ArgumentParser(
        prog="python -m git_loopy.adr_number",
        description=(
            "Report the next ADR number after fetching origin/main and reading "
            "the files open pull requests add. Run from the repository root."
        ),
    ).parse_args(argv)
    try:
        main_paths = (read_main or (lambda: fetched_main_paths("docs/adr/")))()
        pull_requests = (read_pull_requests or open_pr_added_files)()
    except (GitError, GhError, OSError) as exc:
        print(f"error: could not read ADR numbering evidence: {exc}", file=sys.stderr)
        return 2
    main_numbers = [
        int(match[1])
        for path in main_paths
        if (match := _ADR_PATH.fullmatch(path)) is not None
    ]
    highest_main = max(main_numbers, default=0)
    pending = {
        number: sorted(path for path in paths if _ADR_PATH.fullmatch(path))
        for number, paths in pull_requests.items()
    }
    pending = {number: paths for number, paths in pending.items() if paths}
    highest = highest_main
    for paths in pending.values():
        for path in paths:
            match = _ADR_PATH.fullmatch(path)
            assert match is not None
            highest = max(highest, int(match[1]))
    lines = [
        f"Next free ADR number: {highest + 1:04d}",
        f"Highest on origin/main: {highest_main:04d}" if main_numbers
        else "Highest on origin/main: none",
        "Open pull requests adding ADR files:",
    ]
    for number, paths in sorted(pending.items()):
        lines.append(f"  #{number}:")
        lines.extend(f"    {path}" for path in paths)
    if not pending:
        lines.append("  none")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
