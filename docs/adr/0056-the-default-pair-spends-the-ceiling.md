# The run-wide default pair is `claude-opus-5 @ max`, and it spends the ceiling

**Status:** accepted

Supersedes [ADR-0036](0036-the-default-pair-reserves-the-ceiling.md), which set the same model
one effort rung lower and named the reservation as its whole point. The model is unchanged and
every argument ADR-0036 gives for `claude-opus-5` stands. Only the effort moves: **`xhigh` →
`max`**, family-wide, in all three Orchestrators and in this repository's own tracked Config.

ADR-0048's rule for the *routed* table — **no routed pair holds `max`** — is untouched and is
what keeps this decision from being total.

## What ADR-0036 decided, and what it cost

ADR-0036's rule was that the default **reserves** the ceiling rather than spending it: `max` is
the escalation rung, so a default of `max` leaves a silently-stalled unclassified issue with a
second attempt at the identical pair. Holding the default at `xhigh` made the rung reachable and
escalation a real pair change.

The reservation is not free, and its price falls on the *first* attempt of *every* unclassified
issue — which, as ADR-0036 itself establishes, is the entire corpus while nothing produces
`task-type:` labels. Every one of those runs at one notch below the strength available, so that
the subset which stalls silently can have a stronger second try.

## Why spend it

**The first attempt is the one that matters.** The reservation buys a better retry by paying
for a worse initial attempt. That is the wrong side of the trade when the initial attempt is
what decides whether there is a retry at all: a stronger first pass does not merely raise the
odds of success, it removes the stalled session the rung exists to rescue.

**The rung's payout is gated behind a wasted session.** Escalation fires only on **silent
no-progress**, and only after the full session that produced it — up to two hours at the send
timeout. So the reserved notch is not "a stronger attempt", it is "a stronger attempt purchased
with a failed one". Applying that strength immediately is strictly cheaper for the issues that
would have stalled and strictly better for the ones that would not.

**The escalation it protects is weak by ADR-0036's own account.** ADR-0036 lists this under its
consequences: the residual's escalation is *"an effort bump, not the model flip that the
evidence says rescues stalled work. Weak beats none."* An effort bump from `xhigh` to `max` is
the smallest pair change the ladder admits. Weighed against a permanent one-notch downgrade of
every first attempt, weak does **not** beat none here.

**Escalation is blind to quality anyway.** Progress is commit-shaped, not quality-shaped
(`git_loopy.wrapper.did_iteration_make_progress`), so a default that confidently commits bad
work never escalates. The reservation only ever helps the *silent* failure mode, while the
downgrade it pays with applies to every mode.

## What this costs, stated plainly

**Escalation on unclassified work becomes a no-op.** The default pair now equals the built-in
escalation rung, so an unclassified issue that ends in silent no-progress is retried at the
identical pair, running the identical computation for no new information. This is the exact
failure ADR-0036 was written to prevent. It is accepted, not overlooked, and it is asserted in
`tests/test_config.py` so that a future reader meets it as a decision rather than as a bug.

Three things bound the damage:

- **Every classified issue still escalates for real.** ADR-0048's rule holds: no recommended
  routed pair holds `max`, so all seven Task types have a live rung. The no-op is confined to
  the residual.
- **The rung is still configurable.** `[escalation]` in either Config scope overrides the
  built-in, so an operator who wants a real rung for unclassified work names one — a cross-model
  pair being the obvious choice, since ADR-0036 already records that a model flip, not an effort
  bump, is what the evidence says rescues stalled work.
- **The rung is kept, not deleted.** Removing it would also remove it for routed work and for
  operators who override it. A rung that is inert for one population is not a rung that should
  stop existing.

**It is dearer.** `max` on `claude-opus-5` is the most expensive pair the kit can select, and
the no-config population now starts there. That is the decision, not a side effect: this is the
"quality, not cheap" argument of ADR-0036 carried to its end rather than stopped one notch
short.

## The pair stays atomic

Unchanged from ADR-0036, and worth restating because this decision does not touch it: every
Orchestrator defaults the *model* unconditionally but the *effort* **only when the model is also
unset**. `GIT_LOOPY_MODEL=claude-opus-5` resolves to *no* effort, not `max`. Naming a model opts
out of the kit's effort too, and the pair becomes "let the backend pick". Collapsing that would
hand `max` to models that do not accept it and print warnings for an effort nobody asked for.

## Lockstep, not Python-first

All three Orchestrators change together, for the reason ADR-0036 gives: the default pair already
exists identically in all three and is stated in §11's precedence-spine table as a MUST. The
constants are `git_loopy.cli._DEFAULT_REASONING_EFFORT`,
`git_loopy_resolve_config` in `git-loopy/shell/lib/orchestrator.sh`, and
`Resolve-GitLoopyConfig` in `git-loopy/powershell/GitLoopy.Orchestrator.psm1`. The tracked
`git-loopy/config.toml` moves with them, because `tests/test_config.py` binds it to the built-in
so the kit cannot silently disagree with itself.

## Consequences

- The no-config population gains a one-notch upgrade on every first attempt and loses a
  meaningful retry on the subset that stalls silently.
- The relation between the default and `RECOMMENDED_ROUTING` remains **rationale, not
  mechanism**. The default is still an independent constant, never derived from the table.
- Two recommended rows — `planning` and `bugfix`, both `claude-opus-5 @ xhigh` — now sit *below*
  the run-wide default. Labelling such an issue makes it **cheaper** than leaving it unlabelled,
  inverting the relation ADR-0048 assumed when it wrote that `bugfix` stays at `xhigh` because
  `high` "would make labelling a bug cheaper than not labelling it, since the run-wide default is
  already `xhigh`." That sentence is now false. Retuning the table is deliberately **out of
  scope** here — this decision is about the residual, and ADR-0048's values were measured — but
  it is the first thing a table retune should reconsider.

**Review trigger:** a retune of `RECOMMENDED_ROUTING`, a change to the **escalation rung**, or a
roster change touching `claude-opus-5` or the top of the effort ladder.
