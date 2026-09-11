# Live evidence guides per-issue routing under explicit operator authority

**Status:** accepted design; not yet implemented.

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
