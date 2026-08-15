# The recommended routing table is locked, and a reasoning-incapable model is unroutable

**Status:** accepted

Implemented by [#401](https://github.com/bradcstevens/git-loopy/issues/401), under the
per-issue routing spec [#400](https://github.com/bradcstevens/git-loopy/issues/400). Locks the
values decided on [#285](https://github.com/bradcstevens/git-loopy/issues/285) and
[#294](https://github.com/bradcstevens/git-loopy/issues/294), sourced from the model-routing run
of 2026-07-24 posted on the map [#280](https://github.com/bradcstevens/git-loopy/issues/280).

`RECOMMENDED_ROUTING` — the table `git-loopy init` and `config routing use-recommended` seed —
shipped two pairs the design had considered and **rejected**. The table is corrected to the
locked values, and the rule that disqualified one of them is written down so it stops being a
preference somebody can talk themselves out of.

## The locked table

| `task-type` key | model | effort |
| --- | --- | --- |
| `planning` | `claude-opus-5` | `max` |
| `review` | `gpt-5.6-terra` | `xhigh` |
| `implementation` | `claude-sonnet-5` | `low` |
| `test` | `claude-sonnet-5` | `medium` |
| `docs` | `claude-sonnet-5` | `low` |
| `chore` | `gpt-5.6-luna` | `none` |
| `bugfix` | `claude-opus-5` | `xhigh` |

Presentation order is load-bearing — the guided walk surfaces the core in this sequence — so it
is pinned separately from the values. Cross-vendor review holds **by construction** rather than
by mechanism: Anthropic plans, implements, tests and documents; OpenAI reviews and does chores.
There is no vendor map, no cross-entry check and no warning, exactly as #285 settled.

## A reasoning-incapable model is unroutable

`chore` routed to `claude-haiku-4.5 @ none`. Haiku exposes **no effort dial at all** — its
roster entry is the empty set — so that pair is not a configuration that exists. It *reads* as
"Haiku with reasoning off" and *delivers* Haiku with an internal thinking budget running
uncapped. It is the one row in the table that meant the opposite of what it said.

The reason this is a defect and not untidiness is
[ADR-0019](0019-roster-derived-from-the-pinned-harness.md)'s finding: an effort supplied to an
effort-incapable model **hard-rejects session creation** rather than downgrading. The effort
gate rescued the shipped table by dropping the effort and warning, but the gate is a rescue, not
a licence — a pair that only survives because something upstream rewrites it fails wherever that
rewrite is not in the path. `n/a` is not `none`. `gpt-5.6-luna @ none` is deterministic about
not thinking, and it makes the chore path cross-vendor at no cost.

So: **a reasoning-incapable model is unroutable through `[routing]`**, now a MUST in §14 of the
Wrapper contract. It was already invariant at four independent layers — `RECOMMENDED_ROUTING`'s
`tuple[str, str]` type, `settings.table_routing`'s exactly-`{model, effort}` demand, the guided
walk's `supported_efforts` filter, and the uniform gates-clean assertion in `test_config.py` —
and the shipped table was the one thing contradicting all four.

**This was latent, not harmless.** Zero issues carry a `task-type:` label today, so the pair
reached no session. It would have gone live the moment the corpus was labelled, which is why
correcting the table **blocks** labelling it.

## The two departures from the run, so they are not "corrected" back

**`review` is `gpt-5.6-terra`, not the run's `gpt-5.6-sol`.** The run's entire mitigation for
Sol's measured task-cheating — the highest detected-cheating rate METR had recorded — is one
clause: Sol *"holds only review roles here, where it writes no files and has no metric to
game."* That is true of the `code-review` **subagent** and false of the `review` **Task type**.
A `task-type:review` issue is a full git-loopy Lane: its own worktree, commit and push
authority, and authority to close its own issue, with "did the loop finish" as precisely the
metric it is documented to game. Terra is the run's own runner-up for the row: same vendor, same
ladder, none of the findings.

**`implementation` stays at `low`** even though the escalation backstop it assumes is a separate
ticket. The rule licensing that: *the recommended table may lean on unbuilt mechanism when the
failure mode is bounded and visible; it may not when the failure mode is silent.* A task Sonnet
5 at `low` cannot converge on burns its iteration budget and fails visibly, because the tests
still gate. Sol's metric-gaming is neither bounded nor visible, which is why the same latitude
was refused above — and why `bugfix` exists at all: a cheap model produces a plausible wrong fix
that commits and goes green, so bug work may not be filed under `implementation`.

`bugfix` sits at `xhigh` rather than `high`, because `high` would make labelling a bug *cheaper*
than not labelling it, and rather than `max`, because `max` is the escalation rung and a routed
pair equal to the rung makes escalation a no-op.

## What this decision is not

**Not a closed taxonomy.** `RECOMMENDED_ROUTING` is a **seed**, not the vocabulary.
`resolve_iteration_model` reads the operator's `[routing]` table as the sole source of valid
keys and never consults this constant. Locking the values leaves the door where it found it.

**Not a table of triples.** The routing run adds a context-tier dial, but all seven rows are
`default`; a tier column would set seven identical values and differentiate nothing. Context
tier is [ADR-0017](0017-context-tier-and-live-context-gauge.md)'s.

**Not the run-wide default.** That is [ADR-0036](0036-the-default-pair-reserves-the-ceiling.md),
and the resemblance between it and the `planning` row is rationale, never mechanism.

## Provenance, and when to review this

The table has been retuned three times in one 36-hour window, twice with no recorded reasoning,
which is the whole argument for writing this down: a value with no rationale cannot be told from
a mistake, and the second retune's reasoning was gone within the hour.

**Review trigger: a roster change touching a routed model** — not a calendar date, which passes
unnoticed. The routed set is `claude-opus-5`, `gpt-5.6-terra`, `claude-sonnet-5`, `gpt-5.6-luna`.

## Consequences

- `git-loopy init` and `config routing use-recommended` seed only pairs the design locked, and
  every routed pair in the shipped table can create a session.
- The tracked project Config carries this table, so the repository that owns the feature is no
  longer the one repository proving it inert.
- A future reasoning-incapable route fails `test_config.py` before it fails an operator's Run.
- The `chore` route moves vendor. An operator who adopted the recommended core before this
  lands keeps their written `[routing]` table — it is their Config, not the kit's — and
  `config routing use-recommended` is how they take the correction.
