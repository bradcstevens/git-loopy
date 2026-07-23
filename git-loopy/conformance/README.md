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
| `event-schema.json` | Additive compatibility schema 1 (fixture revision 1.1): exact type literals, per-Orchestrator Insight capabilities, payload contracts, null/zero and UTC/monotonic semantics, and stable envelope-first JSON serialization |
| `continuation-scenarios.json` | Continuation 1.0 native command framing, complete Action/interaction/condition schemas, canonical bounds, exact native publish results, trusted immutable-revision and index-repair cases, literal per-distribution capability scenarios, fail-closed operations, and scripted GitHub publish-to-reconcile workflows |
| `skill-consultation.json` | Per-Iteration consulted-skill detection, deduplication, ordering, and Summary rendering |
| `model-roster.json` | Canonical `model → accepted reasoning-effort` sets; its keys are the supported-model set (§14) |
| `routing-resolution.json` | Per-issue `task-type:` labels + `[routing]` config → resolved `(model, effort)` and whether it warns (§14) |
| `effort-gate.json` | Model + requested reasoning effort → gated result and whether it warns (§14) |
| `release-version.json` | Root Release version expectation, representative valid/invalid SemVer values, unavailable-authority scenarios, and source/package/publication drift cases |

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
semantic dispatch they describe. The shell adapter drives those same portable
JSON, completion-envelope, vocabulary, fingerprint, receipt, and atomic-failure
records through its public native `publish` command. The PowerShell adapter
drives the shared trusted publish-to-Reconciliation workflow through its public
native command and deterministic scripted GitHub transport.

`release-version.json` is independent of the Wrapper, Event, and Continuation
compatibility versions. `expected_release_version` mirrors the repository-root
`VERSION` authority for family adapters; `expected_python_distribution_version`
records only the normalized Python packaging representation of that same value.
The Python repository validator reads the authority plus the source and package
metadata copies without importing the Orchestrator:

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
native discovery adapters call their production discriminator, Checkpoint-message,
and exit-code seams from
[`shell/tests/test-orchestrator-conformance.sh`](../shell/tests/test-orchestrator-conformance.sh)
and
[`powershell/tests/test-orchestrator-conformance.ps1`](../powershell/tests/test-orchestrator-conformance.ps1).
Their Event-schema adapters call the production serialization and replay seams
from
[`shell/tests/test-event-conformance.sh`](../shell/tests/test-event-conformance.sh)
and
[`powershell/tests/test-event-conformance.ps1`](../powershell/tests/test-event-conformance.ps1).
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

Run them from the repository root:

```bash
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_conformance.py
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_continuation_scenarios.py
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_release_version.py
bash git-loopy/shell/tests/test-event-conformance.sh
bash git-loopy/shell/tests/test-orchestrator-conformance.sh
bash git-loopy/shell/tests/test-continuation-conformance.sh
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-event-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-orchestrator-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-continuation-conformance.ps1
```

To change the Wrapper contract, update the written contract and its version,
the affected fixture, and every Orchestrator adapter in the same change.
