# A remote Run preserves its Runner record for the laptop to import

**Status:** accepted design, gated with [ADR-0099](0099-a-remote-run-preserves-its-work-halts-and-azure-deletes-it.md)
on adoption proof C (#959). Implementation acceptance is tracked by #961.

Decided by [A remote Run's trace and Summary outlive its Teardown](https://github.com/bradcstevens/git-loopy/issues/961).
Amends ADR-0099 decision 6.

Under ADR-0099, Teardown deletes a remote Run's trace, diagnostics and **Summary** along with its
microVM. Route reuse (ADR-0057) reads only traces, so a remote Run teaches later Routing nothing,
and `git-loopy runs` has no outcome to show for it.

## Decision

1. **The guest preserves the record.** The ADR-0099 supervisor already holds GitHub authority and
   already pushes a run-addressed ref. Before it halts, it also preserves the Run's record to
   origin under a run-addressed ref.
2. **Only the Runner's own record leaves the guest.** The record is the Runner's `wrapper.*`
   events and the Run's Summary. Tool calls and results, assistant messages, reasoning and
   diagnostics are never pushed: origin may be public, and those payloads can carry anything a
   session read. Diagnostics are still lost at Teardown, and launch still says so.
3. **The laptop imports it.** When a laptop client next runs `git-loopy remote`, `git-loopy sweep`
   or `git-loopy runs`, it imports each record ref it finds into the clone's logs as that Run's
   trace and Summary. Only then does it delete the ref from origin. A ref that has not been
   imported stays on origin.
4. **Retention counts imported Runs.** An imported record counts toward ADR-0095's count window
   like a local Run's trace, and the Sweep reclaims it the same way.

## Considered options

- **Laptop-only collection during the 7-day deletion delay.** Rejected. A record survives only if
  some client runs within 7 days, and under ADR-0099 the laptop is a backstop, never the trigger.
- **Preserving the full trace.** Rejected. It would publish every tool payload and reasoning
  block of the Run to origin.
