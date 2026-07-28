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
