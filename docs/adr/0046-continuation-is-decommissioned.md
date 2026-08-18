# Continuation is decommissioned

**Status:** accepted

**Supersedes** [ADR-0034](0034-contract-carrying-skills-are-authored-upstream.md), whose
entire subject — the twelve Skills that carry a `git-loopy continuation` request, mirrored
into this repository and guarded by an owner-coverage suite — has no referent once the
command it names does not exist.

Workflow Continuation was a durable-record contract: a Skill that finished a piece of work
published a typed JSON record through `git-loopy continuation publish`, and a later session
reconciled those records into an answer about what to do next. It reached roughly thirty
thousand lines across the Runner family — `continuation.py` (4,941), `continuation_frontier.py`
(1,079), `continuation_report.py` (175), `verification.py` (335), `shell/lib/continuation.sh`
(6,408), a PowerShell module, some sixteen thousand lines of test, and a 38,396-line
Conformance fixture — plus a §15 of the Wrapper contract obliging every Orchestrator to
expose the namespace.

## Decision

Continuation is removed from git-loopy entirely, and from the external Skill catalog that
git-loopy pins. Nothing is deprecated first and no tombstone command is kept.

A replacement will be designed later. It is deliberately not designed here, and this ADR
records no requirement it must satisfy: the point of removing the mechanism now is that the
next one should be reached from the problem, not inherited from this one's vocabulary.

## Why

The feature was **not on the critical path and never had been.** `resolve_authority` defaulted
to mode `off`, so an ordinary Run — serial or Parallel — never invoked it. The Rust Dashboard
core referenced it nowhere. Wrapper contract §15 said so in as many words: the namespace was
required "without making Continuation part of the Run loop". A subsystem of that size that no
default path executes is carrying cost against no delivered behaviour.

The cost was **concentrated in the places that are most expensive to be wrong.** It occupied
about 312 lines and some seventy terms of `CONTEXT.md` — Workstream, Anchor, Basis, Producer,
Consumer, Blocker, Readiness, AFK-safe, Automation frontier — which is the vocabulary every
other decision in this repository is written against. It bound all three Orchestrators to one
38,396-line Conformance fixture, so any change to it was a three-language change.

And the **published half was newer than the specified half.** ADR-0034 records that at the
pinned catalog revision not one of the twelve Skills carried a single `continuation` request:
git-loopy specified, tested and proved a contract that no installed session actually
exercised. Deleting the specification costs less than finishing the thing it specified.

## Consequences

- The Wrapper contract goes to **2.0**. Repealing a MUST-level public namespace is breaking
  by definition: a distribution claiming 1.x would still advertise commands that no longer
  exist, so a minor bump would encode a false compatibility claim. §15 is deleted and the
  sections after it renumbered; `WRAPPER_CONTRACT_VERSION` moves out of `continuation.py` to
  a new `git_loopy/version.py`, where the family's identity does not depend on one feature's
  module surviving.
- The four `wrapper.continuation.*` Event types leave the Event schema and the shared
  fixture. The schema stays at compatibility `schema_version` 1: they were additive, and
  consumers are already required to ignore unknown types.
- `git-loopy init` no longer runs a Continuation capability self-check, and `source_release`
  no longer verifies a capability manifest during a Release. Both verified a feature that is
  gone.
- Eleven Skills keep the discipline that lived beside the machinery — run validation, push a
  durable head, **post a plain evidence comment on the ticket** — and lose only the JSON
  request and the command. That discipline was never Continuation's contribution; it just
  shared a section with it.
- Routing between Skills becomes prose. A Continuation `Action` used to name a successor in a
  machine-readable way; each Skill now names the next one in its own text. `/next` is
  unaffected — it routes from live tracker, branch, diff and worktree state, and never read a
  Continuation record.
- `Performer` is gone from the glossary, folded into **Agent**. Its three surviving uses all
  meant "the live harness session that loads and invokes a Skill", which is what an Agent
  already is; only the Continuation framing made them look like different things.
- `Contract-carrying Skill` is gone too. It denoted a Skill whose instructions invoke a
  `git-loopy` subcommand, and after this change no Skill does.
- Earlier ADRs that cite Continuation keep their text and gain a pointer to this one. An ADR
  records what was decided and why *at the time*; rewriting that reasoning would destroy the
  audit trail the format exists to provide. In particular
  [ADR-0028](0028-measured-routing-is-a-committed-tier.md) cites
  `git-loopy/shell/lib/continuation.sh:18` as evidence for a version figure. **That citation
  now dangles.** It was true when written, git history still holds the file, and it is left
  standing deliberately rather than quietly repaired.
