# Demotion is counted per pair, applied after the Run, and steps up into the unmeasured

**Status:** accepted

[ADR-0027](0027-routing-is-calibrated-by-measurement.md) rests its whole tolerance for
five-sample evidence on **Demotion** — *"Search does not have to be right. It has to be cheap
and reversible."* Its rule was *"a **Measured routing** entry whose pair accumulates consecutive
**Strikes** on real work is removed."* That sentence does not compile against the code, and
[ADR-0028](0028-measured-routing-is-a-committed-tier.md) separately recorded the write path as
*"unresolved."* We count per-pair no-progress from the persisted run record instead, apply the
result **after** the Run, and step **up** the price staircase into a pair nobody has measured —
recorded under a new status so it can never be mistaken for one that was.

## Why the stated rule cannot work

**Strikes are not per-pair.** `NMTStrikeStateMachine` (`wrapper.py:255`) is instantiated **once
per Run** (`loop.py:886-887`), and all Lanes *"share ONE Strike machine"* (`loop.py:1921-1923`).
`.tick()` **resets the counter to 0 on any progress**. In Rolling mode, concurrent Lanes run
*different* routed pairs against one counter — so a good pair committing work erases the strikes
a bad pair was accumulating. There is no per-pair strike signal in the system to read.

ADR-0027 used precisely this fact to disqualify bandit exploration. It did not notice the same
fact disqualifies its own demotion rule.

**The Run dies first.** `max_strikes` defaults to **3**, and reaching it ends the Run. So any
threshold of 3 or more means Demotion **never fires**, and 1–2 means it fires on noise. The
usable range is empty at one end and worthless at the other.

**The write has no mechanism and a race.** `_maybe_push` (`loop.py:1391`) pushes only when an
Iteration produced commits or a **Checkpoint**; nothing commits an arbitrary tracked file
mid-Run. And `routing.measured.toml` is a single tracked file at the repository root while N
Lanes work in N worktrees.

## Decision

### Count per pair, from the record

The routed pair is bound onto the Contribution at Pickup (`loop.py:2554`) and every session's
usage lands in `.git-loopy/runs/*.json` regardless of cost-meter wiring. **The record already
knows which pair worked which issue and whether it made progress.** Demotion reads that, not the
Strike machine — which yields a genuine per-pair signal and sidesteps the shared counter
entirely.

> **Amendment (#366, on implementation): the record is the Contribution, not the run file.**
>
> The second sentence above is false, and false in exactly the mode Demotion applies to. A
> **Lane** session is constructed with `event_observer=self._cost_meter`, *not* the rollup
> accumulator, and Lane contributions emit no `wrapper.iteration.start` / `.end` at all
> (#219/#306, and a standing comment in `loop.py` says so). `IterationRollupAccumulator`
> discards everything while it holds no open Iteration, so `.git-loopy/runs/*.json` contains
> **no Lane rows whatsoever** in Rolling mode. "Usage lands in the run record regardless of
> cost-meter wiring" describes the JSONL event log, which is a different file with a different
> writer; the run *summary* is not built from it.
>
> The two facts Demotion needs are nonetheless recorded together, in one place and at one
> moment: `RollingScheduler.finalized` — a `tuple[Contribution, ...]`, each row carrying the
> **Routed pair** bound at Pickup (`model` / `reasoning_effort`) and the terminal `reason` set
> at finalization, where `published` is the only progress. The decision is unchanged and only
> its *source* moves: **per-pair, per-progress, from the Run's finalized contributions.**
>
> This is strictly better than what was written. The rows are in memory at the quiescent point
> the next section already required, so Demotion parses nothing and opens no second file; and
> the pair is the one actually **bound**, not one inferred from a `consumption.model` field that
> carries no effort and so cannot name a pair at all. The last consequence below is amended with
> it: the exposure is not "a session type that skips the run record" but a contribution that is
> never finalized — and a Run that never finalized a contribution has, correctly, nothing to say
> about any pair.

The Strike counter keeps its existing job unchanged: ending a Run that is going nowhere. It was
never a per-pair quality measure and is not made into one.

### Apply it after the Run

Nothing required Demotion to happen mid-Run, and moving it out **dissolves both remaining
problems at once**: there is no concurrent-Lane race because no Lane is running, and there is no
need to invent a mid-Run commit mechanism because the write happens at a quiescent point. The
change arrives as a reviewable, revertible commit — exactly the property ADR-0028 committed the
artifact to obtain.

This closes ADR-0028's open question about **Checkpoint** and push durability
([ADR-0004](0004-runner-checkpoint-and-push-durability.md)) by removing the interaction rather
than resolving it.

### Step up, into the unmeasured, and say so

Demotion must fall back to *something*, and the obvious answer — the next-cheapest pair the
Calibration already measured — **does not exist.** Cheapest-first search walks ascending and
stops at the first pass, so every measured rung sits *below* the winner and **failed**, and
nothing above it was ever trialled. The set of measured pairs above the winner is empty by
construction.

So Demotion steps **up the price order into a pair nobody has measured.** That is the right
direction — ADR-0027 already prefers it, noting that a strict bar *"biases toward false fails —
toward stepping up the staircase — which is the cheap error"* — but it means the tier now holds
a value that was never measured.

That requires a **fourth `MeasuredStatus`**. The shipped enum (`measured_routing.py:102-117`)
has three, and `DEMOTED` **clears the pair**, with its field set fixed at
`{status, demoted_model, demoted_effort, demoted_after_strikes}` (`measured_routing.py:539-545`)
and the loader enforcing mutual exclusion against a live one. A provisional pair carrying
`status = measured` would be the third instance of the failure this design has already ruled
out twice: *"a stopped search must look stopped"*, and ADR-0026's rule that an unknown cost
renders as unavailable, never as zero. **An unmeasured pair must look unmeasured.**

Demotion then **notifies** that the task type needs re-calibration rather than re-searching on
its own — ADR-0028's notify-don't-act rule, applied unchanged, and for the same reason: an
implicit trigger converts an unattended Run into a benchmark suite.

## Considered options

- **Add real per-pair Strike counters** — rejected. It duplicates a Run-control mechanism to
  serve a routing question, and the record already carries the data.
- **Use the shared counter as ADR-0027 wrote it** — rejected. Concurrent Lanes contaminate it,
  and the usable threshold range is empty below the Run-ending limit of 3.
- **Demote mid-Run** — rejected. Requires inventing a commit mechanism and a merge story for N
  worktrees racing on one tracked file, for no benefit over waiting until the Run ends.
- **Demote only when a human next runs `calibrate`** — rejected as too slow; a bad pair keeps
  working real issues until someone remembers.
- **Demote as a proposal a human accepts** — rejected. The commit is already the review.
- **Fall through to `RECOMMENDED_ROUTING`** — rejected, though it is what the shipped schema
  does today. One bad streak would discard the entire measurement in favour of the unmeasured
  hand-authored guess ADR-0028 demoted to a bootstrap default.
- **Refuse to route the task type until re-calibrated** — rejected. It converts a routing
  regression into a work stoppage.
- **Resolve the fallback at read time and never record it** — rejected. The artifact would show
  a demoted entry while the system quietly ran on something the file does not name.

## Consequences

- **A fourth status is a schema change** to a tier that has already shipped
  (`measured_routing.py`), including its loader invariants, its tests, and the conformance
  fixture ADR-0028 requires. **Discharged by amendment:** it shipped as `provisional` (#376) —
  schema, loader field set, round-trip, the precedence contribution, the
  `provisional (unmeasured)` reporting tier, the Wrapper contract's status table and a
  `routing-resolution` conformance case. Its term is **Provisional**, and because that state is
  reachable — such a record loads, routes and reports itself apart — the term is a `## Language`
  entry in `CONTEXT.md`, where **Demotion** is still only a decision. What has *not* shipped is
  the writer: nothing puts a **Provisional** record into the artifact until this entry's
  mechanism exists.
- **The shipped `demoted` state is left without a writer.** Nothing in this decision produces
  one. Demotion writes `provisional`, because `demoted` *clears* the pair and so falls through
  to the hand-authored bootstrap this entry rejects by name above; and its
  `demoted_after_strikes` field encodes the consecutive-**Strike** rule *"Why the stated rule
  cannot work"* disproves outright. The state is therefore **superseded**, not pending. It stays
  in the schema because it has shipped and a loader must still read a hand-placed one — retiring
  the field set is an artifact schema change and belongs to the mechanism (#366). `CONTEXT.md`
  records the same, so the glossary does not send that implementer at the wrong record.
- **The tier can now hold unmeasured values.** "Measured routing" becomes a slight misnomer;
  the name is kept because the status field carries the distinction precisely.
- **Demotion needs a threshold nobody has chosen.** This entry establishes *what is counted*
  (per-pair no-progress contributions, from the record) but not *how many*. Unlike the Strike
  limit it is not bounded by anything structural, so it is a free parameter and should be
  configurable.
- **Demotion is only as good as the record.** Sessions invisible to `RunCostMeter` still write
  usage to the JSONL log, so the signal survives — but any future session type that skips the
  run record entirely would be invisible to Demotion too.
- **Nothing demotes in serial mode**, because nothing routes there.
