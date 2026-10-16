# Run traces are retained by count, at the route-reuse window

**Status:** accepted (#359). Extends the **Sweep** decided in #344 and shipped
in `git_loopy.sweep`; reuses #352's control-artifact lock as its liveness
oracle. Nothing here is contract-visible.

## Context

Nothing reclaims `.git-loopy/logs/`. On the clone that measured it, it held
304 MB over 90 days: 119 replay-grade traces (`<stem>.jsonl`, 281 MB), their
diagnostics logs (`<stem>.log`, 23 MB), 62 control artifacts (`<stem>.control`)
and 3 Stop-request directories (`<stem>.control.stops/`). `runs/` held 1 MB of
Run summaries.

A trace is not a **Lane workspace**. A workspace is reconstructible from its
branch, which is what let #344 delete it safely. A trace is reconstructible
from nothing: the Dashboard is a pure fold of it, so deleting one destroys the
only record its Run left. Retention is therefore a choice about real loss.

The trace's one cross-Run reader sets the floor. `route_reuse` derives
ADR-0057's **Reusable routes** from the newest `MAX_HISTORY_LOGS` (64) traces
and from nothing else.

## Decision

1. **The Run is the unit of retention.** A Run's `.jsonl`, `.log`, `.control`
   and `.control.stops/` are kept or reclaimed together, keyed by the Run stem
   (`persist.parse_run_stem`). A file whose name is not a Run stem (for example
   `calibration-*.jsonl`) is never touched.
2. **Keep the newest N Runs per logs directory, where N *is*
   `MAX_HISTORY_LOGS`.** One constant, not two, so retention can never drift
   below what route reuse reads. N is not configurable until someone needs it.
3. **Rank every Run by stem, newest first, live or not.** The newest N are
   kept. An older Run is reclaimed only if it is **provably finished**: either
   its control artifact's lock is free, or it has no control artifact and its
   trace contains `wrapper.run.end`. A held lock is live. Anything unprovable
   (no artifact and no run-end, or no `flock`) is unknown, and unknown is never
   dead: it is preserved and still takes a slot.
4. **Reclamation deletes. Compression is rejected.** Every trace reader (Python
   route reuse and Run discovery, the Rust Dashboard, the shell and PowerShell
   members) would need a second format. A count cap already bounds the
   directory, and a 6× gain does not pay for four format changes.
5. **Run summaries in `runs/` are kept forever** and are outside retention. At
   about 10 KB a Run, they are the cheapest long-term history.
6. **The existing residue actors apply it**, the start-of-Run sweep and
   `git-loopy sweep`, with that command's `--dry-run` and the Sweep's
   report-on-action, silent-otherwise rule. A Run never prunes its own logs on
   exit.
7. **The domain is the clone.** Each `.git-loopy/logs/` of this clone's
   registered worktrees (the set the Sweep already enumerates for control
   artifacts) is capped independently. Nothing outside the clone is examined.

## Considered options

- **Age-based** retention deletes all history after an idle month, including
  the evidence route reuse reads. **Size-based** retention depends on how
  verbose a Run happened to be. **Never** is the defect being fixed.
- **Separate policies for traces and diagnostics logs** would leave a trace
  without its diagnostics, or diagnostics for a Run that has no trace.
- **Treating legacy traces as dead by age** would break the "unknown is never
  dead" rule the Sweep and Run discovery share (ADR-0058). The cost is
  accepted: Runs from before #352 that were killed have neither a control
  artifact nor `wrapper.run.end`, so they stay until removed by hand. There
  were 12 on the measured clone, and no new ones can be created.

## Consequences

- Raising `MAX_HISTORY_LOGS` raises retention with it. Lowering it lowers
  retention, which deletes history route reuse no longer reads anyway.
- Retention is housekeeping. Like the rest of the Sweep, it emits no Event and
  is never a **Strike**.
- Out of scope: the operator's own worktrees placed under `.git-loopy/`. The
  Sweep identifies its quarry by the Reserved branch namespace (#344 §7), and
  those directories are not git-loopy's.
- **Retention** enters the glossary when this ships, not before.
