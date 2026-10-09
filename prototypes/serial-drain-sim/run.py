"""THROWAWAY evidence, not a policy recommendation. Python standard library only."""

import argparse
import csv
import json
import time
from pathlib import Path
from statistics import fmean

from model import CANDIDATES, Engine, Job, NS, UPPER_BOUNDS, percentile, policy_pi, scenario

HERE = Path(__file__).resolve().parent


def summary(rows):
    result = {}
    for key in rows[0]:
        values = [r[key] for r in rows]
        metric = key.removeprefix("delta_")
        if metric in {"priority_plain_mean", "priority_plain_p95", "first_priority_plain"}:
            values = [r[key] for r in rows if r["priority_plain_count"] > 0]
        elif metric == "normal_plain_mean":
            values = [r[key] for r in rows if r["normal_plain_count"] > 0]
        result[key] = tuple(percentile(values, p) for p in (.5, .05, .95))
    return result


def cell_seed(workload, l, q, seed):
    # No randomized Python hash; identical issue-level draws across policies/g.
    return 428_000_000 + {"A": 1, "B": 2, "C": 3}[workload] * 1_000_000 + l * 10000 + q * 100 + seed


def validation(calibration, seeds):
    text = [
        "## Validation — before sweeping",
        "",
        "Observed-mechanics replay fixes each first cohort's terminal disposition and",
        "recorded recovery count, and uses `S_replay = C - g - attempts*(10+g)`",
        "(unchanged branches use C). Thus it checks FIFO/drain accounting, **not**",
        "the accuracy of a fitted service/conflict model. It is deliberately labelled",
        "a replay, not an independent prediction. Negative start offsets below a",
        "minute are ignored. g=10, N=16; plain supply is the observed Q.",
        "",
        "The independent column uses the unchanged stochastic model, empirical",
        "agent_seconds S, empirical D, all branches durable, and 30 seeds per shape.",
        "",
        "| L / Q | Observed drain | Mechanics replay | Stochastic drain median [p5,p95] | Full-supply drain % | Observed Run span |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    replay_sum = 0
    stochastic_total, stochastic_span = 0, 0
    for row in calibration["validation_cohort"]:
        jobs = []
        for i, lane in enumerate(row["lanes"]):
            unchanged = lane["reason"] == "unchanged_branch"
            duration = lane["duration"] if unchanged else max(
                0, lane["duration"] - 10 - lane["attempts"] * 20
            )
            jobs.append(Job(
                i, "lane", 0, duration, 1, 1, 0, 0,
                unchanged=unchanged, forced_attempts=lane["attempts"],
                forced_published=lane["reason"] == "published",
            ))
        l, q = len(jobs), row["plain_supply"]
        for i in range(q):
            jobs.append(Job(l + i, "plain", 0, 1, 1, 1, 0, 0))
        replay = Engine(jobs, 16, "P0", 10).run()["first_drain"]
        replay_sum += replay
        samples = [
            Engine(scenario(calibration, 16, "A", cell_seed("A", l, q, s), l, q),
                   16, "P0", 10).run()
            for s in range(seeds)
        ]
        sm = summary(samples)
        stochastic_total += fmean(r["first_drain"] for r in samples)
        stochastic_span += fmean(r["makespan"] for r in samples)
        text.append(
            f"| {l} / {q} | {row['observed_drain']:.1f} | {replay:.1f} | "
            f"{interval(sm['first_drain'])} | {sm['drain_share'][0]*100:.1f} | "
            f"{row['observed_span']:.1f}{'*' if not row['span_has_end'] else ''} |"
        )
    observed_total = sum(r["observed_drain"] for r in calibration["validation_cohort"])
    observed_span = sum(r["observed_span"] for r in calibration["validation_cohort"])
    text.extend([
        "",
        f"Observed: {observed_total:.1f}/{observed_span:.1f} = {observed_total/observed_span:.1%}. "
        f"Mechanics replay: {replay_sum:.1f}/{observed_span:.1f} = {replay_sum/observed_span:.1%}.",
        f"Independent full-supply model: expected first drains sum {stochastic_total:.1f} min; "
        f"expected spans sum {stochastic_span:.1f} min; ratio {stochastic_total/stochastic_span:.1%}.",
        "",
        "**Calibration limitation, not a passed predictive validation:** the mechanical",
        "replay reproduces 33–87-minute drains and ~14%, but the independent model does",
        "not reproduce that combination. The actual nine Runs do not exhaust the",
        "8–50-issue plain supplies; observed stopping/censoring budgets differ from",
        "a sim that finishes one issue per Iteration. Some real branches are unchanged;",
        "the sweep instead integrates every branch, and its inherited conflict proxy",
        "adds recovery not observed in those cohorts. Adding g/recovery to C directly",
        "would double-count Integration, so the stochastic model uses agent_seconds.",
        "No conflict coefficient or D distribution was tuned to force a 14% pass.",
        "Sweeps below are conditional design evidence, **not calibrated forecasts**.",
        "The replay denominator is observed exposure, not simulated all-issue makespan.",
        "* = no run.end: last wrapper-event time supplies the observed span.",
        "",
    ])
    return "\n".join(text)


def interval(values, digits=1):
    med, lo, hi = values
    return f"{med:.{digits}f} [{lo:.{digits}f},{hi:.{digits}f}]"


def number(cell, key):
    return cell[key][0]


def focused_report(cells):
    text = [
        "## Current decision surface: default precedence, no human assertion",
        "",
        "**P2 has been dropped:** a new overlap licence would duplicate",
        "`parallel-safe`, the existing human overlap assertion. Historical P2",
        "numbers appear only in the rejected-candidate upper-bound appendix.",
        "**P1c is adopted boundary behaviour**, not a proposed default selection.",
        "",
        "Default comparisons explicitly set pi=0: P0=Lane-first,",
        "P1a=plain-first, P1b=oldest-ready-class first, P1d=age-fenced oldest-first,",
        "P3=Lane-favouring deferral. Every serial Pickup sorts priority first,",
        "then creation order (ADR-0032), including P0. P1d fences even the",
        "post-serial refill; other policies retain one full refill turn.",
        "Representative W-A is L=6,Q=20;",
        "all twelve W-A supply cells remain in metrics.csv. g=10 here;",
        "g=5/10/20 is swept for every retained policy and N.",
        "",
        "| Default | N | Workload | Oldest plain [p5,p95] | Mean Lane [p5,p95] | Lane job p95 | Makespan [p5,p95] | Drain % [p5,p95] | Alternation cycles [p5,p95] |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in ("P0", "P1a", "P1b", "P1d", "P3a", "P3b30", "P3b60", "P3b120"):
        for n in (16, 60):
            for work, l, q in (("A", 6, 20), ("B", 0, 0)):
                c = cells[p, n, work, 10, l, q]
                values = [interval(c[k]) for k in ("first_plain", "lane_mean")]
                values.append(f"{number(c,'lane_p95'):.1f}")
                values += [
                    interval(c["makespan"]),
                    interval(tuple(100*v for v in c["drain_share"])),
                    interval(c["cycles"]),
                ]
                text.append(f"| {p} | {n} | {work} | " + " | ".join(values) + " |")
    return text + [""]


def priority_report(cells):
    text = [
        "## Corrected priority Pickup: P0 versus P1c/P3c",
        "",
        "ADR-0032 binds the priority head, not an ordinary FIFO head: every",
        "policy sorts (priority first, creation oldest-first). Thus a priority",
        "issue is the issue served by a priority-induced serial latch.",
        "P0 is rerun at the **same pi** as each boundary policy; default tables",
        "remain pi=0. Priority waits condition on a nonempty priority subset;",
        "normal waits condition on a nonempty normal subset. No empty group",
        "is treated as an observed zero wait. All other metrics use all seeds.",
        "priority.csv supplies paired deltas and ranges for every listed metric.",
        "",
        "| Policy | pi | N | Workload | First priority [p5,p95] | Mean priority [p5,p95] | Mean non-priority | Mean Lane | Makespan |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for pi in (0.1, 0.3):
        suffix = ".1" if pi == 0.1 else ".3"
        for n in (16, 60):
            for work, l, q in (("A", 6, 20), ("B", 0, 0)):
                for p in ("P0", "P1c"+suffix, "P3c"+suffix):
                    c = cells[p, n, work, 10, l, q, pi]
                    text.append(
                        f"| {p} | {pi} | {n} | {work} | "
                        f"{interval(c['first_priority_plain'])} | {interval(c['priority_plain_mean'])} | "
                        f"{number(c,'normal_plain_mean'):.1f} | {number(c,'lane_mean'):.1f} | "
                        f"{number(c,'makespan'):.1f} |"
                    )
    text.extend([
        "",
        "Paired changes versus the matching priority-enabled P0; negatives are",
        "shorter waits/makespan. Do not substitute subtraction of the medians above.",
        "",
        "| Policy | pi | N | Workload | Δ first priority [p5,p95] | Δ mean priority | Δ normal | Δ Lane | Δ makespan [p5,p95] |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ])
    for identity,c in cells.items():
        p,n,work,g,l,q,pi = identity
        if p == "P0":
            continue
        text.append(
            f"| {p} | {pi} | {n} | {work} | {interval(c['delta_first_priority_plain'])} | "
            f"{number(c,'delta_priority_plain_mean'):.1f} | "
            f"{number(c,'delta_normal_plain_mean'):.1f} | {number(c,'delta_lane_mean'):.1f} | "
            f"{interval(c['delta_makespan'])} |"
        )
    return text + [""]


def fenced_report(cells):
    text = [
        "## P1d: age-fenced oldest-first, including post-serial refills",
        "",
        "At every refill, only Lane issues older than the current serial head",
        "can reserve. Priority plain heads outrank every unlabelled Lane.",
        "Encountering the plain head closes the fence immediately, even in the",
        "formerly full post-serial refill turn; already-started work drains.",
        "No cancellation or relaxed quiescence. Integration exhaustion still",
        "forces the shared serial-fallback latch and can fragment any layout.",
        "",
        "This comparison sets pi=0. Layouts reuse the same job-level draws:",
        "shuffled; every plain older than every Lane; every plain younger than",
        "every Lane. W-A here L=6,Q=20; W-B L=2N,Q=20; 30 paired seeds.",
        "Cohort size counts contributions/setup held or admitted at latch and",
        "is averaged over **positive-duration** drains. Immediate grants are",
        "not drains; cycle count means a Lane-reservation epoch followed by serial.",
        "A serial-only initial prefix or Lane-only final suffix is not a complete",
        "Lane→serial cycle. Zero cohort with zero drains means no positive drain.",
        "Idle-drain and idle-ready-demand values are summed Lane-slot minutes.",
        "Idle-ready-demand excludes time after Lane supply has been exhausted.",
        "Agent utilization measures actual S sessions, not parked occupancy.",
        "",
        "| Layout | Workload | N | Policy | Cohort mean | Drains / cycles | Oldest plain | Mean plain | Mean Lane | Idle drain / idle ready-demand | Conflicts / attempts | Makespan | Agent slot % |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for layout in ("shuffled", "plain-older", "plain-younger"):
        for work, l, q in (("A", 6, 20), ("B", 0, 0)):
            for n in (16, 60):
                for p in ("P0", "P1a", "P1b", "P1d"):
                    c = cells[p, n, work, 10, l, q, layout]
                    num = lambda key: number(c,key)
                    text.append(
                        f"| {layout} | {work} | {n} | {p} | {num('cohort_mean'):.1f} | "
                        f"{num('drain_count'):.1f} / {num('cycles'):.1f} | "
                        f"{num('first_plain'):.1f} | {num('plain_mean'):.1f} | "
                        f"{num('lane_mean'):.1f} | {num('drain_idle_slots'):.0f} / "
                        f"{num('idle_with_lane_demand'):.0f} | {num('conflicts'):.1f} / "
                        f"{num('recovery_attempts'):.1f} | {num('makespan'):.1f} | "
                        f"{100*num('agent_slot_util'):.2f} |"
                    )
    text.extend([
        "",
        "### P1d paired changes versus P0, g=10",
        "",
        "Positive drain-count deltas mean more drains. Negative cohort deltas",
        "mean smaller cohorts. Negative makespan/idle deltas are reductions;",
        "positive agent-utilization deltas are more productive slot use.",
        "These are separate metrics, not a policy ranking.",
        "",
        "| Layout | Workload | N | Δ cohort | Δ drains [p5,p95] | Δ Lane | Δ makespan [p5,p95] | Δ idle ready-demand [p5,p95] | Δ agent slot percentage points |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for layout in ("shuffled","plain-older","plain-younger"):
        for work,l,q in (("A",6,20),("B",0,0)):
            for n in (16,60):
                c = cells["P1d",n,work,10,l,q,layout]
                text.append(
                    f"| {layout} | {work} | {n} | {number(c,'delta_cohort_mean'):.1f} | "
                    f"{interval(c['delta_drain_count'])} | {number(c,'delta_lane_mean'):.1f} | "
                    f"{interval(c['delta_makespan'])} | "
                    f"{interval(c['delta_idle_with_lane_demand'],0)} | "
                    f"{100*number(c,'delta_agent_slot_util'):+.2f} |"
                )
    text.extend([
        "",
        "Every metric above, including total idle slots, unpublished work, cohort",
        "p50/max, and paired changes versus P0, has p5/p95 ranges in age-fenced.csv.",
        "",
        "### P1d gate sensitivity (N=16; g=5 / 10 / 20)",
        "",
        "| Layout | Workload | Policy | Oldest plain | Mean Lane | Drains | Makespan |",
        "|---|---|---|---:|---:|---:|---:|",
    ])
    for layout in ("shuffled", "plain-older", "plain-younger"):
        for work, l, q in (("A", 6, 20), ("B", 0, 0)):
            for p in ("P0", "P1a", "P1b", "P1d"):
                triples = [
                    " / ".join(f"{number(cells[p,16,work,g,l,q,layout],k):.1f}" for g in (5,10,20))
                    for k in ("first_plain", "lane_mean", "drain_count", "makespan")
                ]
                text.append(f"| {layout} | {work} | {p} | " + " | ".join(triples) + " |")
    return text + [""]


def interleaved_report(calibration, seeds):
    text = [
        "## Does P1b create more drain/alternation cycles than P0?",
        "",
        "**No in this model and the specified reserve-every-refillable rule.**",
        "The shuffled batch cells and the explicit alternating-age witnesses below",
        "have the same cycle counts. P1b can change who gets the initial turn,",
        "not the size of the mandatory Lane refill. These are not global",
        "age-fenced reservations: when the oldest ready issue is Lane work,",
        "all N refillable slots can take Lanes younger than the next plain issue.",
        "Stopping reservation at each age boundary would be a different policy",
        "and could create more, smaller drains; it is now simulated separately",
        "as P1d in the age-fenced comparison above, not silently changed in P1b.",
        "",
        "Witnesses: W-B, L=2N,Q=20, all ready at t=0; creation indices alternate",
        "classes until plain supply ends. Remaining Lanes follow. Same service/",
        f"risk draws, {seeds} seeds, both starting classes, g=5/10/20.",
        "",
        "| Age pattern | N | g | P0 cycles [p5,p95] | P1b cycles [p5,p95] | Δ cycles [p5,p95] | P0/P1b positive drains | P0 oldest plain | P1b oldest plain | Seeds with more P1b cycles/drains |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for plain_first in (False, True):
        for n in (16, 60):
            for g in (5, 10, 20):
                base, oldest = [], []
                for seed in range(seeds):
                    jobs = scenario(calibration, n, "B", cell_seed("B", 0, 0, seed))
                    lanes = [j for j in jobs if j.kind == "lane"]
                    plains = [j for j in jobs if j.kind == "plain"]
                    first, second = (plains, lanes) if plain_first else (lanes, plains)
                    ordered = [j for pair in zip(first, second) for j in pair]
                    paired_count = min(len(first), len(second))
                    ordered += first[paired_count:] + second[paired_count:]
                    for index, job in enumerate(ordered):
                        job.index = index
                    b_engine = Engine(ordered, n, "P0", g)
                    o_engine = Engine(ordered, n, "P1b", g)
                    b_row, o_row = b_engine.run(), o_engine.run()
                    b_row["positive_drains"] = sum(d > 1e-9 for d in b_engine.drains)
                    o_row["positive_drains"] = sum(d > 1e-9 for d in o_engine.drains)
                    base.append(b_row)
                    oldest.append(o_row)
                b, o = summary(base), summary(oldest)
                delta = [o["cycles"] - b["cycles"] for b, o in zip(base, oldest)]
                more = sum(
                    o["cycles"] > b["cycles"] or o["positive_drains"] > b["positive_drains"]
                    for b, o in zip(base, oldest)
                )
                triple = tuple(percentile(delta, p) for p in (.5, .05, .95))
                text.append(
                    f"| {'plain,Lane,…' if plain_first else 'Lane,plain,…'} | {n} | {g} | "
                    f"{interval(b['cycles'])} | {interval(o['cycles'])} | {interval(triple)} | "
                    f"{number(b,'positive_drains'):.1f} / {number(o,'positive_drains'):.1f} | "
                    f"{number(b,'first_plain'):.1f} | {number(o,'first_plain'):.1f} | "
                    f"{more} / {seeds} |"
                )
    return text + [""]


def read_cells(path):
    cells = {}
    with path.open() as stream:
        for row in csv.DictReader(stream):
            identity = (
                row["policy"], int(row["N"]), row["workload"],
                int(row["g"]), int(row["L"]), int(row["Q"]),
            )
            if "pi" in row:
                identity += (float(row["pi"]),)
            if "age_layout" in row:
                identity += (row["age_layout"],)
            cells[identity] = {
                key[:-4]: tuple(float(row[key[:-4] + "_" + suffix])
                                for suffix in ("med", "p5", "p95"))
                for key in row if key.endswith("_med")
            }
    return cells


def upper_bound_appendix():
    archive = read_cells(HERE / "upper-bound.csv")
    text = [
        "",
        "## Appendix — P2 upper bound, NO LONGER A CANDIDATE",
        "",
        "**Dropped by the maintainer:** licensing partial quiescence would",
        "duplicate `parallel-safe`, which already supplies the human overlap",
        "assertion. No new licence is proposed. P2 is excluded from the candidate",
        "sweep, candidate CSVs, default comparisons, and adopted P1c analysis.",
        "",
        "The numbers below are frozen evidence from 2db0e8ef, before that ruling,",
        "not newly simulated alternatives. upper-bound.csv is this appendix's",
        "numeric source: 3,528 historical cells, 30 seeds each, all N, W-A supply",
        "combinations, W-B/W-C, and g=5/10/20. It is not a candidate-results file.",
        "phi=.1/.3 were abandoned licensing scenarios; phi=1 permits every",
        "original plain issue to overlap sessions, an access upper bound only.",
        "",
        "Historical assumptions: head plain issue alone controls its licence;",
        "unlicensed heads require full quiescence; plain FIFO never skips a head;",
        "serial work excludes all Integration/admission and other serial work.",
        "At an empty admitted backlog, a serial grant precedes admitting parkers.",
        "Serial commits advance base and increase Lane drift. k=1/2/4 bounds",
        "live sessions; setup must finish. Fallback issues were unlicensed.",
        "Those assumptions are preserved for reading the evidence, not endorsed.",
        "",
        "| Rejected upper bound | N | Workload | First plain [p5,p95] | Mean Lane | Makespan | Unlicensed mean | Conflicts / attempts / unpublished |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for p in UPPER_BOUNDS:
        for n in (16, 60):
            for work, l, q in (("A", 6, 20), ("B", 0, 0), ("C", 0, 0)):
                c = archive[p, n, work, 10, l, q]
                unlicensed = "NA" if p.endswith("@1.0") else interval(c["unlicensed_plain_mean"])
                text.append(
                    f"| {p} | {n} | {work} | {interval(c['first_plain'])} | "
                    f"{number(c,'lane_mean'):.1f} | {number(c,'makespan'):.1f} | "
                    f"{unlicensed} | {number(c,'conflicts'):.1f} / "
                    f"{number(c,'recovery_attempts'):.1f} / {number(c,'unpublished'):.1f} |"
                )
    return text + [
        "",
        "None of these upper bounds is part of the remaining design decision.",
        "They do not establish safety, conflict realism, or a recommended policy.",
        "",
    ]


def report(calibration, cells, seeds, validation_text, elapsed, priority_cells, age_cells):
    text = [
        "# THROWAWAY — serial-drain scheduling evidence",
        "",
        "Question: how does a plain issue's wait grow with fixed Lane capacity,",
        "and what does each scheduling policy improve and worsen?",
        "No policy is selected or recommended.",
        "",
        validation_text,
        *focused_report(cells),
        *priority_report(priority_cells),
        *fenced_report(age_cells),
        *interleaved_report(calibration, seeds),
        "## Calibration snapshot",
        "",
        f"{calibration['log_files']} JSONL files; {calibration['selected_runs']} qualifying local "
        "Runs since 2026-08-24, effective_lane_limit=16.",
        f"{len(calibration['first_latches'])} first latches: "
        f"{sum(x > 1 for x in calibration['first_latches'])} delayed, "
        f"{sum(x <= 1 for x in calibration['first_latches'])} immediate.",
        "Across all qualifying Runs (including later latches), the event counts are:",
        "",
        "`" + json.dumps(calibration["counts"], sort_keys=True) + "`",
        "",
        "| Distribution (minutes) | n | mean | p5 | p50 | p95 | max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, values in (
        ("Lane contribution C, complete start→end", [r["duration"] for r in calibration["contributions"]]),
        ("Lane agent S, summary.agent_seconds", [r["agent"] for r in calibration["contributions"]]),
        ("Serial D, advanced/no_progress start→end", calibration["serial_durations"]),
    ):
        text.append(
            f"| {label} | {len(values)} | {fmean(values):.1f} | "
            f"{percentile(values,.05):.1f} | {percentile(values,.5):.1f} | "
            f"{percentile(values,.95):.1f} | {max(values):.1f} |"
        )
    text.extend([
        "",
        "Nonparametric independent resampling with replacement; no lognormal fit.",
        "C is extracted and reported but NOT resampled as S. S includes the",
        "summary's reported agent usage; its attribution across recovery is not",
        "independently verifiable. Complete pairs only (43 starts, 38 ends);",
        "unfinished Lane sessions are right-censored and omitted.",
        "D omits aborted/all_skipped/all_blocked outcomes; completed no_progress",
        "is retained, including short sessions. Those sessions are treated as service",
        "durations, not as a measured probability of issue closure.",
        "",
        "## Reading the tables",
        "",
        f"Every exact (policy,N,workload,g,L,Q) cell has {seeds} deterministic seeds.",
        "Bracketed intervals are seed p5–p95 of the indicated per-run metric,",
        "not confidence intervals. Job p95 and seed p95 are different:",
        "e.g. `plain_p95_med` in metrics.csv is the median of each Run's job p95.",
        "All times are minutes; slot times sum over slots. Utilization and drain",
        "share use the entire Run, including the post-arrival tail for W-C.",
        "metrics.csv contains **every requested metric**, median/p5/p95 for every",
        "exact cell, including all twelve W-A supply combinations and all g values.",
        "CSV output is only written with --write-results; default execution persists",
        "no state. P4 reuses P0's paired samples exactly, including intervals.",
        "Default policy rows use pi=0. P1c/P3c rows use their suffix pi and their",
        "delta columns compare to P0 rerun at that same pi, NOT the pi=0 P0 row.",
        "Use priority.csv for the explicit priority-enabled P0 rows.",
        "",
        "## Baseline: first drain versus N (g=10)",
        "",
        "| N | W-A L=1,Q=20 | W-A L=2,Q=20 | W-A L=3,Q=20 | W-A L=6,Q=20 | W-B L=2N,Q=20 |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for n in NS:
        values = [interval(cells[("P0", n, "A", 10, l, 20)]["first_drain"]) for l in (1, 2, 3, 6)]
        values.append(interval(cells[("P0", n, "B", 10, 0, 0)]["first_drain"]))
        text.append(f"| {n} | " + " | ".join(values) + " |")
    for work, l, q, label in (
        ("A", 6, 20, "W-A representative L=6,Q=20 (other 11 combinations in metrics.csv)"),
        ("B", 0, 0, "W-B L=2N,Q=20"),
        ("C", 0, 0, "W-C nonstationary Poisson arrivals over 24h, then finish all work"),
    ):
        text.extend([
            "",
            f"## {label} — policy versus N, g=10",
            "",
            "| Policy | N | First plain [p5,p95] | Mean plain | Mean Lane | Makespan | Drain p95 / max | Drain % | Park slot-min | Int % | Conflicts / attempts / unpublished | Cycles | Starved plain |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for p in CANDIDATES:
            for n in NS:
                c = cells[(p, n, work, 10, l, q)]
                num = lambda key: number(c, key)
                text.append(
                    f"| {p} | {n} | {interval(c['first_plain'])} | {num('plain_mean'):.1f} | "
                    f"{num('lane_mean'):.1f} | {num('makespan'):.1f} | "
                    f"{num('drain_p95'):.1f} / {num('drain_max'):.1f} | "
                    f"{num('drain_share')*100:.1f} | {num('park_slot_minutes'):.0f} | "
                    f"{num('integrator_util')*100:.1f} | "
                    f"{num('conflicts'):.1f} / {num('recovery_attempts'):.1f} / "
                    f"{num('unpublished'):.1f} | {num('cycles'):.1f} | {num('starved_plain'):.1f} |"
                )
    text.extend([
        "",
        "## P4 legibility distribution (identical to P0, g=10)",
        "",
        "These are median per-Run drain p50/p95/max, including immediate grants.",
        "With many plain issues after the Lane supply drains, p50 is often zero.",
        "Do not mistake that for a short FIRST drain.",
        "",
        "| Workload | N | Drain p50 [p5,p95] | Drain p95 [p5,p95] | Drain max [p5,p95] | Idle Lane slot-min during drains [p5,p95] |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for work, l, q in (("A", 6, 20), ("B", 0, 0), ("C", 0, 0)):
        for n in NS:
            c = cells[("P4", n, work, 10, l, q)]
            text.append(f"| {work} | {n} | " + " | ".join(interval(c[k]) for k in (
                "drain_p50", "drain_p95", "drain_max", "drain_idle_slots"
            )) + " |")
    text.extend([
        "",
        "Pooled event distribution across all seed latches, to expose actual maxima",
        "rather than the median of per-Run maxima. Positive-only columns remove",
        "immediate grants; seed ranges remain in the per-Run table above.",
        "",
        "| Workload | N | All drain events p50 / p95 / max | Positive-only p50 / p95 / max |",
        "|---|---:|---:|---:|",
    ])
    for work, l, q in (("A", 6, 20), ("B", 0, 0), ("C", 0, 0)):
        for n in NS:
            drains = []
            for seed in range(seeds):
                engine = Engine(scenario(calibration, n, work, cell_seed(work, l, q, seed), l, q),
                                n, "P0", 10)
                engine.run()
                drains.extend(engine.drains)
            positive = [value for value in drains if value > 1e-9]
            triples = [
                f"{percentile(values,.5):.1f} / {percentile(values,.95):.1f} / {max(values,default=0):.1f}"
                for values in (drains, positive)
            ]
            text.append(f"| {work} | {n} | " + " | ".join(triples) + " |")
    text.extend([
        "",
        "## Gate-time sensitivity — every policy, N=16 and N=60",
        "",
        "Each triple is g=5 / 10 / 20. Full seed ranges and other N in metrics.csv.",
        "",
        "| Policy | N | Workload | First plain (g=5/10/20) | Makespan (g=5/10/20) | Conflicts (g=5/10/20) |",
        "|---|---:|---|---:|---:|---:|",
    ])
    for p in CANDIDATES:
        for n in (16, 60):
            for work, l, q in (("A", 6, 20), ("B", 0, 0), ("C", 0, 0)):
                triples = [
                    " / ".join(f"{number(cells[(p,n,work,g,l,q)],k):.1f}" for g in (5, 10, 20))
                    for k in ("first_plain", "makespan", "conflicts")
                ]
                text.append(f"| {p} | {n} | {work} | " + " | ".join(triples) + " |")
    text.extend([
        "",
        "## Tradeoffs against P0 — paired seed deltas, W-B, g=10",
        "",
        "Negative wait/makespan/conflict deltas are improvements in that metric;",
        "positive deltas are costs. A tradeoff can have no gain in this workload.",
        "Delta columns use paired seed differences, **not** subtraction of medians.",
        "",
        "| Policy | N | Δ first plain [p5,p95] | Δ mean plain | Δ mean Lane | Δ makespan | Δ conflicts | Δ parked slot-min |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for p in CANDIDATES:
        for n in (16, 60):
            c = cells[(p, n, "B", 10, 0, 0)]
            text.append(f"| {p} | {n} | {interval(c['delta_first_plain'])} | " +
                        " | ".join(f"{number(c,'delta_'+k):+.1f}" for k in (
                            "plain_mean", "lane_mean", "makespan", "conflicts", "park_slot_minutes"
                        )) + " |")
    def delta_pair(p, key, negate=False, work="B", l=0, q=0):
        sign = -1 if negate else 1
        return " / ".join(
            f"{sign*number(cells[(p,n,work,10,l,q)], 'delta_'+key):.1f}"
            for n in (16, 60)
        )

    text.extend([
        "",
        "Metric-specific reading of those paired W-B deltas (g=10, medians;",
        "N=16 / N=60 respectively):",
        "",
    ])
    for p in ("P1a", "P1b", "P1c.1", "P1c.3"):
        text.append(
            f"- {p}: first-plain improvement {delta_pair(p,'first_plain',True)} min; "
            f"mean Lane pickup cost {delta_pair(p,'lane_mean')} min. "
            f"Makespan delta {delta_pair(p,'makespan')}; conflicts delta "
            f"{delta_pair(p,'conflicts')}. Seed intervals expose gains/costs hidden by medians."
        )
    for p in ("P1d", "P3a", "P3b30", "P3b60", "P3b120", "P3c.1", "P3c.3"):
        text.append(
            f"- {p}: Lane pickup improvement {delta_pair(p,'lane_mean',True)}; "
            f"first-plain cost {delta_pair(p,'first_plain')}; parked-slot cost "
            f"{delta_pair(p,'park_slot_minutes')}; makespan delta "
            f"{delta_pair(p,'makespan')}. Negative improvement is a regression; "
            "negative cost is a gain."
        )
    text.extend([
        "- P4: no metric improvement/regression versus P0; it changes",
        "  only how the drain distribution is made legible.",
        "",
        "These are descriptive readings, not a ranking. All exact cells and",
        "paired delta intervals for retained policies are in metrics.csv.",
    ])
    text.extend([
        "",
        "## Mechanisms and assumptions",
        "",
        "- W-A is supply-bound: N beyond L cannot put more branches in flight.",
        "  W-B is capacity-bound: the first turn reserves N branches, and H=2",
        "  bounds **admission**, not parked branches. One serialized server still",
        "  has to gate/recover that cohort. Conflicts approach the proxy's 85% cap",
        "  with many branches; N=500 is reported without truncating its tail.",
        "- P1a/b/c ordering changes the first cohort; the mandatory ONE",
        "  full refill turn after every serial Iteration remains for those policies. It does not",
        "  allow a plain-only batch to bypass every subsequent Lane refill.",
        "- P1d is the explicit exception under investigation: every refill,",
        "  including the post-serial turn, stops at the oldest plain head.",
        "- P3 can delay the FIRST latch while many issues age. A short latch-to-turn",
        "  interval is not evidence of a short ready-to-start wait. Recovery",
        "  exhaustion still forces a latch even in Lane-first policies.",
        "- W-C rates: Lane λ=0.03/min in hours 0–6 and 12–18, λ=0.6/min in",
        "  hours 6–12 and 18–24; plain λ=0.015/min throughout. Independent",
        "  Poisson processes; expected 453.6 Lane and 21.6 plain arrivals.",
        "  Mean S=41.8 gives unconstrained session loads 1.3 and 25.1:",
        "  below/above N=16. Integration is overloaded during high periods.",
        "  There is **no stationary steady-state distribution** in this workload;",
        "  postwarm_plain_mean reports arrivals after hour 6, not an equilibrium",
        "  claim. Arrivals stop at 24h and the model drains to completion; a plain",
        "  issue is starved iff its actual ready-to-start wait exceeds 1,440 min.",
        "- Plain Pickup sorts priority first, then creation order for EVERY policy",
        "  (ADR-0032). All default-precedence comparisons set pi=0. Batch indices randomly",
        "  interleave the two classes. P1b compares oldest still-ready creation",
        "  indices (not live jobs); there is no perfect information about future",
        "  arrivals. P1c/P3c are compared to P0 with the same priority-enabled Pickup.",
        "  P1c pi=0.1/0.3 is adopted boundary behaviour; P3c also uses both values.",
        "  Obsolete licence draws remain unused to preserve the paired seed stream.",
        "- One serial Iteration clears one plain issue; a failed Lane appends one",
        "  serial-fallback job using an independently resampled D. No retry loop",
        "  or Run stopping limit is imposed. All synthetic branches are durable.",
        "- Sources: ADR-0032 priority/oldest eligible Pickup; ADR-0020 Serial interleave;",
        "  ADR-0008 isolation; PRD #219",
        "  §§1,4,5,6; #198 resolution; rolling_scheduler.py reserve/refillable/",
        "  serial_turn/serial_finished and loop.py _drive_rolling, read at origin/main",
        "  ccb424df. Effective N is fixed, no controller contraction.",
        "- Conflict proxy copied from df21f52:",
        "  prototypes/rolling-concurrency-control/model.py Config and",
        "  _begin_integration: min(0.85, 0.04 + 0.012*wait + 0.055*base_drift).",
        "  Its README and the earlier rolling-dispatch-sim sim.py/README were",
        "  read as precedent. The earlier sim used 0.015/0.06; those older",
        "  coefficients are **not** silently mixed with the newer model.",
        "  One inherited tick is treated as one minute. Each issue's fixed risk",
        "  draw makes comparisons paired; resolution need is uniform {1,2,3,4},",
        "  need=4 exhausts K=3. Gate g=5/10/20; recovery costs 10+g per attempt.",
        "- Local setup=0; remote Actions setup=2 min per Lane reservation,",
        "  overlapping setup, with the same S/D and no Actions queueing.",
        "  remote.csv supplies paired g=10 evidence at every N/workload/policy.",
        "",
        "## What this does NOT show",
        "",
        "- Real gate-time distribution; g is an assumption, not a measurement.",
        "- Real merge/red-gate probability or recovery success distribution.",
        "- An independent predictive validation at 14% (see the explicit failure above).",
        "- Censored-session tails, closure probabilities, changing readiness/eligibility,",
        "  retries/no-progress Run termination, Stops, cap limits, setup failures,",
        "  dirty-base checks, Checkpoints, or issue-source refresh delays.",
        "- Adaptive contraction, host contention at N=500, rate/credit limits,",
        "  corporate SDK behaviour, cost, GitHub Actions queueing, or cross-Run locking.",
        "- A stable equilibrium for the deliberately bursty, overloaded W-C.",
        "- A recommended winner. Improvements and regressions are metric-specific.",
        "",
        f"Reproducible stdlib sweep time on this host: {elapsed:.1f}s. No tests,",
        "dependencies, tracker changes, product changes, or network access are needed.",
        "",
    ])
    text.extend(upper_bound_appendix())
    return "\n".join(text)


def run_sweep(calibration, seeds):
    cells = {}
    remote = {}
    shapes = [("A", l, q) for l in (1, 2, 3, 6) for q in (8, 20, 50)] + [
        ("B", 0, 0), ("C", 0, 0)
    ]
    for n in NS:
        for work, l, q in shapes:
            inputs = [
                scenario(calibration, n, work, cell_seed(work, l, q, seed), l, q)
                for seed in range(seeds)
            ]
            for g in (5, 10, 20):
                baselines = {0.0: [Engine(jobs, n, "P0", g, pi=0).run() for jobs in inputs]}
                for p in CANDIDATES:
                    pi = policy_pi(p)
                    if pi not in baselines:
                        baselines[pi] = [Engine(jobs,n,"P0",g,pi=pi).run() for jobs in inputs]
                    baseline = baselines[pi]
                    rows = baseline if p in {"P0", "P4"} else [
                        Engine(jobs, n, p, g).run() for jobs in inputs
                    ]
                    paired = paired_rows(rows, baseline)
                    cells[(p, n, work, g, l, q)] = summary(paired)
                    if g == 10:
                        remotes = [
                            Engine(jobs, n, p, g, overhead=2).run() for jobs in inputs
                        ] if p != "P4" else remote[("P0", n, work, g, l, q)]["_raw"]
                        remote[(p, n, work, g, l, q)] = {
                            **summary([
                                {**r, **{"delta_" + k: r[k] - local[k] for k in r}}
                                for r, local in zip(remotes, rows)
                            ]), "_raw": remotes,
                        }
        print(f"Completed N={n}", flush=True)
    for cell in remote.values():
        cell.pop("_raw")
    return cells, remote


def paired_rows(rows, baseline):
    return [
        {**row, **{"delta_" + k: row[k] - base[k] for k in row}}
        for row, base in zip(rows, baseline)
    ]


def extra_sweeps(calibration, seeds):
    priority_cells, age_cells = {}, {}
    for n in (16, 60):
        for work, l, q in (("A", 6, 20), ("B", 0, 0)):
            inputs = [
                scenario(calibration,n,work,cell_seed(work,l,q,seed),l,q)
                for seed in range(seeds)
            ]
            for pi in (0.1, 0.3):
                baseline = [Engine(jobs,n,"P0",10,pi=pi).run() for jobs in inputs]
                suffix = ".1" if pi == 0.1 else ".3"
                for p in ("P0","P1c"+suffix,"P3c"+suffix):
                    rows = baseline if p == "P0" else [
                        Engine(jobs,n,p,10,pi=pi).run() for jobs in inputs
                    ]
                    priority_cells[p,n,work,10,l,q,pi] = summary(paired_rows(rows,baseline))
            for layout in ("shuffled", "plain-older", "plain-younger"):
                layout_inputs = [
                    scenario(calibration,n,work,cell_seed(work,l,q,seed),l,q,layout)
                    for seed in range(seeds)
                ]
                for g in (5,10,20):
                    baseline = [Engine(jobs,n,"P0",g,pi=0).run() for jobs in layout_inputs]
                    for p in ("P0","P1a","P1b","P1d"):
                        rows = baseline if p == "P0" else [
                            Engine(jobs,n,p,g,pi=0).run() for jobs in layout_inputs
                        ]
                        age_cells[p,n,work,g,l,q,layout] = summary(paired_rows(rows,baseline))
    return priority_cells, age_cells


def write_csv(path, cells, extra_fields=()):
    fields = ["policy", "N", "workload", "g", "L", "Q"] + list(extra_fields)
    identity_fields = len(fields)
    fields += [f"{key}_{suffix}" for key in next(iter(cells.values())) for suffix in ("med", "p5", "p95")]
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for identity, cell in cells.items():
            values = dict(zip(fields[:identity_fields], identity))
            for key, triple in cell.items():
                for suffix, value in zip(("med", "p5", "p95"), triple):
                    values[f"{key}_{suffix}"] = round(value, 6)
            writer.writerow(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--write-results", action="store_true")
    parser.add_argument("--validation", action="store_true")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--cell", action="store_true")
    parser.add_argument("--policy", choices=CANDIDATES, default="P0")
    parser.add_argument("--n", type=int, default=16)
    parser.add_argument("--g", type=int, default=10)
    parser.add_argument("--workload", choices=("A", "B", "C"), default="B")
    parser.add_argument("--lanes", type=int, default=6)
    parser.add_argument("--plain", type=int, default=20)
    parser.add_argument("--pi", type=float, choices=(0,0.1,0.3),
                        help="Priority fraction for every policy; defaults to policy suffix or 0")
    parser.add_argument("--age-layout", choices=("shuffled","plain-older","plain-younger"),
                        default="shuffled")
    args = parser.parse_args()
    calibration = json.loads((HERE / "calibration.json").read_text())
    if args.trace or args.cell:
        rows = [
            Engine(scenario(calibration, args.n, args.workload,
                            cell_seed(args.workload, args.lanes if args.workload == "A" else 0,
                                      args.plain if args.workload == "A" else 0, seed),
                            args.lanes, args.plain, args.age_layout),
                   args.n, args.policy, args.g, trace=args.trace,pi=args.pi).run()
            for seed in range(1 if args.trace else args.seeds)
        ]
        print(json.dumps(summary(rows), indent=2))
        return
    validation_text = validation(calibration, args.seeds)
    print(validation_text, flush=True)
    if args.validation:
        return
    start = time.perf_counter()
    cells, remote = run_sweep(calibration, args.seeds)
    priority_cells, age_cells = extra_sweeps(calibration,args.seeds)
    text = report(calibration, cells, args.seeds, validation_text, time.perf_counter() - start,
                  priority_cells,age_cells)
    if args.write_results:
        (HERE / "RESULTS.md").write_text(text)
        write_csv(HERE / "metrics.csv", cells)
        write_csv(HERE / "remote.csv", remote)
        write_csv(HERE / "priority.csv",priority_cells,("pi",))
        write_csv(HERE / "age-fenced.csv",age_cells,("age_layout",))
        print("Wrote RESULTS.md, metrics.csv, remote.csv, priority.csv, age-fenced.csv")
    else:
        print(text)


if __name__ == "__main__":
    main()
