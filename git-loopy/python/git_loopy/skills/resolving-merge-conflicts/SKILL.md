---
name: resolving-merge-conflicts
description: "Use when you need to resolve an in-progress git merge/rebase conflict."
---

# Resolve the Conflict

This skill is the **Transition owner** for conflict resolution. It performs a
`Resolve conflict` Action and, because resolution always produces a different
head, publishes a **new** `Review head` occurrence against it.

1. **See the current state** of the merge/rebase. Check git history, and the conflicting files. Record the pre-resolution head and the operation in flight (`git status`, `MERGE_HEAD`/`REBASE_HEAD`).

2. **Find the primary sources** for each conflict. Understand deeply why each change was made, and what the original intent was. Read the commit messages, check the PRs, check original issues/tickets.

3. **Resolve each hunk.** Preserve both intents where possible. Where incompatible, pick the one matching the merge's stated goal and note the trade-off. Do **not** invent new behaviour. Always resolve; never `--abort`.

4. Discover the project's **automated checks** and run them — typically typecheck, then tests, then format. Fix anything the merge broke.

5. **Finish the merge/rebase.** Stage everything and commit. If rebasing, continue the rebase process until all commits are rebased.

6. **Publish the resolution transition.** Only now — with the merge or rebase actually finished and a new commit reachable from `HEAD` — does the durable transition exist. Resolve the new head with `git rev-parse HEAD`.

   Check that publication is available at all: `git-loopy continuation capabilities` reports `operations.publish`. That capability — not the Continuation *mode*, which is a Run-level automation setting and never a publication feature flag — decides whether there is a native command to publish through. When the command is absent or reports `publish` unsupported, report the resolved head and the review now due, and stop. That is an absent capability, not a failure.

   Record the transition's own durable evidence: `transition.evidence` accepts `issue-comment` references only, so post one comment on the ticket or pull request naming the conflict, the resolution, and the new head, and reference that comment. The resolution commit is the Action's `target` and `basis`, not the transition's evidence.

   Publish with the native command, reading the one JSON completion envelope from a file:

   ```bash
   git-loopy continuation publish --input <request.json>
   ```

   The envelope carries `"transition": {"owner": "resolving-merge-conflicts", "evidence": [<that issue-comment>]}`. Publish the `Resolve conflict` Action against the resolution commit, and one new `Review head` Action whose `occurrence` is the **new** head. Every Action carries one interaction classification and its evidence — `AFK-safe` with a `transition-owner-attestation` whose `owner` matches `completion.transition.owner`, or `HITL-required` with a complete `human-boundary` (`kind`, `reason`, and a durable typed `resolution_condition`) — and one completion condition chosen from the contract's registry that is **not already satisfied when it is published**. A `commit-exists` condition against the resolution commit you just made is true immediately, so it would complete the review instead of requesting it.

   A resolved head is a changed head, so the prior review occurrence is retired and cannot be reused — even when the resolution was purely mechanical, even when the resolution was `--ours`, and even when the tree matches a previously reviewed one. Retirement is provable only on the immutable-revision chain: call `reconcile` with `revision_protocol: true`, pass its exact `observation` and ordered `parents` to `publish`, and carry one `completion.retirements` receipt naming the `predecessor_revision_id`, the retired `action_key`, and a `reason` of `completed` or `supersession`. The recurrence must carry a **distinct** `occurrence`; re-declaring the retired one proves no retirement. Never inherit a prior review's completion, and never publish while a conflict is still in flight, the index still has unmerged paths, or the rebase has commits left to apply: an unfinished operation has no durable git evidence to publish from.

7. **Treat a failed publication as repair required.** The resolution commit is durable whether or not publication succeeded. A rejected, errored, or `repair_required` receipt is reported as **repair required**, naming the resolved head and the operation that failed. Never re-resolve to "retry", never fall back to session-only advice, and never report a success-shaped result.

**Human boundaries.** A conflict that turns on a decision no diff can settle — which product behaviour is intended, which of two incompatible designs wins — is not yours to invent: stop and report it. Authority the session does not hold (a login, MFA prompt, new token or secret, consent screen, or permission expansion needed to read a fork, a protected branch, or a private submodule) is an `Authorize operation` Action, always `HITL-required`, carrying a complete `human-boundary`: `kind`, a `reason` of `credential-required`, `consent-required`, or `privilege-expansion`, and a durable typed `resolution_condition`. Unattended execution never prompts for or works around that boundary.
