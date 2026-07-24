# Overriding a Copilot subagent's model without owning its prompt

**Question** ([#284](https://github.com/bradcstevens/git-loopy/issues/284), map [#280](https://github.com/bradcstevens/git-loopy/issues/280)):
can a Copilot subagent's model be overridden without git-loopy forking and maintaining that
subagent's prompt?

**Captured** 2026-07-24 · **SDK** `github-copilot-sdk==1.0.5` (pins CLI 1.0.67) ·
**CLI bundle read** 1.0.75 · **Method** source and documentation, plus on-disk inspection.
No live session was run.

## Answer

The prompt-fork tax is **mandatory in form** — `prompt` is a required field, so a shadow agent
must carry prompt text. It is **largely avoidable in substance**: the Copilot CLI ships every
built-in subagent's full definition as plain YAML on disk, so the text can be re-emitted
verbatim rather than invented.

But the copy **cannot be faithful**, and more importantly the question turns out to be aimed at
the wrong target.

## The finding that actually decides #295

**GitHub already routes the high-frequency built-in subagents to cheap models.** Verified on
disk at `~/.copilot/pkg/darwin-arm64/1.0.75/definitions/`:

| Agent | shipped `model:` |
|---|---|
| `explore` | **`claude-haiku-4.5`** |
| `task` | **`claude-haiku-4.5`** |
| `research` | **`claude-sonnet-4.6`** |
| `code-review` | *absent — inherits parent* |
| `security-review` | *absent — inherits parent* |
| `rubber-duck` | *absent — inherits parent* |
| `rem-agent` | *absent — inherits parent* |
| `general-purpose` | *no definition file — not file-backed* |

The obvious win — "make `explore` fast and cheap" — **is already the shipped default**. Chasing
it buys nothing.

The *remaining* leverage is the inverse of the original framing: the three agents with **no**
shipped model are exactly the **quality-gate** agents — `code-review`, `security-review`,
`rubber-duck`. They silently inherit whatever the parent session runs on. So a `chore` issue
routed to Haiku would have its code review performed *by Haiku*.

That connects directly to the cross-vendor review rule in [#285](https://github.com/bradcstevens/git-loopy/issues/285):
the routing table keeps reviews on an OpenAI model so the reviewer's blind spots are independent
of the implementer's. Intra-session, that invariant is currently **violated by construction** —
`code-review` always inherits the implementer's own model, so the reviewer is not merely
same-vendor, it is the *same model*.

## 1. Built-in prompts are obtainable — verbatim, from disk

`<cli-pkg>/definitions/*.agent.yaml` ships full definitions: prompt, tools, and `model:`.
Example, `explore.agent.yaml`:

```yaml
name: explore
displayName: Explore Agent
model: claude-haiku-4.5
promptParts:
  includeAISafety: true
  includeToolInstructions: true
  includeParallelToolCalling: true
  includeCustomAgentInstructions: false
  includeEnvironmentContext: true
prompt: |
  You are an exploration agent. Answer the question as fast as possible, then stop.
```

**A shadow cannot be byte-equivalent.** Two gaps:

- **`promptParts` has no `CustomAgentConfig` counterpart** (full field list at
  `copilot/session.py:1062-1077`). Built-ins set `includeCustomAgentInstructions: false`; a
  custom agent cannot. So the prompt *scaffolding* differs even when the prompt body matches,
  and behaviour will diverge in ways that cannot be corrected.
- Prompts contain unresolved template placeholders (`{{grepToolName}}`, `{{globToolName}}`,
  `{{shellToolName}}`) that the runtime substitutes.

**Drift signal exists but is not a contract.** Definitions live under a version-pinned package
path and multiple versions coexist on disk (`1.0.72-0`, `1.0.74`, `1.0.74-1/-3/-4`, `1.0.75`),
so cross-version `diff` is a workable drift detector. But this is an **unpublished
implementation detail**, not an API — nothing versions or announces prompt changes.

Runtime discovery does **not** expose prompts: `AgentInfo` (`copilot/generated/rpc.py:15426-15468`)
carries `description`, `displayName`, `id`, `name`, `mcpServers`, `model`, `path`, `skills`,
`source`, `tools`, `userInvocable` — **no `prompt`**.

## 2. There is no alternative model-setting surface

- **`model_capabilities`** is not a routing surface. `ModelCapabilitiesOverride` is
  `{supports: {vision, reasoning_effort}, limits: {...}}` (`copilot/session.py:102-126`); its
  `reasoning_effort` is a **boolean support flag**, not a value, and there is no per-agent
  dimension.
- **`enable_config_discovery`** covers MCP servers and skill directories only
  (`client.py:1814-1820`) — agents are not mentioned.
- **`config_directory`** maps to `payload["configDir"]` (`client.py:2059-2060`); no agent semantics.
- **Frontmatter `model:`** *defines* agents, deduplicated by filename across
  user/project/org/enterprise levels. Built-ins are not part of that ladder, and the CLI docs
  state *"The CLI's default agents are not included in this list."*
- **[copilot-cli#1354](https://github.com/github/copilot-cli/issues/1354)** — *"Model routing,
  per-agent model selection, and global hooks support"* — is **open**, and requests exactly this,
  keyed on `explore`/`task`/`code-review`. Strong negative evidence.
- **Undocumented:** the runtime supports `includedBuiltinAgents`, an *allowlist*
  (`sdk/index.d.ts:25366-25367`). It is **not exposed by Python SDK 1.0.5**.

## 3. Exclusion plus a same-named custom agent does re-bind

`sdk/index.d.ts:25653-25658`, corroborated verbatim in `schemas/api.schema.json:4885, 26832,
28624` and `copilot/generated/rpc.py:20869-20873`:

> "Built-in subagents to exclude from this session. Excluded built-ins are **removed from
> task-tool discovery** and cannot be dispatched unless a custom agent with the same name is
> available."

Dispatch is **by name through the task tool's agent enumeration**, so there is no separate caller
binding to re-point. Excluding *without* shadowing does not error and does not fall back — the
name simply stops being offered to the model.

Policy inherits down the subagent chain: denylists **union**, allowlists **intersect**
(`sdk/index.d.ts:22000-22009`).

**Mechanical caveat.** `prompt` is emitted unconditionally via `.get()` (`client.py:3415`):

```python
wire_agent: dict[str, Any] = {"name": agent.get("name"), "prompt": agent.get("prompt")}
```

Every other field is conditional (`:3416-3429`). Omitting `prompt` therefore sends
`"prompt": null`, not an absent key — and `CustomAgentConfig` is `TypedDict, total=False`, so a
type checker will not catch it. Docs mark `prompt` required, so `null` is very likely rejected
server-side. **Untested.**

> A third-party web result claimed built-ins "cannot be shadowed" and that
> `excludedBuiltinAgents` is internal-only. Discounted: it contradicts four independent
> first-party artifacts that state the opposite in identical language.

## 4. Subagent model is fully observable

`copilot/generated/session_events.py`:

- `SubagentStartedData` `:6749-6755` — `agent_name`, `agent_display_name`, `agent_description`,
  `tool_call_id`, **`model: str | None`**
- `SubagentCompletedData` `:6612-6620` — plus **`model`**, `duration`, `total_tokens`, `total_tool_calls`
- `SubagentFailedData` `:6671-6680` — plus **`model`**, `error`, tokens, tool calls
- `SubagentSelectedData` `:6722-6726` — **no** model

The official docs' event table **omits `model` entirely** — the SDK schema is richer than
documented. This matters twice over: it unblocks per-subagent model visibility for
[#290](https://github.com/bradcstevens/git-loopy/issues/290), and — because `total_tokens` is
reported per subagent — it means subagent spend is **attributable**, which the cost accounting
would otherwise silently absorb into the parent.

## 5. Per-subagent reasoning effort

- [copilot-cli#2904](https://github.com/github/copilot-cli/issues/2904) — **open**
- [copilot-sdk#1131](https://github.com/github/copilot-sdk/issues/1131) — **open**

**But current docs list `reasoningEffort` as a `CustomAgentConfig` property** (*"Reasoning effort
to use while this agent runs. When omitted, no override is sent and the backend chooses its
default"*), naming `reasoning_effort` for Python. Pinned SDK **1.0.5 does not have it** — absent
from `session.py:1062-1077` and not forwarded by `client.py:3415-3430`. It evidently landed in a
newer SDK and the issues were never closed. **An SDK bump is likely all that is required.**

**Critically, effort is not inherited.** Docs: *"The parent session effort is not inherited, and
the SDK does not add a per-agent default."* So on SDK 1.0.5 a shadowed subagent runs at
**backend default** — shadowing `explore` in a session running at `xhigh` would silently drop
that subagent to the backend's default effort. This is a silent-failure mode, not a warning.

## 6. Empirical confirmation: not performed

Two claims most needing a live check:

1. Does a same-named custom agent actually receive dispatches that would have gone to the built-in?
2. Is `prompt: null` rejected, or accepted as "no agent prompt"?

## Corrections to the routing reference

The routing run (comment on #280) contains claims this evidence contradicts:

- It asserts the SDK exposes per-agent `reasoningEffort` — **false for pinned 1.0.5**.
- It recommends `research` → Opus 5 @ xhigh; the **shipped default is `claude-sonnet-4.6`**.
- It calls `rubber-duck`'s model "not user-settable"; it ships as a normal YAML definition with
  no `model:` (i.e. it inherits, and is settable by the same mechanism as any other).
- It says `security-review` is "not a documented built-in agent" — correct as to docs, but the
  CLI **does** ship `security-review.agent.yaml`.
- It recommends routing `explore`/`task` to Haiku — **already the shipped default**.

Also: official docs list **six** built-ins (explore, task, general-purpose, code-review,
research, rubber-duck). `security-review` is shipped but undocumented.

## Version skew

Findings mix **Python SDK 1.0.5** (pins `CLI_VERSION = "1.0.67"`, `copilot/_cli_version.py:17`)
with the **on-disk CLI 1.0.75** used for `definitions/`, `sdk/index.d.ts`, and
`schemas/api.schema.json`. The 1.0.75 runtime is newer than what SDK 1.0.5 spawns; definitions
content and `includedBuiltinAgents` availability may differ at 1.0.67.

## Recommendation for #295

**Do not shadow all seven.** The headline savings are already banked by GitHub, and shadowing
would mean maintaining seven prompt copies scraped from an unpublished version-pinned directory,
knowingly imperfect (`promptParts` is inexpressible), while silently dropping every shadowed
subagent to backend-default effort on SDK 1.0.5. Large permanent surface, small delta.

**The credible scope is narrow and inverted:** `code-review` and `security-review` — and possibly
`rubber-duck` — have **no shipped `model:`**, so they inherit the implementer's model and quietly
break the cross-vendor review invariant #285 exists to protect. Two prompt copies, not seven, and
the argument for them is a correctness argument rather than a cost one.

**Cheapest credible first step:** bump the SDK past 1.0.5 to restore per-agent `reasoningEffort`
(and possibly `includedBuiltinAgents`), then re-evaluate. Until that bump, any shadow silently
loses effort control.
