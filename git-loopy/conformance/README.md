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
| `dashboard-insights.json` | Renderer-neutral Dashboard seam (fixture revision 1.1): normalized Event prefixes, injected clock/zone/config inputs, canonical Dashboard and drill-in inventory, Queue and Iteration-breakdown columns and scopes, placeholders, an SDK-backed and a native-Orchestrator unavailable-capability case, and expected semantic view models consumed by Python and the Rust Dashboard core |
| `continuation-scenarios.json` | Continuation 1.0 native command framing, complete Action/interaction/condition schemas, canonical bounds, exact native publish results, trusted immutable-revision and index-repair cases, literal per-distribution capability scenarios, fail-closed operations, and scripted GitHub publish-to-reconcile workflows |
| `skill-consultation.json` | Per-Iteration consulted-skill detection, deduplication, ordering, and Summary rendering |
| `skill-policy.json` | Closed-world **Skill policy** (§17): base-scope selection, explicit empty policy, exact environment replacement, Run overlays with disable-wins, deprecated legacy subtraction, Minimal fallback and its reason, the four validation failures, startup classification, and the redacted `wrapper.skill_policy.resolved` projection |
| `model-roster.json` | Canonical `model → accepted reasoning-effort` sets; its keys are the supported-model set (§14) |
| `routing-resolution.json` | Per-issue `task-type:` labels + `[routing]` config → resolved `(model, effort)` and whether it warns (§14) |
| `effort-gate.json` | Model + requested reasoning effort → gated result and whether it warns (§14) |
| `release-version.json` | Root Release version expectation, representative valid/invalid SemVer values, stable/prerelease publication classification, invalid tag scenarios, unavailable-authority scenarios, and source/runtime/package/publication drift cases |
| `tui-artifacts.json` | The published **TUI helper** artifact set: the pinned release toolchain, the seven Phase 2 targets with their release runners, cross container and package provisioning, and native/cross build kind, targets deferred *by name* rather than by absence, canonical archive/checksum/executable naming, the download URL one Release publishes them at, and the host aliases and selection cases an installer resolves its own artifact with |
| `release-trust.json` | The **platform-trust gate** a Release passes before publication: per-platform signing mechanism and the cargo-dist key that enables it, the credentials each mechanism reads, the protected and unprotected release environments and the credential-free jobs, evidence a platform *cannot* carry recorded by name and reason, the evidence each channel requires, and the stable/prerelease publication decisions including the marking the GitHub Release itself must carry |
| `homebrew-tap.json` | The **Homebrew channel**: the tap and formula identity, the four platforms Homebrew runs on and the artifact each installs, the three published targets it excludes *by name*, the stable-only publication decisions including the marking the Release itself must carry, and the version, URL, host, digest, coverage, and version-probe drift a formula is refused for |

Legacy decision fixtures carry `schema_version` and the Wrapper
`contract_version` they pin. The Continuation harness names every independent
version axis explicitly: fixture schema, Continuation contract, record format,
Wrapper contract, and Event schema. Fixture content is data only: do not add
host-language expressions, executable hooks, or implementation-specific
expected-value generation.

`tui-artifacts.json` is the one fixture whose consumers are not Orchestrators:
release automation builds exactly the target set it declares, and the shell and
PowerShell installers resolve their own artifact through its host aliases and
selection cases. Everything about a published helper that more than one of them
has to agree on — the toolchain pin, the target list, the archive and checksum
names — is stated once there, so a renamed artifact cannot leave one consumer
resolving a name that no longer exists. `git-loopy/tui/Cargo.toml` is what
actually builds; `git-loopy/python/tests/test_tui_release.py` fails when the two
disagree.

`release_download_url_template` is there for the same reason the names are: the
installers, the Homebrew tap, and the winget/Scoop manifests all download the
same bytes, and a channel that resolves its own URL is a channel that can
install a different Release. It is pinned against the helper manifest's
`repository`, so a fork or a rename cannot leave every channel downloading from
the old repository's Releases. The shell adapter is
[`shell/tests/test-tui-install.sh`](../shell/tests/test-tui-install.sh), which
drives the same `selection_cases` and installs a fake published Release over
`file://` — a real transfer, with no network.

Three of the seven targets cannot link on a bare runner, so each target also
declares the container it builds inside and the packages that container needs.
That provisioning is cargo-dist's answer rather than this repository's, so the
release pipeline proves it: `dist plan --output-format=json` is checked against
this fixture before a byte is compiled, and a plan that would build on another
runner, in another container, or under another toolchain fails the release
instead of the linker.

`release-trust.json` is the other half of that story: `tui-artifacts.json` says
*what* is published, and `release-trust.json` says what publishing it has to
prove. Signing happens inside `dist build`, because cargo-dist writes each
`.sha256` afterwards and a published checksum must be a checksum of the *signed*
artifact. Both of its signers degrade to a warning when their credentials are
absent — which is what keeps a pull request buildable, and exactly why the gate
proves a signature by unpacking the artifact and asking it, rather than by
trusting a step that did not fail. Evidence a platform cannot carry is recorded
by name and reason, the same way targets are deferred by name: an absent
requirement and an impossible one look identical from the outside, and only one
of them is a decision.

Each of its `publication_cases` also states the prerelease flag the GitHub
Release carries, alongside the version being published. The version decides
which evidence is *required*; the marking is what an operator, and every package
channel that resolves "the stable Release", actually sees. The unsigned Windows
allowance belongs to the prerelease channel alone, so a case where the two
disagree is a refusal in both directions rather than a preference for the
version string.

`homebrew-tap.json` is the third of that set, and the one furthest from the
bytes. A package channel is the only distribution path where nobody reads what
they installed: `brew install git-loopy-tui` resolves a URL and a digest that
release automation wrote into a formula, and an operator cannot tell a formula
naming the Release it claims from one naming a different build. So the formula is
*generated* from the completed Release and then read back by a separate gate —
version, per-platform artifact, trusted host, published digest, complete
coverage, and the `brew test` version probe are each proven independently, and
each drift is refused by its own name. The tap credential is not declared here:
`release-trust.json` is the pipeline's one credential registry, and a channel
that carried its own list could add one nothing reviewed.

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

The `event-schema.json` normalized rollup cases are production-seam cases in the
same sense. A case whose input is a list of `iterations` drives the Orchestrator's
stateful Iteration-lifecycle accumulator — the seam its own Run loop uses — rather
than a single rollup call, so per-issue first activation, retroactive fallback
binding, and cumulative Active time are pinned *across* Iterations. Each such case
is one Run: an adapter must reset the accumulator between cases, or per-issue
history leaks forward and the fixture silently pins case order instead of
behavior. Because the adapter feeds decoded fixture values straight to the seam,
these cases also pin that nested lifecycle timestamps are republished as RFC3339
UTC regardless of how a producer represented the instant.

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

`skill-policy.json` is a different fact from `skill-consultation.json`:
consultation is what one Iteration *used*, policy is what the whole Run *may
use*. Its `resolution_cases` drive the production resolver seam — the same one
Run preflight calls — so an adapter passes only by resolving, never by
restating; `startup_cases` drive the production startup classifier that decides
whether a Run offers a one-time migration; and `event_payload_cases` drive the
production redacted audit projection. Because Skill policy is Python-first, the
fixture names its own transition state: `native_transition.implemented` lists
the distributions that resolve a policy, and `native_transition.fail_closed`
lists the ones that must abort before source collection when they detect any
`native_transition.policy_surfaces` entry.

The shell adapter consumes that transition block directly: its conformance suite
drives `git_loopy_detect_skill_policy_surfaces` — the seam Run preflight calls —
and asserts the detected surfaces equal `policy_surfaces` in the fixture's own
order, so a surface added to the contract turns the port red rather than leaking
through it. Its boundary suite drives every surface through the real entrypoint
and proves the Run exits `1` before Pool collection and before the fake Copilot
process exists.

The PowerShell adapter reads the same block the same way: its conformance suite
drives `Get-GitLoopySkillPolicySurfaces` and its boundary suite drives every
surface through the real entrypoint. Both ports also assert their own name is in
`fail_closed` and absent from `implemented`, so the day either gains native
support the stale expectation fails loudly instead of passing by omission.

The Python reference adapter is
[`python/tests/test_conformance.py`](../python/tests/test_conformance.py). The
adapter drives Python's normalized Iteration-rollup seam and the production
Dashboard semantic projection through every fixture snapshot. The Rust Dashboard
core drives the *same* `dashboard-insights.json` snapshots through its production
reducer and projection from
[`tui/tests/dashboard_conformance.rs`](../tui/tests/dashboard_conformance.rs), so
the two renderers' semantics cannot diverge: `dashboard-insights.json` is the only
place a Dashboard decision is stated, and neither member is the other's oracle. The
Rust terminal rendering tests in
[`tui/tests/dashboard_render.rs`](../tui/tests/dashboard_render.rs) and
[`tui/tests/drill_in_render.rs`](../tui/tests/drill_in_render.rs) read their
expected cell values back out of the same fixture rather than restating them, so a
drawn band — on either screen — cannot disagree with the projection it draws. The
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
bash git-loopy/shell/tests/test-tui-install.sh
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-event-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-orchestrator-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-continuation-conformance.ps1
```

To change the Wrapper contract, update the written contract and its version,
the affected fixture, and every Orchestrator adapter in the same change.
