# The Continuation Contract

> The separately versioned, language-neutral contract for Workflow Continuation across the
> git-loopy Runner family. Domain terms are defined in [`CONTEXT.md`](../CONTEXT.md).

**Continuation contract version:** 1.2

**Record format:** 1

The Continuation contract is independent of Wrapper contract 1.4, Event schema 1.1, and the
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
git-loopy continuation resolve-authority [--input FILE]
```

The command surface never performs a Continuation action. `publish` records a Transition owner's
typed revision, `reconcile` derives current guidance, `record-dispatch-result` records only the
contract's exceptional Dispatch-evidence classes, `repair-index` repairs discovery metadata, and
`resolve-authority` narrows operator configuration into one effective authority (§10). Their
semantic request and result records land in later capability-gated revisions.

`resolve-authority` is the only operation that contacts no tracker at all: it is pure computation
over the request, so an operator can ask what a Run would be permitted to do without that question
itself being an action.

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
manifest remains the declaration of its other capabilities. Report mode, execute-frontier, and
concurrent Dispatch remain unsupported everywhere; `record-dispatch-result` is supported by every
family member. Python, shell, and PowerShell now all advertise `terminal_rendering: true`; no family
member fails closed on `reconcile --terminal` any longer. Python, shell, and PowerShell advertise
their trusted immutable-revision protocol and explicit `repair-index`. Mode is `off`.

Contract 1.2 adds the optional `fixed_frontier_authorization` capability. A distribution
advertising it implements every §9 field — the AFK safety case, the frozen Automation scope and
frontier, typed Performer eligibility, the single DispatchAuthorization, the locked stop
precedence, and the two Dispatch-evidence classes — and supports the `record-dispatch-result`
operation. Python, shell, and PowerShell all advertise `fixed_frontier_authorization: true`.

The §9 stop `workstreams-terminal` is derived from a `complete` Reconciliation status over closed
coverage, and `complete` is derived from the §8 `outcomes` projection. Workstream outcomes and the
Waiting/guidance/Complete status are therefore **not** gated by `prospective_projection`: they are
a prerequisite of the locked stop precedence, and a distribution that advertised
`fixed_frontier_authorization` without them could never return the one stop that means the Run is
finished. Every family member projects `outcomes` and the three statuses. `prospective_projection`
continues to gate only the fields §9 does not need — retirement receipts, the refresh delta, and
the Handoff reference — so a distribution may advertise `fixed_frontier_authorization: true` beside
`prospective_projection: false`, as shell did until its native §8 projection landed.

Contract 1.1 adds the optional `prospective_projection` capability. A distribution advertising it
implements the §8 fields §9 does not require — retirement receipts, refresh delta, and Handoff
reference. Python, shell, and PowerShell all advertise `prospective_projection: true`. A
distribution that does not advertise it fails closed rather than ignoring the gated fields:
`completion.retirements` is a structural `publish` rejection, and `previous_actions` or `handoff` on
`reconcile` returns `unsupported_operation`. The deterministic ordering of §8 is **not** gated: it
is the contract's ordering rule and every family member implements it.

A distribution advertises the contract versions whose records it accepts. Python, shell, and
PowerShell all advertise `["1.0", "1.1", "1.2"]`. Each added version introduces only optional
fields, so a record published under an earlier version stays valid
without rewriting. A record whose Action carries a `safety_case` **must** declare `1.2`: the
AFK-safe classification is what authorizes unattended Dispatch, so a reader that would silently
drop the case while keeping the claim must reject the whole record instead.

### Transition-owner coverage gates report mode

Reconciliation projects records; it does not invent them. So the guidance report mode renders is
only ever as complete as the set of Skills that publish, and advertising `report` while a locked
Action kind, disposition or coverage area has no recognized Transition owner would present a
projection built from records nobody in the catalog writes — visibly authoritative and quietly
partial.

Complete core Transition-owner coverage is therefore a **mandatory precondition** for advertising
`continuation_modes.report`, not a quality target beside it. Coverage is complete when every locked
coverage area — planning, specification, decomposition, implementation, review, publication,
conflict resolution, direct research/prototype ownership, pointer-only helpers, parent cleanup,
partial decomposition quarantine, exact-head recurrence and repair-required failure — has a
recognized owner, and the owners between them publish every kind in the closed v1 Action-kind
registry and all three dispositions, each hard-HITL kind behind an explicit human boundary.

Two properties make that checkable rather than asserted. Each Skill carries its completion request
as a fenced JSON template behind a `<!-- continuation-request: NAME -->` marker, so what a prompt
tells a session to publish is executable against the real native command. And the packaged
distribution mirrors the project sources byte for byte, so the requests an adopter's session
publishes are the ones the coverage suites executed. The Python distribution enforces both, together
with the report-mode interlock, in `tests/test_continuation_owner_coverage.py`.

Contract 1.3 is the revision that satisfies this precondition and advertises
`continuation_modes.report: true` across Python, shell and PowerShell. The interlock stays an
implication in the direction it was written: coverage gates the advertisement, and the suite fails
if `report` is advertised while any locked area is unowned. `default` remains `off` — an adopter
opts in per §10 — and `execute-frontier` is advertised only by a distribution that can actually
dispatch one. The #267 rollout gate is the point at which that became the whole family: the Python
Runner (#264), the shell Orchestrator (#265) and the PowerShell Orchestrator (#266) each advertise
`execute-frontier: true`. Advertising the mode is serial Dispatch only; `concurrent_dispatch` stays
`false` everywhere, so no reader can extrapolate one from the other.

A distribution advertising `execute-frontier: true` is judged by setup against the
`execute-frontier` capability profile, which adds two requirements to `report`'s:
`mode-execute-frontier` (the mode is advertised) and `fixed-frontier` (the
`fixed_frontier_authorization` optional capability §9's authorization is gated by is advertised
beside it). Each is defeatable on its own, so a refusal names which half of the claim the manifest
stopped serving.

## 5. Event observations

Event schema 1.1 adds `wrapper.continuation.reconciled`,
`wrapper.continuation_dispatch.started`, `wrapper.continuation_dispatch.ended`, and
`wrapper.continuation.stopped`. Events are redacted observations only: they never establish
Producer authority, carry authoritative records, grant Dispatch, or contain runnable Instructions.

Report mode emits `wrapper.continuation.reconciled` once per observation. Its payload is built by
**naming what survives**, never by removing what must not: `mode`, `phase`, `repository`, `status`,
`action_identities`, `dispositions`, `kinds`, `reason_codes`, `counts` and `duration_ms`. A field
the record format grows tomorrow is therefore absent from the Event until someone adds it on
purpose, rather than leaking until someone notices. Instruction text, summaries, targets, bases,
Producers, safety cases, completion conditions, Prerequisites and interaction requirements are all
outside that set, so the Event is an observation that guidance *changed* and never a channel it can
be acted on through.

## 6. Native scenario harness

[`continuation-scenarios.json`](../git-loopy/conformance/continuation-scenarios.json) is the
language-neutral, data-only public-command harness. It independently declares fixture schema 1.7,
Continuation contract 1.2, record format 1, Wrapper contract 1.4, and Event schema 1.1.

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

Fixture schema 1.4 adds the §8 prospective-projection vocabulary to the same data-only harness:
`revision_protocol.diagnostic_codes` pins the family's complete `reconcile` diagnostic vocabulary —
the union across every distribution, not one member's subset — so a distribution emitting an
unregistered code is a fixture change rather than a silent addition. Scenarios may pin
`retirements`, `outcomes`, `delta`, and `handoff_reference` result fields and the exact Continuation
view order. Ordering scenarios name every distribution, because ordering is ungated; scenarios
exercising the gated §8 request fields name only the distributions advertising
`prospective_projection`.

Fixture schema 1.6 makes a narrowed scope a claim the fixture checks rather than an authoring
convention. Family adapters run only the scenarios and workflows naming their distribution, so a
record narrowed to a subset is a question the rest of the family is never asked; until 1.6 the
advertised `optional_capabilities` manifests and the per-record `distributions` arrays were two
hand-maintained lists kept in sync by discipline alone. `capability_coverage` requires every
narrowed record to declare one of three reasons. `manifest-identity` is reserved for the per-family
capability scenarios. `capability-absent` names an optional capability, and the record's
`distributions` MUST equal exactly the set of distributions whose advertised manifest carries that
capability with the declared value — scoping is derived from advertisement, not asserted beside it.
`family-local-detail` names an operation and a variant group whose members MUST be pairwise
disjoint, MUST together cover every distribution advertising that operation, and MUST agree on
arguments, exit code, and error code; only human-readable detail may differ. The manifests measured
against are the fixture's own per-family capability scenarios, each proven by executing the real
native entrypoint, so the chain runs from real CLI through advertised manifest to scenario scoping
with no hand-asserted link. Every family adapter enforces the gate, because a distribution must be
able to prove itself without a Python runtime present.

Fixture schema 1.7 completes the foundation gate's registry of ten end-to-end scenarios and puts the
check in every family. `end_to_end_coverage.locked_scenarios` maps each locked scenario to the
workflows driving it through the real native commands; every family adapter pins the ten names
itself, requires each scenario to name an existing workflow, requires a scenario's workflows to
cover every distribution between them — a member lacking an optional capability answers by failing
closed, in a workflow narrowed for a `capability_coverage` reason — and requires `publish`,
`reconcile`, and `record-dispatch-result` all to be exercised. `read_only_call_prefixes` pins the
read-only shape of Reconciliation against the observed transport for every pinned `reconcile`, so a
refresh that writes fails on the call it made rather than on the words it printed.

The same revision retires `unsupported_reconciliation_semantics` from
`revision_protocol.diagnostic_codes`. The vocabulary is the union across every distribution, and no
distribution emits that code since the path-local PowerShell derivation was deleted; a registered
code nobody produces is vocabulary a port can implement wrongly and never be told.

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

The subset narrows *discovery*, never *derivation*. Both paths decide what an Action means through
the same evaluation: the same Prerequisite and completion-condition evaluators, the same stable
fact reads, the same readiness and `unsatisfied_prerequisites`, the same union basis and
provenance, and the same `action_conflict`, `prerequisite_cycle`, `unverified_completion`, and
`unverified_prerequisite` diagnostics. A path-local derivation is a rejection, not an
implementation choice: an §9 authorization is computed over whatever `actions` and `diagnostics`
Reconciliation returned, so a narrower label-indexed derivation would drop the very
`action_conflict` that makes the frozen coverage untrustworthy and authorize a Dispatch the
revision-protocol path refuses.

Normal Reconciliation reports missing or stale index labels but never mutates them. Python, shell,
and PowerShell `repair-index` are the only index mutation paths. Each command authenticates the
operator and every record author, adds labels to trusted carriers, and removes labels only from
artifacts with no marked record. Publication still establishes the label before append and rereads
the exact comment before commit. Any operational failure after the durable workflow transition
returns `repair_required`; it never falls back to ephemeral guidance or a success-shaped receipt.

"After the durable workflow transition" is not conditional on the revision protocol. The transition
has already happened by the time `publish` starts, so a failed index label, a failed carrier index
edit, a failed append, an author that does not match the completion Producer, and a reread that
does not match the append all strand exactly the same evidence whether or not the request carried
an `observation`. All five are `repair_required` on both discovery paths in every family member.
Reading the transition's own evidence happens *before* the index label and is therefore an ordinary
`github_error`. The reread is issued before the appended author is judged: both describe the same
post-transition write, so a reader that short-circuits on the author records one call less evidence
than the operator needs to repair it. A `gh` call that succeeds but answers with something that is
not a comment object strands the same write and is `repair_required` too; only the human-readable
detail is the transport's own parse diagnostic and therefore family-local prose.

## 8. Prospective projection: retirement, ordering, delta, Handoff, and terminal rendering

A distribution advertising the `prospective_projection` capability derives a wholly prospective
projection from `reconcile`: every result replaces the prior one in full from durable facts rather
than appending to a history, queue, or journal. The following optional, version-1.1 request and
result fields extend the contract without changing any existing request or pinned response shape;
omitting them selects exactly the prior behavior. The deterministic ordering below is the exception:
it is ungated and every family member implements it.

**Retirement receipts (request `completion.retirements`, result `retirements`).** A successor's
`completion` may carry a bounded, transient list of typed retirement receipts, each naming a
`predecessor_revision_id`, the retired `action_key`, a `reason` of `completed`, `lost-basis`,
`workstream-outcome`, or `supersession`, durable `evidence`, and — only for `supersession` — a
`replacement` Action identity inputs. Reconciliation derives a receipt's live legitimacy purely by
comparing the requesting successor's own projected Action identities against the exact
predecessor(s) it names in its own `parents`: a retirement is proven when the predecessor's Action
identity is absent from the successor. There is no central journal, tombstone, or cache. Every
recurrence — whatever the receipt's `reason` — must carry a durable `occurrence` discriminator
distinct from the retired one; an Action re-declared under the retired occurrence identity is the
same occurrence, not a recurrence, and proves no retirement. A `supersession` must additionally name
its `replacement` explicitly, sharing the retired Action's Workstream Anchor, kind, and Target while
differing in `occurrence`, and that replacement must be live in the successor. A receipt naming an
unrelated revision, an unknown action key, or a `replacement` outside `supersession` is a structural
rejection; a receipt naming a real predecessor that the successor does not actually retire or
supersede surfaces as an `invalid_retirement_receipt` diagnostic rather than a fatal error.

Retirement is enforced across the whole ancestor chain, not just the immediate parents. An Action
identity retired anywhere in a revision's ancestry can never resurrect: a later revision re-declaring
that identity taints itself and reports `retired_occurrence_resurrected` with the offending
identities. Retirement itself stays transient — reported once, for the refresh that proves it, and
never persisted.

Retirement legitimacy is provable only against an immutable revision chain. Label-indexed discovery
is deliberately lineage-free (the atomic-root capability subset), so it can neither prove nor project
a Retirement. In a distribution advertising `prospective_projection`, when discovered records carry
receipts the label-indexed path reports `retirements_require_revision_protocol` naming those
`revision_ids` rather than silently dropping them; `revision_protocol: true` is required to project
`retirements`.

Transient retirements are therefore a **revision-protocol guarantee, not a default-path one**. The
`retirements` key is nevertheless always present in a `reconcile` result, on every path and in every
distribution, so its absence never carries silent meaning. Presence alone is not a claim. In a
distribution advertising `prospective_projection`, under `revision_protocol: true` the list is the
projection's own answer and an empty list means nothing was retired, while on its label-indexed path
an empty list means only that Retirement was not computable, and the
`retirements_require_revision_protocol` diagnostic — the discriminator there — names every revision
whose receipts went unevaluated. A Consumer that needs retirement evidence must request the revision
protocol from a projecting distribution and read the diagnostics; one that does not may read the gate
as "ask again with lineage".

A distribution that does not advertise `prospective_projection` never projects receipts, so it emits
`retirements` as an empty list on every `reconcile` and never emits that diagnostic. For these
distributions the capability manifest — not a result diagnostic — is what tells a Consumer the empty
list is capability-derived rather than proven.

Such a distribution must nevertheless **read** `completion.retirements` under the revision protocol:
it accepts, structurally validates, and preserves receipts on discovered records rather than
quarantining them, because rejecting a conformant successor would resurface the predecessor that
successor retired as live guidance. Structural validation matches the authoring rules above, so a
malformed receipt is an `invalid_revision` quarantine in every distribution alike. It refuses to
**author** receipts it cannot project, so `completion.retirements` on a `publish` request is a
structural rejection there.

That read guarantee holds on the label-indexed path as well as under the revision protocol. Every
distribution reads lineage-bearing records discovered through the label index and digests them by
the same rules the revision-protocol reader applies, so the path a record was discovered on never
decides whether its lineage survives. The shared scenario
`reconcile-reads-a-lineage-record-from-the-label-index` pins this for all three distributions.

**Workstream outcomes (result `outcomes`).** Each terminal Workstream head contributes one outcome
entry (`workstream_anchor`, `kind`, `destination_satisfied`, durable `evidence`) alongside any other
Workstream's still-open guidance. `status` is `complete` only when every discovered Workstream has an
explicit, destination-satisfied terminal outcome over closed coverage; an empty non-terminal
projection reports `waiting`; anything else with open guidance reports `guidance`. A merely empty
Action list never implies completion — closed coverage must be explicit (`revision_protocol: true`
with a full paginated read, or an equivalent explicit closed-coverage read).

**Deterministic ordering.** Every distribution orders Actions `Ready` before `Blocked`, then by a
per-record local topological layer derived only from local `action-completed` prerequisites in that
same fragment, then by canonical Workstream Anchor. Within one Workstream, local declaration order
applies before Action identity breaks the final tie. Layers relax to a fixed point rather than
resolving by recursive descent, so the answer never depends on the order Actions were declared or
visited; anything still unresolved when the relaxation stops is on, or feeds, a prerequisite cycle
and layers 0. No local declaration order leaks across Workstreams, and no global Action-kind stage
order, timestamp, or discovery order ever participates. This rule is not capability-gated: it
governs both the label-indexed and revision-protocol paths in every family member.

**Refresh delta (request `previous_actions`, result `delta`).** Supplying the caller's own prior
observed `actions` list (identity plus `semantic_fingerprint` pairs) makes reconcile return a
bounded `delta` of `added`, `retired`, and `changed` action identities versus that explicit prior
projection. Reconciliation holds no hidden memory of past calls; omitting `previous_actions` omits
`delta` entirely and changes nothing else.

**Handoff reference (request `handoff`, result `actions[].handoff_reference`).** After Actions are
fully derived, ordered, and their Readiness fixed, an explicit `handoff` request naming one
`action_identity`, `context_available`, and, when available, one opaque machine-local `reference`
and an optional human-readable `note` attaches at most one `handoff_reference` to the exactly
matching Action. An unavailable local context or an Action no longer present is reported as a
diagnostic-only `handoff_context_unavailable` or `handoff_action_unavailable` code; neither changes
any Action's identity, Readiness, order, or completion — Handoff is a resume pointer, never an
input to semantics.

**Terminal rendering (`reconcile --terminal`).** A distribution advertising `terminal_rendering`
renders one primary Action in full detail (Readiness, summary, Instruction, durable Target and Basis
locators — never their content), a genuinely bounded Ready/Blocked remainder, a separate
Needs-attention section for diagnostics (conflicts, malformed guidance, unstable reads, and
Unverified scopes), Workstream outcomes, transient retirements from that refresh, and the bounded
refresh delta when present. Each remainder group states both its full count and how many rows were
withheld — `Ready (N more, H hidden)` — and points at plain `reconcile` to see the rest, so the
rendering stays bounded no matter how large the projection is. `--terminal` is accepted only by
`reconcile`; any other operation rejects it as a malformed invocation and exits `2` per §3. It is
never mixed with machine JSON on the same invocation. A terminal-mode Reconciliation failure
preserves its typed machine JSON and exit `1` rather than rendering human guidance. Independent
verified guidance remains usable even while unrelated occurrences sit in Needs attention.


## 9. Fixed-frontier Automation authorization

Gated by the optional `fixed_frontier_authorization` capability. This section decides *whether* one
Action may be dispatched to one Performer. It never executes one: `reconcile` returns an
authorization, and the Runner that acts on it is out of scope here.

**AFK safety case (`completion.actions[].safety_case`).** An Action may only be dispatched
unattended when its Transition owner published the positive case that justifies it. The case is
versioned and covers the *exact* occurrence: its `instruction`, `target`, and `completion_condition`
must equal the Action's own, so a case can never be written for a different Instruction variant than
the one that runs. It also declares `effects` (the bounded authority the Instruction consumes),
typed `assumptions`, typed `requirements` (the noninteractive capability and access the Performer
must already hold), a `retry` strategy, and the exceptional HITL `triggers` that end the Dispatch.
The case is the single source of truth for those fields: an Action carrying a safety case may not
also declare `effects`, `requirements`, or `triggers` itself. Only an `AFK-safe` Action may carry
one — a case on a HITL-required Action is a structural rejection, not a downgrade.

The case participates in the Action's semantic fingerprint. Those fields used to live on the Action
itself and were hashed there; moving them into the case without moving them into the fingerprint
would let a case widen its own effects or drop to `at-most-once` retry while its frozen fingerprint
— and therefore its authorization — stayed valid for the rest of the Run.

An `AFK-safe` Action *without* a safety case is not a rejection: it stays visible, and it is simply
never selectable. The claim alone does not authorize anything.

**Frozen scope.** One Run's Automation coverage and execution grants are computed once, before
dispatch, from the ordered ceilings the caller supplies. Ceilings **intersect** — a repository or
grant absent from any one ceiling is absent from the Run. Denials **accumulate** — a denial in any
one ceiling denies for the Run. Runtime `revocations` narrow immediately and fold into denials.
Nothing widens: a grant or capability that appears later waits for a new Run.

The frozen scope is replayed with the frozen frontier. A subsequent Reconciliation in the same Run
supplies its prior effective scope as `automation.scope.prior`, and the newly computed scope is
intersected with it and its denials unioned. The frontier alone is not the freeze: a Run that
carried its frontier forward but recomputed authority from whatever arrived next would let a grant
added mid-Run authorize work the Run was never entitled to.

**Frozen frontier.** The initial stable Reconciliation freezes every in-coverage Action *identity*
and *semantic fingerprint*, including Blocked, HITL, ineligible, and quarantined members. Readiness
of a frozen member may change during the Run — an initially Blocked member whose Prerequisites
become satisfied becomes selectable. Identity and semantics may not: an Action the freeze never saw
is `newly-produced`, and a frozen identity whose fingerprint moved is `changed-semantics`. Both are
returned in `report_only` and neither is ever dispatched.

**Eligibility.** Each current Action returns `automation_selectable` plus, when false, its typed
`reasons` drawn from `already-dispatched`, `grant-missing`, `human-boundary`, `not-ready`,
`outside-coverage`, `outside-frontier`, `performer-ineligible`, `quarantined`, and
`safety-case-absent`. An Action is eligible only when it is current, Ready, conflict-free,
positively AFK-safe, inside the frozen coverage and frontier, covered by the frozen grants, and
freshly matched to the Performer's declared noninteractive posture.

The posture is a **closed world**. It declares `satisfied_requirements` *and* the
`instruction_modes` the Performer has a handler for; an Action whose Instruction mode the Performer
never claimed is `performer-ineligible`. Silence is never read as universal competence.

A `guidance-fault` prevents authorization outright. The conflicted or unverifiable fragment never
reaches `actions` at all, so the selectable Action beside it looks healthy — but what the fault
makes untrustworthy is the *coverage* the Run froze, and nothing inside an untrustworthy
description of the project may be dispatched on the strength of it.

**DispatchAuthorization.** At most one is returned per Reconciliation. It binds one Action
occurrence identity, its semantic fingerprint, one Performer, the Workstream anchor, the Target, the
safety case version, the completion condition, and the case's bounded effects, requirements, retry
strategy, and triggers. Ordering is the §8 ordering rule: the first selectable Action wins.

**Stop.** Exactly one stop is returned, and exactly one of `authorization` or `stop` is present. The
disposition is `complete`, `expected-boundary`, or `attention-required`, derived from the locked
reason precedence, strongest first:

| Reason | Disposition |
| --- | --- |
| `workstreams-terminal` | `complete` |
| `safety-case-violation` | `attention-required` |
| `uncertain-effect-state` | `attention-required` |
| `guidance-fault` | `attention-required` |
| `human-boundary` | `expected-boundary` |
| `grant-missing` | `expected-boundary` |
| `performer-ineligible` | `expected-boundary` |
| `frontier-drained` | `expected-boundary` |
| `awaiting-prerequisites` | `expected-boundary` |

`workstreams-terminal` is the only completion. Every other stop states its nonterminal status, one
trustworthy next Action or readiness condition, decisive durable evidence, secondary barriers,
report-only successors, and the explicit statement that no successor Action was executed.

**Dispatch evidence (`record-dispatch-result`).** Only two exceptional classes are recordable:
`safety-case-violation` (the Dispatch met a boundary the published case said it would not) and
`uncertain-effect-state` (a non-retry-safe effect whose state cannot be determined). Ordinary
success and ordinary execution failure stay in the Runner's existing artifacts, Events, retry, and
Strike paths and are never written here. The record carries no Instruction and no field to put a
secret in: an identity, a fingerprint, a Performer, a class, a one-line summary, durable evidence
references, and — for a violation — one human-boundary reason.

The record is bound to its own Performer at both ends. Writing requires the authenticated actor to
be the `performer` the record names; reading requires the comment's author to be that same
Performer, and applies the *whole* closed schema the writer applied. Anyone with write access can
leave a comment, so a fragment naming an identity is not enough to change a Run's authority.

The record is immutable and **non-Producer**. It never retires the Action or creates a replacement;
only the Transition owner can. Until one does, the next Reconciliation returns a
`dispatch_evidence_quarantine` diagnostic naming the affected identity *and semantic fingerprint*,
and marks that exact pair `quarantined` — a Transition owner who publishes a repaired occurrence
moves the fingerprint and the quarantine lifts with it, rather than holding down the correction it
asked for. The evidence class selects the stop: `safety-case-violation` and `uncertain-effect-state`
are different problems for the human who reads them, and both are `attention-required`.

## 10. Operator-configured authority

Nothing in §1-§9 says *who is running this and how much are they allowed to do*. Report mode makes
that question load-bearing: a Run that participates must know its mode, whose records it trusts,
what identity it speaks as, and which repositories, Targets, Action kinds, Instruction modes and
effect scopes are in scope. `resolve-authority` answers it from operator configuration alone.

**Three sources, in one locked order: `global`, then `project`, then `runtime`.** Each carries a
`mode`, a `trusted_producers` list and all five `ceilings` axes, plus an optional `actor` and
`maintainers`. Absent is not empty. A tier that declared no mode never asked for anything and drops
out; a tier that declared `off`, or an empty ceiling, asked for *nothing in particular* and narrows
everything else down to it. A request must name at least one source, in order, without duplicates.

**Narrowing is monotonic. Nothing widens.**

- `mode` is the minimum over the lattice `off < report < execute-frontier`.
- `trusted_producers`, `maintainers` and every ceiling axis are **set intersection**.
- `actor` is first-declared-wins; a later source naming a *different* actor is `invalid_request`,
  because two identities are not a narrowing of one and picking either would be a guess.
- A persisted `prior` authority narrows last, on the same rules.

This is deliberately *not* the Wrapper's ordinary "most specific tier wins" precedence. Under that
chain a project could widen a ceiling its global table imposed, which is the one thing an authority
model exists to forbid. The five ceilings are **positive allowlists**: `repositories` is open
because a repository name is not enumerable, and `targets`, `action_kinds`, `instruction_modes` and
`effect_scopes` are checked against the contract's own closed vocabularies, so a typo is a rejection
rather than a silently unreachable cap.

The result also carries `declared_mode` — the *highest* mode any source asked for, unnarrowed. It is
never used for authorization; it exists so an operator can see the gap between what they configured
and what they got, which is otherwise invisible from a Run that simply behaved like `off`.

**Two ways to end up at `off` without asking for it.** After all narrowing, an empty `repositories`
ceiling or an empty `trusted_producers` set forces `mode: off` with a typed reason
(`coverage-empty`, `trusted-producers-empty`). A projection over no repository, or over nobody's
records, is authoritative and empty — indistinguishable from "everything is finished" at exactly the
moment that is least true.

**Configuration narrows; capability fails closed.** An operator asking for less somewhere is
recorded in `narrowed` and exits `0`. A *distribution* that cannot serve the effective mode is a
different thing entirely and exits `1` with `unsupported_operation`: the mode is not advertised in
`continuation_modes`, the tracker adapter is not supported, or the adapter is missing an operation
the mode requires (`report` requires `reconcile`; `execute-frontier` also requires
`record-dispatch-result`). Mode `off` consults the manifest at no point, so a distribution that
supports nothing can still answer honestly that this Run does nothing.

**One narrowing implementation, two callers.** The native command and the Python Runner's own
preflight resolve through the same function. The Runner collects its `continuation_*` config keys
and `GIT_LOOPY_CONTINUATION_*` environment overrides into three *uncombined* sources and hands them
over; it never pre-merges. A Runner with its own copy of these rules would be projecting a different
view of the project than the one an operator can ask for by hand — and the copy that drifts is
always the one nobody runs directly.

## 11. Staged rollout and adoption

§4 says what a distribution *may* advertise and §10 says what an operator *may* authorize. This
section is the sequence between them: how a family member reaches an advertisement, how a project
that predates Continuation joins it, and what an operator does when either goes wrong.

**Setup selects and verifies one native distribution.** An adopter installs one family member and
runs setup, which judges *the distribution running it* against one named capability profile before
it writes or installs anything. That is how the selection is recorded without naming a family member
or committing an executable path: nothing here resolves an entrypoint, and every command in this
repository's own gates is relative and resolved through `PATH`, because Integration runs them in a
throwaway worktree on whatever host the operator has. A distribution that fails its profile fails
*before* the project is configured, rather than during a Run that had already started.

**The rollout gates are staged, and each one is a precondition rather than a target.**

1. `foundation` — the contract version, record format, tracker Adapter, native operations and
   `default: off` every distribution must clear.
2. `report` — `foundation` plus `resolve-authority` and `continuation_modes.report`. Complete core
   Transition-owner coverage gates the advertisement itself; see §4.
3. `execute-frontier` — `report` plus `mode-execute-frontier` and `fixed-frontier`. A member
   advertises it only once the mandatory Conformance fixtures pass for it, and the family-wide
   advertisement is a separate decision from any one member's implementation. `profile_distributions`
   in the shared fixture is where a narrower profile is recorded, so a member is never judged
   against a requirement set it has not implemented — and a member that stages a mode ahead of the
   others is a registered narrowing rather than a surprise in a Run.

`default` stays `off` at every gate: adoption is the operator's decision per §10, not the
distribution's. The first execute-frontier release is **serial only**. `concurrent_dispatch` stays
`false` across the whole family and is a later family-wide capability gate, not something a port
reaches on its own; general concurrency needs issue-backed `parallel-safe` together with the
Prerequisite, Target and effect-scope checks §9 does not perform, and serial Dispatch is not a step
towards it that a reader may extrapolate from.

**Migration is by publication, never by backfill.** A Workstream that predates Continuation carries
no marked record, and Reconciliation projects records rather than inventing them, so such a
Workstream is simply absent from the projection. It is adopted at the moment its next recognized
Transition owner publishes the first trusted root for it — one ordinary `publish`, authenticated the
way every other record is. Nothing infers, converts or mass-backfills Producer semantics from
labels, prose, unmarked comments, local files or conversation history: an unmarked comment is never
parsed, a record whose embedded Producer disagrees with its comment author is discarded, and the
discovery label is an index only.

This is why mixed coverage is reported explicitly rather than smoothed over. A project part-way
through adoption reads as `waiting` — `complete` is never inferred from an empty Action list, and a
discovery path that cannot perform a complete paginated read never has closed coverage and so can
never claim project-wide `complete`. An unadopted Workstream therefore contributes to no
authorization and to no terminal-completion claim, which is the property that makes a partial
migration safe to run against: the one moment "everything is finished" is least true is exactly the
moment an empty projection would otherwise say it.

**Failure recovery.** Each failure has one place it surfaces and one thing an operator does about
it.

- *Setup refuses the distribution.* The verdict names the unsatisfied requirements, each defeatable
  on its own, so the answer is which half of the claim the manifest stopped serving — not a
  re-install.
- *A Run refuses the mode.* Configuration narrows and exits `0`; a distribution that cannot serve
  the effective mode exits `1` with `unsupported_operation` (§10). The remedy is a distribution that
  advertises the mode or an authority that asks for less — never a Run that quietly resolves
  `report` or `off` instead.
- *Authority resolves to `off` unasked.* An empty `repositories` ceiling or an empty
  `trusted_producers` set carries a typed reason (`coverage-empty`, `trusted-producers-empty`), and
  `declared_mode` shows the gap between what was configured and what was resolved.
- *A Dispatch ends in exceptional evidence.* `safety-case-violation` and `uncertain-effect-state`
  become durable evidence bound to their Performer and stop the Run under §9's locked precedence.
  Ordinary success and ordinary execution failure stay in the Runner's existing artifacts, Events,
  retry and Strike paths.
- *The index disagrees with the records.* Reconciliation reports index diagnostics and never mutates
  labels; `repair-index` is the explicit, separate operation.

None of these establishes a central continuation issue, an authoritative Markdown snapshot, a
mutable project queue, an append-only execution journal, a central tombstone ledger or an
authoritative local cache. Rollout does not earn an exception to §1: a migration that needed one
would be building the very artifact whose absence makes every record independently verifiable.
