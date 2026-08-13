# Loop Engineering Concepts

> Three mental models explain why git-loopy shapes work into small issues, starts
> every Iteration from a clean context, and treats the workflow itself as the
> thing being built.

Loop engineering is not "give the model everything and hope." The loop engineer
keeps each task inside the model's competent envelope, persists intent in
reviewable artifacts, and uses fresh execution contexts instead of carrying an
ever-growing conversation.

## The Smart Zone

Model quality degrades as context grows. A practical planning budget is about
**100k tokens per focused session**, even when a model advertises a larger
window. The exact threshold varies; the operating rule does not: keep work small
enough that the agent can still reason about the whole slice.

That is why the [workflow](workflow.md) uses `/wayfinder` when planning itself is
too large, `/to-tickets` to create tracer-bullet issues, and one Active issue per
serial Iteration. A vertical slice should deliver one verifiable behavior
through every affected layer without requiring the agent to hold the rest of the
roadmap in memory.

## The Memento Model

Every Iteration starts with a fresh Copilot CLI context. The agent recovers from
the prompt, `AGENTS.md`, the Active issue, domain docs, ADRs, and recent commits;
it does not inherit an opaque summary of the previous conversation.

This is a feature. Durable state travels through explicit artifacts:

- **Repository history.** Commits preserve implementation and changes to domain
  context.
- **Issue tracker state.** Specs, tickets, comments, labels, dependencies, and
  closures preserve intent and progress.

If information matters to a later Iteration, put it in one of those reviewable
surfaces. Do not rely on scratchpads or compaction sediment that the next
context cannot verify.

## The Compounding Workflow

The first two models explain how one Iteration succeeds. This one explains why
the effort is worth encoding at all.

A conversation with an agent produces one result and then ends. Whatever you
learned about how to prompt it, which checks caught the mistake, and what the
model got wrong dies with the session. A **workflow** — issue shape, acceptance
criteria, routed model, feedback loops, guardrails, review gate — produces the
same result on demand, tens or hundreds of times, and every improvement to it is
paid back by every subsequent Run.

That is the practice git-loopy is built for: encoding specialized engineering
knowledge, yours or your organization's, into workflows that run repeatably. Two
consequences follow, and both are design rules rather than preferences:

- **Deterministic work belongs in code, not in an agent.** A linter, a test
  suite, a status transition, or a route lookup is faster, cheaper, repeatable,
  and testable. An agent is for judgment and flexible knowledge work. That split
  — engineers own intent and final judgment, agents own reasoning, code owns
  routing, validation, and gates — is what makes a Run's outcome explainable.
- **More agents is not more leverage.** Spawning a crowd of subagents multiplies
  token spend and the number of paths by which unreviewed output reaches a
  branch, while leaving nothing durable behind to improve. One bounded Iteration
  against a real gate beats five speculative ones.

The payoff is **meta-engineering**: you stop being the person who edits the
application and become the person who improves the system that builds and
operates it. Evidence from a Run — a red gate, a wasted Iteration, a mis-routed
task — is fed back into the workflow, not just into the branch
([ADR-0031](adr/0031-encoded-workflows-retire-the-loop-name.md)).

## What git-loopy provides

- **A loop-engineering workflow.** A pinned catalog of skills — whose source of
  record is
  [`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills),
  installed by `git-loopy init` into git-loopy's own config home and refreshed
  on every Run — shapes intent, gathers evidence, records a spec, slices
  tickets, triages them, and supports disciplined execution.
- **A Runner family.** The Python reference Orchestrator plus the shell and
  PowerShell members are shippable today; a Rust Orchestrator is planned. All of
  them implement one [Wrapper contract](wrapper-contract.md).
- **Guarded Iterations.** The Pool, Active issue, Working marker, Strikes,
  Checkpoints, push durability, Dashboard, and Summary make autonomous work
  bounded and observable.
- **Stack-agnostic feedback.** Repository-specific tests, type checks, lint, and
  build commands live in `AGENTS.md`, so the loop validates work through the
  same interfaces as a human contributor.

---

**Next:**
- [`docs/workflow.md`](workflow.md) - the complete planning-to-review loop.
- [`docs/runners.md`](runners.md) - the Runner family and Iteration contract.
- Back to [`README.md`](../README.md) - the git-loopy front door.
