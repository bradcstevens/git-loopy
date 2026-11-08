# Every transition a Run makes gets an Event

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Amends [ADR-0077](0077-hermes-capabilities-adapt-to-existing-concepts-and-issues-carry-no-pins.md).
Decided with the Loop engineer while specifying the Web frontend PRD, from the Web frontend
entry under *Left to the PRDs* in [Wayfinder: git-loopy 1.0.0 on the Python
path](https://github.com/bradcstevens/git-loopy/issues/648). That entry asked which additive
Events the Board's transitions need under Event schema 1.

ADR-0077 gives every transition a board shows an **Event**. Three transitions the **Board**
shows can never have one. No **Run** makes them, so no Run is there to emit them:

- a dead Run's **Lease** expiring, which frees its issue with nobody watching;
- a human's tracker edit: a triage role, a `task-type:` or `path:` label, a dependency, a close
  or a reopen;
- an Agent's tracker write that blocks nothing, such as a Follow-up filed in session.

The rule therefore reads: every transition a Run makes gets an Event. The Board reads those
three from the tracker and the Lease refs, as it reads every other tracker fact (ADR-0080), and
the tracker's own history records them.

## No new Event type

Once the Events of [PRD: Task-graph
execution](https://github.com/bradcstevens/git-loopy/issues/913) land, every transition a Run
makes already has its Event. The Web frontend therefore adds no Event type under Event schema 1.

| Change a Run makes | Its Event |
| --- | --- |
| Ready to Running | `wrapper.pickup.bound`. Taking the Lease is Pickup's last step, and a Lease handed back inside Pickup leaves `wrapper.pickup.skipped` or `wrapper.pin.unread`. |
| A Status change within Running | the contribution and Integration Events |
| Running to Review | `wrapper.review.started` |
| Review to Running | `wrapper.review.verdict` with `changes_requested`, outside a drain |
| To Done | `wrapper.issue.closed` |
| To Blocked by a Review handoff | `wrapper.review.handoff` |
| Back to Ready after that handoff | `wrapper.run.end` |
| Running to Ready or Todo | the row's `wrapper.iteration.end` or `wrapper.contribution.end`, whose `blockers_added` names a blocker the Agent recorded |
| Running to Todo, when a pull request the Agent advanced will close the issue | `wrapper.pr.advanced` |

A Pool exclusion and Unresolved readiness are facts a Run reads rather than makes. They keep
their existing Events, `wrapper.pool.excluded` and `wrapper.pickup.skipped`, and the Board
decides those columns from its own read.

## The rule is tested

A test replays the shared Conformance Event streams through the Board model. It fails on any
change a Run makes to a card's column or Status that has no Event. A new Run behaviour that
moves a card therefore lands with its Event, as ADR-0083 already requires of every new Status.

## Considered options

- Lease Events for taking, renewing and returning a Lease. Rejected: Pickup's own Events
  already mark taking and returning one, and expiry, the one case they miss, happens when no
  Run is alive to emit it.
- An Event for every issue an Agent creates. Rejected: the Orchestrator does not create issues,
  and it sees one created in session only by reading the tracker. A Follow-up that blocks its
  issue already reaches the row's `blockers_added`; one that blocks nothing is a tracker fact.
