# Operator worktrees retire by preservation, not feature completion

**Status:** accepted

Decided with the operator on 2026-09-09 for
[consolidation #546](https://github.com/bradcstevens/git-loopy/issues/546) and
[preservation #547](https://github.com/bradcstevens/git-loopy/issues/547).

The initial plan made final directory retirement wait for unfinished feature
delivery, including the #430 -> #458 -> #459 chain, even when the work could be
preserved independently. For this operator-authorized consolidation, retirement
instead requires verified preservation and an explicit local-data disposition.
An unfinished contribution may remain on a verified issue-scoped or recovery
reference while its redundant worktree is retired; its feature issue stays open.
This accepts retained branches and private recovery archives rather than making
directory cleanup finish the backlog.

## Boundaries

- **Preservation is not publication.** Intended git-loopy work must remain
  durably recoverable on the existing repository's remote; a stash or a local
  branch alone is insufficient. Dirty work uses the existing close-keyword-free
  **Checkpoint** convention, not an issue-closing commit.
- **Publication still means green Integration.** Existing candidates may receive
  their missing review, feedback-loop gate, and PR publication. A candidate
  requiring source fixes, conflict resolution, or unresolved review decisions is
  preserved and deferred, not repaired as part of consolidation. A failed
  publication gate does not prevent retiring a separately preserved worktree.
- **Operator authority is explicit and bounded.** The operator hands the fixed
  inventory to one coordinator. Before each mutation it rechecks for competing
  or ambiguous ownership and pauses the affected candidate until a handoff is
  obtained. A missing PID, old control file, or clean branch is not authority.
- **Ignored does not mean disposable.** Canonical Run/reference evidence remains
  untouched. Independent scratch repositories and uncertain local-only material
  are privately archived, with restoration verified before retirement, including
  nested Git/worktree relationships. Only positively identified regenerable
  environments and build caches may be discarded.
- **The final checkout follows published main.** Previously checked-out,
  unfinished work remains separately recoverable, including #459 at
  `recovery/issue-459-checkpoint-20260909`. Reconciliation must not lose dirty
  work or force-push the remote. Remote feature/recovery references are retained.

## Approved operation

The linked-worktree inventory is `git-loopy-432`, `git-loopy-460`,
`git-loopy-487`, `git-loopy-488-publish`, `git-loopy-492`, `git-loopy-506`,
`git-loopy-516`, `git-loopy-521`, `git-loopy-522`, and
`git-loopy-prompt-drift`. The independent scratch inventory is
`git-loopy-review-scratch-13472`, `git-loopy-review-scratch-15325`, and
`git-loopy-review-scratch-15740`. The canonical checkout is retained. Later
external arrivals require a scope update; the coordinator accounts for its own
temporary workspaces separately.

Archives live under the operator-approved local root
`~/Backups/git-loopy-consolidation/`, in an owner-only, uniquely named
per-operation directory. They have no automatic expiry and are not published to
GitHub. This is a local operating choice, not a new installation default.

The consolidation graph therefore waits for preservation and retirement evidence,
not feature-issue closure. Real feature dependencies remain intact, and #547
cannot close merely because its planning questions have been answered.

These decisions do not expand **Sweep** ownership or change the **Reserved
branch namespace**, **Lane workspace** lifecycle, or ADR-0020's Runner-owned
publication and closure contract. They authorize this bounded operator
consolidation, not future unattended reclamation of arbitrary worktrees.
