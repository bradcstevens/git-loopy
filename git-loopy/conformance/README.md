# Conformance Suite

These language-neutral JSON fixtures pin the pure decisions in the
[Wrapper contract](../../docs/wrapper-contract.md). Every Orchestrator must read
these files directly through a thin host-language adapter. An adapter may
translate fixture records into native values, but it must call the
Orchestrator's production decision seams rather than reproduce their logic.

| Fixture | Contract decision |
| --- | --- |
| `discriminator.json` | Required issue headings and optional parent metadata |
| `close-references.json` | Reference regex, line boundaries, deduplication, Pool whitelist, and issues-only closure |
| `progress-strikes.json` | Agent commits, closures, Checkpoints, PR advances, Strike resets, and abort thresholds |
| `checkpoint-messages.json` | Runner-authored Checkpoint subject/body/trailer per Active issue, its close-keyword freedom, and its detectability |
| `exit-codes.json` | Clean, aborted, and usage-error process exits |
| `event-schema.json` | Additive compatibility schema 1 (fixture revision 1.1): exact type literals, exact Run-start Release identity, per-Orchestrator Insight capabilities, production-seam normalized rollup cases, payload contracts, null/zero and UTC/monotonic semantics, and stable envelope-first JSON serialization |
| `dashboard-insights.json` | Renderer-neutral Dashboard seam (fixture revision 1.1): normalized Event prefixes, injected clock/zone/config inputs, canonical Dashboard and drill-in inventory, Queue and Iteration-breakdown columns and scopes, placeholders, an SDK-backed and a native-Orchestrator unavailable-capability case, and expected semantic view models consumed by Python and future renderer #143 |
| `continuation-scenarios.json` | Continuation 1.0 native command framing, complete Action/interaction/condition schemas, canonical bounds, exact native publish results, trusted immutable-revision and index-repair cases, literal per-distribution capability scenarios, fail-closed operations, and scripted GitHub publish-to-reconcile workflows |
| `skill-consultation.json` | Per-Iteration consulted-skill detection, deduplication, ordering, and Summary rendering |
| `model-roster.json` | Canonical `model → accepted reasoning-effort` sets; its keys are the supported-model set (§14) |
| `routing-resolution.json` | Per-issue `task-type:` labels + `[routing]` config → resolved `(model, effort)` and whether it warns (§14) |
| `effort-gate.json` | Model + requested reasoning effort → gated result and whether it warns (§14) |
| `release-version.json` | Root Release version expectation, representative valid/invalid SemVer values, stable/prerelease publication classification, invalid tag scenarios, unavailable-authority scenarios, and source/runtime/package/publication drift cases |

Legacy decision fixtures carry `schema_version` and the Wrapper
`contract_version` they pin. The Continuation harness names every independent
version axis explicitly: fixture schema, Continuation contract, record format,
Wrapper contract, and Event schema. Fixture content is data only: do not add
host-language expressions, executable hooks, or implementation-specific
expected-value generation.

Fixture schema 1.1 adds distribution selectors, literal capability scenarios, a
cross-host transport probe, and multi-command workflows sharing one ordered
scripted-GitHub transport. An adapter runs only records naming its distribution,
must consume every scripted call, and must reject unlisted calls.

Fixture schema 1.2 adds pinned completion-record vocabularies and physical JSON
bounds. Fixture schema 1.3 adds complete Action-classification,
interaction-evidence, and condition schemas; exact Python native-publish stdout
and stderr; raw-segment stress requests; and literal request templates with
RFC 6902 `add`, `remove`, and `replace` patches for invalid completion cases.
Adapters may materialize those generic fixture records but must not recreate the
semantic dispatch they describe. The shell and PowerShell adapters drive the
same portable JSON, completion-envelope, vocabulary, fingerprint, receipt, and
atomic-failure records — including the raw-segment stress requests and the
byte-order-mark rejection — through their public native `publish` command.
Shell and PowerShell also drive the shared immutable-revision trust,
observation, DAG, race, fork, quarantine, re-attestation, and `repair-index`
scenarios through native commands and their deterministic scripted GitHub
transports. Shell and PowerShell also drive the live prospective Reconciliation
fixtures for typed stable reads, every v1 condition, Ready/Blocked derivation,
smallest-scope uncertainty, equivalent-claim provenance, incompatible Action
semantics, and Prerequisite cycles; each port's production-boundary probe
traverses complete issue and comment pagination without treating the discovery
label as authority, and must reproduce the same shared Reconciliation result
once the extra pages carry only unindexed carriers and ordinary discussion.

Fixture schema 1.4 adds the prospective-projection vocabulary. `revision_protocol.diagnostic_codes`
pins the family's complete `reconcile` diagnostic vocabulary — the union across every distribution,
not one member's subset — so emitting an unregistered code is a fixture change rather than a silent
addition. Scenarios may pin the `retirements`, `outcomes`, `delta`, and `handoff_reference` result
fields and the exact Continuation view order. Ordering scenarios name every distribution because the
ordering rule is ungated; scenarios exercising the gated request fields name only distributions
advertising `prospective_projection`.

Every `reconcile` result pins `retirements`, because the key is always emitted and its absence
therefore never carries meaning. A distribution without `prospective_projection` pins it empty. For
distributions that do project receipts the `retirements_require_revision_protocol` diagnostic
distinguishes "nothing was retired" from "not computed here"; for the rest the capability manifest
is the discriminator, since they never emit that diagnostic.

`retirement-bearing-record-is-readable-without-prospective-projection` replays the same world as the
Python retirement scenario through shell and PowerShell. It pins the split between reading and
authoring receipts: both must project the successor's Action and leave `retirements` empty, and
neither may quarantine the record. Without it, a distribution that rejected `completion.retirements`
as an unknown field would silently resurface the retired predecessor as live guidance.

`malformed-retirement-receipt-is-quarantined-in-every-distribution` is the matching fail-closed
control, shared by all three. Reading a receipt is not the same as waving it through: a receipt with
an unsupported `reason` must quarantine its revision as `invalid_revision` with an identical
diagnostic message everywhere, so accepting the field can never become accepting anything named
`retirements`.

The retirement, HITL-stop, genuine-completion, out-of-order-completion, and Handoff families are
scoped `["python"]` rather than shared. That gap is deliberate and capability-derived, not an
oversight: shell and PowerShell advertise `prospective_projection: false` and
`terminal_rendering: false`, never project `outcomes` or a `complete` status, and hold no retirement
vocabulary at all, so they cannot run these scenarios as written. Companion scenarios assert that
gap instead of merely declaring it — shell and PowerShell fail closed on `handoff` and
`previous_actions` with `unsupported_operation`, so a distribution that silently started accepting
either would break the fixture.

`release-version.json` is independent of the Wrapper, Event, and Continuation
compatibility versions. `expected_release_version` mirrors the repository-root
`VERSION` authority for family adapters; `expected_python_distribution_version`
records only the normalized Python packaging representation of that same value.
The Python repository validator reads the authority plus the source, packaged
runtime, and package metadata copies without importing the Orchestrator. The
Python runtime copy and the shell and PowerShell distributions' root authority
are the fail-closed inputs for each Orchestrator's `git-loopy --version`,
Run-start Events, and native Continuation capability manifest:

```bash
uv run --project git-loopy/python --all-extras \
  python -m git_loopy.release_version --repository-root .
```

A skill is **consulted** once per Iteration when either an explicit `skill`
tool call names it or any tool-call argument references
`.copilot/skills/<name>/SKILL.md`. Consulted names are deduplicated and sorted;
catalog globs that do not identify a concrete `<name>` do not count.

The Python reference adapter is
[`python/tests/test_conformance.py`](../python/tests/test_conformance.py). The
adapter drives Python's normalized Iteration-rollup seam and the production
Dashboard semantic projection through every fixture snapshot. The
native discovery adapters call their production discriminator, Checkpoint-message,
and exit-code seams from
[`shell/tests/test-orchestrator-conformance.sh`](../shell/tests/test-orchestrator-conformance.sh)
and
[`powershell/tests/test-orchestrator-conformance.ps1`](../powershell/tests/test-orchestrator-conformance.ps1).
Their Event-schema adapters call the production serialization and replay seams from
[`shell/tests/test-event-conformance.sh`](../shell/tests/test-event-conformance.sh)
and
[`powershell/tests/test-event-conformance.ps1`](../powershell/tests/test-event-conformance.ps1).
Each family adapter drives its native normalized Iteration-rollup seam from its
orchestrator-scoped shared cases.
The native Continuation adapters invoke each real public entrypoint from
[`python/tests/test_continuation_scenarios.py`](../python/tests/test_continuation_scenarios.py),
[`shell/tests/test-continuation-conformance.sh`](../shell/tests/test-continuation-conformance.sh),
and
[`powershell/tests/test-continuation-conformance.ps1`](../powershell/tests/test-continuation-conformance.ps1).

The Python reference adapter additionally pins the phase-3 per-issue routing
decisions (Wrapper contract §14): it drives `routing-resolution.json` and
`effort-gate.json` through the production `resolve_iteration_model` and
`gate_reasoning_effort` seams and asserts its in-language model roster equals
`model-roster.json`. Native ports do not implement routing yet, so these three
fixtures are Python-adapter-only.

The family-level terminal Release adapter
[`python/tests/test_release_identity_conformance.py`](../python/tests/test_release_identity_conformance.py)
invokes the real Python, shell, and PowerShell entrypoints in one seam. It proves exact early
`--version` output, no Run-preflight calls or artifacts, and explicit failure for malformed,
non-UTF-8, or unavailable Release metadata.

The source-release verifier
[`python/git_loopy/source_release.py`](../python/git_loopy/source_release.py)
uses the same fixture to reject lightweight or mismatched tags, missing explicit
Release-version bumps, missing edited notes, metadata drift, and drift in any
real Orchestrator `--version` or Continuation capability output. It generates
and verifies the tagged source archive before
[`source-release.yml`](../../.github/workflows/source-release.yml) creates a
stable or prerelease GitHub Release. Publication has no custom artifact or
package-channel upload.

Run them from the repository root:

```bash
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_conformance.py
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_continuation_scenarios.py
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_release_version.py
uv run --project git-loopy/python --all-extras \
  pytest -q git-loopy/python/tests/test_source_release.py \
  git-loopy/python/tests/test_source_release_workflow.py
uv run --project git-loopy/python --all-extras \
  pytest -q git-loopy/python/tests/test_release_identity_conformance.py
bash git-loopy/shell/tests/test-event-conformance.sh
bash git-loopy/shell/tests/test-orchestrator-conformance.sh
bash git-loopy/shell/tests/test-continuation-conformance.sh
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-event-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-orchestrator-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-continuation-conformance.ps1
```

To change the Wrapper contract, update the written contract and its version,
the affected fixture, and every Orchestrator adapter in the same change.
