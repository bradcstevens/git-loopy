# The Header's Parallel posture reports the Run, not the Orchestrator

**Status:** accepted

Decided by [#476](https://github.com/bradcstevens/git-loopy/issues/476), which could not land
[ADR-0051](0051-the-headers-parallel-posture-is-a-declared-composite.md)'s fixture edit without
settling it. It records what `availability` gates on, a question
[ADR-0044](0044-the-dashboard-core-attributes-rolling-work-by-the-contribution-triple.md) raised
and answered two ways.

## The decision

**`parallel.availability` is `not_declared` until a posture Event arrives, and `available` from
then on.** It is not read from the Run-start `parallel_capabilities` manifest, and `unavailable`
is not one of its values.

The **Insight capability** Declarations beside it — `cost`, `rate_card`, `routing` — answer *can
this Orchestrator observe the thing at all*, because the thing is a measurement the producer either
takes or cannot take. Parallel posture is not a measurement: it is **what this Run is doing right
now**, and an Orchestrator that can fill three **Lanes** but is filling one has a posture that its
manifest cannot express. So the two questions only look alike. The Header answers the Run's.

## Why the manifest reading could not stand

ADR-0051 states, as settled fact, that the undeclared form is "the single unconditional form
Rust's `parallel_declaration()` already emits for a Wave trace", and [#476](https://github.com/bradcstevens/git-loopy/issues/476)
requires it on all twenty-four shared snapshots. It was not what the core emitted. Every shared
case's Run start declares a `parallel_capabilities` manifest — eight declare `parallel_mode: true`
and two declare `false` — so the manifest gate projected `available` on eight Wave traces and
`unavailable` on two, and `not_declared` on none of them. The value ADR-0051 describes was
reachable only by gating on the posture Events.

ADR-0044's own sentence agrees, and only under this reading: *"The shell and PowerShell
Orchestrators declare `parallel_mode: false`, so `not_declared` is truthful for them."* Under the
manifest gate that sentence is false — declaring `false` yielded `unavailable`. Under this one it
is exactly right. ADR-0044's decision, which this does not reopen, is that the four posture Events
collapse into **one Header entry**; how that entry's gate is computed is what was left
underdetermined, and is what is decided here.

The reading also survives the surface [#478](https://github.com/bradcstevens/git-loopy/issues/478)
builds on it: a serial **Run** renders no posture segment at all, which is what that ticket's first
acceptance criterion asks for and what the manifest gate could not deliver, since it would have
announced a posture on every Run the Python Orchestrator has ever driven.

## Why not

- **Keep the manifest gate and pin the true per-case values** (eight `available`, two
  `unavailable`). Honest about what the code does, and it needs no Rust change. Rejected because
  it makes `available` mean *"this Runner has a Parallel mode"* on a Wave trace that never filled
  a second Lane — a Header that announces a posture no Lane ever took — and it would put a healthy
  posture segment on every serial Run's Header, which is the noise ADR-0051's priority rule exists
  to avoid.
- **Gate on the manifest but fall back to the posture** (`unavailable` when declared `false`,
  `available` once observed, `not_declared` otherwise). Keeps the manifest read alive. Rejected
  because the two native cases still project `unavailable`, so the shared snapshots are not one
  form, and the composite would carry two unrelated questions under one key.
- **Delete `ParallelCapabilities` from the decoder**, now that no projection reads it. Rejected as
  a different ticket's call: the unread-field rule ADR-0051 applies is about **reducer state**
  holding a fact the Dashboard cannot show, and this is a Run-start Event field the wire contract
  declares. It stays decoded and is projected by nothing — and what it must *not* do is pinned at
  the library boundary, so the gate cannot quietly start reading it again.

## Consequences

- **`unavailable` is unreachable for this one entry.** The `availability` vocabulary is otherwise
  unchanged, and the three true Declarations still use all three values.
- **The shared oracle pins the gate from both ends.** Eight shared cases declare `parallel_mode:
  true` and project `not_declared`, so a member that restored the manifest reading fails the
  family comparison rather than drifting; the private rolling case pins `available` under posture
  Events.
- **`ParallelPosture.declared` is replaced by `observed`.** No state field goes unread, which is
  the rule ADR-0051 applies to `serial_required_issue`.
- **No wire change.** `event_schema_version` does not move; `dashboard-insights.json` moves to
  fixture revision `1.5` because the Header gains an entry, not because a payload did.
