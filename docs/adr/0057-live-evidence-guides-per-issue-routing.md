# Live evidence guides per-issue routing under explicit operator authority

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided in a human-led `/grill-with-docs` session under the model-routing map
[#280](https://github.com/bradcstevens/git-loopy/issues/280). This records the new
policy, not a claim that the current Runner or Wrapper contract implements it.

Where the operator has not selected a **Static route**, **Dynamic routing** will
forecast the shortest time to an acceptance-passing issue result using current
Copilot eligibility, published model evidence, and bounded issue-specific context.
That objective includes routing overhead, tool and test time, and likely retries,
not merely response latency. It deliberately admits an evidence-grounded model
judgment where ADR-0027 required an experiment, while refusing to call that judgment
a measurement or a guarantee of the best possible outcome.

## Authority and evidence

Dynamic routing is the default for unpinned work. **Calibration** remains an explicit,
optional source of local evidence, not a mandatory winning route or an automatically
triggered benchmark suite. Its results do not become more authoritative merely
because they are committed, and a forecast must never be recorded as a Calibration
result. Human-owned Config is not rewritten by routing.

The **Route selector** is elected deterministically: the highest Artificial Analysis
Intelligence Index among verified, currently eligible, runnable model/effort
configurations, using the effort that earned the matched score. Its context tier is
the smallest supported tier that fits its bounded input. Exact score ties use
comparable measured speed, then a stable identifier. No model chooses the selector
recursively, and a spend limit does not authorize substituting a cheaper selector.

Artificial Analysis is the required source for that election. Comparable,
same-harness SWE-bench Verified results are optional supporting evidence for the
work model; additional sources require vetted adapters. Benchmark, model revision,
reasoning configuration, harness, methodology version, and test conditions remain
attached to evidence. Unrelated scales are not averaged, different harness versions
are not treated as comparable without grounds, and mirrors are not independent votes.

Copilot eligibility comes from the authenticated harness the Runner actually uses,
including its capability and policy constraints, not from a public plan comparison
or a hardcoded model roster. A listing is not a promise that a later session will
succeed. Model identities and scored configurations must be verifiably associated;
similar names, unknown scores, and a model's training-time recollection are not a
mapping. Unevidenced or unverified models are excluded from automatic selection,
not scored as zero. An explicit static choice need not have a leaderboard match.
An empty verified intersection makes dynamic routing unavailable.

The selector receives the issue and acceptance criteria, its **Task type**,
repository instructions, bounded targeted read-only context, and available local
measurements. It neither implements the issue nor runs Trials or a whole-repository
audit. Repository content is not sent to leaderboard services; those services receive
model-data requests only. The seven-type **Task-type classifier** remains separate:
it supplies a missing classification during eligible proposal preparation, before
deciding whether a static route makes a selector call unnecessary.

## Proposal, binding, and reuse

A running Runner prepares a nonbinding **Routing proposal** when it discovers an
eligible Pool candidate. Blocked, unreadable, or otherwise ineligible candidates
do not buy selector calls. Preparation prioritizes the next **Pickup** and bounds
concurrency; a proposal is not a **Lease**, and shallow membership alone does not
authorize execution.

Every new proposal and Pickup checks the latest published evidence and current
Copilot eligibility. Concurrent checks may share an in-flight request; conditional
revalidation is usable only where a provider supports it. A changed relevant input
requires selection again. At Pickup the resulting **Routing resolution** becomes
binding, and model, effort, and tier remain fixed for the **Agent**'s lifetime.

A subsequent Run may reuse a local result only after fresh checks and matching
relevant issue/context, policy, capability, evidence, and attempt-history inputs.
It records the new validation and the original decision's provenance. Reusable
state is derived from canonical event history, not from a tracker label, a new
committed table, or a second authoritative store. Routing's own comments and labels
are projections, not changes to the decision's task inputs.

When the existing **Attempt lifecycle** permits another dynamic attempt, its next
Pickup reselects with current evidence and the previous outcome. Infrastructure
failure is not automatically evidence of insufficient model capability. Attempt
and **Strike** limits stay in force; routing does not manufacture extra attempts.
Dynamic retries have no fixed **Escalation rung**, so a first attempt may use `max`
without reserving it for a later, possibly obsolete model.

## Static choices and supported settings

A Static route is an atomic model/effort/context choice for a Task type and bypasses
the selector. Existing model/effort entries remain static and inherit the run-level
context tier. Partial per-task-type dynamic constraints are out of scope.

Explicit run-wide model or effort flags/environment overrides continue to suppress
dynamic routing. A context-only override fixes the work tier while permitting
dynamic model/effort selection, preserving the existing run-wide distinction.
Static routes remain fixed across retries unless the operator explicitly configures
escalation; an inherited built-in rung is not explicit authorization.

The context choice is a supported `default` or `long_context` tier, not an arbitrary
token-window allocation. Working-budget and compaction policy remain unchanged, and
actual capacity remains a live harness observation. A model without an effort dial
is allowed when otherwise qualified: its effort is explicitly **not configurable**,
which is different from the actual effort value `none`, and no effort argument is sent.

Unsupported static settings are refused with an actionable error. Invalid selector
output cannot start work and may only be retried within routing limits. The new
contract does not drop an effort, downgrade a tier, or silently replace a selected
configuration with a backend default. Dynamic defaults contain no historical
model-name exclusions or vendor preferences; the existing recommended static
recipe remains available without being retuned by this decision.

## Authorization and failure

Dynamic setup requires an operator-owned Artificial Analysis API key, held outside
versioned Config, and explicit finite values for an assessment deadline, a per-Run
routing-credit allowance, and selector concurrency. Classification and selector
retries count toward routing usage and reported **Consumption**. No further routing
calls are admitted after allowance exhaustion; in-flight, post-paid billing can
overshoot and must be disclosed rather than described as a hard prepaid ceiling.

Upgrade requires a keep-or-migrate decision, not a guess that a saved recommended
value is disposable. Interactive setup/update presents that choice; unattended use
must supply or inherit an explicitly recorded choice before agent work. Existing
Config is untouched until then. The prompt must disclose strict route validation
and the removal of implicit static escalation. New installations do not seed static
rows that would accidentally suppress the dynamic default.

If fresh trustworthy evidence, eligibility, or a valid selector result cannot be
obtained, new dynamic work does not start. Running Agents may finish and eligible
static work may proceed; if nothing can advance, the Run stops with the actual
reason. There is no silent stale-data or hardcoded-model fallback. Routing
unavailability is not an empty Pool, a native dependency becoming **Blocked**, or
permission to spend another task attempt.

## One record, several projections

The existing Routing resolution remains the final record. Canonical local Run
events retain proposal, validation, supersession, and binding provenance: chosen
settings, selector settings, relevant input identity, eligible candidates, evidence
references and timestamps, rejected alternatives, and a concise decision summary.
The CLI and Dashboard read those records instead of recomputing explanations.
Missing measurement dates or versions remain unknown; retrieval time is not silently
substituted for them.

Each materially changed final assignment also gets an idempotent, append-only issue
comment and one observational **Route label** containing model, effort, and tier.
Unchanged revalidation does not repeat the comment; provisional proposals are not
published. A compact label must be deterministic and unambiguous, with exact values
retained in the comment and canonical event. Rerouting changes an issue's label
associations, never renames a shared repository-wide label or replaces unrelated
labels. Neither the label nor the comment becomes routing authority.

Local durability is mandatory before work. Tracker publication is non-blocking:
failures are visible, pending delivery survives, and retries are bounded and
idempotent. A failure is never reported as successful publication. Published summaries
are issue-safe explanations with evidence links, not raw prompts, private repository
excerpts, credentials, or hidden reasoning.

## Evidence limits and delivery boundary

The [Artificial Analysis API reference](https://artificialanalysis.ai/api-reference)
documents a keyed free endpoint limited to 1,000 requests per day, and exposes
Intelligence Index, time-to-first-token, and output-throughput metrics. Those
inference metrics are not elapsed completion times for a git-loopy issue through
Copilot. Its [methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking)
also cautions that a general Intelligence Index need not transfer directly to every
use case. A fresh request means the latest evidence the source publishes, not a new
benchmark measurement or knowledge of an undisclosed backend change.

The named [Hugging Face Space's source](https://huggingface.co/spaces/ArtificialAnalysis/LLM-Performance-Leaderboard/blob/main/index.html)
embeds the Artificial Analysis leaderboard. [SWE-bench Verified's documentation](https://www.swebench.com/verified.html)
distinguishes arbitrary coding systems from same-harness model comparisons and
warns about comparability across harness versions. These are reasons to preserve
provenance and uncertainty, not to invent a composite certainty score.

The [public Python SDK's model-listing implementation](https://github.com/github/copilot-sdk/blob/main/python/copilot/client.py)
caches a successful `list_models()` response until disconnect. Implementation must
verify a genuinely refreshed, supported path against the pinned harness; repeated
calls to a memoized method do not satisfy this decision. Fresh eligibility must not
rewrite the Run's already-recorded billing provenance.

First delivery covers Python's serial and parallel issue-owning Agents and the Rust
Dashboard. The Wrapper contract and Conformance fixtures must describe the new
behavior when it lands, with shell and PowerShell support explicitly deferred, not
silently claimed. Subagents and Integration Agents retain their existing settings.
This documentation change does not change runtime code, fixture semantics, or the
contract version.

The accepted policy changes the relevant portions of ADR-0017/0019 (tier scope and
gate behavior), ADR-0027/0028 (routing authority, objective, inference and evidence
storage), ADR-0029 (classifier timing), and ADR-0035/0048 (effort-dial eligibility,
historical table constraints and the reserved rung). The **Default pair** of ADR-0056
does not become a fallback for a failed dynamic decision. Historical rationale,
Calibration's experimental meaning, and the optional recommended static values are
preserved rather than rewritten as though this had always been the design.

The map remains open for [#293](https://github.com/bradcstevens/git-loopy/issues/293)
(blast-radius guards) and [#295](https://github.com/bradcstevens/git-loopy/issues/295)
(intra-session Subagent routing). Excluding them from this delivery does not answer
their separate questions.

## Partial activation status (#567)

Python's opt-in `update --routing` now collects or reuses an explicit keep-or-migrate
choice, retains every authored Static row, requires operator-owned Dynamic access
outside Config and explicit finite limits, and uses the shared Run/doctor readiness
verdict before saving. Unattended use of this option with no recorded or supplied
choice refuses without prompting. Bare update does not choose a policy.
Both choices disclose strict validation, inherited-tier
semantics and the end of implicit Static escalation.

Local Python Runs now refuse saved Config without an effective Static/Dynamic choice,
using one no-I/O authority verdict at CLI startup and shared Run/doctor
preflight. The CLI refuses before Skill migration, listing or detachment, and
rechecks authority if Skill migration reloads Config. Run-local Config presence
survives detached transport; it is not a new persisted Config key or Event field.
Refusal starts no Agent and writes no Config. Explicit policy flags/environment
may supply authority temporarily; a recorded project or inherited global choice
also suffices. Model/effort overrides alone do not supply migration consent.
Offline serial/Lane recovery cases exercise both choices into actual work
settings and canonical Pickup, including Static work without leaderboard access
and temporary authority expiring at the next invocation.
These cases now consume `routing-resolution.json`'s shared `migration_recovery`
inputs through the real CLI, including retained Static pairs and inherited tiers,
run-wide model/effort pins, context-only controls, and required access/evidence
lost after migration. They assert actual sessions, canonical Pickup and Dashboard
readback, or no work, final publication or Strike on refusal. The Wrapper declares
the same staged local guard and member deferrals; this is not final activation.
This remains staged activation: empty Config scopes keep the no-Config path.
Bare setup may save an unselected policy, but the following Run then refuses
until authority is supplied; `init --routing` authorizes before saving.
Non-local activation is explicitly deferred: applying this guard to the shipped
GitHub Actions host would leave saved Config with no runnable policy, because
its capabilities cannot be validated from this machine. Unselected remote Runs
retain their legacy path; selected policies still refuse rather than substituting
local eligibility. `--route-policy unselected` can explicitly retain that remote
legacy path even when local Config records a policy, without rewriting Config.
This compatibility boundary is not completed migration or remote routing support.
Wrapper contract 14.3 and the Conformance routing-resolution notes now state this
Python-local guard and explicitly defer shell/PowerShell enforcement. Existing
one-route cases and historical streams are unchanged. The fixture's
`migration_recovery` matrix drives temporary flag/environment and saved
project/inherited-global choices through the real CLI into serial and Lane
sessions, checking actual settings, canonical Pickup, selector usage, unchanged
Config and authority lifetime against synthetic configurations. This reconciles
the staged guard's contract with its existing implementation, not final activation.

Upgrade now requires a supplied or recorded machine-global choice before
distribution handoff, prompting only on an interactive terminal. It shares the
same consent collector as init/update without writing Config or buying an
assessment. The installed Runner chains `update --routing` to check readiness
before saving; recorded authority is re-read instead of replayed over a later
operator edit. A same-Release target skips reinstalling, not the choice or update.
Readiness failure preserves Config but does not roll back an installed
distribution. A Release-retired routing key likewise refuses without implicit
repair or asset refresh: the operator runs the newly installed Release's
`update --global` repair, then retries `update --routing keep` or
`update --routing migrate`. This keeps retired-key repair separate from consent
to preserve authored routes. An older target without the routing-aware update refuses rather
than silently falling back. Project scope is never inferred or written.
Composed serial/Lane cases carry this saved global choice into actual work
settings and canonical Pickup records, including retained Static routes, Keep
without leaderboard access and fresh source outages after setup.

Under Dynamic policy, Calibration remains evidence rather than an implicit Static
pin. Its artifact is preserved; legacy/unselected and Static policies retain the
Measured routing tier. Config readback reports uncovered Dynamic work as pending
Pickup rather than inventing a final pair.

Opt-in `init --routing` now shares that authorization and readiness seam before
saving any operator choices. Its fullscreen review hands off to explicit
terminal authorization questions; missing limits have no defaults and missing
associations must be authored, not inferred. No Static rows are seeded by
default; the recommended Static recipe remains an explicit option. Unattended
routing setup preserves saved/inherited model, effort, prompt and Skill policy and never treats
`--yes` as consent. Cancellation, readiness failure or detected edits to its
inputs abort before scope writes. The operator-owned key stays outside Config.
The shared `routing-resolution.json` `first_setup` matrix now starts with neither
Config scope present and drives the real guided `init --routing` CLI and Textual
keyboard walk into unattended serial and Lane Runs for both project and global setup. It covers
explicit Dynamic and Static setup plus refusal and recovery for missing access,
invalid deadline/allowance/concurrency, an empty verified intersection and
unavailable evidence/capabilities. Refusal leaves operator choices and tracker
labels unwritten; after repair, actual work settings, canonical Pickup,
Dashboard and final tracker comments agree. The Run preserves Config, Static
setup requires no leaderboard access or Dynamic limits, and no Static rows are
seeded. Wrapper 14.3 declares this opt-in first-setup obligation and explicitly
defers native-member activation; bare init and auto-setup routing defaults remain unchanged.
This composed walk exposed a setup blocker hidden by prebuilt wizard answers:
Skill discovery tried to run its async lifecycle on Textual's already-running
event loop. The synchronous discovery/rebuild callback now uses a dedicated,
joined worker, retaining the same Skill policy validation and error propagation
before any scope write. The real wizard's default route choice, rather than a
test-supplied empty routing table, is what the matrix carries through Save.

Bare init now enters the same routing-aware path when the chosen scope records
or inherits an explicit Static/Dynamic choice. Previously it could save an
unready Dynamic scope and overwrite its model, prompt and Skill policy with
unattended defaults simply because `--routing` was omitted. Recorded authority
now uses the same consent reader and readiness verdict, not a second policy
resolver. The fullscreen wizard follows the chosen scope's authority and does
not seed unvisited Static rows; changing from a legacy custom walk retains
explicit choices rather than treating its unvisited defaults as authored pins.
Additive route collection stays additive when switching back to an unselected
scope, without supplying migration consent. Bare init and `--routing ask` capture
the Config inputs that establish authority or its absence before collection,
then refuse byte changes before authorization and saving; concurrent operator
edits are preserved. Invalid chosen-scope authority is reported with
the existing no-save and prerequisite-residue diagnostic.
Serial/Lane cases carry bare setup into actual work settings and canonical
Pickup, including inherited authority and Static work without leaderboard
access. This closes a saved-choice bypass; it does not choose a policy for
unselected setup or activate final Dynamic defaults.

Missing or invalid Dynamic prerequisites at Run preflight now leave retained Static work
usable rather than refusing the whole Run. The shared verdict still validates
Static settings and makes setup, migration and doctor refuse an unready Dynamic
choice. Classification keeps its existing order: explicit routing limits can
authorize it to discover a retained Static route without leaderboard access,
and its usage still consumes the same Run allowance. Missing, invalid or exhausted limits
admit no classification. In serial and Lane Pickups, work still uncovered after
classification is refused before a Bump-class or fallback work session; later
eligible Static work can proceed. Saved init/update/upgrade cases remove access
after authorization and observe actual Static session settings, canonical Pickup,
unchanged Config, and no leaderboard or selector call. Mixed-Pool cases preserve
the non-empty-work refusal and charge no Strike for unstarted Dynamic work.
Missing prerequisites require a new Run after repair; a live-source outage with
authorized prerequisites retains its existing fresh-Pickup recovery path.
Wrapper contract 14.4 now states this affected-work refusal instead of incorrectly
requiring every missing Dynamic prerequisite to stop eligible Static work.
The routing deadline begins before the first live routing read, including a
listing shared with retained Static validation. The fixture's `preflight_deadline`
cases drive the real CLI into serial and Lane work with a validation that consumes
the whole deadline: no classifier or selector starts, no unstarted work charges
a Strike, and already-classified Static work retains its exact session settings.
The missing-access case proves classification cannot receive a fresh deadline
merely because the Route selector is unavailable.

The saved first-setup and migration choices are composed through actual serial
and Lane sessions and canonical Pickup records, including fresh source outages
after setup. The composed matrix also carries both saved entrypoints through
outcome-aware permitted retries, attempt and allowance exhaustion, fresh
cross-Run reuse, and pending publication recovery with unchanged Config and
idempotent tracker effects. Startup readback now distinguishes uncovered Dynamic
work and permitted reselection from a Static default or fixed escalation rung.
Retained Static routes and explicit escalation are echoed without legacy roster
downgrades under either selected policy; historical readbacks are preserved.
The saved-choice matrix now also covers run-wide model/effort authority without
leaderboard access and context-only controls in both modes. Composition exposed
that the work-tier override was missing from routing requests and candidate
selection. It now survives detached startup, filters only work candidates while
preserving the strongest selector's independent input-fit tier, and participates
in fresh proposal/Pickup validation and cross-Run reuse. Doctor uses the same
work-candidate readiness rule. Withdrawal of the requested tier refuses new work;
changed evidence or eligibility requires reassessment within existing limits.
Unselected bare init and auto-setup retain their staged behavior.
The saved-setup Consumption cases now cross the real classifier and selector
session adapters into SDK billing observations in both serial and Lane modes.
They exposed Run-only billing being attributed to an open serial Iteration and
lost from CLI totals once kept out of that row. Run-only Consumption now retains
its existing canonical scope and is shown separately in CLI and Dashboard
Summary readback, including refusals that bought classification but no work.
Lane Summary billing reads the canonical issue Consumption instead of losing
the bill behind the historical `cost_usd` placeholder. These cases also cover
classification exhausting the allowance, the strongest selector remaining
unchanged with a smaller allowance remaining, and post-paid overshoot while
already-authorized work finishes.
The concurrent saved-setup matrix now covers both classifier and selector bills
while their sessions remain open, at exact exhaustion and with post-paid
overshoot. It exposed admission counting only completed calls: another
assessment could start after the SDK had already reported exhaustion. Each
observed routing bill now reaches the shared admission ledger immediately;
completion or cancellation settles only an unreported remainder. Canonical
preparation records, CLI totals and Dashboard Run-only Consumption retain the
same bill exactly once. Both modes keep already admitted work running with its
bound settings, preserve Config, and refuse further assessment without a Strike
or a cheaper selector.
Cancellation cleanup can report additional billing, including a new overshoot;
it remains visible exactly once before the cancelled preparation is recorded.
Malformed billing cannot turn cancellation into a normal assessment refusal.
Valid observations already charged remain charged; invalid settlement preserves
cancellation and its validation error rather than inventing a replacement bill.
The saved-setup matrix also exposed late assessment results being accepted
after the authorized deadline. Shared settlement now retains Consumption but
refuses the result before it can publish a Task-type label or become a Routing
proposal. Already-bound Agents still finish on their frozen settings.

Non-local activation, the remaining composed acceptance and Wrapper/Conformance activation
obligations still precede final default
activation. Dynamic routing remains opt-in; shell/PowerShell activation is
deferred, and no Subagent or Integration routing support is implied.

The shared `publication_recovery` matrix now carries recorded init/update
authorization through repeated real CLI Runs in serial and Lane modes. Eight cases
cover fresh reuse, permission/rate-limit/transient failures, partial delivery,
exhausted retries and capability withdrawal requiring a changed assignment.
The composed seam exposed a publication-budget bypass: restart retries excluded
terminal delivery, but a later Pickup of the same assignment tried again.
The shared delivery path now respects that terminal state before any tracker I/O,
retains its original failure, and leaves locally recorded work unblocked. Restored
access does not silently renew an exhausted budget; a changed final assignment
gets its own delivery. Actual sessions, canonical Pickup and Dashboard route
readback agree, with original reuse provenance, unchanged Config and idempotent
tracker effects. This closes that bounded-publication gap, not final activation.
If replacement exhausts while an old association remains, or after its removal
but before the replacement add succeeds, the tracker can remain stale or missing.
That partial projection has no authority: local failure stays visible and does
not authorize unbounded repair calls. The matrix covers both outcomes.
