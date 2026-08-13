# Routing is calibrated by measurement against the project's own history

**Status:** proposed

`[routing]` is hand-authored from `RECOMMENDED_ROUTING` (`config.py:131-166`), seven task
types pointing at seven models nobody measured. The proposal was that git-loopy conduct
"an extremely deep review and analysis of the project" and conclude which model each task
type should use. That inference does not close: the review examines *the project*, the
conclusion is about *the models*, and no property of this repository can observe which
model writes better code in it. We will replace the analysis with an **experiment** — a
cheapest-first search over the live candidate list, scored by the AGENTS.md gate on a
**Proving set** mined from this repository's own closed issues — and accept that its
evidence is thin, because **Demotion** catches what it gets wrong.

## The premise that was wrong

Two premises, in fact.

**"A deep review of the project reveals the best model."** It reveals nothing of the kind.
Comparative model quality is a fact about model behaviour, observable only by running
models. The only three other places the claim could come from are all dead ends: the
roster carries `{efforts, tiers}` and prices — capability, not quality
([ADR-0019](0019-roster-derived-from-the-pinned-harness.md)); the **Rate card** is
provenance, not arithmetic ([ADR-0026](0026-billed-cost-and-the-live-rate-card.md)); and
an agent reasoning from its own priors produces confident, unfalsifiable prose about
models that postdate its training. That last one is the real hazard, because it is what a
"deep analysis" degenerates into by default, and depth of prose reads exactly like depth
of evidence.

**"Pick the most capable model that balances capability, quality and response time."**
Backwards. The AGENTS.md gate is a **bar, not a gradient** — this repository's own
AGENTS.md says the feedback-loop table "is not documentation about the gate, it *is* the
gate's input." Once an arm clears it the work is acceptable by the project's declared
standard, and capability beyond the bar buys nothing while costing money. The objective
inverts to **the cheapest pair that reliably clears the gate**. The original phrasing also
omitted cost entirely, which guarantees convergence on the largest model: marginal
betterment at any price always wins an objective with no price term.

## Decision

### The gate is the oracle, and cost is the discriminator

A **Trial** is scored lexicographically: **cleared the gate → fewest AI Credits → shortest
end-to-end wall clock.** Nothing else. No LLM judge, because a model rating models is the
unfalsifiable-opinion machine this decision exists to avoid, and judges are known to
self-prefer and to reward verbosity — a bias pointing the same way as the missing cost
term. No weighted composite, because the weights would be chosen by the same judgment the
measurement replaces.

The consequence is deliberate and load-bearing: **the gate becomes the definition of
quality.** A weak gate elects a cheap model that writes bad code that passes. Under this
decision, strengthening the AGENTS.md table is how routing improves — a far healthier loop
than tuning judge weights, but it makes the gate load-bearing in a way it was not before.

### The Proving set is mined from closed issues, SWE-bench style

A **Proving task** is one closed issue restored to the commit *before* its fix, carrying
that fix's own test changes as the oracle and its issue body as the task. The material is
already here: **327 closed issues**, ~**78%** carrying both `## What to build` and
`## Acceptance criteria`; **222 commits** carrying `Closes/Fixes/Resolves #N` — the Wrapper
contract's own backstop keyword makes every fix traceable; **52 of the 60 most recent**
(87%) ship test changes.

The Proving set earns three things nothing else does:

- **A Calibration can run before any forward work exists.** The proposal's first-run
  behaviour was otherwise impossible: a Trial needs a real issue with acceptance criteria,
  and a fresh checkout has none pending.
- **A new model can be measured today.** The set is *frozen*, so when the roster changes
  the same fixed tasks yield a comparable number immediately, with no waiting for new
  issues to arrive and no confounding. Nothing else makes re-measurement coherent.
- **It is the honest reading of "deep analysis of the project."** The deep work is not an
  essay about the architecture; it is mining the project's own history into a benchmark.
  That is genuinely deep, genuinely specific to this repository, and unlike the essay it
  produces something falsifiable.

It is a **proxy** and is recorded as one, in the same discipline
[ADR-0019](0019-roster-derived-from-the-pinned-harness.md) applied when it called the
`long_context` price block a proxy for tier capability rather than pretending it was the
thing itself. Historical replay overstates: the issue body may describe its own solution,
and later docs and ADRs in the tree may contain the answer. Re-deriving a known fix is not
the same skill as novel work. The 13% of fixes shipping no test change cannot be replayed
at all, which biases the set toward test-bearing work.

### The search is cheapest-first, and the roster stays fully in scope

The fixture carries **21 models and 85 distinct (model, effort) pairs**. An exhaustive
tournament is 85 × 7 task types × 5 tasks = **2,975 agent sessions**, each with a full
five-loop gate run. Infeasible.

The objective dissolves it. "Cheapest that clears the bar" is a **search, not a ranking**:
walk the candidate list in ascending expected cost and stop at the first pair that passes.
Everything above is dominated *by the objective itself* and is never worth trialling. Cost
scales with the answer's position, not the roster's size — realistically 3–8 rungs per task
type.

So **all 85 pairs stay in scope**, exactly as proposed. The scope was never the problem;
the traversal was. The ordering comes from the **Rate card**, which is measured billing
data, so no prior and no judgment enters the search. Within a model, effort is monotone in
expected spend; across models, the billing multiplier orders them.

The search is **not seeded from `RECOMMENDED_ROUTING`.** A hardcoded human guess as a
starting position biases where the search lands and is never escaped — the inferential
prior returning as an initial condition.

### Five Trials, unanimous, and no pretence of statistical confidence

A pair is promoted on **5 Proving tasks, unanimously.** This is not a rate estimate and
must never be described as one. Separating a 70%-reliable pair from a 90%-reliable one
needs dozens of tasks per rung; at 3–8 rungs × 7 task types that is thousands of sessions,
which is the arithmetic this decision already rejected. **Confidence is unaffordable, and
the design says so rather than dressing five samples in the language of analysis.**

Unanimity is chosen because the two errors are not symmetric. A **false pass** merges bad
work into the repository; a **false fail** overpays. A strict bar biases toward false
fails — toward stepping *up* the staircase — which is the cheap error.

### Demotion is what buys the right to be cheap

Promotion requires measurement. **Demotion requires only experience**: a **Measured
routing** entry whose pair accumulates consecutive **Strikes** on real work is removed, and
the task type falls back. This is valid without a counterfactual because it is an absolute
threshold rather than a comparison.

The two stages are one design. Stage one is a cheap, noisy, honestly-low-confidence search;
stage two catches what it got wrong in production. Search does not have to be right. It has
to be **cheap and reversible**.

### Exploration is disqualified by the Strike counter

A bandit over live issues is the textbook answer and this system cannot have it. A Strike
is a **shared consecutive counter and reaching the limit ends the Run.** An exploratory arm
does not merely waste credits on one issue — a bad arm exploring twice in a row can
**terminate an unattended overnight Run**. Exploration here is a liveness risk, not a tax.
git-loopy structurally cannot be an online learner without first decoupling exploration
from Strike accounting, and that is not proposed.

### A Trial is not an Iteration

A Trial contains an agent session, which anywhere else in git-loopy would be an
**Iteration**. It is deliberately not one: Iterations are attributed to a **Run** and tick
the Strike counter, and a Trial must do neither — otherwise a Calibration could strike out
and end something. A Trial belongs to a **Calibration**; an Iteration belongs to a Run.

### Learning from observational history is rejected outright

The tempting design — mine `.git-loopy/runs/*.json` and rank pairs by how they did — is not
merely weak, it is **systematically backwards**. Every issue is worked by exactly one pair,
so the counterfactual is never observed, and `RECOMMENDED_ROUTING` already assigns the hard
task types to the expensive models. The best model is handed the worst issues *by design*.
Naive history therefore concludes the best model is the worst one, with high confidence and
a growing sample: a system that gets more wrong as it collects more data. Stratifying by a
seven-value `task-type:` label does not fix it, because `bugfix` spans "fix a typo in a
docstring" and "fix the race in rolling dispatch" — it only hides the confounder behind a
smaller sample.

### The Proving set needs human labels, and may not infer them

Calibration is per task type, and **zero of 328 closed issues carry a `task-type:` label.**
There is no per-task-type corpus.

They cannot be backfilled automatically. `CONTEXT.md` states that a **Task type** is "read,
never inferred — no title, body, or other content may imply it." An agent classifying issue
bodies violates a stated invariant of the domain model, and this decision does not carve
out an exception for its own convenience.

The bill is small because the sample is: **5 tasks × 7 task types = 35 issues**, hand
labelled once. At ~68% of closed issues being both AFK-shaped and test-bearing, that is
about 50 issues to review. It is one sitting of human work, and it is a prerequisite —
Calibration for a task type with fewer than five labelled, replayable Proving tasks does not
run.

## Considered options

- **An agent reasons from the roster and its priors** — rejected. Opinion formatted as
  analysis, unfalsifiable, and confidently wrong about models released after its training.
- **Rank pairs from `.git-loopy/runs/*.json` history** — rejected. Confounded by difficulty
  in a direction that inverts the true ordering, and worsens with sample size.
- **Stratify observational history by task type** — rejected. A seven-value label does not
  control for difficulty.
- **Randomised exploration / a bandit** — rejected. Strikes are shared and consecutive, so
  exploration can end an unattended Run.
- **An LLM judge on acceptance criteria** — rejected. Reintroduces the inferential option
  through the side door, with a verbosity-and-self-preference bias pointing at the more
  expensive model.
- **A weighted composite of gate, cost, time and quality** — rejected. False precision;
  the weights are the judgment the measurement was meant to replace.
- **Exhaustive tournament over all 85 pairs** — rejected. ~2,975 sessions to produce a full
  ranking that the objective never consults.
- **Seed the search from `RECOMMENDED_ROUTING` and probe outward** — rejected. A hardcoded
  guess as a starting position determines most outcomes and is never escaped.
- **A synthetic benchmark shipped with git-loopy** — rejected. It measures models against
  git-loopy's own toy repository, which deletes the premise; if the answer is generic, ship
  a generic table and skip the machinery.
- **Trial on live issues and discard the losing work** — rejected as pure waste. Live
  Trials are retained only as confirmation, with the winner's diff merged.
- **A large `k` for a genuine rate estimate** — rejected. Thousands of sessions, which is
  the arithmetic that killed the exhaustive tournament.
- **Have an agent label historical issues by task type** — rejected. `CONTEXT.md` forbids
  inferring a Task type from content.

## Consequences

- **Routing will pick smaller models than anyone expects.** "Cheapest that clears the bar"
  is a materially different system from "most capable," and the results will look wrong to
  someone who has not read this entry. That is the reason it is written down.
- **The gate is now load-bearing.** Its weakness is silently inherited by every routing
  decision. Strengthening the AGENTS.md table is the improvement path.
- **The Proving set expires.** It measures the project you *were*. It needs a refresh
  policy, or in a year today's work is routed on 2025's tasks.
- **35 hand-labelled issues are a hard prerequisite**, and per-task-type Calibration is
  blocked until they exist.
- **Trials must be excluded from Strike accounting and from Run attribution**, or a
  Calibration can end a Run. This is a real change to how sessions are accounted.
- **Wall clock, not spend, is the practical limit.** Each Trial runs the full five-loop
  AGENTS.md gate — `cargo` twice, pytest, and the shell and PowerShell suites. The stop
  rule in [ADR-0028](0028-measured-routing-is-a-committed-tier.md) is denominated in
  credits and does not bound this.
- **There is no bootstrap paradox.** Each Trial is run *by* the candidate pair under test,
  so no meta-model chooses. The search has no privileged model.
