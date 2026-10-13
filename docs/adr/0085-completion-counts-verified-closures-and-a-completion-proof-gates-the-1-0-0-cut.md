# Completion counts verified closures, and a Completion proof gates the 1.0.0 cut

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

**Clarified by [PRD: Language paths and release](https://github.com/bradcstevens/git-loopy/issues/791):**
the Completion proof's baseline arm is the last stable release before 1.0.0, as the
glossary's **Completion proof** entry says. That release was 0.11.1 when this was decided.
0.11.2 was then promoted by exception, so the arm is 0.11.2 today, and the record names the
release it measured.

Decided by [Define end-to-end completion and its quality
guardrails](https://github.com/bradcstevens/git-loopy/issues/662), against the baseline
from [Measure the end-to-end completion baseline from past
Runs](https://github.com/bradcstevens/git-loopy/issues/657)
([findings](../research/completion-baseline.md)).

git-loopy 1.0.0 is meant to raise end-to-end ticket completion without losing quality,
but nothing defined either half. In the baseline, Runs closed 72.0% of the issues they
worked in that same Run (serial 81.6%, Parallel mode 62.0%), and 95.1% of picked-up
issues were eventually closed by some Run. None of those closes was verified. 228 of 345
were an Agent's bare `gh issue close`. Every tracker write carries the operator's token,
so closes could only be attributed by time window, and Integration failures and Recovery
outcomes left no Event at all.

## Completion rate

The unit is a worked **Queue** row: an issue a **Run** bound at least once. The research
called this a "Pickup", but the glossary's **Pickup** is one bound unit of work, and one
row can hold several. The **Attempt lifecycle**, **Strikes** and **Review handoff** are
all scoped to the same row.

A row completes when the **Orchestrator** closes its issue on an `approved` **Review
verdict** (ADR-0072), in that Run. Nothing else counts as a completion, whether a human
or an Agent closes the issue, or it is closed as not planned. Help a human gives during
the Run is accepted as noise: the Runner writes with the operator's token, so that help
cannot be told apart. A human act that prevents closure, such as a **Stop** or a
handoff, already ends the row without a completion.

The **Completion rate** is completions over worked rows. Every worked row stays in the
denominator, including:

- **A block**, meaning a recorded blocker, `NO MORE TASKS`, or an **Acceptance ticket**
  that found gaps. Whether a block was legitimate cannot be decided mechanically, and
  excluding blocks would reward blocking.
- **A row the Run itself cut off** at its iteration cap, an interruption or a crash.
  Excluding these rows (censoring them) would hide work that fails slowly.

Non-completions are reported by **Status**, **Ending** and the Run's end reason. The
**Eventual completion rate** is reported beside it: the share of picked-up issues that
some Run completes, which shows whether work is being abandoned. Both rates are split by
dispatch mode, serial and **Parallel mode**.

## Spec closure is the Spec-level completion

Some Specs carry ADR-0082 acceptance, meaning an Acceptance ticket or a one-ticket graph.
For them, **Spec closure** is the Spec-level completion: the figure is the share of
those Specs the Orchestrator closed rather than a human. Its quality figure is the
**Story gap**, a User Story the Acceptance ticket found no closed ticket delivered. Gaps
are counted per User Story, and the figure records whether the Acceptance ticket's first
worked row found none. Spec closures of Specs from before 1.0.0 are reported but not
counted, because no story was proved for them.

The glossary already lists "Spec acceptance" as a term to avoid for Spec closure, so no
such term is added. The Spec-level signal is the Acceptance ticket together with Spec
closure.

## Quality guardrails

"Acceptance criteria met" is not a separate guardrail. Every completion carries an
approved Spec axis, which judges the result against the issue's acceptance criteria, and
Story gaps catch what a per-ticket review cannot see. The guardrails are:

| Guardrail | Baseline |
| --- | --- |
| Completions reopened within 30 days | 1.9% (measured with no window) |
| First-pass approval, and the Review-handoff rate | none yet |
| Integration failures per admitted contribution, with their **Recovery** outcome | none: no Event |
| Cost per completion: median and p90 **AI Credits** and active minutes, charging all spend, including rows that did not complete | 1,415 / 4,770 credits; 55.5 / 148.7 min |
| Share of spend on rows that did not complete | 43.8% |
| Story gaps per User Story | none yet |

The research's keyword proxy for follow-up defects (3.5%) is dropped: it neither proves
a defect nor catches an unlinked one.

## Two instruments

**Live measurement** is always on. The Python Runner computes the rates and guardrails
from Run Events, and reads the tracker only for reopens and Story gaps. Every figure is
labelled as measured history (ADR-0077) and never presented as a controlled comparison,
because issue mix, model drift and the stricter numerator all confound it.

A **Completion proof** runs before the cut. It works the same admitted **Proving tasks**
(ADR-0027) under the 0.11.1 stable release and under the 1.0.0 candidate's per-ticket
pipeline, which includes its **Agent profile** and **Review stage**. It follows the
research author's measurement hygiene:

- **Identical inputs.** Each task starts at its base commit, with its issue body as the
  work.
- **Fixed policies.** Each **Task type** has one **Routed pair**, and the SDK, CLI and
  Skill pins and the **Config** are the same in both arms.
- **Isolated histories.** Every repetition starts in a fresh worktree with harness memory
  off (ADR-0076), and nothing reads the tracker.
- **Randomized order and repeated trials.** The order of task and arm is randomized, the
  arms are interleaved, and each task is worked at least twice per arm.
- **Verified outputs.** Each result is scored fail-to-pass on the fix's own tests, and
  the whole AGENTS.md gate guards against regressions (pass-to-pass).
- **Blind review.** The **Reviewer** judges both arms' heads without knowing which arm
  produced them, and a human checks a random blind sample. **Calibration**'s three
  scoring keys stay unchanged (ADR-0027); the proof records the verdict beside them.

The proof is sized at about 30 tasks, roughly 120 repetitions costing 170,000 to 240,000
AI Credits. That can catch a gross regression but cannot prove a lift: telling 72% from
85% would take about 150 tasks per arm. It is therefore reported as measured on N tasks,
never as a confidence level, the same claim ADR-0027 declined to make for five Trials.
Effects on the task graph and the Spec cannot be measured one task at a time, so they
stay live-only.

## The 1.0.0 target

The Completion proof is a precondition of the cut. It asks for non-regression, not lift.
On the same tasks:

- the 1.0.0 arm completes at least as many tasks, verified, as the 0.11.1 arm;
- neither the oracle nor the gate regresses;
- blind review approves no smaller share;
- cost per completion is at most 1.5 times the 0.11.1 arm's, since review adds sessions.

The release rehearsal refuses a Python stable candidate at or above 1.0.0 unless a
committed Completion proof record meets those bars and names a measured revision in the
candidate's history. This follows ADR-0079 and ADR-0083: a by-exception Promotion never
meets the milestone, so only the rehearsal covers both routes. The rehearsal reads the
record and never runs a proof. Linking the evidence from the `v1.0.0` milestone remains
good tracker hygiene.

The live targets give direction and gate nothing:

- a Completion rate of at least 72%, the old unverified rate, now with every completion
  verified;
- an Eventual completion rate of at least 95%;
- at most 2% of completions reopened;
- at most 43.8% of spend on rows that did not complete.

## Where it lives

The **Event schema** gains what is needed to compute every worked row's outcome from Run
logs alone:

- the Orchestrator's closure of an issue;
- an Integration failure and its Recovery outcome;
- the Review Events ADR-0072 names;
- the Spec closure ADR-0082 requires.

Today `wrapper.auto_close` records only the closures made by the keyword backstop, and
`issues[].closed_at` is never set. The Task-graph execution PRD owns these Events and the
live figures. The web frontend shows a figure only as a signal declared by an Insight
capability.

The Completion proof is a committed research harness under `docs/research/`, like the
baseline's `completion-baseline/`. It reuses the Proving set and Trial modules, and it
becomes a product command only if it gets a second use. The Language paths & release PRD
owns the harness, the record's shape, its revision rule and the rehearsal's refusal.

## Scope

The GitHub Issues source on the Python path (ADR-0073), as for ADR-0082. The dormant
shell and PowerShell paths emit nothing new.

## Considered options

- **First-Run or eventual completion per issue as the headline.** First-Run completion
  misses repeat failures. Eventual completion, at 95%, measures persistence rather than
  leverage.
- **Every bound unit of work as the unit.** It has no baseline, and review remediation
  adds units.
- **Any closure inside the Run's time window**, the baseline's rule. It counts human and
  unverified closures.
- **Excluding rows a human touched.** This cannot be measured while the Runner shares the
  operator's token.
- **Excluding blocks, or censoring rows the Run cut off.** Excluding blocks rewards
  blocking, and censoring hides slow failure.
- **A "Spec acceptance" term, or gaps alone.** The glossary already avoids that term.
  Gaps alone would ignore Spec closure, the only Spec-level completion git-loopy records.
- **Only live before-and-after measurement.** It meets almost none of the hygiene list.
- **A proof sized for statistical confidence.** It would need at least 300 repetitions
  and 420,000 credits, likely more admitted tasks than this repository's history yields.
- **A lift target, gating on the live rate, or report-only.** A lift needs the
  confidence-sized proof, the live rate is confounded, and report-only states no bar.
- **A human check when the milestone closes.** A by-exception Promotion bypasses it.
- **A product command for the proof now, or research scripts for the live figures.** The
  first builds a comparison harness before there is a second use for it. The second
  leaves deterministic arithmetic out of the code.
- **A fifth PRD, or the Agent profiles PRD owning the proof.** The proof's verdict gates
  the cut, not a profile. A fifth PRD would separate the Events from the Runner changes
  that emit them.
