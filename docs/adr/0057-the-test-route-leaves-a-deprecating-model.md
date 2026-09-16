# The test route leaves a deprecating model, and a resolved context tier finally reaches the session

**Status:** accepted

Supersedes the `test` **value** in
[ADR-0048](0048-the-recommended-routing-table-is-retuned.md). Every **rule** ADR-0048 carried
forward from [ADR-0035](0035-the-locked-routing-table.md) stands unchanged, including the MUST in
§14 of the Wrapper contract. Closes the execution half of
[#560](https://github.com/bradcstevens/git-loopy/issues/560).

Two things land together because the second is what makes the first provable.

## The table

| `task-type` key | model | effort |
| --- | --- | --- |
| `planning` | `claude-opus-5` | `xhigh` |
| `review` | `claude-opus-5` | `high` |
| `implementation` | `gpt-5.6-terra` | `high` |
| **`test`** | **`claude-sonnet-5`** | **`high`** |
| `docs` | `gpt-5.6-terra` | `low` |
| `chore` | `gpt-5.6-luna` | `medium` |
| `bugfix` | `claude-opus-5` | `xhigh` |

One row changed. Presentation order is load-bearing and stays pinned separately from the values.

## Why `test` had to move

`gemini-3.6-flash` is **being withdrawn**. The authenticated harness now attaches an
`info_messages` entry to it — `model_pending_deprecation`, *"Gemini 3.6 Flash has a planned
deprecation date of 2026-10-02"* — and attaches the same notice to `gemini-3.5-flash` and
`claude-opus-4.7`. This is first-party lifecycle data read off the live roster, not a preference.
A routed default pointing at a model that stops existing is a table that breaks on a date rather
than on a change, which is the failure mode hardest to attribute afterwards.

Independent of the withdrawal, the incumbent was also the **weakest** row in the table. On
Arena's Agent board (2026-09-15T14:00Z, field 45, percentage-point causal treatment effect
against a moving average-model baseline) `gemini-3.6-flash` sits at **−6.06 ± 1.03 pp** — the
only entry on that board decisively separated from every other candidate, and separated
downward. `claude-sonnet-5` sits at **+5.73 ± 1.61 pp** and scores **85.2** on SWE-bench
Verified.

## What is checkable, and therefore what a later reader may not quietly undo

**`test` is no longer pinned to its model's ceiling.** ADR-0048 recorded that row as bounded by
its roster entry: `gemini-3.6-flash` offered nothing above `high`, so raising it would
hard-reject at session creation. `claude-sonnet-5` offers `xhigh` and `max`, so that guard is
gone and the row is now held down only by the **no-`max`** rule. That rule still holds over the
whole mapping and is still asserted in `test_config.py`.

**Vendor diversity narrowed, and the invariant that matters still holds.** The routed set was
`claude-opus-5`, `gpt-5.6-terra`, `gemini-3.6-flash` and `gpt-5.6-luna`; it is now
`claude-opus-5`, `claude-sonnet-5`, `gpt-5.6-terra` and `gpt-5.6-luna`. Google leaves the table
and `test` joins `planning`, `review` and `bugfix` on Anthropic. ADR-0048's stated property is
unaffected — `review` is Anthropic while `implementation` and `docs` are OpenAI, so a Lane still
does not review its own vendor's work — but the table is now two vendors rather than three, and
a future retune should treat that as a cost it is paying rather than a fact it inherited.

**`claude-sonnet-5` was already on the pinned roster.** It needed no roster extension and no
conformance-fixture change, which is deliberate: the roster is a function of CLI version
([ADR-0019](0019-roster-derived-from-the-pinned-harness.md)), stamped at `1.0.75` and held in
lockstep with `conformance/model-roster.json` across all four Runner family members. The
otherwise-obvious replacement — `gemini-3.8-flash`, which would have kept Google on the table —
is **off-roster**, and so are `gpt-6-astra`, `gemini-3.7-flash` and both Grok entries. Choosing
one of those would have made a routing retune into a contract change.

**`review` is still not `gpt-5.6-sol`.** Untouched, and worth restating because this retune had
evidence pointing the other way: on Bug Hunt Bench — 105 planted bugs, pass@1, blind judge,
claimed-only findings excluded — `gpt-5.6-sol` finds 42/39/34 at `max`/`xhigh`/`high` against
`claude-opus-5`'s best of 27. Sol is the better defect finder *and still may not have the
`review` Task type*, because the mitigation for its measured task-cheating is that it writes no
files and has no metric to game, which describes the `code-review` subagent and not a Lane with
a worktree, push authority, and "did the loop finish" as exactly the metric it games.

## The routed context tier now reaches session creation

[ADR-0017](0017-context-tier-and-live-context-gauge.md) gave a Run a root-session **context
tier**. The resolver gated it against the routed model, `RoutingResolution` carried it as a
triple, a Pickup published it, and the Dashboard rendered it — and then
`IterationSession.__aenter__` called `create_session(model=..., reasoning_effort=...)` and
**dropped it**. The tier was decided, transported, reported, and never sent.

This was not a cosmetic gap. Probing both runtimes directly, with no prompt sent:

| Requested `context_tier` | native `1.0.84-9` | SDK-pinned `1.0.67` |
| --- | --- | --- |
| omitted — what a Run did | **no tier reported at all** | **no tier reported at all** |
| `"default"` | `default` | `default` |
| `"long_context"` | `long_context` | `long_context` |

An omitted tier does not resolve to the default one; it leaves the session with **no tier**.
`session.metadata.contextInfo` returns null in every case, so there was no way to observe after
the fact which tier a Run had actually used — the value on the Event stream was the value the
resolver *chose*, not the value the session *ran on*.

`IterationSession` now takes `context_tier` and forwards it. The resolved tier is threaded
through every mode, because [ADR-0037](0037-routing-takes-effect-in-every-mode.md) holds that a
resolved route takes effect wherever it is resolved: the serial Iteration passes
`resolution.context_tier`; a Lane binds it onto its `Contribution` beside the pair, so recovery
reuses it exactly as it reuses the model; and `ContributionRequest` carries it across the
Execution host seam onto the wire, so a remote GitHub Actions contribution runs on the tier its
Pickup resolved instead of on none.

`None` remains meaningful and is pinned by a test: it means *do not send the field*, leaving the
choice to the backend. That is the behaviour every Run had before this change, which is why the
new parameter defaults to it rather than to `"default"`.

## Provenance, honestly

The `test` row's **withdrawal** trigger is first-party runtime data. Its **replacement** is a
maintainer retune informed by public leaderboards, and is entitled to no more authority than
that. No calibration run produced it. [ADR-0027](0027-routing-is-calibrated-by-measurement.md)
still refuses to seed the Calibration search from `RECOMMENDED_ROUTING`, and
[ADR-0028](0028-measured-routing-is-a-committed-tier.md) still demotes this table to a bootstrap
default the moment a repository has measured values of its own. Nothing here is labelled
measured, and no `routing.measured.toml` was touched.

The supporting evidence has known limits, recorded so they are not rediscovered as surprises:
there is **no test-authoring or mutation-testing benchmark** on any surface consulted, so
`claude-sonnet-5` was selected on repository-coding proxies; Terminal-Bench was deliberately
**not** used for this row, because it scores test-file edits as reward hacking and is therefore
evidence *against* a model for test authoring rather than for it; and the two harnesses that run
Terminal-Bench disagree materially about the field, a disagreement left standing rather than
reconciled.
