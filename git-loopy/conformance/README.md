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
| `exit-codes.json` | Clean, aborted, and usage-error process exits |
| `event-schema.json` | Event type literals and stable envelope-first JSON serialization |

Every file carries `schema_version` and `contract_version`. Fixture content is
data only: do not add host-language expressions, executable hooks, or
implementation-specific expected-value generation.

The Python reference adapter is
[`python/tests/test_conformance.py`](../python/tests/test_conformance.py). Run it
from the repository root:

```bash
uv run --project git-loopy/python pytest -q git-loopy/python/tests/test_conformance.py
```

To change the Wrapper contract, update the written contract and its version,
the affected fixture, and every Orchestrator adapter in the same change.
