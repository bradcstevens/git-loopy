# PROTOTYPE — rolling-vs-barrier scheduler sim

> **Throwaway.** Answers one question for wayfinder ticket
> [#133](https://github.com/bradcstevens/git-loopy/issues/133) on map
> [#131](https://github.com/bradcstevens/git-loopy/issues/131). The *findings*
> belong in the ADR/PRD; **this code does not ship.**

## The question

> How does a rolling/greedy dispatcher behave when the **serialized
> Integration** (ADR-0009: merge each Lane's branch one-at-a-time, re-run the
> full feedback loops, K≤3 auto-resolution) meets many Lanes finishing at once?
> Does rolling just **move the bottleneck onto the single Integration server**,
> and where does **added risk** creep in?

## Run it

```
python3 prototypes/rolling-dispatch-sim/run.py            # interactive stepper
python3 prototypes/rolling-dispatch-sim/run.py --compare  # headless side-by-side table
python3 prototypes/rolling-dispatch-sim/run.py --selftest # invariant checks
```

Stdlib only, no deps. `[space]` steps one tick; `[m]` toggles Wave↔Rolling;
`[b]` backpressure; `[+/-]` N; `[l]` rate limit; `[c]` side-by-side numbers;
`[r]` run to completion; `[q]` quit.

## What it models (faithful to `git-loopy/python/git_loopy/loop.py`)

- **Wave (today)** = `_run_wave()`: sequential pre-barrier worktree **setup** →
  one `asyncio.gather` **barrier** (the Wave waits on the slowest Lane) →
  **serialized Integration** (`_integrate_wave`, ascending issue order, gate
  re-run, K≤3 auto-resolution, then serial fallback) → Strike → next Wave.
- **Rolling** = refill each Lane slot from the pool the instant it frees, with
  the *same single* Integration server draining continuously.
- **Rate limit** = a per-tick token bucket over WORK Lanes — the undocumented
  per-minute API ceiling (429 ⇒ no-progress, burns quota) from research #132.
- **Added-risk proxy**: a branch's merge/gate-conflict probability rises with
  how long it waited in the Integration queue + how far `base` drifted since it
  was cut. Stale branch ⇒ more conflicts ⇒ more auto-resolution ⇒ deeper queue.

## What it shows (default: 12 issues, N=3, seed=42)

| | Wave (today) | Rolling | Rolling+BP |
|---|---:|---:|---:|
| Makespan (lower=faster) | 141 | **86** | 98 |
| Integration-server busy | 46% | **90%** | 85% |
| Max integ queue depth | 3 | 5 | **3** |
| Worst queue wait | 17 | **37** | **13** |
| Stale conflicts | 3 | 5 | 4 |

- **Rolling ≈ halves makespan** and **drives Integration to ~90% busy** — yes,
  the bottleneck **moves onto the serialized Integration**. Toggle rate limit
  off and sweep N: past N≈2 Integration saturates (~93%) and makespan *stops
  improving* — extra Lanes just pile into the queue (max depth 3→10, worst-wait
  16→80).
- **Added risk = staleness**: rolling's deeper queue makes branches wait ~2×
  longer before landing, so conflicts/auto-resolutions rise.
- **Crank N (`+`)**: the *second* ceiling appears — 429 throttles explode with
  no makespan gain (quota burned for nothing).
- **Backpressure (`b`, WIP cap 2)** keeps most of rolling's throughput while
  bounding queue depth and **halving staleness** — the seed recommendation:
  rolling dispatch **+ a WIP cap on the Integration queue** (+ overlap setup).

## Simplifications (discount these)

- Integer ticks (~minutes), unit-agnostic.
- Only WORK Lanes draw the rate bucket; auto-resolution sessions don't (in
  reality they would, making rolling's rate pressure *worse*, not better).
- Conflict probability is a smooth proxy, not a real merge model.
- One Integration server, FIFO for rolling / issue-order for wave — the
  rolling ordering is itself an open design choice (see #135).
