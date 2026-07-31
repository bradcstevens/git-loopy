---
name: next
description: Route workflow continuation from live project state. Use when a workflow skill concludes or the user asks what to do next.
---

# Route the Workflow

This skill is the model-invoked router for the engineering flow. Inspect the
current state and return one recommendation. Leave the repository and issue
tracker unchanged.

## 1. Refresh the durable state

Locate `docs/agents/issue-tracker.md`. If the repository has not been configured
for these skills, make `/setup-agent-skills` the sole candidate. Otherwise read
the file and refresh the workstream referenced by the conversation from its
configured tracker: issue or PR state, labels, assignees, comments, sub-issues,
and blockers. Inspect the local branch, commits, and diff when review or
publication may be next.

When the conversation names no workstream, review the open workflow-bearing
issues and their relationships to find the active maps, specs, tickets, and PRs.
Use live records rather than session summaries because concurrent sessions may
have changed them.

This step is complete when every candidate action has current state and blocker
information from its durable source.

## 2. Find the earliest unresolved gate

The workflow is composable, not a fixed checklist. For each active workstream,
choose the first matching transition:

| Current state | Next route |
| --- | --- |
| The repository is not configured for the engineering skills | `/setup-agent-skills` |
| The current thread is near its useful context limit or must branch into a fresh session | `/handoff` |
| The same conversation is at an intentional phase break and can continue from a summary | `/compact` |
| An idea outside a codebase still needs sharpening | `/grill-me` |
| An idea in a codebase still has human decisions | `/grill-with-docs` |
| The destination is too foggy or large for one planning context | `/wayfinder` |
| A hard bug lacks a tight command that reproduces it | `/diagnosing-bugs` |
| A factual unknown can be resolved from primary sources | `/research` |
| A decision depends on information only another person can provide | `/to-questionnaire` |
| A runnable behavior or visual answer is cheaper than more discussion | `/prototype` |
| Domain language itself blocks the decision | `/domain-modeling` |
| A module interface, seam, or boundary needs designing | `/codebase-design` |
| The destination is agreed but no durable spec exists | `/to-spec` |
| A spec exists but executable tracer-bullet tickets do not | `/to-tickets` |
| A raw incoming issue or external PR needs readiness work | `/triage` |
| An in-progress merge, rebase, or cherry-pick has conflicts | `/resolving-merge-conflicts` |
| A concrete behavior should be built test-first without the broader ticket flow | `/tdd` |
| An unblocked `ready-for-agent` ticket or small agreed change is available | `/implement` |
| Implemented work or review fixes still need a fixed-point review | `/code-review` |
| Reviewed work remains local or the current branch lacks its PR | `/push` |
| No delivery work is active and codebase health needs a survey | `/improve-codebase-architecture` |
| The user wants a stateful learning path | `/teach` |
| The task is to write or revise an agent skill | `/writing-great-skills` |
| The accepted work is closed, reviewed, and published | No next route: report completion |

Apply these flow rules:

- Keep `/grill-with-docs`, `/to-spec`, and `/to-tickets` in one unbroken context.
  Start each `/implement` ticket in a fresh context. A genuinely small change can
  move directly from grilling to `/implement` in the current context.
- Use `/compact` only at an intentional phase break where a summary is enough.
  Use `/handoff` when a fresh session or preserved branch of the current thread
  is required.
- Bridge a prototype detour with `/handoff` in both directions when the original
  thread must survive. Route the validated answer back into the main flow.
- Route source-answerable gaps to `/research` and answers held by another person
  to `/to-questionnaire`. Resume the decision flow with either result in
  `/grill-with-docs` or `/to-spec`.
- `/to-tickets` output is already agent-ready; reserve `/triage` for work that
  arrived raw.
- If a Wayfinder map has an open frontier, continue `/wayfinder`. When the
  destination is clear and no decision ticket remains, route to `/to-spec`, not
  directly to implementation unless the effort proved genuinely small.
- `/implement` drives `/tdd` internally and closes with `/code-review`. Route
  directly to those skills only for their standalone branches.
- If review finds defects, route back to `/implement` with the findings. After a
  bug fix, route to `/improve-codebase-architecture` only when the diagnosis
  exposed a missing seam or structural cause.
- A candidate selected by `/improve-codebase-architecture` becomes an idea for
  `/grill-with-docs`; the survey does not implement it.
- Reach for `/domain-modeling` or `/codebase-design` directly only when the
  vocabulary or module shape is itself the unresolved gate.

This step is complete when every candidate is classified as ready, blocked, or
complete.

## 3. Rank the actions

Rank ready actions before blocked actions. Within each group, prefer:

1. The workstream continued by this session.
2. The action that clears the most downstream blockers.
3. The oldest tracker item, then the lexical target name, as stable tie-breakers.

Return at most one action. A blocked action must name the condition that makes
it ready.

This step is complete when the ordering follows all three rules and every
blocked action carries a checkable readiness condition.

## 4. Return the recommendation

Use this shape:

````markdown
1. **<concrete action>** - `/<route>` - <HITL | AFK-safe>
Target: <linked issue, PR, map, spec, branch, document, or current conversation>
State: <Ready | Blocked by ...>
Context: <Continue here | Fresh session>
Why now: <one sentence grounded in live state>

Prompt:
```text
/<route> "<concise imperative naming the target and desired outcome>"
```
````

Write the prompt as one physical line beginning with the exact skill invocation.
Use straight ASCII quotes and spaces, and keep all labels and explanation outside
the code fence. For `/compact`, use `/compact` with no argument. Match `Context`
to the flow rules above. For `/handoff`, use `Continue here` and say that its
output opens the fresh session.

Mark an action `AFK-safe` only when its target is fully specified and requires no
new human judgment. Otherwise mark it `HITL`. For a terminal workstream, return:

```markdown
**Complete:** <why no further workflow skill is needed>.
```

Routing is complete when every active candidate has been classified and every
recommendation names a live target, an exact invocation, a one-line terminal
command in its own code fence, the correct context, and any blocker.