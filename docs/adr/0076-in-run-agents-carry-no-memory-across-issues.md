# In-Run Agents carry no memory across issues

**Status:** accepted

Decided by [Decide whether Agent profiles carry memory across
issues](https://github.com/bradcstevens/git-loopy/issues/652).

No in-Run **Agent profile** carries memory from one issue to the next. Every
**Agent** a **Run** starts — a serial **Iteration**'s session, a **Lane**'s
session, a **Recovery** session, or a **Reviewer** — begins fresh, as the
[Memento Model](../concepts.md#the-memento-model) requires. Continuity reaches it
only through reviewable, versioned surfaces: the issue tracker and the
repository's history.

Hermes treats a "named profile with persistent memory" as a defining trait of its
Kanban workers. git-loopy declines it. Memory that only an agent reads is the
compaction sediment the Memento Model rules out: no reviewer gates it, no
**Feedback loop** checks it, and a later Agent cannot tell a sound lesson from a
stale one. It is also the shared-history confound the Hermes research named,
because later workers could see earlier runs' work.

## The Orchestrator enforces it in code

A prompt rule cannot keep injected memories out of an Agent's context, so the
**Orchestrator** turns ambient harness memory off for every in-Run Agent. That
is deterministic work, and it belongs in code:

- Session creation passes the SDK `memory={"enabled": False}`, overriding the
  runtime default that applies when no memory configuration is given.
- The permission handler refuses the harness's cross-session history tool
  (`session_store_sql`).

A Wrapper-contract check proves both, so a member that leaves either reachable
fails Conformance rather than drifting silently.

Before this decision the Runner set neither, so the runtime default applied. Run
logs recorded no memory writes, but they recorded 39 cross-session history reads
across 7 Runs.

## Where continuity lives instead

No new surface is added. Each kind of continuity Hermes gets from memory already
has a reviewed home:

- **Learned conventions** go into `AGENTS.md`, `CONTEXT.md`, an ADR, or a Skill
  (as a paired change, ADR-0064). They land as commits through the normal gates.
  That is the Compounding Workflow: improve the workflow, not one agent's recall.
- **Failure notes for the same issue** stay on the issue: comments, the
  per-issue Review handoff (ADR-0072), and **Strike** evidence. A fresh Agent
  already reads them there.

An in-Run Agent edits workflow documents only when its own issue requires it.
That edit is part of its diff and passes the **Review stage**. A lesson that
goes beyond the issue leaves the Run as a Follow-up on the tracker: in the
closing comment or as a new issue. The **Loop engineer** and the **Planner**
triage it into a workflow change. A Run never rewrites its own workflow outside
a reviewed diff.

## The Planner is outside this rule

The rule binds in-Run Agents only. The **Planner** is human-driven and runs in
the Loop engineer's own harness outside a Run (ADR-0071), so it may use whatever
memory that harness provides. Its only output that counts is the approved,
recorded graph revision. Every fact that graph relies on must be written into its
issues, because no in-Run Agent can read the Planner's memory.

## Considered options

- **Reviewable memory for some or all profiles.** Rejected: it reintroduces
  sediment that has no curator and no gate.
- **A committed, append-only learnings file.** Reviewable in principle, but
  nothing prunes it. It would become the same sediment under version control.
- **A prompt rule instead of code.** Cheaper, but it can't be verified and it
  still lets injected memories reach the Agent.
- **Disabling memory writes but allowing session-history reads.** Rejected:
  reading another session's history is memory by another name.
