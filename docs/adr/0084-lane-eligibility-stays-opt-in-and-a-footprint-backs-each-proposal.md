# Lane eligibility stays opt-in, and a Footprint backs each `parallel-safe` proposal

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide whether concurrent dispatch becomes 1.0.0's
default](https://github.com/bradcstevens/git-loopy/issues/660).

**Reaffirms** [ADR-0008](0008-across-issue-parallelism-via-git-worktrees.md) and
[ADR-0074](0074-the-serial-drain-waits-for-the-whole-lane-cohort.md): only `parallel-safe`
issues run concurrently, and the runner never infers independence. **Extends**
[ADR-0071](0071-the-planner-stays-outside-the-run.md) with the evidence a Planner's
`parallel-safe` proposal carries to graph approval.

## Context

The 1.0.0 target pattern is Hermes' Kanban orchestrator (no goal). Its dispatcher starts
every card whose dependencies are done, so the measured run fanned three workers out at once
on the strength of the graph alone: independence was implied by missing edges, never
asserted ([evidence](../research/wanderloots-hermes-coordination/companion-post.md)). The
question was whether git-loopy 1.0.0 should dispatch the same way.

Most of the question was settled before it was worked:

- Concurrent dispatch is already the Python default. A bare `git-loopy` always uses
  **Rolling dispatch**, and serial runs only as an Iteration driver
  ([ADR-0067](0067-python-retires-the-mode-switches.md)).
- The bound **Execution host**'s declared capacity is the only **Lane cap**, and the adaptive
  controller is the only authority under it
  ([#456](https://github.com/bradcstevens/git-loopy/issues/456),
  [ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md)).
- The **Planner** only proposes `parallel-safe`. The **Loop engineer** approves each marking
  explicitly, and a ticket without one stays **Serial-required** (ADR-0071).
- Serial-required work waits in the **Serial drain**, which `priority` crosses (ADR-0074).

That left the eligibility default and the evidence a proposal carries.

## Decision

1. **Lane eligibility stays opt-in.** Only a ticket that carries `parallel-safe` takes a
   **Lane**. Nothing is inferred from the dependency graph: a missing edge between two
   tickets makes neither one Lane-eligible.
2. **A Footprint backs every Planner proposal.** Each `parallel-safe` proposal states the
   ticket's **Footprint**: the paths it is expected to change, plus any shared counter or
   registry it is expected to bump, such as a contract version or a document number. It
   must not overlap the Footprint of any other `parallel-safe` proposal in the same graph
   revision that no chain of dependencies orders against it. Dependencies already keep two
   ordered tickets from running at once, so only tickets that could be Ready together are
   compared. Collisions with other graphs' tickets are left to **Integration** and
   **Recovery**.
3. **The Footprint serves approval only.** It lives in the recorded graph revision, where
   the Loop engineer sees it beside each marking. Changing a Footprint is a material change
   to a concurrency proposal, so it invalidates the approval (ADR-0071). No **Agent**,
   **Reviewer** or **Orchestrator** reads it: the Orchestrator reads only the label, and
   the ticket body goes on describing behaviour, not files.
4. **The Lane ceiling and the Serial drain do not change.** #456's host-declared ceiling and
   ADR-0074's Serial drain, with `priority` crossing it, stand for 1.0.0, as do **Serial
   fallback** and **Recovery handoff**. If Footprint-backed proposals raise `parallel-safe`
   supply, ADR-0074's reopen triggers are the safeguard: a latch that drains more than 6
   Lanes, a latch-to-grant wait over 120 minutes, or the first Actions-host Run that drains
   a Lane reopens [#428](https://github.com/bradcstevens/git-loopy/issues/428).

## Scope

- Outside a Planner graph, a human still applies `parallel-safe` directly, as today. The
  Footprint binds Planner proposals only, including a Follow-up the Planner adopts in a
  graph revision
  ([ADR-0082](0082-an-acceptance-ticket-closes-a-code-graph-and-the-orchestrator-closes-its-spec.md)).
- The **Acceptance ticket** follows the same rule. Every other ticket in its graph blocks
  it, so its only possible concurrent peers are other graphs' tickets.
- The Python path only ([ADR-0073](0073-language-paths-release-and-gate-independently.md)).
  Shell and PowerShell stay dormant with their operator-selected flags (ADR-0067).
- Delivery is a paired Skill change
  ([ADR-0064](0064-a-paired-skill-change-is-published-proved-then-pinned.md)): the
  Planner, today `/to-tickets`, states each Footprint and shows every proposed marking with
  it at approval. The pinned `/to-tickets` adds `parallel-safe` "only when the ticket can
  land beside another ticket without colliding", states no evidence, and shows only title,
  blockers and deliverable in its approval quiz.

## Considered options

- **Graph-derived concurrency, as in Hermes.** Any approved ticket with no open blocker
  takes a Lane. Rejected: Hermes' three concurrent workers were research workers, and
  "Research does not need Git" (companion post). In one repository a missing edge says
  nothing about two tickets changing the same files, and ADR-0008 already rejected
  inferring independence from dependency metadata as weak, inconsistent and falsely
  confident.
- **Opt-out.** Every ticket is Lane-eligible unless a `serial-required` label withholds it.
  Rejected for ADR-0008's reason: it assumes independence by default.
- **A one-line reason, or the label alone.** Rejected: neither gives the Loop engineer
  anything to check, and neither catches two tickets that bump the same counter.
- **A Footprint the Agent sees and the Reviewer checks.** Rejected: paths in a ticket steer
  the Agent toward a chosen implementation, and a finding for every overrun sends
  legitimate changes into remediation, against completion. An overrun that collides with
  nothing does no harm.
- **A Footprint the Orchestrator measures** and records as a Run Event. Rejected for 1.0.0:
  the Runner would read something other than the label, and a collision, not an overrun, is
  what matters. Collision outcomes belong to the Integration and Recovery Events no Run
  records yet.
- **A lower 1.0.0 ceiling**, such as min(host capacity, 6), or **a different Serial drain**,
  such as age-fenced admission. Rejected: supply, not capacity, bounds the drain today, and
  ADR-0074's measured reopen triggers act on evidence. ADR-0074 already rejected age-fenced
  admission as the default.

## Evidence

- **Completion.** In the operator's Run logs, 26 of 38 Lane contributions published (68%),
  against 81.6% same-Run closure for serial Pickups
  ([#657](https://github.com/bradcstevens/git-loopy/issues/657),
  [findings](https://github.com/bradcstevens/git-loopy/blob/7a195604e7041d0a892660edbe88e56bd3eee05d/docs/research/completion-baseline.md)).
  The sample is small and the denominators differ, but nothing shows Lane work completing
  better. Concurrency buys throughput; the 1.0.0 map judges options by completion and
  quality.
- **Serial waits.** ADR-0074's simulation puts the first Serial-required wait at 183 minutes
  when `parallel-safe` supply bounds the drain, and at 687 minutes when 16 Lanes do. Raising
  supply moves a Run from the first case toward the second.
- **Supply.** 69 closed issues carry `parallel-safe`; 1 of the 5 open `ready-for-agent`
  issues does.
- **Shared counters.** Parallel changes in this repository have collided on ADR numbers
  without a merge conflict, because each file has its own slug: commits `7d3f4590` and
  `ab337970` renumbered ADRs to 0078 and 0080 after the fact. A path list alone would not
  have caught them, so a Footprint names the counters as well.
