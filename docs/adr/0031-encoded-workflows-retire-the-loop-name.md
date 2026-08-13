# git-loopy encodes repeatable workflows, so the "Ralph loop" name is retired

**Status:** accepted

## Context

[ADR-0005](0005-rename-to-copiloop.md) retired the `ralph-afk` *brand* but deliberately
**kept "Ralph loop" as the name of the technique** — "the unattended, iterative execution
loop that drives the Copilot agent." That was accurate when the product was one loop: collect
`ready-for-agent` issues, run one, repeat.

It is no longer what ships. A Run today selects a **Routed pair** per **Task type** from a
closed seven-key taxonomy, calibrates those routes from measurement
([ADR-0027](0027-routing-is-calibrated-by-measurement.md),
[ADR-0030](0030-demotion-is-measured-per-pair.md)), works several issues at once in
worktree-isolated **Lanes** behind a serialized **Integration** gate
([ADR-0008](0008-across-issue-parallelism-via-git-worktrees.md),
[ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md)), runs the repository's
own declared feedback loops as that gate ([ADR-0009](0009-runner-driven-integration-and-auto-resolution.md)),
loads a closed world of Skills ([ADR-0015](0015-closed-world-skill-policy.md)), accounts for
context and billed cost ([ADR-0017](0017-context-tier-and-live-context-gauge.md),
[ADR-0026](0026-billed-cost-and-the-live-rate-card.md)), and derives the next action from
durable workflow facts (the Continuation contract).

The loop is one control-flow feature inside that system. Naming the whole after it describes
the least valuable part and imports a lineage the project has outgrown. What the project
actually does — and the reason to build it rather than prompt an agent — is **encode
specialized engineering knowledge into workflows that run repeatably**. A workflow that can be
trusted runs tens, hundreds, or thousands of times and compounds; a single agent conversation
cannot. The corollary matters as much: value comes from placing engineers, agents, and
deterministic code deliberately, *not* from spawning more agents. Sprawl costs tokens, hides
intent, and multiplies the paths by which poor output reaches a branch.

## Decision

Retire **"Ralph loop"**. The last retained use of `ralph` in this project's vocabulary ends
here.

- The technique keeps a glossary entry under the neutral name **Autonomous loop**, defined as
  the unattended, iterative execution loop realized as a **Run** of **Iterations**. Its
  `_Avoid_` line names "Ralph loop" as retired, so the retirement is discoverable from the
  term it replaces.
- The framework's stated purpose in `CONTEXT.md` and `README.md` becomes *encoding
  specialized engineering knowledge into repeatable, autonomous workflows*, and **loop
  engineering** is defined by that unit of value rather than by the loop's mechanics.
- **Meta-engineering** joins the glossary as a practice term: working on the system that
  builds and operates the software rather than on the software directly. It names why
  evidence from a Run is fed back into the workflow and not only into the branch.
- The retired-branding guard (`test_no_retired_branding.py`) forbids the bare substring
  `ralph`, not just the `ralph_afk` / `ralph-afk` / `RALPH_` / `ralph/` brand forms. With the
  concept retired there is no legitimate live use left, so the narrow patterns that existed
  only to protect it are no longer needed.
- Existing ADRs keep their contemporaneous wording. They are point-in-time records, already
  exempt from the guard, and rewriting them would falsify the history that explains the
  current names. This ADR is the pointer that tells a reader which of their sentences no
  longer holds.

## Considered options

- **Keep "Ralph loop" for the technique** — rejected: it is the single term that still frames
  the product as one loop, which is the framing this ADR exists to correct. Keeping it also
  forces the guard to stay narrow forever, so `ralph` in any *new* form keeps slipping through.
- **Retire the entry outright and rely on Run / Iteration** — rejected: `Run` and `Iteration`
  are units of execution and accounting, not the name of the technique, and deleting the entry
  would delete the `_Avoid_` line that makes the retirement discoverable.
- **Coin a product-specific name for the technique** — rejected: a coinage would need its own
  teaching, and the point of the change is to stop naming the system after its loop.
- **Rewrite the historical ADRs** — rejected: ADRs are immutable history; the guard already
  exempts `docs/adr/` for exactly this reason.

## Consequences

- `README.md` leads with the workflow-encoding and meta-engineering framing, states the
  three-actor split (engineers own intent and judgment, agents own reasoning, code owns
  routing, validation, and gates), and says explicitly that an army of agents is not the goal.
- `CONTEXT.md` gains **Meta-engineering** and **Autonomous loop**, redefines **Loop
  engineering**, and records the retirement in its flagged ambiguities.
- The guard's forbidden pattern widens to bare `ralph`; the former repo slug
  `github-copilot-ralph-starter-kit` is consequently flagged outside the exempt records, which
  is correct — that slug was retired by ADR-0012.
- Test fixtures that used `ralph` as filler prompt text or commit subjects are renamed. They
  asserted nothing about the word, so the change is mechanical.
- [ADR-0005](0005-rename-to-copiloop.md) and [ADR-0012](0012-rebrand-copiloop-to-git-loopy.md)
  each contain a sentence retaining the "Ralph loop" concept. Those sentences are superseded
  by this ADR and carry a pointer to it.
