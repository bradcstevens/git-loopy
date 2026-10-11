"""Extract one record per (Run, issue) Pickup from git-loopy Run logs.

Usage: python extract_runs.py <logs-dir> > runs.json

Read-only over the logs. Handles both Event schemas seen in .git-loopy/logs:
- early (2026-07-18..07-23): bare wrapper.iteration.end, no per-issue list; the
  worked issue is inferred from checkpoint/auto_close/agent close calls, else the
  first issue the agent viewed/edited/commented in that Iteration ("inferred").
- current (2026-07-24..): per-issue issues[] on wrapper.iteration.end and
  wrapper.contribution.end, plus wrapper.issue.activated / wrapper.pickup.bound.
"""
import collections
import glob
import json
import os
import re
import sys

NMT = "<promise>NO MORE TASKS</promise>"
CLOSE_RE = re.compile(r"\bgh issue close\s+#?(\d+)")
VIEW_RE = re.compile(r"\bgh issue (?:view|edit|comment)\s+#?(\d+)")


def main(logs_dir):
    runs = [scan(p) for p in sorted(glob.glob(os.path.join(logs_dir, "*.jsonl")))]
    json.dump(runs, sys.stdout, indent=1, default=list)


def scan(path):
    run = {
        "file": os.path.basename(path), "run_id": None, "start": None, "end": None,
        "outcome": None, "parallel_mode": None, "model": None, "iterations": 0,
        "iteration_outcomes": collections.Counter(), "strikes_warn": 0, "strikes_abort": 0,
        "skip_reasons": collections.Counter(), "serial_fallbacks": 0,
        "integration_published": 0, "contribution_reasons": collections.Counter(),
        "schema": "early",
    }
    units = {}
    iter_issue = {}
    iter_usage = collections.defaultdict(lambda: {"credits": 0.0, "tokens": 0})
    iter_dur = {}
    iter_nmt = set()
    iter_closecalls = collections.defaultdict(list)
    iter_views = collections.defaultdict(list)
    iter_checkpoints = collections.defaultdict(list)

    def unit(n):
        return units.setdefault(int(n), {
            "issue": int(n), "evidence": set(), "statuses": [], "iters": set(),
            "active_seconds": None, "credits": None, "tokens": None, "close_calls": [],
            "nmt": 0, "auto_close": False, "checkpoints": 0, "contributions": [],
        })

    with open(path) as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            ts = e.get("ts")
            it = e.get("iter")
            run["run_id"] = run["run_id"] or e.get("run_id")
            run["start"] = run["start"] or ts
            run["end"] = ts or run["end"]
            if t == "wrapper.run.start":
                run["parallel_mode"] = e.get("parallel_mode")
                run["model"] = e.get("model")
            elif t == "wrapper.run.end":
                run["outcome"] = e.get("outcome")
            elif t == "wrapper.iteration.end":
                run["iterations"] += 1
                run["iteration_outcomes"][e.get("outcome")] += 1
                if "outcome" in e:
                    run["schema"] = "current"
                iter_dur[it] = e.get("duration_seconds")
                for i in e.get("issues") or []:
                    u = unit(i["issue"])
                    u["evidence"].add("iteration.end")
                    u["statuses"].append(i.get("status"))
                    u["iters"].add(it)
                    iter_issue[it] = i["issue"]
                    absorb(u, i)
            elif t == "wrapper.contribution.end":
                run["contribution_reasons"][f'{e.get("reason")}/{e["summary"].get("closure_outcome")}'] += 1
                for i in e.get("issues") or []:
                    u = unit(i["issue"])
                    u["evidence"].add("contribution.end")
                    u["statuses"].append(i.get("status"))
                    u["contributions"].append(e.get("reason"))
                    absorb(u, i)
            elif t in ("wrapper.issue.activated", "wrapper.pickup.bound", "wrapper.contribution.start"):
                unit(e["issue"])["evidence"].add(t.split(".", 1)[1])
            elif t == "wrapper.checkpoint.recorded" and e.get("issue"):
                unit(e["issue"])["checkpoints"] += 1
                iter_checkpoints[it].append(e["issue"])
            elif t == "wrapper.auto_close":
                unit(e["issue"])["auto_close"] = True
                iter_checkpoints[it].append(e["issue"])
            elif t == "wrapper.strike":
                key = "strikes_" + (e.get("outcome") or "warn")
                run[key] = run.get(key, 0) + 1
            elif t == "wrapper.pickup.skipped":
                run["skip_reasons"][e.get("reason", "").split(":")[0]] += 1
            elif t == "wrapper.parallel.serial_fallback":
                run["serial_fallbacks"] += 1
            elif t == "wrapper.integration.published":
                run["integration_published"] += 1
            elif t == "tool.call":
                args = e.get("arguments")
                cmd = (args.get("command") if isinstance(args, dict) else args) or ""
                cmd = cmd if isinstance(cmd, str) else ""
                for m in CLOSE_RE.finditer(cmd):
                    iter_closecalls[it].append((int(m.group(1)), ts))
                for m in VIEW_RE.finditer(cmd):
                    iter_views[it].append(int(m.group(1)))
            elif t == "assistant.message":
                if NMT in (e.get("content") or ""):
                    iter_nmt.add(it)
            elif t == "usage.tokens":
                u = iter_usage[it]
                u["tokens"] += (e.get("input") or 0) + (e.get("output") or 0)
                if e.get("credits") is not None:
                    u["credits"] += e["credits"]

    for it in sorted(i for i in set(iter_dur) | set(iter_closecalls) | iter_nmt if i is not None):
        issue = iter_issue.get(it)
        if issue is None and run["schema"] == "early":
            cands = ([n for n, _ in iter_closecalls.get(it, [])]
                     + iter_checkpoints.get(it, []) + iter_views.get(it, []))
            if cands:
                issue = cands[0]
                u = unit(issue)
                u["evidence"].add("inferred")
                u["statuses"].append("closed-call" if iter_closecalls.get(it) else "unknown")
                u["iters"].add(it)
                u["tokens"] = (u["tokens"] or 0) + iter_usage[it]["tokens"]
                if iter_dur.get(it) is not None:
                    u["active_seconds"] = (u["active_seconds"] or 0) + iter_dur[it]
        for n, ts in iter_closecalls.get(it, []):
            unit(n)["close_calls"].append(ts)
        if it in iter_nmt and issue is not None:
            unit(issue)["nmt"] += 1
    run["nmt_iterations"] = len(iter_nmt)
    run["units"] = [dict(u, evidence=sorted(u["evidence"]), iters=sorted(u["iters"]))
                    for u in units.values()]
    return run


def absorb(u, i):
    c = i.get("cumulative_active_seconds") or i.get("active_seconds")
    if c is not None:
        u["active_seconds"] = max(u["active_seconds"] or 0, c)
    cons = i.get("consumption") or {}
    if cons.get("credits") is not None:
        u["credits"] = (u["credits"] or 0) + cons["credits"]
    if cons.get("tokens_in") is not None:
        u["tokens"] = (u["tokens"] or 0) + cons["tokens_in"] + (cons.get("tokens_out") or 0)


if __name__ == "__main__":
    main(sys.argv[1])
