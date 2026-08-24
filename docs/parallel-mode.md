# Parallel mode

By default a Run works **one issue at a time**. **Parallel mode** is the opt-in
execution mode in which the runner works several independent issues at once,
each isolated in its own git worktree. This page is the operator's guide to
turning it on and reading what it does. [ADR-0020](adr/0020-rolling-dispatch-with-bounded-green-integration.md)
records the design; §12 of the [Wrapper contract](wrapper-contract.md) defines
the Events; [`CONTEXT.md`](../CONTEXT.md) defines every term in bold below.

## Before you turn it on: does your distribution have it?

Parallel mode is a **scheduling** capability, and not every member of the
**Runner family** has one. Every Run declares what it can schedule on its
`wrapper.run.start` record:

```json
"parallel_capabilities": {
  "parallel_mode": true,
  "rolling_dispatch": true,
  "integration_backlog": true,
  "adaptive_lane_limit": true,
  "contribution_events": false
}
```

Today the **Python Orchestrator** is the only member that schedules **Lanes**.
The shell and PowerShell Orchestrators declare every key `false`. A distribution
that declares `parallel_mode: false` will **refuse** a **Lane cap** above 1 at
preflight rather than accept it and run serially:

```
git-loopy: a Lane cap of 3 was requested, but the shell Orchestrator declares parallel_mode unsupported.
git-loopy: this distribution has no Rolling dispatch scheduler, so it cannot fill a second Lane.
git-loopy: unset GIT_LOOPY_MAX_PARALLEL or set it to 1 to run serially, or use a distribution whose parallel_capabilities.parallel_mode is true.
```

That refusal is deliberate. A silently serial Run looks exactly like a Parallel
Run that found no eligible work, so accepting the flag and ignoring it would
leave you unable to tell an unimplemented feature from an unlabelled backlog.

`contribution_events: false` means the same kind of honesty about the record: the
**Lane contribution** lifecycle Events are reserved in the contract but have no
producer yet, so a Parallel Run still writes legacy **Wave**-shaped rows. Nothing
about how the Run *behaves* depends on that key — it tells you what a replay log
will contain.

## Turning it on

Set the **Lane cap**:

```bash
GIT_LOOPY_MAX_PARALLEL=3 git-loopy
```

Optionally give each worktree a setup command — dependency installation, a
virtualenv, whatever a fresh checkout of your repository needs before the
feedback loops can run:

```bash
GIT_LOOPY_MAX_PARALLEL=3 GIT_LOOPY_WORKTREE_SETUP='npm ci' git-loopy
```

A cap of `1` (or unset) is the ordinary serial loop, unchanged.

## Eligibility is yours to assert: `parallel-safe`

The runner **never infers** that two issues can be worked at the same time. An
issue enters Parallel mode only if a human has added the `parallel-safe` label
alongside `ready-for-agent`. `git-loopy init` seeds the label; you apply it.

Apply it to an issue you believe is genuinely independent: it does not need
another open issue's changes, and its likely diff does not overlap theirs. Two
issues that both rewrite the same module are both `ready-for-agent`, and neither
is `parallel-safe`.

Everything eligible but unlabelled is **Serial-required**: unlabelled issues,
pull requests, and local-markdown items. A Parallel Run still drains them, one
serial **Iteration** at a time, with exclusive use of the base worktree.

If nothing carries the label, the Run tells you so rather than looking broken:

```
⇉ serial iteration  (0 eligible parallel-safe issues — no ready-for-agent issue carries parallel-safe)
```

That is a **Serial fallback**, and it is reported on the operator's own output —
not only in the Event stream — because you are the only one who can fix it. The
other two reasons it can give are that every `parallel-safe` issue it found was
already worked this Run, and that a candidate could not be read.

## The Lane cap is a ceiling, not a target

`GIT_LOOPY_MAX_PARALLEL` is a safety and resource bound. **Rolling dispatch**
fills **Lanes** continuously — a Lane is refilled the moment its work is handed
off, with no barrier round waiting for its neighbours — but it will deliberately
leave capacity idle. A Run that sits at two Lanes under a cap of five is not
malfunctioning. The reasons it holds back:

- **A small eligible Pool.** There is nothing `parallel-safe` left to start.
- **A blocked candidate.** An issue carrying an open `blocked_by` dependency — or
  one whose dependencies the **Membership read** could not determine — is refused
  Lane candidacy, so it is never reserved and never released. It stays cached, and
  the next refresh that sees its last blocker closed makes it a candidate again
  mid-Run, with nothing restarted.
- **Integration backpressure** (below).
- **A contracted Effective Lane limit.** The number of Lanes the runner may fill
  *right now* starts below your cap and moves against **Pressure signals**:
  sustained API rate limiting, AI-credit burn against a configured ceiling, host
  or worktree-setup load, and the **Integration backlog**. It contracts quickly
  and expands one Lane at a time against sustained evidence of health, and never
  above your cap, which never moves. A signal the Run cannot observe is reported
  *unknown* — never estimated, and never used as evidence that expanding is safe.

Each authoritative change emits `wrapper.concurrency.changed` carrying both the
immutable configured cap and the current effective limit.

## Integration: the serialized stage, and its backpressure

Finished Lane work does not go straight to your base branch. Each **Lane
contribution** is merged into its own private **Integration stage**, where the
full feedback loop is re-run against the latest published green base. Only a
contribution that passes *there* is published, and its issue is closed only once
that publication is verified. A red or conflicting result is never observable on
the base branch, so there is nothing to undo. A conflict or a failing loop gets a
bounded runner-driven recovery attempt — at most three — inside that same stage.

**Integration** is serialized: it handles one contribution at a time. The
**Integration backlog** feeding it holds at most **two** — one integrating plus
one waiter, admitted in finish order. A third finisher **parks**: it holds its
Lane and waits.

That is **Integration backpressure**, and it is why the Lane cap is a ceiling.
While the backlog is full, Rolling dispatch stops *starting* new work; Lanes
already running finish normally, nothing is cancelled, and refill resumes the
instant a slot frees. It exists to stop unbounded branch staleness — the further
a Lane's branch drifts from a moving base, the more of its verified result is
wasted re-verifying.

Practically: raising `GIT_LOOPY_MAX_PARALLEL` past the point where Integration
saturates buys nothing. Integration, not the Lane count, is the governing
resource.

## Where the workspaces live, and which branches are ours

A **Lane workspace** — and the private **Integration stage** its contribution is
gated in — is a git worktree, and both are placed inside your repository's own
git directory:

```
<repo>/.git/git-loopy/<run_id>/issue-<N>            ← the Lane workspace
<repo>/.git/git-loopy/<run_id>/integrate/issue-<N>  ← its Integration stage
```

That location is chosen so a live Lane cannot get in the way of the very
commands the agents in it are running. The git directory is not *content* in any
working tree, so a workspace never appears in `git status`, cannot be picked up
by `git add -A` (and produces no embedded-repository warning), survives `git
clean -ffxd`, and is skipped by tree-walking feedback loops — with **no
`.gitignore` entry**, so nothing about your repository has to change to make it
so. It is also per-clone: two clones of the same repository each get their own
workspaces, neither can see the other's, and deleting a clone deletes its
workspaces with it.

You do not have to clean anything up in the normal case: a workspace is torn
down as soon as its contribution finishes.

**`git-loopy/` is a reserved branch namespace.** Every branch the runner cuts
for itself lives under it — `git-loopy/<run_id>/issue-<N>` for a Lane and
`git-loopy/<run_id>/integrate/issue-<N>` for its stage — and it is the *only*
thing git-loopy will ever use to decide that a workspace is its own to reclaim.
Don't put your own branches there.

Reading ownership from the branch rather than from a directory is what makes
residue safe to identify. Earlier Runs placed workspaces in a sibling
`<repo>.worktrees/` directory, which an operator's own worktrees could also be
living in; sweeping that directory by location would take work nobody asked
git-loopy to touch. A leftover from those Runs is still recognisable — it is on
a `git-loopy/` branch, wherever it sits — while a worktree of yours next to it
is not, and never will be.

## Sweep: what happens to residue nobody is holding

A workspace is torn down as soon as its contribution finishes, and again at the
Run's own exit if an exception or a **Stop** ended it instead — but a hard kill
or a lost power cable runs no code at all, so some residue survives every
in-process handler. **Sweep** is what reclaims it.

Every Run sweeps at startup, and `git-loopy sweep` does the same on demand for
when nothing is running:

```bash
git-loopy sweep --dry-run   # report exactly what would be removed
git-loopy sweep             # remove it
```

A sweep that reclaimed nothing prints nothing, so on a clean machine both
commands are silent and any output at all is news.

What a sweep is allowed to touch is decided by one thing at a time:

- **Whose residue is it?** Only a Run that can be *proven* dead. Each Run holds
  an OS advisory lock on its own control artifact for as long as it lives, so a
  free lock means the Run is gone and its workspaces are reclaimable. A Run
  still holding its lock is never touched — which is what lets two Runs share a
  clone, including from *different worktrees* of it, since a sweep looks for
  that lock in every worktree the clone registers. Where the lock cannot be
  read at all, liveness is *unknown* rather than dead, and nothing is reclaimed.
- **Is any of it unfinished work?** A dirty workspace is committed to its own
  Lane branch as a Checkpoint — **salvaged** — before the directory goes. Work
  is never destroyed, so the only workspace a sweep leaves behind is one whose
  salvage failed. Salvaged work is recoverable, not resumable: a later Run cuts
  a fresh Lane branch for the issue rather than continuing that one.
- **Is the branch still worth anything?** A stage branch is always collected.
  A Lane branch is collected once it is *resolved* — merged into the branch
  you are on, or belonging to an issue that has since closed. An unmerged
  branch for an issue still open stays, because it may be the only copy. So
  does one whose last commit is a Checkpoint: an issue closing tells you the
  issue was settled, not that anyone ever looked at work its author never
  committed. That is what makes a salvage worth performing — it survives the
  sweep that rescued it, and every sweep after.
- **Is the directory empty?** `git worktree remove` takes only the leaf it is
  given, so run and `integrate/` directories pile up empty forever. A sweep
  removes a directory only when it is genuinely empty, which is why one shared
  with your own worktrees is safe to point it at.

A sweep is not work: it emits no events, produces no strikes, and never appears
in a Run's summary. A Run that swept an issue's residue did not work that issue.

## Interleaving with serial work

When the runner finds **Serial-required** work, serial demand latches: refill
stops, the Lanes already running drain, and one serial Iteration is granted
exclusive use of the base worktree. Rolling dispatch then gets one full refill
turn before serial demand can latch again, so neither side starves the other.

A serial Iteration granted *alongside* remaining eligible Lane work is
interleaving, not a fallback, and is reported as neither.

## What you will see

- Each Lane is one active row in the **Dashboard**, with its own timer and
  **Log**.
- The **Queue** accounts for an issue across every contribution it took.
- Per-Lane records in `.git-loopy/logs/<iso>-<run_id>.jsonl` are attributed to
  their contribution, so a Lane being refilled never reattributes earlier work.

## Related reading

- [Wrapper contract §12](wrapper-contract.md#12-event-schema-phase-1-must) — the
  Event schema, the capability manifests, and the Integration bounds.
- [ADR-0020](adr/0020-rolling-dispatch-with-bounded-green-integration.md) — why
  rolling refill replaced the barrier round.
- [ADR-0008](adr/0008-across-issue-parallelism-via-git-worktrees.md) and
  [ADR-0009](adr/0009-runner-driven-integration-and-auto-resolution.md) — the
  original worktree-isolation and runner-driven-Integration decisions, partially
  superseded by ADR-0020.
- [`docs/runners.md`](runners.md) — which Orchestrator has what.
