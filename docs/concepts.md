# Loop Engineering Concepts

> Two mental models explain why git-loopy shapes work into small issues and
> starts every Iteration from a clean context.

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

## What git-loopy provides

- **A loop-engineering workflow.** Vendored skills under
  [`.copilot/skills/`](../.copilot/skills) shape intent, gather evidence, record
  a spec, slice tickets, triage them, and support disciplined execution.
- **A Runner family.** The Python reference Orchestrator is available now;
  shell, PowerShell, and Rust members are planned around one
  [Wrapper contract](wrapper-contract.md).
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
