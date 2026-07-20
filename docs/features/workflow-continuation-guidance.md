# Workflow Continuation and Next-Step Guidance

> Source idea for `/wayfinder` or `/grill-with-docs`.
>
> Recommended starting point: `/wayfinder`, because this spans multiple skills,
> concurrent sessions, issue-tracker state, and both human-led and autonomous
> execution.

## Problem

When a human runs several Copilot CLI sessions manually, each session may
successfully complete its immediate work while leaving the overall workflow
unclear. One session may resolve a Wayfinder ticket, another may run
`/grill-with-docs`, and later sessions may create specs, tickets, or
implementation work. Once those sessions close, there is no single lightweight
view that answers:

1. What should happen next?
2. Which skill should be invoked?
3. What exact prompt should be given to that skill?
4. Which issue, map, spec, or ticket should it operate on?
5. Is the action currently unblocked, waiting on a human, or safe for an
   autonomous agent?

The human must reconstruct the workflow from issue labels, sub-issues,
dependencies, comments, local artifacts, and memory. Guidance from an earlier
session can also become stale after other sessions run in parallel or complete
later work.

## Desired Outcome

Git-loopy's workflow skills should make continuation explicit. Completing a
session should leave behind, or contribute to, a concise ordered list of
recommended next actions similar to:

1. Invoke `/triage` with a specific prompt to close completed parent issues.
2. Invoke `/wayfinder` against a named map and frontier ticket.
3. Invoke `/to-spec` after the map reaches its destination.
4. Invoke `/to-tickets` against the resulting PRD.
5. Invoke `/implement` against the first unblocked implementation ticket.

Each recommendation should identify the skill, provide a copy-pasteable prompt,
link to the relevant durable artifact, and explain any blocker or human decision
that prevents it from running immediately.

The same continuation information should help:

- A human deciding what terminal session to start next.
- A fresh agent session resuming work without reconstructing the whole project.
- A git-loopy Iteration selecting the next autonomous step when that step is
  fully specified and safe to run without human judgment.
- The Orchestrator stopping clearly at a HITL boundary instead of ending with no
  explanation of what the human needs to invoke next.

## Capabilities to Explore

### Explicit next-step recommendations from skills

Explore a shared completion convention for workflow skills such as `/triage`,
`/wayfinder`, `/grill-with-docs`, `/prototype`, `/to-spec`, `/to-tickets`,
`/implement`, and `/code-review`.

When a skill concludes, it should recommend one concrete next step, including:

- The next skill to invoke.
- A copy-pasteable prompt tailored to the completed work.
- The issue, map, PRD, ticket, branch, or document that provides context.
- Whether the next action is HITL or AFK-safe.
- Any dependencies that must close before the action becomes available.
- A clear terminal state when the workflow is genuinely complete.

The recommendation should be specific rather than generic. For example, prefer
"Run `/wayfinder` on map #89 and resolve frontier ticket #92" over "Continue
planning."

### A holistic cross-session continuation view

Explore a lightweight project-local representation of the recommended sequence
across all active workflows. The user's initial preference is a small Markdown
document, but the design should determine whether that is the best source of
truth or only a rendered view.

The continuation view should:

- Combine results from multiple sessions rather than describe only one session.
- Order actionable work using issue dependencies and workflow semantics.
- Distinguish ready actions, blocked actions, completed actions, and HITL stops.
- Remain useful when sessions run concurrently or finish out of order.
- Detect or discard stale recommendations when tracker state changes.
- Link to existing durable artifacts instead of copying their contents.
- Stay concise enough to scan in a terminal.
- Be refreshable or regenerable instead of becoming an append-only activity log.

An illustrative output shape:

```markdown
1. **Close completed parents** - `/triage`
   Prompt: "Verify #<parent> has no open children, then close it as completed."

2. **Resolve the next design decision** - `/wayfinder`
   Target: map #<map>, ticket #<ticket>
   Prompt: "Claim and resolve #<ticket>; update #<map>; do not implement."

3. **Publish the locked design** - `/to-spec`
   Blocked by: all Wayfinder decision tickets closing
   Prompt: "Synthesize map #<map> into a PRD and publish it ready-for-agent."

4. **Implement the first unblocked slice** - `/implement`
   Target: #<ticket>
   Prompt: "Implement #<ticket> from PRD #<prd>; use /tdd and /code-review."
```

### Human and autonomous continuation

Explore how the same ordered recommendations can serve both manual and
autonomous workflows without erasing the HITL boundary.

- Human-led actions should be clearly marked and should produce an exact command
  or prompt the human can run.
- AFK-safe actions may be eligible for a future git-loopy Iteration to select and
  execute.
- The runner must not answer grilling questions, make maintainer decisions, or
  silently cross another HITL boundary on the human's behalf.
- When autonomous progress stops, the run should report the next required human
  action rather than merely reporting that no executable ticket is available.

### Relationship to `/handoff`

Keep this concern separate from `/handoff` by default.

`/handoff` currently compacts one conversation for one fresh session and stores
the result in the operating system's temporary directory. The requested
continuation view is broader: it summarizes the next actions across multiple
durable workflows and sessions.

Explore reuse only if it does not blur these responsibilities:

- **Handoff:** session-specific context needed to continue one thread.
- **Continuation guidance:** project-level ordering of what skill or issue should
  be worked next across all active threads.

The continuation view should reference a handoff document when one is relevant,
not duplicate its contents.

## Constraints

- Keep the human-facing result lightweight: an ordered list of actions, not a
  second issue tracker or a growing project journal.
- Avoid one permanent Markdown file per completed session.
- Do not duplicate specifications, ADRs, issue bodies, resolution comments,
  commits, or handoff documents.
- Prefer tracker relationships and existing durable artifacts as authoritative
  state.
- Make concurrent-session updates safe and make stale entries detectable.
- Preserve the existing distinction between planning skills, implementation
  skills, and HITL decisions.
- Do not require a human to remember the full skill workflow before asking what
  to do next.
- Consider the whole Runner family if the Orchestrator eventually consumes this
  guidance, rather than introducing Python-only workflow semantics accidentally.

## Open Questions for Wayfinding

- Should continuation guidance be a committed Markdown artifact, an ignored
  local file, a generated terminal view, a GitHub issue, or some combination?
- Is a dedicated manually invoked skill needed, or should every existing skill
  implement a shared "next action" output contract?
- What is the minimum schema for an action: skill, prompt, target, state,
  blockers, source, and generated time?
- Which source is authoritative when local recommendations disagree with GitHub
  issue state?
- How should concurrent sessions update the continuation view without clobbering
  or duplicating one another?
- Should sessions write recommendations incrementally, or should one command
  rebuild the sequence from the issue tracker and durable artifacts on demand?
- How are completed and stale actions removed so the file remains small?
- How should a Wayfinder map signal that it is ready for `/to-spec`, and how
  should a completed PRD signal that its parent can be closed?
- How should the Orchestrator distinguish AFK-safe actions from actions requiring
  `/grilling`, `/domain-modeling`, maintainer confirmation, external access, or
  manual testing?
- When should git-loopy automatically continue to the next AFK-safe action, and
  when should it stop after merely recommending it?
- Can `/handoff` expose a small reusable pointer or metadata shape without
  becoming the project-level continuation store?
- What tests or Conformance-suite fixtures would keep next-step behavior
  consistent across workflow skills and future Orchestrators?

## Supporting Context

- [The Matt Pocock Skills Workflow: A Complete Guide](../../.reference/mattpocock-skills-workflow-guide.md)
  describes the main skill sequence and the deliberate context-clearing points
  between tickets.
- [`/handoff`](../../.copilot/skills/handoff/SKILL.md) currently creates a
  temporary, session-specific continuation document and already includes a
  "suggested skills" section.
- Existing GitHub issue labels, native sub-issues, and `blocked_by` dependencies
  already encode much of the state needed to calculate a current frontier.

<details>
<summary>Original request</summary>

> Generate a feature .md file to explore adding steps like this as new HITL
> manually ran skills and git-loopy features. Currently, I'm finding that when I
> run multiple sessions manually in my terminal, where I'm evoking various skills
> like `/wayfinder`, `/grill-with-docs`, etc. that once I close that session,
> having ran multiple additional sessions in parallel or afterwards, I don't
> always know what to do next, which is why I asked you to guide me in this
> session.
>
> I'd like to find a way to enhance the skills used here in this project to always
> recommend a specific next step, such as the steps in the way that you responded
> back in your previous response to me. Ideally, whatever enhancements we would
> add for something like this would assist the human, but also a potential agent
> iteration to be able to use to continue workflows without running into sudden
> stops that require a human to come in and manually start again.
>
> I'm not sure the best way to organize it without potentially leading to
> Markdown file bloat, but my initial thoughts are to create some kind of local
> Markdown file that will keep track of whatever the recommended skills and
> issues to work on next would be, and to collect those holistically as different
> sessions conclude.
>
> We have the handoff skill, which stores a document to a temporary directory on
> the user's machine, which I think should stay separate unless you think it's
> perfectly viable to use for a purpose like what I'm describing. What I'm
> looking for really is just something super lightweight, just like you responded
> back with: a sequential order of different things to run, which is the result of
> having completed multiple sessions.

</details>
