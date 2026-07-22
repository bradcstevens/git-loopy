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

At the 1.0 foundation gate, Python, shell, and PowerShell advertise the GitHub Adapter but no
supported tracker operation. `publish`, `reconcile`, `record-dispatch-result`, `repair-index`,
terminal rendering, report mode, execute-frontier, and concurrent Dispatch are all unsupported.
Mode is `off`.

## 5. Event observations

Event schema 1.1 adds `wrapper.continuation.reconciled`,
`wrapper.continuation_dispatch.started`, `wrapper.continuation_dispatch.ended`, and
`wrapper.continuation.stopped`. Events are redacted observations only: they never establish
Producer authority, carry authoritative records, grant Dispatch, or contain runnable Instructions.

## 6. Native scenario harness

[`continuation-scenarios.json`](../git-loopy/conformance/continuation-scenarios.json) is the
language-neutral, data-only public-command harness. It independently declares fixture schema 1.0,
Continuation contract 1.0, record format 1, Wrapper contract 1.2, and Event schema 1.1.

Every family adapter reads the fixture directly and invokes its real native entrypoint. Request
objects are supplied through the declared stdin or file source. `$INPUT_FILE` is the fixture
harness's sole path placeholder. `github_script` is an ordered deterministic scripted-GitHub
transport; adapters fail on an unlisted call and compare observed calls with `github_calls`.
Fixture records contain data only—no host-language expression, executable hook, or duplicated
expected-value algorithm.

The foundation scenarios prove the capability manifest, request framing, explicit terminal
selection, fail-closed operations, and exit mapping without contacting GitHub or entering a Run.
Later semantic tickets extend the same harness rather than creating private command or transport
oracles.
