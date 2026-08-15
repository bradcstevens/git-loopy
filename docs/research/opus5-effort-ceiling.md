# Opus 5's reasoning-effort ceiling in the Copilot harness

**Question** ([#282](https://github.com/bradcstevens/git-loopy/issues/282), map [#280](https://github.com/bradcstevens/git-loopy/issues/280)):
what reasoning-effort values does `claude-opus-5` actually accept, and does `xhigh` take
effect or get silently downgraded?

**Captured** 2026-07-24 · **Harness** Copilot CLI 1.0.75, enterprise plan,
`api.enterprise.githubcopilot.com` · **SDK** `github-copilot-sdk==1.0.5`

> This file, alongside `routed-model-pricing.md`, establishes `docs/research/` as the home
> for research-ticket findings. The repo had no prior convention.

## Answer

`claude-opus-5` accepts **`low, medium, high, xhigh, max`**. `xhigh` **takes effect** — it
is transmitted to the provider unchanged, not downgraded. `max` is **accepted and
transmitted verbatim**, and sits one rung *above* `xhigh`.

The routing run's §B claim — *"Copilot's enum stops at `xhigh`, so `max` is unreachable from
this harness"* — is **wrong**. The live `list_models()` query and the kit's roster are
**correct**; `conformance/model-roster.json` and `config.py:MODEL_REASONING_EFFORTS` need no
change for `claude-opus-5`.

**The planning route `claude-opus-5 @ xhigh` is safe**, and `max` is available if wanted.

## But the roster *is* wrong — for Gemini

While verifying, a second and more consequential result appeared: **the SDK's `models.list()`
and the CLI's CAPI `/models` payload do not agree with each other.**

| model | CAPI `/models` (CLI) | SDK `models.list()` | kit roster |
|---|---|---|---|
| `claude-opus-5` | `low, medium, high, xhigh, max` | `low, medium, high, xhigh, max` | matches |
| `claude-sonnet-5` | `low, medium, high, xhigh, max` | `low, medium, high, xhigh, max` | matches |
| `claude-haiku-4.5` | *key absent* | `None` | matches (`frozenset()`) |
| `gpt-5.6-sol` | `none, low, medium, high, xhigh, max` | `none, low, medium, high, xhigh, max` | matches |
| **`gemini-3.5-flash`** | **`minimal, low, medium, high`** | `low, medium, high` | follows SDK — **missing `minimal`** |
| **`gemini-3.6-flash`** | **`minimal, low, medium, high`** | **`None`** | follows SDK — **says non-configurable; it is not** |

This **explains the "contradictory capability data" for `gemini-3.6-flash`** that the prior
handoff recorded and deferred. It is not a transient inconsistency: the two surfaces genuinely
report different capability data for the Gemini flash family, and git-loopy's roster was built
from the surface that is wrong.

The practical consequence: if routing ever targets a Gemini flash model, `gate_reasoning_effort`
would force `effort=None` for a model that in fact accepts four effort levels — silently
discarding the operator's choice.

**This makes "which surface is authoritative for the roster fixture?" a live question**, not a
theoretical one. It was question 3 of this ticket, and the answer is: *they differ, and the
divergence is real*. Feeds the roster-drift item in the map's **Not yet specified**.

Also worth recording:

- **`claude-sonnet-4.5` is absent from both surfaces** — confirmed retired upstream while still in the kit's roster.
- **`gpt-5.4-nano`** appears in CAPI (`none, low, medium, high, xhigh`) but not in the SDK list or the roster.
- **`claude-opus-5`'s default effort is `medium`**, per `models.list()`. The routing run states Opus 5 defaults to `high`; that is Anthropic's own API default, not Copilot's.

## Evidence 1 — the CAPI capability payload

The Copilot CLI logs the raw `/models` response at `logLevel: all`. Parsed directly from
`~/.copilot/logs/process-1784929484807-93197.log`, 39 models:

```
claude-opus-5      reasoning_effort=['low', 'medium', 'high', 'xhigh', 'max']
claude-sonnet-5    reasoning_effort=['low', 'medium', 'high', 'xhigh', 'max']
gpt-5.6-sol        reasoning_effort=['none', 'low', 'medium', 'high', 'xhigh', 'max']
gemini-3.5-flash   reasoning_effort=['minimal', 'low', 'medium', 'high']
gemini-3.6-flash   reasoning_effort=['minimal', 'low', 'medium', 'high']
claude-haiku-4.5   reasoning_effort=None          # key absent from `supports`
```

`max` is present for six model families and is ordered *above* `xhigh`. That alone refutes
the routing run's claim, independent of anything below.

Opus 5's full row also carries `max_context_window_tokens: 1000000`,
`long_context.max_prompt_tokens: 936000`, `adaptive_thinking: true`,
`max_thinking_budget: 32000`, `policy.state: "enabled"`.

## Evidence 2 — `xhigh` reaches the provider unchanged

`~/.copilot/logs/process-1784928463080-91116.log` (21:27–21:28Z):

- `:511` — `"model": "capi:claude-opus-5:defaultReasoningEffort=xhigh"`
- `:8581`, `:8891`, `:9170`, `:9472` — the outbound CAPI request body, four consecutive calls:
  ```json
  "thinking": { "type": "adaptive", "display": "summarized" },
  "output_config": { "effort": "xhigh" }
  ```
- HTTP 200, streamed, with non-zero `usage.completion_tokens_details.reasoning_tokens`.

Requested `xhigh` → sent `xhigh`. No `medium`, no substitution anywhere in the chain.

## Evidence 3 — `max` is accepted, not coerced

`~/.copilot/logs/process-1784929936884-79986.log` (22:16Z):

- `:618` — `"model": "capi:claude-opus-5:defaultReasoningEffort=max"`
- `:8687`, `:8999`, `:9252`, `:9544` — `"output_config": { "effort": "max" }`
- HTTP 200; `usage: { prompt_tokens: 38919, completion_tokens: 713, reasoning_tokens: 496 }`

No error, no coercion to `xhigh`, no fallback.

## Why copilot-cli #3823 does not apply

[github/copilot-cli#3823](https://github.com/github/copilot-cli/issues/3823) (opened
2026-06-16, still open) reports that `xhigh` silently downgrades to `medium`. Its precondition
is explicit: *the active model does **not** advertise `xhigh`*. Its own table lists Opus 4.6
and Sonnet 4.6 as lacking `xhigh`, and Opus 4.7/4.8 as having it.

**Opus 5 advertises `xhigh`, so the planning route is outside the bug's scope.** As a bonus,
#3823's own table independently contradicts "Copilot's enum stops at `xhigh`" — it lists `max`
for four models.

## Where the routing run's claim came from

Almost certainly the SDK's stale type stub, not the wire protocol:

- `copilot/session.py:162` — `ReasoningEffort = Literal["low", "medium", "high", "xhigh"]`.
  Omits `max`, `none`, and `minimal`.
- `copilot/client.py:1929-1930` — **no runtime validation**; the string is forwarded verbatim:
  ```python
  if reasoning_effort:
      payload["reasoningEffort"] = reasoning_effort
  ```
- `copilot/session.py:2841` — `set_model(..., reasoning_effort: str | None = None)` types the
  same value as a plain `str`. The SDK is internally inconsistent.

So `"max"` works at runtime and only trips a strict type checker. **The `ReasoningEffort`
Literal must not be treated as the enum** — it lags the service.

## `claude-haiku-4.5` — non-configurable, confirmed

Its CAPI `supports` block has no `reasoning_effort` key at all. The kit already handles this
correctly and documents why:

- `config.py:80` — `"claude-haiku-4.5": frozenset()`
- `config.py:256-258` — *"Reasoning-incapable model (empty effort set): force `effort` to `None`
  (the live CLI hard-rejects `session.create` otherwise)"*
- `conformance/effort-gate.json:21-36` — incapable-model cases assert `expected_effort: null`

So the routing table's `chore → claude-haiku-4.5` route needs no gate change. A live
`session.create` with an effort was **not** attempted — the repo's own comments record that it
hard-rejects, and re-deriving it would cost a call for no new information.

## Gaps and what is inferred rather than verified

1. **Server-side honouring is not client-observable.** Verified: the harness *transmits*
   `effort: "xhigh"` / `"max"` and CAPI returns 200 with reasoning tokens. **Not verified:**
   that the provider actually applies that budget. No response field echoes an effective
   effort. Closing this needs either a GitHub-side echo field or a controlled A/B — same
   prompt, n>=5 per rung, comparing `reasoning_tokens` distributions. Single-turn counts in
   these captures (0–680) are far too noisy to infer anything.
2. **The captures are from the CLI path, not the SDK path.** No log containing a JSON-RPC
   `session.create` payload from `copilot.CopilotClient()` was found. The inference that the
   SDK behaves identically rests on two facts: it forwards the string with no validation
   (`client.py:1929-1930`), and it spawns the same CLI binary (`_cli_version.py:17`,
   `~/.copilot/pkg/`). Strong, but one rung below the CLI evidence. The Gemini divergence above
   shows the two surfaces are **not** interchangeable for capability *reporting*, even if they
   share a transport.
3. **A readback channel does exist and is unused.** `copilot/generated/session_events.py:1436-1437,
   1461-1462` — `AssistantUsageData.reasoning_effort` and `.reasoning_tokens`, parsed from the
   `assistant.usage` event. This is a per-call field git-loopy could assert on, and is the
   natural way to make effort verifiable rather than assumed. Relevant to the visibility ticket (#290).
4. **Type-checker hazard, unresolved.** Passing `reasoning_effort="max"` to `create_session`
   violates the SDK's `Literal` even though it works. Whether git-loopy currently suppresses
   that was not checked.
5. **Docs were useless here, as expected.** GitHub's CLI programmatic reference documents
   `--model` but carries no effort table, and a web search returned an AI-synthesised table
   mixing Kiro and Claude Code documentation, which was discarded. Every conclusion above rests
   on the CAPI payload, wire captures, or SDK source.

## Sources

- `~/.copilot/logs/process-1784929484807-93197.log` — CAPI `/models` payload, 39 models (parsed)
- `~/.copilot/logs/process-1784928463080-91116.log:511,8581,8891,9170,9472` — `xhigh` wire capture
- `~/.copilot/logs/process-1784929936884-79986.log:618,8687,8999,9252,9544` — `max` wire capture
- Live `models.list()` via `github-copilot-sdk==1.0.5`
- `copilot/session.py:162,2841` · `copilot/client.py:1929-1930,730-732` · `copilot/generated/session_events.py:1436-1462`
- https://github.com/github/copilot-cli/issues/3823
- `git-loopy/python/git_loopy/config.py:75-128,256-258` · `git-loopy/conformance/model-roster.json` · `conformance/effort-gate.json:21-36`
