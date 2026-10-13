# The tracker is the board, and its states are derived

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

**Amended by [ADR-0089](0089-1-0-0-promises-an-explicit-stable-surface-and-removes-what-0-x-deprecated.md):**
1.0.0 removes the local-markdown source from the Python path, so what this decision says
about that source describes 0.x only.

**Amended by [ADR-0091](0091-a-dormant-path-stands-at-the-last-stable-family-release-carrying-its-owed-debt-frozen.md):**
once Path manifests are active, an issue owned by a dormant path is also a Pool exclusion,
`dormant_path`, until the path resumes.

Decided by [Decide the board of record and its states](https://github.com/bradcstevens/git-loopy/issues/659).

git-loopy 1.0.0 keeps the issue tracker as its only board of record: GitHub
Issues, or the local-markdown source when a Run uses it. Hermes keeps its board
in a local SQLite `kanban.db`; git-loopy adds no board store, mirror, or cache of
record. The web board is a derived view, and anything it writes goes back to the
tracker. Every durable board fact already lives there: triage labels, native
dependencies (ADR-0047), closing pull requests (ADR-0069), Leases (ADR-0033),
Review verdict comments (ADR-0072), and the Planner's approved graph (ADR-0071).
A second store would duplicate those facts and race with the tracker over them.

## States are derived, never stored

A board state, shown as a **Board column**, is computed from facts the tracker and
live Runs already hold. git-loopy adds no `state:*` or column labels. A stored
state would be a second copy of a fact that drifts: a "running" label outlives
the Run that died, while a Lease expires on its own. Humans keep asserting only
what they assert today: the triage roles, `parallel-safe`, `priority`, the
`task-type:` labels and, where they apply, the `path:` labels.

Columns are computed from the same rules **Pickup** asks: eligibility,
**Readiness**, **Leases** and the **Queue**'s **Status**. The board can never call
an issue Ready that a Run would skip, or the reverse. A rule that should stop
agents belongs in Pickup admission, where the board picks it up for free.

## Columns

Evaluated top to bottom; the first match wins.

| Column | Derived from |
| --- | --- |
| Done | The issue is closed, whatever the reason, `wontfix` included. |
| Review | A live Run's Queue shows `reviewing`. |
| Running | A live Lease, or a Queue Status of `active`, `parked`, `admitted`, `integrating` or `recovering`. |
| Blocked | Needs a human: `needs-info`, `ready-for-human`, a **Pool exclusion**, **Unresolved readiness**, or `review-handoff` in its live Run. |
| Todo | `ready-for-agent`, and Readiness proves a wait: the Readiness fact **Blocked** (an open dependency) or **Awaiting merge**. |
| Ready | `ready-for-agent`, Pickup-admissible, and no Lease held elsewhere. |
| Triage | Any other open issue: `needs-triage`, or no triage role yet. |

Todo groups the two waits that clear themselves, as the `all_blocked` Run ending
already does. **Awaiting merge** is not Review: Review is the Reviewer's lane, and
the glossary already rules out "in review" for a pull request waiting to merge.
`parked` is Running work waiting for capacity, not a review.

## Hermes' names on the board only

The column names follow Hermes, so the board column **Blocked** means "waiting on
a human" and dependency-waiting shows as **Todo**. The rename is limited to the
board. The glossary term **Blocked**, ADR-0047, the `all_blocked` Run ending, and
the Readiness verdict `blocked` keep their dependency meaning across the Wrapper
contract and every Runner member. Hermes itself uses both meanings: its BLOCKED
column means waiting on you, while its card drawer lists dependencies as
"Blocked by / Blocks". Renaming the contract term to match a UI would have been
a breaking change across four members and the Conformance fixtures, and gained
nothing.

## What the board does not add

- **No Scheduled state.** git-loopy has no per-issue timer: a Run's start decides
  when work happens, and Pickup order decides the sequence within a Run. A future
  timer would be a new Readiness fact, not a column.
- **A Review handoff stays per-Run**, as ADR-0072 has it. The card is Blocked for
  the rest of its Run, then goes back to Ready. The drawer marks "last Run: review
  handoff". The Runner gains no right to relabel an issue. This matches the
  Attempt lifecycle, whose defeats are also per-Run.
- **Planning documents are not cards.** `Spec:`, `PRD:` and `wayfinder:map` issues
  are never Pickup-admissible, so they appear only as parent context of the
  tickets made from them.
- **The local-markdown source** goes through the same derivation over the facts it
  carries: `done/` is Done, a body that fails the AFK-ready check is Blocked, live
  Queue and Leases give Review or Running, and everything else is Ready. Its
  Triage and Todo columns are always empty, because it carries no triage roles
  and no dependencies.

## Path ownership labels

The `path:<key>` labels of ADR-0073 apply only in a repository that declares
**Path manifests**. They describe git-loopy's own Language paths, so an adopter's
repository with no manifests has no path vocabulary and no refusal.

- **Vocabulary.** Each declared Language path, dormant paths included,
  contributes one `path:<key>` label to the **Label vocabulary**. `git-loopy init`
  ensures it and `git-loopy labels` reconciles it, like every other entry. The
  set is derived from the manifests and never listed in code.
- **Migration.** A migration reports by default and writes only behind `--apply`,
  following the `labels` and `doctor` pattern. It lists every open executable
  issue with no `path:` label or with more than one; planning documents are not
  executable and are never listed. `--apply` adds `path:python` only to issues
  with none, which is ADR-0073's interim meaning. An issue with several path
  labels is left for a human to split.
- **Enforcement.** Once the interim meaning ends, a missing or duplicate path
  label is a **Pool exclusion**: an authoring defect only a human can fix, named
  to the operator. It shows as Blocked on the board, and Readiness is untouched.
  The refusal turns on in the release that activates the Path manifests, with no
  separate switch. Until then, a missing label means Python.
