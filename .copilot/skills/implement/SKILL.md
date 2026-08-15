---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

# Implement the Ticket

This skill is the **Transition owner** for the implementation transition. It
performs an `Implement ticket` or `Address review findings` Action, produces a
committed candidate, and publishes the `Review head` successor pinned to that
exact head. Writing a candidate is not ticket completion, and this skill never
reviews its own head.

## 1. Establish the ticket and its durable Target

Read `docs/agents/issue-tracker.md`, then refresh the live state of the ticket
in scope: its state, labels, dependencies, parent, and any review findings
already recorded against an earlier head. Note the branch and its current head.

Ticket text that only *asserts* readiness is not durable **Basis**. A ticket
whose scope is ambiguous, whose acceptance is a matter of taste, or whose
prerequisites are unmet is not implementable here — say so and stop rather than
inventing the missing decision.

This step is complete when the ticket, its branch, its starting head, and any
outstanding findings are known from their durable sources.

## 2. Implement and validate

Implement the work. Use `/tdd` at pre-agreed seams, and `/diagnosing-bugs` for a
defect that needs a reproducing signal first. Consult `/codebase-design` before
a new module boundary and `/domain-modeling` for vocabulary.

Those nested skills are **pointer-only participants**: they return tests,
findings, diagrams, decisions, or references to this transition. They publish no
shared guidance of their own, and neither does `/handoff`.

Run the repository's own checks — typecheck and single test files as you go, the
full suite once at the end. Every automated check that covers the change must
pass before the candidate is committed. Failing or unrun validation is not a
finding for review to catch; it is unfinished implementation.

This step is complete when the work satisfies the ticket's acceptance criteria
and every automated check covering it passes.

## 3. Commit the candidate

Stage exactly the intended paths and commit. The commit is the durable evidence
the review transition is pinned to, so it happens **before** any review Action
exists. Record the resulting head with `git rev-parse HEAD`.

This step is complete when `HEAD` is a new commit containing the whole candidate
and the working tree carries no unstaged part of it.

## 4. Publish the Review head transition

The durable transition has now happened: a candidate exists at an exact head.

First check that publication is available at all. `git-loopy continuation
capabilities` reports `operations.publish`; that capability — not the
Continuation *mode*, which is a Run-level automation setting and never a
publication feature flag — is what decides whether there is a native command to
publish through. When the command is absent or reports `publish` unsupported,
report the committed head and the review now due, and stop. That is an absent
capability, not a failure.

Then record the transition's own durable evidence. `transition.evidence` accepts
`issue-comment` references only, so post one comment on the ticket stating what
was implemented and the exact head, and reference that comment. The commit
itself is not evidence of the transition; it is the Action's `target` and part
of its `basis`.

Publish with the native command, reading the one JSON completion envelope from a
file:

```bash
git-loopy continuation publish --input <request.json>
```

The envelope carries `"transition": {"owner": "implement", "evidence": [<that
issue-comment>]}`, the contract's Producer role, and one Action:

- `kind`: `Review head`;
- `occurrence`: the exact head SHA, so a later head is a **new Action
  occurrence** rather than a reuse of this one;
- `target`: the `commit` just made;
- `basis`: the ticket and that commit;
- `instruction`: `/code-review <head>`;
- `interaction`: `AFK-safe` with a `transition-owner-attestation` whose `owner`
  matches `completion.transition.owner`, or `HITL-required` with a complete
  `human-boundary` — `kind`, `reason`, and a durable `resolution_condition`.
- `completion_condition`: a typed condition from the contract's registry that is
  **not already satisfied when the Action is published**. Publishing an Action
  reconciliation would complete on its first read is a defect, not a fast path:
  a `commit-exists` condition against the candidate you just made is true the
  moment it is published, so the review would vanish instead of being done.

Never publish a `Review head` Action whose occurrence names a head that does not
exist yet, and never republish an unchanged head — the prior occurrence still
covers it.

When this candidate supersedes an earlier one — remediation commits, an amend, a
rebase — the earlier `Review head` occurrence has to be **retired**, and
retirement is provable only on the immutable-revision chain. Call `reconcile`
with `revision_protocol: true`, pass its exact `observation` and ordered
`parents` to `publish`, and carry one `completion.retirements` receipt naming
the `predecessor_revision_id`, the retired `action_key`, and a `reason` of
`completed` or `supersession`. The recurrence must carry a **distinct**
`occurrence` — re-declaring the retired one is the same occurrence and proves no
retirement at all.

If the transition genuinely produced no successor — the candidate changed
nothing a later Action depends on — that is not silence. Publish a shared
`no-guidance` completion with reason `no-successor-created`, so "nothing follows"
is a recorded claim rather than an absent record.

This step is complete when the receipt is committed, or the absence of the
capability has been reported.

## 5. Treat a failed publication as repair required

The candidate commit is durable whether or not publication succeeds, so a
failure after it strands real evidence. A rejected, errored, or `repair_required`
receipt is reported as **repair required**, naming the head, the transition
owner, and the operation that failed.

Never retry by mutating the candidate, never fall back to session-only advice,
and never report the iteration as complete. A missing Producer revision must not
look like successful completion.

This step is complete when a failed publication has been reported as repair
required and no success-shaped result has been claimed.

## 6. Route the human boundaries out of the AFK-safe path

Some of the ticket's work is not this skill's to do, and hiding it inside an
AFK-safe implementation is what makes an unattended Run wrong rather than slow.
Publish these as their own Actions instead:

- **Subjective acceptance** — "does this feel right", visual judgement, a device
  or hardware check, or any acceptance no automated check can settle. Publish a
  `Perform manual validation` Action. It is always `HITL-required`, carries a
  complete `human-boundary` evidence — `kind`, a `reason` of
  `subjective-validation`, and a durable typed `resolution_condition` — and is
  never folded into the implementation Action's completion condition.
- **Authority the Run does not already hold** — a login, MFA prompt, new token
  or secret, an OAuth consent screen, a permission or scope expansion, or any
  approval that widens what the Run may touch. Publish an `Authorize operation`
  Action. It is always `HITL-required`, with a complete `human-boundary` whose
  `reason` is `credential-required`, `consent-required`, or `privilege-expansion`
  and whose `resolution_condition` is a durable typed condition.

A `human-boundary` missing its `reason` or `resolution_condition` is rejected, so
an incomplete boundary is not a lenient one — it loses the whole envelope.

Unattended execution never prompts for login, MFA, secrets, consent, or
privilege expansion, and never works around the boundary with a stored or
guessed credential. It stops at the boundary and lets the published Action carry
it to a human.

This step is complete when every human-judgement and authority boundary the
ticket met is a separate hard-HITL Action rather than a step inside the
implementation.

## 7. Report

Report the committed head, the checks that passed, the published Review head
occurrence, and any hard-HITL Action the ticket produced. Review of that head is
`/code-review`'s transition, and publication is `/push`'s; this skill performs
neither.
