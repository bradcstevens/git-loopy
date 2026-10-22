# The resumed shell charges Strikes per issue from its Endings and retires its Run-wide claim

**Status:** accepted design. Planning only: no fixture, adapter, register, manifest or label
changes here. Execution belongs to #834 and #553, after the session adapter.

Decided by [Decide how the shell Attempt lifecycle, Strikes and Endings fit together](https://github.com/bradcstevens/git-loopy/issues/984)
on [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979).
Builds on [ADR-0102](0102-a-resumed-shell-path-declares-contract-support-2-38-serial-and-without-config-parity.md)
(no routing; floor 2.38) and [ADR-0103](0103-the-resumed-shell-path-speaks-headless-json-rpc-from-bash.md)
(the adapter observes crash, timeout, content-filtered and the sentinel; progress comes only from
Git and the tracker). Applies [ADR-0070](0070-a-strike-is-charged-to-the-issue.md)
and is the explicit retirement [ADR-0073](0073-language-paths-release-and-gate-independently.md)
requires before a path drops a compatibility claim.

The dormant shell binds an issue at Pickup but holds no **Attempt lifecycle**. It counts consecutive
no-progress Iterations, ends `stuck` and latches a `strike_limit` Wind-down. It carries two
**Owed waivers**, `attempt-lifecycle.json` (#553) and `status-endings.json` (#834), and claims the
two `progress-strikes.json` cases that pin Run-wide accounting for both shell and PowerShell.
Wrapper contract §6 already divides on holding a lifecycle, so the shell's move needs no normative
§6 change. It does need one coherent order, and one owner for each surface it touches.

## Decision

1. **Order.** The ADR-0103 session adapter lands first, then Endings (#834), then the lifecycle
   (#553), and #553 carries the whole Strike-fork migration in one change. Between #834 and #553 the
   shell emits Endings but keeps Run-wide accounting and charges nothing from them. That interim is
   never released, because the path stays dormant until ADR-0102's floor is met.
2. **Endings.** The shell reconciles in the shared order: crash, timeout, actual progress,
   no-more-tasks, content-filtered, then silent no-progress. Advancing or closing refutes the last
   three, never crash or timeout. A transport or protocol failure mid-session is a crash. A launch
   failure or a preflight refusal is not a session and reports no Ending. Anything the adapter
   cannot classify reports no Ending; nothing is defaulted.
3. **Strike-fork retirement.** #553 narrows `progress-resets-consecutive-strikes` and
   `abort-at-threshold-and-remain-terminal` from `["shell","powershell"]` to `["powershell"]` and
   rewrites the fixture note so only PowerShell keeps Iteration-counting accounting. The shell
   adapter then asserts only `progress` on family cases. `exit-codes.json` is unchanged, because it
   pins the exit-code table, not whether a member reaches `stuck`. PowerShell's cases, its
   `strike_limit` stop and its `wrapper.stop.lifted` vocabulary stay intact.
4. **Escalation is inert.** The shell has no routing, so its adapter runs all twelve
   `attempt-lifecycle.json` cases and asserts every step field except `owed_rung`. The fixture note
   and §14.3 say so explicitly: `owed_rung` is read only by a member with routing escalation.
5. **Evidence and exits.** Once #553 lands, the shell charges one Strike to the issue a session
   worked for each Session outcome. It emits `wrapper.strike` attributed to that issue with
   `defeated_by` on the Nth, declines a skipped candidate with `wrapper.pickup.skipped`, ends
   `all_skipped` when nothing can be bound, and never ends `stuck` or raises `strike_limit`.
   `GIT_LOOPY_MAX_NMT_STRIKES` keeps its name and takes the per-issue meaning §11 already gives it.
6. **Claims.** #834 turns `status-endings.json` from `waived_owed` to `claimed`, and #553 does the
   same for `attempt-lifecycle.json`, each only after fixture-derived assertions against production
   pass.

## Considered options

- **Lifecycle before Endings.** Rejected: Strikes are charged per Session outcome, which the
  Endings classification produces. Landing the lifecycle first would mean charging from a subset
  and rewiring later.
- **Delete the two Run-wide cases.** Rejected: PowerShell still implements that accounting, and the
  map leaves its dormant baseline intact.
- **Skip the escalation cases for the shell.** Rejected: eleven of the twelve cases carry a rung,
  so skipping them would claim almost nothing. The rung is inert, not the case.
