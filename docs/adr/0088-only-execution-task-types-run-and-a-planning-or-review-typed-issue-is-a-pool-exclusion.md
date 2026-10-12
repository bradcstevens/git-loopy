# Only execution Task types run, and a planning- or review-typed issue is a Pool exclusion

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide what a Run does with an executable issue whose Task type is
planning](https://github.com/bradcstevens/git-loopy/issues/774). Builds on
[ADR-0071](0071-the-planner-stays-outside-the-run.md),
[ADR-0078](0078-one-agent-profile-per-task-type.md),
[ADR-0080](0080-the-tracker-is-the-board-and-its-states-are-derived.md),
[ADR-0086](0086-agent-profiles-are-markdown-beside-the-skills-proved-and-installed-with-the-catalog.md)
and
[ADR-0087](0087-the-planner-is-generated-from-the-pin-and-launched-into-the-loop-engineers-harness.md).
Amends ADR-0078's profile selection and [ADR-0029](0029-agents-infer-the-task-type.md)'s
classifier.

ADR-0078 selects an issue's **Agent profile** from its **Task type** label. Two of the
seven profiles never act as an issue's own Agent. The **Planner** (`planning`) works
outside every **Run** (ADR-0071). The **Reviewer** (`review`) only judges another Agent's
result (ADR-0072). Today nothing stops either label. `is_planning_document` tests only the
title prefix and the `wayfinder:map` label. So a `ready-for-agent` issue labelled
`task-type:planning` is picked up and routed. Under ADR-0078 as written, a `review`-typed
issue would make the read-only Reviewer its author. A Run now starts only the five
execution profiles, and an issue typed for either of the other two is a **Pool exclusion**.

## Only execution Task types run

The **execution Task types** are `implementation`, `bugfix`, `test`, `docs` and `chore`.
A Run starts only their profiles as an issue's own Agent. `planning` and `review` stay
Task types, and the taxonomy stays closed at seven: they name the Planner's work and the
Reviewer's work. Routing keeps both rows. `git-loopy plan` resolves the `planning` row
(ADR-0087), and the Reviewer runs as the `review` Task type (ADR-0072).

The Run stays the one place where execution is autonomous. A planning-typed issue asks for
a decision or a decomposition. ADR-0071 gives that work to a human-driven Planner and its
atomic approval. The evidence agrees. Across 110 Run logs, #692 ("Decide and pin Awaiting
merge") was picked up four times as `planning` and ended `no-progress` every time, about
4.8 active hours in all. #448, a spike, closed in its one planning-typed Pickup: it was
execution work that happened to carry the label. No `review`-typed Pickup was recorded.
The two AFK-shaped `review`-typed issues found, #576 and #577, were test work.

## A planning- or review-typed issue is a Pool exclusion

A `ready-for-agent` candidate that carries the exact label `task-type:planning` or
`task-type:review` is a **Pool exclusion**, whatever other `task-type:` label it also
carries. Its reason is `non_execution_task_type`. The test checks only whether the label is
present, as the `wayfinder:map` test does. The label says whose work this is, and a
conflict between keys needs a human anyway. The reason ranks after `planning_document` and
before the missing-heading reasons, so the operator sees the most actionable cause first.

The two labels assert opposite things. `ready-for-agent` says "execute this", and the Task
type says "this is planning" or "this is review". Only a human can settle that. They can
retype the issue to an execution Task type, or remove `ready-for-agent` and hand the work
to the Planner. So the exclusion is named to the operator, as every Pool exclusion is, and
it shows on the **Board** as Blocked (ADR-0080). The Runner never relabels the issue
(ADR-0080). `git-loopy labels` neither reports nor repairs it: there are two right fixes,
and `--apply` cannot choose between them.

The test joins the shared AFK-ready discriminator, so everything that reads that
discriminator refuses the same issues:

- Pool collection;
- the **Membership read**;
- the Pickup re-read;
- an explicit `--issue` pin;
- Proving-set mining.

The reason is named for the Task type, not for the issue. The Board holds "every executable
issue", meaning every ticket that is not a planning document. An excluded ticket stays on
the Board, in the Blocked column. Calling it "non-executable" would take it off the Board by
definition.

## The classifier proposes only execution Task types

The **Task-type classifier** chooses among the five execution Task types wherever it runs.
Only a human asserts `planning` or `review`. Today its prompt lists all seven keys, and its
example marker is literally `<task-type>planning</task-type>`, because `planning` is the
first key.

`docs/agents/triage-labels.md` justifies the classifier's unattended write because "a
wrong `task-type:` guess picks a suboptimal model". A guess that excluded an issue would
take it off the Board, a larger blast radius than that argument covers. The classifier
also runs after Pool admission. Its label would only exclude the issue at the next Pool
read, after this Pickup had already bound it. Now a wrong guess at worst picks a weaker
execution profile. It never leaves an issue without one.

## The Planner proposes only execution Task types

The Planner proposes an execution Task type on every ticket its graph would make
`ready-for-agent`. ADR-0071's approval applies `ready-for-agent` mechanically. An approved
ticket typed `planning` or `review` would be excluded on its first Pickup, so the approval
would open nothing. Evidence the Planner needs stays its own bounded research or prototype
detour (ADR-0071), never a planning-typed ticket in the graph. This is a paired Skill change
to `/to-tickets` and `profiles/planning.md` (ADR-0064). The Pool exclusion stays as the
backstop for issues that enter outside the Planner.

Planning documents keep `task-type:planning`, and so do wayfinder decision tickets made
`ready-for-human`. Neither is a graph ticket made agent-ready.

## What follows

- Recovery, and the remediation Agent after `changes_requested`, run as the issue's own
  profile (ADR-0078). That is now always an execution profile.
- Preflight's Required Skill union stays {`code-review`, `tdd`, `diagnosing-bugs`}
  (ADR-0086). No Run can start the Planner, so `/to-tickets` stays out of it.
- The local-markdown source carries no labels. Only the narrowed classifier could type an
  item there, and it never picks either key.
- An excluded ticket stays open, so its Spec stays open too (ADR-0082). It is never worked,
  so it is never one of the worked rows the Completion rate counts (ADR-0085).
- This is Python-path work. The dormant shell and PowerShell paths keep their Path
  baselines (ADR-0073).
- An issue that ran this way before, like #448 or #692, waits until a human retypes it.

## Amendments

- ADR-0078 says "the profile is the issue's Task type label". That now holds only for the
  five execution Task types. An issue typed `planning` or `review` is a Pool exclusion. No
  Run starts the Planner, and none makes the Reviewer an issue's author.
- ADR-0029 lets the classifier propose any of the seven keys. It now proposes only the five
  execution Task types. The taxonomy stays closed at seven, and labels still carry all seven.

## Left to the PRD

The PRD settles:

- the wording of the exclusion table in Wrapper contract §3.1, and its minor version;
- the new `discriminator.json` cases and the fixture revision;
- the classifier prompt and its tests;
- how Proving-set mining maps the new reason;
- the paired change to `/to-tickets` and `profiles/planning.md`;
- the triage documents: `docs/agents/triage-labels.md` and the triage rule in `AGENTS.md`.

## Considered options

- **Run it as the `implementation` fallback.** Rejected: it silently overrides a human
  assertion, and the issue would still get the `planning` Routed pair. #692 shows decision
  work failing inside a Run across four Pickups.
- **Let a Run start the `planning` profile.** Rejected: ADR-0071 refused to host
  decomposition inside the Run. It would also skip the human approval of the graph, and
  pull `/to-tickets` into every Run's Preflight.
- **Exclude only when `planning` or `review` is the sole `task-type:` key, and run mixed
  keys as `implementation`.** Rejected: the human asserted the work belongs to the Planner
  or the Reviewer, and a conflict needs a human anyway.
- **Keep all seven keys in the classifier, and refuse a `planning` or `review` proposal so
  the issue runs as `implementation`.** Rejected: it spends a call on an answer that can
  never be used, and the prompt would still offer it.
- **Keep all seven keys, and let the written label exclude the issue from the next Pool.**
  Rejected: a model's guess would take work off the Board. The unattended write was never
  justified for a blast radius that large.
- **Report these issues in `git-loopy labels`.** Rejected: there are two right fixes, so
  there is nothing to repair. The Run, the Board and the pin refusal already name them.
- **Decide `planning` alone, and file `review` as a separate question.** Rejected: it is the
  same gap in ADR-0078's selection rule, and it has the same answer.
