# Runner-side checkpoint-and-push durability net

**Status:** accepted

## Context

Iterations sometimes ended with the agent's changes **uncommitted** (modified tracked
files) or **unpushed**. The next iteration's stale-worktree guard (`git.is_dirty`)
then aborted the whole run with exit 1 (`stale_worktree`), and work never reached the
remote — so the run appeared to "quit early" or "think it was done." Until now
`ralph_afk.git` was strictly read-only (no `add` / `commit` / `push`).

## Decision

The runner authors a **Checkpoint** as a safety net. At each iteration boundary, if the
worktree has any uncommitted or untracked changes, the runner stages everything
(`git add -A`, honouring `.gitignore`) and makes a single **close-keyword-free**
Checkpoint commit attributed to the active issue (so the auto-close backstop never
fires on it). After accounting, the runner **auto-pushes** the current branch to its
upstream whenever the iteration produced new commits; push failures (no remote, auth,
non-fast-forward) **warn but never abort**. The hard `stale_worktree` abort is
**replaced** by this checkpoint — a dirty tree is captured and the loop continues.

Checkpoints are tagged (a commit trailer) and **excluded from Strike progress**: only
**agent** commits and closures count, so the stuck-agent protection (abort after N
strikes) stays intact even though the tree is now always clean.

## Considered options

- **Auto-stash** the dirty tree instead of committing — rejected: stashes are easily
  lost and never reach the remote, defeating durability.
- **Count checkpoints as progress** (lenient strikes) — rejected: a flailing agent that
  keeps touching files would never strike out and could loop until the iteration cap,
  burning spend.

## Consequences

- `ralph_afk.git` gains mutating helpers (`add_all`, `commit`, `push`, untracked
  detection) through its existing subprocess wrapper; the user's git config stays the
  single source of truth.
- The loop's terminal-outcome set loses `stale_worktree`; commit accounting must
  separate agent commits from Checkpoints when feeding the Strike machine and the
  Summary.
- Commit history now contains runner Checkpoints interleaved with agent commits; they
  are rendered distinctly in the Log/Summary so they aren't mistaken for agent work.
- Auto-push assumes the run's branch has a pushable upstream; local-only repos keep
  working because push failures are non-fatal.
