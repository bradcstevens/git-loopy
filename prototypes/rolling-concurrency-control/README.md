# PROTOTYPE — Lane-cap and Integration-backlog concurrency control

> **Throwaway.** This artifact supports
> [Prototype Lane-cap and Integration-backlog concurrency control](https://github.com/bradcstevens/git-loopy/issues/199)
> under
> [Wayfinder: Keep every Lane busy — rolling-dispatch scheduling for Parallel mode](https://github.com/bradcstevens/git-loopy/issues/131).
> It captures the confirmed HITL answer as prototype evidence, not production
> code.

## Question

Which bounded policy should control Lane utilization when a user-configured
**Lane cap**, a hard **Integration high-water**, API/credit/host pressure, and
auto-resolution compete under Rolling dispatch?

The model preserves the locked design:

- the configured Lane cap is an upper bound;
- Integration is one event-driven FIFO server;
- `H` counts the integrating candidate plus FIFO-admitted candidates;
- a finisher parks in its Lane when `H` is full;
- a candidate owns private Integration/gating/auto-resolution state and only a
  green base is published;
- one validated serial demand arrives during the run, latches refill off,
  drains Lane and Integration state fully, receives one unchanged serial turn,
  and then grants one full rolling refill turn;
- roundless accounting and Dashboard behavior are not modeled.

## Run

```bash
python3 prototypes/rolling-concurrency-control/run.py --report
python3 prototypes/rolling-concurrency-control/run.py
```

Stdlib only. The report compares fixed and adaptive policies, sweeps
Integration high-water values, compares auto-resolution budget treatment, and
prints every contraction/recovery with its pressure snapshot. The TUI exposes
the complete state after each tick.

## Model boundaries

- Integer ticks and synthetic work durations.
- A deterministic pressure burst improves after ticks 35 and 75 so hysteresis
  and recovery can be observed.
- 429 thresholds and host/credit weights are proxies because
  [Research: Copilot SDK/API concurrency & cost ceilings for maximal Lane throughput](https://github.com/bradcstevens/git-loopy/issues/132)
  found that GitHub does not publish a numeric concurrency/rate ceiling.
- Conflict probability is a branch-wait/base-drift proxy, not a real merge.
- The model does not decide Strike/Checkpoint/Summary cadence or Dashboard
  representation.

## Evidence (default scenario)

Default: 36 Parallel-safe work items, configured Lane cap 6, deterministic seed
199, one validated serial demand, and `H=2` (one integrating + one FIFO
admitted).

| Measure | Fixed cap 6 | Bounded adaptive |
|---|---:|---:|
| Makespan | 297 | **271** |
| Peak active / parked | 6 / 4 | **4 / 1** |
| Integration busy / high-water full | 88% / 71% | 82% / **45%** |
| Auto-resolution ticks / attempts | 72 / 24 | **33 / 11** |
| 429 throttles | 10 | **0** |
| AI credits / wasted | 473.2 / 2.0 | **408.8 / 0.0** |
| Integration wait p95 / max | 37 / 38 | **14 / 15** |
| Max staleness / conflicts | 50 / 13 | **20 / 7** |
| Host peak / overloaded ticks | 7.0 / 30 | **4.2 / 0** |
| Serial drain wait / refill turns | 41 / 1 | **24 / 1** |

The adaptive run starts from the research-backed safe default of
`min(configured cap, 3)`, explores upward one Lane at a time, and moved only
between 2 and 4. It retained throughput while cutting simulated credit burn
14%, p95 Integration wait 62%, peak parked work 75%, and staleness 60%.

### Integration high-water

| `H` | Makespan | Integration busy | p95 / max wait | Credits | Conflicts |
|---:|---:|---:|---:|---:|---:|
| 1 | 349 | 56% | 6 / 10 | 365.6 | 2 |
| **2** | **271** | 82% | **14 / 15** | **408.8** | **7** |
| 3 | 291 | 87% | 34 / 39 | 456.8 | 12 |
| 4 | 321 | 90% | 52 / 55 | 514.4 | 17 |

`H=1` starves Integration; `H>2` buys little utilization while sharply
increasing wait, staleness, auto-resolution, credit burn, and serial-drain
latency. `H=2` is the provisional knee.

With `H=2`, allowing Integration pressure alone to contract from 2→1 changed
makespan 271→279 while leaving credits, wait, staleness, and conflicts
unchanged. A floor of 2 therefore preserves the better feed rate; the separate
429/credit/host rules may still contract to 0 when continuing Lane attempts is
itself the pressure source.

### Auto-resolution budget edge case

With the selected `H=2`, a long-running conflicting candidate, one FIFO-admitted
candidate, and four parked finishers:

| Policy | Complete by tick 120 | Landed | Parked | Auto budget wait |
|---|---:|---:|---:|---:|
| Auto-resolution outside Lane cap | yes (tick 42) | 6 | 0 | 0 |
| Hard shared Lane/auto budget | **no** | 0 | 4 | 99 |

A hard shared budget circularly waits: private Integration needs a budget slot,
while four parked finishers retain Lane occupancy and cannot enter the full
Integration backlog. Auto-resolution therefore cannot be delayed behind Lane
capacity; it can instead contribute to 429, credit, and host pressure.

## Confirmed ADR/PRD reaction table

| Signal/state | Confirmed reaction |
|---|---|
| Startup, complete signals | Effective cap starts at `min(user Lane cap, 3)` |
| Integration WIP reaches `H=2` | `H` includes the integrating candidate + one FIFO waiter; stop refill and park a completing Lane |
| `H` full + parked Lane for 4 of 6 ticks | Cap −1 after cooldown, no lower than 2 for Integration pressure alone |
| At least 3 observed 429s in 6 ticks | Cap −2 after cooldown |
| Credit burn above 110% target for 6 ticks | Cap −1; target must be explicitly configured from authoritative AI-credit telemetry |
| Host/setup pressure above 102% for 6 ticks | Cap −1; pressure is the maximum configured CPU, memory, disk/worktree-I/O, or setup concurrency/latency ratio |
| Several signals fire in one window | Apply one strongest reaction only: 429 −2 wins, otherwise one −1 |
| Repeated external pressure at cap 1 | Cap 1→0; active work continues and Integration drains |
| A prior contraction is still draining | Do not contract again |
| 10 healthy observations after a 5-observation cooldown | Cap +1, never above user Lane cap; healthy means zero 429s, no parked work, `H` full ≤1/6, credit and host below 85%, and remaining eligible demand |
| Any required pressure signal/configuration unavailable | Freeze at `min(user Lane cap, 3)` with hard `H=2`; never estimate missing signals |
| Parked completed work | Retains and consumes its Lane |
| Auto-resolution | Starts immediately outside Lane cap; counts toward rate, credit, and host pressure |
| Validated serial demand | Latch refill off, drain fully, run one serial Iteration, grant one full refill turn |

This intentionally leaves roundless bookkeeping and Dashboard behavior to their
downstream decisions.
