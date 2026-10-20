"""Small, bounded execution-API messages; only the guest proves Run liveness."""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from git_loopy.git import SubprocessGitClient
from git_loopy.run_discovery import (
    DiscoveredRun, RunLiveness, RunTargetError, discover_runs, resolve_run,
)
from git_loopy.stop_request import StopSubmission, latched_operator_stage

GUEST_REPOSITORY = Path("/var/lib/git-loopy/repository")
CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class RemoteSnapshot:
    run: DiscoveredRun
    chunk: bytes
    offset: int
    eof: bool
    submission: StopSubmission | None = None
    acknowledged: str | None = None

    def encode(self) -> str:
        return json.dumps({
            "schema_version": 1,
            "run_id": self.run.run_id,
            "started_at": self.run.started_at.isoformat(),
            "liveness": self.run.liveness.value,
            "trace_path": str(self.run.trace_path),
            "control_path": str(self.run.control_path),
            "chunk": base64.b64encode(self.chunk).decode("ascii"),
            "offset": self.offset,
            "eof": self.eof,
            "submission": None if self.submission is None else asdict(self.submission),
            "acknowledged": self.acknowledged,
        })

    @classmethod
    def decode(cls, text: str, *, run_id: str, offset: int) -> RemoteSnapshot:
        try:
            value = json.loads(text)
            chunk = base64.b64decode(value["chunk"], validate=True)
            if (
                value["schema_version"] != 1 or value["run_id"] != run_id
                or type(value["offset"]) is not int
                or value["offset"] != offset + len(chunk)
                or len(chunk) > CHUNK_SIZE or type(value["eof"]) is not bool
                or value["acknowledged"] not in (None, "drain", "cancel")
            ):
                raise ValueError
            started_at = datetime.fromisoformat(value["started_at"])
            if started_at.tzinfo is None:
                raise ValueError
            submission = value["submission"]
            if submission is not None:
                submission = StopSubmission(**submission)
                if (
                    not isinstance(submission.request_id, str)
                    or submission.stage not in ("drain", "cancel")
                    or type(submission.redelivered) is not bool
                ):
                    raise ValueError
            return cls(
                DiscoveredRun(
                    run_id, GUEST_REPOSITORY, started_at, RunLiveness(value["liveness"]),
                    Path(value["trace_path"]), Path(value["control_path"]), "remote",
                ),
                chunk, value["offset"], value["eof"], submission, value["acknowledged"],
            )
        except (ValueError, KeyError, TypeError):
            raise RunTargetError("Invalid remote Run response; no client operation confirmed.") from None


def inspect_run(
    repository: Path, run_id: str, *, offset: int = 0, request_id: str | None = None,
) -> RemoteSnapshot:
    """Observe or submit one durable Stop using the same machinery as local."""
    from git_loopy.stopcmd import _submit_preserving_identity

    if type(offset) is not int or offset < 0:
        raise RunTargetError("Invalid remote trace offset.")
    run = resolve_run(run_id, discover_runs(git=SubprocessGitClient(repository)))
    if run.run_id != run_id:
        raise RunTargetError("Remote execution requires an exact Run identity.")
    submission = None
    if request_id is not None:
        if run.liveness is not RunLiveness.LIVE:
            raise RunTargetError("The remote Run is not proven live; Stop was not submitted.")
        submission = _submit_preserving_identity(run.control_path, request_id)
    with run.trace_path.open("rb") as handle:
        if offset > handle.seek(0, 2):
            raise RunTargetError("The remote trace was truncated; observation cannot continue.")
        handle.seek(offset)
        chunk = handle.read(CHUNK_SIZE)
        eof = not handle.read(1)
    return RemoteSnapshot(
        run, chunk, offset + len(chunk), eof, submission,
        latched_operator_stage(run.trace_path),
    )


def main() -> int:
    try:
        request = json.loads(base64.b64decode(sys.argv[1], validate=True))
        snapshot = inspect_run(
            GUEST_REPOSITORY, request["run_id"], offset=request["offset"],
            request_id=request.get("request_id"),
        )
        print(snapshot.encode())
        return 0
    except (RunTargetError, OSError, ValueError, TypeError, KeyError, IndexError):
        print("Remote Run control failed; inspect the guest Run artifacts.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
