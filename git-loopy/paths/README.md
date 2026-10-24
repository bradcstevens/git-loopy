# Language-path manifests

Each JSON file declares one Language path (ADR-0073, ADR-0083, ADR-0091).
Python is active; shell and PowerShell are dormant. There is no Rust path:
the Rust `git-loopy-tui` helper is a Python Path artifact.

## Fields (schema version 1)

| Field | Meaning |
| --- | --- |
| `key`, `state` | Path identity and `active` or `dormant` state |
| `release_authority` | Repository-relative authority `file`; Python's `copies` are the existing verified distribution-version sites. A dormant authority names its own manifest and the dotted `field` `baseline.release_version` |
| `tag_namespace` | Python inherits `vX.Y.Z` and its history; `<key>/vX.Y.Z` is reserved, unused while dormant |
| `contract_support` | Newest supported Wrapper contract version; support only advances |
| `event_schema_support` | Supported compatibility-schema integers, not Event-fixture revisions |
| `members` | Conformance member identities |
| `artifacts` | Owned published artifact identities; `git-loopy` is the Python distribution and `git-loopy-tui` its helper. Dormant paths publish no separate artifacts |
| `owned_files` | Repository-relative file patterns; `/**` includes all descendants |
| `gate_rows` | Exact loop names in `AGENTS.md`; commands still live only in that table |
| `baseline` | Dormant Release authority, source tag and peeled commit, and carried `owed_waivers`, each with its fixture filename and tracking issue number |

The shell and PowerShell entry points, Run-start Events, and helper installers
read their own `baseline.release_version`. They never use root `VERSION` for
runtime identity. Missing or malformed manifest identity fails explicitly; it
does not fall back to Python's version. The shell reader uses its existing `jq`
prerequisite even for `--version`.

Source-tree and tagged-archive verification compare each native entry point with
the authority named by its manifest's `release_authority.file` and dotted JSON
`field`, not a hardcoded baseline. The authority file must stay within the source
tree, and its selected value must be a Semantic Versioning string; missing or
malformed declarations, files or fields fail closed. Verification accepts both
`git-loopy <key> X.Y.Z` and the current plain `git-loopy X.Y.Z` from non-Python
entry points, with the version checked against that authority. Python's expected
Release and unqualified identity checks remain unchanged (ADR-0105, #1013).
Historical distributions without a manifest directory still prove their shared
Release; an incomplete or malformed manifest set never takes that legacy path.

Root `VERSION`, its verified copies and `conformance/release-version.json` remain
Python's authority. The dormant ports' repository Release-line writers retain
their existing behaviour for repositories they operate on; those writers do not
define the installed Orchestrator's identity. Only Python works this repository's
tracker.

This is the baseline/identity slice (#792), not the whole activation of PRD #791.
The deep loader and validation belong to #793. Support-scoped Conformance and
the register/selector rename from legacy `rust` to manifest member `tui`, Path
gate selection, and tracker exclusions land in the dependent tickets. Until
then, Integration and CI still run every existing feedback loop. No dormant
release workflow is introduced.

## Baseline audit

At the split, the latest published stable family Release was **v0.11.2**,
peeled commit **a0f912b57c222f2525238d37ae268b0adb4c764f**. The audit read
`VERSION`, the `docs/wrapper-contract.md` header, `event-schema.json`'s
compatibility schema, and every `waived_owed` row in `fixture-claims.json`
from that tag, not from the moving default branch:

| Path | Release | Contract support | Event schema | Carried owed waivers |
| --- | --- | --- | --- | --- |
| shell | 0.11.2 | 2.26 | 1 | `attempt-lifecycle.json`: #553; `status-endings.json`: #834 |
| powershell | 0.11.2 | 2.26 | 1 | `attempt-lifecycle.json`: #554; `status-endings.json`: #835 |

The tag's [source-release gate](https://github.com/bradcstevens/git-loopy/actions/runs/38326744225)
passed shell syntax and suites on macOS/Linux, and PowerShell suites on
macOS/Linux/Windows. These are baseline-commit results, not evidence inferred
from the split's later head. To reproduce the audit, inspect `git show
v0.11.2:<path>` for the four files above and the check runs for the peeled commit.

Both paths bind a serial Pickup and count Run-wide consecutive Strikes.
Neither implements an Attempt lifecycle or emits an Ending. These are
implementation debts, not missing test coverage or role waivers. #834 and #835
were created with `path:shell` and `path:powershell`, respectively, replacing
#732. Their issues stay open for resumption; #553 and #554 remain unchanged
here because #797 owns their label migration.

ADR-0098 withdraws ADR-0093's Sandbox exception to ADR-0091's no-new-debt rule.
`sandbox.json` was introduced at contract 2.35, above both dormant paths'
Contract support 2.26, so their register rows are `waived_out_of_scope` with no
tracking issue. Neither manifest's frozen Release, Contract support nor carried
`owed_waivers` changes. The fixture's cases and `distributions` stay unchanged;
a path that resumes owes a claim or waiver once its Contract support includes
2.35. Until then, both ports still launch Agents with `copilot --yolo -p`,
without Sandbox preflight, fail-closed enforcement or bypass denial. An operator
who needs a Sandbox uses the Python Runner.

No published helper exists at or below this baseline. A default dormant
installation therefore reports no eligible helper; the launcher remains usable
in plain mode, and `--no-tui` / `-NoTui` skips helper installation. A compatible
helper on `PATH` still attaches through Event-schema negotiation, with a warning
on Release drift. Dormancy changes no helper compatibility rule.
