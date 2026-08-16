# A Pickup publishes the pair it resolved, on the record that resolved it

**Status:** accepted

Implemented by [#407](https://github.com/bradcstevens/git-loopy/issues/407), under the per-issue
routing spec [#400](https://github.com/bradcstevens/git-loopy/issues/400). Builds on
[#402](https://github.com/bradcstevens/git-loopy/issues/402) (the resolver answers a **Routing
resolution** record) and [ADR-0037](0037-routing-takes-effect-in-every-mode.md) (that record's
pair is what the session runs on, in every mode).

A **Pickup** carries the Routing resolution it reached — on its own
`wrapper.pickup.bound` **Event**, and as one line on stdout. Nothing else is added to the wire.

## The decision it was invisible to

ADR-0037 made a `[routing]` table change which model works an issue. It did not make that visible:
the Pickup Event carried no pair, and it had **no renderer handler at all**, so Pickup printed
nothing. An operator therefore got a routed Run and the same evidence they had for an unrouted
one — none. The failure mode ADR-0037 fixed and the fixed state were indistinguishable from the
outside, which is the same shape of problem [ADR-0032](0032-the-runner-picks-the-oldest-eligible-issue.md)
records about being passed over: a decision nobody can see is a decision nobody can audit.

Two audiences ask the same question at different times. An operator watching a Run asks it live;
an operator reading a replay asks it afterwards, of a Run that already ended. One answer serves
both only if it is on the record, so the record comes first and the line renders from it.

## The Pickup is extended, not joined

The pair resolves **at** the Pickup, for the Pickup's key, at the Pickup's instant. A second Event
would therefore have the same key, the same cardinality and the same timestamp as the first, and
every consumer would have to join two records to answer one question — while a stream that lost
one of them would report a Pickup that routed nowhere. So the resolution rides on
`wrapper.pickup.bound`, and no `wrapper.pickup.routed` exists.

The seven fields are **optional-when-present**. Routing is Python-only (Wrapper contract §14), so
a native port resolves nothing and has nothing to say; it emits the binding exactly as it did at
contract 1.13 and stays conforming. A consumer reads their absence as *"this Runner does not
route"* rather than as a route that failed to report — which is why a `null` is a **value** and
never an omission: a null `effort` says the backend chose.

Two naming decisions are load-bearing:

- **`model` and `effort`**, because that is already the family's wire vocabulary for a **Routed
  pair** — the same two words `wrapper.contribution.end`'s `summary` carries. A consumer that can
  read a Contribution's pair reads a Pickup's with the same code.
- **`routing_source`, not `source`**, because this same payload's `reason` already answers "why"
  in the unrelated Pickup vocabulary (`order` / `priority` / `pin`). One record cannot carry two
  differently-scoped answers to one word without a reader guessing which it has.

## What the line says, and what it declines to say

The Pickup line prints at default verbosity, one per unit of work: the issue, the pair, why that
pair, and the context tier only when the tier is worth a word.

**The no-label fallback renders shortest of all six sources.** While the corpus carries no **Task
type** it is the overwhelmingly common case, so spelling it out would nag on every Iteration.
Rendering *nothing* was rejected outright: it would collapse the fallback into the **suppressed**
case, which states the opposite thing — one operator labelled nothing, the other pinned a model
on purpose. So it renders, as one word.

**Three effort states stay distinct across two values.** An explicit `none`, a backend-chosen
effort, and a backend-chosen effort that followed a **dropped** one are three facts, and the last
two are both `null`. The gate warning beside the null is the entire difference, and saying so on
this line is the only place per-issue routing has a gate diagnostic on stdout. Both of the gate's
drop-shaped warnings — `dropped_effort` and `incapable_model` — read as *dropped*: they differ in
why the model refused the effort, not in what became of it.

**The tier is silent until it is news.** The run-level tier (ADR-0017) holds its default on every
Run today, so printing it every time would be a constant. A tier that is not the default, or one
the model gate downgraded to it, is not.

The line deliberately does *not* repeat the Pickup's selection reason or its position in the
order. Those are on the record, in the replay, and on the Dashboard's own Pickup log line; adding
them here would make the routing answer harder to find on the line that exists to give it.

## Consequences

- `RoutingResolution` projects its own wire form (`as_pickup_payload`). A serial **Iteration** and
  a **Lane** both spread that one mapping, so the two Pickups cannot describe one decision two
  ways, and provenance is never recomputed at an emit site where it could disagree with the pair
  the session was built with.
- The serial loop carries the whole resolution per candidate rather than the bare pair. The pair
  it hands the session is read off the same record the Event is written from.
- `wrapper.pickup.bound` gains its first renderer handler; Pickup stops being invisible on stdout.
- The Wrapper contract bumps to **1.21**. `event_schema_version` does not move: an unknown payload
  field is additive by §12, no existing key changed name, type or meaning, and a consumer pinned
  to fixture revision 1.1 reads a 1.21 stream unchanged.
- `event-schema.json` pins the payload twice over — as a contract (`routing_optional`, the closed
  `routing_source_values` and `lifecycle_position_values`) and as a serialization case, so a port
  that adopts routing produces the same bytes, arrays and nulls included.
- The **Dashboard** queue column and the Run-start routing block are *not* here. They are separate
  surfaces on the same record, and this ADR is what makes the record worth reading.
