# The model roster describes the pinned harness, and the kit reads it live

**Status:** accepted

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

## The premise that was wrong

Reasoning-effort capability is not vendor data reaching the kit through two paths. In the
CLI bundle, `models.list` discards CAPI's `capabilities.supports.reasoning_effort` and
substitutes a lookup into a client-side constant table. When a model is missing from that
table the field is omitted entirely, and the SDK reports `None`.

That table ships with the CLI, so the roster is a function of CLI version. Running
identical SDK code against three binaries on one account, minutes apart:

| CLI | `gemini-3.5-flash` | `gemini-3.6-flash` |
| --- | --- | --- |
| **1.0.67** — pinned by `github-copilot-sdk==1.0.5`, the binary git-loopy runs | `low, medium, high` | *absent* |
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
- **The pending SDK bump is now a known roster change.** The pin is two releases behind,
  and the CLI it would move to sits between a version where `gemini-3.6-flash` is absent
  and one where it is present. What that CLI reports must be established before the bump
  lands.
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
