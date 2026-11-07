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

> **Amendment (2026-11-06, [#1045](https://github.com/bradcstevens/git-loopy/issues/1045)): an
> already-delivered issue takes the same path.**
>
> An issue can stay open and agent-ready after its work has landed. Typically its closing pull
> request merged and then something reopened the issue. **Readiness** admits it, because under
> ADR-0069 a merged pull request never holds a candidate. Its Agent found nothing to change and
> was not allowed to close or relabel the issue, so it ended silently. That ending was
> `no_progress`, read as evidence against the Routed pair. Each attempt charged a Strike, and the
> issue was retried up to three times per Run and again in every Run. Two such issues,
> bradcstevens/ignite-2026-gift-occasion-demo#119 and
> bradcstevens/ignite-2026-gift-occasion-demo#140, cost about 180 AI Credits over three Runs.
>
> An Agent whose issue is **Already delivered** now takes decision 1's path. The issue is already
> delivered when every acceptance criterion holds on the current base and no failure named since
> the delivery still fails, such as the reason the issue was reopened. If one does still fail,
> that failure is the work. The Agent searches for an open decision issue asking to close its
> issue, or else opens one, labelled `needs-triage` only. Its title is
> `Close #N? Already delivered by owner/repo#M`, or `Close #N? Its acceptance criteria hold at
> <sha>` when the Agent finds no delivering change. Its body gives the evidence and a human's two
> outcomes: close the issue as completed, or comment on it with what remains. Either way, the
> human then closes the decision issue. The Agent records the decision issue as its issue's native
> blocker, comments on its issue, and emits `<promise>NO MORE TASKS</promise>`. The ending is then
> `no_more_tasks` rather than `no_progress`, and the issue is **Blocked** after one Iteration.
>
> Considered and not taken:
> - **An empty `Closes #N` commit for the Run to publish.** An Agent alone would close an issue a
>   human may have reopened on purpose, since nothing reviews a closure before PRD #913. Each one
>   would also run the whole Integration gate.
> - **A Readiness refusal.** It reverses ADR-0069. A deliberate reopen also comes after the merge,
>   so only the actor who reopened the issue would tell the two apart.
> - **A Runner relabel.** It stays deferred, for the reason above.
> - **NO MORE TASKS alone.** It would still cost three Iterations per Run.
> - **A Pickup notice of merged closing pull requests.** Deferred: it needs a new Event field, and
>   the decision issue already reaches triage.
>
> An automation that reopens a delivered issue belongs to its repository, and git-loopy adds no rule
> about it.
>
> Consequence: decision 1 remains the one case where in-Run Agents open tracker issues, now with two
> triggers. #929, which narrows when Agents may file issues, must keep both triggers or name them as
> exceptions.
