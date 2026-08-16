# git-loopy and Loop Engineering

**git-loopy** is the GitHub Copilot SDK framework and brand for encoding
specialized engineering knowledge into repeatable, autonomous workflows over
agentic-engineering work. **Loop engineering** is the practice of shaping that
work into explicit, reviewable units, setting guardrails and feedback loops, and
supervising autonomous execution. This glossary fixes the vocabulary shared by
the planning skills, issue tracker, Runner family, and live interface.

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
The practice of encoding an engineer's or organization's specialized engineering
knowledge into repeatable workflows, then operating and improving them. It connects
human-led planning and context engineering to small triaged issues, explicit
acceptance criteria, feedback loops, guardrails, and human review so autonomous
execution stays aligned. Its unit of value is a workflow that can run hundreds of
times, not a single conversation that ends with its session.

**Loop engineer**:
The human who designs, triages, and supervises the loop. The loop engineer owns
intent, domain language, issue slicing, acceptance criteria, guardrails, and final
judgment; git-loopy owns repeatable execution.

**Meta-engineering**:
Working on the system that builds and operates the software rather than on the
software directly. The loop engineer's leverage: an improvement to a workflow,
a prompt, a routing decision, or a feedback loop is paid back by every subsequent
**Run**, which is why evidence from a Run is fed back into the system and not only
into the branch.
_Avoid_: automation (too broad), tooling work, prompt engineering (a part, not the whole).

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

**Continuation capability profile**:
One named set of requirements a **Continuation capability manifest** must satisfy for a
setup surface to install or write anything. `foundation` is the only profile the family
declares. Judging a manifest against a profile is a **Consumer** act, not a Continuation
operation, and satisfying one grants no execution authority.
_Avoid_: capability tier, feature flag, Continuation mode, readiness level.

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

**Agent**:
One live harness session doing work in a **Run**, bound to a single **Routed pair** for
its lifetime: a serial **Iteration**'s session, a **Lane**'s session, or an
**Integration** auto-resolution session. It is the unit a **Run** has several of at once
in **Parallel mode**, and the unit an **Activity window** shows. Distinct from the
`ready-for-agent` label, which describes an issue's triage state rather than anything
live.
_Avoid_: session (the harness's word), worker, lane (a Lane is a slot, and an Agent may
outlive it).

**Subagent**:
An agent an **Agent** spawns inside its own session, on its own model. Its lifecycle —
start, finish, failure, and the totals it reports — is observable; its output never
reaches the **Run**'s event stream, so elapsed time is the only account of what it did.
Its **Consumption** is accounted to its parent **Agent** and never tallied as its own.
_Avoid_: child agent, nested session, task (the launching tool's name).

**Skill**:
A named capability package whose instructions and resources a **Performer** may load
when a task matches its purpose. Its canonical name is its policy identity; when
multiple sources provide that name, git-loopy's **installed catalog** wins and the
external agent client's source precedence resolves the remaining candidates.
_Avoid_: custom instruction, tool.

**Skill baseline**:
The exact initial enabled/disabled selection across the **Skill catalog**, copied from
an operator's external agent client when establishing the first configured
**Skill policy**. It seeds that policy but does not remain its authority. A later
project Skill policy starts from the inherited global policy unless the operator
explicitly requests another external-client sync.
_Avoid_: live mirror, source of truth.

**Installed catalog**:
The Skills git-loopy itself provides: a checkout of the external Skill catalog at the
immutable revision this release pins, written into git-loopy's own config home rather
than into any repository or agent-client directory. Setup installs it and every **Run**
refreshes it against the pin, so it is current without being mutable. It is the only
Skill source git-loopy reads directly; the consuming repository's own agent-skill
directory is not one.
_Avoid_: packaged fallback, vendored skills, bundled catalog.

**Skill catalog**:
The inventory of Skills an operator may inspect and select for git-loopy, including
project, personal, plugin, built-in, and custom sources reported by the external agent
client, plus git-loopy's **installed catalog**. It is refreshed
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

**Contract-carrying Skill**:
A Skill whose instructions invoke a `git-loopy` subcommand, so what it tells a session
to do is part of a git-loopy contract rather than general engineering guidance. Twelve
carry the **Workflow continuation** contract today: eleven Transition owners that
publish a record through the native command, and the one Consumer that reconciles
those records and writes nothing. Each is authored in the external Skill catalog and
reaches git-loopy as publish, pin, mirror, in that order: a copy edited here is a
change no **Installed catalog** can hand to a session
([ADR-0034](docs/adr/0034-contract-carrying-skills-are-authored-upstream.md)).
_Avoid_: git-loopy skill, bundled skill, packaged skill.

**Iteration**:
One serial cycle of the loop — collect the pool, let the agent work exactly one task,
then do commit accounting and a progress check. The serial unit by which elapsed time
and streamed output are measured and attributed. Each fresh serial work session is a
new Iteration, including a context-cutover continuation pinned to the same
**Active issue**.
_Avoid_: round, pass, tick; session as a separate accounting unit.

**Label vocabulary**:
The labels a repository's tracker must carry before a Run can do anything: the five
canonical triage roles a human triages with, plus **Parallel-safe**, **Priority**, and
the seven closed **Task type** labels. `git-loopy init`
ensures it exists, creating only what is absent and never altering a label that is
already there. The five roles take whatever strings the repository's documented
triage-label mapping gives them; the rest take the one string the runner
reads. A tracker with no `ready-for-agent` yields an empty **Pool** forever, one
with no `parallel-safe` can never engage **Parallel mode**, and one with no
`priority` ranks every eligible issue the same.
_Avoid_: tags; triage vocabulary as the name for the whole set — **Parallel-safe**,
**Priority** and the **Task type** labels are not triage roles.

**Vocabulary drift**:
The difference between the **Label vocabulary** and what a tracker actually carries.
Because `init` *ensures* rather than reconciles, and runs once, a label added to the
vocabulary afterwards never lands and a colour or description that diverges stays
diverged — in both cases silently. `git-loopy labels` reports every entry as missing,
drifted, or matched, and `--apply` writes the difference back. A renamed triage role is
neither: it resolves through the documented mapping and *is* the vocabulary under this
tracker's string. Reconciling is additive — it creates and corrects, never renames, and
a tracker label outside the vocabulary is never reported and never deleted.
_Avoid_: sync, mirror; drift as the name for a **Roster drift** (which is about models).

**Pool**:
The candidate `ready-for-agent` work discovered from the source. A serial **Iteration**
collects its own full authoritative Pool at the start of the Iteration and offers it to
the agent together in a single prompt; the agent picks one. **Rolling dispatch** instead
keeps a continuously refreshed cache of *shallow* Pool membership and re-reads one
candidate authoritatively immediately before reserving its **Lane** — membership alone is
never authority to start a **Lane contribution**. An incomplete or failed read leaves the
Pool's emptiness unknown rather than empty. A **Parallel mode** Run's Pool has two halves
— **Parallel-safe** candidates and **serial-required** work — and may be called empty
only once both have been seen.
_Avoid_: batch, backlog.

**Pool exclusion**:
A `ready-for-agent` candidate the AFK-ready discriminator rejected because its body
lacks `## What to build`, `## Acceptance criteria`, or both. A human deliberately
triaged the issue; only a human can fix it, so every exclusion is named to the operator
and carried as a `wrapper.pool.excluded` **Event** with its reason. A candidate the
source could not *read* is not an exclusion — it was never discriminated against.
_Avoid_: skipped, filtered, invalid issue.

**Strike**:
A recorded no-progress result: a serial **Iteration** made no meaningful progress, or
a parallel **Lane contribution** terminated unpublished. A fixed number of consecutive
strikes ends the run. Both kinds of work tick one shared count, so in **Parallel mode**
reaching the limit stops refill and grants no further serial Iteration, then ends the run
once started work has drained. A runner **Checkpoint** does not count as progress.
_Avoid_: failure, miss.

**Session outcome**:
How one **Agent**'s session ended, as data the loop keeps rather than a sentence it
logs. Five endings and the absence of one are the whole vocabulary: silent no-progress,
timeout, crash, an explicit declaration that no tasks remain, and content-filtered — and
a session that advanced its issue reached none of the three that are claims about the
work, because a commit refutes each. A timeout and a crash are facts about a session the
Orchestrator lost, which progress does not launder. Distinct from a **Strike**,
which is the Run's *accounting* of a result: several endings tick the same strike, and
the ending is what says which. Distinct too from an Iteration's outcome in the Run
summary, which reports what the work produced rather than how the session finished.
_Avoid_: exit code, session status, failure reason.

**Session error**:
The structured identity of a failure the harness reported during a session — its error
type, its message, the service status code where one was reached, and where in the
session it arose — classified into a closed set of kinds: quota exhaustion, rate
limiting, authentication, bad request, transport, service fault, and unknown. It
is what lets an exhausted account be told from a session that merely produced nothing,
which a flattened string never could. Recording one implies no reaction: the ending is
still attributed to the issue that happened to be in hand. Content filtering is not one
of these kinds: the harness reports it as a verdict on a call it completed rather than
as a failure, so it is an ending a session reached, never an identity a failure carries.
_Avoid_: error message, failure string, exception.

**Checkpoint**:
A runner-authored commit that captures any uncommitted or untracked changes the agent
left at a serial **Iteration** or Lane-work boundary, so subsequent work starts from a
clean durable branch. It is close-keyword-free (never auto-closes an issue) and does
not count as Strike progress. Distinct from the agent's own commits.
_Avoid_: autosave, stash, snapshot.

### Issues and attribution

**Active issue**:
The single issue the agent is working during the current iteration, chosen by the
runner from the pool at **Pickup** and bound for the length of that work. In
**Parallel mode** each **Lane** has its own Active issue. The agent is told which
issue it is working; it does not choose.
_Avoid_: current task, current ticket.

**Working marker**:
The agent's explicit, up-front restatement of its active issue, used to attribute the
iteration's timing and streamed output to that issue in real time. Because the runner
binds the Active issue at **Pickup**, the marker confirms that binding rather than
creating it; a marker naming a different issue is a disagreement to record, not a
reassignment.

**Pickup**:
The instant the runner binds one issue to a unit of work, before that unit's agent
session begins. Because the issue is known first, pickup is where an unlabelled issue's
**Task type** is inferred, where its **Routed pair** resolves and where its **Lease** is
taken. Every unit of work has a pickup — a serial
**Iteration** as much as a **Lane** — and every pickup is carried as a
`wrapper.pickup.bound` **Event** naming which issue, why, where it sat in the order, and
the **Routing resolution** it reached. The resolution rides on that one record rather than
on a second Event, because it is settled at the same instant, for the same issue.
_Avoid_: assignment, dispatch, selection.

**Pickup skip**:
A candidate the runner walked past at **Pickup** without binding — today, one whose
`task-type:` label refuses to resolve a **Routed pair**. Distinct from a **Pool
exclusion**, which happens at collection and is a human's mistake to fix: a skip is the
runner declining work it could not start, and it carries a `wrapper.pickup.skipped`
**Event** so that being passed over leaves a trace. An issue passed over fifty times
used to be indistinguishable from one nobody had reached yet.
_Avoid_: rejection, exclusion, deferral.

**Lease**:
A run's exclusive right to work one issue, taken at **Pickup**, held while its owner
stays alive, and expiring on its own if the owner stops. It names the run that holds
it, so a run that dies frees its issues without anyone intervening. Leases are what
let several runs share one tracker: an issue under another run's live Lease is not
available to this run.
_Avoid_: claim, lock, reservation, hold.

**Priority**:
The axis on which an issue is worked ahead of older ones, carried as a label and read
at selection. It is orthogonal to **Task type**, which selects a **Routed pair** and
never affects order. Priority reorders work; it does not change what is eligible, and
it never lets an issue past a **Lease**.
_Avoid_: severity, urgency, task type.

**Pin**:
One issue an operator names for one invocation (`--issue N`), worked ahead of the head
of the order and ahead of **Priority** — it outranks the label because a human said so
directly rather than in advance. A pin bypasses order and *nothing else*: the issue
still has to be eligible, and a pin that is not fails the invocation rather than
falling back to the order, because silently working a different issue than the one
named is worse than stopping. It lasts exactly one invocation, which is why it is
neither a label nor an environment variable — both are global, and would point every
concurrent run at the same issue.
_Avoid_: lock, claim, assignment, selection, priority.

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
line-by-line scrollback output. It has two forms: the **voluntary** one the operator
asks for, and the **involuntary** one a **Dashboard fault** produces. Both produce the
same continuation — the loop runs on, the sinks swap to the parked line printer — and
are labelled differently, because one of them is a bug the operator wants to see
(ADR-0024).
_Avoid_: background, minimize, exit.

**Dashboard fault**:
A **Dashboard** that raises — at startup or mid-**Run** — which the Run survives. It
is an involuntary **Detach**: the operator loses the live view, not the work, and the
Run continues on the parked line printer (ADR-0024). It is recorded distinguishably
from a voluntary Detach, reported at the point of the swap, and carries its own exit
code, so a supervising script is never told everything was fine.
_Avoid_: TUI crash, renderer error, dashboard failure (as the name).

**Terminal owner**:
The single component responsible for the terminal's mode state for the whole process
(ADR-0024). It captures the terminal's entry state before the **Dashboard** starts and
restores that captured state — not an assumed one — on every ordinary exit path,
including a **Stop**, a **Detach**, a **Dashboard fault**, an unhandled exception and a
signal. Release is idempotent, and the non-interactive path acquires no ownership at
all.
_Avoid_: terminal manager, screen guard, teardown hook (restoration is not the
Dashboard's teardown).

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
the run-end table. A band of the **Dashboard**, not a separate screen. A row is cut
only when its accounting unit **finalizes**, so work still parked, integrating, or in
recovery has no partial row — under **Rolling dispatch** an unfinished **Lane
contribution** is simply absent from the Summary, never half-counted in it.

**Activity**:
The **Dashboard** band that holds one **Activity window** per live **Agent**, always
present below the **Queue** (between it and the **Summary**). A glance at what every
Agent is doing right now, so a run reads as active instead of appearing stuck while
issues sit **queued**; it complements, and does not replace, the per-issue **Log** that
enter opens for the full, scrollable history. The operator sizes it by dragging its
header row, or from the keyboard a row per press, and it holds two numbers rather than
one: the height that was *asked for* and the largest that currently *fits*. Only a
gesture writes the former, so a terminal that shrinks and grows again returns the band
to the operator's height; and it never grows past what leaves the **Queue** its own
three-row floor. Sized below its own three-row floor it is **Collapsed**. A band of the
**Dashboard**, not a separate screen.
_Avoid_: stream, feed.

**Collapsed**:
The **Activity** band rendering its one-line header and nothing else. A state of the
band, not its absence: it keeps that row in the **Dashboard** layout, so an operator can
always see an Activity band is there and the gesture that collapsed it has a handle to
undo it with. `a` and a bare click on the header collapse and restore, preserving the
height the operator asked for; a drag of the header past the band's three-row floor
collapses, as `shift+down` does, and dragging back out of the stub reopens the band —
where `shift+up` reopens it at that floor rather than the remembered height, because
sizing gestures state fresh intent. The controls degrade in the order **drag → click →
keys**, so a terminal that reports less than motion still has one. It
survives a terminal resize and a drill-in to a **Log**, and is in-session only — no
**Config** or **Run** state records it.
_Avoid_: hidden, closed, minimised, off.

**Activity window**:
One **Agent**'s pane within the **Activity** band: a header naming that Agent's issue,
**Task type**, **Routed pair**, **Context fill**, and live **Subagent** count, above the
Agent's own tail. It follows the newest line until the operator scrolls away from the
bottom, and follows again when they return to it. In **Parallel mode** a window is held
by a **Lane** slot and re-labelled when that slot refills; **Integration** holds its own.
_Avoid_: pane, split, tab.

**Context fill**:
One **Agent**'s live context-window occupancy — current tokens divided by the model's
token limit — shown in the **Dashboard** header for a serial **Iteration** and in each
**Activity window** in **Parallel mode**, with Smart-Zone target and ceiling cues. It is
scoped to the Agent's own session and resets when a new one starts, and is distinct from
both **Observed tokens** and **Consumption**, which accumulate billed tokens and cost.
_Avoid_: context usage, cumulative tokens, token consumption.

**Consumption**:
The tokens-in / tokens-out, the model they were billed against, and the billing the
harness reported for them — **AI Credits**, premium requests, and the cache-read /
cache-write split — attributed to a scope: a serial **Iteration** or parallel **Lane
contribution** (the basis for a **Summary** row's Cost), or an **Active issue** — summed
across every accounting unit that worked it — the basis for the **Queue**'s per-issue
Cost. Consumption *carries* what was billed; it does not denominate it. Turning a
Consumption tally into a Cost figure is the **Cost denomination**'s job. A figure no
sample reported stays unknown, and a total missing one of its terms is unknown too
rather than an understatement.
_Avoid_: usage, spend (for the token measure); billing.

**Cost denomination**:
The single injected seam that turns a **Consumption** tally into a Cost figure, resolved
once per **Run** and threaded to every Cost-bearing surface — the **Summary**, the
**Queue**, the **Iteration breakdown** and **Rolling dispatch**'s cost pressure — so
those surfaces cannot disagree about what an issue cost. The one production
denomination reads the **AI Credits** the harness billed; unknown is unknown, never zero
(ADR-0026).
_Avoid_: pricing, cost calculator, estimator.

**AI Credits**:
The unit Cost is denominated in: the billing the harness itself reports, which is what
the quota is drawn against. It is the primary, un-derived Cost figure — read and
totalled, **never recomputed** from tokens and prices in any code path, including as a
fallback — with the **premium-request count** reported alongside it, because that is the
budget an operator actually exhausts mid-**Run**. A figure the harness did not report is
unknown, and unknown renders as unavailable — never as zero, and never as an estimate
wearing a billed figure's clothes (ADR-0026). git-loopy publishes **no USD figure**: no
field on any surface the kit reads is denominated in dollars by its schema, and a
currency is never inferred from an unlabelled float. The bar for revisiting that is a
**dollar-denominated figure published by the harness itself**.
_Avoid_: credits (unqualified), spend, estimated cost; dollars or USD as a name for
this unit.

**Rate card**:
The harness's own live per-model price listing — separate input, output, cache-read and
cache-write prices, the billing batch size and the nested long-context block — read from
the same `models.list` call that supplies the model roster, resolved once per **Run**,
held fixed, and published in that Run's **Insight capability** block. Its prices are
denominated in **AI Credits**, the unit the harness has already billed in, so the card is
**provenance, not arithmetic**: nothing derives a figure from it, and an absent card
never costs a figure. Never packaged, cached, or otherwise hand-maintained (ADR-0026).
_Avoid_: pricing table, price list, `pricing.toml` (the deleted hand-authored table).

**Observed tokens**:
The cumulative tokens-in plus tokens-out reported during an **Iteration**. An
accounting total, not model-window occupancy; it is never expressed as a percentage
of the context window.
_Avoid_: context used, context utilisation, context fill.

**Iteration breakdown**:
The per-issue drill-in band that itemizes each **Iteration**'s contribution to that
issue, including its **Consumption**, Cost, and available peak **Context fill**. In
**Parallel mode**, the row is the issue's **Lane contribution** — attributed to the
issue that produced it, never to whichever issue its **Lane** slot went on to work.
_Avoid_: session breakdown, history table.

**ModelSelectionMode**:
The opt-in startup state — entered with the `--select-model` flag or
`GIT_LOOPY_MODEL_SELECT=1` — that shows the live model + reasoning-effort picker before the
run starts. Off by default: an ordinary launch uses the configured model and reasoning
effort with no prompt.
_Avoid_: picker mode, interactive model prompt.

### Framework and configuration

**git-loopy**:
The GitHub Copilot SDK loop-engineering framework and brand for encoding specialized
engineering knowledge into repeatable, autonomous workflows. It ships a **Runner
family**: the Python reference runner (the globally-installed `git-loopy` console
command; `git loopy` also works as a git subcommand) plus the planned **shell**,
**PowerShell**, and **Rust** ports, all implementing one **Wrapper contract**.
Written `git-loopy` as the distribution, console command, and on-disk/brand
spelling; `git_loopy` as the importable Python package. Supersedes the retired
**copiloop** and **ralph-afk** brands.
_Avoid_: copiloop, ralph-afk, "the runner" as a proper name.

**Release version**:
The Semantic Versioning identity assigned to one published git-loopy distribution.
Every included **Orchestrator** and **TUI helper** shares it; the **installed catalog**
does not, because a distribution carries no Skills — only the pin naming the revision
to install. Contract and schema versions remain separate compatibility identities.
_Avoid_: component version, protocol version, schema version.

**Release target**:
The planned **Release version** to which an issue contributes. It states delivery
intent without changing the issue's workflow readiness or dependency relationships.
_Avoid_: version label, release label.

**Autonomous loop**:
The *technique* git-loopy orchestrates — an unattended, iterative execution loop that
drives the Copilot agent to work triaged issues one at a time, bounded by feedback
loops, **Strikes**, and human review. Realized as a **Run** of **Iterations**; a
concept, never a code identifier. Named the "Ralph loop" until
[ADR-0031](docs/adr/0031-encoded-workflows-retire-the-loop-name.md) retired that name.
_Avoid_: Ralph loop (retired), ralph-afk (the retired brand), "ralph" in any form.

**Config**:
The persisted settings (model, reasoning effort, strike policy, denylists, ...) that carry across
runs so they need not be re-passed each time. Held on disk as a hand-editable `config.toml` in a
**project** and/or **global** **scope**, and merged key by key along the precedence chain
**CLI flag > env var > project > global > built-in default** (the denylists are the set *union*
across every tier). Replaces the per-run environment the retired bash launcher used to hard-code.
_Avoid_: settings file, profile.

**init**:
First-run setup that installs the **installed catalog**, then writes **Config** — and
optionally an editable prompt — into a chosen **scope**. Runs automatically the first
time on an interactive terminal; also invocable as `git-loopy init`.
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

**Task type**:
The classification an issue carries as a `task-type:<key>` label. Either set by an operator or
inferred from the issue's own content by the **Task-type classifier** and written back to the
tracker, where the two are thereafter indistinguishable (ADR-0029). The taxonomy is **closed**:
only the seven shipped keys are valid, and a classifier proposing or a label carrying any other
value is refused rather than warned about, even when a run-wide override suppresses routing. A
task type selects a **Routed pair** and nothing else: it does not order or prioritise work.
_Avoid_: category, priority, issue kind.

**Task-type classifier**:
The agent call that reads an unlabelled issue's own content and proposes its **Task type**.
Runs at every **Pickup**, serial and **Lane** alike, on the issue that Pickup bound and on no
other, so the spend buys the pair this **Iteration** runs on. Runs on its own configured pair —
defaulting to the cheapest pair on the live roster, never the run-wide default, so the prior it
introduces is named rather than implicit; where no cheapest rung is measurable it is inert
rather than guessing. It spends nothing on an issue that already carries a label, its spend is
folded into the run's cost like any other session, and it never ticks a **Strike**.
_Avoid_: triage, categoriser, router (that is **Routing**).

**Routing**:
Resolving an issue's **Task type** to the model and reasoning effort its work runs on,
replacing the single run-wide default with a per-task-type default. A **Config**-file-only
tier: any explicit model or reasoning-effort flag or environment override suppresses routing
for the whole run. An unlabelled issue or two keys disagreeing fall back to the run-wide default;
an unknown key is refused before selection, and a **Task type** the table does not configure
falls back to that default too.
_Avoid_: model selection (that is the operator-facing picker), dispatch, assignment.

**Routing resolution**:
The one per-Pickup record **Routing** answers with: a gated model, reasoning effort and context
tier; the `task-type` keys exactly as the tracker spelled them; the gate warnings raised while
gating them; a **Routing source**; and the attempt's lifecycle position. Every surface that shows
what a Pickup chose reads this record rather than recomputing it. Its source and lifecycle
position are distinct axes so an escalated pair and a same-pair retry stay tellable apart. It is
published on the **Pickup**'s own Event and as one line on stdout, so which model worked an issue
is answerable live and after the fact from the same record (ADR-0039).
_Avoid_: routed pair (only the model and effort values), routing decision (ambiguous).

**Routing source**:
The closed provenance vocabulary on a **Routing resolution**: routed, or defaulted for no label,
for a key the `[routing]` table does not configure, for conflicting keys, or for an explicit
run-wide override — or escalated. It says why the settings were selected; it does not say whether
this is the first attempt or a retry.
_Avoid_: lifecycle position, routing tier.

**Routed pair**:
The single model and reasoning effort one unit of work runs on, after **Routing** has
resolved it and the effort has been gated against the model roster — an effort the model
does not accept is dropped so the backend chooses. Resolved once at **Pickup** and never
switched mid-session; the same pair carries the unit's follow-on work.
_Avoid_: model override, effective model.

**Default pair**:
The kit's built-in model and reasoning effort — the pair a unit of work runs on when
**Routing** resolved nothing, which is every unit whose issue carries no **Task type**.
**Atomic**: naming a model opts out of the kit's effort too, and the pair becomes "let the
backend pick". It sits deliberately one rung *below* the escalation rung, so the default
**reserves** the ceiling instead of spending it and work that stalls has somewhere to escalate
to (ADR-0036). Identical in every member of the **Runner family**, and an independent constant:
it resembles one seeded **Routed pair** by rationale, never by derivation.
_Avoid_: global default (ambiguous — **Config** has global scope), fallback model.

**Escalation rung**:
The one model and reasoning effort a silently stalled unit of work is retried at. A **Session
outcome** of silent no-progress — and only that one, because a timeout answered with a slower
pair near-guarantees a second timeout, a crash is evidence about the harness, and a no-more-tasks
declaration is the **Agent** saying there is nothing to do — makes the next **Pickup** of that
issue resolve to the rung instead of its **Routed pair**, reporting the escalated **Routing
source** so a retry at a dearer pair is never mistaken for a routed one. Escalation is **once**
(a single rung, not a ladder), **sticky** for the rest of the **Run** so the issue does not fall
back to the pair that already stalled on it, **strike-free** because trying harder must not be
punished by the mechanism that aborts a **Run**, a **no-op** where the routed pair already equals
the rung, and per issue rather than per mode — a **Lane** and a serial **Iteration** read and feed
one ledger. It is configurable from the **Config** file only and on by default at
`claude-opus-5 @ max`, and an explicit model pin suppresses it exactly as it suppresses
**Routing**. It is blind to work that ran expensively and produced nothing usable, because
progress is commit-shaped and not quality-shaped.
_Avoid_: retry model, fallback pair (that is the **Default pair**), escalation ladder.

**Measured routing**:
The **Calibration**-authored precedence tier — one rung between global **Config** and the
built-in default, so it supplies a **Routed pair** only where the operator is silent and a
hand-written `[routing]` entry beats it forever, with no override flag and no special case. It
is a single committed artifact, `routing.measured.toml` beside the project `config.toml`,
carrying the table and its evidence in the same file and no free-text key for an opinion to
occupy. Only current state is stored, because git is the ledger: a change arrives as a
reviewable pull-request diff rather than a cache refresh, `git blame` names the **Calibration**
that set a **Task type**'s pair, and deleting the file is the entire opt-out. It is a committed tier rather than a
cache for that reason (ADR-0028), and like all **Routing** it takes effect in every mode, a
serial **Iteration** as much as a **Lane** (ADR-0037) — it was reported as inert at
`parallel == 1` until the pair a **Pickup** resolves became the pair its session runs on.
_Avoid_: auto-routing, learned routing, routing cache.

**Calibration**:
The measured search that fixes a **Task type**'s **Routed pair**, and the operator act that buys
it: one invocation is one Calibration under one `calibration_id` — the identity every **Trial**'s
record carries and the namespace its working branches are cut in — running one search per Task
type it was asked for. `git-loopy calibrate` measures every eligible one and `git-loopy calibrate
<task-type>` exactly one, and a Task type short of admitted **Proving tasks** is skipped with its
shortfall rather than measured. Each search walks a price-ordered candidate staircase from its
cheapest rung and stops at the first pair whose five **Trials** against the gate all come back
green — unanimity over a deliberately thin sample, never a reliability estimate — bounded by an
**AI Credits** ceiling and an elapsed wall-clock one, both applied per **Task type** so an
expensive walk cannot spend the credits the next one was to be measured with; a search that
exhausts either, or is interrupted, publishes no winner at all and keeps the incumbent — and
where that incumbent is a pair an earlier Calibration measured, the pair stays in the artifact,
because a walk that stopped disagreed with nothing about it and so records *around* it rather
than over it. Its objective
is inverted from the obvious reading — the gate is a bar, not a gradient, so the target is the
*cheapest* pair that reliably clears it, never the most capable one. Its output is a
measurement table and an argmax, with no written analysis and no prose conclusion anywhere in
it. Always an explicit operator act: it never starts itself, not on a first **Run**, not at
preflight, not when the roster moves (ADR-0027, ADR-0028).
_Avoid_: assessment, analysis, evaluation.

**Proving set**:
The frozen corpus of replayed closed issues a **Calibration** measures against, mined from this
repository's own history and stratified by **Task type** so a search can draw the tasks its own
task type is judged on. Mining reads tracker and git metadata only and yields *candidates*; a
candidate becomes a member solely by being **admitted** — replayed with its real historical fix
and shown to fail before it and pass after — so the mined count and the admitted count are
different numbers and only the second is measurable. Being frozen, it expires: it measures the
project you *were*, so a stronger AGENTS.md table propagates through a refresh policy and never
by editing the set (ADR-0027).
_Avoid_: benchmark, replay corpus (**replay** is taken by the event log), test set.

**Proving task**:
One admitted member of the **Proving set**: a closed issue pinned to the commit *before* its
fix, carrying that fix's own changed test paths as the oracle and the issue body as the work.
The pin is an issue number and two commits, which a **Trial** resolves against the admitted set
it was handed — so measuring reaches the tracker exactly never (ADR-0027).
_Avoid_: benchmark case, sample, fixture.

**Trial**:
One candidate pair working one **Proving task** in its own worktree at that task's base commit.
It is scored **fail-to-pass** on the task's oracle — the base commit's own feedback loops,
narrowed to the fix's test paths — with the whole AGENTS.md gate beside it as a **pass-to-pass**
regression guard, so a pair cannot satisfy its own tests while breaking a neighbouring suite. It
is then scored **lexicographically** on exactly three keys — cleared the gate, then **AI
Credits**, then wall clock — with deliberately no fourth *scoring key* for a judge or a weighted
composite to occupy, since those weights would come from the judgment the measurement replaced.
It *records* more than it is scored on: why a red Trial went red, and which gate and oracle
loops ran — detail nothing branches on, and what makes *the gate that ran was the one declared
at the base commit* checkable rather than promised. It is **not** an **Iteration**: it belongs
to a **Calibration** rather than a **Run**, ticks no **Strike**, earns no **Queue** entry, and
leaves neither worktree nor branch behind (ADR-0027).
_Avoid_: run, experiment, sample.

**Provisional**:
The **Measured routing** record state carrying a **Routed pair** that is *in force and was
never measured* (ADR-0030) — what **Demotion** installs when it steps up the price staircase
into a rung nobody trialled, cheapest-first having stopped at the first pass so every measured
rung sits *below* the winner and failed. It routes: the pair reaches the precedence chain
exactly as a measured one does, on the same rung rather than a new one, so a hand-written
`[routing]` entry beats it by the unchanged rule. What separates it from a measured row is the
status alone, so it is attributed to its own reporting tier — `provisional (unmeasured)` — and
carries the pair it replaced and a closed-vocabulary `reason`, with no rung and no **Proving
task** beside it at all: nothing trialled this pair, and evidence next to it would be the
replaced pair's, read as its own. **Demotion** is what writes one, at the end of a **Run**
(ADR-0030); such a record loads, routes, round-trips and is reported apart, and the **Wrapper
contract** lists it.
_Avoid_: fallback, temporary, pending, unverified (that is an **Observation** classification).

**Demotion**:
The Run-end replacement of a **Measured routing** entry whose **Routed pair** stopped making
progress on real work. Its signal is counted per **Routed pair** from the Run's finalized
**Lane contributions** — a contribution that reached a terminal disposition without publishing is
a **no-progress** one — and deliberately *not* from the **Strike** counter, which is a single
Run-scoped counter every **Lane** shares and any Lane's progress resets, so it can never carry a
per-pair meaning; the Strike counter's own job, ending a Run that is going nowhere, is unchanged
(ADR-0030). The threshold is **Config**, and it is an absolute bar rather than a comparison:
nothing is claimed about which pair would have done better, only that this one is failing. It is
evaluated and applied after the **Run** ends and never mid-Run, at the one quiescent point where
every Lane has finalized and nothing is in flight to race it over the single tracked file — which
is also how the **Checkpoint** interaction ADR-0028 left open was closed, by removal rather than
resolution — and it lands as one reviewable, revertible commit. A demoted **Task type** steps
*up* the price staircase, into the next rung — which nobody has measured, cheapest-first having
stopped at the first pass so every measured rung sits *below* the winner and failed — so the
replacement is recorded **Provisional** and never as measured, carrying the pair it replaced and
the count of no-progress contributions that moved it. It **notifies** that the Task type now needs
re-calibrating and **starts no search** itself, ADR-0028's notify-don't-act rule applying
unchanged and for the same reason: an implicit trigger would turn an unattended Run into a
benchmark suite. It reaches the measured tier only — a hand-written `[routing]` entry is **never
demoted**, however badly its pair performs, because it is the operator's decision and this system
does not overrule those — and being part of **Routing** the replacement it installs is in force
in every mode (ADR-0037), a serial **Iteration** as much as a **Lane**. A serial Run nonetheless
demotes nothing, for a narrower reason than the **Parallel mode** scope it used to inherit: a
**Lane contribution** is a Lane's record and serial opens none, so the tally is empty.
_Avoid_: rollback, regression.

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
after the reusable **Lane** moves on. It is the **accounting unit** of Parallel mode,
not the **Lane** slot: it owns its own boundary events, its own **Consumption** and
timing, its own **Summary** row, and its own durable record entry, so a refilled slot
never inherits or overwrites them. It is *not* an **Iteration** — there is no barrier
round to number — so it carries no Iteration number.
_Avoid_: parallel Iteration, round, Wave, session.

**Lane cap**:
The configured upper bound on concurrent **Lane** work. It is a safety and resource
ceiling, not a utilization promise: **Rolling dispatch** may deliberately leave capacity
idle when the eligible **Pool** is small or **Integration** applies backpressure.
_Avoid_: worker count, target concurrency.

**Effective Lane limit**:
The number of **Lanes** **Rolling dispatch** may actually fill right now. It starts at
a static-safe value below the **Lane cap**, contracts under sustained 429, AI-credit,
host/setup, or **Integration backlog** pressure, and expands one Lane at a time against
sustained evidence of health — never above the Lane cap, which never moves. It may reach
zero, at which point started work and Integration still drain; nothing is cancelled for
it. A pressure signal the Run cannot observe is shown unknown and never estimated, which
also means it can never be used to justify an expansion.
_Avoid_: dynamic cap, current cap, throttle, adjusted Lane cap.

**Pressure signal**:
One authoritative input to the **Effective Lane limit**: observed 429s, AI-credit burn
against an explicitly configured ceiling, host/setup load against a configured budget, or
the Run's own **Integration backlog**. Only the strongest active signal governs a
transition, and a signal the Run cannot see is _unknown_ — never zero, never estimated, and
never usable as evidence of health.
_Avoid_: metric, load factor, health score.

**Integration backlog**:
The bounded set of finished Lane branches admitted to **Integration** but not yet
published. Admission is FIFO and its high-water mark is two — one contribution
integrating plus one waiter; a third finisher parks. A full backlog applies
**Integration backpressure**, preventing unbounded branch staleness and wasted API
capacity.
_Avoid_: Queue (the per-Run issue ledger), merge queue.

**Integration backpressure**:
The rule that a full **Integration backlog** stops **Rolling dispatch** from starting new
Lane work. It is a refill bound, not a pause: Lanes already running finish normally, and
the moment a backlog slot frees, refill resumes. It is why the **Lane cap** is a ceiling
rather than a utilization promise.
_Avoid_: throttling, pausing, draining.

**Integration stage**:
The private worktree a **Lane contribution** is merged into and gated in before anything
reaches the base branch. Each contribution gets its own stage, and bounded
auto-resolution reuses the stage its contribution is already in. Because the stage is
private, a red or conflicting result is never observable on base and there is nothing to
undo.
_Avoid_: integration branch, staging area, merge queue entry.

**Integration**:
The serialized **Parallel mode** stage that consumes the **Integration backlog** one
contribution at a time. It merges each finished Lane branch into a private **Integration
stage**, re-runs the feedback loops *there*, and only then publishes the verified result
to the base branch and closes the issue — the issue is closed only after its contribution
is verifiably published green. A conflicting or loop-failing contribution triggers a
runner-driven auto-resolution attempt in that same stage; persistent failure falls back
to a serial **Iteration**. Runner-owned — it never waits on a human.
_Avoid_: merge (as the name for this step), landing.

**Parallel-safe**:
A `ready-for-agent` issue a human has additionally asserted is independent and
well-scoped enough to be worked in its own **Lane**, concurrently with others.
Carried as a triage label alongside `ready-for-agent`; the runner never infers it.
_Avoid_: independent, parallelizable (as the label name).

**Serial-required**:
Eligible `ready-for-agent` work that is not **Parallel-safe** and therefore can only be
worked as a serial **Iteration** — the unlabelled issues, pull requests, and
local-markdown items a **Parallel mode** Run must still drain. It is invisible to
**Rolling dispatch**, whose **Pool** membership cache only ever surfaces Parallel-safe
candidates, so the runner discovers it by its own reading of the Pool. Finding any
latches serial demand: refill stops, started Lane work drains, and one unchanged serial
Iteration is granted exclusive use of the base worktree before **Rolling dispatch** gets
one full refill turn back.
_Avoid_: plain work, non-parallel work, leftover.

**Serial fallback**:
A serial **Iteration** a **Parallel mode** Run works because **Rolling dispatch** found
no eligible **Parallel-safe** candidate. Because eligibility is a human assertion, the
usual cause is that nothing carries the label — indistinguishable, from the operator's
seat, from the flag being broken. So every fallback is named to the operator and carried
as a `wrapper.parallel.serial_fallback` **Event** with the eligible count and a reason
that separates "nothing carries `parallel-safe`", "this Run already worked them all",
and "the ones there are could not be read". A serial Iteration running *alongside*
remaining eligible Lane work is interleaving, not a fallback.
_Avoid_: degraded mode, serial mode.

**Wave** _(historical)_:
The retired barrier round of the original **Parallel mode** design (ADR-0008): a fixed
cohort of **Lanes** dispatched together, every Lane waiting at the barrier before
**Integration** ran and the next cohort started.
[ADR-0020](docs/adr/0020-rolling-dispatch-with-bounded-green-integration.md) replaced it
with **Rolling dispatch**, which has no round at all. The term is retained *only* as
historical vocabulary — for reading ADR-0008 and ADR-0009, for the comments and live-state
identifiers that still describe per-Wave reset semantics, and for replay logs that carry
`lane_issue` and no contribution identity. Those legacy traces stay readable and are never
reinterpreted as **Lane contributions**. Nothing new is described as a Wave: the unit of
Parallel work is the **Lane contribution**, the concurrency bound is the **Lane cap**, and
the ordering constraint is the **Integration backlog**.
_Avoid_: using it for anything current — say **Lane contribution**, **Lane cap**, or
**Rolling dispatch** instead.

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
- A **Contract-carrying Skill** reaches a **Run** only through the **Installed catalog**,
  so it is authored in the external Skill catalog and mirrored into this repository,
  never the other way round.
- A **Run** resolves one **Effective Skill policy** before work begins. Every
  **Required Skill** must belong to it, and every serial **Iteration** and parallel
  **Lane** shares it.
- A **Run** has many serial **Iterations** and/or parallel **Lane contributions**.
- An **Iteration** is offered one **Pool** and produces at most one **Active issue**.
- A **Queue** belongs to exactly one **Run** and aggregates every issue seen across
  its serial **Iterations** and parallel **Lane contributions**, keyed by issue.
- An **Active issue** is the **Pool** member bound by the Orchestrator's authoritative
  activation record.
- A serial **Iteration** binds to at most one **Active issue**, at **Pickup**, before its
  agent session starts — as a parallel **Lane** does. The **Working marker**, closure,
  commit and single-member-**Pool** paths that once bound it are retained only to replay
  streams recorded before that reversal (ADR-0032).
- The **Dashboard** shows the **Queue**; selecting a row opens that issue's **Log**.
  Each issue has its own **Log**, which accumulates across every serial **Iteration**
  and parallel **Lane contribution** that worked it.
- The **Activity** band shows one **Activity window** per live **Agent**; each window
  renders the same lines as that Agent's issue **Log**, so the band is a live view of
  existing per-issue record rather than a record of its own.
- A serial **Iteration**, a **Lane**, and an **Integration** auto-resolution attempt each
  run exactly one **Agent**. An **Agent** may spawn many **Subagents**, whose
  **Consumption** its own telemetry already carries.
- A **Checkpoint** is authored by the runner (not the agent) at a serial
  **Iteration** or Lane-work boundary and is attributed to the **Active issue**, but
  never counts as **Strike** progress.
- **Consumption** is attributed to a scope: a serial **Iteration** or parallel
  **Lane contribution** (a **Summary** row's Cost), or an **Active issue** (the
  **Queue**'s per-issue Cost). Both resolve Cost through the same **Cost denomination**,
  so per-issue and accounting-row figures stay reconcilable.
- Cost is the **AI Credits** the harness reported billing, never a figure git-loopy
  recomputed from tokens and prices. The **Rate card** records the prices a **Run** was
  billed under and denominates nothing, so a Run without one reports Cost exactly as a
  Run with one does.
- A context cutover starts another **Iteration** pinned to the same **Active issue**;
  it does not create a sub-Iteration accounting entity.
- An issue's **Iteration breakdown** has one row per Iteration contribution; the
  Queue's Iteration count is the number of those rows.
- In **Parallel mode**, **Rolling dispatch** reuses **Lanes** continuously rather than
  grouping them into barrier-synchronized rounds.
- A **Lane contribution** belongs to one **Active issue** and may outlive the reusable
  **Lane** that began it while it parks, integrates, or recovers.
- The **Lane cap** is an upper bound; **Integration backpressure** may intentionally
  leave Lane capacity idle.
- A **Lane** works exactly one **Parallel-safe** issue at a time. Once its finished
  branch enters the bounded **Integration backlog**, the Lane can take another issue —
  unless that admission filled the backlog, in which case refill waits.
- **Integration** consumes that backlog serially, verifies each contribution in its own
  private **Integration stage**, then publishes it to base and closes the issue, so the
  **Queue** reaches **closed** the same way it does in serial mode.
- An **Integration** auto-resolution attempt occupies no **Lane**: its contribution
  released its Lane at admission, so that slot refills while recovery is still running.
  Recovery costs **Integration backlog** capacity, never **Lane cap** capacity.
- A contribution that never goes green is never published, so base only ever carries
  verified results and the base branch is never observed red.

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
  resolved: the issue a unit of work is bound to is the **Active issue**, bound by the
  runner at **Pickup** and confirmed by the agent's **Working marker**.
- `log` vs `transcript` were both used for the live per-issue output (the code's
  drill-in called it a "transcript"; the early UI also had a whole-run "Log" tab) —
  resolved: the single per-issue, timestamped, auto-scrolling record is the **Log**;
  "transcript" and the whole-run Log tab are retired.
- `commit` was ambiguous once the runner began authoring commits — resolved: an
  agent-authored commit is a plain commit and counts as progress; a runner-authored
  one is a **Checkpoint** and does not.
- `wave` vs `iteration` — resolved historically as parallel and serial round units, then
  superseded for Parallel mode by
  [ADR-0020](docs/adr/0020-rolling-dispatch-with-bounded-green-integration.md):
  **Rolling dispatch** has no barrier round. An
  **Iteration** remains the serial session/accounting unit; **Lanes** are reusable and
  **Integration** is a separate serialized stage. **Wave** is retained only as historical
  vocabulary and now has its own glossary entry saying so.
- `ralph` / `ralph-afk` / `copiloop` / `git-loopy` were used interchangeably for the tool —
  resolved: **git-loopy** is the framework, CLI, and brand (`git-loopy` as the distribution and
  console command, `git_loopy` as the importable Python package); the technique it orchestrates
  is the **Autonomous loop**. Two product brands are retired: **ralph-afk**
  (every `ralph` / `ralph_afk` identifier, the `ralph/` and `.ralph/` directories, and the
  `RALPH_*` env vars — ADR-0005) and **copiloop** (every `copiloop` / `Copiloop` identifier, the
  `copiloop/` and `.copiloop/` directories, the `copiloop.*` spans, and the `COPILOOP_*` env
  vars — ADR-0012), both in favour of `git-loopy`, `git_loopy`, `.git-loopy/`, and `GIT_LOOPY_*`.
  ADR-0005 had retained "Ralph loop" as the name of the *technique*;
  [ADR-0031](docs/adr/0031-encoded-workflows-retire-the-loop-name.md) retires that last use too, so `ralph`
  now survives only in the point-in-time records that narrate the renames.
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
- `model` was used for both the run-wide configured choice and the choice one unit of
  work actually runs on, which diverge once **Routing** is configured — resolved: the
  run-wide setting remains the default, and the per-unit resolved-and-gated choice is
  the **Routed pair**, fixed at **Pickup**. The billed model reported in **Consumption**
  is a third, observed thing and is neither.
- `task type` was read as implying work order — resolved: a **Task type** selects a
  **Routed pair** only. Scheduling priority is a separate axis, now modelled as
  **Priority** and carried on its own label (ADR-0032); it must not be folded into the
  `task-type:` label vocabulary, and selection never reads a task type.
- `self-selected` was stated of the **Active issue** and `a serial Iteration has no
  pickup` of **Pickup** — resolved: both are reversed (ADR-0032). The runner picks the
  issue, in serial **Iterations** as much as in **Lanes**, so every unit of work has a
  **Pickup** and the **Working marker** confirms a binding it no longer creates.
- `read, never inferred` and `an open, operator-extensible taxonomy` were both stated of
  **Task type** — resolved: **both are reversed, and they reverse in opposite directions**
  (ADR-0029). Inference is now permitted, because a **Proving set** cannot be stratified
  without it and no `task-type:` label exists on any of the 334 closed issues; the taxonomy is
  now *closed*, because an unattended classifier that may invent keys writes them permanently
  into the tracker and silently corrupts those same strata. The invariant that survives is
  narrower and is the one that was actually load-bearing: **the label is authoritative, and
  the runner routes on the label rather than on content.** Inference happens once, before the
  label exists; routing never re-reads the body.
- `cost` was rendered in dollars, from a hand-authored list-price table git-loopy
  maintained itself, and was described as derived from **Consumption** by multiplying
  tokens by a per-model price — resolved: Cost is the harness's own reported billing,
  denominated in **AI Credits**, and no USD figure is published, because no surface the
  kit reads is denominated in dollars by its schema (ADR-0026, superseding one clause of
  ADR-0018). The table, the operator-supplied conversion rate and the estimate are
  deleted; the live **Rate card** replaces none of them, being provenance rather than
  arithmetic. An operator-supplied rate, an offline price fallback and any recompute of a
  figure the harness already billed all stay rejected.
- `assessment` / `analysis` / `conclusions` were used for the act of choosing which model
  each **Task type** should route to, implying a considered review of the project that
  produces a written judgment — resolved: it is a measured search, not a review, and the
  canonical term is **Calibration** (ADR-0027). It walks a price-ordered candidate list and
  stops at the cheapest **Routed pair** that clears the AGENTS.md gate on every
  **Proving task**, so its output is a measurement table and an argmax with **no written
  analysis and no prose conclusions at all** — a free-text rationale field is the
  inferential judgment the measurement replaced, returning by the back door (ADR-0028). The
  objective is inverted from the obvious reading: the gate is a bar rather than a gradient,
  so the target is the *cheapest* pair that reliably clears it, never the most capable one.
  A **Trial** is one candidate pair working one Proving task and is deliberately **not** an
  **Iteration** — it is attributed to a Calibration rather than a **Run**, and never ticks
  a **Strike**. All five terms those two decisions fixed have now shipped and hold their own
  **Language** entries: **Calibration**, **Proving set** (with **Proving task** beside it),
  **Trial**, **Measured routing** and — last to arrive, with
  [ADR-0030](docs/adr/0030-demotion-is-measured-per-pair.md) settling it — **Demotion**, so this
  resolution now *is* the glossary and promises no further term. One distinction the entries rest
  on is kept here, because it is a trap rather than a definition: the state Demotion writes is
  `provisional`, not `demoted`. ADR-0030 needed a *fourth* status precisely because the older
  `demoted` one **clears the pair** and falls through to the hand-authored bootstrap, an option it
  lists and rejects by name; `demoted` is therefore **superseded and has no writer**, its
  `demoted_after_strikes` field set encoding the consecutive-**Strike** rule ADR-0030 shows cannot
  be read per-pair at all. Both terms were admitted to Language by the same test — being
  **reachable**, so that a reader can invoke what the entry describes — which `provisional` met
  first, as a record that loads, routes, round-trips and is reported apart, and which Demotion met
  when its mechanism shipped. One further consequence outlives these entries: the **Proving
  set**'s refresh policy is stated — re-base when the pinned **Task-type classifier** pair moves,
  or on an interval, whichever comes first — and nothing refreshes it yet, so a set left alone
  silently measures the project you used to be.
