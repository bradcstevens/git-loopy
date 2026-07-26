# Per-Agent insight facts and Subagent lifecycle enter the Event contract

**Status:** accepted — extends the family-wide Dashboard insight contract (#172); consumed by
[ADR-0021](0021-activity-windows-per-agent.md)

## Context

The **Dashboard** cannot say what any **Agent** is actually running under. The facts exist
inside the runner and are thrown away:

- A **Lane**'s **Routed pair** is resolved once at **Pickup** by `resolve_iteration_model`
  and never emitted. Nothing downstream can tell a `docs` issue on a cheap model from an
  `implementation` issue on the expensive one.
- **Task type** is read from the issue's `task-type:` label at pickup; the pool event
  carries bare refs, so no consumer sees labels.
- `usage.context_window` is not in the Lane-stamped event set, so **Context fill** does not
  exist per Agent — only for the serial Iteration.
- The harness emits `subagent.started` / `.completed` / `.failed` with the spawned agent's
  name, description, model, duration, token total, and tool-call count. git-loopy maps none
  of them, so a **Subagent** fan-out is indistinguishable from a hang: the Subagent's own
  output never reaches the parent stream, and its elapsed time is unaccounted for.
- The harness's usage records also carry `initiator` and a parent tool-call id — the only
  handles that could ever attribute a share of billed **Consumption** to a Subagent.

The family rule (#172) is that renderers *derive presentation only and never infer*. So a
renderer cannot reconstruct any of this locally without becoming the thing that rule exists
to prevent. These facts have to reach the wire or they do not exist.

## Decision

Carry the per-Agent facts on the existing **Event schema**, additively.

- **The pickup event carries the pickup's decisions.** `wrapper.issue.activated` gains
  `task_type`, the resolved model, reasoning effort, context tier, and `routing_source`
  (routed / default / fell back after an unknown or conflicting label). It is emitted on
  every dispatch path, serial and Lane, at exactly the moment **Routing** resolves — and it
  is already the authoritative binding attribution keys off, so no new event type is spent
  on facts that have no life apart from the pickup that produced them.
- **Context becomes Agent-scoped.** `usage.context_window` joins the Lane-stamped event
  set, so **Context fill** attributes to the Agent that produced it rather than to the Run.
- **Subagent lifecycle becomes first-class.** `subagent.started`, `subagent.completed`, and
  `subagent.failed` map to new additive event types under a new `subagents` **Insight
  capability**. `subagent.selected` / `.deselected` are deliberately not mapped: they
  describe which agents a session *may* use, which is configuration, not work.
- **Subagent Consumption is never added twice.** The parent stream's harness-reported
  billing stays the sole source of **Consumption** and Cost. A Subagent's self-reported
  totals are display detail on its own line and are never summed into an issue's tally.
  `initiator` and the parent tool-call id are passed through on the usage event so a later
  slice can attribute a *share* of an existing total without changing any total.
- **Python-first, contract-complete.** The Python Orchestrator observes these truthfully;
  the shell and PowerShell Orchestrators declare the new capability `false` and render the
  em dash, exactly as they already do for tokens, context, and cost. The conformance
  fixtures are written now, so `git-loopy-tui` has a defined target rather than a moving
  one.

## Considered options

- **Derive it in the Python renderer only** — rejected: it makes the renderer infer facts
  the contract does not carry, which is the family drift #172 exists to stop, and leaves
  every other renderer permanently unable to show the same screen.
- **A dedicated `wrapper.routing.resolved` event** — rejected: an extra type, plus its own
  capability negotiation, for facts that are attributes of a pickup that already emits an
  event.
- **Put the facts on `iteration.start` / `contribution.start`** — rejected: those are
  serial-only and rolling-only respectively, so it would be two emitters and a gap.
- **Infer Subagents from the launcher tool call** — rejected: no model, no duration, no
  totals, and it breaks the moment the tool is renamed.
- **Add Subagent totals to the issue's tally** — rejected: the parent usage stream appears
  to already include calls made inside a tool call, so summing would silently inflate the
  one number an operator acts on. Under-reporting a Subagent's line is visible; double
  counting Cost is not.
- **Require shell and PowerShell to emit the same facts** — rejected: they have already
  declared, truthfully, that they cannot observe structured agent events, tokens, or
  context; asking them to fabricate these would trade a visible gap for an invisible lie.

## Consequences

- The Event schema grows additively; `INSIGHT_CAPABILITY_NAMES` grows by one. Existing
  consumers ignore unknown keys and types, so no version break is required.
- **Subagent** and **Agent** enter `CONTEXT.md`; **Context fill** is redefined from
  Iteration-scoped to Agent-scoped.
- Two Orchestrators declare a capability `false` on arrival. That is the contract working
  as designed, but it does widen the gap between the Python reference runner and the ports.
- The replay log becomes the place the open question — whether harness billing already
  includes Subagent calls — gets answered, because `initiator` will be in it. If the answer
  turns out to be "no", this decision's accounting rule is the thing to revisit.
- Subagent lines appear in the per-issue **Log** as well as the **Activity window**, since
  both render the same lines.
