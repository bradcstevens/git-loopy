# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Canonical label   | Label in our tracker | Meaning                                  |
| ----------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for autonomous execution |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (for example, "apply the `ready-for-agent` triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use. `git-loopy init`
reads this table when it ensures the labels exist in the tracker, so a renamed role is
created under *your* string, not the canonical one — the two cannot silently desynchronise.

## Parallel execution label

`parallel-safe` is **not** one of the five canonical triage roles — it is an additional,
opt-in eligibility label a human applies **alongside** `ready-for-agent`. It asserts the
issue is independent and well-scoped enough to be worked concurrently, in its own worktree,
as one **Lane contribution** when the runner is started in **Parallel mode**. The runner
never infers it: an issue that lacks it runs serially even in Parallel mode. Because it is
not a triage role it is not in the table above and is not renameable — the runner reads
that exact string. See `CONTEXT.md` and
`docs/adr/0008-across-issue-parallelism-via-git-worktrees.md`.

## Priority label

`priority` is the second human assertion, and it works the same way: not one of the five
canonical triage roles, not in the table above, and **not renameable** — three
Orchestrators read that exact string at selection, one of them a Bash script. The runner
reads it and **never infers it** from issue content; unlike a `task-type:` label it is
never machine-set.

What it does is **reorder**, and only reorder. An issue carrying `priority` is selected
ahead of older ones; two `priority` issues order oldest-first against each other, so it is
a jump to the front of the queue rather than an escape from it. What it does **not** do is
change eligibility, in any respect:

- a `priority` issue still needs `ready-for-agent` to enter the **Pool** at all;
- it still has to pass the AFK-ready body discriminator (`## What to build` plus
  `## Acceptance criteria`);
- it still needs `parallel-safe` to take a **Lane** — urgency is not a concurrency
  assertion, and the two labels are decided by a human for different reasons;
- and it never lets an issue past a **Lease**.

It is also orthogonal to **Task type**, which selects a **Routed pair** and never affects
order. See `CONTEXT.md`, `docs/wrapper-contract.md` §3.2 and
`docs/adr/0032-the-runner-picks-the-oldest-eligible-issue.md`.

## Creating the labels

`git-loopy init`, run inside the repository, creates whichever triage,
`parallel-safe`, `priority`, and Task-type labels are absent and leaves the ones
that already exist untouched. Re-running it creates nothing.

## Task-type labels

Task type is a closed routing taxonomy. The only valid labels are
`task-type:planning`, `task-type:review`, `task-type:implementation`,
`task-type:test`, `task-type:docs`, `task-type:chore`, and
`task-type:bugfix`. Do not create other `task-type:` labels.

Unlike `parallel-safe` and `priority`, a `task-type:` label **may be machine-set**:
the **Task-type classifier** reads an unlabelled issue's own content and proposes
its key (ADR-0029), because zero closed issues carried one and routing could not
otherwise apply to anything. Three consequences follow, and none of them is an
oversight:

- **Provenance is gone.** A human-set and a classifier-set label are the same
  string on the same issue, and nothing afterwards distinguishes them.
- **A label already present is never replaced.** Whoever put it there — including
  a human who put a wrong or legacy key there — is not overruled by inference.
  Correcting a task type means editing the label.
- **The taxonomy stays closed for exactly this reason.** An unattended writer
  plus `gh label create --force` would make an invented key permanent, so a
  proposal outside the seven is refused rather than warned about.

The labels differ in blast radius, which is why only one of the three opened: a
wrong `parallel-safe` guess lets unsafe work run concurrently and a wrong
`priority` guess reorders somebody's backlog, while a wrong `task-type:` guess
picks a suboptimal model.
