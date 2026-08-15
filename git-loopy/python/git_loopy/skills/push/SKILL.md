---
name: push
description: Publish current work by staging intended changes, committing, pushing, and opening a pull request when needed.
disable-model-invocation: true
---

# Publish the Current Work

Treat invocation as approval to persist the work in scope from the current
conversation. Preserve unrelated worktree changes. Honor any commit message,
remote, base branch, or pull-request preference supplied by the user.

This skill is the **Transition owner** for the publication transition. It
performs a `Publish head` Action and, once the branch is durably published,
publishes the hard-HITL `Review and merge PR` successor. A push is not
implementation completion and never closes a ticket or its parent as a side
effect.

## 1. Establish the publication state

Inspect:

- `git status --short`
- staged and unstaged diffs
- the current branch and upstream
- configured remotes and the remote default branch
- commits ahead of the upstream

A detached `HEAD` needs a user-selected branch before publication. For an active
merge, rebase, or cherry-pick conflict, stop and recommend
`/resolving-merge-conflicts`.

This step is complete when the intended paths, branch, remote, upstream state,
and existing local commits are known.

## 2. Gate on validation

Reuse successful validation from the current session when it still covers the
current diff. If the work changed afterward, run the smallest existing tests,
checks, or build commands that cover it. Resolve failures before publication.

This step is complete when the exact work being published has current passing
validation, or consists only of documentation with no repository-specific docs
check.

## 3. Stage exactly the intended change

Stage explicit in-scope paths with `git add -- <paths>`. Use `git add -A` only
when every dirty path belongs to this work. Inspect `git diff --cached` and
`git status --short` after staging, including untracked files and accidental
credentials.

This step is complete when the index contains all and only the intended change;
unrelated worktree changes remain unstaged.

## 4. Create the commit

When the index is non-empty, derive a concise commit message from the staged diff
and recent repository history, while honoring user-supplied wording and required
trailers. Let commit hooks run and resolve their failures.

When the index is empty, continue only if the branch already has unpublished
commits. Otherwise report that there is nothing to publish.

A commit made here advances the head past whatever was last reviewed, so the
head about to be pushed is an **unreviewed** head. Note that: step 7 owes it a
new `Review head` Action rather than a merge Action, and no clean review
transfers to it.

This step is complete when `HEAD` contains the intended change, the index is
clear of it, and any remaining worktree diff is identified as unrelated.

## 5. Push the branch

Use a normal fast-forward push:

- Existing upstream: `git push`
- No upstream: choose the configured remote, preferring `origin`, then run
  `git push -u <remote> <branch>`

With no configured remote, report the publication blocker. With multiple remotes
and no upstream or `origin`, ask the user to choose the destination.

If the remote rejects the push, fetch and report the divergence while leaving
history intact. A force push requires separate, explicit user approval.

This step is complete when the remote branch resolves to the local `HEAD`.

## 6. Resolve the pull request

For a GitHub remote on a non-default branch, use `gh` to find an open pull
request for the branch. Return its URL if one exists; otherwise create one
against the remote default branch using the commit range, validation results,
and relevant issue references for the title and body. A default-branch push or
non-GitHub remote makes a pull request inapplicable.

This step is complete when the pull-request URL is known or its inapplicability
is established.

## 7. Publish the publication transition

Only now — with the remote branch actually resolving to the pushed head and the
pull-request question answered — does the durable transition exist.

First check that publication is available at all. `git-loopy continuation
capabilities` reports `operations.publish`; that capability — not the
Continuation *mode*, which is a Run-level automation setting and never a
publication feature flag — decides whether there is a native command to publish
through. When the command is absent or reports `publish` unsupported, report the
durable result below and stop. That is an absent capability, not a failure.

Then record the transition's own durable evidence. `transition.evidence` accepts
`issue-comment` references only, so post one comment on the ticket or pull
request naming the pushed head, the remote branch, and the pull request, and
reference that comment. The commit, branch, and pull request themselves are the
Actions' `target` and `basis`, not the transition's evidence.

Publish with the native command, reading the one JSON completion envelope from a
file:

```bash
git-loopy continuation publish --input <request.json>
```

The envelope carries `"transition": {"owner": "push", "evidence": [<that
issue-comment>]}`. Which successor it publishes depends on whether the head that
reached the remote is the reviewed one:

- **The pushed head is the clean reviewed head, on a non-default branch of a
  GitHub remote** → one `Review and merge PR` Action targeting the pull request.
  It is always `HITL-required` and carries a complete `human-boundary` — `kind`,
  a `reason` of `human-decision`, and a durable typed `resolution_condition`.
  Its Prerequisites are the durable, machine-evaluable ones the contract's
  registry can actually evaluate: a `pull-request-review-state` condition of
  `approved` against the required review, and a `branch-head-equals` condition
  proving the branch still resolves to the exact reviewed head. Its completion
  condition is `pull-request-merged`. Required status checks and a conflict-free
  base are GitHub's own merge requirements and are reported in the Action's
  summary; do not invent condition kinds for them. Never merge, approve, or
  auto-merge it yourself.
- **The push created or advanced the head past the reviewed one** — an amend, a
  rebase, a squash, or new commits made in step 4 → the prior review occurrence
  is retired and the successor is a new `Review head` Action against the new
  head, with `/code-review` as its Instruction. Publish that edge **instead of**
  the merge edge: an unreviewed head has no clean review to carry, so a
  `Review and merge PR` Action here would ask a human to merge something nothing
  reviewed. A new head never inherits the prior head's review, however small the
  change that moved it.
- **Default-branch push or a non-GitHub remote** → a pull request is
  inapplicable. There is still a recognized durable transition, so when it adds
  no successor, publish a shared `no-guidance` completion with reason
  `no-successor-created` rather than publishing nothing.

Publish nothing before the evidence exists: no `Publish head` completion for a
push the remote rejected, and no `Review and merge PR` Action for a pull request
that was never created. Choose every completion condition from the contract's
registry, and only one that is **not already satisfied when the Action is
published**: an Action reconciliation completes on its first read has been
published dead.

Retiring the prior review occurrence is provable only on the immutable-revision
chain. Call `reconcile` with `revision_protocol: true`, pass its exact
`observation` and ordered `parents` to `publish`, and carry one
`completion.retirements` receipt naming the `predecessor_revision_id`, the
retired `action_key`, and a `reason` of `completed` or `supersession`. The
recurrence must carry a **distinct** `occurrence`.

Publication is not ticket completion. A merged pull request plus durable
ticket-completion evidence completes the implementation **Workstream**; a closed
but unmerged pull request never counts as success. Leave the ticket's own
closure to that evidence, and leave parent cleanup alone: closing the last child
neither closes its parent nor proves the parent's **Destination**. Parent
cleanup is an independent, low-priority Successor **Workstream** carried by a
separate `Close parent` Action that `/triage` owns. It never blocks this
publication and this publication never performs it.

## 8. Treat a failed publication as repair required

The commit, the pushed branch, and the pull request are durable whether or not
publication succeeded, so a failure after them strands real evidence. A rejected,
errored, or `repair_required` receipt is reported as **repair required**, naming
the pushed head, the remote branch, the pull request, and the operation that
failed. Never re-push to "retry", never fall back to session-only advice, and
never report a success-shaped result.

## 9. Report the durable result

Report the commit SHA and subject, remote branch, pull-request URL or status, the
published successor, and any unrelated changes left in the worktree.

Publication is complete only when the remote branch matches local `HEAD`, the
pull-request requirement is resolved, and the successor is published or its
absence explained.

## Human boundaries this skill does not cross

Authority the session does not already hold — a login, MFA prompt, new token or
secret, an OAuth consent or SSO authorization screen, a protected-branch
override, or any permission or scope expansion — is published as an
`Authorize operation` Action, always `HITL-required`, with a complete
`human-boundary`: `kind`, a `reason` of `credential-required`,
`consent-required`, or `privilege-expansion`, and a durable typed
`resolution_condition`. Unattended execution never prompts for login,
MFA, secrets, consent, or privilege expansion, and never works around the
boundary with a stored or guessed credential. A force push is the same kind of
boundary: it needs separate, explicit human approval and is never taken
unattended.
