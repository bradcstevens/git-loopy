# A resumed shell path declares Contract support 2.38, serial and without Config parity

**Status:** accepted design. Planning only: nothing here activates the shell manifest, edits the
Conformance register or makes a tracked issue agent-ready.

Decided by [Decide the resumed shell path's supported surface](https://github.com/bradcstevens/git-loopy/issues/982)
on [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979),
from the [contract-gap ledger](https://github.com/bradcstevens/git-loopy/blob/037e180352b0aecf6e25beeabaa36f73e34cda97/docs/research/shell-resumption-contract-gaps.md)
audited at `0ee87115` (contract 2.38). Applies [ADR-0073](0073-language-paths-release-and-gate-independently.md),
[ADR-0091](0091-a-dormant-path-stands-at-the-last-stable-family-release-carrying-its-owed-debt-frozen.md)
and [ADR-0098](0098-a-dormant-path-owes-the-sandbox-rules-only-once-it-resumes.md) as written.

ADR-0073 lets a **Language path** declare a **Contract support** behind the contract of the day,
and support only advances. The shell path stands dormant at its **Path baseline** of Release 0.11.2
and support 2.26. It carries two **Owed waivers**: Attempt lifecycle (#553) and Endings (#834).
Resuming it means choosing how far to advance its support. Below 2.35 a resumed shell would still
launch Agents with `copilot --yolo -p` and no Sandbox. Below 2.38 it would still close issues before
it pushes. The carried Endings already need a runtime session adapter, the same adapter that
history denial (2.28) and Sandbox (2.35) need. That makes the extra cost of the higher target mostly
adapter capability, not a second interface.

## Decision

1. **Contract support 2.38, Event-schema support `[1]`.** If the Sandbox-row cleanup (#952) lands
   first as register cleanup only, the target is the minor it lands at. Activation re-runs the drift
   audit against the contract of its own day, and may advance support before the first
   `shell/vX.Y.Z` Release.
2. **No activation below that floor.** If no interface reachable from bash can meet §18's history
   denial or §19's Sandbox, the path stays dormant. Activating at a lower support needs a new
   decision, never a silent fallback.
3. **Serial only.** Every `parallel_capabilities` key stays `false` and `GIT_LOOPY_MAX_PARALLEL>1`
   is refused. Rolling dispatch, Lanes, Integration and Execution host remain role exclusions.
4. **One entry point.** `git-loopy.sh [N] [options]` is the whole command surface. `init`, `plan`,
   `labels`, `attach`, `doctor` and Stop stay with the Python path. A compatible Dashboard helper
   attaches by Event-schema negotiation, and `artifacts` stays `[]`.
5. **No Config parity.** Routing stays Python-only under §14: one Run-wide model and effort, no
   `task-type:` routing. `config.toml` is read only to fail closed on `enabled_skills` (§17.6).
6. **The opening of §18 binds the shell; §18.1–§18.4 do not.** The shell owes memory disabled at
   creation and a recorded `session_store_sql` denial for Agents and Subagents. It claims
   `agent-profiles.json` through the serial-iteration and history-refusal cases once their
   distributions include `shell`. Execution profiles, Skill exposure, the Reviewer and profile
   activation (2.30, 2.31, 2.33) stay Python-scoped, as does Membership cadence (2.37).
7. **All five Pool-exclusion reasons.** An active shell applies `non_execution_task_type` (2.29),
   because the check is a label test that needs no routing, and it stops a shell Run picking up a
   planning or review issue.
8. **The shell owns its prompt.** `PROMPT.md` belongs to the Python path. The resumed shell ships a
   fork under `git-loopy/shell/`, so Python prompt edits stop reaching it unpinned.
9. **No shell Lease transport.** The shell keeps claiming only the pure `issue-lease.json` decisions,
   consistent with the Python-only transport recorded under #971. One Run per repository is
   documented, not enforced.
10. **The baseline is history.** `baseline` in `paths/shell.json` is never edited. Resumption
    advances only the top-level `state`, `contract_support` and `event_schema_support`. Paying a
    carried waiver closes its tracker and changes the live register, not `baseline.owed_waivers`.

What the shell then owes beyond its baseline claims: the two carried waivers; history denial (2.28);
the fifth exclusion reason (2.29); Host yield (2.32); `wrapper.sandbox.resolved` (2.34); Sandbox
(2.35); and verified close after publishing (2.38), which retires `wrapper.auto_close`.

## Considered options

- **Hold at 2.26 and pay only the carried waivers.** Rejected: an active path would still run
  `--yolo` and close issues before it publishes them.
- **Stop at 2.34, just below Sandbox.** Rejected for the same reason. Most of its added cost
  (history denial and Endings) already needs the runtime adapter Sandbox would use.
- **Python-feature parity.** Rejected: routing, profiles, Parallel mode and the operator commands
  are not why a bash Runner exists, and ADR-0073 retired full-feature parity.
