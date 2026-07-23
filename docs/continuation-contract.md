# The Continuation Contract

> The separately versioned, language-neutral contract for Workflow Continuation across the
> git-loopy Runner family. Domain terms are defined in [`CONTEXT.md`](../CONTEXT.md).

**Continuation contract version:** 1.0

**Record format:** 1

The Continuation contract is independent of Wrapper contract 1.2, Event schema 1.1, and the
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
tracker operation. The Python and shell distributions now advertise their capability-gated
`publish`/`reconcile` implementations described below. Each family member's native manifest remains
the declaration of its other capabilities. `record-dispatch-result`, `repair-index`, terminal
rendering, report mode, execute-frontier, and concurrent Dispatch remain unsupported. Mode is `off`.

## 5. Event observations

Event schema 1.1 adds `wrapper.continuation.reconciled`,
`wrapper.continuation_dispatch.started`, `wrapper.continuation_dispatch.ended`, and
`wrapper.continuation.stopped`. Events are redacted observations only: they never establish
Producer authority, carry authoritative records, grant Dispatch, or contain runnable Instructions.

## 6. Native scenario harness

[`continuation-scenarios.json`](../git-loopy/conformance/continuation-scenarios.json) is the
language-neutral, data-only public-command harness. It independently declares fixture schema 1.3,
Continuation contract 1.0, record format 1, Wrapper contract 1.2, and Event schema 1.1.

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

Python and shell `publish` accept one version-identifiable completion envelope for one Workstream
and one planning Producer transition. Publication is `shared` or `ephemeral`, and the disposition
is exactly one of:

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

The revision identity is the SHA-256 digest of the canonical completion envelope. Each Action also
gets a SHA-256 semantic fingerprint over only its Instruction, Prerequisites, interaction
classification and evidence, completion condition, effects, requirements, and triggers. Summary,
Basis, Producer provenance, carrier, timestamps, Readiness, and display order do not alter that
fingerprint.

Python `reconcile` performs a fresh all-state read of labeled carriers, parses marked comments,
requires the comment author and embedded Producer to match the explicit trusted policy, validates
the revision digest and semantic fingerprints, and reads current Action Targets. Supported open
Targets are returned with their identity, semantic fingerprint, Instruction, Target, Basis,
Producer provenance, interaction classification, Prerequisites, and completion condition.
Terminal and no-guidance records contribute no Action. The discovery label is an index only: the
Producer comment and current GitHub facts are authority, and no queue, journal, snapshot, or local
cache is created.
