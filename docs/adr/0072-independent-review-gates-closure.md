# Independent review gates every issue closure

**Status:** accepted

Every result the Orchestrator would publish and close must first pass an
independent **Review stage**. A dedicated **Reviewer** Agent profile uses the
`review` Task type and required `/code-review` Skill to judge the exact durable
head on separate Spec and Standards axes; both must pass. The Orchestrator alone
publishes and closes approved work, and review is never bypassed for route
availability, latency, or resource pressure.

The exact pre-publication head is pushed to a fast-forward-only remote **Review
ref** before review. Each **Review verdict** is either `approved` or
`changes_requested`, is recorded in the Run stream and on the issue, and names
that head plus both axis outcomes. A clean Integration merge preserves approval;
any remediation or Recovery that changes code creates a new head and requires a
new review.

A `changes_requested` verdict returns the same issue to a fresh implementation
Agent without charging a Strike or changing its Attempt lifecycle. Three rejected
verdicts in one Run produce a **Review handoff**: the issue remains open and
ineligible for more work in that Run, its Review ref and findings remain durable,
and unrelated work continues. A Reviewer session that produces no verdict is an
ordinary failed Agent session and uses the existing Session outcome, Strike, and
Attempt accounting.

In Parallel mode a contribution retains its Lane through review and remediation,
so review concurrency remains bounded by the Lane cap; an approved contribution
releases the Lane when admitted to Integration. The Queue exposes `reviewing` and
`review-handoff`, and the Run publishes `wrapper.review.started`,
`wrapper.review.verdict`, and `wrapper.review.handoff`. Required author
self-review is removed: deterministic feedback loops remain separate gates, while
the independent Reviewer is the single mandatory semantic review.
