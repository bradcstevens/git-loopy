"""Internal child entrypoint for detached TTY Runs."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Sequence

from git_loopy import cli as cli_module
from git_loopy.run_sidecar import decode_detached_run_spec

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(
            "usage: python -m git_loopy.run_child <encoded-detached-run-spec>",
            file=sys.stderr,
        )
        return 2
    spec = decode_detached_run_spec(args[0])
    started_at = datetime.fromtimestamp(
        spec.started_at_epoch_ms / 1000.0,
        tz=timezone.utc,
    )
    return asyncio.run(
        cli_module._drive_line_printer(
            spec.config,
            rate_card=spec.rate_card,
            staircase=spec.staircase,
            run_id=spec.run_id,
            started_at=started_at,
            mirror_diagnostics_to_stderr=False,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
