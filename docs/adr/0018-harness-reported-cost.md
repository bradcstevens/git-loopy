# Cost is reported by the harness, not estimated from a price table

**Status:** accepted

git-loopy estimated the cost of a Run by multiplying token counts against
`pricing.toml`, a hand-maintained table of provider list prices. That table was
premised on the claim, stated in its own header, that "GitHub Copilot CLI bills on a
premium-request quota that the SDK does not expose". The claim is no longer true: the
harness reports what it actually billed, including the cache-read split that dominates a
real agent loop. We will delete the pricing table and the estimator entirely and take
Cost from harness telemetry, denominated in AI Credits.

Decided in [#287](https://github.com/bradcstevens/git-loopy/issues/287) under the
model-routing map [#280](https://github.com/bradcstevens/git-loopy/issues/280). Follows
[ADR-0017](0017-context-tier-and-live-context-gauge.md), which removed the context window
from the same file, and supersedes its expectation that a pricing table would survive as
a pure price table. Retires the remaining schema questions from
[#283](https://github.com/bradcstevens/git-loopy/issues/283).

## Decision

### Cost comes from telemetry, and the table goes

- Cost is read from the harness's reported billing rather than computed by git-loopy.
  `pricing.py`, `pricing.toml`, and the `GIT_LOOPY_PRICING_FILE` override are deleted,
  along with the preflight assertion that pricing data parses and the abort it could
  cause.
- An operator who still sets `GIT_LOOPY_PRICING_FILE` gets a startup warning naming the
  removal. The kit's rule is to warn on malformed intent and stay silent on absent
  intent, and setting a variable is intent. A silently ignored override would reproduce
  the exact defect this decision removes.

### The unit is AI Credits, and there is no dollar figure

- Cost is denominated in **AI Credits**, the unit both the usage telemetry and the
  server's own rate card speak. The premium-request count is reported alongside it as a
  quota signal, because that is the budget an operator actually exhausts mid-Run.
- git-loopy publishes **no USD figure**. Converting to dollars requires a rate that no
  telemetry corrects and that varies by plan — a hand-maintained constant with precisely
  the failure modes this decision exists to remove.
- The fixture-pinned `cost_usd` field is renamed rather than repurposed or left
  permanently null, and the Event schema is revised accordingly.

### Cost availability is a declared capability

- When billing telemetry is absent, the Run declares `cost` unavailable in the
  capabilities it already publishes at Run start. This is neither silent, nor a
  per-Run warning, nor a setup error.
- It is not a setup error for the same reason an unsupported context tier is not: the
  Run is valid, the work lands, and the tokens are still counted. Only the price label is
  missing, and aborting an unattended Run over a reporting gap serves nobody.
- What must not survive is the bare em dash, which today renders identically whether a
  model was missing from a table or the Orchestrator cannot report Cost at all. Those are
  different facts and the operator can act on only one of them.

### Consumption records the cache split and the reported model

- Consumption gains cache-read and cache-write counts, surfaced in the per-Iteration
  drill-in rather than as new Summary columns. The Summary needs no fourth column,
  because Cost is now trustworthy on its own.
- The split is what distinguishes a genuinely oversized Iteration from a long agent loop
  re-sending the same context, which are opposite situations that currently look
  identical. The kit's context ceiling is read as a triage signal, and that reading
  depends on being able to tell them apart.
- Consumption records the model identifier the harness reports, verbatim, alongside the
  pair git-loopy resolved. The harness is authoritative about what ran, and it reports
  identifiers that encode reasoning effort and the context tier as suffixes — variants
  the roster does not list. Normalising them away would destroy the only available
  evidence that a configured tier failed to take effect.

### The live rate card is not read

- The server publishes a full per-model rate card — separate input, output, cache-read
  and cache-write prices, a billing batch size, a nested long-context block, and a
  time-boxed promotional discount. git-loopy does not read it.
- The rate card is the *input* to an arithmetic the harness has already performed
  correctly. Recomputing a figure that was handed over is how the kit acquired its
  existing errors, the largest of which lived in the formula rather than the data.

## Considered options

- **Correct the table and grow its schema** — rejected because the axes it would have to
  express are already six and growing, and because the table was wrong on four
  independent counts within a single audit. Correcting instances of a bug class that
  regenerates at every model release buys a fix with a shelf life.
- **Keep the estimator as a fallback when telemetry is absent** — rejected because a
  fallback preserves every maintenance obligation the table imposed while being exercised
  almost never, and a rarely-taken path that reports money is a path that reports it
  wrongly.
- **Report Cost in USD via one operator-supplied conversion rate** — rejected because a
  single scalar reintroduces the whole failure mode at reduced surface area, and the kit
  would be authoring a number that depends on a billing plan it cannot see.
- **Leave `cost_usd` in place and add new fields additively** — rejected despite being the
  cheaper contract change, because it would institutionalise a field that is null in every
  Orchestrator by construction. A field whose only remaining job is to be empty is the
  original complaint written into the contract.
- **Redefine `cost_usd` to carry AI Credits** — rejected outright. Silently changing the
  unit of a field named for its currency is undetectable downstream.
- **Refuse to start when Cost cannot be reported** — rejected because it makes
  observability a precondition for doing work.
- **Read the live rate card for prospective cost estimates** — rejected as a different
  feature from reporting what a Run cost, and one that would return ownership of the
  pricing formula to the kit.

## Consequences

- The Dashboard stops showing a dollar figure. This is a real loss of legibility,
  accepted because the number it replaces was a disclaimed estimate and the number that
  replaces it is what GitHub billed. The renderer's list-price caveat is deleted along
  with the estimate it qualified.
- Cost remains Python-only. The billing signal arrives on the SDK event stream, so the
  shell and PowerShell Orchestrators continue to declare Cost unavailable, exactly as they
  do today for pricing.
- Two facts must be confirmed empirically before implementation. Whether the per-call
  billing fields populate on the event stream the runner subscribes to, and whether the
  reported input-token count already includes cache reads — the session rollup and the
  shutdown summary disagree on that convention, and the answer changes the reported
  figures by more than an order of magnitude.
- Whether the harness's suffixed model identifiers belong in the roster is left open. It
  is a question about which surface is authoritative for the roster, not about pricing.
- Anyone later wanting prospective cost estimates should read the server's rate card
  rather than reconstruct a table. The SDK's model-billing type currently discards it,
  which is an upstream gap rather than an absent capability.
- The glossary entries for Consumption and Cost become inaccurate once this ships, since
  Cost will no longer derive from Consumption by a shared token-multiplication rule. They
  are deliberately left alone until it does.
