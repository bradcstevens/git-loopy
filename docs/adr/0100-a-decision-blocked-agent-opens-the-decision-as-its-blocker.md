# A decision-blocked Agent opens the decision as its blocker

**Status:** accepted

Decided by [A spec-blocked Agent can only say NO MORE TASKS, so the Runner retries it every Run](https://github.com/bradcstevens/git-loopy/issues/849),
with the option from candidate 1 of [Spec #958](https://github.com/bradcstevens/git-loopy/issues/958).

An Agent that finds its issue cannot proceed without a human decision has only one way to say so:
`<promise>NO MORE TASKS</promise>`. The Runner charges that as a retryable **Strike**, so the
issue is retried until its Strikes run out. Strike exhaustion is Run-scoped and writes nothing to
the tracker, so the issue keeps `ready-for-agent` and every later Run pays again. #817 cost six
Iterations over two Runs. On 2026-10-20, #866, #868 and #871 each took three blocked attempts that
asked for the same human decision.

## Decision

1. **The Agent opens the decision as an issue.** When the blocker is a human decision, the Agent
   first searches the tracker for an open issue that already asks it. Only if none exists does it
   open one, stating the question, the options it sees, and a link to the blocked issue. Then it
   records that decision issue as its own issue's native blocker, comments on its own issue, and
   emits `<promise>NO MORE TASKS</promise>`.
   - The decision issue carries `needs-triage` only. Only a human asserts `task-type:planning`
     (ADR-0088), and triage decides where the question goes.
   - The Agent opens at most one decision issue per blocker and never works on it.
2. **Readiness does the rest.** Under ADR-0047, an issue with an open native blocker is not
   Pickup-admissible. The blocked issue therefore drops out after its first attempt in this Run
   and in every later Run, until a human closes the decision issue. This reuses the existing
   native-dependency rule in `PROMPT.md`, whose not-an-issue branch this decision closes.
3. **The run-end table names strike-exhausted issues.** Each issue that exhausts its Strikes in a
   Run is named with its last ending, so a blocker that slipped past decision 1 reaches a human on
   the first Run, not the fifth. This changes no Event and no Wrapper contract.

## Considered options

- **A distinct `blocked` or `needs_decision` ending.** Deferred. It needs a Wrapper-contract
  minor and an implementation in every Runner family member. Decision 1 already removes the
  repeat, through a channel selection reads today.
- **A tracker write on strike exhaustion.** Deferred. Moving the issue off `ready-for-agent`
  would make the Runner relabel issues, which ADR-0080 and ADR-0088 keep it from doing, and
  `git-loopy labels` refuses repairs that have two right fixes. It stays available as a backstop
  if Agents miss decision 1.
- **Report-only.** Kept as decision 3, but it is not enough alone: it still spends three
  Iterations per Run.

## Consequences

- In-Run Agents now open tracker issues. This is the one case where they do, and each new issue
  enters triage like any other.
- The cost of a decision-blocked issue falls from three Iterations per Run, repeated without end,
  to one Iteration in total.
