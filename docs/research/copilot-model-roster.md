# R1: Copilot Model Roster Research — Wayfinder #106

> **Branch:** `research/copilot-model-roster`  
> **Date:** 2026-07-19  
> **Role in map:** R1 for wayfinder map #104 (per-issue-type model & reasoning selection)  
> **Feeds:** D5 (#110) — assign default model+reasoning-effort per work kind

---

## 1. The Roster

Source of truth: `git-loopy/python/git_loopy/config.py:58–84` (`MODEL_REASONING_EFFORTS`).
Every model in that dict is cross-checked below against the official GitHub Copilot billing and
supported-models docs (as of 2026-07-19).

### 1.1 Billing model note

GitHub Copilot has migrated from **per-request premium multipliers** to **AI credits** (token-based
billing). **1 AI credit = $0.01 USD.** Per-token rates are billed in AI credits; the old
"multiplier" table (1×, 9×, 27×) is now *legacy* and only applies to some annual-plan Pro/Pro+
subscribers on the old request-based system. The kit reads a live `billing.multiplier` float from
the SDK's `list_models()` call (see `interactive/models.py:124`); that runtime value is the live
source of truth and cannot be observed without a live Copilot session.

For this table, **"cost signal"** uses the *official Copilot per-token output price* (output tokens
typically dominate), normalised to `gpt-5-mini` = 1.0×. Both the pricing.toml values and the
official Copilot prices are noted where they differ.

### 1.2 Full model table

> Pricing source: `https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing`  
> Extended capabilities source: `https://docs.github.com/en/copilot/reference/ai-models/supported-models`  
> Speed/category source: `https://docs.github.com/en/copilot/reference/ai-models/model-comparison`

**Abbreviations used:**
- Ctx: context window (D = default tier; X = extended/1M opt-in; pricing.toml column = kit's TOML value)
- RE: reasoning-effort values accepted by this model (from `MODEL_REASONING_EFFORTS`)
- Reasoning effort order (low→high): `none < minimal < low < medium < high < xhigh < max`

| Kit Model ID | Provider | Category | Copilot In/Out ($/MTok) | Cost Signal (out norm.) | Ctx (D / X) | RE levels (from kit) | Speed Tier |
|---|---|---|---|---|---|---|---|
| `auto` | GitHub routing | Special | varies (discount on paid plans) | variable | runtime | none (routing only) | — |
| `gpt-5-mini` | OpenAI | Lightweight | $0.25 / $2.00 | **1.0×** (baseline) | 256K / — | low, medium, high | **Fast** |
| `claude-haiku-4.5` | Anthropic | Versatile | $1.00 / $5.00 | **2.5×** | 200K / — | *none* | **Fast** |
| `mai-code-1-flash-picker` | Microsoft | Lightweight | $0.75 / $4.50 | **2.25×** | ~128K / — ⚠ | low, medium, high | **Fast** |
| `gpt-5.4-mini` | OpenAI | Lightweight | $0.75 / $4.50 | **2.25×** | ~256K / — ⚠ | none, low, medium, high, xhigh | **Fast** |
| `gpt-5.6-luna` | OpenAI | Lightweight | $1.00 / $6.00 (D≤200K), $2.00/$9.00 (>200K) | **3.0–4.5×** | ≤200K / 1M | none, low, medium, high, xhigh, max | **Fast** |
| `gemini-3.5-flash` | Google | Lightweight | $1.50 / $9.00 | **4.5×** | 1M | low, medium, high | **Fast** |
| `claude-sonnet-5` | Anthropic | Versatile | $2.00 / $10.00 (promo to 2026-08-31) | **5.0×** (promo) | 200K / 1M | low, medium, high, xhigh, max | **Balanced** |
| `gemini-3.1-pro-preview` | Google | Powerful | $2.00 / $12.00 (D≤200K), $4.00/$18.00 (>200K) | **6.0–9.0×** | ≤200K / 1M | low, medium, high | **Balanced/Deep** |
| `gpt-5.3-codex` | OpenAI | Powerful | $1.75 / $14.00 | **7.0×** | 1M (extended) | low, medium, high, xhigh | **Balanced (agentic)** |
| `gpt-5.4` | OpenAI | Versatile | $2.50 / $15.00 (D≤272K), $5.00/$22.50 (>272K) | **7.5–11.25×** | ≤272K / 1M | none, low, medium, high, xhigh | **Balanced** |
| `gpt-5.6-terra` | OpenAI | Versatile | $2.50 / $15.00 (D≤272K), $5.00/$22.50 (>272K) | **7.5–11.25×** | ≤272K / 1M | none, low, medium, high, xhigh, max | **Balanced** |
| `claude-sonnet-4.6` | Anthropic | Versatile | $3.00 / $15.00 | **7.5×** | 200K / 1M | low, medium, high, max | **Balanced** |
| `claude-sonnet-4.5` | Anthropic | Versatile | $3.00 / $15.00 | **7.5×** | 200K / — | *none* | **Balanced** |
| `claude-opus-4.6` | Anthropic | Powerful | $5.00 / $25.00 | **12.5×** | 200K / 1M | low, medium, high, max | **Deep** |
| `claude-opus-4.7` | Anthropic | Powerful | $5.00 / $25.00 | **12.5×** | 200K / 1M | low, medium, high, xhigh, max | **Deep** |
| `claude-opus-4.8` | Anthropic | Powerful | $5.00 / $25.00 | **12.5×** | 200K / 1M | low, medium, high, xhigh, max | **Deep** |
| `gpt-5.5` | OpenAI | Powerful | $5.00 / $30.00 (D≤272K), $10.00/$45.00 (>272K) | **15.0–22.5×** | ≤272K / 1M | none, low, medium, high, xhigh | **Deep** |
| `gpt-5.6-sol` | OpenAI | Powerful | $5.00 / $30.00 (D≤272K), $10.00/$45.00 (>272K) | **15.0–22.5×** | ≤272K / 1M | none, low, medium, high, xhigh, max | **Deep** |

**Pricing.toml discrepancy note:**  
The kit's `pricing.toml` (dated 2026-05-16) stores *Anthropic/OpenAI provider list prices*, **not** GitHub Copilot platform prices. Differences:

| Model | Kit toml (in/out) | Copilot docs (in/out) |
|---|---|---|
| `claude-opus-4.8` | $15.00 / $75.00 | $5.00 / $25.00 ← lower through Copilot |
| `claude-opus-4.7` | $15.00 / $75.00 | $5.00 / $25.00 |
| `claude-sonnet-4.6` | $3.00 / $15.00 | $3.00 / $15.00 ✓ matches |
| `gpt-5.4` | $1.25 / $10.00 | $2.50 / $15.00 ← higher through Copilot |
| `gpt-5-mini` | $0.25 / $2.00 | $0.25 / $2.00 ✓ matches |

Context windows in kit toml (200K for Anthropic, 256K for OpenAI models) are **base windows only**. All Claude Opus 4.6+, Claude Sonnet 4.6+, GPT-5.3+, GPT-5.4, GPT-5.5, GPT-5.6 variants, and Gemini 3.1 Pro support **1M token opt-in extended windows** in Copilot CLI (confirmed in the extended capabilities table).

---

## 2. Model Tiers

### Tier 1: Fast / Cheap

*Criteria: output cost ≤ 4.5× baseline ($9/MTok); "Lightweight" category in Copilot docs.*

| Model | Cost signal | Ctx | Reasoning | Best for |
|---|---|---|---|---|
| `gpt-5-mini` | 1.0× | 256K | low/med/high | Quick edits, issue-admin, high-volume chores |
| `gpt-5.4-mini` | 2.25× | ~256K ⚠ | none/low–xhigh | Agentic exploration, grep-heavy tasks |
| `mai-code-1-flash-picker` | 2.25× | ~128K ⚠ | low/med/high | Fast inline suggestions, simple completions |
| `claude-haiku-4.5` | 2.5× | 200K | none | Lightweight Q&A, syntax help |
| `gpt-5.6-luna` | 3.0–4.5× | 1M opt-in | none/low–max (full) | Repetitive tasks with full-effort option available |
| `gemini-3.5-flash` | 4.5× | 1M | low/med/high | Fast, large-context, cost-efficient |

### Tier 2: Balanced

*Criteria: output cost 5–8× baseline; "Versatile" category or specialist coding.*

| Model | Cost signal | Ctx | Reasoning | Best for |
|---|---|---|---|---|
| `claude-sonnet-5` | 5.0× (promo) | 1M opt-in | low/med/high/xhigh/max | Strong all-rounder; promo price expires 2026-08-31 |
| `gpt-5.3-codex` | 7.0× | 1M | low/med/high/xhigh | Agentic software dev; specialized for code agents |
| `gpt-5.4` | 7.5–11.25× | 1M opt-in | none/low–xhigh | Deep code reasoning; default-tier sufficient for most tasks |
| `gpt-5.6-terra` | 7.5–11.25× | 1M opt-in | none/low–max (full) | Best GPT-5.6 default; balanced interactive + agentic |
| `claude-sonnet-4.6` | 7.5× | 1M opt-in | low/med/high/max | Reliable Anthropic balanced choice; no xhigh |
| `claude-sonnet-4.5` | 7.5× | 200K | none | No reasoning; use haiku instead for lighter tasks |

### Tier 3: Deep / Expensive

*Criteria: output cost ≥ 12.5× baseline; "Powerful" category.*

| Model | Cost signal | Ctx | Reasoning | Best for |
|---|---|---|---|---|
| `claude-opus-4.6` | 12.5× | 1M opt-in | low/med/high/max | Deep reasoning (no xhigh) |
| `claude-opus-4.7` | 12.5× | 1M opt-in | low/med/high/xhigh/max | Anthropic flagship; complex problems |
| `claude-opus-4.8` | 12.5× | 1M opt-in | low/med/high/xhigh/max | **Kit default**; most powerful Anthropic model |
| `gemini-3.1-pro-preview` | 6.0–9.0× | 1M opt-in | low/med/high | Advanced reasoning; preview status |
| `gpt-5.5` | 15.0–22.5× | 1M opt-in | none/low–xhigh (no max) | Most powerful GPT; highest cost |
| `gpt-5.6-sol` | 15.0–22.5× | 1M opt-in | none/low–max (full) | Complex agentic/repo-wide work; highest GPT-5.6 tier |

---

## 3. Reasoning-effort coverage by model (summary)

Models with **no** configurable reasoning (empty set in `MODEL_REASONING_EFFORTS`):
- `auto`, `claude-sonnet-4.5`, `claude-haiku-4.5`

Models with **partial** coverage (no `xhigh` or `max`):
- `gpt-5.5` (none/low/med/high/xhigh — no max)
- `gpt-5.4` (none/low/med/high/xhigh — no max)
- `gpt-5.4-mini` (none/low/med/high/xhigh — no max)
- `gpt-5.3-codex` (low/med/high/xhigh — no none, no max)
- `claude-sonnet-4.6` (low/med/high/max — no xhigh)
- `claude-opus-4.6` (low/med/high/max — no xhigh)
- `gemini-3.1-pro-preview` (low/med/high — no none, xhigh, max)
- `gemini-3.5-flash` (low/med/high — no none, xhigh, max)
- `gpt-5-mini` (low/med/high — no none, xhigh, max)
- `mai-code-1-flash-picker` (low/med/high — no none, xhigh, max)

Models with **full** coverage (none through max):
- `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` (none/low/med/high/xhigh/max)
- `claude-opus-4.7`, `claude-opus-4.8`, `claude-sonnet-5` (low/med/high/xhigh/max — no none)

---

## 4. Implications for D5 (INPUT ONLY — not a decision)

> **This section inputs to D5 (#110). The D5 decision itself belongs to the map driver.**

### 4.1 Suggested candidate defaults per work kind

| Work kind | Candidate model | Effort | Rationale |
|---|---|---|---|
| **Planning / design** | `claude-opus-4.8` | `max` | Kit's own default; 1M opt-in for large context; broadest reasoning range; best for upfront architectural reasoning where cost is secondary to quality |
| **Planning / design** (alt) | `gpt-5.6-sol` | `high` or `xhigh` | GPT-5.6 Sol = highest OpenAI reasoning ceiling; 15× cost but full effort range including `max` |
| **Implementation** | `claude-sonnet-5` | `high` | Strong balanced choice; 5× cost (promo); 1M context; xhigh/max available; note promo expires 2026-08-31 |
| **Implementation** (alt) | `gpt-5.6-terra` | `high` | OpenAI balanced default; full effort range; 7.5× cost; good for interactive loops |
| **Docs / markdown** | `claude-sonnet-4.6` | `medium` | Solid writing quality; 7.5×; 1M opt-in; `max` available if needed; no `xhigh` is a minor gap |
| **Docs / markdown** (alt) | `gpt-5.6-terra` | `low` | Full effort range; faster/cheaper end of terra's range for lower-complexity writing |
| **Issue-admin / chore** | `gpt-5-mini` | `low` | Cheapest (1×); 256K context; suitable for labelling, summarising, routing |
| **Issue-admin / chore** (alt) | `gpt-5.6-luna` | `none` | Full effort range with `none` disabling reasoning; fast and cheap; 3× cost but richer capability ceiling if needed |

### 4.2 Structural observations for D5

1. **No universal free lunch**: The two cheapest options with reasoning (`gpt-5-mini`, `mai-code-1-flash-picker`) cap at `high` — no `xhigh`/`max`. If D5 wants per-task effort floors, it needs a model in tier 2+.
2. **The GPT-5.6 family has the widest effort range** (`none` through `max` for all three variants), making it the most controllable axis for D5's knobs.
3. **Claude Opus 4.8 is the kit's current default** (`_DEFAULT_MODEL = "claude-opus-4.8"`, `_DEFAULT_REASONING_EFFORT = "max"` in `cli.py:106-111`). Any D5 change needs to update those constants or override via config/env.
4. **Gemini models cap at `high`** — they have no `xhigh`/`max`, which matters if D5 wants to reserve top-effort for specific work kinds.
5. **`auto` is uncontrollable**: It picks the model dynamically and accepts no reasoning configuration; D5 cannot control it and should not assign it to work kinds that need deterministic cost or effort.
6. **Claude Sonnet 5 promo expires 2026-08-31**: After that, its price likely rises (Anthropic provider list is $3/$15; the current promo $2/$10 will sunset). D5 should plan for that scenario.

---

## 5. Sources

### Local files (kit source of truth)
- `git-loopy/python/git_loopy/config.py:58–84` — `MODEL_REASONING_EFFORTS` dict
- `git-loopy/python/git_loopy/config.py:101–112` — `REASONING_EFFORT_ORDER`, `REASONING_EFFORTS`
- `git-loopy/python/git_loopy/cli.py:106–111` — `_DEFAULT_MODEL`, `_DEFAULT_REASONING_EFFORT`
- `git-loopy/python/git_loopy/pricing.toml:1–45` — per-model pricing (5 models; provider list prices; dated 2026-05-16)
- `git-loopy/python/git_loopy/pricing.py:1–10` — explicit note that toml prices are provider list, not Copilot billing
- `git-loopy/python/git_loopy/interactive/models.py:86–146` — `to_model_choices()`, `ModelChoice` dataclass (billing.multiplier, context_window, supported_reasoning_efforts)
- `git-loopy/python/git_loopy/init.py:184–207` — `_static_choices()` fallback, `_model_label()` display format

### Official GitHub Copilot documentation
- <https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing> — per-token AI credits pricing for all models (fetched 2026-07-19)
- <https://docs.github.com/en/copilot/reference/ai-models/supported-models> — full model roster, extended capabilities (1M ctx, configurable reasoning) per model (fetched 2026-07-19)
- <https://docs.github.com/en/copilot/reference/ai-models/model-comparison> — task-based categorisation (General-purpose / Fast / Deep reasoning / Agentic) (fetched 2026-07-19)

### Supplementary sources
- <https://en.ittrip.xyz/ai/gpt-5-6-github-copilot> — GPT-5.6 Sol/Terra/Luna speed and use-case breakdown (cites GitHub Blog 2026-07-09 changelog)
- GitHub Blog changelog: GPT-5.6 Sol/Terra/Luna GA announcement (2026-07-09)
- Web search aggregation for context window sizes (GPT-5.5, Claude Opus 4.8 = 1M confirmed by multiple third-party sources)

---

## 6. Confidence / Gaps

| Attribute | Confidence | Gap / Note |
|---|---|---|
| Model IDs (kit's canonical list) | ✅ High | Directly from `config.py:MODEL_REASONING_EFFORTS` |
| Reasoning-effort capability sets | ✅ High | Directly from `config.py:MODEL_REASONING_EFFORTS` |
| Provider family | ✅ High | Confirmed via official Copilot supported-models docs |
| Copilot per-token pricing (current) | ✅ High | Fetched from official docs.github.com billing page 2026-07-19 |
| 1M token context window support | ✅ High | Confirmed in official extended-capabilities table for each listed model |
| Default context window tier | ⚠ Medium | Inferred from pricing-table thresholds (272K/200K); exact default window not stated per model |
| Speed/latency tier | ⚠ Medium | Based on Copilot docs category labels + GPT-5.6 comparison; no latency benchmarks cited |
| `mai-code-1-flash-picker` context | ⚠ Low | Not in pricing.toml; not in extended capabilities table; "-picker" suffix suggests routing alias; ~128K estimated |
| `gpt-5.4-mini` context | ⚠ Low | Not in pricing.toml; not in extended table; ~256K estimated (Lightweight OpenAI) |
| `billing.multiplier` runtime value | ❌ Unknown | Comes from live SDK `list_models()` call; not accessible without a live Copilot session |
| `auto` mode cost/model | ❌ Unknown | Discount applies per docs, exact magnitude not documented; model chosen dynamically |
| Claude Sonnet 5 post-promo price | ❌ Unknown | Promo $2/$10 expires 2026-08-31; post-promo rate not stated in docs |
| Reasoning effort depth differences | ❌ Unknown | The behavioral difference between e.g. `high` vs `xhigh` vs `max` is model-internal; not documented |
