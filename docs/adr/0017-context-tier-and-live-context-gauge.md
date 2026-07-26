# Context tier is a run-level dial, and the context gauge measures live telemetry

**Status:** accepted

Copilot exposes a third model dial alongside model and reasoning effort: a
**context tier** (`default` or `long_context`) that selects the prompt-token budget a
session is given. git-loopy modelled neither the dial nor its consequences, so the tier
leaked in ambiently from `~/.copilot/settings.json` and a Run's context budget — and its
price — depended on the operator's machine rather than on recorded configuration. We
will model the tier as a run-level setting resolved per Iteration, keep the context
gauge measured against live harness telemetry rather than any static table, and remove
the static context-window field from the pricing table entirely.

Decided in [#297](https://github.com/bradcstevens/git-loopy/issues/297) under the model-routing
map [#280](https://github.com/bradcstevens/git-loopy/issues/280). Supersedes the
context-window half of [#283](https://github.com/bradcstevens/git-loopy/issues/283)'s
recommendation and unblocks [#287](https://github.com/bradcstevens/git-loopy/issues/287).

## Decision

### The tier is modelled, and it is run-level

- git-loopy sets `context_tier` explicitly on session creation. The SDK accepts it as a
  first-class `create_session` argument, so leaving it ambient is a choice, not a
  constraint — and an unattended Run must not inherit whatever the operator last set
  interactively.
- The tier is a **run-level** setting with a `default` default, not a per-task-type entry
  in the `[routing]` table. Model and reasoning effort are routed per task type because
  they demonstrably vary that way; the tier does not. A tier column would hold six
  identical values.
- The **Routed pair remains a pair** in the `[routing]` table, but the *resolution* of an
  Iteration returns a triple. A run-level tier meets a per-task-type model, so tier
  validity is model-dependent and must be gated after the model resolves — the same seam
  where reasoning effort is already gated, at Pickup.
- Only `default` and `long_context` are modelled. `inherit` exists solely in the CLI's
  *subagent* settings schema and has no meaning for a root session, which has no parent
  to inherit from.

### Unsupported tiers warn and downgrade

- Per-model tier capability joins the existing model roster fixture rather than forming a
  parallel table, so there is one lockstep point with the live catalog instead of two.
- An unsupported tier **warns and downgrades to `default`**; it never fails the Iteration.
  The harness itself silently ignores an unavailable `long_context`, so erroring would
  make git-loopy stricter than the thing it wraps, and would abort an unattended Run over
  a dial that changes nothing the kit acts on. Warning is the point: silence is the defect.

### The gauge measures live telemetry, not a table

- Context fill continues to measure against the `token_limit` reported live by the
  harness, which is by definition the budget in force for whatever tier is active. No
  static table can be more accurate, and no static table can go stale.
- The hardcoded `0.5` highlight threshold is replaced by the budget the kit already
  computes. That constant was a proxy for "100k on a 200k window" — the same 100k the
  effective-budget calculation returns as its target — and the two were one number
  wearing two hats. They decouple above a 200k window, where the highlight fires roughly
  five times too late. Highlighting on the computed target, with a second band at the
  computed ceiling, is byte-identical below 200k and correct for the first time above it.
- The kit's absolute context budget stays **flat across tiers**. The accuracy argument for
  a bounded prompt — prefill cost is roughly quadratic and quality degrades well before
  the cap — concerns absolute token counts, not a fraction of the window.

### Context window leaves the pricing table

- The static `context_window` field is removed from the pricing schema, along with the
  unused `context_utilisation` helper that was its only reader.
- It is a live-reported capability, not a price; it is the only non-monetary required
  field in a pricing entry; and the tier makes it unrepresentable, since one scalar
  cannot express both a default and a long-context budget for the same model.

### Compaction becomes visible, not avoided

- git-loopy surfaces the harness's truncation and compaction events instead of ignoring
  them, and sets the infinite-session thresholds explicitly rather than inheriting SDK
  defaults — the same reproducibility argument that decided the tier itself.
- Compaction is **not** disabled, and the tier is **not** widened to postpone it. At the
  default tier the kit's own ceiling sits only a few percent below the compaction point,
  which is deliberate: an Iteration that reaches it has outgrown its issue, and that is a
  triage signal worth seeing rather than a window to enlarge.

## Considered options

- **Treat the tier as ambient operator config** — rejected because a Run's model
  configuration is meant to be recorded and replayable, and the tier carries its own
  pricing, so an invisible dial silently changes both behaviour and cost.
- **Make the tier a third element of the `[routing]` table** — rejected because it does not
  vary by task type, and the change would cost a schema revision plus contract bumps
  across three Runners to express a constant.
- **Correct the static context windows instead of deleting them** — rejected because it
  fixes instances of a bug class that regenerates at every model release, while deleting
  the field retires the class.
- **Measure the gauge against a static per-tier cap** — rejected because the harness
  already reports the enforced budget live and per tier.
- **Scale the kit's context budget with the tier** — rejected because the quality argument
  that motivates the ceiling is about absolute prompt size, not window fraction.
- **Disable auto-compaction so long Runs fail loudly** — rejected because the failure would
  be worse than the lossy summarisation it replaces; visibility solves the actual problem,
  which is that compaction is currently undetectable.

## Consequences

- Runs stop inheriting a machine-local tier, so a machine currently configured for
  `long_context` will see its Iterations compact where previously they did not. That is
  the intended correction, and the new compaction telemetry is what makes it observable.
- Tier capability data must be verified against the live catalog before the roster is
  extended. The candidate list of unsupported models originates from a source whose
  harness claims have already been wrong more than once, so it needs the same treatment
  prices received rather than being taken on trust.
- The pricing table becomes purely a price table, which is the shape the pricing-policy
  decision needs in order to reason about tier-dependent rates cleanly.
- The gauge, the header's target and ceiling cues, and the kit's budget policy converge on
  one set of numbers instead of three.
- Compaction visibility touches event mapping, the rollup, the Dashboard, and all three
  Runners, and is sized as its own change rather than part of the tier work.
