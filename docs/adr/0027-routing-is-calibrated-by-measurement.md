# Routing is calibrated by measurement against the project's own history

**Status:** accepted

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

> **Amended: "cleared the gate" is narrower than it first appears.** The whole-repo gate is a
> bar for *the tree*, not an oracle for *a task*, and a Proving task needs both. Scoring is
> therefore **fail-to-pass on the replayed fix's own tests**, with the full gate retained as a
> **pass-to-pass regression guard** so nothing that breaks the tree can win. See *The Trial's
> oracle is narrower than the gate* below. The lexicographic ordering is unchanged.

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

### Trials run concurrently, and the ceiling they are bounded by is elapsed

**Amendment (#381).** Trials are embarrassingly parallel — each is one pair working one
Proving task in its own worktree at its own base commit, sharing no state — so they run
concurrently, **bounded by an operator concurrency setting**, because the useful ceiling is
the host's and this project cannot pick it. Two decisions the amendment forced, neither of
which is a detail:

**The wall-clock ceiling bounds elapsed time, not summed Trial time.** The serial search
folded a walk's wall clock by adding its Trials up, which is the same number only while
nothing overlaps. Five overlapping ten-minute Trials take ten minutes and sum to fifty, so
a summing search would trip a four-hour ceiling after forty-eight minutes of real
time — punishing an operator for the very concurrency that exists to fit the Calibration
inside the hours they authorised. Trials bought together therefore contribute the longest
of them. A serial search buys one at a time, whose maximum is its sum, so nothing about the
serial walk changed.

**A rung's first Trial is a probe, run alone.** Promotion is unanimous, so one red Trial
kills a rung — which makes a single Trial the cheapest possible evidence that a rung is
dead, and cheapest-first means most rungs are. Buying all five at once would multiply the
credits spent on every rung the search *expects* to fail by the operator's width, converting
a wall-clock saving into a credit bill nobody asked for. Probing first keeps a rung that
dies at its first Trial costing exactly what it costs serially, while a rung that lives
still collapses from five Trial-times to two.

The bound is a **dispatcher**, injected: how many Trials run at once is one module's
(`git_loopy.trial_concurrency`), and *which* Trials are bought stays the search's and is
identical at every width. A serial Calibration is that dispatcher at width 1 — the same code
path, which is why "the gate outcome and Consumption for a given Trial are identical to a
serial run" holds by there being nothing else to run rather than by two paths being
compared. Isolation is issued rather than assumed: each request carries a **slot**, distinct
for every Trial in flight, and the Trial runner keys its worktree and working branch off it.

One cost is accepted knowingly: concurrency contends for CPU, so a Trial's measured wall
clock is noisier than it would be serially. Wall clock is only the third key in the
ordering, behind clearing the bar and behind credits, so the tie-breaking it does is worth
the wall clock it buys back — and the record states the width it was measured at, because a
rung's wall clock is comparable only to another rung measured the same way.

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

### The Trial's oracle is narrower than the gate

*(Added by amendment.)* Three facts make the whole-repo gate unusable as a per-task scorer.

`AgentsMdGateRunner.run(worktree)` reads **`<worktree>/AGENTS.md`** (`gate.py:293-360`), and a
Proving task's worktree sits at a months-old base commit — so it runs *that commit's*
feedback-loop table. **This severs the improvement path claimed above:** strengthening today's
AGENTS.md has no effect on a frozen set replayed at old commits. Refreshing the Proving set,
not editing the table, is what propagates a stronger gate.

The gate is also fail-fast and whole-repo. A base commit that is red for **any** unrelated
reason — a flaky test, a since-fixed lint, a toolchain drift — fails *every* pair at *every*
rung, so the search walks the entire staircase and records "incomplete." Nothing distinguishes
"this model is too weak" from "this task was never runnable."

And "ships a test change" — the 87% figure above — does not imply that test **fails before the
fix and passes after**, which is the property that makes a replay mean anything.

So: score on **fail-to-pass over the replayed fix's own tests**; keep the **full gate at the
base commit as a pass-to-pass regression guard**, so a cheap model that satisfies the fix's
tests while breaking a neighbouring suite still cannot win; and admit a task to the Proving set
only after a **mandatory validation pass** replays it with its real historical fix and confirms
fail-before / pass-after. That admission pass is also the only thing that filters out red base
commits, and it turns "87% ship test changes" from a hopeful proxy into a verified property of
every task in the set.

### The oracle is the base commit's own feedback loops, narrowed to the fix's test paths

*(Added by amendment, shipped by #369.)* The clause above says *what* to score on and left
*how to run it* open, and "run the fix's own tests" has no language-agnostic answer: the
oracle is a set of **paths**, and turning paths into a command is per-ecosystem knowledge
this project has exactly one honest source of — the base commit's own `## Feedback loops`
table.

So the oracle is the **subset of that table that already covers the oracle's test paths**:
for each path, the runnable loops naming the *deepest* directory containing it, unioned
across paths. On this repository's real table a Python-only fix selects `Python suite`
alone, and a fix spanning two members selects both members' rows. Deepest-first is what
makes it narrower than the gate rather than accidentally equal to it — a table declaring a
whole-repo loop beside per-member ones yields the per-member ones.

Three things follow, and each is the reason for the choice rather than a cost of it:

- **No second command runner.** `AgentsMdGateRunner` is already parameterised by the
  filename it reads, so the oracle is that same class pointed at a generated
  single-purpose table written into the Trial's own worktree. The oracle therefore
  inherits the gate's fail-fast execution, its per-loop wall-clock bound
  ([ADR-0009](0009-runner-driven-integration-and-auto-resolution.md), #374) and its failure detail,
  with nothing to keep in step. The generated table is removed before the agent session
  starts: an agent that could read the list of loops it is judged by could edit it.
- **A task this repository cannot score is not a task the pair failed.** When no loop
  covers an oracle path the Trial goes red *naming that*, rather than reporting a model
  too weak to solve something nothing was ever going to run.
- **It inherits the frozen-table consequence rather than escaping it.** The oracle is read
  from the worktree like the gate is, so it too is the months-old commit's. Refreshing the
  Proving set remains the only thing that propagates a stronger table, exactly as above.

### Calibration only affects Parallel mode

*(Added by amendment.)* `resolve_iteration_model` is called from exactly one place —
`_run_lane_lifecycle` (`loop.py:2554`) — and **`parallel: 1` is the default, which is serial**
(`config.py:425-429`). Routing therefore does nothing out of the box, and neither does anything
built on it.

It is not merely unimplemented in serial but incoherent there: the serial path folds the entire
pool into a *single* prompt (`loop.py:1125`) and runs it on the run-wide default
(`loop.py:1153`). One session, many issues, many task types, one model — there is no per-issue
thing to route, and fixing that would abandon ADR-0008's deliberate promise that serial runs
*"byte-for-byte unchanged."*

So this is declared a **Parallel-mode feature**: `git-loopy calibrate` refuses or warns when
`parallel == 1`, and `config get` reports the measured tier as inert in serial. Silence is the
worst option available — it yields a feature that appears to work, commits evidence, and
changes nothing.

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

> **Amended and reversed by [ADR-0029](0029-agents-infer-the-task-type.md).** The section below
> is retained as written because it states the problem correctly; only its *conclusion* is
> overturned. Agents now infer the Task type — unattended, at Pickup as well as here — and the
> taxonomy closes to seven keys in exchange. The paragraph beginning "They cannot be backfilled
> automatically" no longer holds, and the corresponding entry under *Considered options* is
> likewise reversed. The hand-labelling prerequisite below is **discharged**, not deferred.

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
  inferring a Task type from content. **Reversed by
  [ADR-0029](0029-agents-infer-the-task-type.md)**, which reads the invariant as protecting the
  runtime property (routing reads a label, never content) rather than the act of inference, and
  pays for the exemption by closing the taxonomy to seven keys.

## Consequences

- **Routing will pick smaller models than anyone expects.** "Cheapest that clears the bar"
  is a materially different system from "most capable," and the results will look wrong to
  someone who has not read this entry. That is the reason it is written down.
- **Wall clock, not spend, is the practical limit.** Each Trial runs the full five-loop
  AGENTS.md gate — `cargo` twice, pytest, and the shell and PowerShell suites. The stop
  rule in [ADR-0028](0028-measured-routing-is-a-committed-tier.md) is denominated in
  credits and does not bound this. **Resolved by amendment:** a **wall-clock ceiling** is added
  alongside the credit ceiling, both landing on ADR-0028's existing *"incomplete — stopped at
  rung N of M"* path, and Trials run **in parallel across worktrees** reusing ADR-0008's
  machinery, bounded by an operator concurrency setting — **shipped by #381**, which also
  found that the ceiling was being enforced on *summed* Trial time and would therefore have
  tripped at a fifth of the hours an operator authorised the moment Trials overlapped (see
  *"Trials run concurrently, and the ceiling they are bounded by is elapsed"*). The credit
  ceiling alone was pointed
  the wrong way: cheapest-first spends its early rungs on the cheapest pairs, while the gate
  costs the same wall clock on every rung regardless of the model under test — so credits trip
  last in exactly the pathological case the ceiling was written for. The arithmetic: 5 Trials ×
  3–8 rungs × 7 task types is **105–280 sessions**, each with a full gate run.
- **The gate subprocess is unbounded today.** `gate.py:326-334` runs each loop with **no
  `timeout=`**, and no workflow sets `timeout-minutes:`. One hung loop blocks forever. This is a
  latent hang in Integration (ADR-0009) already, so it is fixed **separately** from this work
  rather than as part of it — but Calibration multiplies its blast radius by 105–280 and runs
  unattended.
- **The gate is now load-bearing.** Its weakness is silently inherited by every routing
  decision. Strengthening the AGENTS.md table is the improvement path.
- **The Proving set expires.** It measures the project you *were*. It needs a refresh
  policy, or in a year today's work is routed on 2025's tasks. **Resolved by amendment:**
  refresh when the pinned classifier pair changes ([ADR-0029](0029-agents-infer-the-task-type.md))
  **or** on a stated interval, whichever comes first. The classifier pin matters because it runs
  on "the cheapest pair on the live roster," which moves with the roster — so the taxonomy drifts
  underneath a deliberately frozen set unless a pin change forces a re-base.
- **35 hand-labelled issues are a hard prerequisite**, and per-task-type Calibration is
  blocked until they exist. **Discharged by [ADR-0029](0029-agents-infer-the-task-type.md):**
  the labels are inferred by an agent in a single backfill over the 334 closed issues, so no
  human labelling sitting is required and nothing is blocked on one.
- **Trials must be excluded from Strike accounting and from Run attribution**, or a
  Calibration can end a Run. This is a real change to how sessions are accounted.
- **Wall clock, not spend, is the practical limit.** Each Trial runs the full five-loop
  AGENTS.md gate — `cargo` twice, pytest, and the shell and PowerShell suites. The stop
  rule in [ADR-0028](0028-measured-routing-is-a-committed-tier.md) is denominated in
  credits and does not bound this.
- **There is no bootstrap paradox.** Each Trial is run *by* the candidate pair under test,
  so no meta-model chooses. The search has no privileged model.
