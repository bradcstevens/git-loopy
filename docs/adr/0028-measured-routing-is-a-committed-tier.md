# Measured routing is a committed precedence tier, calibrated only on request

**Status:** accepted

Once **Calibration** ([ADR-0027](0027-routing-is-calibrated-by-measurement.md)) produces a
better **Routed pair**, something has to own it. The proposal was that git-loopy write the
result into `config.toml`, keep a log of the models available at each run, re-calibrate
whenever that log changed, and append every result to a growing record of previous
conclusions. That is **three stores answering one question** — `config.toml`, a session
store, and the evidence file — plus a fourth for the roster. We will add one: a
**Measured routing** tier below global **Config**, committed to the repository, holding
current state only. The other three are deleted, and each is deleted by something the
design already has.

## Decision

### Measured routing is a precedence tier, not a write into `config.toml`

`config.py` already merges along **CLI flag > env var > project > global > built-in
default**, and `CONTEXT.md` defines **Config** as *hand-editable*. Machine writes into a
hand-editable file mean reformatted comments, clobbered overrides, and `git-loopy config
set` fighting a background process. Files with two authors and no fence lose one of them;
adding a fence means hand-rolling block-preserving TOML rewriting and a merge story, which
is the same cost plus a parser.

Instead the chain gains one tier:

> CLI flag > env var > project config > global config > **measured** > built-in default

`config.toml` stays **entirely human**. Measured routing applies **only where the operator
is silent** — a hand-written `[routing]` entry for `bugfix` wins forever, with no override
flag and no special case, while the six task types nobody configured get measured values.
Human supremacy is not a feature here; it is the precedence chain that already shipped.

### The tier is committed, and its evidence lives in the same file

The artifact is **committed to the repository**, and it carries the evidence inline as
first-class data: the winner, the rungs walked, each rung's five **Trial** results, credits,
wall clock, provenance, completeness. One file, so **the table and its justification cannot
disagree** — the two-homes-one-value drift that
[ADR-0019](0019-roster-derived-from-the-pinned-harness.md) was written about, and which a
separate human-readable report would immediately reintroduce.

Committing it buys the reviewability that makes automated routing safe to ship at all:

- A routing change arrives as a **pull-request diff a human can read, question and revert**
  — the ergonomics of "propose, don't apply" without the manual step.
- Teammates and CI inherit it. `.git-loopy/` is auto-appended to `.gitignore` by
  `persist.py:210-233`, so anything written there is deleted by `git clone`: a learning
  system whose memory does not survive cloning, and a CI that is permanently cold because
  it clones fresh every time.

Raw per-Trial records stay local and disposable in `.git-loopy/`. What is committed is the
small, stable distillate. The cost is accepted: **the raw log is disposable, so the
distillate must stand on its own.**

Evidence does **not** pool across repositories. A Proving set mined from one project cannot
speak for another, and pooling would silently reintroduce genericity.

### No free-text field anywhere in the artifact

Every field is machine-checkable. There is no `rationale`, no `summary`, no `conclusion`.

Under ADR-0027 there is nothing to narrate: a price staircase from measured billing, five
pass/fail results per rung, and an argmax. Prose around that adds no information and
re-imports the inferential option ADR-0027 exists to exclude — narrative is exactly how a
five-sample result starts sounding like a finding. The moment a free-text field exists,
something writes an opinion into it.

### Git is the append-only ledger

Current state only. The proposal's append-forever record is **version control inside a
version-controlled file**: `git log -p` is every past Calibration in order, `git diff` is
"compared to previous conclusions" rendered automatically, `git blame` names which
Calibration set a task type's model, and `git revert` undoes a bad one. Every reason to
append is better served by the commit history the artifact already has, without a file that
grows unbounded in every diff forever.

There is a correctness argument too: comparing today's result against conclusions drawn
under an older roster, an older repository and an older gate is apples to oranges. The
comparison that means something is the diff.

### The distillate carries its own provenance, so the model log is unnecessary

The artifact records the **CLI version**, the candidate set searched, the gate's shape and
the Proving tasks used. "Has the roster changed?" is then *live roster vs. the roster
stamped on the distillate* — no second file, no second truth, and the provenance sits on
the thing it justifies.

> **Amended:** provenance also stamps the **pinned classifier pair**
> ([ADR-0029](0029-agents-infer-the-task-type.md)). The classifier runs on the cheapest pair on
> the live roster, so it moves when the roster does, and the Task-type taxonomy would otherwise
> drift underneath a deliberately frozen Proving set. Pinning it makes the refresh rule
> self-justifying rather than an arbitrary interval: **refresh when the pin changes or on a
> stated interval, whichever comes first.**

This is [ADR-0019](0019-roster-derived-from-the-pinned-harness.md)'s own finding applied one
level up. That entry diagnosed a persisted roster whose *"real defect is that it names no
version at all"* — a record that drifted twice and eventually aborted Iterations in
production. A standalone model-availability log would be exactly that record again: a
roster snapshot nothing validates.

The proposal's other claim here is already satisfied. `model_listing.py`'s
`fetch_live_models()` performs a live `list_models()` once per **Run**, memoised and held
fixed, so "look up the available models on every invocation" ships today.

### Calibration is always an explicit act

**`git-loopy calibrate`**, run by a human. Never on first run, never on every invocation,
never automatically when the roster changes.

Auto-calibration on roster change makes **vendor release timing a trigger for your spend**:
GitHub ships a model on a Tuesday and an unattended overnight Run silently converts itself
into a benchmark suite instead of doing the work. ADR-0019 rejected the same coupling in a
milder form when it refused a live CI check because it *"makes vendor release timing a cause
of red builds on unrelated work."* Same defect, larger blast radius — credits rather than a
red tick.

A roster change may only **notify**, and only when it could change an answer:

> Notify when the live roster contains an unmeasured pair **cheaper than the current
> measured winner** for some task type.

Under ADR-0027's objective a *more expensive* new model is structurally incapable of winning
while the incumbent still passes, so it warrants silence. This is ADR-0019's warning rule
applied verbatim — *"a warning that fires on routine vendor churn trains operators to ignore
it"* — and it means most vendor releases correctly produce nothing.

The honest price is that **the fully-automatic version is dead**: routing improves only when
a human runs `calibrate`. If autonomy is wanted later, the way to get it is a **spend
allowance the operator pre-authorises**, not an implicit trigger. That is deliberately not
proposed now.

### There is no session store

The proposal's "refer to routing settings from the last previous session" has no work to do.
`config.toml` persists. The committed distillate persists. Between them they *are* the memory
of the last session, and a third store would only be a third thing to disagree.

### A stopped search must look stopped

Cheapest-first search has a pathological case: nothing cheap passes and it walks the whole
85-rung staircase. Calibration therefore carries a **hard AI-Credit ceiling**. On exhaustion
it **stops, keeps the incumbent, and records "incomplete — stopped at rung N of M"** on the
distillate.

> **Amended: the credit ceiling does not bind.** Cheapest-first spends its early rungs on the
> cheapest pairs, while the gate costs the same wall clock on every rung regardless of the model
> under test — so credits trip *last* in precisely the pathological case above. A **wall-clock
> ceiling** is added alongside, landing on this same "incomplete" path. The shipped schema
> already anticipated it: `INCOMPLETE`'s field set carries `wall_clock_seconds` next to
> `credits`.

A partial search is never presented as a finished one — the same discipline
[ADR-0026](0026-billed-cost-and-the-live-rate-card.md) applied when it required an unknown
cost to render as unavailable and never as zero.

### `config get` and `config list` must name the winning tier

They report the effective merged value today. With a machine-written tier, *"why is this
model set?"* becomes a question the tooling must answer — human, measured, or built-in
default — or the system is unexplainable. That is precisely the failure ADR-0019 diagnosed
when it found a roster that named no version at all.

## Considered options

- **Write into `config.toml` directly** — rejected. Two authors, no fence, and a
  hand-editable file loses.
- **A fenced machine-owned block inside `config.toml`** — rejected. Block-preserving TOML
  rewriting and a merge story for a two-author file: all of the tier's complexity plus a
  parser.
- **Propose only; the operator applies by hand** — rejected as redundant. A committed
  artifact already arrives as a reviewable, revertible diff.
- **Keep the artifact in gitignored `.git-loopy/`** — rejected. Deleted by `git clone`;
  CI never accumulates evidence; two laptops learn different tables from one repository.
- **Keep it per-machine in the global config home** — rejected. Survives cloning but pools
  evidence across unrelated projects and still never reaches teammates or CI.
- **Commit the raw run history too** — rejected. Diff noise, merge conflicts on a hot file,
  and it publishes timings and spend into the repository.
- **A separate human-readable report beside the machine table** — rejected. Two homes for
  one value; a report that no longer matches the table is worse than no report.
- **Carry the evidence in TOML comments** — rejected. Comments are lost by any writer that
  is not hand-rolled, and the justification silently evaporates.
- **Append every Calibration into the file** — rejected. Version control inside version
  control, and an unbounded file.
- **A standalone model-availability log** — rejected. A roster record nothing validates,
  which is the ADR-0019 defect exactly.
- **Auto-calibrate when the roster changes** — rejected. Vendor release timing becomes a
  trigger for unattended spend.
- **Calibrate at `init` only** — rejected. Predictable and permanently stale.
- **Notify on any roster change** — rejected. Trains operators to ignore the notification.
- **A pre-authorised spend allowance for unattended Calibration** — deferred, not refused.
  It is the honest mechanism if autonomy is wanted; it is not needed to ship this.

## Consequences

- **A new precedence tier is a contract change.** ADR-0019 took the Wrapper contract to 1.5
  for less. The contract, the conformance fixtures and eventually the shell and PowerShell
  Orchestrators are all in scope — though ADR-0019 already records that routing is
  Python-only today, so the cross-language cost is deferred rather than discharged.
  **Corrected by amendment:** the Wrapper contract is now at **1.8**
  (`git-loopy/shell/lib/continuation.sh:18`), not 1.5 — that figure was stale on the day it was
  written and would mislead whoever implements this. **Resolved by amendment:** a
  `measured-routing` conformance fixture is added and the contract bumped **now**, with the
  shell and PowerShell *implementations* still deferred. Of the 17 fixtures in
  `git-loopy/conformance/` there is a `routing-resolution.json` but no measured-routing fixture,
  so the tier that has already shipped in Python is invisible to the family gate — the fixture
  is what makes the deferral honest and visible rather than an undocumented gap.
- **The measured tier is inert in serial mode**, which is the default. See ADR-0027's
  *Calibration only affects Parallel mode*: `calibrate` refuses or warns at `parallel == 1`, and
  `config get` must report the tier as inert rather than merely reporting a value that has no
  effect.
- **A fourth `MeasuredStatus` is required.** [ADR-0030](0030-demotion-is-measured-per-pair.md)
  demotes *upward* into a pair nobody measured, and the three shipped states cannot represent
  that — `DEMOTED` clears the pair outright. A provisional pair carrying `status = measured`
  would be the same failure as a stopped search that looks finished.
- **`config get` / `list` / `path` gain a provenance obligation** they do not have today.
- **`RECOMMENDED_ROUTING` is demoted to a bootstrap default.** Calibration will disagree
  with it, and by design the measurement wins. A repository with no closed-issue history
  keeps it and says so plainly.
- **Routing changes now appear in pull requests.** That is the point, and it is also
  review load nobody is currently expecting.
- **Demotion writes to a committed file** during a Run, which means an unattended Run can
  produce a commit to the distillate. How that interacts with **Checkpoint** and push
  durability ([ADR-0004](0004-runner-checkpoint-and-push-durability.md)) is unresolved and
  needs its own decision. **Resolved by [ADR-0030](0030-demotion-is-measured-per-pair.md)**,
  which moves Demotion to *after* the Run and so removes the interaction rather than resolving
  it: no Lane is running, so there is no race on the shared tracked file and no need for a
  mid-Run commit mechanism that does not exist today.
- **The vocabulary is recorded here rather than in `CONTEXT.md`.** ADR-0019 set the
  precedent — *"`CONTEXT.md` is deliberately untouched. It is a glossary of shipped reality,
  and none of this has shipped."* The terms **Calibration**, **Proving set**, **Trial**,
  **Measured routing** and **Demotion** are fixed by these two entries and move into the
  glossary when they ship. **Partially departed from by amendment:** `CONTEXT.md`'s **Task
  type** entry is rewritten *now*, ahead of the code, because
  [ADR-0029](0029-agents-infer-the-task-type.md) reverses two clauses that are currently stated
  there as fact. **Discharged by amendment:** four of the five terms have now shipped and are
  `## Language` entries — **Calibration**, **Proving set** (with **Proving task** beside it),
  **Trial** and **Measured routing**. **Demotion** waits, because
  [ADR-0030](0030-demotion-is-measured-per-pair.md)'s mechanism has not shipped. That mechanism
  writes `provisional` — the one state of the two that has shipped — and never the older
  `demoted`, which ADR-0030 supersedes and leaves without a writer; nothing writes either today.
  A term entered ahead of its code is the precise failure ADR-0019's
  precedent exists to prevent, so the glossary records four and names the fifth as owed.
