# End-to-end completion baseline from past Runs

Facts for [Define end-to-end completion and its quality guardrails](https://github.com/bradcstevens/git-loopy/issues/662) on the
[Wayfinder: git-loopy 1.0.0 on the Python path](https://github.com/bradcstevens/git-loopy/issues/648) map, resolving
[Measure the end-to-end completion baseline from past Runs](https://github.com/bradcstevens/git-loopy/issues/657).
Measured 2026-10-11. No decision is made here.

## What was measured, and how

- **Run logs:** every `*.jsonl` under the operator's main-checkout `.git-loopy/logs/` (110 Run logs, 2026-07-18 → 2026-10-02),
  read only. A further 8 Run `.log` files with no `.jsonl` are preflight refusals (missing Skills, refused route, an SDK crash)
  that did no work and are excluded.
- **Tracker:** GitHub GraphQL timeline (`ClosedEvent` with closer, `ReopenedEvent`, `CrossReferencedEvent`) for every
  issue a Run picked up, fetched read-only via `gh api graphql`.
- **Unit:** a **Pickup** = one (Run, issue) pair the Run worked — evidenced by `wrapper.issue.activated`,
  `wrapper.pickup.bound`, `wrapper.contribution.*` or an `issues[]` entry on `wrapper.iteration.end`. Runs before
  2026-07-24 log a bare `wrapper.iteration.end`; there the issue is inferred from Checkpoints, `wrapper.auto_close`,
  and the agent's own `gh issue close|view|edit|comment N` calls.
- **"Closed by the Run":** the tracker shows a `ClosedEvent` between the Run's first event and 15 minutes after its last.
  All closes carry the operator's token, so the actor cannot distinguish a Run from a concurrent human; the window is
  the attribution.

Scripts and data live beside this file in [`completion-baseline/`](completion-baseline/):

| File | Role |
| --- | --- |
| `extract_runs.py <logs-dir> > runs.json` | One record per Run and per Pickup from the Event logs |
| `fetch_tracker.py runs.json > tracker.json` | Tracker history for every picked-up issue (not committed: re-fetch) |
| `analyze.py runs.json tracker.json` | The join and every number below |
| `runs.json` | Snapshot of the extract, since local logs are not durable |
| `baseline.txt` | The exact `analyze.py` output these findings quote |

## Findings

### Completion

| Measure | Value |
| --- | --- |
| Runs with ≥1 Pickup | 81 of 110 (43 in Parallel mode) |
| Pickups | 479 over 327 distinct issues |
| Pickup closed in its Run | **345 (72.0%)** — 291 with work evidence, 54 with only no-progress/aborted/no record |
| Advanced, not closed | 25 (5.2%) |
| No-progress | 53 (11.1%) + 15 early-schema Pickups with no close (3.1%) |
| Aborted | 15 (3.1%) |
| Gone (activated, no end record) | 26 (5.4%) |
| Close rate by mode | serial 81.6% (200/245) · Parallel-mode Runs 62.0% (145/234) |
| Distinct issues eventually closed by a Run | **311 / 327 (95.1%)**; closing Pickup had work evidence in 264 |
| Closed on the first Pickup | 252 / 327 (77.1%) |
| Pickups per closed issue | median 1 · p90 2 · mean 1.3 |
| Iterations per closed issue | median 1 · p90 3 · mean 1.8 |
| The 16 never closed by a Run | 13 closed outside a Run as completed (7 by hand, 4 by PR, 2 by commit), 1 not planned, 2 still open |

How a Run's close happened: 228 a bare agent `gh issue close`, 114 a closing keyword in a commit pushed to `main`,
3 a merged PR. **No close passed an independent verification** — none exists yet, so "verified close" has no
measurable baseline beyond these mechanisms.

### Cost

| Measure | Value |
| --- | --- |
| Active minutes per closed issue (all Pickups to close; n=226 with timing) | median 55.5 · p90 148.7 · mean 81.8 |
| AI Credits per closed issue (n=171 where every Pickup recorded credits) | median 1,415 · p90 4,770 · mean 2,518 |
| Tokens per closed issue (n=287) | median 17.7M · p90 60.2M · mean 29.2M |
| Share of recorded AI Credits spent on Pickups that did not close | 43.8% (of 493,876 over 266 Pickups) |
| Share of recorded active time on Pickups that did not close | 33.3% (of 361.9 h) |

Credits are recorded only by later Runs; early Runs log tokens alone.

### Strikes, Skips, `NO MORE TASKS`

- **Strikes:** 123 warnings, 25 aborts; 25 Runs ended `stuck`. Run outcomes: 30 `iteration_cap`, 25 `stuck`,
  11 `empty_pool`, 7 `all_skipped`, 6 `all_blocked`, 1 operator Stop, 1 interrupted, 29 with no `wrapper.run.end`.
- **Skips (1,999 total):** 1,772 `blocked_by_open_dependency`; re-pick refusals after an earlier attempt this Run:
  103 no-progress, 65 `no_more_tasks`, 41 timeout, 18 crash.
- **`NO MORE TASKS`:** emitted in 34 of 644 Iterations, across 28 Pickups; 7 of those Pickups' issues still closed in the Run.
- **Iteration outcomes:** 303 advanced, 181 no-progress, 19 aborted, 7 all-skipped, 6 all-blocked, 128 early-schema (unrecorded).

### Parallel mode

43 Runs started in Parallel mode, but **327 `wrapper.parallel.serial_fallback`** events (mostly
`no_parallel_safe_candidates`) mean almost all of their work ran serially. Only 38 Lane contributions ended:
26 published, 7 unchanged branch, 5 serial fallback; 12 `wrapper.integration.published`.
**Integration failures and auto-resolution outcomes cannot be measured**: no Run log contains an Event for either.

### Quality proxies (over the 311 issues a Run closed)

- **Reopened after the Run's close:** 6 (1.9%) — #251, #252 (reopened the same day), and #603, #604, #607, #608
  (reopened together on 2026-09-26, re-closed 2026-10-10).
- **Referenced by an issue created after the close:** 53 (17.0%); of those, a bug-labelled or
  fix/regress/follow-up/broken/revert-titled issue for 11 (3.5%): #72, #205, #219, #251, #252, #304, #316, #356, #396, #412, #456.
  This is a keyword proxy; it neither proves a defect nor catches unlinked follow-ups.

## Limits

- Attribution is by time window; a concurrent human close inside a Run's window counts as the Run's. The 54
  "closed without work evidence" Pickups are where that, or an "already done" close, would sit.
- Early-schema Runs (before 2026-07-24) infer the worked issue, so their Pickup counts are a lower bound.
- `closed_at`/`issue_elapsed_seconds` are never populated in any `issues[]` entry; closure comes from the tracker only.
- Four Pickups appear in both an Iteration and a Lane contribution record; their cost may be double counted.
