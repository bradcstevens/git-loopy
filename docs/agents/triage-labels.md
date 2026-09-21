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
`parallel-safe`, `priority`, `in-progress`, and Task-type labels are absent and
leaves the ones that already exist untouched. Re-running it creates nothing.

That is *ensure*, not reconcile, and it runs once — at whatever moment `init`
happened to run. So a label added to the vocabulary afterwards never lands on a
tracker set up before it, and a colour or description that drifts stays drifted.
`git-loopy labels` is the command that says so:

```bash
git-loopy labels            # report only — reads the tracker, changes nothing
git-loopy labels --apply    # create what is missing, correct what drifted
```

It reports every label in the vocabulary as `missing`, `drifted` (naming the
attribute that differs), or matched, resolving the five roles through the table
above so a renamed role is neither missing nor drift. `parallel-safe`,
`priority`, `in-progress`, and the `task-type:` labels are compared on their
literal strings.
Labels the tracker carries outside the vocabulary are never reported, never
edited, and never deleted; nothing is ever renamed. An unreachable or
unauthorised tracker warns and exits non-zero.

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
  string on the same issue, and nothing afterwards distinguishes them. What the
  classifier applied is named in the run's diagnostics — stderr and the run log
  — and that is the only audit trail there is.
- **A label already present is never replaced.** Whoever put it there — including
  a human who put a wrong or legacy key there — is not overruled by inference.
  Correcting a task type means editing the label. The check is re-asked of the
  tracker immediately before the write, so a label you apply *while* a run is
  classifying the issue still wins.
- **The taxonomy stays closed for exactly this reason.** An unattended writer
  plus `gh label create --force` would make an invented key permanent, so a
  proposal outside the seven is refused rather than warned about.

The write is not gated on a human, because a ratification step would reintroduce
the recurring cost ADR-0029 exists to remove. It is idempotent, so re-running
changes nothing, and it is non-fatal: a tracker that refuses the write costs the
label and not the work.

The labels differ in blast radius, which is why only one of the three opened: a
wrong `parallel-safe` guess lets unsafe work run concurrently and a wrong
`priority` guess reorders somebody's backlog, while a wrong `task-type:` guess
picks a suboptimal model.

## The `in-progress` label

`in-progress` is the only label in the vocabulary a human should never set, and the
only one that asserts nothing. It is half of the **Lease** mirror: the runner attaches
it when it takes a Lease on an issue and removes it when it gives that Lease back, so
that the GitHub UI shows which issues are being worked and by what. The other half is
a single comment carrying the `<!-- git-loopy-lease:v1 -->` marker, which names the
owning Run, when the Lease was taken, and when it expires; it is updated in place
rather than duplicated.

Like `parallel-safe` and `priority` it is not one of the five canonical triage roles,
is not in the table above, and is **not renameable** — three Orchestrators must write
and remove the exact same string, and a member that spelled it differently would leave
a label no other member's release ever clears.

**It is never authoritative, and that is the whole point.** No selection, claim,
expiry, or fence decision may read this label or the comment. The Lease is a git ref
and the ref is the only truth:

```bash
git ls-remote origin 'refs/heads/git-loopy/leases/*'
```

Three consequences follow:

- **A leftover `in-progress` blocks nothing.** A Run killed with `SIGKILL` leaves its
  label attached and its Lease ref behind. The ref expires on its own and the next Run
  takes the issue *with the stale label still on it*. This is the specific failure the
  design exists not to reproduce: the naive "claim by label" approach needs a human to
  clean up after every crash, and this one does not.
- **Removing the label by hand frees nothing.** The Lease still exists. Delete the ref
  if you mean it: `git push origin --delete git-loopy/leases/issue-<N>`.
- **Drift self-heals.** A label with no Lease, or a Lease with no label, is corrected
  by the next write and is never treated as a conflict. A mirror write that fails —
  an unreachable tracker, a revoked token — costs the mirror and not the Lease; the
  Run warns and keeps working.

A Run also never clears a mirror it has stopped owning. If its Lease was stolen while
it worked, releasing removes nothing: the label and comment on that issue now belong
to the Run that holds it, and stripping them would make the mirror wrong about a
*live* Lease — the one error a non-authoritative mirror must not make.

`git-loopy init` creates it like any other vocabulary label, and `git-loopy labels`
reports it. It is deliberately **not** required at preflight: the runner mints it on
the way in, so a tracker that has never seen a Lease is not refused a Run over a label
that Run will create for itself.

See `CONTEXT.md` and `docs/adr/0033-one-issue-one-run-lease.md` §6.
