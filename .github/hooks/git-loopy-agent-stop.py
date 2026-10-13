#!/usr/bin/env python3
"""Implement the route-request contract described by ADR-0008."""
from __future__ import annotations

import atexit
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

# Re-blocking an unconfirmed request has to stop somewhere. The runtime permits
# 8 consecutive blocks and then exits without saying why, so the chain's own cap
# trips first and names what it abandoned — see ADR-0004.
MAX_ROUTE_ATTEMPTS = 3

LEDGER_RELATIVE_PATH = os.path.join(".git-loopy", "subagents.jsonl")


def decision(reason: str, **details: object) -> None:
    print(json.dumps({"decision": "allow", "reason": reason, **details}, separators=(",", ":")))


def repository_root(cwd: object) -> str | None:
    if not isinstance(cwd, str) or not cwd:
        return None

    result = subprocess.run(
        ["git", "-C", cwd, "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return line.removeprefix("worktree ")
    return None


def acquire_lock(lock_dir: str) -> bool:
    deadline = time.monotonic() + 1
    while True:
        try:
            os.mkdir(lock_dir)
        except FileExistsError:
            if lock_is_stale(lock_dir):
                reclaimed = f"{lock_dir}.reclaim.{os.getpid()}"
                try:
                    os.replace(lock_dir, reclaimed)
                    os.unlink(os.path.join(reclaimed, "pid"))
                    os.rmdir(reclaimed)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
            continue

        start = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "UTC"},
        ).stdout.split()
        with open(os.path.join(lock_dir, "pid"), "w", encoding="utf-8") as owner:
            owner.write(f"{os.getpid()}\t{' '.join(start)}\n")
        return True


def lock_is_stale(lock_dir: str) -> bool:
    try:
        with open(os.path.join(lock_dir, "pid"), encoding="utf-8") as owner:
            pid_text, owner_start = owner.read().rstrip("\n").split("\t", 1)
            pid = int(pid_text)
    except (FileNotFoundError, ValueError):
        try:
            stale_after = int(os.environ.get("CHAIN_LOCK_STALE_SECONDS", "300"))
        except ValueError:
            return False
        try:
            return stale_after >= 0 and time.time() - os.stat(lock_dir).st_mtime >= stale_after
        except FileNotFoundError:
            return False

    try:
        if pid <= 0:
            raise ProcessLookupError
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    current_start = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": "UTC"},
    ).stdout.split()
    return " ".join(current_start) != owner_start


def release_lock(lock_dir: str) -> None:
    try:
        os.unlink(os.path.join(lock_dir, "pid"))
        os.rmdir(lock_dir)
    except FileNotFoundError:
        pass


def cleanup_replacements(ledger_path: str) -> None:
    """Remove replacement files left by a helper interrupted after creation."""
    for replacement in glob.glob(os.path.join(os.path.dirname(ledger_path), ".subagents.*")):
        try:
            os.unlink(replacement)
        except FileNotFoundError:
            pass


def read_ledger(ledger_path: str) -> list | None:
    try:
        with open(ledger_path, encoding="utf-8") as ledger:
            return [json.loads(line) for line in ledger if line.strip()]
    except (OSError, json.JSONDecodeError):
        return None


def write_ledger(ledger_path: str, rows: list) -> bool:
    """Replace the ledger in one atomic step.

    A half-written ledger is worse than no write at all: the next hook reads it
    to decide, so a truncated file looks like a ledger with no completed run.
    The replacement is built beside the ledger and renamed over it, so an
    interrupted helper leaves the previous ledger whole.
    """
    replacement = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(ledger_path),
            prefix=".subagents.",
            delete=False,
        ) as temporary:
            replacement = temporary.name
            for row in rows:
                temporary.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(replacement, ledger_path)
        return True
    except OSError:
        if replacement is not None:
            try:
                os.unlink(replacement)
            except OSError:
                pass
        return False


def owed_a_route(row: object) -> bool:
    """A finished run the chain still owes a route.

    Neither a routed row nor an abandoned one is owed anything: the first was
    confirmed, and the second gave up under a reason that named it.
    """
    return (
        isinstance(row, dict)
        and bool(row.get("finish_time"))
        and row.get("outcome") != "reclaimed"
        and not row.get("routed")
        and not row.get("route_abandoned")
    )


def route_attempts(row: dict) -> int:
    """How many times a route has been asked for on this row.

    The count is the request itself, because the helper writes it whatever the
    payload carried. The request *time* is only provenance: it comes from
    `timestamp`, which ADR-0005 keeps optional because nothing read it, so a
    request resting on it would be unconfirmable whenever it is absent.
    """
    attempts = row.get("route_attempts")
    return attempts if isinstance(attempts, int) and attempts > 0 else 0


def awaiting_confirmation(row: object) -> bool:
    return owed_a_route(row) and route_attempts(row) > 0


def requested_by(row: dict) -> list:
    sessions = row.get("route_requested_by")
    if not isinstance(sessions, list):
        return []
    return [session for session in sessions if isinstance(session, str) and session]


def confirm_route_request(payload: dict, ledger_path: str | None) -> None:
    """Promote a pending route request to a routed fact, then stand aside.

    Nothing on this path can block. `stop_hook_active` never starts a fresh
    route, so an unreadable or locked ledger only means there is nothing to
    confirm — never a reason to hold the parent open again.
    """
    if ledger_path is None:
        decision("stop-hook-active")
        return

    lock_dir = ledger_path + ".lock"
    if not acquire_lock(lock_dir):
        decision("stop-hook-active")
        return
    atexit.register(release_lock, lock_dir)
    cleanup_replacements(ledger_path)

    rows = read_ledger(ledger_path)
    if rows is None:
        decision("stop-hook-active")
        return

    session = payload.get("sessionId")
    requested = [
        row
        for row in rows
        if awaiting_confirmation(row)
        and isinstance(row.get("target"), str)
        and row["target"]
        and (
            not isinstance(session, str)
            or not session
            or not requested_by(row)
            or session in requested_by(row)
        )
    ]
    if not requested:
        decision("stop-hook-active")
        return

    for row in requested:
        row["routed"] = True
        row["routed_at"] = payload.get("timestamp")
    if not write_ledger(ledger_path, rows):
        decision("stop-hook-active")
        return

    targets = [
        row["target"]
        for row in requested
        if isinstance(row.get("target"), str) and row["target"]
    ]
    if len(targets) == 1:
        decision("stop-hook-active", confirmed=targets[0])
    else:
        decision("stop-hook-active", confirmed=targets)


try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    decision("invalid-agent-stop-payload")
    raise SystemExit(0)

if not isinstance(payload, dict):
    decision("invalid-agent-stop-payload")
    raise SystemExit(0)

root = repository_root(payload.get("cwd"))
ledger_path = None
if root is not None:
    candidate = os.path.join(root, LEDGER_RELATIVE_PATH)
    if os.path.exists(candidate):
        ledger_path = candidate

if payload.get("stop_hook_active") is True:
    confirm_route_request(payload, ledger_path)
    raise SystemExit(0)

if root is None:
    decision("repository-not-found")
    raise SystemExit(0)

if ledger_path is None:
    decision("no-ledger")
    raise SystemExit(0)

lock_dir = ledger_path + ".lock"
if not acquire_lock(lock_dir):
    decision("ledger-busy")
    raise SystemExit(0)
atexit.register(release_lock, lock_dir)
cleanup_replacements(ledger_path)

rows = read_ledger(ledger_path)
if rows is None:
    decision("invalid-ledger")
    raise SystemExit(0)

unrouted = [row for row in rows if owed_a_route(row)]
if not unrouted:
    decision("no-unrouted-completion")
    raise SystemExit(0)

targets = [
    row["target"]
    for row in unrouted
    if isinstance(row.get("target"), str) and row["target"]
]
if not targets:
    decision("invalid-completed-row")
    raise SystemExit(0)

abandoned = [
    row for row in unrouted
    if isinstance(row.get("target"), str)
    and row["target"]
    and route_attempts(row) >= MAX_ROUTE_ATTEMPTS
]
for row in abandoned:
    row["route_abandoned"] = True
    row["route_abandoned_at"] = payload.get("timestamp")

pending = [
    row
    for row in unrouted
    if row not in abandoned
    and isinstance(row.get("target"), str)
    and row["target"]
]
if not pending:
    if not write_ledger(ledger_path, rows):
        decision("ledger-update-failed")
        raise SystemExit(0)
    abandoned_targets = [row["target"] for row in abandoned]
    if len(abandoned_targets) == 1:
        decision("route-abandoned", target=abandoned_targets[0])
    else:
        decision("route-abandoned", targets=abandoned_targets)
    raise SystemExit(0)

for row in pending:
    row["route_attempts"] = route_attempts(row) + 1
    # The attempt count is the request. The timestamp is provenance only and
    # may be absent, so it must never control whether a request is confirmable.
    requested_at = payload.get("timestamp")
    if requested_at and not row.get("route_requested_at"):
        row["route_requested_at"] = requested_at
    session = payload.get("sessionId")
    if isinstance(session, str) and session and session not in requested_by(row):
        row["route_requested_by"] = requested_by(row) + [session]
if not write_ledger(ledger_path, rows):
    decision("ledger-update-failed")
    raise SystemExit(0)

requested_targets = [row["target"] for row in pending]
abandoned_targets = [row["target"] for row in abandoned]
if abandoned_targets:
    abandoned_text = ", ".join(abandoned_targets)
    reason = f"Route abandoned for {abandoned_text}. "
else:
    reason = ""
if len(requested_targets) == 1:
    reason += "A completed run is unrouted. Run /next now."
else:
    reason += (
        f"{len(requested_targets)} completed runs are unrouted. "
        "Run /next now and refill every freed slot."
    )
print(
    json.dumps(
        {
            "decision": "block",
            "reason": reason,
            "targets": requested_targets,
        },
        separators=(",", ":"),
    )
)
