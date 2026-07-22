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
tracker operation. The Python distribution now advertises the first capability-gated
`publish`/`reconcile` tracer bullet described below. Shell and PowerShell retain the foundation
manifest until their native tracer bullets land. `record-dispatch-result`, `repair-index`, terminal
rendering, report mode, execute-frontier, and concurrent Dispatch remain unsupported. Mode is
`off`.

## 5. Event observations

Event schema 1.1 adds `wrapper.continuation.reconciled`,
`wrapper.continuation_dispatch.started`, `wrapper.continuation_dispatch.ended`, and
`wrapper.continuation.stopped`. Events are redacted observations only: they never establish
Producer authority, carry authoritative records, grant Dispatch, or contain runnable Instructions.

## 6. Native scenario harness

[`continuation-scenarios.json`](../git-loopy/conformance/continuation-scenarios.json) is the
language-neutral, data-only public-command harness. It independently declares fixture schema 1.1,
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

Fixture schema 1.1 permits distribution selectors, literal distribution-specific capability
scenarios, and workflows. A workflow executes multiple fresh native commands against one ordered
scripted-GitHub transport. Family adapters run only scenarios and workflows naming their
distribution, so a member advertises and proves a capability only when its native implementation
lands.

## 7. Python trusted-Action tracer bullet

Python `publish` accepts the first narrow `continue` completion envelope: one Workstream, one
planning Producer, one issue Producer carrier, one durable issue-comment transition-evidence
reference, and exactly one Action with an empty Prerequisite set. The request names the repository
and an explicit non-empty trusted-Producer policy; publication never infers Producer trust from
labels, prose, local state, or conversation history.

Publication verifies the durable transition-evidence comment before mutation, establishes the
repairable `git-loopy-continuation` discovery label, appends one record-format-1 carrier comment,
and rereads that exact comment before returning a committed receipt. The body is:

````text
<!-- git-loopy-continuation:1 -->
```json
<one canonical JSON Producer revision>
```
````

The revision identity is the SHA-256 digest of the canonical completion envelope. Record keys are
sorted and JSON is compact UTF-8. The complete portable canonicalization and completion-envelope
validation profile remains capability-gated follow-up work; this tracer bullet accepts only its
single pinned planning shape.

Python `reconcile` performs a fresh all-state read of labeled carriers, parses marked comments,
requires the comment author and embedded Producer to match the explicit trusted policy, validates
the revision digest, and reads the current Action Target. An open Target with the tracer bullet's
empty Prerequisite set is returned as one Ready Action with its Instruction, Target, Basis,
Producer provenance, interaction classification, and completion condition. The discovery label
is an index only: the Producer comment and current GitHub facts are authority, and no queue,
journal, snapshot, or local cache is created.
