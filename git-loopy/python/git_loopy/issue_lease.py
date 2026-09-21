"""The Lease: one issue, one Run (ADR-0033).

Two halves. :func:`inspect_lease` is the pure decision seam — it reads a
record and a clock and says absent, live or expired, and nothing more. Expiry
is not a fence. :class:`LeaseTransport` is the ref half: it takes, renews,
steals and releases a Lease by compare-and-swapping one ref per issue, and
:meth:`LeaseTransport.holds` is the fence every side effect must pass.
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from .git import GitError, is_stale_lease_rejection


_log = logging.getLogger(__name__)
_MAX_INTEGER = 2**53 - 1
_RUN_ID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_LEASE_REF_PREFIX = "refs/heads/git-loopy/leases/issue-"
_WRITE_ATTEMPTS = 4
_WRITE_BUDGET_SECONDS = 15.0
_PERMANENT_WRITE_FAILURE = re.compile(
    r"authentication failed|permission denied|access denied|repository not found|"
    r"certificate|host key verification failed|protected branch|"
    r"pre-receive hook declined|remote rejected|stale info",
    re.IGNORECASE,
)
_TRANSIENT_WRITE_FAILURE = re.compile(
    r"\b(?:HTTP\s+|requested URL returned error:\s*)(?:429|5\d\d)\b|"
    r"rate limit exceeded|secondary rate limit|too many requests|"
    r"could not resolve host|temporary failure in name resolution|"
    r"failed to connect|unable to connect|"
    r"(?:connection|operation) timed out|connection reset|connection closed|"
    r"remote end hung up unexpectedly|unexpected disconnect|"
    r"empty reply from server|network is unreachable",
    re.IGNORECASE,
)
#: The refusals that mean *this clone* may never write a Lease ref, whatever it
#: retries. Spelled out as the messages git and the forge actually emit — note
#: GitHub says "Permission to <repo> denied", not "permission denied" — because
#: this pattern turns the Lease off for a whole Run and must therefore never
#: match a fault that would have passed.
_WRITE_REFUSAL = re.compile(
    r"authentication failed|authentication is not possible|"
    r"permission denied|permission to .{0,120}? denied|access denied|"
    r"repository not found|does not appear to be a git repository|"
    r"protected branch|pre-receive hook declined|push declined|"
    r"refusing to allow|remote rejected|"
    r"you are not allowed to push|not authorized|403 forbidden|"
    r"certificate|host key verification failed",
    re.IGNORECASE,
)


def is_transient_lease_error(error: GitError) -> bool:
    """Recognize only recoverable transport/rate-limit/server write failures."""
    return bool(
        error.returncode != 127
        and not _PERMANENT_WRITE_FAILURE.search(error.stderr_tail)
        and _TRANSIENT_WRITE_FAILURE.search(error.stderr_tail)
    )


def is_permanent_lease_write_refusal(error: GitError) -> bool:
    """Recognize a refusal that will not pass: this clone may never write here.

    Narrower than "not transient", and deliberately its own pattern rather than
    :data:`_PERMANENT_WRITE_FAILURE`. That one answers "should this be retried?",
    where over-matching merely wastes an attempt; this one answers "should the
    Lease be turned off for the whole Run?", where over-matching disables the
    exclusivity. An unrecognized failure therefore stays unclassified on
    purpose — the caller's deny-by-default is the safe reading of a fault
    nobody has named — and only refusals git and GitHub state outright answer
    yes: bad credentials, no permission, no such repository, a rejecting
    ruleset or hook, an untrusted certificate or host key.

    A ``--force-with-lease`` rejection can never reach here. That is the one
    failure a Lease reads as another Run's *answer*, and
    :func:`~git_loopy.git.push_ref` returns ``False`` for it rather than
    raising, so no raised error carries it. Were it to leak in, this would read
    "someone holds the Lease" as "Leases do not work here", and turn the
    mechanism off at exactly the moment it is load-bearing.
    """
    return bool(
        error.returncode != 127
        and _WRITE_REFUSAL.search(error.stderr_tail)
        and not is_stale_lease_rejection(None, error.stderr_tail)
    )


def lease_ref(issue: int) -> str:
    """Return the ref that *is* the Lease for ``issue``.

    One ref per issue, so creating it is the compare-and-swap that admits
    exactly one Run. It sits inside git-loopy's reserved ``git-loopy/``
    branch namespace (:func:`~git_loopy.git.is_reserved_branch`) under
    ``refs/heads/`` deliberately, so an operator sees Leases in an ordinary
    branch listing rather than having to know a hidden namespace (ADR-0033).

    Raises:
        ValueError: If ``issue`` is not a positive whole number. A ref name is
            an identity, so a bad issue must never address some *other* ref.
    """
    if not _whole_number(issue, 1):
        raise ValueError("Lease ref: invalid issue")
    return f"{_LEASE_REF_PREFIX}{int(issue)}"


@dataclass(frozen=True)
class LeaseRecord:
    run_id: str
    issue: int
    repository: str
    claimed_at: int
    heartbeat_at: int
    ttl_seconds: int
    host: str
    pid: int


@dataclass(frozen=True)
class LeaseInspection:
    state: Literal["absent", "live", "expired"]
    record: LeaseRecord | None
    diagnostics: tuple[str, ...] = ()


def _whole_number(value: object, minimum: int) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= value <= _MAX_INTEGER
        and value == int(value)
    )


def _parse_record(raw: str, repository: str, issue: int) -> LeaseRecord | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    run_id, record_repository, host = (
        value.get("run_id"),
        value.get("repository"),
        value.get("host"),
    )
    if (
        not isinstance(run_id, str)
        or _RUN_ID.fullmatch(run_id) is None
        or not isinstance(record_repository, str)
        or _REPOSITORY.fullmatch(record_repository) is None
        or record_repository.lower() != repository.lower()
        or not isinstance(host, str)
        or not host
    ):
        return None
    for field, minimum in (
        ("issue", 1),
        ("claimed_at", 0),
        ("heartbeat_at", 0),
        ("ttl_seconds", 1),
        ("pid", 1),
    ):
        if not _whole_number(value.get(field), minimum):
            return None
    if value["issue"] != issue or value["heartbeat_at"] < value["claimed_at"]:
        return None
    return LeaseRecord(
        run_id=run_id,
        issue=int(value["issue"]),
        repository=record_repository,
        claimed_at=int(value["claimed_at"]),
        heartbeat_at=int(value["heartbeat_at"]),
        ttl_seconds=int(value["ttl_seconds"]),
        host=host,
        pid=int(value["pid"]),
    )


def render_record(record: LeaseRecord) -> str:
    """Render a Lease record as the commit message its ref points at.

    Key order is fixed and separators are compact so two renderings of one
    record are byte-identical, and a reader is the inverse of a writer.
    """
    return json.dumps(
        {
            "run_id": record.run_id,
            "issue": record.issue,
            "repository": record.repository,
            "claimed_at": record.claimed_at,
            "heartbeat_at": record.heartbeat_at,
            "ttl_seconds": record.ttl_seconds,
            "host": record.host,
            "pid": record.pid,
        },
        separators=(",", ":"),
        sort_keys=False,
    )


def inspect_lease(
    raw: str | None,
    *,
    now: int,
    repository: str,
    issue: int,
    skew_tolerance_seconds: int = 60,
) -> LeaseInspection:
    """Inspect one ref's message with an injected Unix-seconds clock.

    ``None`` denotes an absent ref, not an unreadable fetch. The transport must
    propagate read failures, never substitute absence. A malformed message is
    expired with a diagnostic; invalid caller context instead raises ValueError.
    Diagnostics are structural signals for the eventual caller to report.
    """
    for name, value, minimum in (
        ("now", now, 0),
        ("issue", issue, 1),
        ("skew_tolerance_seconds", skew_tolerance_seconds, 0),
    ):
        if not _whole_number(value, minimum):
            raise ValueError(f"Lease inspection: invalid {name}")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Lease inspection: invalid repository")
    if raw is None:
        return LeaseInspection("absent", None)
    record = _parse_record(raw, repository, issue)
    if record is None:
        return LeaseInspection("expired", None, ("malformed_record",))
    return LeaseInspection(
        "expired" if now - record.heartbeat_at > record.ttl_seconds else "live",
        record,
        ("clock_skew",) if record.claimed_at - now > skew_tolerance_seconds else (),
    )


class LeaseRefPort(Protocol):
    """The four git mechanics a Lease needs, as an injectable seam.

    Deliberately narrower than :class:`~git_loopy.git.GitClient`, which
    satisfies it structurally: a Lease touches refs and objects only, and
    never a worktree, an index or a checkout.
    """

    def probe_remote_ref(self, remote: str, ref: str) -> str | None:
        """Return a remote ref's SHA, or ``None`` when it is absent."""
        ...

    def fetch_commit_message(self, remote: str, sha: str) -> str:
        """Return an advertised commit's message, fetching the object first."""
        ...

    def write_orphan_commit(self, message: str) -> str:
        """Record ``message`` as a parentless commit and return its SHA."""
        ...

    def push_ref(
        self, remote: str, ref: str, sha: str | None, expected: str | None,
        *, timeout_seconds: float = 15.0,
    ) -> bool:
        """Compare-and-swap ``ref``; ``False`` on rejection, raise on transport."""
        ...


LEASE_ACTIONS = ("claim", "release", "fence")


def decide_lease_action(
    action: str,
    *,
    state: str,
    owner: str | None,
    run_id: str,
) -> str:
    """Decide what one Lease action may do, given what the ref currently says.

    The whole of a Lease's policy, as a pure function of an observation: no
    clock, no network, no state. Every member of the Runner family implements
    exactly this table, which is what makes "identical Lease decisions for
    every fixture case" a thing a fixture can pin rather than a hope.

    Two rules run through all four actions. **Only identity confers
    ownership** — a ref carrying another ``run_id`` is never ours to renew,
    release or write behind, whatever its expiry says. And **an unreadable
    record is expired and owned by nobody**, so it can never wedge an issue
    shut, and can never be mistaken for permission.

    Args:
        action: One of :data:`LEASE_ACTIONS`.
        state: ``absent``, ``live`` or ``expired``, from :func:`inspect_lease`.
        owner: The ``run_id`` on the ref, or ``None`` if absent or unreadable.
        run_id: The Run asking.

    Returns:
        ``claim`` → ``claim`` / ``steal`` / ``refuse``; ``release`` →
        ``release`` / ``absent`` / ``not_owned``; ``fence`` → ``hold`` /
        ``lost``.

        Renewal is deliberately absent: its verdict is the remote's
        compare-and-swap, not a local reading, so asking here would need an
        extra round trip to produce a weaker answer.

    Raises:
        ValueError: On an unknown action. The set is closed: falling through
            to a default is how a guard becomes permission.
    """
    if action not in LEASE_ACTIONS:
        raise ValueError(f"Lease action: invalid action {action!r}")
    if state not in ("absent", "live", "expired"):
        raise ValueError(f"Lease action: invalid state {state!r}")
    ours = owner is not None and owner == run_id
    if action == "claim":
        if state == "live":
            return "refuse"
        return "claim" if state == "absent" else "steal"
    if action == "fence":
        return "hold" if ours else "lost"
    if state == "absent":
        return "absent"
    return "release" if ours else "not_owned"


DEFAULT_TTL_SECONDS = 300
RENEW_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class LeaseHold:
    """Proof one Run took a Lease, and the SHA its next swap must match.

    Carries the exact SHA this Run last wrote, so a renewal, a release and the
    fence all compare against what *this* Run put on the remote rather than
    against whatever is there now. That is what makes a steal detectable.
    """

    ref: str
    issue: int
    run_id: str
    sha: str
    record: LeaseRecord
    stolen: bool = False
    displaced_run_id: str | None = None


@dataclass(frozen=True)
class LeaseObservation:
    """One read of a Lease ref: what was there, and the SHA to swap against."""

    ref: str
    sha: str | None
    inspection: LeaseInspection

    @property
    def state(self) -> Literal["absent", "live", "expired"]:
        """The Lease's state as of the clock this observation was read with."""
        return self.inspection.state

    @property
    def owner(self) -> str | None:
        """The ``run_id`` holding the Lease, or ``None`` when unreadable."""
        record = self.inspection.record
        return record.run_id if record is not None else None


class LeaseTransport:
    """Takes, renews, steals and releases Leases over one ref per issue.

    Holds no Lease state of its own: every decision is made against a fresh
    read of the remote, because a cached answer is exactly what the fence
    exists to refuse (ADR-0033 §4.4).

    Transient writes retry the same SHA and expectation up to four attempts
    within a 15-second monotonic budget, with jittered 1/2/4-second backoff.
    The clock, sleeper and jitter are injectable; no retry buys a new Pickup.
    Git cleanup has its own bounded grace period. Reads are not retried, and
    exhausted writes propagate their last error rather than implying success.
    """

    def __init__(
        self,
        git: LeaseRefPort,
        *,
        remote: str,
        repository: str,
        skew_tolerance_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self._git = git
        self._remote = remote
        self._repository = repository
        self._skew_tolerance_seconds = skew_tolerance_seconds
        self._clock = clock
        self._sleep = sleep
        self._jitter = (
            jitter if jitter is not None
            else lambda interval: random.uniform(interval / 2, interval)
        )

    def _push(
        self, ref: str, sha: str | None, expected: str | None,
        *, timeout_seconds: float = _WRITE_BUDGET_SECONDS,
    ) -> bool:
        """Retry the identical swap, never a fresh observation or a rejected swap."""
        accepted, _ = self._push_counted(
            ref, sha, expected, timeout_seconds=timeout_seconds
        )
        return accepted

    def _push_counted(
        self, ref: str, sha: str | None, expected: str | None,
        *, timeout_seconds: float = _WRITE_BUDGET_SECONDS,
    ) -> tuple[bool, int]:
        """As :meth:`_push`, also reporting how many attempts it took.

        The count is what tells a *replayed* write from a first one, and only
        a replay can have landed before its acknowledgement went missing. A
        swap rejected on attempt one sent nothing before it, so a ref that is
        gone afterwards was removed by somebody else.
        """
        deadline = self._clock() + min(timeout_seconds, _WRITE_BUDGET_SECONDS)
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                return self._git.push_ref(
                    self._remote, ref, sha, expected,
                    timeout_seconds=deadline - self._clock(),
                ), attempt + 1
            except GitError as exc:
                if (
                    attempt == _WRITE_ATTEMPTS - 1
                    or not is_transient_lease_error(exc)
                ):
                    raise
                interval = min(2.0**attempt, 4.0)
                delay = self._jitter(interval)
                if not math.isfinite(delay) or not 0 <= delay <= interval:
                    raise ValueError("Lease retry jitter must be within its interval")
                if self._clock() + delay >= deadline:
                    raise
                _log.warning(
                    "Lease write for %s failed; retrying in %.3fs: %s", ref, delay, exc
                )
                self._sleep(delay)
                if self._clock() >= deadline:
                    raise
        raise AssertionError("Lease write attempt limit did not terminate")

    def observe(self, issue: int, *, now: int) -> LeaseObservation:
        """Read the Lease ref for ``issue`` and judge it against ``now``.

        A read failure propagates as :exc:`~git_loopy.git.GitError` and is
        never reported as absence: absence admits a claim, so inferring it
        from a broken fetch would hand one issue to two Runs.
        """
        ref = lease_ref(issue)
        sha = self._git.probe_remote_ref(self._remote, ref)
        raw = None if sha is None else self._git.fetch_commit_message(self._remote, sha)
        return LeaseObservation(
            ref=ref,
            sha=sha,
            inspection=inspect_lease(
                raw,
                now=now,
                repository=self._repository,
                issue=issue,
                skew_tolerance_seconds=self._skew_tolerance_seconds,
            ),
        )

    def claim(
        self,
        issue: int,
        *,
        run_id: str,
        now: int,
        host: str,
        pid: int,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> LeaseHold | None:
        """Take the Lease on ``issue``, or return ``None`` if another Run holds it.

        The compare-and-swap: the ref is pushed expecting exactly the SHA that
        was just observed — no SHA at all for an unheld issue, the dead
        owner's SHA when stealing one judged expired. Two Runs racing either
        way, exactly one wins, and the loser learns immediately and cheaply,
        before any session starts or any worktree exists (ADR-0033 §2.3).

        A rejected swap is **not** retried: it is another Run's answer, not a
        transient fault, so the caller moves to the next candidate instead.
        """
        observation = self.observe(issue, now=now)
        verdict = decide_lease_action(
            "claim", state=observation.state, owner=observation.owner, run_id=run_id
        )
        if verdict == "refuse":
            return None
        return self._write(
            issue=issue,
            run_id=run_id,
            claimed_at=now,
            heartbeat_at=now,
            host=host,
            pid=pid,
            ttl_seconds=ttl_seconds,
            expected=observation.sha,
            stolen=verdict == "steal",
            displaced_run_id=observation.owner,
        )

    def _write(
        self,
        *,
        issue: int,
        run_id: str,
        claimed_at: int,
        heartbeat_at: int,
        host: str,
        pid: int,
        ttl_seconds: int,
        expected: str | None,
        stolen: bool = False,
        displaced_run_id: str | None = None,
        timeout_seconds: float = _WRITE_BUDGET_SECONDS,
    ) -> LeaseHold | None:
        """Swap one fresh orphan record in, returning ``None`` if rejected."""
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Lease write timeout must be positive and finite")
        deadline = self._clock() + min(timeout_seconds, _WRITE_BUDGET_SECONDS)
        record = LeaseRecord(
            run_id=run_id,
            issue=issue,
            repository=self._repository,
            claimed_at=claimed_at,
            heartbeat_at=heartbeat_at,
            ttl_seconds=ttl_seconds,
            host=host,
            pid=pid,
        )
        ref = lease_ref(issue)
        sha = self._git.write_orphan_commit(render_record(record))
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise GitError(["git", "push", self._remote], 124, "Operation timed out")
        if not self._push(ref, sha, expected, timeout_seconds=remaining):
            return None
        return LeaseHold(
            ref=ref,
            issue=issue,
            run_id=run_id,
            sha=sha,
            record=record,
            stolen=stolen,
            displaced_run_id=displaced_run_id,
        )

    def renew(
        self, hold: LeaseHold, *, now: int,
        timeout_seconds: float = _WRITE_BUDGET_SECONDS,
    ) -> LeaseHold | None:
        """Refresh ``hold``'s heartbeat, or return ``None`` if it was stolen.

        Swaps against the SHA this Run last wrote, so a Lease taken from it
        while it worked is detected rather than overwritten. ``claimed_at`` is
        carried forward untouched: renewal moves the *liveness* of a Lease,
        never the moment it was taken.

        Renewal is driven by the clock alone — never by session output,
        commits or Iteration boundaries — so a slow agent can never be
        mistaken for a dead one (ADR-0033 §3.2). A refusal is final: the Run
        stops work on that issue and does not reclaim (§3.4).

        ``timeout_seconds`` caps the usual write budget to the owner's remaining
        TTL. Local record creation consumes this budget too; it never grants a
        fresh retry window after the deadline.
        """
        return self._write(
            issue=hold.issue,
            run_id=hold.run_id,
            claimed_at=hold.record.claimed_at,
            heartbeat_at=now,
            host=hold.record.host,
            pid=hold.record.pid,
            ttl_seconds=hold.record.ttl_seconds,
            expected=hold.sha,
            timeout_seconds=timeout_seconds,
        )

    def holds(self, hold: LeaseHold, *, now: int) -> bool:
        """Re-read the Lease ref and answer whether this Run still owns it.

        **The fence** (ADR-0033 §4.4). Every side effect a Run performs on an
        issue's behalf — push, issue comment, label write, issue close — is
        gated on this, individually, immediately before it happens. It is the
        safety mechanism; the TTL is only an optimisation. Expiry cannot tell
        a dead owner from a slow one, and a paused laptop or a partition can
        trip any TTL, so the fence is what makes a false steal survivable: it
        costs duplicated *effort* and never corrupted *state*. Weakening it to
        save round trips defeats the design.

        Ownership is the ``run_id`` on the ref, not the SHA: this Run's own
        heartbeat moves the SHA continuously, and a renewal is not a theft.
        The read is deliberately fresh every time — a cached answer is exactly
        what the fence exists to refuse.

        Deny-by-default: an absent ref, an unreadable record or another Run's
        identity all return ``False``. A *read failure* is not an answer at
        all and propagates as :exc:`~git_loopy.git.GitError`, because treating
        a broken fetch as permission would defeat the guarantee.
        """
        observation = self.observe(hold.issue, now=now)
        return (
            decide_lease_action(
                "fence",
                state=observation.state,
                owner=observation.owner,
                run_id=hold.run_id,
            )
            == "hold"
        )

    def release(
        self, hold: LeaseHold, *, now: int
    ) -> Literal["released", "absent", "not_owned"]:
        """Give up ``hold``'s Lease, freeing the issue without waiting for a TTL.

        Idempotent by design, because it runs on the success path *and* on
        every handled failure path: an already-absent ref answers ``absent``
        and writes nothing. It cannot be guaranteed on ``SIGKILL`` — which is
        precisely why expiry exists.

        The delete is itself a compare-and-swap against the SHA just observed,
        so the one thing release must never do — free an issue some *other*
        Run is now working — cannot happen, whether the ref changed before the
        read (``not_owned``) or between the read and the swap (a rejection,
        reported the same way). Renewal must be stopped before release, or a
        Run's own heartbeat will race its delete (ADR-0033 §3.6).

        ``absent`` means only one thing: the ref was *already* gone when this
        call looked, so this call removed nothing. A delete whose
        acknowledgement was lost answers ``released``, because it is one — the
        ref was read as this Run's, a delete was pushed, and its absence
        confirmed. Keeping those two apart matters to the caller: a live hold
        answered ``absent`` is a Lease that ended without its owner's
        knowledge, which is evidence of loss, while a lost acknowledgement is
        an ordinary success.
        """
        observation = self.observe(hold.issue, now=now)
        verdict = decide_lease_action(
            "release",
            state=observation.state,
            owner=observation.owner,
            run_id=hold.run_id,
        )
        if verdict == "absent" or observation.sha is None:
            return "absent"
        if verdict != "release":
            return "not_owned"
        accepted, attempts = self._push_counted(hold.ref, None, observation.sha)
        if not accepted:
            # A delete may have landed before its acknowledgement was lost —
            # but only a *replayed* one can have. A swap rejected on the first
            # attempt sent nothing before it, so a ref that is gone afterwards
            # was removed by somebody else, which is news of loss and not a
            # release. Re-reading can confirm absence, never authorize another
            # write.
            if attempts > 1 and self.observe(hold.issue, now=now).state == "absent":
                return "released"
            return "not_owned"
        return "released"
