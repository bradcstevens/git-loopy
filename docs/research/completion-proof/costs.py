"""Read-only log cost summaries, reusing the Completion baseline's extractor."""

import collections
import importlib.util
import json
from pathlib import Path
import statistics
import sys


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p90": values[min(len(values) - 1, int(0.9 * len(values)))],
        "mean": statistics.mean(values),
        "total": sum(values),
    }


def main():
    baseline = Path(__file__).resolve().parent.parent / "completion-baseline"
    extractor = load("completion_baseline_extract", baseline / "extract_runs.py")
    analyze = load("completion_baseline_analyze", baseline / "analyze.py")
    logs = Path(sys.argv[1])
    runs = [extractor.scan(str(p)) for p in sorted(logs.glob("*.jsonl"))]
    events = collections.Counter()
    sessions = {}
    has_credits = set()
    models = collections.defaultdict(list)
    identity_shapes = collections.Counter()
    for path in sorted(logs.glob("*.jsonl")):
        for line in path.open():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            events[kind] += 1
            identity = (
                path.name, event.get("iter"), event.get("contribution_id"),
                event.get("lane_id"),
            )
            if kind == "session.created":
                sessions.setdefault(identity, {
                    "created": 0, "credits": 0, "model": event.get("model"),
                })["created"] += 1
            if kind == "usage.tokens" and event.get("credits") is not None:
                has_credits.add(identity)
                identity_shapes[",".join(
                    k for k in ("iter", "contribution_id", "lane_id", "session_id")
                    if event.get(k) is not None
                )] += 1
                sessions.setdefault(identity, {
                    "created": 0, "credits": 0, "model": event.get("model"),
                })["credits"] += event["credits"]
    for identity, record in sessions.items():
        if identity in has_credits and record["created"] == 1:
            models[record["model"]].append(record["credits"])
    result = {
        "logs": len(runs),
        "span": [min(r["start"] for r in runs), max(r["end"] for r in runs)],
        "baseline_extract_matches": json.loads(json.dumps(runs, default=list))
        == json.loads((baseline / "runs.json").read_text()),
        "pickup_credits": summary([
            u["credits"] for r in runs for u in r["units"]
            if u["credits"] is not None
        ]),
        "single_session_scope_credits": summary([
            record["credits"] for identity, record in sessions.items()
            if identity in has_credits and record["created"] == 1
        ]),
        "single_session_scopes_by_model": {
            model: summary(values) for model, values in models.items()
        },
        "credit_scopes_with_multiple_sessions": sum(
            identity in has_credits and record["created"] > 1
            for identity, record in sessions.items()
        ),
        "usage_identity_shapes": dict(identity_shapes),
        "session_and_review_event_counts": {
            kind: count for kind, count in events.items()
            if kind and ("session" in kind or "review" in kind or "subagent" in kind)
        },
        "single_session_scope_baseline_summary": analyze.q([
            record["credits"] for identity, record in sessions.items()
            if identity in has_credits and record["created"] == 1
        ]),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
