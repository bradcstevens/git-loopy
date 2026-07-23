# Use one Release version for the complete distribution

Git-loopy publishes one shared Semantic Versioning Release version for every
Orchestrator, packaged Skill set, and TUI helper included in a distribution,
with a root `VERSION` file as its source of truth. This gives operators one
product identity across languages and package channels; Wrapper, Event, and
Continuation versions remain independent compatibility identities because
release equality alone does not determine runtime interoperability.

Artifacts selected as one packaged distribution require exact Release-version equality. Externally
discovered TUI helpers negotiate Event-schema capabilities instead: a compatible helper from a
different Release may be used with a warning, while Release equality by itself never proves
compatibility.
