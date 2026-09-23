# The model roster describes the pinned harness, and the kit reads it live

**Status:** accepted

**Accepted-design amendment:** [ADR-0057](0057-live-evidence-guides-per-issue-routing.md)
retains the actual harness as authority, requires fresh eligibility at proposal/Pickup
boundaries, and refuses invalid selected routes instead of rewriting them. Delivery is
judged against that policy; this entry's historical investigation remains intact.

`conformance/model-roster.json` was hand-transcribed and had drifted twice. The question
asked was which of two disagreeing surfaces should be authoritative: the SDK's
`models.list()` or the CLI's CAPI `/models` payload. Investigation dissolved the question.
There are not two surfaces. `models.list` never reads CAPI's advertised reasoning-effort
array — the CLI overwrites it from a table hardcoded in its own bundle. The disagreement
was **CLI version skew**, and the roster's real defect is that it names no version at all.
We will treat the SDK-pinned CLI's `models.list` as the only authority, read it live in
production, and keep the fixture as a version-stamped offline fallback.

Decided in [#296](https://github.com/bradcstevens/git-loopy/issues/296) under the
model-routing map [#280](https://github.com/bradcstevens/git-loopy/issues/280). Answers the
roster-authority question [ADR-0018](0018-harness-reported-cost.md) left explicitly open,
supplies the per-model tier capability [ADR-0017](0017-context-tier-and-live-context-gauge.md)
requires, unblocks [#281](https://github.com/bradcstevens/git-loopy/issues/281), and
corrects the diagnosis recorded in [#282](https://github.com/bradcstevens/git-loopy/issues/282).

## Upgrade record: SDK 1.0.14

**Historical:** superseded by the corporate-compatible records below. The
corporate feed did not yet offer 1.0.14, so the pin went to 1.0.13 and then
1.0.14rc1 before returning to 1.0.14. The SDK 1.0.14 final record below
describes the current pin and roster; the present tense in this record
describes #594's refresh.

[#594](https://github.com/bradcstevens/git-loopy/issues/594) advances the SDK pin to
1.0.14 and its CLI to 1.0.85. The refreshed fallback includes all 19 models returned
by that pinned harness on the upgrade account, with their reported effort sets.
The 1.0.67/1.0.71/1.0.75 comparisons below remain historical evidence, not current pins.

**Migration exception:** to preserve existing saved Config behavior, the upgrade retains
seven account-unlisted compatibility entries rather than deleting them: the two older
Sonnet rows, Opus 4.6, the three existing Gemini rows, and `mai-code-1-flash-picker`.
Their efforts were not reverified; the CLI stamp identifies the harness used to refresh
the observed rows, not a claim that the account offered every compatibility entry.
The Runner README lists both sets. The live listing remains the Run's authority.
Gemini 3.8 was not an existing roster entry and was not returned by that listing.
It remains off-roster, preserving configured selections and efforts through the
warning-and-pass-through path rather than introducing an unverified capability gate.

## Corporate-compatible release record: SDK 1.0.13

**Historical:** superseded by the SDK 1.0.14rc1 and 1.0.14 final records below.
The present tense in this record describes 2026-09-20, and "the SDK 1.0.14
record above" in its last sentence is #594's record, not the current pin.

For [#610](https://github.com/bradcstevens/git-loopy/issues/610), the operator
explicitly selected SDK 1.0.13 because the corporate feed did not offer 1.0.14.
This carries current main's changes into dev.5 rather than returning to the
dev.2-only maintenance baseline of #595.

The cached SDK runtime reported CLI 1.0.83 and protocol 3 on 2026-09-20.
Its live listing returned the same 19 observed models and effort sets as the
existing fallback, so only the CLI provenance stamp changes. The seven
compatibility entries and saved model choices remain unchanged. Global Skill
metadata is read through `ServerSkillsApi.discover`, without an agent session.
The SDK 1.0.14 record above remains historical evidence, not the current pin.

## Corporate-compatible upgrade record: SDK 1.0.14rc1

**Historical:** superseded by the SDK 1.0.14 final record below. The present
tense in this record describes 2026-09-21.

The pin recorded here was SDK 1.0.14rc1, whose CLI is 1.0.84-5. It is the newest
`github-copilot-sdk` the Microsoft corporate feed offers: 1.0.14 final is
published upstream but has not been ingested there. The pin is therefore a
prerelease by necessity, not preference. It is still a published wheel carrying
an injected `CLI_VERSION`, which is why it keeps the pinned-harness invariant
this ADR depends on — a source or editable install ships the sentinel
`CLI_VERSION = None`, disabling the runtime download and leaving
`spawned_harness_version()` empty, so the readback could report no divergence at
all. Installing from source to reach 1.0.14 was rejected for that reason as well
as the corporate feed policy.

The live listing captured through CLI 1.0.84-5 on 2026-09-21 returned **20**
models. All 19 previously observed rows and their effort sets are unchanged, so
the SDK bump itself alters no roster content. The twentieth is `grok-4.7`
(`low`, `medium`, `high`), which is added to the observed rows.

**Provenance of `grok-4.7`:** it is a backend catalogue addition, not an effect
of the CLI bump. Re-running the same listing through the previously pinned CLI
1.0.83 on the same date returned the same 20 models with an identical effort
array for `grok-4.7`, so the committed roster was already one row stale
independently of this upgrade. Its efforts are live-verified against two
harnesses; unlike the Gemini 3.8 row rejected in #594, this is a capture rather
than an assumption.

The seven account-unlisted compatibility entries and every saved model choice
remain unchanged, and `gemini-3.8-flash` remains off-roster on the same
warning-and-pass-through terms. The SDK 1.0.13 and 1.0.14 records above remain
historical evidence, not the current pin. `python -m git_loopy.sdk_feed`
reports when the corporate feed later carries a newer release (#639). It does
not change this pin, and it is not an Integration feedback loop.

## Corporate-compatible upgrade record: SDK 1.0.14 final

[#638](https://github.com/bradcstevens/git-loopy/issues/638) moves the pin from
1.0.14rc1 to SDK 1.0.14 final once the Microsoft corporate feed carried it; on
2026-09-23 `python -m git_loopy.sdk_feed` reported it as a stable release newer
than the pin. The installed wheel injects `CLI_VERSION = "1.0.85"`, the CLI
[#594](https://github.com/bradcstevens/git-loopy/issues/594) established for this
release, and `spawned_harness_version()` reports the same value, so the stamp
moves to 1.0.85. Upstream had already tagged 1.0.15 prereleases
(`v1.0.15-preview.0` and `v1.0.15-preview.1`, which PEP 440 normalizes to
`1.0.15rc0` and `1.0.15rc1`), but the corporate Python feed carried no 1.0.15
release that day. They were not sourced from anywhere else, so 1.0.14 is the
newest pin the feed allowed.

The live listing captured through CLI 1.0.85 on 2026-09-23 returned **23**
models. All 20 previously observed rows and their effort sets are unchanged, so
the SDK bump itself alters no roster content. Three rows are added to the
observed set: `claude-opus-5.5` (`low` through `max`), and `gpt-6-luna` and
`gpt-6-sol` (both `none` through `max`).

**Provenance of the three additions:** they are backend catalogue additions, not
an effect of the CLI bump. The listing re-run through the previously pinned CLI
1.0.84-5 on the same date returned the same 23 models with identical effort
arrays. That CLI had returned 20 models on 2026-09-21, so the roster was already
three rows stale independently of this upgrade. As with `grok-4.7`, each
addition is a capture against two harnesses rather than an assumption.

The seven account-unlisted compatibility entries remain unchanged and are still
not offered by the upgrade account. `gemini-3.8-flash` remains off-roster on the
same warning-and-pass-through terms. Neither the kit's defaults nor the
recommended routes change. The tracked project Config moves to the new rows in
the same change, but that is an operator's edit, not a migration of saved
choices. The SDK 1.0.14rc1 record above is now historical evidence, not the
current pin.

## Amendment: the roster follows the harness you are running

The stamped fixture bounded more than it was meant to. `cli_version` records
*which harness the effort capability was captured against*, but the keys of that
same table were also being used as the answer to a different question — "is this
model real?" — on three advisory surfaces: the routing typo-check, the
unknown-model pass-through warning, and `git-loopy config routing set`, which
refused outright. An operator whose Copilot CLI is newer than the stamp is
offered models the fixture has never heard of, and the kit called every one of
them a probable typo. Worse, the `config` path made it a hard refusal, so a
perfectly valid model could not be written to Config at all.

That is a pin the original decision never argued for. ADR-0019 already says the
**live catalogue defines the supported-model set**; only the offline surfaces
had no way to reach it.

**Decision.** The capability refresh that already reads the authenticated
harness — `static_route.refresh_harness_capabilities`, the one read both a Run's
preflight and `git-loopy doctor` arrive through — now records the model ids it
observed to `<config-home>/git-loopy/model-roster.json`. The advisory surfaces
read the built-in roster **unioned** with that observation
(`git_loopy.roster_cache.supported_models`). A newer CLI therefore stops
producing false "not in the kit's supported set" warnings, and stops refusing
valid Config, without a fixture edit and without an SDK pin bump.

Four properties make this safe rather than a second source of truth:

- **Union, never replacement.** The SDK 1.0.14 record above deliberately retains
  seven account-unlisted compatibility rows so existing saved Config keeps
  resolving. Replacing the roster with one account's listing would delete exactly
  those. Union only ever *adds* a model somebody's harness actually offered.
- **Models, not capability.** Only model *identity* is remembered. No effort set
  and no context tier is inferred from an observation, so this introduces no
  unverified capability gate — the hazard the SDK 1.0.14 record named when it
  left Gemini 3.8 off-roster. An observed model with no measured effort row is
  reported as exactly that, in different words from the typo case, because a
  message listing the model it claims is missing is worse than no message.
- **Advisory only.** `validate_static_route` still refuses an unlisted model from
  the **fresh** listing read at that moment. An eligibility decision is never
  made from a remembered answer, which is ADR-0057's requirement unchanged.
- **Best-effort, and never load-bearing.** An unwritable config home, a malformed
  document, a future schema, or an empty listing all degrade to the built-in
  roster. The cache can never fail the capability read that produces it, and an
  account that lists nothing is not evidence that nothing exists.

The fixture keeps its job and its stamp: it remains the offline fallback, the
cross-language contract, and the thing CI holds against
`copilot._cli_version.CLI_VERSION` with no network and no credentials. What it
stops being is the ceiling on which models an operator is allowed to name.


## The premise that was wrong

Reasoning-effort capability is not vendor data reaching the kit through two paths. In the
CLI bundle, `models.list` discards CAPI's `capabilities.supports.reasoning_effort` and
substitutes a lookup into a client-side constant table. When a model is missing from that
table the field is omitted entirely, and the SDK reports `None`.

That table ships with the CLI, so the roster is a function of CLI version. Running
identical SDK code against three binaries on one account, minutes apart:

| CLI | `gemini-3.5-flash` | `gemini-3.6-flash` |
| --- | --- | --- |
| **1.0.67** — pinned by `github-copilot-sdk==1.0.5` at the time of this investigation | `low, medium, high` | *absent* |
| 1.0.71 | `minimal, low, medium, high` | *absent* |
| 1.0.75 — the operator's Homebrew install | `minimal, low, medium, high` | `minimal, low, medium, high` |

The fix that closed #282 matched the fixture to 1.0.75, a CLI the kit does not run,
because the operator's shell and the kit's harness were different binaries and nothing
recorded which was which. `list_models()` did not produce the bug. It produced the
version-correct answer and was overruled.

## Decision

### The pinned harness is the authority, and it is read live

- The roster answers one question: **will this value survive the call the kit is about to
  make?** That makes the authority the `models.list` of the CLI the SDK actually spawns —
  not GitHub's catalogue, not the CLI on the operator's `PATH`, and not CAPI, which the
  effort data never came from.
- `gate_reasoning_effort` takes the roster as an **injected parameter**. In Python the
  Orchestrator injects a live `list_models()` result. A synced fixture cannot be correct
  under `COPILOT_CLI_PATH`, which relocates the harness at runtime; an injected live
  roster is correct by construction.
- The **live** catalogue therefore defines the supported-model set. `UNKNOWN_MODEL`
  changes meaning from "absent from our list" to "the harness does not offer this model",
  which is the fact the operator can act on.

### The fixture stops being a mirror and becomes a stamped fallback

- The fixture remains, as the offline fallback and as the cross-language contract the
  shell and PowerShell Orchestrators are held to. It is no longer the production source
  of truth, so it can no longer silently be wrong in production.
- It carries a **`cli_version`** field, and CI asserts that field equals
  `copilot._cli_version.CLI_VERSION`. This runs **offline, with no authentication and no
  network**, which is why it can exist at all — no workflow in this repository has Copilot
  credentials. It catches the event that *causes* drift, an SDK bump without regeneration,
  rather than the drift itself.
- A generator script produces the fixture for a human to review and commit. There is
  deliberately **no live `--check` in CI**: it cannot authenticate, and if it could it
  would fail unrelated pull requests on GitHub's model-release schedule.
- The fixture is corrected to the pinned CLI: the Gemini rows revert, and
  `claude-sonnet-4.5` is removed. That entry has been absent from every CLI version and
  from CAPI for the fixture's whole life and nobody noticed, which is the argument for
  everything above.

### Divergence is reported at Run start, not at build time

- When the live roster disagrees with the fixture, the Run records the divergence in the
  capability block it already publishes at Run start, alongside the **CLI version actually
  spawned** — the single fact whose absence produced this entire investigation.
- A prominent warning is raised only when the divergence would change a gating decision.
  Every catalogue change would otherwise warn, and a warning that fires on routine vendor
  churn trains operators to ignore it.

**Amendment ([#410](https://github.com/bradcstevens/git-loopy/issues/410)):** the version half
of that report now ships, and it ships *unconditionally*. The **Run readback** prints the
spawned CLI version beside the version `model-roster.json` was stamped against, and flags them
as diverged when they differ — an offline comparison of two constants, which is why it can be
unconditional where the live-roster comparison cannot: reading the live catalogue needs the
network, so a Run that could not reach it would otherwise print nothing at all about the fact
this entry exists to surface. The two reports are complementary rather than redundant. This one
answers *is the roster even about the binary this Run spawns*, which is the question whose
absence produced this investigation; the live comparison answers *and is its content still
right*, and keeps its warn-only-when-it-would-change-a-decision rule.

### The kit's two enforcement branches are named, because they are not symmetric

Probing the pinned harness established behaviour that four tickets had assumed rather than
tested:

| requested | harness behaviour |
| --- | --- |
| an effort on a model that supports **no** effort | **hard reject** — the session fails to create |
| an effort outside a supporting model's advertised set | **accepted** and forwarded |
| `long_context` on a model without it | **accepted**, silently ignored |
| a syntactically invalid tier | **hard reject** |

- The **hard** branch is a stale-fixture liability, not a cosmetic one. A fixture claiming
  an effort for a model the pinned CLI treats as effort-incapable **aborts the Iteration**.
  This is why the fixture correction is urgent rather than tidy-up.
- The **soft** branch stays restrictive: an effort the roster does not list is dropped to
  none and warned about, even though the harness would forward it. The harness enforces
  only the capable/incapable split; the server beyond it coerces unsupported values
  silently. Warning converts an invisible server-side downgrade into a visible kit-side
  one, matching the warn-and-downgrade precedent ADR-0017 set for tiers.

### The roster carries tier capability, derived and not transcribed

- Roster entries become `{efforts, tiers}`. Tier support is derived from the presence of a
  `long_context` block in the model's billing prices, reachable through an exported SDK
  type rather than by reaching into raw dictionaries.
- No surface anywhere publishes tier capability directly — the price block is a **proxy**,
  and recording it as one matters. Its failure mode is to under-report, which routes into
  the existing warn-and-downgrade path rather than into a broken Run.
- Against the pinned CLI the models lacking the block are exactly the five ADR-0017 named
  from a manual reading. That premise, and ADR-0017's assumption that an unavailable tier
  is silently ignored rather than rejected, are now verified rather than assumed.

### Suffixed identifiers stay out of the roster

- The effort- and tier-suffixed identifiers found in live usage events appear in **no**
  catalogue surface at any CLI version. They are outputs, not inputs — the harness reports
  them, nothing accepts them.
- The rule is: **derive what is derivable, record what is not, parse nothing.** ADR-0018
  already requires recording the reported identifier verbatim. Decomposing a suffix to
  recover a base model would invent a parser for a format nobody documents, and would
  destroy the evidence ADR-0018 preserved it for.
- The corresponding guard is that a harness-*reported* identifier must never be fed back
  into routing or gating.

### The behavioural fixture stops naming real models

`effort-gate.json` pins gate *behaviour*, but its cases referenced real model identifiers —
including the retired `claude-sonnet-4.5`, load-bearing in two of them. Its cases become
synthetic and declare the roster they run against, so that a vendor catalogue change can no
longer silently invalidate a behavioural test.

### Two things deliberately not changed

- **`REASONING_EFFORT_ORDER` stays as written.** It is already exactly the union of every
  advertised ordering, and `minimal` is already correctly placed. No invariant tying it to
  the roster is added: an unrecognised effort already fails loudly and names the accepted
  values, so a future gap surfaces as a visible missing capability rather than a silent
  misordering.
- **The SDK's narrow `ReasoningEffort` literal is left alone.** It omits three values the
  runtime accepts, but the kit does not run a type checker, so the hazard is latent. The
  literal is itself a hand-maintained mirror of the CLI's table — the chain is three
  mirrors deep, and this decision breaks the only link the kit owns. Typing the kit's own
  field as a plain string is already the correct response to an upstream type that is
  wrong.

### This is a contract change

The Wrapper Contract goes to 1.5 and the routing fixtures move with it, as one change. The
fixture schema gains provenance and tier capability, the gate gains an injected roster, and
the supported-model set becomes live. Shipping the corrected values separately as a "data
fix" would reproduce the original defect exactly: a corrected file carrying no provenance
is indistinguishable from the defective one, which is how the last correction went wrong.

## Considered options

- **Make CAPI authoritative** — rejected. CAPI is not the origin of the effort data at all,
  and it is reachable only by scraping the CLI's debug log. Following it would encode
  capability the pinned harness will reject.
- **Follow the operator's installed CLI** — rejected. It is not the binary that runs the
  work. This is precisely the confusion that produced the drift.
- **Regenerate the fixture in CI against the live API** — rejected. No workflow has Copilot
  authentication, and the check would fail unrelated pull requests whenever GitHub ships a
  model.
- **Keep the gate reading a synced fixture** — rejected. It cannot be correct under
  `COPILOT_CLI_PATH`, and it leaves the roster as a mirror, which is the defect class.
- **Fail the build when live and fixture diverge** — rejected. It makes vendor release
  timing a cause of red builds on unrelated work.
- **Relax the soft branch to forward unlisted efforts** — rejected. The harness forwards
  them and the server coerces them silently; the kit's warning is the only place the
  operator learns the requested effort did not happen.
- **Parse suffixed identifiers back to a base model and effort** — rejected. It invents a
  parser for an undocumented format and discards the evidence it was recorded to preserve.
- **Ship the corrected Gemini rows now and defer the rest** — rejected as the mechanism of
  the original bug.

## Consequences

- **The roster is now coupled to the SDK pin.** Bumping `github-copilot-sdk` changes which
  CLI is spawned and can change roster contents, so the pin bump and the fixture
  regeneration become one atomic change. This coupling is real and is accepted: it is the
  honest expression of a dependency that already existed and was merely unnamed.
- **At decision time, the pending SDK bump was a known roster change.** The pin was two
  releases behind, and the proposed CLI sat between a version where `gemini-3.6-flash`
  was absent and one where it was present. That motivated verifying the new harness
  before every change; the corporate-compatible SDK 1.0.14 final record above
  documents the current refresh.
- The prose stating that the fixture's keys *are* the supported-model set becomes false for
  the Python Orchestrator, which reads the live set. The contract must say which
  Orchestrators are held to the fixture and which are not.
- Updating the other Orchestrators costs nothing today, because no shell, PowerShell, or
  TUI source reads any routing fixture — routing remains Python-only. The cross-language
  obligation is deferred, not discharged.
- ADR-0018's remark that the SDK's model-billing type discards token prices is **incorrect**
  at the pinned SDK version; they parse. This decision depends on their being present.
  [ADR-0026](0026-billed-cost-and-the-live-rate-card.md) reads those same prices as the
  **Rate card**, on this decision's injection terms and for this decision's reason.
- The fixture correction should be treated as a fix, not a refinement. Until it lands, the
  offline fallback path can abort an Iteration on a model the kit routes to.
- `CONTEXT.md` is deliberately untouched. It is a glossary of shipped reality, and none of
  this has shipped.
