# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Canonical label   | Label in our tracker | Meaning                                  |
| ----------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Parallel execution label

`parallel-safe` is **not** one of the five canonical triage roles — it is an additional,
opt-in eligibility label a human applies **alongside** `ready-for-agent`. It asserts the
issue is independent and well-scoped enough to be worked concurrently, in its own worktree,
as one **Lane** of a **Wave** when the runner is started in **Parallel mode**. The runner
never infers it: an issue that lacks it runs serially even in Parallel mode. See `CONTEXT.md`
and `docs/adr/0008-across-issue-parallelism-via-git-worktrees.md`.
