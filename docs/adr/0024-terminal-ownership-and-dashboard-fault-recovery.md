# The terminal has one owner, and a Dashboard fault is an involuntary Detach

**Status:** accepted

**Supersedes:** the exit-model clause of
[ADR-0001](0001-observer-control-model-for-interactive-runner.md) (an unflagged app
exit is a **Stop**). ADR-0001's peer-task core is upheld in full.

The **Dashboard** puts the terminal into alternate screen, raw mode and mouse
tracking, and those are undone only as a side effect of Textual's orderly teardown.
When the Dashboard dies abnormally that teardown does not complete, nothing else in
the process is responsible, and `git-loopy` exits into a terminal still in raw mode
with the alternate screen active — a blank window that will not take input and can
only be recovered by closing it. The same fault is also fatal to the work:
`InteractiveDriver.run` treats *any* app exit that is not a flagged **Detach** as an
operator **Stop** and cancels the loop task, then joins both peers with
`return_exceptions=True` while inspecting only the loop's result — so the Dashboard's
exception is discarded unread and the cancelled loop returns zero. A crash that
destroys hours of unattended work is currently recorded as a clean stop with no error
anywhere. We will give the terminal a single named owner, and demote a **Dashboard
fault** from fatal to survivable.

Decided in [#321](https://github.com/bradcstevens/git-loopy/issues/321) under the map
[#320](https://github.com/bradcstevens/git-loopy/issues/320).

## Decision

### The Terminal owner

- A **Terminal owner** is named: the one component responsible for the terminal's
  mode state for the whole process. The rule it exists to keep is that git-loopy
  **never returns control to a shell in a terminal state it did not find**.
- It captures the terminal's entry state *before* the Dashboard starts and restores
  **that captured state**, not an assumed one. What the operator had is what the
  operator gets back.
- Restoration lives **outside** the Dashboard's own teardown. It stops being a side
  effect of a component finishing tidily and becomes somebody's job — which is the
  whole point, because the path that fails is the path where the Dashboard has
  already lost control.
- Alternate screen, raw mode and mouse tracking are one indivisible acquisition and
  are released together, so no single mode is left set.
- Release is **idempotent**. The ordinary sequence — Textual restores, then the owner
  restores — is harmless rather than a double reset that clears scrollback. A
  Dashboard that dies part-way through its own teardown still ends fully restored.
  Release when acquisition never happened is a no-op, so a failed Dashboard startup
  does not itself corrupt the terminal.
- Every exit path goes through it: natural **Run** completion, an operator **Stop**,
  a **Detach**, a **Dashboard fault**, an unhandled loop exception, and a signal.
  Signal restoration reuses the same owner and the same idempotent release rather
  than becoming a second restoration mechanism with its own behaviour to learn.
- The run-end **Summary** is emitted **after** release, so the permanent record lands
  in real scrollback rather than in an alternate screen that is about to be
  discarded.
- The owner imports nothing from Textual or the `[tui]` extra, so it is unit-testable
  against a fake terminal without a TTY — the same import-guard convention ADR-0001
  imposes on `LiveRunState`. Tests assert the terminal's observable mode state after
  exit, not which internal method performed the restore.
- The non-interactive path (pipe / redirect / CI / `--no-interactive` / `[tui]` extra
  absent) acquires **no** terminal ownership at all; its output stays byte-for-byte
  what it is today.
- No terminal-emulator-specific or vendor detection is introduced. The captured entry
  state makes it unnecessary.

### A Dashboard fault is an involuntary Detach

- A **Dashboard fault** — a Dashboard that raises — takes the **Detach** path, not
  the **Stop** path. The operator loses the live view, not the work.
- The driver's app-exit branch becomes three outcomes rather than two. An operator
  **Stop** cancels the loop exactly as it does today. A voluntary **Detach** swaps
  sinks exactly as it does today. A **Dashboard fault** swaps sinks to the parked
  line printer and leaves the loop task running to its natural outcome.
- The fault reuses the **existing swap seam** rather than a second continuation
  mechanism, and the swap stays atomic with respect to the loop's synchronous event
  dispatch: no event is dropped or duplicated across the handoff.
- The operator is told **at the point of the swap** that the live view has gone and
  why, rather than being left guessing why their screen turned into a line printer.
- The fault stops being swallowed. Both peers' outcomes are inspected, the exception
  is surfaced, and the fault is recorded in the **Run**'s durable record
  distinguishably from a voluntary Detach.
- The exit code distinguishes a Run that continued past a Dashboard fault from a
  clean Stop, so a supervising script is never told everything was fine. When the
  loop later completes naturally after a fault, the loop's own exit code for the
  actual work is preserved — a renderer crash does not mask the outcome of the work.
- A Dashboard that fails **during startup** degrades to the parked line printer
  instead of aborting the Run, on the same terms the runner already uses when the
  `[tui]` extra is absent. Observability is never a precondition for doing work.
- `KeyboardInterrupt` continues to propagate unswallowed and a second `Ctrl+C` still
  forces an immediate exit. Making faults recoverable must never make the process
  unkillable.
- A voluntary and an involuntary Detach produce the **same continuation** and are
  **labelled differently**, because one of them is a bug the operator wants to see.

### What ADR-0001 keeps, and the one clause it loses

- **Upheld:** the loop is a peer asyncio task the Dashboard merely *observes*; sinks
  are a swappable list; `LiveRunState` imports no Textual; the non-interactive path
  is byte-for-byte unchanged. That core claim — the loop's lifetime is not the app's
  — is what makes Detach possible at all, and it is not in question here.
- **Superseded:** the exit branch that made an app exit without a Detach flag
  equivalent to a Stop.
- **Why that branch was reasonable when written.** ADR-0001 enumerated the app's
  exits as `q` / `Ctrl+C` and Detach — both operator-initiated, and exactly one of
  them flagged. Under that enumeration "not a Detach" really did mean "a Stop", and
  the branch was an exhaustive match rather than a lazy fallback. What changed is
  that the enumeration was incomplete: an exception is a third way for the app task
  to finish, and it carries no intent to read. Treating an unflagged exit as intent
  therefore contradicted ADR-0001's own core claim in the single case where it
  mattered most. This is a changed circumstance, not a reversal of judgement.

### Out of scope

Diagnosing the specific defect that crashed the Dashboard is **explicitly out of
scope**. Making the fault survivable and visible is the decision. A renderer bug is a
bug to be fixed on its own merits, and a kit that survives only the crashes it has
already diagnosed has not been made robust.

## Considered options

- **Restore the terminal inside the Dashboard's own teardown, hardening Textual's
  exit path** — rejected because that is precisely the arrangement that failed. A
  component cannot be made responsible for cleanup along the path where it has
  already lost control.
- **Reset the terminal unconditionally with a fixed escape sequence on exit** —
  rejected because it restores an assumed state rather than the captured one, and a
  full reset clears scrollback. It would destroy the run-end record on the ordinary
  path in order to repair the abnormal one.
- **Keep a Dashboard fault fatal, but restore the terminal on the way out** —
  rejected because it fixes the shell and still loses the work. The loop is a peer
  with its own outcome; ending it because its observer died inverts ADR-0001's core
  claim.
- **Restart the Dashboard after a fault** — rejected because a renderer that just
  raised will most likely raise again on the next frame, and an oscillation between a
  live view and scrollback is worse for the operator than either one.
- **Give the fault its own continuation mechanism, separate from Detach** — rejected
  because two ways to end up in scrollback would be two behaviours to learn and two
  to keep correct. The difference between the voluntary and the involuntary case is a
  label and a record, not a mechanism.
- **Detect the terminal emulator and tailor the restore to it** — rejected as a
  standing maintenance obligation of exactly the kind the kit removes elsewhere, and
  unnecessary once the entry state is captured rather than assumed.
- **Leave a failed Dashboard *startup* fatal, and cover only post-startup faults** —
  rejected because it is the same wrong answer at a different moment, and it already
  disagrees with the runner's existing behaviour when the `[tui]` extra is absent,
  where it degrades to the line printer quietly.

## Consequences

- **Terminal owner** and **Dashboard fault** are new terms the shipped code will
  implement, and **Detach** now covers a form the operator did not choose. The
  glossary owes all three; the correction is sequenced in
  [#336](https://github.com/bradcstevens/git-loopy/issues/336) so that no term is
  recorded before the code implements it.
- A Run's exit code gains a distinguishable state — continued past a Dashboard fault
  — which supervising scripts can act on. It is not the loop's own exit code for the
  work, which survives a later natural completion unchanged.
- The decision is about the process that hosts the Dashboard. The shell and
  PowerShell **Orchestrators** host none and are unaffected; any future renderer that
  acquires the terminal owes the same release.
- The three-way app-exit branch is where the fault is classified, but the Terminal
  owner sits *outside* it — acquisition happens before the Dashboard starts and
  release is unconditional — so an exit path nobody enumerated still restores the
  terminal.
- This ADR is numbered **0024**, not 0023 as #321 anticipated. ADR-0023 was taken by
  the pinned external Skill catalog before this one was written; a published number
  is never reused. The billed-Cost ADR that #322 calls ADR-0024 lands as **0025** for
  the same reason.
