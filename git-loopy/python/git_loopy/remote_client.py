"""Remote client transport; Run ownership never crosses into the client."""

from __future__ import annotations

import sys
import time
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Callable

from git_loopy.remote_control import RemoteSnapshot
from git_loopy.remote_launch import RemoteProvider
from git_loopy.remote_runs import RemoteRunReceipt
from git_loopy.run_discovery import RunLiveness, RunTargetError
from git_loopy.run_control import RunControlArtifact
from git_loopy.stop_request import StopRequest, StopRequestError, _require_request_id, read_stop_requests


def read_snapshot(
    provider: RemoteProvider, receipt: RemoteRunReceipt, *,
    offset: int = 0, request_id: str | None = None,
) -> RemoteSnapshot:
    snapshot = provider.inspect(receipt, offset=offset, request_id=request_id)
    if snapshot is None or snapshot.run.liveness is RunLiveness.UNKNOWN:
        raise RunTargetError(
            f"The liveness of remote Run {receipt.run_id} is unknown; "
            "no operation is confirmed and the Run will not be resumed."
        )
    return snapshot


def submit_remote_stop(
    provider: RemoteProvider, receipt: RemoteRunReceipt, request_id: str,
) -> RemoteSnapshot:
    try:
        _require_request_id(request_id)
    except StopRequestError as exc:
        raise RunTargetError(str(exc)) from None
    for attempt in range(3):
        try:
            snapshot = read_snapshot(provider, receipt, request_id=request_id)
            if snapshot.submission is None or snapshot.submission.request_id != request_id:
                raise RunTargetError("Remote Stop response did not identify this request.")
            return snapshot
        except RunTargetError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def request_remote_stop(
    provider: RemoteProvider, receipt: RemoteRunReceipt, *,
    request_id: str, timeout: float, output: Callable[[str], None], outbox: Path,
) -> int:
    from git_loopy.stopcmd import _acknowledged, _submitted

    try:
        _require_request_id(request_id)
    except StopRequestError as exc:
        raise RunTargetError(str(exc)) from None
    relay_pending_stops(provider, receipt, outbox, output)
    # Print before the call: a lost execution response must not lose the
    # identity the operator needs to redeliver without escalating.
    output(f"Sending Stop request {request_id} for remote Run {receipt.run_id}.")
    snapshot = submit_remote_stop(provider, receipt, request_id)
    submission = snapshot.submission
    assert submission is not None
    output(_submitted(receipt.run_id, submission))
    deadline = time.monotonic() + timeout
    while True:
        if snapshot.acknowledged == "cancel" or snapshot.acknowledged == submission.stage:
            output(_acknowledged(receipt.run_id, submission, announced=snapshot.acknowledged))
            return 0
        if time.monotonic() >= deadline:
            print(
                f"git-loopy: Run {receipt.run_id} has not acknowledged {submission.stage} "
                f"within {timeout:g}s (request {request_id}). The request is unconfirmed. "
                "Redeliver with the same --request-id; this is not a finished Run.",
                file=sys.stderr,
            )
            return 1
        time.sleep(min(.25, max(0, deadline - time.monotonic())))
        snapshot = read_snapshot(provider, receipt, offset=snapshot.offset)


def _pending_stops(client: Path) -> list[StopRequest]:
    try:
        requests = read_stop_requests(client / "run.control")
    except FileNotFoundError:
        return []
    return [
        request for request in requests
        if not (client / "sent" / request.request_id).is_file()
    ]


def relay_pending_stops(
    provider: RemoteProvider, receipt: RemoteRunReceipt, outbox: Path,
    output: Callable[[str], None],
) -> None:
    """A later client redelivers abandoned requests, never a new identity."""
    try:
        try:
            clients = tuple(outbox.iterdir())
        except FileNotFoundError:
            return
        pending = [
            (request.seq, request.request_id, client)
            for client in clients if client.name.startswith("client-") and client.is_dir()
            for request in _pending_stops(client)
        ]
        for _, identity, client in sorted(pending):
            output(f"Sending Stop request {identity} for remote Run {receipt.run_id}.")
            submit_remote_stop(provider, receipt, identity)
            sent = client / "sent"
            sent.mkdir(exist_ok=True)
            (sent / identity).touch()
    except OSError:
        raise RunTargetError(
            f"Cannot read or mark the remote Stop outbox {outbox}; "
            "pending request identities are retained for redelivery."
        ) from None


@dataclass(frozen=True)
class RemoteAttachChannel:
    provider: RemoteProvider
    receipt: RemoteRunReceipt
    repo_root: Path

    def observe(self, *, output: Callable[[str], None] = print) -> int:
        from git_loopy.git import SubprocessGitClient
        from git_loopy.run_sidecar import observe_existing_run

        outbox = (
            SubprocessGitClient(self.repo_root).common_git_dir()
            / "git-loopy" / "remote-stops" / self.receipt.run_id
        )
        try:
            outbox.mkdir(parents=True, exist_ok=True)
            client = Path(mkdtemp(prefix="client-", dir=outbox))
            try:
                with TemporaryDirectory(prefix="git-loopy-attach-") as directory:
                    trace = Path(directory) / "remote.jsonl"
                    trace.touch()
                    with RunControlArtifact.acquire(client / "run.jsonl") as control:
                        mirror = _TraceMirror(
                            self.provider, self.receipt, trace, control, output, outbox,
                        )
                        mirror.refresh()
                        return observe_existing_run(
                            repository_root=self.repo_root, trace_path=trace,
                            control_path=control.path, run_id=self.receipt.run_id,
                            output=output, refresh=mirror.refresh,
                        )
            finally:
                if _pending_stops(client):
                    print(
                        f"git-loopy: Pending remote Stop requests retained under {outbox}; "
                        "attach or stop this Run again to redeliver their original identities.",
                        file=sys.stderr,
                    )
                elif not read_stop_requests(client / "run.control"):
                    shutil.rmtree(client)
        except OSError:
            raise RunTargetError(
                "Cannot maintain the remote Attach trace mirror; the Run was not stopped."
            ) from None


class _TraceMirror:
    """Client-only artifacts, never a second Run or a liveness authority."""

    def __init__(
        self, provider: RemoteProvider, receipt: RemoteRunReceipt, trace: Path,
        control: RunControlArtifact, output: Callable[[str], None], outbox: Path,
    ) -> None:
        self.provider, self.receipt = provider, receipt
        self.trace, self.control, self.output = trace, control, output
        self.outbox = outbox
        self.offset = 0
        self.pending = b""
        self.last_read = 0.0
        self.finished = False
        self.liveness = RunLiveness.UNKNOWN

    def refresh(self) -> None:
        if not self.finished and time.monotonic() - self.last_read >= .25:
            snapshot = read_snapshot(self.provider, self.receipt, offset=self.offset)
            self.liveness = snapshot.run.liveness
            self.offset = snapshot.offset
            self.pending += snapshot.chunk
            complete, separator, self.pending = self.pending.rpartition(b"\n")
            if separator:
                with self.trace.open("ab") as handle:
                    handle.write(complete + separator)
            self.last_read = time.monotonic() if snapshot.eof else 0
            if snapshot.eof and snapshot.run.liveness is RunLiveness.DEAD:
                self.finished = True
                self.control.close()
        if self.liveness is RunLiveness.LIVE:
            relay_pending_stops(self.provider, self.receipt, self.outbox, self.output)
