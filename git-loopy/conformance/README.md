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
| `event-schema.json` | Event type literals and stable envelope-first JSON serialization |
| `model-roster.json` | Canonical `model → accepted reasoning-effort` sets; its keys are the supported-model set (§14) |
| `routing-resolution.json` | Per-issue `task-type:` labels + `[routing]` config → resolved `(model, effort)` and whether it warns (§14) |
| `effort-gate.json` | Model + requested reasoning effort → gated result and whether it warns (§14) |

Every file carries `schema_version` and `contract_version`. Fixture content is
data only: do not add host-language expressions, executable hooks, or
implementation-specific expected-value generation.

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

The Python reference adapter additionally pins the phase-3 per-issue routing
decisions (Wrapper contract §14): it drives `routing-resolution.json` and
`effort-gate.json` through the production `resolve_iteration_model` and
`gate_reasoning_effort` seams and asserts its in-language model roster equals
`model-roster.json`. Native ports do not implement routing yet, so these three
fixtures are Python-adapter-only.

Run them from the repository root:

```bash
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_conformance.py
bash git-loopy/shell/tests/test-event-conformance.sh
bash git-loopy/shell/tests/test-orchestrator-conformance.sh
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-event-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-orchestrator-conformance.ps1
```

To change the Wrapper contract, update the written contract and its version,
the affected fixture, and every Orchestrator adapter in the same change.
