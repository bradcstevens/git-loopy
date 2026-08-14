# Conformance Suite

These language-neutral JSON fixtures pin the pure decisions in the
[Wrapper contract](../../docs/wrapper-contract.md). Every Orchestrator must read
these files directly through a thin host-language adapter. An adapter may
translate fixture records into native values, but it must call the
Orchestrator's production decision seams rather than reproduce their logic.

| Fixture | Contract decision |
| --- | --- |
| `discriminator.json` | Required issue headings, optional parent metadata, and the Pool-exclusion reason each rejection carries |
| `issue-ordering.json` | The **total order** over eligible issues (§3.2): `priority` rank ahead of age, `created_at` ascending, issue number as the tie-break that makes the order total, the narrow timestamp grammar and the `absent`/`malformed` defect an undated issue is reported with, and the head of the order each case selects — plus the **read schedule** (§2.1) the order is computed over: the shared first ask and ceiling, the `complete`/`continue`/`incomplete` decision each page forces, the walk each backlog produces, and the page boundary falling mid-order that a member stopping one page early would select the wrong head from. Eligibility is deliberately absent — that is `discriminator.json`'s decision, and a second home for it is a second place it can drift |
| `close-references.json` | Reference regex, line boundaries, deduplication, Pool whitelist, and issues-only closure |
| `progress-strikes.json` | Agent commits, closures, Checkpoints, PR advances, Strike resets, and abort thresholds |
| `checkpoint-messages.json` | Runner-authored Checkpoint subject/body/trailer per Active issue, its close-keyword freedom, and its detectability |
| `exit-codes.json` | Clean, aborted, and usage-error process exits |
| `event-schema.json` | Additive compatibility schema 1 (fixture revision 1.1): exact type literals, exact Run-start Release identity, per-Orchestrator Insight and **Parallel mode** capability manifests, production-seam normalized rollup cases, payload contracts, the rolling-dispatch **Lane contribution** identity and lifecycle vocabulary, whole ordered rolling Event streams every member serializes through its own seam, null/zero and UTC/monotonic semantics, and stable envelope-first JSON serialization |
| `dashboard-insights.json` | Renderer-neutral Dashboard seam (fixture revision 1.2): normalized Event prefixes, injected clock/zone/config inputs, canonical Dashboard and drill-in inventory, per-band projection field inventory and per-column field mapping, Queue and Iteration-breakdown columns and scopes, placeholders, an SDK-backed and a native-Orchestrator unavailable-capability case, the activation `binding_source` vocabulary, and expected semantic view models consumed by Python and the Rust Dashboard core |
| `continuation-scenarios.json` | Continuation 1.0 native command framing, complete Action/interaction/condition schemas, canonical bounds, exact native publish results, trusted immutable-revision and index-repair cases, literal per-distribution capability scenarios, fail-closed operations, and scripted GitHub publish-to-reconcile workflows, and the fixed-frontier Automation vocabulary, safety case, eligibility, stop, and Dispatch-evidence scenarios |
| `skill-consultation.json` | Per-Iteration consulted-skill detection, deduplication, ordering, and Summary rendering |
| `skill-policy.json` | Closed-world **Skill policy** (§17): base-scope selection, explicit empty policy, exact environment replacement, Run overlays with disable-wins, deprecated legacy subtraction, Minimal fallback and its reason, the four validation failures, startup classification, and the redacted `wrapper.skill_policy.resolved` projection |
| `model-roster.json` | Canonical `model → accepted reasoning-effort` sets; its keys are the supported-model set (§14) |
| `routing-resolution.json` | The closed **Task type** taxonomy (`task_type_taxonomy`) and the refusals an unknown key meets — suppression does not excuse one, and matching is exact — plus per-issue `task-type:` labels + `[routing]` config → resolved `(model, effort)` and whether it warns, plus the **Measured routing** precedence cases — CLI flag > env > project > global > measured > built-in default — each declaring the synthetic roster it runs against, including a `provisional` measured entry (a pair in force that nobody measured) and the tier it is reported under (§14) |
| `effort-gate.json` | Model + requested reasoning effort → gated result and whether it warns (§14) |
| `calibration-search.json` | The **Calibration** search: its own synthetic roster and the five-of-five promotion rule declared rather than inferred, then the cheapest-first walk, unanimity, early rung abandonment, the equal-price tie-break, both the **AI Credit** and the wall-clock ceiling, an unreported Consumption latching credits to unknown, an interrupted and an exhausted walk, a Proving set too thin to promote anything, and the newest-first Proving-task draw every rung measures |
| `release-version.json` | Root Release version expectation, representative valid/invalid SemVer values, stable/prerelease publication classification, invalid tag scenarios, unavailable-authority scenarios, and source/runtime/package/publication drift cases |
| `tui-artifacts.json` | The published **TUI helper** artifact set: the pinned release toolchain, the seven Phase 2 targets with their release runners, cross container and package provisioning, and native/cross build kind, targets deferred *by name* rather than by absence, canonical archive/checksum/executable naming, the download URL one Release publishes them at, and the host aliases and selection cases an installer resolves its own artifact with |
| `release-trust.json` | The **platform-trust gate** a Release passes before publication: per-platform signing mechanism and the cargo-dist key that enables it, the credentials each mechanism reads, the protected and unprotected release environments and the credential-free jobs, evidence a platform *cannot* carry recorded by name and reason, the evidence each channel requires, and the stable/prerelease publication decisions including the marking the GitHub Release itself must carry |
| `homebrew-tap.json` | The **Homebrew channel**: the tap and formula identity, the four platforms Homebrew runs on and the artifact each installs, the three published targets it excludes *by name*, the stable-only publication decisions including the marking the Release itself must carry, and the version, URL, host, digest, coverage, and version-probe drift a formula is refused for |
| `windows-channels.json` | The **winget and Scoop channels**: the package identity and committed paths each writes, the one published target a Windows package manager runs and the six it excludes *by name*, the claims neither format can carry recorded *by name and reason*, the stable-only publication decisions, the trust-receipt defects that keep an unsigned or unattributable artifact out of both channels, and the version, identifier, URL, host, digest, publisher, and version-probe drift committed metadata is refused for |

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

`windows-channels.json` is the fourth, and it holds **two** channels rather than
one because winget and Scoop install the same single artifact — the signed
`x86_64-pc-windows-msvc` archive — and differ only in the format they write it
down in. Splitting them would give one fact two homes that could disagree
without either failing.

Three things distinguish it from the tap fixture. It records a **signing
identity**: on Windows an operator is shown a publisher rather than a digest, so
both channels read that artifact's own `.trust.json` receipt before writing
anything, and the required evidence is read from `release-trust.json` rather than
restated — a second list is a second place to relax it. It records **what each
format cannot carry**, by name and reason, because the two ecosystems are not
symmetric: Scoop has no publisher field, winget has no post-install hook, and
left absent each channel would quietly be held to the weaker bar of whichever
format has fewer fields. And its gate finishes with **canonical equality** —
each committed file must be byte-for-byte the text this Release generates — so
reading the claims is what earns each drift its own refusal rather than what
makes the gate sound.

Canonical equality proves the files the policy names, and those are the files
that reach operators only if nothing else does. A package manager resolves a
package from its *directory*, so each channel also declares the directories it
exclusively owns: winget's version directory holds that package's manifests and
nothing else, and anything found there the gate did not read is refused by name.
Scoop declares none, because its bucket directory holds every other package in
it — a channel that claimed its neighbours' directory would refuse packages it
has nothing to do with. Each channel also names the repository its pull request
is opened against, which for winget is the community repository its checkout is
a fork of rather than the checkout itself.

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

`malformed-retirement-receipt-is-quarantined-in-every-distribution` is the matching fail-closed
control, shared by all three. Reading a receipt is not the same as waving it through: a receipt with
an unsupported `reason` must quarantine its revision as `invalid_revision` with an identical
diagnostic message everywhere, so accepting the field can never become accepting anything named
`retirements`.

The retirement, HITL-stop, genuine-completion, out-of-order-completion, refresh-delta, Workstream
outcome, Handoff, and terminal-rendering families no longer name a subset of the family. Every
distribution now advertises `prospective_projection` and `terminal_rendering`, so the scenarios that
used to be scoped to whichever members held the capability are shared, and the companion scenarios
that asserted the gap — the `handoff` and `previous_actions` `unsupported_operation` controls, and
the read-without-authoring split in
`retirement-bearing-record-is-readable-without-prospective-projection` — are gone with it. They
asserted the absence of what every member now implements, and no member is left to demonstrate them.
The §8 rules they encoded stay written down in the contract, so a fourth distribution arriving
without the capability still knows what failing closed means.

`retired-occurrence-cannot-resurrect-later-in-the-chain` is the ancestor-chain control for the
distributions that do project receipts. A revision that re-declares an Action identity its own
ancestry proved retired taints itself and reports `retired_occurrence_resurrected`, so guidance
falls back to the last untainted head. Enforcing this only against the immediate parents would let
a retired occurrence reappear one revision later, which is exactly what having no tombstone ledger
must not cost.

`resurrection-keeps-coverage-open-so-completion-is-not-claimed` is its safety twin. The tainted
head falls back to a terminal ancestor carrying a satisfied `complete` outcome and no live Action —
every surface ingredient of a finished project. Because an unresolved resurrection means coverage is
not closed, the correct projection is `waiting`, not `complete`. A distribution that omits
`retired_occurrence_resurrected` from its coverage-uncertainty codes passes every other retirement
scenario and still reports a Workstream terminal on evidence it has just contradicted.

`supersession-replacement-must-repeat-the-retired-operation` pins the one receipt reason that names
its own successor. A `supersession` replacement must repeat the retired operation — same Workstream
Anchor, Action kind, and durable Target — under a new `occurrence`. Here the named replacement is a
live but unrelated Action, so the receipt proves nothing and surfaces as
`invalid_retirement_receipt`. Checking only that the replacement is live and distinct would let any
Producer retire any Action by pointing at whatever it happened to publish alongside it.

`terminal-remainder-is-bounded-with-truthful-hidden-counts` covers the remainder groups that the
one-primary-Action scenarios never reach. Six Ready Actions leave a five-strong remainder, of which
at most three rows may be rendered; the heading states both the full count and how many rows were
actually withheld. It is a byte-exact case because "bounded" and "truthful" are claims about the
rendered text, not about the machine projection behind it.

`label-indexed-projects-workstream-outcomes` and
`label-indexed-retirement-receipts-are-gated-not-silently-empty` cover the lineage-free discovery
path, which the `revision_protocol: true` scenarios skip entirely. Label-indexed discovery still
projects Workstream outcomes, but it can neither prove nor project a Retirement — so a record
carrying receipts must raise `retirements_require_revision_protocol` and name the revisions left
unevaluated. Since `retirements` is always emitted, that diagnostic is the only thing distinguishing
"nothing was retired" from "not computed on this path".

`handoff-reference-must-be-a-non-empty-string` guards the request validators the
prospective-projection families reach. A blank or whitespace-only identity, reference, or note is a
typed `invalid_request`, not an accepted value that quietly renders as an empty line of human
guidance.

`reconcile-terminal-preserves-invalid-request-{python,shell,powershell}` pin that a terminal-mode
failure still writes its typed machine JSON to stdout and exits 1 — `--terminal` selects a rendering,
not a second error channel. They are a `family-local-detail` variant group rather than one shared
scenario because the three families do not yet agree on which `invalid_request` message an empty
request produces, and these scenarios are byte-exact. The exit code and the `invalid_request` code
are contract and the group asserts all three agree on them; only the message is family-local.

`reconcile-reads-a-lineage-record-from-the-label-index` pins the label-indexed path against a record
carrying `parents`. A revision identity over a lineage record is a digest of `{completion, parents}`,
not of the completion alone, so a reader that recomputed it the flat way silently discards every
lineage record it finds through the label index — reporting zero Producer revisions rather than a
rejection. It is shared by all three distributions: shell carried the same defect on its own
label-indexed reader — tracked by #300 — until that reader started going through the same record
parser and Action derivation as the immutable-revision path, and the scenario is scoped to shell
here because it now passes rather than because the gap was declared closed.

`reconcile-terminal-bounds-the-ready-and-blocked-remainder` is the only scenario that exercises the
terminal renderer's bounding arithmetic. A seven-Action projection produces
`Ready (4 more, 1 hidden)`, the expand line, and a separate `Blocked (2 more, 0 hidden)` group, so a
renderer that printed the whole remainder — or miscounted what it withheld — fails here rather than
on an operator's terminal.

`reconcile-projects-the-refresh-delta` pins all three delta groups at once — five `added`, one
`retired`, one `changed` — because a delta that reported only the groups it happened to populate
would still look correct against a world where two of them are empty.

Fixture schema 1.5 adds the fixed-frontier Automation vocabulary. The `automation` group pins the
safety-case contract version, the assumption, retry, and Instruction-mode kinds, the ineligibility
and report-only reasons, the locked stop precedence, and the two Dispatch-evidence classes and their
comment marker. `automation-binds-one-dispatch`,
`automation-refuses-an-afk-claim-without-a-safety-case`,
`automation-quarantines-dispatched-semantics`,
`record-dispatch-result-records-a-safety-case-violation`, and
`record-dispatch-result-refuses-a-malformed-request-from-file` pin the authorization, the stop, the
quarantine, the exact Dispatch-evidence digest, and the request framing byte for byte across every
family member. They are scoped `["python", "shell", "powershell"]`: all three distributions now
advertise `fixed_frontier_authorization`, so §9 is shared contract rather than a capability split.

`record-dispatch-result-fails-closed-from-file` was deleted rather than rescoped. It asserted the
absence of what those five scenarios add, and no distribution fails closed on
`record-dispatch-result` any more, so there is no family member left that could demonstrate it.
Rescoping it to nobody would have left a scenario the suites silently never run.

Two further cases pin §9 against the *label-indexed* Reconciliation path, where every other
Automation case runs. Both were unreachable while a distribution derived
Actions differently on that path, and both are eyes the fixture did not previously have.

`automation-refuses-to-authorize-beside-a-guidance-fault` publishes two trusted Producers that
disagree about the same Action occurrence. The conflicted fragment never reaches `actions` at all,
so the world looks empty and healthy — which is exactly why the case is needed: it is the only
scenario anywhere proving that `action_conflict` reaches the *stop* rather than being a diagnostic
a Run may authorize past. A derivation that silently picked one of the two claims would return an
`authorization` here and pass every other Automation case.

The `automation-blocked-frontier-member-becomes-selectable` workflow is the §8/§9 hinge that no
single scenario can express: an initially Blocked member of the frozen frontier becoming selectable
once its Prerequisite is satisfied. Its first command pins `readiness: "Blocked"`,
`unsatisfied_prerequisites`, the `not-ready` ineligibility and the `awaiting-prerequisites` stop
with its `condition`; its second replays that exact frozen frontier and prior scope over a closed
Prerequisite and pins the authorization. Readiness may move within a Run; identity, semantics, and
authority may not, and only two commands over one transport can show the difference.

Thirty-seven further scenarios carry §9 the rest of the way, because a distribution that agrees
with the oracle on six cases and was never asked about the others is not
contract-identical — it is untested. They are grouped by the thing they can each get wrong alone:

- **Stop precedence.** Exactly one stop is returned and the first matching reason wins, which is
  only observable when more than one could match. `automation-reports-a-human-boundary-ahead-of-a-
  blocker` publishes an Action that is both HITL-required and Blocked and pins `human-boundary`
  over `awaiting-prerequisites` — the barrier a person can act on, not the more numerous one.
  `automation-stops-on-awaiting-prerequisites` pins the Blocked case alone,
  `automation-stops-on-a-drained-frontier` the exhausted one, and
  `automation-reports-workstreams-terminal-as-the-only-completion` the single reason whose
  disposition is `complete`.
- **Grants.** `automation-intersects-global-and-project-ceilings` proves a grant only one ceiling
  offers is not a grant; `automation-applies-a-ceiling-denial` and
  `automation-applies-a-runtime-revocation` prove either narrowing removes a shared one; and
  `automation-refuses-an-effect-outside-the-grant` proves the *safety case's* declared effects are
  what the grant is measured against, not the Action's Target.
- **Eligibility.** `automation-satisfies-every-requirement-kind` exercises all six requirement
  kinds — access, capability, command, evaluator, policy, skill — through one Action, and
  `automation-reports-an-unsatisfied-evaluator-requirement` and its policy twin prove an unmet one
  is typed `performer-ineligible` rather than a failure.
  `automation-matches-requirements-by-exact-name` pins that a name differing only in case is a
  different requirement, and
  `automation-refuses-an-instruction-mode-the-performer-cannot-run` pins the same for the
  Instruction mode a Performer must positively declare.
  `automation-fails-closed-on-an-interactive-performer` is the other half: an interactive posture
  is a safety-critical semantic the command refuses outright, not an ineligibility it reports.
- **The frozen frontier.** `automation-excludes-work-outside-frozen-coverage` proves coverage
  bounds the frontier itself; `automation-keeps-changed-semantics-report-only` and
  `automation-keeps-a-newly-produced-action-report-only` prove the two report-only reasons;
  `automation-binds-the-second-frontier-member-after-the-first` proves independent members drain
  serially; and `automation-scope-can-only-narrow-within-one-run` and
  `automation-narrows-a-prior-scope-further` prove a replayed freeze carries its authority with it
  — narrowing applies immediately, widening is refused.
- **Dispatch evidence.** `record-dispatch-result-records-uncertain-effect-state` and
  `automation-stops-on-uncertain-effect-state` pin the second locked class end to end, and
  `record-dispatch-result-refuses-an-unlocked-evidence-class` pins that an ordinary execution
  outcome is not a third one. `record-dispatch-result-requires-its-own-performer`,
  `automation-ignores-evidence-its-author-did-not-perform`, and
  `automation-ignores-a-malformed-evidence-record` pin the write-side and read-side halves of the
  same binding: anyone with write access can leave a comment, so a record narrows authority only
  when its author is the Performer it names and the whole record is there.
  `automation-clears-quarantine-for-a-corrected-occurrence` proves evidence names one semantics
  rather than one identity forever.
- **The safety case as published data.** `publish-accepts-a-positive-versioned-afk-safety-case` and
  `publish-refuses-a-safety-case-below-contract-1-2` are the version rule; the three
  `publish-refuses-*` cases beside them pin the closed assumption and retry vocabularies and the
  requirement that the case restate the exact Instruction, Target, and completion condition it
  justifies, so a later Instruction change invalidates the argument instead of inheriting it.
- **Ordering.** `automation-orders-grants-by-code-point`,
  `automation-orders-denials-by-code-point`, `automation-orders-report-only-successors`, and
  `automation-orders-secondary-barriers` order hyphen-, underscore-, and case-distinct values that
  a culture-aware comparison reorders and an ordinal one does not. Ordering is contract, and it is
  the failure this family has already shipped once.

All of the above are now scoped `["python", "powershell", "shell"]`. That is the point of having
generated them from the oracle rather than from one port's tests: they were written before shell
implemented §9, so widening them was a measurement rather than a transcription.

Ten `publish-refuses-a-…-strands-a-durable-transition` scenarios are the one §9-adjacent group
shell did not merely have to pass. The durable Transition has already happened when `publish`
starts, so a failed index label, a failed carrier index edit, a failed append, an author that is
not the completion Producer, and a reread that does not match the append all strand exactly the
same evidence — and they do so whether or not the request carried an `observation`.
`post-transition-publication-failure-requires-repair` and `append-reread-mismatch-requires-repair`
pinned two of those five steps and only on the revision-protocol path, so a distribution could
report `github_error` or `invalid_request` on the other three, or on the whole atomic-root path,
and still pass: it would be telling an unattended Runner to retry a write that can never succeed,
and telling an operator nothing needs repairing. Shell did exactly that — `github_error` for the
label and the index edit on *both* paths, and `github_error`/`invalid_request` for the append, the
author, and the reread on the atomic-root path.

The five failures are pinned once per discovery path, `whose-index-label-`, `whose-index-`,
`whose-append-`, `whose-author-` and `whose-reread-`, with the `label-indexed-` prefix naming the
atomic-root twin. Each pair is byte-identical apart from the request's `observation` and the three
authorization calls the revision protocol adds, which is the whole claim. The author pair also pins
the *call sequence*: the reread is issued before the appended author is judged, because both are
the same post-transition write and a distribution that short-circuits leaves the operator one call
short of the evidence the others record.

Three `publish-repairs-…-revision-<family>` groups pin the sixth post-transition failure, and they
are narrowed for a reason the other five are not. A `gh` call that *succeeds* but answers with
something that is not a comment object leaves the same stranded write, so the code is
`repair_required` in every family member — but the human-readable detail is the transport's own
parse diagnostic, and Python's, PowerShell's and shell's decoders cannot produce the same bytes.
`github_error` and post-transition transport detail are family-local prose throughout this fixture;
the error *code* is the contract. So each group asks *every* distribution the same question and lets
each record its own decoder's wording: an undecodable append, an undecodable reread, and a
`gh` call that returns a decodable body that is not an object at all. That last group is why the
grouping is enforced rather than described. Only the shell member of the undecodable pairs existed,
and PowerShell had never been asked the non-object question — given a JSON array it indexed `["id"]`
before checking the shape and threw `Cannot convert value "id" to type "System.Int32"` past every
typed handler, returning no typed result at all where Python and shell returned `repair_required`.

## Fixture schema 1.6 — the capability-coverage gate

A scenario narrowed to a subset of the family is a question the rest of the family is never asked.
Twenty-seven records are narrowed, and until 1.6 nothing anywhere said why: a distribution's
advertised `optional_capabilities` manifest and the per-scenario `distributions` arrays were two
hand-maintained lists kept in sync by author discipline. The PowerShell crash above hid behind
exactly that gap for a full release, and so did a second defect — `maximum-depth-request-is-accepted`
was scoped `["python"]`, and when it was widened shell rejected a request at exactly the maximum
nesting depth 16 that Python and PowerShell accept.

`capability_coverage` closes it as data. Every record whose `distributions` is a proper subset of
the family MUST appear in `scoped_records` under one of three closed reasons:

- **`manifest-identity`** — the three `capabilities-<family>` scenarios. Each asserts one
  distribution's own manifest bytes, so it is intrinsically per-family.
- **`capability-absent`** — the scenario exercises an optional capability. It names the
  `capability` key and the `advertises` boolean, and its `distributions` MUST equal *exactly* the
  set of distributions whose advertised manifest carries that key with that value. Scoping stops
  being a hand-maintained list and becomes **derived** from what each distribution claims.
- **`family-local-detail`** — one input asked of every family, each member recording its own
  decoder's prose. It names the `operation` and a `variant_group`; the group's members must be
  pairwise disjoint, their union must equal the set of distributions advertising that operation,
  and they must agree on `arguments`, `expected.exit_code`, and the pinned error `code`. Only the
  human-readable detail may differ.

The manifests the gate measures against are read from the fixture's own `capabilities-<family>`
scenarios, and each family's real CLI is separately proven to emit exactly those bytes by executing
that scenario. The chain is therefore real CLI ⟷ advertised manifest ⟷ scenario scoping, with no
link asserted by hand.

The gate runs in **all three adapters**, not only in Python. The whole point of the family is that no
distribution depends on a Python runtime; an operator who installs only shell runs only the shell
suite, and a gate living in Python alone would let shell advertise a capability its own fixtures
never exercise. Duplication across three implementations kept honest by shared data is the pattern
this directory already is.

## Fixture schema 1.7 — the end-to-end coverage gate

`capability_coverage` proves a *narrowing* is honest. It cannot prove the fixture asks the questions
that matter, because a question nobody wrote down is not narrowed — it is absent, and absence has no
record to check. The foundation gate is a claim about ten specific end-to-end stories driven through
the real `publish`, `reconcile`, and `record-dispatch-result` commands over one ordered scripted
transport. Schema 1.6 opened `end_to_end_coverage.locked_scenarios` but filled three of the ten from
four workflows, and only the Python adapter pinned the full roster of names — so seven stories were
a roster entry with nothing behind it, and shell and PowerShell could not ask the question at all.

Schema 1.7 fills the registry: all ten locked scenarios are named and mapped to the workflows that
drive them. The names themselves are written into all three adapters rather than read
from the fixture, because the fixture is the thing under test: a locked story that quietly left the
registry would otherwise take its own gate with it. Beyond the names, each story must

- name at least one workflow, and every named workflow must exist;
- be asked of **every** distribution between its workflows, so a story a distribution cannot tell is
  still a story it has to answer — by failing closed, in a workflow narrowed for a reason
  `capability_coverage` already derives from the advertised manifests; and
- collectively exercise all three native operations.

`read_only_call_prefixes` is the second half. "Reconciliation is read-only" is a claim about the
calls the command actually made, not about the words in its projection, so it is asserted against an
allowlist of read shapes over every pinned `reconcile` — a call the allowlist has never heard of
fails rather than passes unnoticed, and a `--method` anywhere in a reconcile transcript is a
mutation by construction.

Like the capability gate, this one runs in **all three adapters**, for the same reason: a
distribution must be able to prove it tells all ten stories without a Python runtime present.

`unsupported_reconciliation_semantics` is retired from `revision_protocol.diagnostic_codes` in the
same revision. No distribution has emitted it since the private PowerShell derivation was deleted,
and a registered code no family produces is vocabulary a port can implement against and never be
told it got wrong.

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

`event-schema.json`'s `contribution_identity` section is the rolling-dispatch half
of that file, and it exists because a **Lane** is reusable the moment its
contribution is admitted to **Integration**. It partitions every literal into four
disjoint scopes — the nine contribution lifecycle types that MUST carry
`contribution_id`/`issue`/`lane_id` with a null `iter`, the existing per-Lane types
that get stamped with the same triple, the five scheduler-scoped rolling types that
describe the Run rather than one contribution, and the two serial Iteration types a
contribution MUST NOT emit. A fixture cannot check its own partition, so the Python
adapter asserts the disjointness, the coverage against `event_types`, and that the
lifecycle contracts and terminal `reason` values equal the constants the production
constructor enforces. The four rolling serialization cases are what every family
executes: they pin a finalized contribution row, a stamped Lane commit, an unknown
(`null`) versus observed-none (`0`) concurrency signal, and a refreshed **Pool**
whose membership keeps source order while the payload keys around it sort. No
Orchestrator emits these yet — the rolling-dispatch scheduler tickets own the
producers — so the fixture revision advertised by each distribution's capability
manifest deliberately has not moved.

`dashboard-insights.json` is consumed by three ports at two different depths.
Python drives the whole case: it replays the normalized Event prefix through the
production reducer and projection and compares the toolkit-neutral view models.
The shell and PowerShell Event-schema adapters drive the *producer* half. Each
case whose Run start declares the native capability manifest carries a
`producer_rollups` list with one entry per `wrapper.iteration.end`, and each
entry's `input` is fed to that port's real Iteration-rollup seam; the rebuilt
payload must equal the Event byte for byte in content. Without it a native
Dashboard trace is only an assertion *about* shell and PowerShell rather than a
trace either of their rollup seams produces — a hand-written rollup can encode
timing or nullability no native producer would ever produce, and every adapter
would still agree with it. Two consequences bind fixture authors: a native case
must declare a producer rollup for every Iteration end it contains, and it must
not pin a fractional `duration_seconds`, because shell rollup arithmetic is
integral. The probe's depth is the rollup seam, so it proves a payload is
producible rather than that today's native Run loop reaches every input the seam
accepts.

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
orchestrator-scoped shared cases and from the `dashboard-insights.json`
producer rollups.
The native Continuation adapters invoke each real public entrypoint from
[`python/tests/test_continuation_scenarios.py`](../python/tests/test_continuation_scenarios.py),
[`shell/tests/test-continuation-conformance.sh`](../shell/tests/test-continuation-conformance.sh),
and
[`powershell/tests/test-continuation-conformance.ps1`](../powershell/tests/test-continuation-conformance.ps1).

The Python reference adapter additionally pins the phase-3 per-issue routing
decisions (Wrapper contract §14): it drives `routing-resolution.json` and
`effort-gate.json` through the production `resolve_iteration_model` and
`gate_reasoning_effort` seams and asserts its in-language model roster equals
`model-roster.json`. `routing-resolution.json`'s `precedence_cases` are driven
through the production `resolve_config` instead, because tier precedence is a
question about the merge and `resolve_iteration_model` receives one already-merged
mapping. Native ports do not implement routing yet, so these three fixtures are
Python-adapter-only.

`calibration-search.json` is the fourth of that set and Python-adapter-only for
the same reason: **Measured routing** is the tier the search feeds, and no
Orchestrator other than the Python reference reads it. The adapter drives the
production `search_price_staircase` seam over scripted **Trial** results — the
one fake the search needs, because everything that spends sits behind its
`TrialRunner` protocol — so the cases pin the walk a **Calibration** actually
performs rather than a restatement of ADR-0027's rules. The fixture states
`promotion_trials` itself rather than leaving a member to count the Trials in a
winning case, and it declares its own synthetic roster, so neither the bar nor
the candidate identities depend on a vendor catalogue.

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
