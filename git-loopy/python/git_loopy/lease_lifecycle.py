"""Every Lease this Run holds, from Pickup to release (ADR-0033).

The Lease's lifecycle, as one object the Orchestrator can hold: it takes a
Lease as the last step of **Pickup**, keeps it alive while the **Agent** works,
answers the fence before each individual side effect, and gives it back.

Three seams sit underneath it and stay underneath it.
:class:`~git_loopy.issue_lease.LeaseTransport` owns the compare-and-swap,
:class:`~git_loopy.lease_heartbeat.LeaseHeartbeat` owns renewal, and
:func:`~git_loopy.issue_lease.decide_lease_action` owns the policy. What this
module adds is the part none of them can see on its own: *which* issues this
Run is holding right now, and the rule that losing one is permanent.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Mapping, Protocol

from .git import GitError
from .issue_lease import (
    DEFAULT_TTL_SECONDS,
    RENEW_INTERVAL_SECONDS,
    LeaseHold,
    LeaseRefPort,
    LeaseTransport,
    is_permanent_lease_write_refusal,
)
from .lease_heartbeat import LeaseHeartbeat
from .repository_identity import repository_from_remote_url

TakeVerdict = Literal["taken", "stolen", "refused", "unavailable"]

#: The remote a Lease is taken on. Leases contend where the work is published,
#: so this is the same remote ADR-0004's Checkpoint push targets.
LEASE_REMOTE: Final[str] = "origin"

#: The env var that overrides :data:`~git_loopy.issue_lease.DEFAULT_TTL_SECONDS`,
#: following the ``GIT_LOOPY_GATE_TIMEOUT_SECONDS`` precedent (ADR-0033 §4.1).
LEASE_TTL_ENV_VAR: Final[str] = "GIT_LOOPY_LEASE_TTL_SECONDS"



def resolve_lease_ttl_seconds(env: Mapping[str, str]) -> int:
    """Resolve how long a Lease survives without renewal, from the environment.

    ``GIT_LOOPY_LEASE_TTL_SECONDS`` > :data:`DEFAULT_TTL_SECONDS`, lenient in
    the same way :func:`~git_loopy.gate.resolve_gate_timeout_seconds` is: a
    malformed, non-integer, non-positive or non-finite value falls through to
    the default rather than aborting, because a stray value must not stop a Run
    from taking Leases at all — which would be strictly *less* safe than the
    default it mistyped.

    Whole seconds only, because that is what a Lease record stores; ``300.7``
    is a typo, not a request for sub-second expiry.

    One bound the gate's knob does not need: a TTL shorter than a single
    renewal interval is refused. It would expire *between* beats however
    healthy its owner, manufacturing exactly the false steal §4.5 exists to
    bound, and a knob whose extreme setting breaks the guarantee is a footgun
    rather than a tuning parameter. ``inf`` is refused for the neighbouring
    reason: a Lease that never expires never frees a crashed Run's issue, so
    there is deliberately no value meaning "never".
    """
    raw = env.get(LEASE_TTL_ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_TTL_SECONDS
    if value < RENEW_INTERVAL_SECONDS:
        return DEFAULT_TTL_SECONDS
    return value


class HeartbeatLike(Protocol):
    """The renewal lifecycle this module drives, as an injectable seam.

    :class:`~git_loopy.lease_heartbeat.LeaseHeartbeat` satisfies it
    structurally. Narrowed to the three calls a lifecycle actually makes, so a
    fixture can prove the ordering — started only after the Lease is held,
    stopped and joined before it is released — without a thread.
    """

    def start(self) -> None:
        """Begin renewing, on a thread of the implementation's choosing."""
        ...

    def stop(self) -> object:
        """Stop renewing and join any in-flight renewal before returning."""
        ...

    def wait_lost(self, timeout: float | None = None) -> bool:
        """Whether renewal has terminally lost the Lease."""
        ...


@dataclass(frozen=True)
class LeaseTake:
    """What one attempt to take a Lease at **Pickup** settled."""

    verdict: TakeVerdict
    hold: LeaseHold | None = None
    displaced_run_id: str | None = None

    @property
    def granted(self) -> bool:
        """Whether this Run may now work the issue."""
        return self.verdict in ("taken", "stolen")


class LeaseLifecycle:
    """Take, hold, fence and release this Run's **Lease**s.

    One instance per Run. A serial **Iteration** holds at most one Lease; in
    Parallel mode each **Lane** holds its own, renewed on its own thread, and
    one Lane's failure never touches another's.

    Four calls are the whole surface. :meth:`take` is **Pickup**'s last step,
    :meth:`fence` gates each individual side effect, :meth:`release` gives an
    issue back, and :meth:`held` says what is outstanding. Everything else —
    which SHA to swap against, when to renew, what a malformed record means —
    belongs to the seams underneath and stays there.

    Two rules are this module's own, because no seam below can see far enough
    to enforce them:

    **Loss is permanent.** A Lease this Run is shown to have lost is never
    regained — not when the thief releases it, not when the ref reappears, not
    at the next Pickup. Reclaiming silently is precisely the two-agents-one-issue
    outcome the Lease exists to prevent (ADR-0033 §3.4).

    **Every refusal is deny-by-default.** A missing Lease, another Run's
    identity, an unreadable record and a read that never happened all answer
    no. The last is the one that matters: a broken fetch is not absence and is
    not permission, because absence admits a claim and permission admits a
    write.
    """

    def __init__(
        self,
        transport: LeaseTransport,
        *,
        run_id: str,
        host: str,
        pid: int,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        warn: Callable[[str], None] = lambda _message: None,
        heartbeats: Callable[[LeaseTransport, LeaseHold], HeartbeatLike]
        | None = LeaseHeartbeat,
    ) -> None:
        self._transport = transport
        self._run_id = run_id
        self._host = host
        self._pid = pid
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._warn = warn
        self._heartbeats = heartbeats
        self._holds: dict[int, LeaseHold] = {}
        self._beats: dict[int, HeartbeatLike] = {}
        self._lost: set[int] = set()
        self._ever_held = False
        self._disabled = False

    def held(self) -> tuple[int, ...]:
        """The issues this Run currently holds a Lease on, lowest first."""
        return tuple(sorted(self._holds))

    def disabled(self) -> bool:
        """Whether this clone has been shown it may never write a Lease ref.

        Not a verdict about any one issue: it is the discovery that the Lease
        mechanism itself is unavailable *here*, so the caller stops asking and
        works unguarded — exactly as it did before ADR-0033, and as it does
        when no lifecycle is built at all.

        This exists because the fourth take verdict, ``unavailable``, denies
        the candidate. That is right for an outage, which passes; it is wrong
        for a clone that is *never* going to be allowed to write the ref, such
        as a read-only fork remote, a ruleset that forbids creating
        ``refs/heads/**``, or an expired SSO authorization. Left undistinguished,
        that clone refuses every candidate in the Pool on every Iteration and
        the Run ends having done no work at all, reporting a readiness fault it
        never had. A Lease this clone cannot take protects nothing, so refusing
        the work buys nothing either.
        """
        return self._disabled

    def _now(self) -> int:
        return int(self._clock())

    def take(self, issue: int) -> LeaseTake:
        """Take the Lease on ``issue``, the last step of **Pickup**.

        Three outcomes are *this Run's* answer and one is nobody's.
        ``taken`` and ``stolen`` grant the issue; ``refused`` means another Run
        holds it live, which is the mechanism working rather than a fault, so
        the caller moves to the next candidate silently (ADR-0033 §8.2).

        ``unavailable`` is the fourth, and it is a read that did not happen.
        A broken fetch must never be reported as absence — absence admits a
        claim, so inferring it would hand one issue to two Runs — and it must
        not end the Run either, because **Pickup** is unattended and a
        candidate it cannot take it passes over (§8.3). It denies, warns, and
        leaves the issue for a later **Iteration**.

        A rejected swap is never retried here: it is another Run's answer, not
        a transient fault. The Lease is taken before any **Agent** session
        starts and before any worktree exists, so losing the race costs
        nothing but the round trip (§2.3).

        An issue this Run has already been shown to have lost is refused
        without a round trip. §3.4 forbids reclaiming: a Run that discovered
        mid-session that someone else had taken its issue must stay away from
        it, even once the thief frees it again, or the two Runs simply trade
        the issue back and forth and neither finishes it.
        """
        if issue in self._lost:
            return LeaseTake("refused")
        try:
            hold = self._transport.claim(
                issue,
                run_id=self._run_id,
                now=self._now(),
                host=self._host,
                pid=self._pid,
                ttl_seconds=self._ttl_seconds,
            )
        except GitError as exc:
            self._warn(f"Lease for issue #{issue} could not be read or taken: {exc}")
            if not self._ever_held and is_permanent_lease_write_refusal(exc):
                # Not an outage: this clone may never write the Lease
                # namespace. Latched only before the first successful write,
                # so it can only ever describe a clone that has never been
                # allowed to take a Lease — never a Run whose credentials were
                # withdrawn mid-flight, which must keep its deny-by-default
                # fence. Degrading loudly to the unguarded pre-ADR-0033
                # behaviour beats refusing every candidate forever.
                self._disabled = True
                self._warn(
                    "This clone cannot write the Lease refs on "
                    f"{LEASE_REMOTE!r} ({exc}); continuing without Leases. "
                    "Cross-Run exclusivity is OFF for this Run: nothing will "
                    "stop a second Run working the same issue. Grant this "
                    "token push access to 'refs/heads/git-loopy/leases/*' to "
                    "restore it."
                )
            return LeaseTake("unavailable")
        if hold is None:
            return LeaseTake("refused")
        self._holds[issue] = hold
        self._ever_held = True
        if self._heartbeats is not None:
            # Renewal starts only after the Lease is actually held, and is
            # driven by the clock alone — never by Agent output, commits or
            # Iteration boundaries — so a slow Agent never resembles a dead
            # one (ADR-0033 §3.2). One per Lease, so Lanes renew independently.
            beat = self._heartbeats(self._transport, hold)
            self._beats[issue] = beat
            beat.start()
        if hold.stolen:
            # §8.1: a Run died. Warn, never prompt — crash recovery happens on
            # the *next* Run, which is the unattended one.
            self._warn(
                f"Lease for issue #{issue} was held by Run "
                f"{hold.displaced_run_id or 'unknown'} and had expired; "
                "taking it over"
            )
        return LeaseTake(
            "stolen" if hold.stolen else "taken",
            hold=hold,
            displaced_run_id=hold.displaced_run_id,
        )

    def readable(self, issue: int) -> bool:
        """Whether the Lease remote answers for ``issue`` right now (#645).

        A pure read: it claims nothing, renews nothing and changes nothing this
        lifecycle holds. It exists for a caller waiting out a ``take`` that was
        ``unavailable``, which must not spend another attempt until the remote
        can answer. An issue this Run has lost needs no round trip, because
        ``take`` refuses it without one.
        """
        if issue in self._lost:
            return True
        try:
            self._transport.observe(issue, now=self._now())
        except GitError:
            return False
        return True

    def lost(self, issue: int) -> bool:
        """Whether this Run has already been shown to have lost ``issue``."""
        return issue in self._lost

    def fence(self, issue: int, action: str) -> bool:
        """Answer whether this Run may perform ``action`` on ``issue`` — **the fence**.

        Called immediately before *each individual* side effect — push, issue
        comment, label write, issue close — and never once for a batch of
        them. It is the safety mechanism; the TTL is only an optimisation
        (ADR-0033 §4.5). Expiry cannot tell a dead owner from a slow one, so a
        false steal must cost duplicated *effort* and never corrupted *state*,
        and that is true only if every write is individually gated on a fresh
        read. Weakening this to save round trips defeats the design.

        Deny-by-default in all four ways it can fail to be a yes: an issue
        this Run never took, a ref another Run now owns, an unreadable record,
        and a read that did not happen at all. The last is the one worth
        stating: a broken fetch is not permission, so it denies and warns
        rather than raising, because a **Pickup**-time fault must not end a Run
        that is otherwise healthy.

        Renewal is *not* a fifth way. A stalled heartbeat gives up on local
        facts — an elapsed TTL after a suspended laptop, a failed write, a
        dead worker — as readily as on a compare-and-swap rejection, and only
        the last is an answer about the ref. It therefore prompts the read
        rather than standing in for it.

        **Loss is permanent.** Once this Run is shown to have lost a Lease, no
        later reading can restore it — not the thief releasing it, not the ref
        being recreated. Reclaiming silently is precisely the two-agents-one-issue
        outcome the Lease exists to prevent, and §3.4 forbids it: the Run stops
        work on that issue and does not reclaim.
        """
        if issue in self._lost:
            return False
        hold = self._holds.get(issue)
        if hold is None:
            return False
        beat = self._beats.get(issue)
        if beat is not None and beat.wait_lost(0):
            # Renewal giving up is a reason to *look*, never a verdict. Only a
            # compare-and-swap rejection is an answer about the ref; renewal
            # also gives up on facts that are purely local — an elapsed TTL
            # after a suspended laptop, a failed write, a dead worker — and
            # those prove nothing about who owns it. Treating them as proof
            # would abandon a Lease whose ref still carries this Run's
            # ``run_id``, and *leak* it too, because a dropped hold is one
            # `release` will no longer delete. So stop the renewal that has
            # stopped anyway, keep the hold, and let the read below be the
            # fence it is supposed to be. Popping the beat means this is said
            # once, not before every subsequent write.
            self._stop_renewal(issue)
            self._warn(
                f"Renewal for issue #{issue}'s Lease has stopped; it will "
                f"expire unless this Run finishes first. Checking the remote "
                f"before {action}."
            )
        try:
            still_held = self._transport.holds(hold, now=self._now())
        except GitError as exc:
            # A read that did not happen is not an answer. Deny without
            # latching: this Run may well still hold the Lease, and a
            # transient fault must not permanently abandon live work.
            self._warn(
                f"Lease for issue #{issue} could not be verified before "
                f"{action}; refusing it: {exc}"
            )
            return False
        if not still_held:
            self._lose(issue, action)
            return False
        return True

    def release(self, issue: int) -> Literal["released", "absent", "not_owned"]:
        """Give ``issue``'s Lease back, freeing it without waiting for a TTL.

        Renewal is stopped and joined *first*, or this Run's own heartbeat
        races its own delete and re-creates the ref it just removed (§3.6).

        Idempotent, because it runs on the success path and on every handled
        failure path alike: an issue never taken, or one already released,
        answers ``absent`` and writes nothing. It cannot be guaranteed on
        ``SIGKILL`` — which is exactly why expiry exists (§5.3).

        ``not_owned`` is the case that matters: a Lease this Run was stolen
        from is *not* deleted, because deleting it would free an issue some
        other Run is now working. The delete is a compare-and-swap for that
        reason, so the race between the read and the swap is closed too (§5.4).

        A live hold whose ref had *already* vanished is the same kind of news
        wearing different clothes — a thief that stole the Lease and then
        freed it, or an operator's hand delete — so it latches the loss too.
        Silently reclaiming an issue at a later **Pickup** on the strength of
        "it looked free" is the two-agents-one-issue outcome §3.4 forbids. An
        issue never taken, or already released here, never reaches that check.
        """
        self._stop_renewal(issue)
        hold = self._holds.pop(issue, None)
        if hold is None:
            return "absent"
        try:
            outcome = self._transport.release(hold, now=self._now())
        except GitError as exc:
            self._warn(f"Lease for issue #{issue} could not be released: {exc}")
            return "not_owned"
        if outcome == "not_owned":
            self._lost.add(issue)
            self._warn(
                f"Lease for issue #{issue} was no longer this Run's to release; "
                "left it alone"
            )
        elif outcome == "absent":
            self._lost.add(issue)
            self._warn(
                f"Lease for issue #{issue} was already gone when this Run came "
                "to release it; abandoning the issue rather than reclaiming it"
            )
        return outcome

    def release_all(self) -> None:
        """Give back every Lease this Run still holds, one failure never stopping the rest."""
        for issue in self.held():
            self.release(issue)

    def _stop_renewal(self, issue: int) -> None:
        """Stop and join ``issue``'s renewal so it cannot race what comes next."""
        beat = self._beats.pop(issue, None)
        if beat is not None:
            beat.stop()

    def _lose(self, issue: int, action: str) -> None:
        """Record a Lease this Run no longer holds, once and for good.

        Reached only from a fresh read that said so, never from a local
        signal: renewal giving up is a reason to look, not a verdict, and
        latching on it would abandon a Lease this Run still owns.
        """
        self._stop_renewal(issue)
        hold = self._holds.pop(issue, None)
        self._lost.add(issue)
        cause = "it is held by another Run"
        if hold is not None:
            try:
                observed = self._transport.observe(issue, now=self._now())
            except GitError:
                observed = None
            if observed is not None and observed.owner is not None:
                cause = f"it is held by Run {observed.owner}"
        self._warn(
            f"Lease for issue #{issue} is gone ({cause}); refusing {action} "
            "and abandoning the issue"
        )


class LeaseClonePort(LeaseRefPort, Protocol):
    """A clone a Lease can be taken in: ref mechanics plus its own identity.

    :class:`~git_loopy.git.GitClient` satisfies it structurally. Deliberately
    one method wider than :class:`~git_loopy.issue_lease.LeaseRefPort` rather
    than taking the whole ``GitClient``: constructing a lifecycle needs to know
    *which* repository this clone contends on, and nothing else about it.
    """

    def remote_url(self, remote: str) -> str | None:
        """Return ``remote``'s configured URL, or ``None`` when unconfigured."""
        ...


def build_lease_lifecycle(
    git: LeaseClonePort,
    *,
    run_id: str,
    env: Mapping[str, str],
    remote: str = LEASE_REMOTE,
    warn: Callable[[str], None] = lambda _message: None,
    clock: Callable[[], float] = time.time,
    host: str | None = None,
    pid: int | None = None,
) -> LeaseLifecycle | None:
    """Build this Run's :class:`LeaseLifecycle`, or ``None`` if it can hold none.

    The activation seam: everything below it is proved by fixtures, and this is
    where a real Run reaches it. It resolves the repository the clone contends
    on from ``remote``'s URL — a local config read, no round trip — and hands
    that identity to the transport, because a Lease record names the repository
    it belongs to and two clones must agree on that name or both believe
    themselves the owner.

    ``None`` means *this Run holds no Lease*, which restores exactly the
    pre-ADR-0033 behaviour rather than failing the Run. It is the answer for a
    clone with no such remote and for one whose remote names no
    ``owner/repo`` — a local path, a ``file://`` clone. That is deliberately
    the safe direction: a Run that holds no Lease is as exposed as every Run
    was before this ADR, while a Run that invented an identity would contend on
    a ref belonging to some *other* repository and could hand one issue to two
    Runs while appearing to prevent exactly that.

    ``host`` and ``pid`` are diagnostic only and are never read by a decision
    (§1.4); they default to this process's own and are injectable so a fixture
    need not depend on the machine it runs on.
    """
    try:
        url = git.remote_url(remote)
    except GitError as exc:
        # Not absence: git itself failed. The answer is still no Lease, but the
        # operator is told, because a Run that silently stops guarding its
        # issues looks identical to one that is guarding them.
        warn(
            f"Leases are off: this clone's '{remote}' URL could not be read, "
            f"so no repository could be resolved to contend on ({exc})"
        )
        return None
    if url is None:
        warn(
            f"Leases are off: this clone has no '{remote}' remote, so there is "
            "no repository to contend on"
        )
        return None
    repository = repository_from_remote_url(url)
    if repository is None:
        warn(
            f"Leases are off: this clone's '{remote}' remote names no "
            "owner/repo, so there is no repository to contend on"
        )
        return None
    return LeaseLifecycle(
        LeaseTransport(git, remote=remote, repository=repository),
        run_id=run_id,
        host=socket.gethostname() if host is None else host,
        pid=os.getpid() if pid is None else pid,
        clock=clock,
        ttl_seconds=resolve_lease_ttl_seconds(env),
        warn=warn,
    )
