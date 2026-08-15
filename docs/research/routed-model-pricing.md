# Routed-model pricing and context windows

**Question** ([#283](https://github.com/bradcstevens/git-loopy/issues/283), map [#280](https://github.com/bradcstevens/git-loopy/issues/280)):
what are the current provider list prices and context windows for every model the
new task-type routing table can route to?

**Captured** 2026-07-24. **Basis:** provider list prices, per `pricing.toml`'s own
header — *not* GitHub Copilot's premium-request billing, which the SDK does not expose.

> This file establishes `docs/research/` as the home for research-ticket findings.
> The repo had no prior convention; design notes land in `docs/adr/`, prototypes in
> `prototypes/`, and neither fits a fact-gathering note.

## Headline

The ticket asked for six missing models. It found those, and three things nobody
was looking for:

1. **`pricing.toml`'s Opus prices are wrong by 3x.** `claude-opus-4.8` and
   `claude-opus-4.7` are recorded at `15.00 / 75.00` — Opus **4.1**-era rates.
   Anthropic's current price for both is `5.00 / 25.00`. Every cost figure
   git-loopy has reported for Opus work is roughly triple the real list price.
2. **Five context windows are stale, not three.** The known-stale Claude trio is
   confirmed, and the live Copilot catalog additionally contradicts `gpt-5.4`
   (`256_000` recorded vs `1_050_000` live) and `gpt-5-mini` (`256_000` vs `264_000`).
3. **The three-field schema cannot express what these models actually cost.** Six
   independent axes are unrepresentable; the cache-read axis alone systematically
   mis-prices a Copilot agent loop, which caches heavily. See [Schema](#schema-inadequacy).

## 1. The six routed models

| Model | input $/MTok | output $/MTok | context window | Confidence |
|---|---|---|---|---|
| `claude-opus-5` | **5.00** | **25.00** | **1_000_000** | High |
| `claude-sonnet-5` | **3.00** std / 2.00 intro | **15.00** std / 10.00 intro | **1_000_000** | High — but see (b) |
| `claude-haiku-4.5` | **1.00** | **5.00** | **200_000** | High |
| `gpt-5.6-sol` | **5.00** | **30.00** | **1_050_000** | High |
| `gpt-5.6-terra` | **2.50** | **15.00** | **1_050_000** | High |
| `gpt-5.6-luna` | **1.00** | **6.00** | **1_050_000** | High |

**Opus 5 at $5.00 / $25.00 is confirmed.** Anthropic's model-pricing table row reads
`Claude Opus 5 | $5 / MTok | $6.25 | $10 | $0.50 | $25 / MTok`, and the same page's
Managed-Agents worked example computes `50,000 x $5 / 1,000,000` and
`15,000 x $25 / 1,000,000`. The prior handoff's figure was right.

**OpenAI prices carry two independent first-party confirmations.** The pricing page's
source array reads `["gpt-5.6-sol", 5, 0.5, 6.25, 30]`, `["gpt-5.6-terra", 2.5, 0.25,
3.125, 15]`, `["gpt-5.6-luna", 1, 0.1, 1.25, 6]` (input, cached input, cache write,
output). The launch post states the same in prose.

## 2. Context windows — resolved against the live catalog

The research pass could not establish the GPT-5.6 window from OpenAI's docs: the model
catalogue pages are client-rendered and publish no fetchable token count, and secondary
sources conflicted (272,000 vs 1,050,000). Rather than guess, it was read from the
harness that actually matters.

```
$ .venv/bin/python -c 'async with CopilotClient() as c: await c.list_models()'
# capabilities.limits.max_context_window_tokens
claude-haiku-4.5        200000
claude-opus-4.6        1000000
claude-opus-4.7        1000000
claude-opus-4.8        1000000
claude-opus-5          1000000
claude-sonnet-4.6      1000000
claude-sonnet-5        1000000
gpt-5-mini              264000
gpt-5.4                1050000
gpt-5.6-luna           1050000
gpt-5.6-sol            1050000
gpt-5.6-terra          1050000
```

**`1_050_000` settles it** — the "272,000 official GA figure" circulating in secondary
write-ups is wrong for this harness. This also corroborates the weak first-party signal
noticed during the doc pass: OpenAI's pricing page labels `gpt-5.4`, `gpt-5.5`, and the
`-pro` rows *"(<272K context length)"* but attaches **no such label to any `gpt-5.6-*`
row*, implying flat pricing across the 5.6 window.

| Entry | `pricing.toml` | Live Copilot catalog | Verdict |
|---|---|---|---|
| `claude-opus-4.8` | `200_000` | `1_000_000` | stale — 5x under |
| `claude-opus-4.7` | `200_000` | `1_000_000` | stale — 5x under |
| `claude-sonnet-4.6` | `200_000` | `1_000_000` | stale — 5x under |
| `gpt-5.4` | `256_000` | `1_050_000` | **stale — not previously known** |
| `gpt-5-mini` | `256_000` | `264_000` | **stale — not previously known** |

The Claude figures agree with Anthropic's published docs, which state that *"Claude 4.6
and later models include the full 1M token context window at standard pricing"* — so
there is no provider-versus-Copilot divergence to reconcile for those. Haiku 4.5 has no
extended tier and is fixed at 200K.

This matters beyond bookkeeping: `context_window` feeds the context-utilisation gauge in
`ui/summary.py`, so today every Claude run and both GPT runs draw against a wrong window.

## 3. Other stale prices found

| Entry | `pricing.toml` | Current list price | Delta |
|---|---|---|---|
| `claude-opus-4.8` | 15.00 / 75.00 | **5.00 / 25.00** | **3x over-stated** |
| `claude-opus-4.7` | 15.00 / 75.00 | **5.00 / 25.00** | **3x over-stated** |
| `gpt-5.4` | 1.25 / 10.00 | **2.50 / 15.00** | ~2x under-stated |
| `claude-sonnet-4.6` | 3.00 / 15.00 | 3.00 / 15.00 | correct |
| `gpt-5-mini` | 0.25 / 2.00 | 0.25 / 2.00 | correct |

`gpt-5.4` at `1.25 / 10.00` is in fact the current `gpt-5` / `gpt-5.1` rate — a plausible
transcription slip. The file header's `# Pricing estimates as of 2026-05-16` needs bumping.

## 4. Schema inadequacy

The current three fields cannot express at least six real pricing axes, ranked by how
much each distorts a git-loopy run. This is input to [#287](https://github.com/bradcstevens/git-loopy/issues/287).

**(a) Cached input and cache writes — the largest silent error.** Both vendors price
three distinct input classes, and the Copilot CLI caches aggressively across a long agent
loop, so much of a real run's "input tokens" are cache reads billed at **10%**.
Anthropic: 5-minute cache write **1.25x** base input, 1-hour write **2x**, cache read
**0.1x** (Opus 5: `$5 / $6.25 / $10 / $0.50 / $25`). OpenAI GPT-5.6: cache write
**1.25x**, cache read retains the **90% discount** — *"Cache writes have no additional
fee on models before the GPT-5.6 family. For GPT-5.6 models and later model families,
cache writes cost 1.25x the uncached input token rate."* A flat `input_per_mtok`
over-states cost when caching hits and under-states it on write-heavy turns. Fixing it
needs `cached_input_per_mtok` / `cache_write_per_mtok` *and* the runner surfacing
`cached_tokens` / `cache_write_tokens` (OpenAI) or `cache_read_input_tokens` /
`cache_creation_input_tokens` (Anthropic) — **first check whether the Copilot SDK exposes
those at all**, which determines whether this axis is actionable.

**(b) Time-varying price — `claude-sonnet-5`.** Anthropic: *"Introductory pricing of
$2/$10 per million input/output tokens is in effect through August 31, 2026, after which
the standard pricing of $3/$15 will take effect."* A single scalar is **wrong on one side
of 2026-09-01 whichever value is chosen**, and Sonnet 5 carries three of the routing
table's six rows (implementations, tests, docs) — the highest-volume model in the design.
The schema has no effective-date.

**(c) Fast mode is a separate selectable Copilot model at 2x.** Anthropic prices Opus 5 /
Opus 4.8 fast mode at **$10 / $50**, and *"Fast mode pricing applies across the full
context window."* GitHub's supported-models table lists *"Claude Opus 4.8 (fast mode)
(preview)"* as its own roster entry. This is a keying problem as much as a schema one —
it needs its own `[models."..."]` entry whose id must come from `models.list()`.

**(d) Context-length price tiers.** OpenAI labels `gpt-5.4`, `gpt-5.5`, `gpt-5.5-pro`,
and `gpt-5.4-pro` *"(<272K context length)"*, so a distinct above-272K rate exists that a
flat schema cannot represent; `gpt-5.4` is already in the table. Anthropic explicitly has
**no** long-context tier on 4.6+, so Claude is safe on this axis.

**(e) Multiplicative modifiers.** Anthropic data residency (`inference_geo:"us"` on 4.6+)
applies **1.1x** to all token categories; OpenAI charges a **10% uplift** for regional
processing on models released on or after 2026-03-05; batch is -50% (Anthropic) or a
separate tier (OpenAI). None are expressible. All are arguably out-of-band for an
interactive CLI, but they mean the table is a point estimate under one deployment
configuration and should say so.

**(f) `context_window` is not single-valued under Copilot.** Sessions select a
`ContextTier` of `"default"` or `"long_context"`, and the routing table uses both. A
single int makes the utilisation gauge correct for at most one tier. Copilot also
auto-compacts at roughly 80% of the window, so usable context is materially below nominal.

### Two axes that are fine — verified, not assumed

- **Reasoning/thinking tokens need no schema change.** OpenAI: *"Reported output token
  usage includes all tokens generated by the model, not only the text visible... The
  Responses API reports this total as `output_tokens`."* Pro mode bills *"at the selected
  model's standard token rates"*, and `ultra`/multi-agent aggregates sub-agent tokens at
  standard rates. Anthropic bills extended/adaptive thinking as output. So
  `output_per_mtok` is already the correct rate — contrary to the natural assumption.
- **Tokenizer change is a comparability trap, not a schema gap.** *"Claude 4.7 and later
  models use a newer tokenizer... approximately 30% more tokens for the same text.
  Claude Sonnet 4.6 and earlier use the previous tokenizer."* Opus 4.7/4.8/5 and Sonnet 5
  therefore consume ~30% more tokens for identical input than Sonnet 4.6, so cost-per-task
  comparisons across that boundary mislead even with correct per-token prices. Worth a
  comment in `pricing.toml`.

## 5. Ready-to-paste TOML

Matches the existing file's formatting: two-decimal prices, underscore digit separators,
aligned `=`. Context windows are the **live Copilot catalog** values, which is the window
the utilisation gauge is actually drawn against.

```toml
# Pricing estimates as of 2026-07-24. PROVIDER LIST PRICES, not GitHub Copilot's
# premium-request billing. (rest of existing header unchanged)
#
# Claude 4.7+ and Sonnet 5 use a newer tokenizer that produces ~30% more tokens
# for the same text than Sonnet 4.6 and earlier — cost-per-task comparisons
# across that boundary mislead even with correct per-token rates.

# --- Anthropic ---
# https://platform.claude.com/docs/en/about-claude/pricing
# https://platform.claude.com/docs/en/about-claude/models/overview  (captured 2026-07-24)

[models."claude-opus-5"]
input_per_mtok  = 5.00
output_per_mtok = 25.00
context_window  = 1_000_000

# CORRECTED 2026-07-24: was 15.00/75.00 (Opus 4.1-era rates) and 200_000.
[models."claude-opus-4.8"]
input_per_mtok  = 5.00
output_per_mtok = 25.00
context_window  = 1_000_000

# CORRECTED 2026-07-24: was 15.00/75.00 (Opus 4.1-era rates) and 200_000.
[models."claude-opus-4.7"]
input_per_mtok  = 5.00
output_per_mtok = 25.00
context_window  = 1_000_000

# Anthropic introductory pricing of 2.00/10.00 runs through 2026-08-31; standard
# 3.00/15.00 takes effect 2026-09-01. The schema cannot express an effective
# date, so the durable standard rate is recorded here. See #287.
[models."claude-sonnet-5"]
input_per_mtok  = 3.00
output_per_mtok = 15.00
context_window  = 1_000_000

# CORRECTED 2026-07-24: context_window was 200_000; prices were already correct.
[models."claude-sonnet-4.6"]
input_per_mtok  = 3.00
output_per_mtok = 15.00
context_window  = 1_000_000

# Haiku 4.5 has no extended context tier; fixed at 200K.
[models."claude-haiku-4.5"]
input_per_mtok  = 1.00
output_per_mtok = 5.00
context_window  = 200_000

# --- OpenAI ---
# https://developers.openai.com/api/docs/pricing
# https://openai.com/index/gpt-5-6/  (captured 2026-07-24)
# Standard tier (non-batch, non-flex, non-priority). GPT-5.6 cache writes bill at
# 1.25x input and cache reads at 0.10x; neither is expressible in this schema.
# Context windows below are the live Copilot catalog values, not OpenAI's docs
# (which publish no fetchable token count for the 5.6 family).

[models."gpt-5.6-sol"]
input_per_mtok  = 5.00
output_per_mtok = 30.00
context_window  = 1_050_000

[models."gpt-5.6-terra"]
input_per_mtok  = 2.50
output_per_mtok = 15.00
context_window  = 1_050_000

[models."gpt-5.6-luna"]
input_per_mtok  = 1.00
output_per_mtok = 6.00
context_window  = 1_050_000

# CORRECTED 2026-07-24: was 1.25/10.00 (those are current gpt-5/gpt-5.1 rates)
# and context_window was 256_000. OpenAI lists gpt-5.4 with a "(<272K context
# length)" qualifier, implying an above-272K rate this schema cannot express.
[models."gpt-5.4"]
input_per_mtok  = 2.50
output_per_mtok = 15.00
context_window  = 1_050_000

# CORRECTED 2026-07-24: context_window was 256_000; prices verified unchanged.
[models."gpt-5-mini"]
input_per_mtok  = 0.25
output_per_mtok = 2.00
context_window  = 264_000
```

## 6. Sources

All first-party, captured 2026-07-24.

| # | Source | Used for |
|---|---|---|
| 1 | https://platform.claude.com/docs/en/about-claude/pricing | Claude prices; cache multipliers; fast mode $10/$50; batch -50%; long-context "no tier"; data residency 1.1x; Sonnet 5 intro pricing |
| 2 | https://platform.claude.com/docs/en/about-claude/models/overview | Claude context windows; tokenizer +30% note; model ids |
| 3 | https://developers.openai.com/api/docs/pricing | GPT-5.6 input/cached/cache-write/output; `(<272K context length)` labels; gpt-5.4 and gpt-5-mini rates |
| 4 | https://openai.com/index/gpt-5-6/ | Prose confirmation of $5/$30, $2.50/$15, $1/$6; cache write 1.25x, read -90% |
| 5 | https://developers.openai.com/api/docs/guides/latest-model | Cache-write 1.25x |
| 6 | https://developers.openai.com/api/docs/guides/prompt-caching.md | Cache-write semantics; `cache_write_tokens` / `cached_tokens` |
| 7 | https://developers.openai.com/api/docs/guides/token-counting.md | Reasoning tokens are inside `output_tokens` |
| 8 | https://docs.github.com/en/copilot/reference/ai-models/supported-models | Copilot roster incl. Opus 4.8 fast mode as a distinct model |
| 9 | Live `models.list()` via `github-copilot-sdk==1.0.5` | All `context_window` values in section 2 |

Repo files read: `git-loopy/python/git_loopy/pricing.toml`, `pricing.py:82-201`,
`interactive/models.py:115-141`, `git-loopy/conformance/model-roster.json`, `git-loopy/config.toml`.

## 7. Open follow-ups

- **Does the Copilot SDK expose cache-read / cache-write token counts** in its usage
  events? This decides whether schema finding (a) is actionable at all in #287.
- **Confirm the exact model id for Opus 4.8 fast mode** from `models.list()` if fast mode
  is ever routed to; it is a distinct roster entry at 2x price.
- `claude-fable-5` and `claude-mythos-5` are excluded by the routing table (org policy /
  not offered in Copilot) and were deliberately not priced.
