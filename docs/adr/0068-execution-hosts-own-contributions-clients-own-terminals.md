# Execution hosts own Lane contributions; clients own terminals

**Status:** accepted

**Supersedes in full:** [ADR-0001](0001-observer-control-model-for-interactive-runner.md),
[ADR-0024](0024-terminal-ownership-and-dashboard-fault-recovery.md), and
[ADR-0010](0010-local-sandbox-per-iteration.md).
**Amends, but does not supersede:** [ADR-0043](0043-a-stop-drains-before-it-cancels.md);
see its revocability clarification. ADR-0058's client distinctions remain in force.

## Context

An in-process Dashboard once observed a peer loop and swapped output sinks to
detach. That model cannot describe a Run whose worker survives the terminal and
whose Dashboard is a separate, repeatable client. A per-Iteration local Sandbox
was blocked by headless harness support, but its more fundamental promise of
blast-radius containment is not one the local Execution host can make. Lane
workspaces now have their own lifecycle, and contributions can run on either
the local machine or GitHub Actions without moving Integration.

## Decision

An **Execution host** is where one **Lane contribution** executes. The
Orchestrator submits an issue, prompt, base revision, resolved model pair,
Effective Skill policy, and Lane identity, and receives a durable outcome and
completed branch identity for its own Integration. The host declares placement,
capacity, and **Isolation grade**; placement does not imply grade. One host is
selected per Run and bound per contribution; running the Orchestrator inside
Actions does not itself make its host remote. Serial Iterations, Integration,
and the shell and PowerShell Orchestrators bind no Execution host. Scheduling,
policy, retries, tracker writes, and publication remain the Orchestrator's
decisions. A host must not silently retry or run two live contributions for
one issue. Its only permitted commit of its own is a close-keyword-free
Checkpoint preserving work.

The closed grades are **workspace separation only** and **machine boundary**.
The local host provides the first: a Lane workspace separates trees, but the
agent retains the operator's filesystem authority, credentials, and network.
GitHub Actions supplies the second with one on-demand workflow run per issue,
not a matrix or a local process dressed as isolation. The grade is disclosed
once per Run, not inferred from host placement or presented as a persistent
Dashboard guarantee. Local blast-radius containment is **refused by grade**,
not deferred until an SDK exposes a headless sandbox. That SDK change alone
does not reopen the decision; a different security contract would need a
separate decision. ADR-0010's policy, degradation, and revisit trigger are
historical, not pending work.

Every Run owns its trace and control artifact; clients attach to that Run,
not to a host. Attach may be repeated or concurrent. Detach disconnects one
client and returns its terminal to the shell; a Dashboard fault reports the
failure and leaves that client attached through the line printer, changing
neither Run outcome nor other clients. A client never returns control to a
shell in a terminal state it did not find: it restores its captured entry
state on every release path, including a fault. A non-interactive client keeps
the established line-printer and JSONL output invariant rather than acquiring
terminal modes. These two invariants survive ADR-0001 and ADR-0024, now owed
by the client rather than an in-process Dashboard or the Run. Only the Run
owns its lifecycle and Wind-down events; Stop crosses the client boundary
as an explicit request.

Lane workspaces and Integration stages belong under the clone's git directory,
identified by the reserved branch namespace rather than by a path shared
with operator worktrees. A live Run salvages its own dirty Lane workspace
before reclaiming it on exit, including Stop; a later Sweep salvages a dead
Run's residue before reclaiming it. Failed salvage alone preserves the
workspace. A salvaged Checkpoint branch is a recoverable Breadcrumb, not
automatically resumed work. Integration remains with the same Orchestrator
instance: it gates the merged result on its private stage, publishes green
base, and verifies closure. Where an upstream exists, base must be at least
as durable as the tracker closure attesting to it before publication counts
as complete; a best-effort push is not sufficient for that transaction.

Host compute has no git-loopy meter. **Report a meter only where its owner
publishes the reading, in the unit and granularity it bills.** The Run
discloses whether compute metering is inapplicable (local), free for the
standard Actions runner on a public target repository, or metered to the
target repository's owner, and directs operators to Actions billing for
the latter. Free here does not promise that every resource is free. No
estimate or minutes ceiling is invented from Lane count. Reopen reporting
only when an owner-published reading is (1) attributable to one Lane
contribution, (2) denominated by its publisher, and (3) readable with
credentials the Run already holds. All three are required.

## Considered options

- Keep the peer-task/sink-swap Dashboard: ties a client's failure and
  lifetime to work it does not own.
- Enable a local Sandbox when upstream headless support arrives: mistakes a
  mechanism for the declared isolation guarantee and leaves the agent's
  credentials and network in the same trust boundary.
- Keep sibling worktrees and retained failed directories: a path is not
  ownership, and salvage makes routine directory retention unnecessary.
- Estimate host minutes or use a count as a billed reading: neither is an
  owner-published contribution-level meter.

## Consequences

The host's outcome is an Integration input, not permission to skip the
Orchestrator's gate or to infer successful publication from a branch name.
Clients can come and go without determining the Run outcome. Historical
in-process and Sandbox instructions in the three superseded ADRs must not
be used as current operator guidance.
