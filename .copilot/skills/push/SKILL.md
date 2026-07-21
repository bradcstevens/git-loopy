---
name: push
description: Publish current work by staging intended changes, committing, pushing, and opening a pull request when needed.
disable-model-invocation: true
---

# Publish the Current Work

Treat invocation as approval to persist the work in scope from the current
conversation. Preserve unrelated worktree changes. Honor any commit message,
remote, base branch, or pull-request preference supplied by the user.

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

## 7. Report the durable result

Report the commit SHA and subject, remote branch, pull-request URL or status, and
any unrelated changes left in the worktree.

Publication is complete only when the remote branch matches local `HEAD` and the
pull-request requirement is resolved.
