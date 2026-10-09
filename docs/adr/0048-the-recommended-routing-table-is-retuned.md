# The recommended routing table is retuned, and no routed pair spends the escalation rung

**Status:** accepted

**Accepted-design amendment:** [ADR-0057](0057-live-evidence-guides-per-issue-routing.md)
permits `max` on a dynamic first attempt and reselects on eligible retries rather than
reserving a fixed rung. Dynamic defaults do not inherit this table's historical model/vendor
preferences; the optional static recipe is unchanged. Delivery is judged against the amendment.

Supersedes the **values** in [ADR-0035](0035-the-locked-routing-table.md). Every **rule**
ADR-0035 established stands unchanged, including the MUST it put in §14 of the Wrapper
contract.

`RECOMMENDED_ROUTING` — the table `git-loopy init` and `config routing use-recommended` seed —
is retuned. Three of its seven rows changed model, five changed effort, and one property that
ADR-0035 held for the run-wide default only now holds for the whole table.

## Quality-first retune: 2026-10-09

The maintainer selected **quality-first** models and **long context** from the
authenticated 2026-10-08 catalog. A fresh listing through the Runner's own
SDK-pinned CLI `1.0.85` confirmed the same available models, efforts and tiers.
This was the initial capture; [ADR-0019](0019-roster-derived-from-the-pinned-harness.md#corporate-compatible-upgrade-record-sdk-1017rc3)
records the later recapture through CLI `1.0.92-3`.
This is a recommended starting preset, not a comparative benchmark result:
the catalog establishes capabilities, not which model performs best on a task.

| `task-type` key | model | effort |
| --- | --- | --- |
| `planning` | `gpt-6.1-sol` | `xhigh` |
| `review` | `claude-sonnet-5.5` | `xhigh` |
| `implementation` | `gpt-6.1-sol` | `high` |
| `test` | `claude-sonnet-5.5` | `high` |
| `docs` | `gpt-6.1-sol` | `low` |
| `chore` | `gpt-6-luna` | `medium` |
| `bugfix` | `gpt-6.1-sol` | `xhigh` |

Planning and diagnosis get the deeper first-pass effort; implementation and
test construction use `high`; routine writing uses less reasoning, and chores
use Luna. Review stays Anthropic while implementation and docs stay OpenAI.
All seven pairs remain below `max`, and each is different from the retuned
legacy escalation rung, `gpt-6.1-sol @ max`. The independent run-wide default
uses that same pair, preserving ADR-0056's ceiling policy and its no-op retry
for unclassified legacy work. Selected Static routing still changes a retry's
pair only when the operator explicitly configures an escalation rung.

The maintainer's project and global presets explicitly choose `long_context`;
every recommended model offers it. The engine's built-in tier remains
`default` where neither Config scope supplies a tier. A static
route still inherits one run-level tier, not a new per-entry Config field.
These are the catalog's reported limits:

| Model | Maximum context window | Default-tier prompt cap | Long-tier prompt cap |
| --- | --- | --- | --- |
| `gpt-6.1-sol` | 1,050,000 | 272,000 | 922,000 |
| `claude-sonnet-5.5` | 1,000,000 | 200,000 | 936,000 |
| `gpt-6-luna` | 1,000,000 | 272,000 | 872,000 |

The window and tier prompt caps are separate reported fields, not quantities
to add or subtract to invent a budget. Context fill continues to read live
session telemetry, and ADR-0017's bounded working-budget/compaction policy is
unchanged. Long context can cost more; `context_tier = "default"` remains an
explicit opt-out.

Opus and Astra were absent from this account's listing, so neither is a
default. Models with a pending-deprecation notice in the supplied catalog are
also excluded from the preset. Existing saved Config elsewhere is not
rewritten: `config routing use-recommended` adopts the new pairs, while the
tracked project Config records this preset explicitly. The static recipe
does not rank or constrain Dynamic routing's live elections.

## Historical table: 2026-08-22

| `task-type` key | model | effort |
| --- | --- | --- |
| `planning` | `claude-opus-5` | `xhigh` |
| `review` | `claude-opus-5` | `high` |
| `implementation` | `gpt-5.6-terra` | `high` |
| `test` | `gemini-3.6-flash` | `high` |
| `docs` | `gpt-5.6-terra` | `low` |
| `chore` | `gpt-5.6-luna` | `medium` |
| `bugfix` | `claude-opus-5` | `xhigh` |

Presentation order is load-bearing — the guided walk surfaces the core in this sequence — so it
stays pinned separately from the values.

## What is checkable, and therefore what a later reader may not quietly undo

**No routed pair holds `max`.** `claude-opus-5 @ max` is [#291][]'s escalation rung.
[ADR-0036](0036-the-default-pair-reserves-the-ceiling.md) made the run-wide default reserve it
rather than spend it, and named the reason: a pair equal to the rung turns escalation into a
second attempt at the identical pair. ADR-0035's `planning` row spent it outright, so the one
Task type most likely to be handed something genuinely hard was the one Task type with no
second attempt. Six rows had a live escalation; now all seven do. This is asserted over the
whole mapping in `test_config.py`, not just over `planning`, so the next row that reaches for
the ceiling fails before an operator's Run does.

**A reasoning-incapable model stays unroutable.** ADR-0035's other half is untouched: an effort
supplied to an effort-incapable model **hard-rejects session creation** rather than downgrading
([ADR-0019](0019-roster-derived-from-the-pinned-harness.md)), so `claude-haiku-4.5`,
`claude-sonnet-4.5` and `auto` — empty roster entries, all three — cannot appear here.

**`review` is still not `gpt-5.6-sol`.** The argument is ADR-0035's and survives the model
moving vendor: Sol's measured task-cheating is mitigated only where it "writes no files and has
no metric to game", which describes the `code-review` **subagent** and not the `review` **Task
type** — a full Lane with a worktree, push authority, and "did the loop finish" as exactly the
metric it is documented to game.

**Cross-vendor review holds by construction.** `review` is Anthropic; `implementation` and
`docs` are OpenAI. A Lane does not review its own vendor's work. As in ADR-0035 there is no
vendor map, no cross-entry check and no warning — the property is a consequence of the values,
which is why it is written down where the values are.

**`test` sits at its model's ceiling.** `gemini-3.6-flash` offers `minimal`, `low`, `medium`,
`high` and nothing above, so this row cannot be raised without also changing its model, and an
operator who overrides it to `xhigh` gets a hard reject. Its floor is `minimal`, not `none` —
the two OpenAI rows accept `none` and this one does not, which is the kind of roster asymmetry
that reads as a typo right up until it fails.

## Provenance, honestly

This is a maintainer retune, not a measurement. No new routing run produced it, and it is not
entitled to more authority than that: `RECOMMENDED_ROUTING` is a hardcoded human guess, which
is precisely why [ADR-0027](0027-routing-is-calibrated-by-measurement.md) refuses to seed the
Calibration search from it and [ADR-0028](0028-measured-routing-is-a-committed-tier.md) demotes
it to a bootstrap default the moment a repository has measured values of its own. Recording it
is the point: ADR-0035 was written because the table had been retuned three times in 36 hours,
twice with nothing written down, and a value with no rationale cannot be told from a mistake.

The shape of the change, stated as intent rather than as evidence: spend on the rows that
decide what gets built and whether it was built right (`planning`, `review`, `bugfix`), buy
capability rather than the cheapest defensible rung on the row that writes most of the code
(`implementation` moves off `claude-sonnet-5 @ low`), and keep the rows whose output is
mechanically checked cheap (`docs`, `chore`).

ADR-0035 licensed `implementation @ low` on the rule that *the recommended table may lean on
unbuilt mechanism when the failure mode is bounded and visible*. That licence is not revoked —
it was correctly applied — but leaning on it costs iteration budget on every task the cheap
rung cannot converge on, and this table declines to pay that.

**Review trigger: a roster change touching a routed model** — not a calendar date, which passes
unnoticed. The routed set is now `claude-opus-5`, `gpt-5.6-terra`, `gemini-3.6-flash` and
`gpt-5.6-luna`. `claude-sonnet-5` leaves it, having held three of seven rows; a roster change
touching Sonnet 5 no longer reaches this table.

## Consequences

- `git-loopy init` and `config routing use-recommended` seed these values. An operator who
  adopted the previous core keeps their written `[routing]` table — it is their Config, not the
  kit's — and `config routing use-recommended` is how they take the retune.
- At this retune, the tracked project Config (`git-loopy/config.toml`) carried the new table
  and `test_config.py` held the two in lockstep. In
  [#594](https://github.com/bradcstevens/git-loopy/issues/594), the maintainer explicitly
  retained the project's subsequently customized routes and replaced that equality check
  with checks of the intentional overrides. The built-in recommendation remains independently
  pinned; its no-`max` rule does not prohibit an operator-authored project route using `max`.
- Escalation is live for every Task type, including `planning`.
- `gemini-3.6-flash` enters the routed set as the first Google model on it, so the roster's
  `minimal` floor and `high` ceiling are now load-bearing for a shipped route rather than
  reference data.
- The relation between `planning`, `bugfix` and the run-wide default is now *equality* rather
  than ADR-0036's near-miss. It remains rationale and not mechanism: the default is an
  independent constant in `cli.py`, never derived from this table, so `config routing set
  planning …` cannot move it.

[#291]: https://github.com/bradcstevens/git-loopy/issues/291
