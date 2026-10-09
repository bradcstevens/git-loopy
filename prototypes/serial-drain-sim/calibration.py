"""THROWAWAY: read local JSONL once; retain only aggregate numeric evidence."""

import datetime
import glob
import json
from collections import Counter
from pathlib import Path

EVENTS = {
    "wrapper.run.start", "wrapper.run.end", "wrapper.serial.requested",
    "wrapper.contribution.start", "wrapper.contribution.end",
    "wrapper.iteration.start", "wrapper.iteration.end",
    "wrapper.integration.published",
}


def minutes(event):
    return datetime.datetime.fromisoformat(
        event["ts"].replace("Z", "+00:00")
    ).timestamp() / 60


def extract(logdir):
    contributions, serial, first_latches, cohort = [], [], [], []
    counts = Counter()
    selected = 0
    for path in sorted(glob.glob(str(Path(logdir) / "*.jsonl"))):
        events = []
        last_time = None
        with open(path) as stream:
            for line in stream:
                # All logs use the same compact event envelope, but do not
                # depend on JSON whitespace when identifying an event.
                if '"wrapper.' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if "ts" in event:
                    last_time = minutes(event)
                if event.get("type") in EVENTS:
                    events.append(event)
        run = next((e for e in events if e["type"] == "wrapper.run.start"), None)
        if (
            not run or run["ts"] < "2026-08-24"
            or run.get("effective_lane_limit") != 16
            or run.get("execution_host", {}).get("placement") != "local"
        ):
            continue
        selected += 1
        starts, iterations = {}, {}
        run_contributions, run_serial = [], []
        latch = next((e for e in events if e["type"] == "wrapper.serial.requested"), None)
        first_serial = next((
            e for e in events
            if latch and e["type"] == "wrapper.iteration.start"
            and minutes(e) >= minutes(latch)
        ), None)
        for event in events:
            typ = event["type"]
            counts[typ] += 1
            if typ == "wrapper.contribution.start":
                starts[event["contribution_id"]] = event
            elif typ == "wrapper.contribution.end":
                start = starts.pop(event["contribution_id"], None)
                if start is None:
                    continue
                summary = event.get("summary", {})
                row = {
                    "duration": minutes(event) - minutes(start),
                    "agent": summary.get("agent_seconds", 0) / 60,
                    "reason": event.get("reason"),
                    "attempts": summary.get("recovery_attempts", 0),
                    "offset": minutes(start) - minutes(latch) if latch else 0,
                    "end_offset": minutes(event) - minutes(latch) if latch else 0,
                }
                contributions.append(row)
                if first_serial and minutes(start) < minutes(first_serial):
                    if minutes(event) <= minutes(first_serial):
                        run_contributions.append(row)
            elif typ == "wrapper.iteration.start":
                iterations[event["iter"]] = event
            elif typ == "wrapper.iteration.end":
                start = iterations.pop(event["iter"], None)
                if start is None:
                    continue
                duration = minutes(event) - minutes(start)
                outcome = event.get("outcome")
                run_serial.append(duration)
                # Empty-Pool and aborted sessions are not completed work
                # service times; preserve their counts, not their duration law.
                if outcome in {"advanced", "no_progress"}:
                    serial.append(duration)
        if latch and first_serial:
            drain = minutes(first_serial) - minutes(latch)
            first_latches.append(drain)
            if drain > 1:
                end = next((e for e in events if e["type"] == "wrapper.run.end"), None)
                span = (minutes(end) if end else last_time) - minutes(run)
                cohort.append({
                    "lanes": run_contributions,
                    "plain_supply": latch.get("serial_required"),
                    "observed_drain": drain,
                    "observed_span": span,
                    "span_has_end": end is not None,
                    "serial_completed": run_serial,
                })
    return {
        "log_files": len(glob.glob(str(Path(logdir) / "*.jsonl"))),
        "selected_runs": selected,
        "counts": dict(counts),
        "contributions": contributions,
        "serial_durations": serial,
        "first_latches": first_latches,
        "validation_cohort": cohort,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("logdir")
    args = parser.parse_args()
    print(json.dumps(extract(args.logdir), indent=2))
