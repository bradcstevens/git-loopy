"""Join runs.json (Run logs) with tracker.json (GitHub history) into the baseline.

Usage: python analyze.py runs.json tracker.json
"""
import collections as C
import json
import re
import statistics
import sys
from datetime import datetime, timedelta

GRACE = timedelta(minutes=15)
FOLLOWUP_RE = re.compile(r"\b(fix|regress|follow[- ]?up|broken|bug|revert)", re.I)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def q(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return "n/a"
    med = statistics.median(xs)
    p90 = xs[min(len(xs) - 1, int(0.9 * len(xs)))]
    return f"n={len(xs)} median={med:,.1f} p90={p90:,.1f} mean={statistics.mean(xs):,.1f} total={sum(xs):,.1f}"


def main(runs_path, tracker_path):
    runs = json.load(open(runs_path))
    tr = json.load(open(tracker_path))
    for r in runs:
        r["_s"], r["_e"] = ts(r["start"]), ts(r["end"])

    def closes(n):
        nodes = tr[str(n)].get("timelineItems", {}).get("nodes", [])
        return [x for x in nodes if x["__typename"] == "ClosedEvent"]

    def reopens(n):
        nodes = tr[str(n)].get("timelineItems", {}).get("nodes", [])
        return [x for x in nodes if x["__typename"] == "ReopenedEvent"]

    pickups = []
    for r in runs:
        for u in r["units"]:
            is_pickup = bool(set(u["evidence"]) - set())  # empty evidence = close call on an unpicked issue
            close_ev = [c for c in closes(u["issue"])
                        if r["_s"] <= ts(c["createdAt"]) <= r["_e"] + GRACE]
            st = u["statuses"]
            if close_ev:
                outcome = "closed"
            elif "advanced" in st:
                outcome = "advanced"
            elif "aborted" in st:
                outcome = "aborted"
            elif "no-progress" in st:
                outcome = "no-progress"
            elif "unknown" in st:
                outcome = "no-close (early schema)"
            else:
                outcome = "gone (activated, no end record)"
            pickups.append({
                "run": r["run_id"], "start": r["_s"], "parallel": bool(r["parallel_mode"]),
                "schema": r["schema"], "issue": u["issue"], "outcome": outcome,
                "close": close_ev[0] if close_ev else None, "iters": len(u["iters"]) or len(st) or 1,
                "active": u["active_seconds"], "credits": u["credits"], "tokens": u["tokens"],
                "nmt": u["nmt"], "picked": is_pickup and u["evidence"] != [],
                "close_calls": len(u["close_calls"]), "auto_close": u["auto_close"],
                "evidence_of_work": bool({"advanced", "closed-call"} & set(st)) or u["checkpoints"] > 0
                or bool(u["close_calls"]) or u["auto_close"],
            })
    real = [p for p in pickups if p["picked"]]
    print(f"# Runs: {len(runs)} JSONL Run logs; {sum(1 for r in runs if r['units'])} with at least one Pickup")
    print("Run outcomes:", dict(C.Counter(r["outcome"] for r in runs)))
    print("Parallel-mode Runs:", sum(1 for r in runs if r["parallel_mode"]))
    span = sorted(r["_s"] for r in runs)
    print("Span:", span[0].date(), "->", span[-1].date())
    print(f"\n# Pickups: {len(real)} (Run, issue) Pickups over {len({p['issue'] for p in real})} distinct issues;"
          f" {len(pickups) - len(real)} close-calls on issues the Run never picked up excluded")
    oc = C.Counter(p["outcome"] for p in real)
    for k, v in oc.most_common():
        print(f"  {k}: {v} ({v / len(real):.1%})")
    for label, sel in (("serial", lambda p: not p["parallel"]), ("parallel", lambda p: p["parallel"]),
                       ("early schema", lambda p: p["schema"] == "early"),
                       ("current schema", lambda p: p["schema"] == "current")):
        sub = [p for p in real if sel(p)]
        if sub:
            c = sum(p["outcome"] == "closed" for p in sub)
            print(f"  {label}: {c}/{len(sub)} closed ({c / len(sub):.1%})")
    cl = [p for p in real if p["close"]]
    bare = [p for p in cl if not p["evidence_of_work"]]
    print(f"  of the closed: {len(cl) - len(bare)} with work evidence (advanced status, Checkpoint or agent close call);"
          f" {len(bare)} closed in the Run window with only no-progress/aborted/no record (already-done closures"
          f" or a concurrent closer)")
    print("Closer of in-Run closes:", dict(C.Counter(
        (p["close"]["closer"] or {}).get("__typename", "none (gh issue close)") for p in cl)))
    print("stateReason of in-Run closes:", dict(C.Counter(p["close"].get("stateReason") for p in cl)))
    print("Close actor:", dict(C.Counter((p["close"]["actor"] or {}).get("login") for p in cl)))

    # Per distinct issue
    by = C.defaultdict(list)
    for p in real:
        by[p["issue"]].append(p)
    closed_issues, never = {}, []
    for n, ps in by.items():
        ps.sort(key=lambda p: p["start"])
        upto = []
        for p in ps:
            upto.append(p)
            if p["outcome"] == "closed":
                closed_issues[n] = upto
                break
        else:
            never.append(n)
    print(f"\n# Distinct issues: {len(by)} picked up; {len(closed_issues)} closed by a Run "
          f"({len(closed_issues) / len(by):.1%}); {len(never)} never closed by a Run")
    fate = C.Counter()
    for n in never:
        t = tr[str(n)]
        if t["state"] == "OPEN":
            fate["still open"] += 1
        else:
            c = closes(n)[-1] if closes(n) else {}
            fate[f"closed outside a Run by {(c.get('closer') or {}).get('__typename', 'manual')} ({t.get('stateReason')})"] += 1
    print("Fate of the never-closed-by-a-Run issues:", dict(fate))
    print("Pickups per closed issue:", q([len(v) for v in closed_issues.values()]))
    print("  closing Pickup carried work evidence:",
          f"{sum(v[-1]['evidence_of_work'] for v in closed_issues.values())}/{len(closed_issues)}")
    print("  first-Pickup close rate:",
          f"{sum(len(v) == 1 for v in closed_issues.values())}/{len(by)}")
    print("Iterations per closed issue:", q([sum(p["iters"] for p in v) for v in closed_issues.values()]))
    print("Active minutes per closed issue (all Pickups up to close):",
          q([sum(p["active"] or 0 for p in v) / 60 for v in closed_issues.values()
             if all(p["active"] is not None for p in v)]))
    print("AI Credits per closed issue (only where every Pickup recorded credits):",
          q([sum(p["credits"] for p in v) for v in closed_issues.values()
             if all(p["credits"] is not None for p in v)]))
    print("Tokens (M) per closed issue:", q([sum(p["tokens"] or 0 for p in v) / 1e6
                                            for v in closed_issues.values()
                                            if all(p["tokens"] is not None for p in v)]))
    allc = [p["credits"] for p in real if p["credits"] is not None]
    print(f"Credits recorded on {len(allc)}/{len(real)} Pickups; total {sum(allc):,.0f}; "
          f"share spent on Pickups that did not close: "
          f"{sum(p['credits'] for p in real if p['credits'] is not None and p['outcome'] != 'closed') / sum(allc):.1%}")
    alla = [p["active"] for p in real if p["active"] is not None]
    print(f"Active hours recorded: {sum(alla) / 3600:,.1f}; share on Pickups that did not close: "
          f"{sum(p['active'] for p in real if p['active'] is not None and p['outcome'] != 'closed') / sum(alla):.1%}")

    # Strikes, skips, NMT
    print("\n# Strikes / Skips / NMT")
    print("Strikes warn:", sum(r["strikes_warn"] for r in runs), "abort:", sum(r["strikes_abort"] for r in runs),
          "Runs ended stuck:", sum(r["outcome"] == "stuck" for r in runs))
    sk = C.Counter()
    for r in runs:
        sk.update(r["skip_reasons"])
    print("Pickup skips by reason:", dict(sk))
    print("Iterations emitting NO MORE TASKS:", sum(r["nmt_iterations"] for r in runs),
          "of", sum(r["iterations"] for r in runs), "Iterations; Pickups with NMT:",
          sum(1 for p in real if p["nmt"]))
    io = C.Counter()
    for r in runs:
        io.update(r["iteration_outcomes"])
    print("Iteration outcomes:", dict(io))
    cr = C.Counter()
    for r in runs:
        cr.update(r["contribution_reasons"])
    print("\n# Parallel mode")
    print("Contribution ends:", dict(cr), "Integration published:", sum(r["integration_published"] for r in runs),
          "serial fallbacks:", sum(r["serial_fallbacks"] for r in runs))

    # Quality proxies
    print("\n# Quality proxies over issues closed by a Run")
    reo, fu, fu_bug = [], [], []
    for n, v in closed_issues.items():
        at = ts(v[-1]["close"]["createdAt"])
        if any(ts(x["createdAt"]) > at for x in reopens(n)):
            reo.append(n)
        nodes = tr[str(n)].get("timelineItems", {}).get("nodes", [])
        later = [x["source"] for x in nodes if x["__typename"] == "CrossReferencedEvent"
                 and x["source"].get("__typename") == "Issue" and x["source"].get("createdAt")
                 and ts(x["source"]["createdAt"]) > at]
        if later:
            fu.append(n)
        if any("bug" in {l["name"] for l in s["labels"]["nodes"]} or FOLLOWUP_RE.search(s["title"]) for s in later):
            fu_bug.append(n)
    k = len(closed_issues)
    print(f"Reopened after the Run closed it: {len(reo)}/{k} ({len(reo) / k:.1%}) {sorted(reo)}")
    print(f"Referenced by a later-created issue: {len(fu)}/{k} ({len(fu) / k:.1%})")
    print(f"  ...where that issue is bug-labelled or titled fix/regress/follow-up/broken/revert: "
          f"{len(fu_bug)}/{k} ({len(fu_bug) / k:.1%}) {sorted(fu_bug)}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
