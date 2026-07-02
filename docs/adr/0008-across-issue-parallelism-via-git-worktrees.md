# Opt-in across-issue parallelism via git worktrees

**Status:** accepted

## Context

The AFK runner works triaged issues **strictly one at a time, in place** on the base
branch: an **Iteration** collects a **Pool**, runs one SDK session, and the agent commits
directly to the current branch. The single-issue discipline exists to stop concurrent
agents colliding, duplicating each other's code, or degrading quality. But independent,
well-scoped issues could be worked concurrently to raise throughput **without** giving up
that safety — provided each agent is isolated so it cannot see or clobber another's working
tree. `CopilotClient.create_session` accepts a `working_directory`, so one client can host
several concurrent sessions, each bound to its own directory. No git-worktree machinery
existed before this decision (`copiloop`'s git module had no `worktree` support).

## Decision

Add an **opt-in Parallel mode** (flag `--parallel N` / `COPILOOP_MAX_PARALLEL`, default off;
the serial loop stays the default and byte-for-byte unchanged, with its own orchestrator
reusing the existing primitives — git, gh, session, sinks, pricing).

In Parallel mode the runner dispatches a **Wave** of up to **N** concurrent **Lanes**
(default N=3). Each Lane is one agent working one **`parallel-safe`** issue in its **own git
worktree + dedicated branch** (`copiloop/<run_id>/issue-<N>`, branched from base), created in a
sibling directory **outside** the repo and prepared with a configurable setup command
(`COPILOOP_WORKTREE_SETUP`, with a best-effort auto-detect fallback) so the feedback loops can
run there. Concurrency is **in-process** — one `CopilotClient`, N sessions joined with
`asyncio.gather`, each pinned via `working_directory` — not subprocess-per-agent.

Eligibility is a **human assertion, never an inference**: only issues carrying **both**
`ready-for-agent` **and** the new `parallel-safe` label may run concurrently; the runner
does not derive independence from any dependency graph. A Wave needs at least two eligible
issues (otherwise the round runs serially). A Parallel run **drains everything**: Waves for
`parallel-safe` issues and normal serial Iterations for the rest.

**Explicitly deferred** to a later slice: **within-issue sub-agents / agent teams** that
decompose a *single* issue into concurrently-worked slices (the obra/superpowers
"subagent-driven-development" pattern). The first cut is across-issue only. This deferral is
recorded here on purpose.

## Considered options

- **Evolve the serial loop into "parallel with N=1"** — rejected: forces a rewrite of the
  battle-tested serial path and risks regressing it.
- **Opt-out (parallelize every `ready-for-agent` issue)** — rejected: assumes independence
  by default, exactly the collision risk the single-issue rule guards against.
- **Infer independence from GitHub dependency metadata** — rejected: weak and inconsistent,
  with a high false-confidence risk.
- **Subprocess-per-agent isolation** — unnecessary once per-session `working_directory` was
  confirmed in the SDK.
- **Worktrees inside a gitignored `.worktrees/`** (the prior-art default) — sibling-outside
  chosen instead to avoid nested-worktree quirks.

## Consequences

- `copiloop`'s git module gains worktree lifecycle (add/remove, branch-from-base); a worktree
  pool manages creation, per-worktree setup, and teardown — remove worktrees at the barrier,
  delete integrated branches, keep failed branches as breadcrumbs.
- The per-Iteration **Sandbox** (ADR-0010) becomes **per-Lane**: each Lane's session needs its
  own sandbox policy scoped to **its own worktree** (its own `config_directory` / `settings.json`),
  not the single shared repo-worktree policy the serial path writes. ADR-0010 explicitly names
  parallel execution as the case its per-Iteration, single-`settings.json` model does not yet cover.
- Env vars, branch names, and identifiers follow the **copiloop** naming from ADR-0005
  (`COPILOOP_*`, `copiloop/…` branches, `copiloop` modules), not the retired `RALPH_*` / `ralph/` /
  `ralph_afk` forms still present in today's code.
- Commit accounting becomes **per-Lane** (each branch's pre/post SHA) instead of the global
  `head_sha` diff; the per-worktree **Checkpoint** (ADR-0004) still runs on each Lane branch;
  the **Strike** machine ticks at the **round** level (a Wave or a serial Iteration).
- The live **Dashboard** becomes **multi-active**: the **Queue** lights one row per Lane,
  each with its own timer and per-issue **Log**. Events are attributed by deterministic
  Lane-to-issue assignment, so the `<working issue=N>` marker becomes redundant for
  attribution in Parallel mode.
- New vocabulary (**Wave**, **Lane**, **Integration**, **Parallel-safe**) enters `CONTEXT.md`
  and a `parallel-safe` label enters `docs/agents/triage-labels.md`.
- How finished Lane branches actually reach the base branch is a separate decision — see
  ADR-0009.
