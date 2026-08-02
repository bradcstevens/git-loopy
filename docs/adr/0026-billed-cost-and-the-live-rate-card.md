# Cost is the harness's billed AI Credits, and the live Rate card speaks that same unit

**Status:** accepted

**Supersedes:** the *"The live rate card is not read"* clause of
[ADR-0018](0018-harness-reported-cost.md). Every other clause of ADR-0018 is
**upheld**, including *"git-loopy publishes no USD figure"* — which this decision
strengthens rather than reverses, for the reason set out below.

[#320](https://github.com/bradcstevens/git-loopy/issues/320) asked for a **USD**
figure alongside **AI Credits**, obtained by applying the server's live **Rate card**
to the harness's reported billing. The reasoning was that ADR-0018 refused USD only
because converting to dollars needed a hand-maintained constant, and that the live
card removes the need for one. Reading the card the pinned harness actually publishes
dissolves half of that: the card is a real, live, per-model price listing and git-loopy
already calls the endpoint that carries it — but its prices are **denominated in AI
Credits**, which is the unit the harness has already billed in. Applying it to a billed
figure converts Credits into Credits. We will read the card, resolve it once per **Run**,
publish it, and declare its availability as its own **Insight capability** — and we will
publish no USD figure, because no field on any surface the kit reads is denominated in
dollars by its schema.

Decided in [#322](https://github.com/bradcstevens/git-loopy/issues/322) under the map
[#320](https://github.com/bradcstevens/git-loopy/issues/320). Follows the injection
pattern [ADR-0019](0019-roster-derived-from-the-pinned-harness.md) established for the
roster, and corrects ADR-0018's remark that the SDK discards the card — as ADR-0019
already noted, the prices parse.

## The premise that was wrong

The premise was that Credits and dollars are two units, that the harness supplies the
first, and that the card supplies the conversion to the second. There is no such
conversion, in either direction, on any surface the kit can reach. Against the pinned
harness (`github-copilot-sdk==1.0.5`, CLI 1.0.67):

| surface | field | what it is denominated in |
| --- | --- | --- |
| `models.list` — the **Rate card** | `billing.tokenPrices.inputPrice` | *"AI Credits cost per billing batch of input tokens"* |
| | `.outputPrice`, `.cacheReadPrice`, `.cacheWritePrice` | AI Credits per billing batch, verbatim in the same words |
| | `.batchSize`, `.longContext`, `billing.discountPercent` | tokens; a nested Credits-denominated block; a whole-number percentage |
| the session event stream | `assistant.usage.copilotUsage.totalNanoAiu` | AI Credits, ×10⁻⁹, **already billed** |
| | `assistant.usage.cost` | **undocumented**; Experimental, no unit stated |
| | `shutdown.modelMetrics[].requests.cost` | premium requests — *inferred*, not schema-stated |
| the retired table | `pricing.toml` `input_per_mtok` | **USD** — hand-authored by git-loopy, from provider list prices |

The only dollar figure in the entire picture is the one git-loopy wrote itself, and
deleting it is what ADR-0018 decided. The card's prices are the *input* to the
arithmetic whose *output* arrives on the event stream as `totalNanoAiu`; the harness
even ships the applied card per call, as a `costPerBatch` / `batchSize` / `tokenCount`
breakdown. So when #322 says the card is applied to "the harness's reported billing",
the composition is the identity — and read the other way, as the card applied to raw
token counts, it is a recompute of a figure the harness already billed, which is
rejected below on its own terms. Neither reading produces dollars. Credits are not a
proxy for a bill that a rate card would refine into money. **Credits are the bill.**

Two floats named `cost` are dispositioned rather than passed over, because either one
being USD would reverse this decision outright. `assistant.usage.cost` is marked
Experimental and carries **no unit at all**, sitting on the very object whose sibling
field states AI Credits explicitly; `shutdown.modelMetrics[].requests.cost` is a float
beside an integer `count`, alongside a `totalPremiumRequests` elsewhere, which makes
premium requests the obvious reading but not a stated one. The rule this decision
adopts is that **git-loopy does not infer a currency from an unlabelled float.** A
figure the operator will read as money must be denominated by the surface that
published it — that is the same rule that condemns the hand-authored table, applied to
the kit's own guesswork instead of someone else's list prices. If a **Run** replay ever
shows one of these fields to be dollar-denominated, this decision should be revisited
on that evidence; nothing here depends on their being absent, only on their not being
labelled.

So the conclusion ADR-0018 reached is unchanged and its reasoning is now
better-evidenced than when it was written: a USD figure still requires a Credits-to-USD
rate that varies by plan, that no telemetry corrects, and that git-loopy would have to
author. That is the hand-maintained constant this whole line of work exists to delete,
and #322's own final criterion — that an operator-supplied conversion rate stays
rejected — is the same rule stated from the other end. The two cannot both be honoured
by publishing USD.

What *was* wrong in ADR-0018 is narrower and worth fixing: it declined to read the card
at all, on the grounds that reading it would mean recomputing what the harness had
already computed. Reading is not recomputing. The card is worth having for what it
records rather than for what could be derived from it.

## Decision

### Cost is billed AI Credits, and the harness is the only author

- **AI Credits** is the primary, un-derived figure: it is what the telemetry reports and
  what the quota is drawn against, so the number closest to the telemetry is the number
  the operator sees first. The **premium-request count** is reported alongside it,
  because that is the budget an operator actually exhausts mid-**Run**.
- git-loopy **never recomputes a figure the harness has already billed**. Where a
  billed figure is present it is read and totalled; it is never re-derived from tokens
  and prices, in any code path, including as a fallback when a figure is missing. A
  missing figure is unknown, and unknown renders as unavailable — never as zero, and
  never as an estimate wearing a billed figure's clothes.

### The Rate card is live, injected, resolved once, and held fixed

- The **Rate card** is obtained from the same `models.list` call that already supplies
  the roster ([ADR-0019](0019-roster-derived-from-the-pinned-harness.md)) and the
  picker's premium column. **Run** start makes no additional round trip.
- It is **injected as a parameter** and never loaded from a packaged file, for exactly
  the reason ADR-0019 gave for the roster: a pinned fixture cannot be correct under
  `COPILOT_CLI_PATH`, which relocates the harness at runtime. An injected live card is
  correct by construction.
- It is **resolved once per Run and held fixed**, so every row of one **Summary** is
  denominated identically even if the server's prices change mid-Run.
- The resolved card is published in the capability block the Run already emits at start,
  so a replay records the prices the work was billed under. That is the card's job:
  **provenance, not arithmetic**.
- It is read **as published** — per-model input, output, cache-read and cache-write
  prices, the billing batch size, the nested long-context block and any whole-number
  discount — rather than flattened to a single rate. A card recorded lossily is not a
  record of what the Run was billed under.

### Rate-card availability is its own Insight capability

- Rate-card availability is a declared **Insight capability**, **distinct from Cost**,
  so that *no billing telemetry* and *no rate card* stay separately declarable facts
  rather than one collapsed unknown.
- Because nothing is derived from the card, an absent card **never costs a figure**: the
  Credits and premium-request figures render exactly as they would with a card present.
  The capability says what the Run knows about its own prices, not what it can display.
- Observability is never a precondition for doing work. A fetch failure — offline,
  unauthenticated, or a listing that simply does not carry prices — leaves the Run
  starting normally and is warned about on exactly the same terms as the existing roster
  fetch failure, so there is one behaviour to learn rather than two.

### git-loopy publishes no USD figure

- **git-loopy publishes no USD figure.** ADR-0018's clause stands, on evidence rather
  than on caution: **no field on any surface the kit reads is denominated in dollars by
  its schema**, and git-loopy does not infer a currency from an unlabelled float.
  Manufacturing a dollar figure means authoring a rate that depends on a billing plan
  the kit cannot see.
- An **operator-supplied conversion rate** remains rejected, as ADR-0018 rejected it: a
  single scalar reintroduces the whole failure mode at reduced surface area.
- An **offline price fallback** — packaged, cached, or reconstructed from a previous
  Run's card — remains rejected. A rarely-taken path that reports money is a path that
  reports it wrongly, and a cached card is a hand-maintained table with a slower
  maintainer.
- The bar for revisiting this is stated so it can be met rather than argued: a
  **dollar-denominated figure published by the harness itself**. The moment one exists,
  denomination is a single seam away — which is why the seam below is built regardless.

### The denomination seam is built anyway

- One injected denomination seam carries Cost to the **Summary**, the **Queue**, the
  per-issue **Iteration breakdown** and **Rolling dispatch**'s cost pressure, so
  per-**Iteration**, per-**Lane contribution** and per-**Active issue** Cost cannot
  disagree about what an issue cost.
- Its value does not depend on USD. It is what stops the four surfaces drifting while
  the table is deleted and billing telemetry arrives, and it is what makes a future
  second unit a substitution rather than a twelfth threading.

## Considered options

- **Apply the card to reported billing to produce USD** — rejected because it does not
  produce USD. The card's prices and the harness's reported billing are the same unit;
  the composition is the identity, and shipping it would put a dollar sign on a Credits
  figure.
- **Multiply Credits by a published Copilot list rate** — rejected. It is an
  operator-supplied conversion rate with the operator taken out of the loop, and
  ADR-0018 rejected it in the form where a human at least knew they had supplied it.
- **Apply the card to token counts to produce Credits when `totalNanoAiu` is absent** —
  rejected. It recomputes a figure the harness bills, which is the practice that
  produced the errors this work removes, and the largest of those lived in the formula
  rather than the data. An absent figure is unknown.
- **Do not read the card at all, as ADR-0018 decided** — rejected now that its cost is
  known to be zero: the call is already made, and the card is the only record of the
  prices a Run was billed under. Without it a replay can total a Run but cannot audit
  it.
- **Cache the last-seen card as an offline fallback** — rejected. It reintroduces a
  stale local price file, which is the artefact under deletion.
- **Derive Credits from the internal per-call `tokenDetails` breakdown** — rejected. It
  is marked internal in the SDK, and `copilotUsage.totalNanoAiu` is the public figure
  saying the same thing.
- **Defer the whole decision until GitHub publishes a dollar rate** — rejected. It would
  hold the deletion of `pricing.toml` hostage to a vendor roadmap, and the deletion is
  the fix.

## Consequences

- **#322's USD criteria cannot be met and the tickets built on them need
  re-specification.** [#332](https://github.com/bradcstevens/git-loopy/issues/332)
  ("USD alongside Credits") has no derivation available; what survives of it is the
  per-row unavailability behaviour and the rule that no unknown Cost renders as zero.
  [#335](https://github.com/bradcstevens/git-loopy/issues/335) should read the new
  Credits, premium-request and cache fields and drop its **Rate card**-derived USD
  field. This is recorded here rather than resolved here: re-slicing those tickets is
  the operator's call, not this ADR's.
- **The "three distinguishable unavailability states" become two, plus an independent
  capability.** *No billing telemetry* and *this Orchestrator cannot report Cost* remain
  distinct Cost states and must not collapse into one em dash. *No rate card* is not a
  third Cost state, because Cost does not depend on the card; it is a separate
  declaration about provenance.
- **[#331](https://github.com/bradcstevens/git-loopy/issues/331) lands as written.**
  Every one of its criteria — same call, injected, resolved once, held fixed, published,
  own capability, non-fatal fetch failure, pinned in **Conformance** — is satisfiable and
  unchanged. Only its stated motivation moves from "so USD becomes possible" to "so a
  Run records the prices it was billed under".
- **[#334](https://github.com/bradcstevens/git-loopy/issues/334) is unaffected.** The
  shell and PowerShell **Orchestrators** declare the new capability `false` for the same
  reason they declare Cost `false`: billing arrives on the SDK event stream, which
  neither port subscribes to.
- **The Dashboard still stops showing a dollar figure**, and this remains the real loss
  of legibility ADR-0018 accepted. What replaces it is what GitHub billed rather than a
  disclaimed estimate of a different quantity, and the renderer's list-price caveat goes
  with the estimate it qualified.
- **The empirical question ADR-0018 left open is now half-answered and still open.** The
  card's unit is settled by its own schema. What a **Run** replay must still confirm is
  whether the per-call billing fields populate on the event stream the runner subscribes
  to, what the unlabelled Experimental `assistant.usage.cost` float actually holds, and
  whether the reported input-token count already includes cache reads — the session
  rollup and the shutdown summary disagree on that last convention, and the answer moves
  the reported figures by more than an order of magnitude. The first Run to populate
  these fields should be dumped and the finding recorded on
  [#329](https://github.com/bradcstevens/git-loopy/issues/329), which already asks for
  exactly that.
- **`CONTEXT.md` is deliberately untouched.** **Rate card** and **AI Credits** are
  [#336](https://github.com/bradcstevens/git-loopy/issues/336)'s entries, sequenced
  after the code implements them; the glossary records shipped reality.
- **This ADR is numbered 0026, not 0024 as #322 anticipated.** 0024 and 0025 were taken
  by terminal ownership and the installed Skill catalog, and a published number is never
  reused.
