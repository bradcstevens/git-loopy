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
| `dashboard-insights.json` | Baseline renderer-neutral Dashboard seam: normalized Event prefixes, injected clock/zone/config inputs, canonical Dashboard and drill-in inventory, Queue columns and scopes, placeholders, and expected semantic view models for future renderer #143 |
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
PowerShell also drives the shared immutable-revision trust, observation, DAG,
race, fork, quarantine, re-attestation, and `repair-index` scenarios through
native commands and its deterministic scripted GitHub transport.

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
adapter pins the Dashboard fixture's public semantic shape; producer and
production-reducer rollout remains in the downstream Dashboard issues. The
native discovery adapters call their production discriminator, Checkpoint-message,
and exit-code seams from
[`shell/tests/test-orchestrator-conformance.sh`](../shell/tests/test-orchestrator-conformance.sh)
and
[`powershell/tests/test-orchestrator-conformance.ps1`](../powershell/tests/test-orchestrator-conformance.ps1).
Their Event-schema adapters call the production serialization and replay seams from
[`shell/tests/test-event-conformance.sh`](../shell/tests/test-event-conformance.sh)
and
[`powershell/tests/test-event-conformance.ps1`](../powershell/tests/test-event-conformance.ps1).
The shell adapter also drives its native normalized Iteration-rollup seam from the shared case.
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
