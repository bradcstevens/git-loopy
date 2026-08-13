# Agents infer the Task type, and the taxonomy closes to pay for it

**Status:** proposed

[ADR-0027](0027-routing-is-calibrated-by-measurement.md) needs a **Proving set** stratified by
**Task type**, and **zero of 334 closed issues carry a `task-type:` label**. It refused to let
an agent supply them, citing `CONTEXT.md`: a Task type is *"read, never inferred."* That refusal
made the feature unbuildable, so we reverse it — agents infer a Task type from issue content,
unattended, at **Pickup** as well as during Proving-set construction, and write the label back
to the tracker. In exchange the taxonomy **closes**: only the seven shipped keys may be written.

## What the invariant actually protected

ADR-0027 rejected agent labelling on a single sentence and made a point of not carving out an
exception *"for its own convenience."* Honouring that literally costs the whole feature, so the
sentence deserves reading rather than obeying.

The prohibition is stated against the *act* of inferring, but everything that depends on it
depends on something narrower: that **routing reads a label and never reads content**.
`resolve_iteration_model` (`config.py:542`) is handed `item.labels` and nothing else, and the
docstring's claim — *"the runner **reads** the label, it never infers the type"* — stays true
under this decision. Inference happens **once, before the label exists**; routing never
re-reads a body. What is given up is not the runtime invariant but the *provenance* one: after
this, a human-set and an agent-set label are the same string on the same issue, and **nothing
distinguishes them.** That is the real price, and it is charged in full because the labels are
written to the tracker (below).

Note that the rule had a sibling: `docs/agents/triage-labels.md` says of `parallel-safe`,
*"The runner never infers it."* That one is **untouched**. `parallel-safe` gates whether work
may run concurrently — a wrong guess corrupts a repository. A wrong `task-type:` guess picks a
suboptimal model, which **Demotion** ([ADR-0030](0030-demotion-is-measured-per-pair.md))
already exists to catch. The two labels differ in blast radius, and only one of them is being
opened.

## Decision

### The classifier is a named pair, not the run-wide default

Classification needs a model, and the model it would naturally borrow is `self._config.model`.
That is refused. Doing so would make the run-wide default determine the Task type, which
determines the **Routed pair**, for every issue — re-admitting exactly the kind of unmeasured
prior ADR-0027 was written to evict, except *hidden*, since unlike `RECOMMENDED_ROUTING` it
would appear nowhere as a routing input.

Instead the classifier gets **its own config knob, defaulting to the cheapest pair on the live
roster.** The prior does not disappear — it cannot — but it becomes named, overridable and
visible.

There is no precedent to copy here: the codebase has exactly two agent-session shapes, and the
only non-work one (auto-resolution, ADR-0009) *reuses the routed pair* —
`model=contribution.model` (`loop.py:3221`) — which is unavailable by definition, because the
routed pair is what classification exists to produce. This is a genuinely new seam.

### Its spend is visible, and it never ticks a Strike

`RunCostMeter` only folds in sessions constructed with `event_observer=`. Lane sessions pass it
(`loop.py:2709`); **auto-resolution does not** (`loop.py:3215-3227`). That precedent is
deliberately *not* followed. A per-issue classifier whose credits never reach the Summary is
the failure [ADR-0026](0026-billed-cost-and-the-live-rate-card.md) forbids when it requires an
unknown cost to render as unavailable and never as zero.

It also takes the same carve-out ADR-0027 wrote for a **Trial**: a classification is not an
**Iteration**, and must never tick a **Strike**. Strikes are shared and consecutive, and
reaching the limit ends the Run — a classifier that can strike out could end an unattended
overnight Run without doing any work.

### The taxonomy closes

This is the concession that makes unattended inference survivable, and it reverses `CONTEXT.md`
in the opposite direction from everything else here.

Today `config.py:617-622` accepts **any** `task-type:<anything>` string: an unrecognised key is
not an error, it warns once and falls back to the global default. Under human labelling that is
a harmless convenience. Under unattended inference it compounds badly, because
`ensure_issue_label` (`gh.py:1631`) runs `gh label create --force` before attaching — so a model
that invents `task-type:refactor` **creates that label in the tracker** and attaches it
permanently. The issue then routes to the default forever, warns once into diagnostics nobody
reads, and — since ADR-0027 mines closed issues for the Proving set — **silently corrupts the
benchmark's strata**. An open taxonomy and an unattended writer cannot both be safe.

So: only the seven keys may be written, anything else is refused rather than warned about, and
**`git-loopy init` bootstraps `task-type:*` into the label vocabulary** — it does not today
(`bootstrap_labels()` at `init.py:829` is fed only `TRIAGE_ROLES` + `parallel-safe`), so the
seven labels do not currently exist in any tracker.

### Labels are written back to the tracker

The alternative was keeping them inside the artifact. Writing to the tracker makes the corpus
inspectable, reusable by the next refresh, and visible to humans who can correct it. The
mechanism already exists and is proven by Continuation's discovery-index label.

## Considered options

- **Human backfill of 35 issues** — rejected as the cost that keeps the feature unbuilt. It is
  also recurring, not one-off, since the Proving set expires.
- **Agent proposes, human ratifies the batch** — rejected. It preserves label provenance and
  was the cheaper-looking middle path, but it reintroduces a human gate on every refresh and so
  reintroduces the cost that this decision exists to remove.
- **Inference confined to Proving-set construction, with Pickup unchanged** — rejected. Live
  unlabelled issues would keep falling back to the run-wide default, so the measured table
  would only ever apply to the minority of issues someone had labelled by hand.
- **The run-wide default model as the classifier** — rejected. A hidden prior determining every
  routing decision.
- **Deterministic heuristics instead of a model** — rejected. Keyword matching on titles is a
  worse classifier than the cheapest model on the roster and still infers from content, so it
  pays the same principled price for less accuracy.
- **Keep the taxonomy open** — rejected. Unattended writers plus `gh label create --force` plus
  warn-and-fallback means invented keys become permanent, silent, and corrupt the strata.
- **Keep labels out of the tracker, inside the artifact only** — rejected. The corpus stops
  being inspectable or correctable, and every refresh re-infers from scratch.

## Consequences

- **Label provenance is gone.** A human-set and an agent-set `task-type:` label are
  indistinguishable. Anyone wanting to audit the classifier must diff the tracker against a
  Calibration's recorded provenance.
- **There is no human-labelled ground truth anywhere in the system.** Combined with the
  big-bang agent backfill, routing is optimised against *the classifier's* notion of each task
  type, not a human's. Nothing anchors the taxonomy.
- **The classifier drifts.** It runs on "the cheapest pair on the live roster," and that pair
  changes when the roster does — so issues labelled in different quarters are classified by
  different models, under a Proving set that is deliberately frozen. This is why the classifier
  pair is pinned in the artifact's provenance and a pin change forces a refresh
  ([ADR-0028](0028-measured-routing-is-a-committed-tier.md)).
- **`CONTEXT.md` changes now, ahead of the code.** Both the *"read, never inferred"* clause and
  the *"open and operator-extensible"* clause are rewritten by this entry, so for the duration
  of implementation the glossary describes the decided model rather than the shipped one. This
  deviates from the discipline ADR-0019 set and is recorded here so it is not mistaken for an
  oversight.
- **The classifier is inert in serial mode**, like everything else routing touches — see
  ADR-0027's scope note. Whether it should still run in serial purely to accumulate labels for
  a future Proving set is **not decided here**.
