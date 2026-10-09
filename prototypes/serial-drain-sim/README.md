# Question: how does a plain issue's drain wait grow with Lane capacity, and what does each policy improve or worsen?

**THROWAWAY discrete-event evidence for #428 — not production, not a decision,
not a recommendation.** This nested `/prototype` records nothing on the tracker.
It is captured on `prototype/428-serial-drain` as the primary source for
ADR-0074 (`docs/adr/0074-the-serial-drain-waits-for-the-whole-lane-cohort.md`,
on `main` once its PR merges) and stays out of `main`. Only
`prototypes/serial-drain-sim/` is changed.

The three large generated tables (`metrics.csv`, `remote.csv`, `upper-bound.csv`,
about 12 MB) are not committed: they are deterministic outputs of `run.py
--write-results` and would weigh on every clone. `RESULTS.md`, `priority.csv`
and `age-fenced.csv` are committed; regenerate the rest to read any other cell.

## Run

From this worktree's root:

```bash
python3 prototypes/serial-drain-sim/run.py
```

Python standard library only; no installation, network, services, or persistent
simulation state. The committed numerical calibration snapshot is the input.
The full sweep prints its report; a single simulated Run takes milliseconds.
An N=500, 1,000-Lane batch took 0.007 seconds; the complete reported sweep
now covers 3,822 retained-policy local cells and 1,274 remote cells, each with
30 seeds, plus 24 priority-comparison and 144 age-layout cells. These are many
independent simulated Runs, not one long Run.
The earlier 113.7-second sweep included the now-dropped P2 scenarios.

Focused commands:

```bash
python3 prototypes/serial-drain-sim/run.py --validation
python3 prototypes/serial-drain-sim/run.py --cell --policy P0 --n 60 --workload B
python3 prototypes/serial-drain-sim/run.py --cell --policy P1c.1 --n 16 --workload C
python3 prototypes/serial-drain-sim/run.py --cell --policy P0 --pi 0.1 --n 16 --workload B
python3 prototypes/serial-drain-sim/run.py --cell --policy P1d --n 60 --workload B --age-layout plain-older
python3 prototypes/serial-drain-sim/run.py --trace --policy P1b --n 16 --workload A --lanes 6 --plain 8
python3 prototypes/serial-drain-sim/run.py --write-results
```

`--trace` surfaces time, base version, held/live/parked Lanes, admitted FIFO,
Integrator/serial ownership, latch, and ready demand at each event.
`--write-results` is the only report-persistence path: it regenerates
`RESULTS.md`, `metrics.csv`, `remote.csv`, `priority.csv`, and `age-fenced.csv`
in this prototype directory.
No simulation resumes from disk. `--seeds` defaults to 30; lower values are
exploration only, not the reported evidence.

To refresh the calibration **read-only** from the operator's main checkout:

```bash
python3 prototypes/serial-drain-sim/calibration.py ../git-loopy/.git-loopy/logs > prototypes/serial-drain-sim/calibration.json
```

The snapshot preserves only numeric durations, dispositions, counts, and
cohort exposure, not raw agent messages, issue bodies, or IDs.

## Shape and sources

This is the smallest reasonable exception to the skill's single-HTML logic-demo
shape: the requested artifact is a numerical stdlib discrete-event simulation.
No HTML, production integration, tests, external libraries, or UI polish.

House precedent, read at **df21f52**:

- `prototypes/rolling-dispatch-sim/sim.py` and its README:
  rolling versus barriers, one serialized Integrator, base staleness.
- `prototypes/rolling-concurrency-control/model.py` and its README:
  H=2, parking retains a slot, complete serial drains, fixed versus adaptive
  concurrency. This is the newer conflict-model source actually reused.

The probability, copied from that newer `Config` / `_begin_integration`, is:

```text
p(conflict or red gate) = min(0.85, 0.04 + 0.012 * waited_minutes
                                            + 0.055 * base_drift)
```

Wait is from agent-session finish to Integration start, including parking and
serial blocking. Drift counts both published Lane branches and serial commits
since reservation/branch cut. Its original unit-agnostic tick is interpreted
as one minute; that interpretation and the proxy itself are unverified.
The older sim's weights, 0.015/0.06, are not mixed in.
Each issue has one fixed uniform risk draw and a recovery need uniformly drawn
from {1,2,3,4}; 4 exhausts K=3. The initial gate takes g; each recovery attempt
takes 10+g. Recovery owns the Integrator and is outside the Lane cap.

Real-system sources read in this clean **origin/main ccb424df** worktree:

- ADR-0020, particularly “Serial interleave” and FIFO Integration;
- ADR-0008 (human eligibility and worktree isolation);
- ADR-0032, “The runner picks the oldest eligible issue”: priority first,
  oldest-first within each priority tier, with runner-bound serial Pickup;
- PRD #219, sections 1/4/5/6, and #198's resolution comment;
- `rolling_scheduler.py`: `reserve`, `refillable`, `serial_turn`,
  `serial_finished`, and `finish_work`;
- `loop.py`: `_drive_rolling`, reserve-first then demand latch.

## Shared engine

All policies use the same event heap, jobs, service durations, random draws,
H=2 Integrator, K=3 recovery, fallback jobs, and metric accumulators. N is fixed.
Reservations normally fill **all** currently refillable slots in one decision,
subject to H backpressure; P1d instead stops at the age fence. Sessions finish
independently. Admission releases a slot;
a parked finisher retains it. Admission creates an immediate new driver turn.
Integration serves admitted work FIFO, through recovery, before publishing.

P0 reserves first, then latches. Latching cancels nothing. Serial service waits
for full quiescence, runs one Iteration, advances base, then grants one full
refill decision before remaining demand can relatch. P1a/b/c and P3 retain
that full post-serial refill opportunity. **P1d is the explicit experimental
exception:** its age fence also limits that post-serial refill.
Integration exhaustion appends unlicensed serial fallback and immediately
forces a latch under every policy. There is no second Lane attempt.

Policies:

| Key | Change from P0 |
|---|---|
| P0 / P4 | Current policy; P4 is exactly the same samples, reported for legibility |
| P1a | Before ordinary refill, latch whenever plain demand exists |
| P1b | Before ordinary refill, latch if the oldest ready creation index is plain |
| P1d | At **every** refill, reserve only Lanes older than the serial head; priority plain heads fence every Lane |
| P1c.1 / P1c.3 | **Adopted** priority boundary behaviour: fraction pi=0.1 / 0.3 triggers a pre-refill latch; ordinary demand uses P0 |
| P3a | Reserve-first, but plain demand alone latches only when pending Lane supply is exhausted |
| P3b30/60/120 | Reserve-first, latch when oldest plain ready-to-now wait reaches W |
| P3c.1 / P3c.3 | Priority pi=0.1 / 0.3 pre-refill latch; otherwise P3a |

**Superseding maintainer ruling: P2 is dropped, not a candidate.** A new
partial-quiescence licence duplicates the existing `parallel-safe` overlap
assertion. The earlier implementation is retained only as inspectable throwaway
code; CLI/default sweeps exclude it. Its already-computed numbers are frozen in
`upper-bound.csv`, used **only by the "upper bound, NO LONGER A CANDIDATE"
appendix** of RESULTS. No further P2 experiments are performed and no new human
assertion is proposed.

Within the plain class, **every policy, including P0, serves
(priority first, then creation order)**, as ADR-0032 specifies.
Priority issues themselves are oldest-first. P1c/P3c additionally allow their
priority demand to cross the class boundary before Lane reservations.
Batch creation order randomly interleaves classes. P1b compares ready heads.
The open question is default precedence without a human assertion: P0
Lane-first, P1b oldest-ready-class first, P1a plain-first, or P3 deferral.
Default-precedence tables explicitly use **pi=0**; no priority flags exist there,
so their previous numbers remain unchanged. Priority-enabled comparisons run
P0 and P1c/P3c at the **same pi**. P1c is reported separately as adopted
boundary behaviour, not as a proposed default.

Event tie order: process same-time completions and arrivals; perform the driver
decision; already-admitted work has precedence. Every retained policy requires
full quiescence before serial work and keeps Integration mutually exclusive
with serial work. Historical P2 tie ordering is documented only in its appendix.

### Does oldest-first mean more drain cycles?

Not under the brief's P1b definition: peek at the oldest ready class, then,
if it is Lane work, reserve **all** refillable Lane slots. This is not an
age-fenced admission policy that stops when the next oldest issue is plain.
The one mandatory full refill turn after serial work also stays intact.
RESULTS includes both Lane-first and plain-first **strictly alternating**
creation-order witnesses at N=16/60, g=5/10/20, thirty paired seeds.
It reports cycle differences and the number of seeds with more P1b cycles.
The shuffled batch cells provide the broader interleaved-order comparison.
Age-fencing is now the separate **P1d**, not a silent reinterpretation of P1b.
At each refill, P1d takes only the prefix of Lane issues older than the oldest
waiting plain head. Once that head is the next candidate, P1d latches and
reserves nothing further. It drains fully, runs one serial Iteration, then
applies the same fence again. A priority plain head outranks every unlabelled
Lane regardless of creation index. Already-started work is never cancelled.
Integration exhaustion retains the mandatory serial-fallback override.

## Workloads, calibration, and paired comparison

- W-A: all twelve L∈{1,2,3,6}, Q∈{8,20,50} batches at t=0.
- W-B: L=2N, Q=20 batch at t=0.
- W-C: independent Poisson arrivals for 24h; Lane λ=0.03/min in hours 0–6
  and 12–18, λ=0.6/min in hours 6–12 and 18–24. Plain λ=0.015/min.
  Expected Lane/ plain counts are 453.6 / 21.6. Mean empirical S=41.8:
  unconstrained session loads 1.3 / 25.1 straddle N=16. Arrivals then stop and
  **all work drains**. Starvation means actual plain wait >24h, not censorship
  of an issue merely still waiting at horizon end.
- This bursty load overloads Integration. There is no honest stationary
  steady-state distribution to estimate; `postwarm_plain_mean` instead
  measures waits for arrivals after hour 6. Its tail is fully observed.
- N∈{3,6,16,20,40,60,500}; g∈{5,10,20}.
- Age-fenced focused comparison: P0/P1a/P1b/P1d, N=16/60, W-A L=6,Q=20
  and W-B L=2N,Q=20, g=5/10/20, pi=0. Three age layouts: shuffled;
  all plain issues older than all Lanes; all plain issues younger than all
  Lanes. Reordering retains each job's service/risk draws and within-class
  creation order, rather than drawing a different workload.
- Priority focused comparison: P0/P1c/P3c, pi=.1/.3, N=16/60, those same
  W-A/W-B supplies, g=10. Every comparator uses priority-enabled Pickup.
- Remote Actions use the same S and D plus 2 minutes of overlapping Lane setup.
  `remote.csv` covers every policy/N/workload, g=10; delta columns compare
  remote to its paired local Run. Gates are swept locally for all three g.
- Seeds are explicit integer formulas, no hash randomization. Each cell
  has 30 seeds; issue-level priority/risk/service draws are assigned
  before policy execution, so comparisons are paired.
  Historical licence draws remain unused solely to preserve the seed stream.
- Every synthetic Lane branch is durable. A serial Iteration clears one issue
  and advances base; exhausted contributions each create one fallback serial
  job. These are design assumptions, not measured closure probabilities.

Empirical C is extracted from paired contribution start/end events; S comes
from `summary.agent_seconds`, not C. C includes the Integration cascade and
would double-count it if used directly as S. Both distributions are reported.
For D, completed advanced/no_progress Iterations are paired start/end;
aborted/empty-Pool outcomes are omitted. Nonparametric resampling with
replacement is used, not a parametric fit. Incomplete Lane pairs are omitted.

**Mandatory validation is not silently declared passed.** The measured nine
first cohorts are reproduced by an observed-mechanics replay (dispositions and
recovery counts fixed, C decomposed at g=10), including ~14% observed exposure.
That checks accounting, not the stochastic predictor. Independent S/D
resampling with the inherited conflict model gives larger drains and a smaller
full-supply drain share. It is not independently calibrated to 33–87 minutes
and 14% together. The real Runs often stop without exhausting Q, have
unchanged branches, and have unobserved/censored exposure. We report and explain
that discrepancy **before** sweeping; no coefficients are tuned to hide it.

## Reading the output

`RESULTS.md` starts with validation, then focused default-precedence tables at
N=16/60 in W-A/W-B (oldest plain wait, Lane wait, makespan, drain share, cycles),
corrected P1c/P3c priority comparisons, P1d age-layout/gate comparisons, and
P1b interleaved-age cycle witnesses.
It continues with baseline capacity growth, retained policy/N tables for each
workload, P4 drain distributions, gate sensitivity, paired metric deltas, and
explicit limits. Historical P2 numbers appear only in the final rejected
upper-bound appendix. P4 includes both
per-Run seed ranges and pooled all-latch/positive-only distributions with the
actual maximum across seeds.

`metrics.csv`: each exact policy/N/workload/g/L/Q cell, all requested metrics,
each as `_med`, `_p5`, `_p95` across seeds, plus paired `delta_` columns versus
local P0 **at the same pi**. Non-boundary policies/defaults use pi=0; P1c/P3c
use their .1/.3 suffix, recorded as `priority_fraction`.
For B/C, L=Q=0 in the key means workload-generated supply; actual
counts are `lane_supply`/`plain_supply`. Never average B/C as empty batches.
For example, `plain_p95_med` is the median per-Run issue p95, not a pooled p95.
`remote.csv` uses the same schema, with paired deltas versus the local policy.
Neither file contains P2 rows. `upper-bound.csv` is the frozen appendix source
from the prior commit; its old schema and metrics are not candidate results.

`priority.csv` adds a `pi` key and contains matching priority-enabled P0
baselines. `first_priority_plain`, `priority_plain_mean`, `normal_plain_mean`,
`lane_mean`, and `makespan` supply the requested comparison and paired deltas.
Priority wait summaries condition on at least one priority issue; normal wait
summaries condition on at least one normal issue. Counts retain all Runs.
No empty subset is treated as a real zero wait. All priority Pickup benefits
now apply to the priority issue actually selected, not an ordinary FIFO head.

`age-fenced.csv` adds an `age_layout` key, and records all requested age-fence
metrics and paired deltas/ranges versus P0 for each layout. Main `metrics.csv`
also sweeps P1d across all N/workloads/g. `cohort_mean/p50/max` describe the
number of held/admitted contributions at latch for positive-duration drains.
`drain_count` excludes immediate grants; `drain_grants` includes them.

Makespan includes completion after the 24h arrival horizon. Integrator
utilization includes gate/recovery minutes; drain share includes latched waiting
but not the serial Iteration. Idle and parked slot-minutes sum over slots.
Cycles count Lane-service epochs followed by serial service, not every
back-to-back serial Iteration. First plain means the oldest original plain
issue, not a newly created fallback.
A serial-only initial prefix and Lane-only final suffix do not form a complete
Lane→serial cycle. A zero cohort paired with zero drains means no positive
drain occurred, not an observed zero-size Integration cohort.
`idle_slots_total` counts all unheld Lane-slot minutes; `idle_with_lane_demand`
counts only time with ready Lane work still waiting, avoiding dilution by a
plain-only tail. `agent_slot_util` counts productive S time, not parking;
`held_slot_util` includes setup/parked ownership. These distinctions matter
when P1d changes when Lanes wait rather than just total makespan.

## Not shown

Real gate times, realistic conflict/recovery probabilities,
adaptive contraction, host contention, rates/credits/cost, real retry/closure
probabilities, changing readiness/eligibility, setup failures, Stops/Strikes/caps,
dirty-base handling, Checkpoints/source refresh, cross-Run locks, or Actions
queueing. Age-fenced reservations and within-plain priority selection are now
modelled, without weakening full quiescence.
S's recovery attribution and censored tails are uncertain. Results
are conditional design evidence, not operational forecasts or a policy winner.
