# The runner picks the oldest eligible issue, and the agent no longer chooses

**Status:** proposed

Implemented by [#389](https://github.com/bradcstevens/git-loopy/issues/389).

Issues were worked newest-first, and nobody decided that. All three Orchestrators call
`gh issue list` with no sort qualifier — `gh.py:1367-1375`, `orchestrator.sh:1120-1128`,
`GitLoopy.Orchestrator.psm1:1253-1258` — and GitHub's unstated default is
`sort=created&direction=desc`. The order git-loopy calls *"source order"* (`gh.py:301-303`,
`rolling_pool.py:350-353`) is an inherited CLI default that has never been a decision.

We sort ascending by `created_at`, the runner takes the head, and the agent is told which
issue it is working. A `priority` label sorts ahead of everything else; **Priority** issues
are themselves ordered oldest-first.

## The order was never the selection signal

Flipping the sort alone would have changed nothing, because in serial mode the code does not
pick the issue at all. The whole pool is rendered into one prompt block (`loop.py:1134`) and
`PROMPT.md:29-34` instructs the agent to self-select by task type — *"Prioritise in this
order... 1. Critical bugfixes... 5. Refactors."* `CONTEXT.md` recorded the consequence
plainly: the **Active issue** was *"self-selected from the pool."*

So list position was a rendering hint competing against an explicit instruction to ignore it.
An issue could be passed over indefinitely and no mechanism existed to notice. That is the
starvation being fixed, and a sort flag alone does not fix it — hence the second half of this
decision, which is the expensive half: **serial Iterations now have a Pickup**, and the
runner binds the issue before the session starts.

## Why not order by task type

Sorting by `(task_type_rank, created_at)` would have preserved `PROMPT.md`'s ordering in code.
It was rejected twice over.

**It recreates the bug.** A `task-type:chore` filed in January waits behind every bugfix
filed forever. Tiering does not remove starvation, it relocates it to the bottom tier — which
is the same complaint, rotated.

**The repository already forbids it.** `CONTEXT.md` defines a **Task type** as selecting a
**Routed pair** and *"nothing else: it does not order or prioritise work"*, and its flagged-
ambiguities log had already reserved the ground: *"Scheduling priority is a separate,
currently unmodelled axis and must not be folded into the `task-type:` label vocabulary."*

**The field is empty anyway.** ADR-0029 records that **zero of 334 closed issues** carry a
`task-type:` label, `bootstrap_labels()` (`init.py:829`) is fed only the triage roles and
`parallel-safe`, and inference is *proposed*, not shipped — and happens **at** Pickup, which
is after selection. Sorting on it would sort on nothing.

**Priority** therefore gets its own label, which is what `CONTEXT.md` said it would need.

## Consequences

- **Two glossary entries reverse.** **Active issue** is no longer self-selected; **Pickup** is
  no longer Lane-only. The **Working marker** survives as attribution and as a disagreement
  signal, but it no longer *binds* — the runner bound the issue before the agent spoke.
- **`PROMPT.md`'s priority list loses its selection role.** It ranks work the agent is not
  being asked to rank any more. It must be rewritten or removed, or it will contradict the
  runner and confuse the agent about whether it may decline the issue it was handed.
- **`--limit 100` inverts its failure mode.** At 100 rows, newest-first hid the oldest issues;
  oldest-first hides the newest. Today's `ready-for-agent` backlog is ~40, so this is latent,
  not live — but it becomes a correctness bug rather than a nuisance the moment the backlog
  passes the page limit, because new work would never be fetched at all.
- **Parallel mode inherits this free.** `RollingPool` already *"validates candidates in FIFO
  order"* (`rolling_pool.py:233-240`) and preserves position across refreshes
  (`rolling_pool.py:350-353`). It was FIFO over a newest-first list, which is why it behaved
  as LIFO. Correcting the source order corrects it.
- **A pin bypasses order and nothing else.** `--issue N` skips the queue but not eligibility
  and   not a **Lease** (ADR-0033); a pinned issue held by another live run fails the
  invocation rather than falling back, because silently working a different issue than the one
  named is worse than stopping.
