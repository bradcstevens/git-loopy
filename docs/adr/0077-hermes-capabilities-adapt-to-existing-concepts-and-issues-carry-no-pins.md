# Hermes capabilities adapt to existing concepts, and issues carry no pins

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide which Hermes capabilities 1.0.0 carries, surface by
surface](https://github.com/bradcstevens/git-loopy/issues/654).

git-loopy 1.0.0 follows Hermes' Kanban orchestrator (no goal) pattern, but it
does not import Hermes' surfaces wholesale. Each Hermes capability is kept,
adapted to an existing git-loopy concept, or dropped. The full keep / adapt /
drop list is the resolution of the deciding issue; this record keeps only the
calls a reader coming from Hermes would find surprising.

## An issue carries no Skill or model pin

A Hermes card can pin extra Skills and a model. A git-loopy issue can do
neither.

- **Skills** reach an **Agent** only through the **Agent profile** it runs
  under, which its **Task type** selects (ADR-0078). Tracker text that could
  add a Skill would widen the closed-world
  **Skill policy** (ADR-0015, ADR-0025) from outside it.
- **Model and effort** come from **Routing** (ADR-0035) and, once delivered, the
  evidence behind **Dynamic routing**
  ([ADR-0057](0057-live-evidence-guides-per-issue-routing.md)). The per-issue
  lever stays the **Task type** label: relabel the issue to change its route.
  **Route labels** remain observational (ADR-0060).

## No model-call Estimate

Hermes estimates a card's tokens with a model call before the work starts.
git-loopy does not: unknown stays unknown, and an unknown figure is shown as
unknown, never as an estimate. The only forecast it offers is measured history,
labelled as history: what past issues of the same **Task type** on the same
**Routed pair** actually consumed.

## Events are the only transition-observation surface

Hermes registers plugin hooks on task transitions. git-loopy adds no in-process
hook or plugin API that runs user code inside the **Orchestrator**. Observers
read the versioned, contract-checked **Events** a Run emits (the **Event
schema**), and every transition a board shows gets an Event. Copilot CLI hooks
such as `.github/hooks/` remain harness configuration.

## Hermes' auxiliary models map to existing roles or are dropped

None of Hermes' auxiliary models becomes a new in-Run model call; git-loopy's
in-Run single calls remain the **Task-type classifier** and the **Route
selector** (ADR-0078). Of Hermes' auxiliary models:

- the specifier and decomposer fold into the out-of-Run **Planner**, behind
  human approval (ADR-0071), so nothing decomposes work inside a Run;
- the reviewer is the **Reviewer** (ADR-0072);
- title generation, the profile describer and the skill curator are dropped.

A profile's description is written by a person, published with each **Agent
profile**, and read by people and the Planner, never matched by a model in-Run.

## Considered options

- **Per-issue pins as Hermes has them.** Rejected: they would make tracker text
  an authority over the Skill policy and the route.
