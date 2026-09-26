# One issue, one run: a Lease taken at Pickup and expired by its own clock

**Status:** proposed

> **Note (superseded in part by [ADR-0066](0066-a-version-label-names-the-release-and-prereleases-move-alpha-beta-rc.md)):**
> Pickup no longer applies a `semver:` label; it writes the issue's `vX.Y.Z` Release-target
> label, or nothing when it infers no version change. The ordering argued below is unchanged.

Implemented by [#390](https://github.com/bradcstevens/git-loopy/issues/390), which is blocked by
[#389](https://github.com/bradcstevens/git-loopy/issues/389).

[ADR-0010](0010-local-sandbox-per-iteration.md) already recorded the gap as a non-goal:
*"it does not solve concurrent-run worktree collisions (issues are worked sequentially within
a run; **two simultaneous runs still collide**)."* Nothing in the repository coordinates two
Runs — no lock, no owner record, no warning — so running git-loopy twice against one tracker
means two agents working one issue.

A **Lease** is a git ref pushed to the remote at **Pickup**. Creating it is the compare-and-
swap: exactly one Run wins, and the loser knows immediately. The commit at that ref carries
the owning `run_id` and its timestamps. A background task renews it every **60s**; it expires
after **300s** without renewal. A label and an issue comment mirror it for humans and are
never authoritative.

## Why a ref, when we already have labels

Because a label cannot be contended for. `gh issue edit --add-label` is a blind write with no
precondition, so two Runs both read *unclaimed*, both write, and both proceed — the collision
is not prevented, merely discovered afterwards. The same is true of comments and of assignees.

Pushing a *new* ref fails cleanly if it already exists, and `--force-with-lease` makes renewal
and theft equally atomic. It is the only compare-and-swap GitHub offers without new
infrastructure, and infrastructure was ruled out: git-loopy requires `git` and `gh` and
nothing else, and a Lease is not worth ending that.

Leases live under `refs/heads/` as ordinary branches. GitHub does accept custom namespaces
(`refs/git-loopy/leases/*`), which would keep them out of `git branch -r` and the branch
picker, and that option remains open — it was passed over for legibility, not capability.

## Expiry cannot tell "dead" from "slow", so safety does not depend on it

The failure that makes leases dangerous is the false steal: a TTL lapses because of a paused
laptop or a network partition, the claim is taken, and two agents now work one issue — the
exact disaster the Lease exists to prevent. No TTL value avoids this. Short expires falsely;
long parks an issue for the whole TTL after a real crash.

We separate the two jobs and require only one of them to be correct:

- **Renewal is independent of agent progress.** A background task heartbeats regardless of how
  long the session runs, so slowness never resembles death. That is what lets TTL be 300s
  rather than exceeding the gate's own 3600s default (`gate.py:93`).
- **A fence guards every side effect.** Before any push, comment, label, or close, the runner
  re-reads its Lease ref and aborts if it no longer owns it. A Run that was stolen from while
  still alive discovers this before it can write anything.

So a false steal costs duplicated *effort* and never corrupted *state*. That is the trade this
design makes deliberately.

## Non-goals

- **It does not salvage a dead Run's work.** Lane branches are never pushed: `loop.py` has one
  `.push()` call site (`:1429`) and it is the serial base-branch path, and no `gh pr create`
  exists anywhere. A crash on another machine leaves nothing on the remote, so there is
  nothing to recover. `loop.py:3312` already keeps failed Lane branches *"as a breadcrumb"*;
  that philosophy is unchanged.
- **It does not reap local worktrees.** A dead Run's `<repo>.worktrees/<run_id>/issue-<N>` is
  still there and nothing removes it (see #359). Its Lease expiring frees the *issue*, not the
  disk.
- **It is not a lock on files.** Two Runs working different issues that touch the same code
  still conflict; that is Integration's problem (ADR-0009), not this one.

## Consequences

- **`git.py:579` deviates from a stated principle.** Its docstring defends *"a bare `git push`
  (no ref arguments, no `--force`)"* as keeping the user's git config the single source of
  truth. Lease writes need an explicit refspec and `--force-with-lease`. That is a real
  exception and is why it is recorded here.
- **Lease writes need retry, and no write in the codebase has any.** `_checked`
  (`gh.py:1279-1296`) *"counts and re-raises rather than recovering"*, so a throttled or
  flaky claim fails hard today. A Lease that cannot be renewed through a transient failure
  expires and is stolen — the failure mode is exactly the one we designed against.
- **A background heartbeat must exist in three languages.** AGENTS.md gates Python, shell, and
  PowerShell alike. The
  [heartbeat prototype](https://github.com/bradcstevens/git-loopy/blob/99ad228/HEARTBEAT-PROTOTYPE.md)
  proved all three feasible, but rejected two tempting precedents: a Python
  `asyncio` task is starved by the Orchestrator's blocking git/gh calls, and
  shell's unbound background ticker survives its parent's death. Python needs a
  dedicated per-Lease thread. The shell renewer must monitor its parent; an
  inherited death-pipe alone did not stop when the Agent still held it open.
  PowerShell's tested Job/ThreadJob shapes remain viable. The long-fixed-TTL
  degradation is not needed.
- **Conflicts warn and skip; they never prompt.** `ready-for-agent` means *ready for autonomous
  execution*, and a blocking prompt fires precisely when nobody is watching — crash recovery
  happens on the *next* run, which is the unattended one. Two situations warn: stealing an
  expired Lease, and finding a leftover worktree from a dead Run on this host. A Lease held by
  a live Run is not a conflict; it is the mechanism working, and the runner moves to the next
  candidate silently.
- **The clock must be injectable.** `rolling_pool.py:126,138` already establishes the
  convention (*"Injected so backoff is deterministic"*) and `GIT_LOOPY_GATE_TIMEOUT_SECONDS`
  establishes the override convention. Lease expiry follows both, or it cannot be pinned in
  `conformance/`.

## Staged implementation

The record/expiry and action decisions are present in all three Orchestrators;
the ref transport is currently Python-only. Its writes now retry recognized
transport, rate-limit and HTTP 5xx failures, never authentication, policy, unknown
failures or a compare-and-swap rejection. Each write keeps one immutable target
SHA and expectation across at most four attempts within a 15-second monotonic
budget, using 1/2/4-second exponential windows with jitter in each window's upper
half. Tests inject the clock, sleeper and jitter rather than sleeping.

Each git push gets the remaining budget, disables credential prompts, and shares
Integration's bounded process cleanup: on POSIX it kills the private process
group, elsewhere the direct child, then allows five seconds to drain and, if
needed, five more to reap. A lost acknowledgement replays the same write. Git can
report a replayed record as up to date but rejects a replayed deletion; release
then confirms absence and reports it as the *release* it is. Only a replay may
do so: a swap rejected on the first attempt sent nothing before it, so a ref
gone afterwards was removed by somebody else and is reported as loss. Release
keeps that distinct from a ref that was already gone before it looked, which
is likewise evidence the Lease ended without its owner's knowledge. Exhaustion
surfaces the last
failure. Ordinary branch pushes and GitHub writes do not acquire retry policy.

The Python `LeaseHeartbeat` scheduler is also staged. One daemon thread per held
Lease renews independently of the Agent, the event loop and other Lanes. It uses
the stored TTL and injected wall/monotonic clocks: 60-second renewal intervals,
capped at half the TTL for short Leases. Transient failures can recover on later
beats, but never extend the last acknowledged deadline. Each renewal's retry
budget is capped to the remaining TTL, including local record-creation time.
Rejection, non-transient failure, a backwards heartbeat clock or elapsed TTL
signals terminal loss, never another Pickup. The owner conservatively stops at
its deadline; this does not change the reader's strictly-past-TTL expiry rule.

Callers must start a heartbeat only after taking a Lease, watch `wait_lost` to
prompt a fence check, and call `stop` before release. `stop` joins an in-flight
renewal and returns the latest acknowledged Hold, so renewal cannot race the
delete.
Immutable snapshots expose the latest Hold, renewal count and failure without
calling the Orchestrator or writing Events on a worker thread. A snapshot is
never a fence: every individual side effect still needs its fresh remote read.
Unexpected worker exceptions remain visible and also signal loss.

`LeaseLifecycle` is the lifecycle those seams are driven through, and it is what
the Python Orchestrator now holds. It takes a Lease as the **last** step of
serial Pickup — after the Attempt-lifecycle filter, Readiness and routing have
all admitted the candidate, so a candidate the walk would have passed over
anyway never costs a Lease. A refusal is an ordinary Pickup skip that moves the
ordered walk to the next candidate, never a raise: an issue another Run holds
live is the mechanism working, and a Run must not end because someone else is
already working something. It distinguishes four outcomes, and the fourth is
the one worth naming — an unreadable remote is `unavailable`, never absence,
because absence admits a claim. Pickup records that fourth outcome as an
*unresolved* refusal rather than an ordinary one, so a systemic fault that
refuses every candidate this way cannot be mistaken by the terminal classifier
for a Pool that refused the work.

Loss is latched. Once a Run is shown to have lost a Lease it can never regain
it within that Run, not when the thief releases it and not when the ref is
recreated; silently reclaiming is exactly the two-agents-one-issue outcome this
ADR exists to prevent. Releasing a live hold whose ref had already vanished
latches the same way, because a thief that stole the Lease and then freed it
leaves an issue that merely *looks* free. The fence denies by default in all
four of its failure modes — an issue never taken, another Run's identity, an
unreadable record, and a read that did not happen at all — and denies without
latching on the last of those, so a transient fault cannot permanently abandon
live work.

Renewal giving up is not a fifth failure mode. A heartbeat signals loss on an
elapsed TTL, a failed write or a dead worker as readily as on a compare-and-swap
rejection, and only the last is an answer about the ref. So the fence treats
that signal as a reason to read rather than a verdict: it stops the renewal
that has stopped anyway, keeps the hold, and lets the remote decide. Latching
on a local fact would abandon a Lease whose ref still names this Run — and leak
it, since a dropped hold is one release no longer deletes.

The Orchestrator fences each side effect **individually and immediately before
it happens**, never once per Iteration and never once for a batch: the
auto-push re-reads the ref for itself, and the completion backstop that closes
issues and comments on them is fenced per candidate ref, by narrowing the Pool
whitelist it filters closing keywords against. One issue's Lease therefore
never authorises closing another, and in Parallel mode one Lane's lost Lease
cannot suppress another Lane's completions.

Only issues are Lease-governed, and the discriminator is the item's *kind*,
never its ref type: a pull request's ref is an `int` exactly like an issue's.
Leasing on the number alone would put PR #412 and issue #412 on one ref and
lock each out of the other's work, and fencing on it would refuse a PR
Iteration's writes over a Lease that was never its to hold — and drop every PR
from the completion Pool, hiding the head advances progress detection is
counting and earning Strikes for an Iteration that was working. Asking the
fence at all is likewise opt-in per caller: a Pool whose items were never
Lease-governed would have every ref denied at "this Run holds no Lease on it"
and close nothing at all. Deny-by-default is right for the fence and wrong for
whether to ask it.

A **Lane** takes its own Lease and answers the same fence. Pickup's last step
is the same step — after routing has admitted the candidate and *before*
classification, and before the worktree is cut, so a lost race costs one round
trip — and the Lane's three side effects are fenced individually: its
publication
onto base, the issue close that follows, and the breadcrumb comment a terminal
auto-resolution leaves. Publication is fenced even though base is local,
because it is the moment a Lane's commits become the Run's trunk and the next
serial Iteration's auto-push sends them to the remote under a *different*
issue's fence; landing them unfenced would launder a lost Lease's work past a
fence that never asked about it.

Taking the Lease *before* classification is load-bearing, not incidental
ordering. `_classify_at_pickup` is not a read: it applies `task-type:` and
`semver:` labels to the issue and buys a classifier session to decide them. A
Lane that classified first would write twice onto an issue a rival Run holds a
live Lease on — the fence's own list of side effects names a label write — and
pay for an agent session it is about to be refused. Serial `admit` has always
leased first for the same reason; the Lane path now matches it.

Three rules follow from Parallel mode rather than from the Lease itself. A
contended Lane candidate is refused *candidacy* for the rest of the Run, not
merely this reservation: a released reservation consumes no `max_iterations`
unit, so the scheduler would refill from the same ordered pool and spin on a
remote round trip per turn, where the serial ordered walk moves on by
construction.

That bound must not then misreport itself, which is the second rule. A
candidate a rival *answered* for and one whose Lease probe merely *failed* are
both passed over, but only the first is a fact about the work. The unread ones
are carried into the Pool's terminal classification through its `read_refused`
seam, so a Run whose Lease remote was down ends `preflight_failed` — what the
serial path already returns for the identical fault — rather than claiming
`all_skipped`, a refusal no read established. Keeping the two dispatch modes
from drifting apart on that question is the whole reason `unbound_pool_outcome`
is asked from both.

And a Lane and a serial Iteration of one Run share a single lifecycle object,
because a Run must not contend with itself — one object knows every issue this
Run holds, whichever path took it.

Release runs in a `finally` around the whole Iteration. A Lane's release is
*not* in that `finally` alone, because a Lane task returning is not the end of
the issue's work: a contribution that finishes against a full Integration
backlog parks, its task returns, and it is integrated later from inside
whichever other contribution drains the FIFO. Releasing there would free the
issue for a rival while this Run still meant to publish and close it, and
would then trip this Run's *own* fence so the parked contribution could never
land. So a Lane gives its Lease back where the contribution finishes — the one
seam every contribution reaches whatever its disposition — and the `finally`
is only the net for a Lane that ended before it ever became a contribution.
Per Lane rather than per Run, which is the point in Parallel mode: a Lane that
crashes frees its own issue and nobody else's. The
TTL is overridable through `GIT_LOOPY_LEASE_TTL_SECONDS`, which refuses a
value below one renewal interval: a Lease that expires between beats
manufactures the very false steal §4 exists to bound.

With no Lease in force — the PRDs backend, or a clone with no resolvable GitHub
repository — every one of these paths answers exactly as it did before this
ADR. That is now a live decision rather than a hypothetical one: a Run resolves
the repository it contends on from `origin`'s URL, which is a local config read
and puts no round trip on the path before a Run has even started, and holds no
Lease when that names no `owner/repo`. The resolution is pure and canonical —
every spelling of one repository, scp-like SSH through `https://` with embedded
credentials, reduces to one name — because two clones that disagreed about the
name would write records each read as belonging to *another* repository, and
therefore as expired and stealable. `conformance/repository-identity.json` pins
that decision in all three Orchestrators for exactly that reason. The shell
and PowerShell pure resolvers now exercise every case through their own
Conformance adapters; their ref transport and activation remain pending.
The ports keep the raw path rather than using a URL helper that decodes
escapes or normalizes dot segments, because either transformation can invent
a repository identity the reference Orchestrator would refuse. Only the
repository-identity Owed waivers are discharged by this slice; resolving a
name alone neither takes a Lease nor protects native Pickup.

Refusal is deliberately the safe direction and is always announced: an unleased
Run is only as exposed as every Run was before this ADR, while a Run that
invented an identity would contend on some other repository's ref while
appearing to prevent the very collision it was causing.

One refusal is not safe in that direction, and is handled apart. An unreadable
Lease remote denies the candidate, which is right for an outage because an
outage passes; but a clone that may *never* write the ref — a read-only fork
`origin`, a ruleset forbidding creations under `refs/heads/**`, an expired SSO
authorization — would refuse every candidate on every Iteration and end the Run
having done nothing, blaming a readiness fault it never had. That refusal is
therefore recognised by name and latched once, before any Lease has ever been
held so it can never describe a Run whose credentials were withdrawn mid-flight,
and the Run continues unguarded with exclusivity reported OFF. A Lease this
clone cannot take protects nothing, so refusing the work buys nothing.

Live cross-Run exclusivity is now active for a GitHub-backed Python Run's
**serial** Iterations and its **Lanes** alike, so an issue labelled
`parallel-safe` is guarded on the path that actually works it. shell/PowerShell
transport and retry, independent native renewal, the human mirror, and
Events/Dashboard all remain pending in #390.
