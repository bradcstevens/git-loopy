# One issue, one run: a Lease taken at Pickup and expired by its own clock

**Status:** proposed

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
  PowerShell alike. Python is `asyncio` already (ADR-0008); shell has no threads, though
  `orchestrator.sh:1740` runs a background ticker subprocess for its monotonic clock and is
  the pattern to copy. **If the heartbeat cannot be made to work in all three, the design
  degrades to a long fixed TTL and the Lease becomes much weaker.**
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
