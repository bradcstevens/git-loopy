# Runner-driven sequential Integration with automated conflict-resolution

**Status:** accepted

## Context

Parallel **Lanes** (ADR-0008) each finish on their own branch, and those branches must
reach the base branch. The kit's goal is an **entirely automated** AFK loop with minimal
human intervention — the issues, and any branches the parallelism feature creates, must not
require human input to land. So the serial loop's pull-request philosophy ("open a PR and
let a human review and merge it in QA") does not fit Parallel mode. Something has to merge
the branches, run the quality gates, and close the issues **without a human in the loop**.

## Decision

At the **Wave** barrier the runner runs a serialized **Integration** step. It merges each
Lane's branch into the base branch **one at a time, in ascending issue-number order**,
re-running the **full** relevant `AGENTS.md` feedback loops after each merge as the
load-bearing gate, then closes the issue itself (serial closure semantics — `gh issue close`
plus the `Closes #N` backstop). It never waits on a human.

When a merge conflicts, **or** merges cleanly but the feedback loops fail, the runner reverts
the merge (base stays green) and dispatches a dedicated **auto-resolution agent**: a fresh
Copilot session in a dedicated integration worktree on base, tasked to merge the branch,
resolve the conflicts, make the loops pass, and commit. It retries up to **K = 3** times. If
every attempt fails, the issue falls back to a normal **serial Iteration** (the proven safe
path) with a single automated breadcrumb comment; if serial also cannot land it, the existing
**Strike** machine eventually aborts the run — the kit's built-in "get a human" valve.

## Considered options

- **Open a PR per issue, a human merges it** (the serial loop's PR-mode philosophy) —
  rejected: reintroduces the human step the automation goal forbids.
- **Auto-merge every clean branch with no gate** — rejected: each branch can be individually
  green yet combine into a broken base; the per-Integration full-loop gate is the safeguard.
- **Discard-and-re-queue on any failure (no auto-resolution)** — simpler and never
  machine-merges a conflict, but wastes the branch's work and lands fewer issues; rejected in
  favour of the auto-resolution agent, **deliberately accepting machine conflict-resolution**.

## Consequences

- The runner **machine-merges and machine-resolves conflicts into base** — a deliberate
  reversal of the serial loop's "never merge, a human lands it" stance. The **feedback loops
  are therefore the only safety net** between a bad merge and the base branch, so their
  coverage and correctness are load-bearing for Parallel mode.
- Integration is sequential, so a pathological branch (up to K = 3 resolution attempts) can
  slow a barrier. The win still holds: the expensive creative work parallelizes across Lanes
  while only the cheaper, deterministic Integration serializes.
- A successful **Integration** counts as **Strike** progress; the auto-resolution agent and
  any runner **Checkpoint** do not, by themselves, reset strikes.
- Issues close via runner-driven closure exactly as in serial mode (not via PR-merge), so
  there is one consistent closure model across both modes.
- A `parallel-safe` issue that repeatedly fails Integration degrades gracefully to serial,
  bounding wasted compute while guaranteeing forward progress or an eventual Strike abort.
