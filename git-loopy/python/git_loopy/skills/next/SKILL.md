---
name: next
description: Route workflow continuation from live project state. Use when a workflow skill concludes or the user asks what to do next.
---

# Route the Workflow

This skill is advisory: inspect the current state and return recommendations. Leave
the repository and issue tracker unchanged.

## 1. Refresh the durable state

Read `docs/agents/issue-tracker.md`, then use `gh` to refresh the GitHub state of
the workstream referenced by the conversation: issue or PR state, labels,
assignees, comments, sub-issues, and blockers. Inspect the local branch, commits,
and diff when review or publication may be next.

When the conversation names no workstream, review the open workflow-bearing
issues and their relationships to find the active maps, specs, tickets, and PRs.
Use live records rather than session summaries because concurrent sessions may
have changed them.

This step is complete when every candidate action has current state and blocker
information from its durable source.

## 2. Find the earliest unresolved gate

The workflow is composable, not a fixed checklist. For each active workstream,
choose the first matching transition:

| Current state | Next skill |
| --- | --- |
| The destination is too foggy or large for one planning context | `/wayfinder` |
| Human decisions remain in a repository or domain design | `/grill-with-docs` |
| A factual unknown blocks a decision | `/research` |
| A runnable behavior or visual answer is cheaper than more discussion | `/prototype` |
| The destination is agreed but no durable spec exists | `/to-spec` |
| A spec exists but executable tracer-bullet tickets do not | `/to-tickets` |
| An issue needs readiness verification, labeling, or parent cleanup | `/triage` |
| An in-progress merge, rebase, or cherry-pick has conflicts | `/resolving-merge-conflicts` |
| An unblocked `ready-for-agent` ticket is available | `/implement` |
| Implemented work or review fixes still need a fixed-point review | `/code-review` |
| Reviewed work remains local or the current branch lacks its PR | `/push` |
| The accepted work is closed, reviewed, and published | No next skill: report completion |

If review found defects, route back to `/implement` with those findings. If a
Wayfinder map still has an open frontier, continue `/wayfinder`; route to
`/to-spec` only when its destination is clear and no decision ticket remains.

This step is complete when every candidate is classified as ready, blocked, or
complete.

## 3. Rank the actions

Rank ready actions before blocked actions. Within each group, prefer:

1. The workstream continued by this session.
2. The action that clears the most downstream blockers.
3. The oldest GitHub issue number, then the lexical target name, as stable
   tie-breakers.

Return at most one action. A blocked action must name the condition that makes
it ready.

This step is complete when the ordering follows all three rules and every
blocked action carries a checkable readiness condition.

## 4. Return the recommendation

Use this shape:

````markdown
1. **<concrete action>** - `/<skill>` - <HITL | AFK-safe>
Target: <linked issue, PR, map, spec, branch, or document>
State: <Ready | Blocked by ...>
Why now: <one sentence grounded in live state>

Prompt:
```text
/<skill> "<concise imperative naming the target and desired outcome>"
```
````

Write the prompt as one physical line beginning with the exact skill invocation.
Use straight ASCII quotes and spaces, and keep all labels and explanation outside
the code fence so the command can be copied directly into a new terminal session.

Mark an action `AFK-safe` only when its target is fully specified and requires no
new human judgment. Otherwise mark it `HITL`. For a terminal workstream, return:

```markdown
**Complete:** <why no further workflow skill is needed>.
```

Routing is complete when every active candidate has been classified and every
recommendation names a live target, an exact skill, a one-line terminal command
in its own code fence, and any blocker.