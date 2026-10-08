# The Planner stays outside the Run

**Status:** accepted

Decided by [Decide whether the Run hosts a Planner, and where the human gate
sits](https://github.com/bradcstevens/git-loopy/issues/649).

git-loopy 1.0.0 has one named, human-driven **Planner** Agent profile, but the
Planner remains in the planning phase outside a **Run**. It drafts one complete
graph of executable issues and native dependencies from planning documents; the
deterministic **Orchestrator** continues to consume only approved executable
tickets, and `Spec:`, `PRD:` and `wayfinder:map` issues remain categorically
excluded from Pickup.

The human gate sits after decomposition. The Planner records a complete graph
revision, including proposed `ready-for-agent` and `parallel-safe` labels, and the
**Loop engineer** approves that revision atomically before execution opens.
Approval mechanically applies `ready-for-agent` to the approved tickets.
`parallel-safe` remains an explicit per-ticket human assertion: graph approval
must visibly include each proposed concurrency marking, and omission leaves the
ticket serial-required.

Material changes to tickets, acceptance criteria, dependency edges, or
concurrency proposals invalidate the recorded approval and require a new
whole-graph review. The Planner may use bounded research or prototype detours for
missing evidence, but task-graph design has one accountable Planner rather than
competing planner fan-out.

This preserves the existing two-phase model and the human meaning of eligibility
while still adopting the useful Hermes separation: a Planner designs the task
graph, deterministic code dispatches ready work, and one-shot Agents execute it.
Hosting decomposition inside the Run was rejected because it would make planning
documents Pickup-admissible, require a new autonomous pause/approval lifecycle,
and add another agent path without improving the durable issue graph or its human
gate.
