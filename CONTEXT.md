# git-loopy and Loop Engineering

**git-loopy** is the GitHub Copilot SDK framework and brand for orchestrating
automated Ralph loops over agentic-engineering work. **Loop engineering** is the
practice of shaping that work into explicit, reviewable units, setting guardrails
and feedback loops, and supervising autonomous execution. This glossary fixes the
vocabulary shared by the planning skills, issue tracker, Runner family, and live
interface.

## Two-phase model

1. **Planning phase (human-led)**: The loop engineer starts with
   `/grill-with-docs` for repo and domain work or `/grill-me` for general planning,
   optionally uses `/prototype` when a decision needs runnable evidence and
   `/research` when it needs primary-source evidence, then runs `/to-spec`,
   `/to-tickets`, and `/triage`. The result is a set of small, explicit issues
   labeled `ready-for-agent`.
2. **Execution phase (autonomous)**: The git-loopy loop collects those triaged
   issues and, by default, works exactly one Active issue per Iteration. The loop
   engineer supervises the Run through its guardrails and Dashboard, then judges
   the completed work.

## Language

### The practice

**Loop engineering**:
The practice of designing, operating, and improving autonomous agent loops. It
connects human-led planning and context engineering to small triaged issues,
explicit acceptance criteria, feedback loops, guardrails, and human review so
autonomous execution stays aligned.

**Loop engineer**:
The human who designs, triages, and supervises the loop. The loop engineer owns
intent, domain language, issue slicing, acceptance criteria, guardrails, and final
judgment; git-loopy owns repeatable execution.

### Workflow continuation

**Workflow**:
The reusable network of valid transitions and human/autonomous boundaries through
which project work can advance. It is composable, not a mandatory linear checklist.
_Avoid_: pipeline, fixed sequence, live effort.

**Workflow transition rule**:
The versioned semantics that recognize durable workflow facts and derive a
**Continuation action**, **Workstream outcome**, or distinct **Successor Workstream**.
The rule is a **Producer**; a Consumer that evaluates it does not become its owner.
_Avoid_: router, display rule, Skill sequence.

**Workstream**:
One project-local traversal of a **Workflow** toward a single **Destination**,
identified across transitions by one durable **Anchor**.
_Avoid_: workflow (for a live effort), session, thread.

**Successor Workstream**:
A distinct **Workstream** established from a predecessor Workstream's terminal outcome
and one **Workflow transition rule**. It has its own Destination and never extends or
resurrects the terminal predecessor.
_Avoid_: next phase of the same Workstream, child Workstream.

**Anchor**:
The one durable artifact that identifies a **Workstream** while its active
**Targets** change.
_Avoid_: current target, source of truth.

**Successor slot**:
The durably addressable, versioned position on a predecessor **Workstream outcome** for
one successor transition. The outcome plus slot is the immutable **Anchor** for that
Successor Workstream before its first Target exists.
_Avoid_: mutable anchor, placeholder artifact.

**Destination**:
The affirmative condition a **Workstream** is meant to satisfy. It defines successful
completion and the Workstream's in-scope boundary.
_Avoid_: next step, target.

**Continuation guidance**:
The current set of unmet **Continuation actions** and explicit **Workstream outcomes**
across the project, together with their prerequisite relationships.
_Avoid_: activity log, project journal, handoff.

**Continuation contract**:
The separately versioned, language-neutral interface governing shared completion
requests, durable **Producer revisions**, **Dispatch evidence**, **Reconciliation**,
capability declarations, and canonical human and automation results.
_Avoid_: guidance schema, Wrapper contract, Event schema.

**Continuation capability manifest**:
A Runner-family member's machine-readable declaration of the Continuation-contract
versions, tracker adapters, Instruction handlers, evaluators, and optional capabilities
it supports. It describes capability but grants no execution authority.
_Avoid_: Automation scope, Performer posture, feature flags.

**Continuation view**:
A **Consumer**-specific ordered projection of **Continuation guidance**. Its ordering
helps select work but does not itself establish prerequisites.
_Avoid_: canonical sequence, queue, timeline.

**Continuation action**:
One prospective, concrete unit of intent that advances exactly one **Workstream**
against one primary **Target**. It is not an execution event, result, historical
record, or **Handoff**.
_Avoid_: event, history entry, handoff.

**Action kind**:
The versioned semantic operation a **Continuation action** intends to perform within
a **Workflow**. It is broader than the wording of one **Instruction** and may be
performed through different compatible representations.
The current vocabulary is **Chart workstream**, **Resolve decision**, **Research fact**,
**Prototype evidence**, **Publish spec**, **Decompose spec**, **Triage item**,
**Provide information**, **Perform manual validation**, **Authorize operation**,
**Implement ticket**, **Address review findings**, **Review head**, **Resolve conflict**,
**Publish head**, **Review and merge PR**, and **Close parent**.
_Avoid_: prompt, display label, Producer name.

**Action occurrence**:
One durably distinguishable lifecycle instance of an **Action kind** against a
**Target**. A recurrence after retirement is a new Action occurrence rather than a
resurrection of the retired one.
_Avoid_: attempt, session, timestamp.

**Action identity**:
The stable logical identity of one **Action occurrence**, determined by its
**Workstream**'s **Anchor**, **Action kind**, **Target**, and durable occurrence
discriminator. **Producer**, carrier, wording, timestamps, **Readiness**, and display
order do not define it.
_Avoid_: record id, content hash, execution id.

**Action semantics**:
The behaviorally significant content carried under one **Action identity**: its
**Instruction**, **Prerequisites**, interaction classification and classification
evidence, and completion condition. Different **Basis** or **Producer** provenance
may support equivalent Action semantics.
_Avoid_: presentation, provenance, observation.

**Instruction**:
The concrete direction a **Performer** follows to carry out a **Continuation action**;
a copy-pasteable skill prompt is one form of Instruction.
_Avoid_: prompt (as the universal term), description.

**Target**:
The primary durable subject a **Continuation action** operates on.
_Avoid_: anchor, basis, context.

**Artifact role**:
The semantic part a durable artifact plays in a **Workflow**, independent of tracker
labels or physical representation. A specification parent and an executable leaf may
share a label while supporting different transitions.
_Avoid_: issue type, label, file format.

**Basis**:
The durable evidence and **Producer** provenance establishing why a
**Continuation action** belongs in current **Continuation guidance**. It may
reference the **Target**, but is distinct from it.
_Avoid_: target, copied context, source of truth.

**Producer**:
The role that contributes or refreshes a **Continuation action** or
**Workstream outcome** from a **Workflow** transition.

**Producer carrier**:
The durable artifact that records a Transition owner's transition evidence and hosts
that Producer's revision lineage for one **Workstream**. It references the Anchor,
Target, and Basis but is not a central guidance ledger.
_Avoid_: Anchor, Target, continuation issue.

**Transition owner**:
The one **Producer** responsible for the semantic delta from a durable **Workflow**
transition. Ownership follows the transition rather than the Skill, command, human, or
platform adapter used to perform it.
_Avoid_: top-level Skill, last writer.

**Pointer-only participant**:
A surface that returns evidence or a durable reference to a **Transition owner** without
publishing shared guidance for that transition. It becomes a Producer only when it owns
a separate, durably anchored transition.
_Avoid_: Producer, Consumer.

**Consumer**:
The role that inspects a **Continuation view** to understand or choose available
work.

**Performer**:
The role that carries out a **Continuation action**. A Consumer is a Performer only
when that action is eligible for it.

**Prerequisite**:
A durably based condition that must hold before a **Continuation action** may
proceed.

**Blocker**:
A currently unsatisfied **Prerequisite**.

**Readiness**:
Whether a **Continuation action** has any **Blockers**: **Ready** when it has none,
**Blocked** otherwise.

**Producer revision**:
One durable version of a **Producer** contribution at its carrier, based on one
observed predecessor revision. Competing non-equivalent successors are a
**Continuation conflict**, never a timestamp contest.

**Publication receipt**:
The typed result of attempting to publish one Producer revision, distinguishing a
clean or idempotent commit from conflict, rejection, or an indeterminate write.
_Avoid_: Producer revision, completion result.

**Reconciliation**:
The on-demand derivation of current **Continuation guidance** from durable Producer
revisions and current workflow facts. It evaluates support, completion, Prerequisites,
identity equivalence, and conflicts rather than maintaining a central mutable sequence.

**Observation**:
A non-authoritative account of one **Reconciliation** read, including the durable-source
validators inspected and any derived **Readiness** or uncertainty.

**Unverified**:
An **Observation** classification used when required durable facts cannot be fetched or
stabilized. It makes no Ready or Blocked claim.

**Continuation conflict**:
An incompatibility that prevents safe **Reconciliation**, such as different live
**Action semantics** under one **Action identity**, cyclic Prerequisites, or competing
Producer revisions.

**Action retirement**:
The evidence-backed removal of a **Continuation action** from current guidance because
it completed, lost supporting Basis, was superseded, or its Workstream reached a
terminal outcome. A Ready-to-Blocked or Blocked-to-Ready transition is not retirement.

**Retirement receipt**:
The bounded durable explanation in a successor **Producer revision** for each prior
**Continuation action** it removes, including the retirement reason, evidence, and
replacement **Action identity** when superseded. It is not a live Action or a central
tombstone.

**HITL-required**:
An intrinsic action classification, backed by a typed human-boundary reason, meaning
completion requires a human to supply intent or information, exercise judgment,
authority, or consent, authorize access, or perform subjective or physical validation.
Missing AFK evidence is not itself a human boundary.

**AFK-safe**:
An intrinsic action classification positively attested by its **Transition owner**
through an **AFK safety case**, meaning every permitted completion path is unattended,
all human-owned decisions and inputs are durably fixed, and completion is objectively
evaluable. A Consumer or Performer may narrow but cannot infer or upgrade it.

**AFK safety case**:
The versioned, evidence-backed **Action semantics** that justify **AFK-safe** by
declaring bounded effects, typed safety assumptions, **Eligibility requirements**, an
objective completion condition, and exceptional **HITL triggers**. It is neither
current Performer eligibility nor Run authorization.

**HITL trigger**:
A typed exceptional condition in an **AFK safety case** that revokes unattended
eligibility when detected. A foreseeable normal human branch invalidates
**AFK-safe** rather than serving as a trigger.

**Safety-case violation**:
Evidence that an **AFK safety case** omitted or contradicted an inherent human
boundary or safety requirement. It quarantines the smallest justified scope until the
**Transition owner** revises or replaces the Action; a Performer cannot reclassify it.

**Dispatch evidence**:
A durable, non-Producer record that one **Continuation dispatch** exposed a
**Safety-case violation** or left authorized effects in an uncertain, non-retry-safe
state. It may quarantine an Action but cannot create or retire Actions or outcomes.
_Avoid_: Producer revision, execution log, Event.

**AFK-eligible**:
A point-in-time, positively verified relationship between a **Ready**, **AFK-safe**
action and a specific **Performer** whose current **Performer posture** satisfies every
**Eligibility requirement**. It means the Performer could safely act, not that a Run
is authorized to select the Action.

**Eligibility requirement**:
A versioned, machine-evaluable capability, access, policy, or completion-evaluation
requirement an **AFK-safe** action declares for matching to a **Performer**.
_Avoid_: prompt hint, inferred requirement.

**Performer posture**:
The current, non-secret capability, noninteractive access, and policy facts used to
decide whether one **Performer** satisfies an action's **Eligibility requirements**.
_Avoid_: agent confidence, generic capability.

**Automation scope**:
The preauthorized, Run-frozen boundary containing coverage—the Workstreams and Targets
the Run observes—and execution grants—the Action and effect scopes it may perform.
It may narrow on revocation but cannot expand during the Run.

**Automation frontier**:
The fixed set of in-coverage **Action identities** and semantic fingerprints captured
by a Run's initial verified **Reconciliation**. Known Actions may change Readiness or
eligibility; new or semantically changed Actions are report-only for that Run.

**Automation-selectable**:
The contextual state of an **AFK-eligible** Action whose identity and semantic
fingerprint belong to the Run's **Automation frontier** and whose effects are covered
by its **Automation scope** execution grants. It does not establish concurrency.

**Continuation dispatch**:
One bounded Orchestrator authorization to perform exactly one
**Automation-selectable** **Action occurrence** under one semantic fingerprint and
**AFK safety case**. A Run may issue multiple dispatches only from its fixed
**Automation frontier**; a dispatch never authorizes a successor Action.
_Avoid_: autonomous chain, workflow run.

**Automation stop**:
An explicit, typed Run result explaining why no further unattended progress can be
selected from its **Automation frontier**. It is not a **Workstream outcome**, Action
retirement, **Strike**, or generic execution failure.
_Avoid_: completion, no work.

**Workstream outcome**:
An affirmative, durably evidenced terminal disposition of a **Workstream**.
**Complete** means its **Destination** was satisfied. **Rejected** means the authorized
decision was not to pursue it. **Abandoned** means it ended intentionally without a
replacement. **Superseded** means another named Successor Workstream replaces it. Only
Complete has a satisfied Destination.

**Parent cleanup**:
The independent transition that verifies a parent artifact's own lifecycle condition
and records its closure or other disposition. Terminal child outcomes may make Parent
cleanup actionable, but do not prove the parent's Destination was satisfied. Parent
cleanup neither creates nor blocks a substantive Successor Workstream.
_Avoid_: automatic cascade close, final child side effect.

**Handoff**:
Session-specific context for continuing one active thread. It may support a
**Continuation action**, but is neither that action nor project-level
**Continuation guidance**.

**Handoff reference**:
A non-authoritative contextual pointer attached only when one current
**Action occurrence** resumes the exact active thread or **Target** described by a
**Handoff**. It is not **Action semantics**, **Basis**, a **Prerequisite**, completion
evidence, or a **Producer** contribution. Its availability is an **Observation** and
cannot change **Readiness** or recreate an Action removed by **Reconciliation**.
_Avoid_: handoff action, shared handoff record, copied handoff.

### The run loop

**Run**:
One invocation of the git-loopy loop, identified by a `run_id`, spanning serial
**Iterations** and/or parallel **Lane contributions** until its authorized work is
exhausted, an **Automation stop** occurs, or the strike limit is reached.

**Skill**:
A named capability package whose instructions and resources a **Performer** may load
when a task matches its purpose. Its canonical name is its policy identity; when
multiple sources provide that name, git-loopy's explicit project source wins and the
external agent client's source precedence resolves the remaining candidates before
git-loopy's packaged fallback.
_Avoid_: custom instruction, tool.

**Skill baseline**:
The exact initial enabled/disabled selection across the **Skill catalog**, copied from
an operator's external agent client when establishing the first configured
**Skill policy**. It seeds that policy but does not remain its authority. A later
project Skill policy starts from the inherited global policy unless the operator
explicitly requests another external-client sync.
_Avoid_: live mirror, source of truth.

**Skill catalog**:
The inventory of Skills an operator may inspect and select for git-loopy, including
project, personal, plugin, built-in, and custom sources reported by the external agent
client, plus git-loopy's explicit project and packaged Skill sources. It is refreshed
at Run preflight to resolve each Skill name to its current source, but the external
client's enabled state has no authority after the **Skill baseline** is established.
Catalog discovery reads metadata only. Catalog membership does not load a Skill's
instructions or resources or make it available to a **Run**; the **Skill policy** does.
_Avoid_: enabled skills, runtime tools.

**Skill policy**:
The git-loopy-owned, closed-world set of Skills a **Run** may expose to its
Performers. A Skill that is absent from the set remains disabled even if it later
appears in a discovered location. Once established, the set changes only through an
explicit git-loopy action, not merely because another agent client's settings changed.
A project Skill policy replaces the global Skill policy; the global policy applies
only when the project has not established one. A project Skill policy is a shared
repository contract: every operator must be able to resolve each enabled Skill name.
An explicitly empty Skill policy is still a policy; absence means inheritance or the
unconfigured fallback. Any enabled name that cannot be resolved makes the **Run**
invalid without changing the saved policy. A project-sourced Skill enabled by a
project Skill policy must be versioned with the repository.
_Avoid_: Copilot settings, deny list, permission list.

**Effective Skill policy**:
The **Skill policy** selected for a **Run**, after applying that invocation's temporary
enable and disable overrides and any legacy deny guards. Conflicting overrides resolve
to disabled, and the result must still contain every **Required Skill**. It is frozen
at Run preflight for every work session and **Lane** in that Run.
_Avoid_: persisted policy, Copilot state.

**Minimal Skill policy**:
The unconfigured, non-interactive fallback that exposes only git-loopy's packaged
**Required Skills**. It keeps a first CI Run usable without consulting personal or
machine-global Skill sources and is also the policy persisted by unattended setup
unless that setup explicitly requests an external-client import. It also governs
unattended Runs during migration from the former open-world skill behavior.
_Avoid_: default user policy, imported baseline.

**Required Skill**:
A Skill declared by the active Run instructions' machine-readable metadata as one a
Performer must be able to invoke. A **Run** whose **Skill policy** omits a Required
Skill is invalid and stops before its first work session rather than silently restoring
or ignoring the Skill. Legacy custom instructions without that metadata inherit the
packaged instructions' Required Skills until they declare their own set.
_Avoid_: default skill, recommended skill.

**Iteration**:
One serial cycle of the loop — collect the pool, let the agent work exactly one task,
then do commit accounting and a progress check. The serial unit by which elapsed time
and streamed output are measured and attributed. Each fresh serial work session is a
new Iteration, including a context-cutover continuation pinned to the same
**Active issue**.
_Avoid_: round, pass, tick; session as a separate accounting unit.

**Pool**:
The set of `ready-for-agent` issues collected at the start of an iteration and
offered to the agent together in a single prompt; the agent picks one.
_Avoid_: batch, backlog.

**Strike**:
A recorded no-progress result: a serial **Iteration** made no meaningful progress, or
a parallel **Lane contribution** terminated unpublished. A fixed number of consecutive
strikes ends the run. A runner **Checkpoint** does not count as progress.
_Avoid_: failure, miss.

**Checkpoint**:
A runner-authored commit that captures any uncommitted or untracked changes the agent
left at a serial **Iteration** or Lane-work boundary, so subsequent work starts from a
clean durable branch. It is close-keyword-free (never auto-closes an issue) and does
not count as Strike progress. Distinct from the agent's own commits.
_Avoid_: autosave, stash, snapshot.

**Sandbox**:
The per-Iteration OS permission boundary the agent's shell commands run inside,
confining which filesystem paths and network they may touch and resetting to a clean
policy at each **Iteration**. Contains an issue's blast radius so one run cannot leak
filesystem state into another; a permission boundary, not a container or a fresh
checkout.
_Avoid_: container, jail, VM.

### Issues and attribution

**Active issue**:
The single issue the agent is working during the current iteration, self-selected
from the pool. In **Parallel mode** each **Lane** has its own Active issue, assigned by
the runner rather than self-selected.
_Avoid_: current task, current ticket.

**Working marker**:
The agent's explicit, up-front declaration of its active issue, used to attribute the
iteration's timing and streamed output to that issue in real time. The first valid
marker immutably binds a serial **Iteration**; later conflicting markers do not
reassign its work.

**Queue**:
The per-run ledger of every issue seen in any pool during the run, each carrying a
status; the selectable list shown in the live interface. Distinct from the pool,
which is a single iteration's input.
_Avoid_: backlog, list.

**Status**:
An issue's lifecycle within a run: **queued** (seen, not yet worked), **active**
(being worked now — several at once in **Parallel mode**, one per **Lane**), **closed**
(finished and closed via a commit close-keyword), **advanced** (progressed but not
closed), **no-progress** (worked without meaningful change), **gone** (left the pool
without resolution).

**Closed**:
The successful terminal **Status** in which the source issue has actually been
closed. It alone has a closure timestamp; **advanced**, **no-progress**, and
**gone** are not completions.
_Avoid_: completed, ended (when the source issue remains open).

**Issue elapsed**:
The span from an issue's first activation to its **Closed** instant, including
inactive gaps between **Iterations**. Distinct from the Queue's Active duration,
which sums only time actually active.
_Avoid_: active time, waiting time.

### Leaving a run

**Stop**:
Ending a run deliberately — the current iteration is wound down cleanly and the loop
exits.
_Avoid_: quit, kill, abort.

**Detach**:
Leaving the live interface while the run keeps going unattended, falling back to the
line-by-line scrollback output.
_Avoid_: background, minimize, exit.

### The live interface

**Dashboard**:
The single top-level screen of the live interface (no tabs): the header band, the
live **Queue**, and the run **Summary**, stacked together. Selecting a **Queue** row
and pressing enter opens that issue's **Log**.
_Avoid_: home, main view, summary view (the **Summary** is one band of it, not a
separate screen).

**Log**:
The time-ordered, timestamped record of one **Active issue**'s output — reasoning,
assistant messages, tool calls, commits, and closures — shown in the per-issue Log
view that enter opens from the **Queue**. It auto-scrolls to the latest entry.
_Avoid_: transcript (the prior code term), output, stream.

**Summary**:
The per-run accounting band of the **Dashboard**, with one row per serial
**Iteration** or parallel **Lane contribution** (**Consumption**, **Observed tokens**,
tools, skill calls, skills consulted, commits, closures, and strikes), mirrored in
the run-end table. A band of the **Dashboard**, not a separate screen.

**Activity**:
The **Dashboard** band that renders the live current tail — the **Active issue**'s
**Log**, or the pre-marker pending output — always visible below the **Queue** (between
it and the **Summary**). An active-only, auto-scrolling glance at what the agent is
doing right now, so a run reads as active instead of appearing stuck while issues sit
**queued**; it complements, and does not replace, the per-issue **Log** that enter opens
for the full, scrollable history. A band of the **Dashboard**, not a separate screen.
_Avoid_: stream, feed.

**Context fill**:
The current **Iteration**'s live context-window occupancy — current tokens divided by
the model's token limit — shown in the **Dashboard** header with Smart-Zone target and
ceiling cues. It resets at every Iteration boundary and is distinct from both
**Observed tokens** and **Consumption**, which accumulates billed tokens and cost.
_Avoid_: context usage, cumulative tokens, token consumption.

**Consumption**:
The tokens-in / tokens-out and the model they were billed against, attributed to a
scope: a serial **Iteration** or parallel **Lane contribution** (the basis for a
**Summary** row's Cost), or an **Active issue** — summed across every accounting unit
that worked it — the basis for the **Queue**'s per-issue Cost. Every Cost figure
derives from Consumption by one shared rule (first non-None model wins; tokens sum),
represented in code by the `UsageTally` value object (`git_loopy.usage`).
_Avoid_: usage, spend (for the token measure); billing.

**Observed tokens**:
The cumulative tokens-in plus tokens-out reported during an **Iteration**. An
accounting total, not model-window occupancy; it is never expressed as a percentage
of the context window.
_Avoid_: context used, context utilisation, context fill.

**Iteration breakdown**:
The per-issue drill-in band that itemizes each **Iteration**'s contribution to that
issue, including its **Consumption**, Cost, and available peak **Context fill**. In
**Parallel mode**, the row is the issue's **Lane** contribution.
_Avoid_: session breakdown, history table.

**ModelSelectionMode**:
The opt-in startup state — entered with the `--select-model` flag or
`GIT_LOOPY_MODEL_SELECT=1` — that shows the live model + reasoning-effort picker before the
run starts. Off by default: an ordinary launch uses the configured model and reasoning
effort with no prompt.
_Avoid_: picker mode, interactive model prompt.

### Framework and configuration

**git-loopy**:
The GitHub Copilot SDK loop-engineering framework and brand for orchestrating
automated Ralph loops for agentic engineering. It ships a **Runner family**: the
Python reference runner (the globally-installed `git-loopy` console command;
`git loopy` also works as a git subcommand) plus the planned **shell**,
**PowerShell**, and **Rust** ports, all implementing one **Wrapper contract**.
Written `git-loopy` as the distribution, console command, and on-disk/brand
spelling; `git_loopy` as the importable Python package. Supersedes the retired
**copiloop** and **ralph-afk** brands.
_Avoid_: copiloop, ralph-afk, "the runner" as a proper name.

**Release version**:
The Semantic Versioning identity assigned to one published git-loopy distribution.
Every included **Orchestrator**, packaged **Skill** set, and **TUI helper** shares it;
contract and schema versions remain separate compatibility identities.
_Avoid_: component version, protocol version, schema version.

**Release target**:
The planned **Release version** to which an issue contributes. It states delivery
intent without changing the issue's workflow readiness or dependency relationships.
_Avoid_: version label, release label.

**Ralph loop**:
The *technique* git-loopy orchestrates — an unattended, iterative execution loop
that drives the Copilot agent to work triaged issues one at a time. A concept,
never a code identifier; "ralph" survives only in this sense.
_Avoid_: ralph-afk (the retired brand); "ralph" as a symbol, directory, or env-var.

**Config**:
The persisted settings (model, reasoning effort, strike policy, denylists, ...) that carry across
runs so they need not be re-passed each time. Held on disk as a hand-editable `config.toml` in a
**project** and/or **global** **scope**, and merged key by key along the precedence chain
**CLI flag > env var > project > global > built-in default** (the denylists are the set *union*
across every tier). Replaces the per-run environment the retired bash launcher used to hard-code.
_Avoid_: settings file, profile.

**init**:
First-run setup that writes **Config** — and optionally an editable prompt and skills — into a
chosen **scope**. Runs automatically the first time on an interactive terminal; also invocable as
`git-loopy init`.
_Avoid_: setup, bootstrap; install (install is the separate act of putting the `git-loopy` command
on PATH).

**config (subcommands)**:
The operator surface over **Config** — `git-loopy config set / get / list / path / edit` — a
convenience over hand-editing `config.toml` (which stays fully supported). `set` persists one key
to a **scope**; `get` / `list` report the *effective merged* value(s) a run would use across the
whole precedence chain (not one file); `path` prints the resolved location(s); `edit` opens the
scope's file in `$EDITOR`. Scope selection mirrors **init**.
_Avoid_: config command as a synonym for the persisted **Config** itself.

**Global vs project scope**:
Whether **Config** and assets apply machine-wide (**global**) or only within one repository
(**project**). Project overrides global. The git-loopy engine is installed once, globally; scope
governs *which* settings and assets resolve for a run, not which binary runs.
_Avoid_: local (ambiguous), workspace.

### The runner family

**Runner family**:
The set of interchangeable git-loopy runners that each implement the same **Wrapper contract**
in a different host language — the Python reference runner plus the planned **shell**,
**PowerShell**, and **Rust** ports. One family, one contract, many languages; an operator picks
the runner that matches their OS and the language they are comfortable with.
_Avoid_: variants, flavors, backends.

**Orchestrator**:
The host-language half of a runner — the loop logic and `gh` / `git` / `copilot` plumbing
(collection, discrimination, run, auto-close, **Strike** accounting, **Checkpoint**, push,
**Config**, OTel). Each language port is a distinct Orchestrator; every Orchestrator drives the
one shared **TUI helper** and emits the one **Event schema**.
_Avoid_: driver, engine; wrapper (the *contract* is the Wrapper contract — the *code* is the
Orchestrator).

**TUI helper**:
The single shared live-interface renderer for the non-Python runners — one Rust/ratatui codebase
compiled to the standalone `git-loopy-tui` binary that the **shell** and **PowerShell**
Orchestrators launch and feed over the **Event schema**, and embedded in-process by the **Rust**
port. The Python runner keeps its own Textual renderer; the TUI helper gives the other ports live
parity without a hand-rolled TUI per language.
_Avoid_: "the TUI" (ambiguous with the Python Textual app), frontend, renderer (collides with the
Python `Renderer`).

**Event schema**:
The single JSONL event vocabulary every **Orchestrator** emits and the **TUI helper** and the
replay log both consume — low-level live records plus authoritative lifecycle and accounting
records, all sharing the envelope (`ts`, `run_id`, `iter`, `type`, payload) and fixed type
string literals (`git_loopy.events`). The string *literals*, not the constant names, are the
contract downstream tooling reads.
_Avoid_: log format, event stream (as the name), telemetry.

**Insight capability**:
An **Orchestrator**'s declaration that its runtime can truthfully supply a particular
**Dashboard** signal. An unavailable signal remains unknown rather than being
estimated; zero and an empty set mean the signal was observed and nothing occurred.
_Avoid_: renderer feature, best-effort metric.

**Wrapper contract**:
The language-neutral behavioural specification every **Orchestrator** must satisfy —
`ready-for-agent` collection, the `## What to build` + `## Acceptance criteria` discriminator, the
pool-whitelisted `Closes/Fixes/Resolves #N` backstop, progress/**Strike** accounting,
**Checkpoint** + push, the exit-code table, and the `GIT_LOOPY_*` env surface. Versioned in
`docs/wrapper-contract.md`; enforced across the family by the **Conformance suite**.
_Avoid_: runner contract, "the spec" (informal).

**Conformance suite**:
The language-neutral fixture set — golden cases for the discriminator, the close-keyword regex,
progress/strike accounting, and the exit-code table — that every **Orchestrator** runs in CI and
must pass, keeping the **Runner family** from drifting. The generalized successor to the deleted
two-runner cross-parity test (ADR-0002).
_Avoid_: parity test (the retired two-runner name), integration tests.

### Parallel execution

**Parallel mode**:
The opt-in execution mode in which the runner works several independent issues at once,
each isolated in its own worktree, instead of one at a time. Off by default — the serial,
one-issue-at-a-time loop is the default.
_Avoid_: concurrent mode, multi mode.

**Rolling dispatch**:
The **Parallel mode** scheduling model that continuously refills reusable **Lanes**
instead of grouping them behind a barrier. It fills toward the **Lane cap** while
eligible work and **Integration** admission capacity exist; worktree setup, Lane work,
and Integration may overlap.
_Avoid_: Wave, batch, cohort, sliding window.

**Lane**:
One reusable concurrent execution slot in **Parallel mode**. A Lane works one
**Parallel-safe** issue at a time in its own worktree and branch, then becomes available
for refill once its finished branch is admitted to **Integration**. Shown as one active
row in the **Dashboard**, with its own timer and **Log**.
_Avoid_: worker, thread.

**Lane contribution**:
One **Parallel-safe** issue's end-to-end unit of **Parallel mode** work, beginning
when its Lane agent session starts and ending at green publication or a terminal
unpublished handoff. It persists through parking, **Integration**, and recovery even
after the reusable **Lane** moves on.
_Avoid_: parallel Iteration, round, Wave, session.

**Lane cap**:
The configured upper bound on concurrent **Lane** work. It is a safety and resource
ceiling, not a utilization promise: **Rolling dispatch** may deliberately leave capacity
idle when the eligible **Pool** is small or **Integration** applies backpressure.
_Avoid_: worker count, target concurrency.

**Integration backlog**:
The bounded set of finished Lane branches admitted to **Integration** but not yet
landed. Its high-water mark applies backpressure to **Rolling dispatch**, preventing
unbounded branch staleness and wasted API capacity.
_Avoid_: Queue (the per-Run issue ledger), merge queue.

**Integration**:
The serialized **Parallel mode** stage that consumes the **Integration backlog**, brings
each finished Lane branch into the base branch one at a time, re-runs the feedback loops,
and closes the issue on success. A conflicting or loop-failing branch triggers a
runner-driven auto-resolution attempt; persistent failure falls back to a serial
**Iteration**. Runner-owned — it never waits on a human.
_Avoid_: merge (as the name for this step), landing.

**Parallel-safe**:
A `ready-for-agent` issue a human has additionally asserted is independent and
well-scoped enough to be worked in its own **Lane**, concurrently with others.
Carried as a triage label alongside `ready-for-agent`; the runner never infers it.
_Avoid_: independent, parallelizable (as the label name).

## Relationships

- A **Workflow** can be traversed by many **Workstreams**. Each Workstream has
  exactly one durable **Anchor** and one **Destination**.
- Artifacts created before a Workstream reaches its Destination may become Targets or
  Basis without changing its Anchor. Crossing a Destination boundary terminates that
  Workstream; later work belongs to a distinct **Successor Workstream**.
- A **Workflow transition rule** derives a Successor Workstream from durable terminal
  evidence. Its predecessor outcome and **Successor slot** form the successor's
  immutable Anchor.
- A **Workstream** owns many **Continuation actions**; each action belongs to exactly
  one Workstream and has one primary **Target**.
- A **Continuation action** has an **Instruction**, durable **Basis** and Producer
  provenance, zero or more **Prerequisites**, an interaction classification, and a
  durably evaluable completion condition.
- Interaction classification evidence is part of **Action semantics**. **AFK-safe**
  requires an **AFK safety case**; **HITL-required** requires a typed human boundary
  and a durable human-resolution condition. Uncertainty is not a fallback HITL label.
- **Chart workstream**, **Resolve decision**, **Provide information**, **Perform manual
  validation**, **Authorize operation**, and **Review and merge PR** are always
  **HITL-required**. **Prototype evidence** may establish objective evidence but never
  resolves the human decision it informs; other Action kinds are classified per Action.
- A `manual` **Instruction** is always **HITL-required**. A `skill` or `command`
  Instruction is only a candidate for **AFK-safe** and must expose a compatible
  noninteractive behavior.
- One **Action identity** denotes one **Action occurrence**. Equivalent live claims
  collapse and combine their **Basis** and **Producer** provenance; different live
  **Action semantics** under that identity form a **Continuation conflict**.
- Each durable Workflow transition has one **Transition owner**. Nested or supporting
  surfaces are **Pointer-only participants** unless they own a separate anchored
  transition.
- A Transition owner publishes only after durable transition evidence exists and may
  replace only its own prior fragment. Failed writes and undefined successors are
  errors, not `no-guidance`.
- Shared Workstream discovery follows durable Anchors, Producer carriers, and typed
  Artifact roles or relationships. Local worktrees, conversations, and temporary
  documents can support only explicitly ephemeral guidance.
- An artifact-creation transition must reach a durable Complete outcome before its
  partial artifacts can support successor Actions. Incomplete specification or ticket
  publication is quarantined rather than treated as executable work.
- A **Prerequisite** becomes a **Blocker** only while it is unsatisfied. **Readiness**
  is independent of whether an action is **HITL-required** or **AFK-safe**.
- **Reconciliation** may move the same Action between Ready and Blocked without
  changing its identity. It retires the Action only with durable evidence and a
  **Retirement receipt** when a **Producer revision** removes it.
- A **Continuation view** respects the Prerequisite graph. Workflow semantics may rank
  otherwise-independent Actions only within a local Workstream rule; Action kind does
  not impose a global stage order across unrelated Workstreams. Stable **Anchor** and
  **Action identity** ordering breaks remaining display ties; timestamps and discovery
  order do not.
- An **Unverified** Action or **Continuation conflict** is outside actionable ordering.
  Its affected dependents are quarantined while independently verified guidance remains
  usable.
- An action is **AFK-eligible** for a **Performer** only when it is **Ready** and
  **AFK-safe**, and fresh positive **Performer posture** evidence satisfies every
  declared **Eligibility requirement**. Eligibility is rechecked before consequential
  effects and never implies Run authorization.
- An **Automation scope** separates observed coverage from execution grants and is
  frozen before dispatch. Revocations may narrow it immediately; later grants do not
  expand the current Run.
- A Run's **Automation frontier** freezes all current in-coverage Action identities and
  semantic fingerprints. Existing Blocked Actions may become
  **Automation-selectable** as facts change; new Actions and changed semantics remain
  report-only until a later authorized Run.
- Each **Continuation dispatch** binds exactly one Action. A Run may reconcile and
  dispatch multiple independent members of its fixed frontier, but it never adds a
  newly produced successor, and AFK eligibility never implies **Parallel-safe**.
- A detected human boundary never becomes an implicit interactive prompt. Authorized
  partial effects may remain, but the Action stays unmet and a **Safety-case violation**
  requires Transition-owner correction; independently verified frontier work may
  continue.
- An **Automation stop** is a Run-level explanation, not a **Strike** or Workstream
  disposition. Only verified terminal outcomes for every Workstream in closed coverage
  permit completion; a drained frontier, HITL boundary, missing grant, ineligible
  Performer, Blocker, or guidance fault leaves nonterminal Workstreams active.
- **Continuation guidance** contains current unmet actions and explicit
  **Workstream outcomes**, not execution history. A **Continuation view** projects
  that guidance for one Consumer without turning display order into dependency.
- **Parent cleanup** is an independent, low-priority Workstream and never blocks a
  substantive successor.
- A review Action occurrence is pinned to one exact durable code revision. Any changed
  head, including conflict resolution or review remediation, requires a new review
  occurrence before publication or integration.
- Empty frontiers, phase completion, clean review, and lack of AFK-eligible work are not
  **Workstream outcomes**. Only a durably recorded Complete, Rejected, Abandoned, or
  Superseded disposition terminates a Workstream.
- A **Handoff** may be referenced as supporting context, but its suggested next step
  is not current **Continuation guidance** until a Producer reconciles it against
  durable workflow state.
- `ready-for-agent` is a tracker delegation signal that may provide **Basis** for an
  issue-execution action; it is not a synonym for **AFK-safe** or **AFK-eligible**.
- A **Skill baseline** seeds a **Skill policy**; later **Skill catalog** changes do not
  expand that policy.
- A **Run** resolves one **Effective Skill policy** before work begins. Every
  **Required Skill** must belong to it, and every serial **Iteration** and parallel
  **Lane** shares it.
- A **Run** has many serial **Iterations** and/or parallel **Lane contributions**.
- An **Iteration** is offered one **Pool** and produces at most one **Active issue**.
- A **Queue** belongs to exactly one **Run** and aggregates every issue seen across
  its serial **Iterations** and parallel **Lane contributions**, keyed by issue.
- An **Active issue** is the **Pool** member named by the current **Working marker**.
- A serial **Iteration** binds to at most one **Active issue**; its first valid
  **Working marker** is authoritative for the rest of that Iteration.
- The **Dashboard** shows the **Queue**; selecting a row opens that issue's **Log**.
  Each issue has its own **Log**, which accumulates across every serial **Iteration**
  and parallel **Lane contribution** that worked it.
- A **Checkpoint** is authored by the runner (not the agent) at a serial
  **Iteration** or Lane-work boundary and is attributed to the **Active issue**, but
  never counts as **Strike** progress.
- A **Sandbox** is scoped to an **Iteration**: each **Iteration**'s agent shell runs
  inside a fresh **Sandbox**, so per-issue isolation follows from per-**Iteration**
  freshness.
- **Consumption** is attributed to a scope: a serial **Iteration** or parallel
  **Lane contribution** (a **Summary** row's Cost), or an **Active issue** (the
  **Queue**'s per-issue Cost). Both derive Cost from the same `UsageTally` rule, so
  per-issue and accounting-row figures stay reconcilable.
- A context cutover starts another **Iteration** pinned to the same **Active issue**;
  it does not create a sub-Iteration accounting entity.
- An issue's **Iteration breakdown** has one row per Iteration contribution; the
  Queue's Iteration count is the number of those rows.
- In **Parallel mode**, **Rolling dispatch** reuses **Lanes** continuously rather than
  grouping them into barrier-synchronized rounds.
- A **Lane contribution** belongs to one **Active issue** and may outlive the reusable
  **Lane** that began it while it parks, integrates, or recovers.
- The **Lane cap** is an upper bound; **Integration** backpressure may intentionally
  leave Lane capacity idle.
- A **Lane** works exactly one **Parallel-safe** issue at a time. Once its finished
  branch enters the bounded **Integration backlog**, the Lane can take another issue.
- **Integration** consumes that backlog serially, brings each branch to base, and closes
  the issue, so the **Queue** reaches **closed** the same way it does in serial mode.
- In **Parallel mode** the **Sandbox** is per-**Lane**: each Lane's agent runs in its own
  **Sandbox** scoped to that Lane's worktree — the parallel analogue of the per-**Iteration**
  Sandbox.

## Example dialogue

> **Dev:** "If the agent works issue #12 across two different iterations, is that one
> queue entry or two?"
> **Domain expert:** "One **Queue** entry — the queue is keyed by issue, and its
> active time sums across every iteration that worked it. Those are two distinct
> **Iterations**, but the same **Active issue**."

## Flagged ambiguities

- `queue` was used to mean both a single iteration's input set and the whole-run list
  of issues — resolved: the per-iteration input is the **Pool**; the whole-run,
  status-bearing list is the **Queue**.
- `current task` / `current issue` was used loosely for whatever the agent was doing —
  resolved: the agent's in-flight selection is the **Active issue**, declared via its
  **Working marker**.
- `log` vs `transcript` were both used for the live per-issue output (the code's
  drill-in called it a "transcript"; the early UI also had a whole-run "Log" tab) —
  resolved: the single per-issue, timestamped, auto-scrolling record is the **Log**;
  "transcript" and the whole-run Log tab are retired.
- `commit` was ambiguous once the runner began authoring commits — resolved: an
  agent-authored commit is a plain commit and counts as progress; a runner-authored
  one is a **Checkpoint** and does not.
- `wave` vs `iteration` — resolved historically as parallel and serial round units, then
  superseded for Parallel mode: **Rolling dispatch** has no barrier round. An
  **Iteration** remains the serial session/accounting unit; **Lanes** are reusable and
  **Integration** is a separate serialized stage. **Wave** remains only as the
  historical ADR-0008 model.
- `ralph` / `ralph-afk` / `copiloop` / `git-loopy` were used interchangeably for the tool —
  resolved: **git-loopy** is the framework, CLI, and brand (`git-loopy` as the distribution and
  console command, `git_loopy` as the importable Python package); a **Ralph loop** is the
  retained *concept* (the loop technique). Two product brands are now retired: **ralph-afk**
  (every `ralph` / `ralph_afk` identifier, the `ralph/` and `.ralph/` directories, and the
  `RALPH_*` env vars — ADR-0005) and **copiloop** (every `copiloop` / `Copiloop` identifier, the
  `copiloop/` and `.copiloop/` directories, the `copiloop.*` spans, and the `COPILOOP_*` env
  vars — ADR-0012), both in favour of `git-loopy`, `git_loopy`, `.git-loopy/`, and `GIT_LOOPY_*`.
- `sandbox per issue` (from the feature request) implied a fresh isolation unit keyed
  to an issue — resolved: the **Sandbox** is scoped to an **Iteration**, which subsumes
  per-issue because every issue boundary is also an **Iteration** boundary.
- `the runner` / `the bash port` / `the script` were used loosely once a second and third
  language port arrived — resolved: the whole is the **Runner family**; a single member is a
  named **Orchestrator** (the Python, shell, PowerShell, or Rust Orchestrator); the shared
  live-interface binary is the **TUI helper**, distinct from the Python runner's own Textual
  renderer. "The runner" as a proper name is avoided (ADR-0013).
- `session` was proposed as a finer-grained Dashboard/accounting unit once context
  cutovers were introduced — resolved: every fresh agent session starts another
  **Iteration**, so no sub-Iteration Session concept is added.
- `completed` was used for any terminal Queue outcome — resolved: only **Closed**
  means the source issue actually finished. **Issue elapsed** is first activation
  through closure; **advanced**, **no-progress**, and **gone** keep an empty closure
  stamp.
- `context usage` was used for both cumulative observed tokens and live window
  pressure — resolved: **Context fill** is the current Iteration's live occupancy,
  **Observed tokens** is its cumulative token total, and **Consumption** is the
  scoped tokens-and-cost measure.
