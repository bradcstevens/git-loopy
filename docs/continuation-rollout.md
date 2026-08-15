# The family-wide Continuation rollout

Continuation ships in stages, and this is the page that says which stage is open,
what it takes to open the next one, and what an operator gets — or does not get —
when they opt in. The gate itself is
[`git_loopy/continuation_rollout.py`](../git-loopy/python/git_loopy/continuation_rollout.py);
the [Continuation contract](continuation-contract.md) defines the behavior each
stage requires.

## Selecting and verifying one native distribution

Setup verifies **the distribution that runs it**. There is no entrypoint to resolve
and no family member to name, which is exactly how the selection is recorded without
committing a host-specific executable path: the choice is expressed by the
invocation and forgotten afterwards. Nothing about it is written into Config, so the
Config a project commits stays portable across the family.

```text
git-loopy init
```

Setup prints two lines, and they answer two different questions:

```text
Verified this distribution's Continuation capabilities (foundation profile, contract 1.3, release …); unsupported optional capabilities: concurrent_dispatch.
Continuation rollout: report is open family-wide; execute-frontier is withheld pending powershell, shell. Concurrent Dispatch is unsupported. Mode stays off until an operator opts in.
```

The first is a claim about **this** distribution: it satisfies a named capability
profile, or setup stops before writing anything. The second is a claim about the
**family**: which staged gates are open everywhere, which is what makes a mode
generally available rather than locally implemented.

The shell Orchestrator verifies the same way from its own installer, and the
PowerShell Orchestrator from its own module. Every command in this page resolves
its tools through `PATH`.

## The staged gates

| Stage | What it requires | Status |
| --- | --- | --- |
| `foundation` | Contract version, record format, the GitHub Adapter and its operations, the native operation set, and `default: off` | Open family-wide |
| `report` | Everything above, plus `resolve-authority` and `continuation_modes.report` — and complete core Transition-owner coverage as a **mandatory precondition** (contract §4) | Open family-wide |
| `execute-frontier` | Everything above, plus `continuation_modes.execute-frontier` and the optional `fixed_frontier_authorization` capability | Withheld pending shell and PowerShell |

Two rules govern the table:

- **A stage is proved, never counted.** It opens only when *every* mandatory member
  — Python, shell, PowerShell — has passed the mandatory fixtures for it. Two of
  three is a withheld gate, not a partly open one. The Rust Dashboard core is not a
  mandatory member: it consumes Events and never publishes or reconciles, so it has
  no Continuation capability manifest to judge.
- **Staging is monotonic.** A later gate stays shut behind a withheld earlier one,
  because the requirement sets are nested. A family that could execute a frontier
  but not report on it would be advertising a mode nobody can observe.

The attribution behind the table lives in the shared Conformance fixture
(`capability_verification.profile_distributions`), and each family member pins its
own row against the manifest its real native entrypoint advertises. The chain runs
real manifest → verdict → attribution → the stage an operator is shown, so a member
that starts advertising a mode without joining the attribution fails its own
Conformance suite rather than surprising an operator mid-Run.

### Serial only, and concurrency is never inferred

The first `execute-frontier` release dispatches **one Action at a time**. Opening
that gate does not open a concurrency gate: `concurrent_dispatch` is advertised
`false` by every family member and has its own later family-wide gate. Serial is the
whole of the claim.

Across-issue concurrency (ADR-0008, Parallel mode) reads a closed set of inputs and
only that set:

1. a human's issue-backed `parallel-safe` assertion,
2. the Actions' Prerequisites,
3. their durable Targets,
4. their effect scopes.

Anything outside it — a label that reads as safe, a Skill name, prose in a comment,
a local file, an earlier conversation — is inference, and inference is what the gate
exists to refuse.

## Migrating a repository that predates Continuation

There is no backfill and no migration script. A legacy Workstream is adopted
**when its next recognized Transition owner publishes the first trusted root for
it** — the ordinary `publish` a Skill already performs at its next durable
transition. Nothing else adopts one: not a label, not prose, not a comment, not a
local file, not conversation history.

That makes a mid-migration repository normal rather than exceptional, so the
projection says so out loud. Report mode's guidance line names the observed carriers
holding no trusted root:

```text
git-loopy continuation (pre-iteration, octo/example): guidance; 1 Ready of 1 Action(s); 1 diagnostic(s). No successor Action was executed. Continuation coverage: 1 adopted Workstream(s); 1 observed carrier(s) hold no trusted root (#41). Unadopted Workstreams are outside authorization and cannot support a terminal-completion claim; a legacy Workstream is adopted when its next recognized Transition owner publishes its first trusted root.
```

Unadopted Workstreams are excluded from **authorization** — a Run may not dispatch
work it cannot see the Producer revision for — and from **terminal-completion
claims**. `index_label_stale` is a coverage-uncertainty diagnostic in every family
member, so a read that finds an indexed carrier holding no trusted record renders
`waiting` rather than a project-wide `complete` built only over the adopted half.

Adoption is per Workstream and monotonic. A repository is expected to spend time
partly adopted; it is never expected to be converted in one pass.

## Capability requirements

A Run fails closed at preflight rather than mid-flight when the distribution cannot
serve the mode it resolved:

- The **GitHub Adapter** is required for any Continuation participation.
- `report` requires `continuation_modes.report` and `resolve-authority`.
- `execute-frontier` requires `report` beside it (an operator's project table may
  narrow a global `execute-frontier` down to `report`, and a distribution
  advertising only the stronger mode would fail closed on the weaker one it just
  resolved to), plus `fixed_frontier_authorization` — §9's authorization is gated by
  that capability, so advertising the mode without it advertises a mode with no
  decision procedure behind it.
- `execute-frontier` additionally requires a configured `actor`. Dispatch evidence
  is bound to its Performer at both ends, so a Run with nothing to write as would
  lose the one record a human needs.

`default` is `off` in every distribution at every stage. An open gate makes a mode
*available*; only an operator's §10 configuration makes it active.

## Failure recovery

| Symptom | What it means | Recovery |
| --- | --- | --- |
| Setup prints `this distribution does not satisfy the … profile (…)` and writes nothing | The named requirements are missing from this distribution's manifest | Install a distribution that meets the profile; the line names requirements, not manifest keys, because hand-editing an advertisement is not a fix |
| Setup reports a stage `withheld pending …` | The named members have not proved the stage | Nothing to fix locally: the mode is not generally available yet. A distribution that implements it may still be run directly |
| A Run resolves a mode and preflight fails closed | Configuration asked for more than this distribution advertises | Narrow the §10 configuration, or install a distribution that advertises the mode |
| `unsupported_operation` from a native command | The operation is present but capability-gated off | Read `git-loopy continuation capabilities`; the manifest is the whole answer |
| Guidance names carriers holding no trusted root | The repository is mid-adoption | Let the next recognized Transition owner publish; do not hand-write a record or relabel a carrier |
| A tainted Producer lineage is quarantined | An ancestor revision is untrustworthy | Use the contract's authorized re-attestation path; recovery is explicit and audited, and it cannot wash away ancestor problems |
| Runtime revocation narrows a Run | Grants or capability were withdrawn mid-Run | Already-authorized partial effects are preserved and the Run stops with a nonterminal status naming the next Action; nothing is synthesized to replace it |

## What the rollout may never introduce

Opening a gate is exactly when a shortcut starts to look reasonable, so contract §1
is restated here unchanged. No Continuation operation may establish a **central
continuation issue**, an **authoritative Markdown snapshot**, a **mutable project
queue**, an **append-only execution journal**, a **central tombstone ledger**, or an
**authoritative local cache**. The gate carries that list in code and pins it
against the contract's own sentence.
