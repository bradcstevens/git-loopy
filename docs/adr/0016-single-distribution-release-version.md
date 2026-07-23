# Use one Release version for the complete distribution

Git-loopy publishes one shared Semantic Versioning Release version for every
Orchestrator, packaged Skill set, and TUI helper included in a distribution,
with a root `VERSION` file as its source of truth. This gives operators one
product identity across languages and package channels; Wrapper, Event, and
Continuation versions remain independent compatibility identities because
release equality alone does not determine runtime interoperability.
