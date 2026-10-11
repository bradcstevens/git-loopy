# One Agent profile per Task type, with its Skills preloaded

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide the Agent profile set and how skills attach to
it](https://github.com/bradcstevens/git-loopy/issues/661).

git-loopy 1.0.0 has one **Agent profile** per **Task type**. The taxonomy is
already closed, label-carried, and the key that **Routing** resolves, so it is
also the profile key. `planning` is the **Planner** (ADR-0071), `review` is the
**Reviewer** (ADR-0072), and `implementation`, `bugfix`, `test`, `docs` and
`chore` are the five execution profiles. No new taxonomy or selector is added.
`PROMPT.md`'s own five-kind classification (critical bugfix, development
infrastructure, tracer bullet, polish, refactor) is retired, because the Task
type now makes that choice in code.

## Selection

The profile is the issue's Task type label. The Planner proposes a `task-type:`
label on every executable ticket in its graph revision, and the Loop engineer
approves the graph and its labels together. The **Task-type classifier** stays
the fallback at **Pickup** for issues that enter outside the Planner. The
**Orchestrator** reads the label and never chooses one. When no Task type
resolves (no label and an inert classifier, or conflicting keys), the issue runs
as the `implementation` profile, as Routing already falls back to the Default
pair. The Reviewer still gates the result.

A **Recovery** session and a remediation Agent after `changes_requested` run as
the issue's own profile. The Task-type classifier and **Route selector** are
single calls inside the Orchestrator, not Agent profiles.

## Skills attach by preload, and exposure is closed per profile

Each profile declares **Required Skills**, which the Orchestrator preloads into
the session's context, and **Permitted Skills**, which the Agent may invoke on
demand. A session exposes Required ∪ (Permitted ∩ **Effective Skill policy**).
Nothing else in the policy is reachable from that profile. The list of Skills
not to invoke, which used to be a rule in `PROMPT.md`, becomes the exposure
itself.

A preload bypasses the permission handler, and an unexposed or disabled preload
does nothing and reports nothing ([#655](https://github.com/bradcstevens/git-loopy/issues/655)).
Preflight therefore refuses a **Run** whose Effective Skill policy is missing a
Required Skill of any profile the Run can start. That extends today's Required
Skill check to every profile.

| Profile | Required | Permitted |
| --- | --- | --- |
| `review` (Reviewer) | `/code-review` | — |
| `implementation` (also the fallback) | `/tdd` | `/prototype`, `/codebase-design` |
| `bugfix` | `/diagnosing-bugs`, `/tdd` | `/codebase-design` |
| `test` | `/tdd` | — |
| `docs` | — | — |
| `chore` | — | — |
| `planning` (Planner) | `/to-tickets` | `/grilling`, `/domain-modeling`, `/codebase-design`, `/research`, `/prototype`, `/to-spec` |

A Run's Required Skills become the union {`code-review`, `tdd`,
`diagnosing-bugs`}. `/prototype` and `/codebase-design` move from Required to
Permitted, so the **Minimal Skill policy** shrinks. The Planner row is advisory:
the Planner runs in the Loop engineer's harness outside a Run (ADR-0071), and no
Run's Skill policy binds it.

## What a profile carries, and what it does not

- **Instructions.** `PROMPT.md` stays the shared Run instructions every in-Run
  profile reads. Each profile adds only its role delta, and the
  `required-skills` metadata moves from `PROMPT.md` to the profiles.
- **No model or effort.** Routing (Static route, `[routing]`, Dynamic routing,
  ADR-0057) is the only model authority. The Orchestrator opens each session on
  the **Routed pair** resolved at Pickup for the same Task type. A per-Skill model
  header in the Skills repository guides a human's `/model` choice and is never
  read by a Run.
- **Tools.** The Reviewer is read-only: it may read, search and run the feedback
  loops, but it has no edit tools and no push. The Orchestrator records its
  verdict and alone publishes and closes (ADR-0072). Execution profiles keep the
  session's full toolset. Per-profile allowlists are not introduced.
- **No cross-profile delegation.** Each session registers only its own profile,
  selected and not available for inference, so no Agent can dispatch another
  git-loopy profile, and an implementer can never start the Reviewer. Built-in
  subagents remain as they are.
- **No memory** across issues (ADR-0076).

## Where profiles live

Profile definitions are authored in `bradcstevens/git-loopy-skills`, carried
under the same pin as the Skill catalog, and published, proved, then pinned
(ADR-0064). The installed-catalog refresh and repair (ADR-0025) cover them. The
Orchestrator supplies them through the SDK as `custom_agents` when a session
starts. They are never written as `.agent.md` files, which a git-loopy session
cannot reach.

## Considered options

- **One generalist Implementer** (the current `PROMPT.md`). Rejected: an
  in-prompt rule that maps a task kind to a Skill can be skipped, and Agents have
  been observed faking `/code-review`. It also leaves two taxonomies side by
  side.
- **A few hand-drawn role profiles.** Rejected: this adds a taxonomy next to the
  Task type and a selector to choose among them.
- **One profile per Skill.** Rejected: more agents is not more leverage
  (`docs/concepts.md`). Most issues need two Skills, and a Skill describes what
  to do, not the role that does it.
- **Profile-carried models.** Rejected: they would be a second model authority
  beside Routing.
- **Exposing the whole Effective Skill policy to every profile.** Rejected: it
  keeps the exclusion list as a prompt rule.
