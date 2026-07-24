# The Continuation Contract

> The separately versioned, language-neutral contract for Workflow Continuation across the
> git-loopy Runner family. Domain terms are defined in [`CONTEXT.md`](../CONTEXT.md).

**Continuation contract version:** 1.0

**Record format:** 1

The Continuation contract is independent of Wrapper contract 1.3, Event schema 1.1, and the
Conformance fixture schema. A change to one version does not imply a change to another.

## 1. Scope

This contract governs shared completion requests, durable Producer revisions, Dispatch evidence,
Reconciliation, native command framing, capability declarations, and canonical machine or human
results. It does not authorize a Performer, execute a Continuation action, or replace the Wrapper
contract's Run lifecycle.

No Continuation operation may establish a central continuation issue, authoritative Markdown
snapshot, mutable project queue, append-only execution journal, central tombstone ledger, or
authoritative local cache.

## 2. Native command namespace

Every supported distribution exposes:

```text
git-loopy continuation capabilities
git-loopy continuation publish [--input FILE]
git-loopy continuation reconcile [--input FILE] [--terminal]
git-loopy continuation record-dispatch-result [--input FILE]
git-loopy continuation repair-index [--input FILE]
```

The command surface never performs a Continuation action. `publish` records a Transition owner's
typed revision, `reconcile` derives current guidance, `record-dispatch-result` records only the
contract's exceptional Dispatch-evidence classes, and `repair-index` repairs discovery metadata.
Their semantic request and result records land in later capability-gated revisions.

## 3. Framing and exits

- `capabilities` accepts no request and emits one machine JSON object.
- Other operations accept exactly one UTF-8 JSON object from stdin by default or from `--input
  FILE`.
- Machine responses emit exactly one JSON object on stdout. Diagnostics use stderr.
- Human terminal rendering is selected explicitly with `reconcile --terminal`; it is never mixed
  with machine JSON.
- Success and committed or idempotent receipts exit `0`.
- Semantic or operational rejection exits `1`.
- Malformed command invocation exits `2`.
- Unsupported operations remain present but fail closed with a typed `unsupported_operation`
  rejection and exit `1`.

## 4. Continuation capability manifest

`capabilities` returns the distribution's supported Continuation-contract versions, record
formats, Wrapper-contract and Event-schema versions, tracker Adapters and their operations,
operation support, Instruction handlers and modes, evaluators, effect scopes, optional
capabilities, and Continuation modes.

The manifest describes capability only. It is not Automation scope, Performer posture, a feature
flag, or authority to publish or Dispatch.

At the 1.0 foundation gate, every family member advertised the GitHub Adapter but no supported
tracker operation. The Python, shell, and PowerShell distributions now advertise their
capability-gated `publish`/`reconcile` implementations described below. Each family member's native
manifest remains the declaration of its other capabilities. `record-dispatch-result`, report mode,
execute-frontier, and concurrent Dispatch remain unsupported everywhere. Python advertises
`terminal_rendering: true`; shell and PowerShell continue to advertise `terminal_rendering: false`
and fail closed on `reconcile --terminal` with `unsupported_operation` until their native renderers
land. Python, shell, and PowerShell advertise their trusted immutable-revision protocol and explicit
`repair-index`. Mode is `off`.

## 5. Event observations

Event schema 1.1 adds `wrapper.continuation.reconciled`,
`wrapper.continuation_dispatch.started`, `wrapper.continuation_dispatch.ended`, and
`wrapper.continuation.stopped`. Events are redacted observations only: they never establish
Producer authority, carry authoritative records, grant Dispatch, or contain runnable Instructions.

## 6. Native scenario harness

[`continuation-scenarios.json`](../git-loopy/conformance/continuation-scenarios.json) is the
language-neutral, data-only public-command harness. It independently declares fixture schema 1.4,
Continuation contract 1.0, record format 1, Wrapper contract 1.3, and Event schema 1.1.

Every family adapter reads the fixture directly and invokes its real native entrypoint. Request
objects are supplied through the declared stdin or file source. `$INPUT_FILE` is the fixture
harness's sole path placeholder. `github_script` is an ordered deterministic scripted-GitHub
transport whose data records pin the command, optional expected stdin, stdout, stderr, and exit
code. Adapters consume every listed call in order, fail on an unlisted call, and compare observed
calls with `github_calls`. The shared transport probe exercises both a listed response and
unlisted-call rejection in every host adapter.
Fixture records contain data only—no host-language expression, executable hook, or duplicated
expected-value algorithm.

The foundation scenarios prove the capability manifest, request framing, explicit terminal
selection, fail-closed operations, and exit mapping without contacting GitHub or entering a Run.
Later semantic tickets extend the same harness rather than creating private command or transport
oracles.

Fixture schema 1.3 permits distribution selectors, literal distribution-specific capability
scenarios, workflows, pinned completion-record vocabularies and physical bounds, complete
Action/interaction/condition schemas, raw-segment stress requests, and exact native `publish`
stdout/stderr. Invalid completion cases derive from literal valid request templates through
data-only RFC 6902 `add`, `remove`, and `replace` patches. A workflow executes multiple fresh
native commands against one ordered scripted-GitHub transport. Family adapters run only scenarios
and workflows naming their distribution, so a member advertises and proves a capability only when
its native implementation lands.

## 7. Native atomic completion records

Python, shell, and PowerShell `publish` accept one version-identifiable completion envelope for one
Workstream and one planning Producer transition. Publication is `shared` or `ephemeral`, and the
disposition is exactly one of:

- `continue`: one or more complete Actions and no outcome or no-guidance branch;
- `terminal`: one shared, durably evidenced `complete`, `rejected`, `abandoned`, or `superseded`
  Workstream outcome and no Actions; or
- `no-guidance`: only the shared `no-successor-created` or ephemeral `ephemeral-only` case.

Ephemeral completion is validated and returned with an `unpublished` receipt. It never establishes
a carrier, enters Reconciliation, or becomes available to automation. Missing semantics, malformed
records, undefined successors, and publication failures are errors rather than an implicit
ephemeral or no-guidance result.

Every Action carries a unique fragment-local key, summary, versioned Action kind, durable
occurrence discriminator, tagged Skill/command/manual Instruction, primary durable Target,
non-empty durable Basis, typed Prerequisites, exactly one interaction classification and evidence,
a typed completion condition, and optional context references. Manual Instructions and the six
contract-defined hard-HITL Action kinds must be `HITL-required`. Local prerequisite references must
name another Action in the same fragment. Unknown Action, condition, outcome, reason, reference,
effect, requirement, or trigger semantics reject the whole envelope.

The v1 Action-kind, interaction-evidence, and condition registries are closed and pinned in the
Conformance fixture. `transition-owner-attestation` is valid only for `AFK-safe` and its required
owner must match `completion.transition.owner`; it must also attest `noninteractive: true`.
Together with the typed completion condition and the Action's canonical empty-or-declared effects,
requirements, and triggers, that attestation is the AFK safety case rather than a bare owner claim.
`human-boundary` is valid only for `HITL-required`; it carries one pinned human-boundary reason and
a durable typed resolution condition. Conditions pin their required and optional fields, string
fields, local-reference field, allowed durable Target kinds, and enum values such as pull-request
review state. They are machine-evaluable durable references or an `action-completed` local
reference; free-text-only prerequisites and completion conditions are invalid. Unknown fields are
rejected outside reserved `advisory_extensions` maps, whose content cannot establish behavior.

Publication verifies the durable transition-evidence comment before mutation, establishes the
repairable `git-loopy-continuation` discovery label, appends one record-format-1 carrier comment,
and rereads that exact comment before returning a committed receipt. The body is:

````text
<!-- git-loopy-continuation:1 -->
```json
<one canonical JSON Producer revision>
```
````

Validation and canonicalization finish before the first GitHub call. The portable profile is UTF-8
without BOM, NFC-normalized strings, duplicate-key rejection, lexically sorted object keys, compact
JSON, no floats, interoperable signed 53-bit integers, maximum depth 16, maximum array length 256,
maximum individual string length 8 KiB UTF-8, and maximum canonical record length 48 KiB. The
command iteratively checks raw JSON nesting before invoking its host JSON decoder. Depth counts
only object and array containers, so a populated value at container depth 16 is valid and depth 17
is rejected. The command also checks the live carrier body limit before establishing the discovery
label.

An atomic root's revision identity is the SHA-256 digest of the canonical completion envelope.
Python immutable successors bind the completion and sorted observed parents; an audited
re-attestation additionally binds its affected heads, authorized actor, and copy, replace, or retire
mode. Each Action also
gets a SHA-256 semantic fingerprint over only its Instruction, Prerequisites, interaction
classification and evidence, completion condition, effects, requirements, and triggers. Summary,
Basis, Producer provenance, carrier, timestamps, Readiness, and display order do not alter that
fingerprint.

Each native `reconcile` performs a fresh all-state read for its supported publication scope,
authenticates a marked comment before semantic parsing, requires the comment author and embedded
Producer to match explicit trust, validates the revision digest and semantic fingerprints, and
reads current Action Targets. Human Producers require current write, maintain, or admin permission;
bot and App identities require explicit allowlisting. Untrusted marker lookalikes are security
diagnostics, not records and not reasons to quarantine trusted guidance. Every pinned v1 Action-kind
and condition kind is genuinely evaluated against current GitHub facts. Open Actions are returned
with their identity, semantic fingerprint, Instruction, Target, Basis, Producer provenance,
interaction classification, Prerequisites, completion condition, and an explicit `Ready` or
`Blocked` Readiness; a Blocked Action's `unsatisfied_prerequisites` names exactly the Prerequisites
still outstanding. Terminal and no-guidance records contribute no Action. The discovery label is an
index only: the Producer comment and current GitHub facts are authority, and no queue, journal,
snapshot, or local cache is created.

Python, shell, and PowerShell immutable-revision Reconciliation discover issues and pull requests with a
complete, paginated all-state read rather than label-authoritative discovery: explicit closed
coverage traverses every returned page regardless of index-label presence or staleness. Each durable read
(issue, pull request, labels, sub-issues, commit, branch, review, comment) uses a source-specific
validator and up to three bounded stable-read attempts; a definitive not-found is itself a stable
negative fact, but persistent churn or an unavailable read yields a typed `unverified_completion` or
`unverified_prerequisite` diagnostic and excludes only the affected Action, never an optimistic
Ready, Blocked, completion, or retirement claim. Completion conditions and every typed Prerequisite,
including local `action-completed` dependencies on other Actions in the same read, are evaluated
against one stable fact set gathered for that call; a cycle formed through mutually referencing
`action-completed` completion conditions is a `prerequisite_cycle` diagnostic, not infinite
recursion. Equivalent live claims sharing one Action identity are deduplicated into one guidance
entry: their durable Basis and Producer provenance union together, and an optional `provenance` list
(present only when more than one lineage actually contributed) records each contributing login,
role, carrier, and revision. Incompatible semantics under one identity are never resolved by
timestamp, discovery order, or recency; they surface as an `action_conflict` diagnostic and are
excluded from guidance until one lineage retires.

Python, shell, and PowerShell Reconciliation return an opaque `sha256:` observation token over the
repository, current Producer heads, and inspected comment validators. Immutable publication names
exactly those observed heads. The append is deterministic and idempotent: an indeterminate retry finds the same
revision. Equivalent concurrent heads deduplicate in guidance; non-equivalent stale appends remain
visible as a fork until one fresh revision names every current head. Edited comments, missing
predecessors, revoked authority, and unauthorized ancestry quarantine only their lineage. Recovery
from a tainted lineage requires a separately allowlisted re-attester and an audited copy, replace,
or retire declaration naming every affected head. When a damaged comment cannot yield a valid
revision identity, Reconciliation supplies a deterministic comment-scoped affected-head identity
so the recovery ceremony remains explicit and satisfiable.

Callers select the Python, shell, or PowerShell immutable-revision capability with
`revision_protocol: true` on `reconcile`, then pass the exact `observation` and ordered `parents` to
`publish`. Omitting those fields selects only the family-wide atomic-root capability subset.
Supplying `parents` or `reattestation` without an observation is invalid rather than silently
ignored.

Normal Reconciliation reports missing or stale index labels but never mutates them. Python, shell,
and PowerShell `repair-index` are the only index mutation paths. Each command authenticates the
operator and every record author, adds labels to trusted carriers, and removes labels only from
artifacts with no marked record. Publication still establishes the label before append and rereads
the exact comment before commit. Any operational failure after the durable workflow transition
returns `repair_required`; it never falls back to ephemeral guidance or a success-shaped receipt.

## 8. Prospective projection: retirement, ordering, delta, Handoff, and terminal rendering

The Python distribution's `reconcile` derives a wholly prospective projection: every result replaces
the prior one in full from durable facts rather than appending to a history, queue, or journal. The
following optional, version-1 request and result fields extend the contract without changing any
existing request or pinned response shape; omitting them selects exactly the prior behavior.

**Retirement receipts (request `completion.retirements`, result `retirements`).** A successor's
`completion` may carry a bounded, transient list of typed retirement receipts, each naming a
`predecessor_revision_id`, the retired `action_key`, a `reason` of `completed`, `lost-basis`,
`workstream-outcome`, or `supersession`, durable `evidence`, and — only for `supersession` — a
`replacement` Action identity inputs. Reconciliation derives a receipt's live legitimacy purely by
comparing the requesting successor against the exact predecessor(s) it names in its own `parents`
and `semantic_fingerprints`: there is no central journal, tombstone, or cache. A supersession must
name a current replacement Action with a different durable occurrence identity; reusing the retired
identity does not create a recurrence. A receipt naming an unrelated revision, an unknown action
key, or a `replacement` outside `supersession` is a structural rejection; a receipt naming a real
predecessor that the successor does not actually retire or supersede surfaces as an
`invalid_retirement_receipt` diagnostic rather than a fatal error. Completed or invalidated
occurrences never resurrect: retirement is transient and reported once, for the refresh that proves
it, not persisted.

**Workstream outcomes (result `outcomes`).** Each terminal Workstream head contributes one outcome
entry (`workstream_anchor`, `kind`, `destination_satisfied`, durable `evidence`) alongside any other
Workstream's still-open guidance. `status` is `complete` only when every discovered Workstream has an
explicit, destination-satisfied terminal outcome over closed coverage; an empty non-terminal
projection reports `waiting`; anything else with open guidance reports `guidance`. A merely empty
Action list never implies completion — closed coverage must be explicit (`revision_protocol: true`
with a full paginated read, or an equivalent explicit closed-coverage read).

**Deterministic ordering.** Actions order `Ready` before `Blocked`, then by a per-record local
topological layer derived only from local `action-completed` prerequisites in that same fragment
(cycle-safe), then by canonical Workstream Anchor. Within one Workstream, local declaration order
applies before Action identity breaks the final tie. No local declaration order leaks across
Workstreams, and no global Action-kind stage order, timestamp, or discovery order ever participates.

**Refresh delta (request `previous_actions`, result `delta`).** Supplying the caller's own prior
observed `actions` list (identity plus `semantic_fingerprint` pairs) makes reconcile return a
bounded `delta` of `added`, `retired`, and `changed` action identities versus that explicit prior
projection. Reconciliation holds no hidden memory of past calls; omitting `previous_actions` omits
`delta` entirely and changes nothing else.

**Handoff reference (request `handoff`, result `actions[].handoff_reference`).** After Actions are
fully derived, ordered, and their Readiness fixed, an explicit `handoff` request naming one
`action_identity`, `context_available`, and, when available, one opaque machine-local `reference`
attaches at most one `handoff_reference` to the exactly matching Action. An unavailable local
context or an Action no longer present is reported as a
diagnostic-only `handoff_context_unavailable` or `handoff_action_unavailable` code; neither changes
any Action's identity, Readiness, order, or completion — Handoff is a resume pointer, never an
input to semantics.

**Terminal rendering (`reconcile --terminal`).** The Python distribution renders one primary
Action in full detail (Readiness, summary, Instruction, durable Target and Basis locators — never
their content), an expandable Ready/Blocked remainder with hidden counts, a separate Needs-attention
section for diagnostics (conflicts, malformed guidance, unstable reads, and Unverified scopes),
Workstream outcomes, transient retirements from that refresh, and the bounded refresh delta when
present. `--terminal` is accepted only by `reconcile`, exits `1` closed for every other operation,
and is never mixed with machine JSON on the same invocation. Independent verified guidance remains
usable even while unrelated occurrences sit in Needs attention.
