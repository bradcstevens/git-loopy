# Custom agents in the pinned Copilot SDK and the CLI it drives

**Question** ([#655](https://github.com/bradcstevens/git-loopy/issues/655), map
[#648](https://github.com/bradcstevens/git-loopy/issues/648)): what does the Copilot SDK
version git-loopy pins, and the Copilot CLI it drives, support for custom agents? An
**Agent profile** would build on that mechanism. The issue asks about four things:

- how a custom agent is defined: instructions, model and reasoning effort, tool allow and deny
  lists, and Skill exposure, and how that meets the closed-world Skill policy (ADR-0015,
  ADR-0025);
- how a custom agent is selected for a session, and whether a session can delegate to a named
  custom agent as a subagent, and on which model;
- how custom agents are discovered (repository, user, SDK-supplied), and whether they can be
  supplied programmatically without writing into the repository;
- which of this needs a newer SDK than the pin, and whether that version is on the corporate
  Python feed.

No decision is made here. This file is the evidence that "Decide the Agent profile set and how
skills attach to it" waits on.

**Captured** 2026-09-25 · **SDK** `github-copilot-sdk==1.0.14`
(`git-loopy/python/pyproject.toml:30`) · **CLI pinned by that SDK** `1.0.85`
(`copilot/_cli_version.py:17`, `CLI_VERSION: str | None = "1.0.85"`) · **Method** source,
on-disk bundle and documentation reading. No live Copilot session was run.

### Vocabulary (#648)

| #648 term | Copilot term used in this file |
|---|---|
| **Agent**: one live harness session | an SDK `CopilotSession`. A *subagent* is a child execution inside that session's event stream, not a separate `CopilotSession` (see Open questions) |
| **Agent profile**: a reusable definition (instructions, model route, tools, Skills) | a **custom agent**, either a `CustomAgentConfig` passed through the SDK or an `*.agent.md` file |

### Source abbreviations

- **`SDK:`** means `git-loopy/python/.venv/lib/python3.13/site-packages/copilot/`, the installed
  `github_copilot_sdk-1.0.14.dist-info`. `SDK:session.py` and `SDK:client.py` are
  **byte-identical** to upstream tag
  [`github/copilot-sdk@v1.0.14`](https://github.com/github/copilot-sdk/tree/v1.0.14)
  (commit `e60d9037353249ef16b349eb4012e8c1d113fda5`), `python/copilot/`. This was checked with
  `diff`. `generated/rpc.py` and `generated/session_events.py` were read from the install only.
- **`SDK105:`** means `git-loopy/python/.venv/lib/python3.12/site-packages/copilot/`, a stale
  `github_copilot_sdk-1.0.5` install left in the same venv. It is used only to date changes and
  to check the prior research.
- **`CLI85:`** means `~/Library/Caches/github-copilot-sdk/cli/1.0.85/prebuilds/darwin-arm64/`,
  the cached runtime bundle for the exact CLI version the pin spawns.
- **`SDKDOC:`** means upstream `github/copilot-sdk@v1.0.14:docs/features/<file>`.
- **`CLIREF`** means the GitHub Docs page
  [Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference),
  read 2026-09-25 through the `docs.github.com/api/article/body` endpoint. Line numbers refer to
  that article body.

## Bottom line

- **An SDK session can define custom agents in code, without writing any file.** Pass
  `create_session(custom_agents=[CustomAgentConfig, ...])` (`SDK:client.py:2299`,
  `:2752-2755`). The only fields sent over the wire are `name`, `prompt`, `display_name`,
  `description`, `tools`, `mcp_servers`, `infer`, `skills`, `model` and `reasoning_effort`
  (`SDK:session.py:1239-1257`, `SDK:client.py:4287-4316`). Any other key in the dict is dropped.
- **Model and reasoning effort are per-agent on the pin.** `reasoning_effort` first appears on
  `CustomAgentConfig` at SDK v1.0.8. It is present in 1.0.14 and absent in 1.0.5 and v1.0.7.
  When `reasoning_effort` is omitted, the runtime **inherits the parent's effort only if the
  subagent runs the same model as the parent** (`SDK:session.py:1255-1257`,
  `SDKDOC:custom-agents.md:262`). This corrects the prior research's statement that effort is
  never inherited.
- **Tools have a per-agent allowlist but no per-agent denylist.** `tools` is an allowlist:
  `None` or omitted means every session tool, and `[]` means no tools. Denying is only
  possible at session level (`available_tools` and `excluded_tools`) or for the main agent only
  (`default_agent={"excluded_tools": [...]}`), which hides tools from the main agent while
  sub-agents that list them keep them (`SDKDOC:custom-agents.md:744-923`).
- **Skills attach to an agent by eager preload.** `skills=[names]` injects the **full content**
  of each named Skill into that agent's context at start. Skills are opt-in, and sub-agents
  **do not inherit** the parent's preloaded Skills. Names resolve against session-level
  `skill_directories` (`SDKDOC:custom-agents.md:270-295`, `SDKDOC:skills.md:384-401`).
  git-loopy supplies an isolated root containing enabled filesystem Skills
  (`git-loopy/python/git_loopy/skill_exposure.py:40-75`), but bundled Skills and the
  interaction between `disabled_skills` and agent preload are not established by the
  SDK docs. A profile must be validated against the Run's effective Skill policy before
  its preloads can be treated as closed-world.
- **Selection works three ways on the pin.** A session can start with an agent selected
  (`create_session(agent="name")`, `SDK:client.py:2301`, `:2763-2765`). An agent can be
  switched mid-session with `session.rpc.agent.select/deselect/get_current/list/reload`
  (`SDK:generated/rpc.py:43506-43539`, experimental). The CLI equivalent is
  `copilot --agent NAME` (CLIREF:530).
- **A parent can delegate to a named custom agent as a subagent, and the subagent's model is
  controllable.** The task tool takes a model argument, and the runtime records where the model
  came from. Precedence is: explicit per-call value, then per-subagent settings override, then
  the agent definition's `model`, then the parent session (CLIREF:1217). This is reflected in
  the `SubagentTaskModelSource` and `SubagentModelSelectionSource` enums
  (`SDK:generated/session_events.py:13318-13345`). The observed model appears on
  `subagent.started` and `subagent.completed` events. A live, SDK-only per-agent override
  exists: `session.rpc.tools.update_subagent_settings`, which sets `model`, `effort_level`,
  `context_tier` and `model_policy` per agent type (`SDK:generated/rpc.py:37845-37862`,
  `:43994-43998`).
- **Some profile features exist only in file-based agents.** `models` (a priority list),
  `modelPolicy: required`, `include-custom-instructions`, `user-invocable` and
  `disable-model-invocation` are frontmatter-only. The Python `CustomAgentConfig` cannot express
  them (CLIREF:1202-1217; they are absent from `SDK:session.py:1239-1257`).
  `modelPolicy: required` can still be applied live, per session, through
  `SubagentSettingsEntry.model_policy`.
- **File discovery sits alongside SDK-supplied agents and is not closed-world by default.** The
  runtime discovers `.github/agents/` and `.claude/agents/` (walking up to the Git root),
  `~/.copilot/agents/`, plugin `agents/` directories, `--add-dir` roots, and remote org or
  enterprise agents (CLIREF:1219-1228; `AgentInfoSource` in `SDK:generated/rpc.py:541-549`).
  git-loopy creates sessions in the SDK's default `mode="copilot-cli"` (`SDK:client.py:826`)
  without passing `custom_agents`. The session is therefore very likely **open-world for
  agents**, the same way ADR-0015 found it open-world for Skills. This is inferred, not
  live-verified. `~/.copilot/agents/wayfinder.agent.md` exists on this host today.
- **Nothing in the question needs an SDK newer than 1.0.14.** 1.0.14 is the newest **stable**
  upstream tag (released 2026-09-16). The only newer upstream tags are `v1.0.15-preview.0`
  through `.3`. In those tags `CustomAgentConfig`, its wire conversion, `_mode.py` and
  `docs/features/custom-agents.md` are unchanged. The one agent-schema addition is
  `AgentInfo.reasoning_effort`, a read-side field. The corporate feed reports no release
  newer than the pin (see [Newer SDK and feed status](#newer-sdk-and-feed-status)).
- **The prior research ([`subagent-model-override.md`](subagent-model-override.md)) needs
  corrections.** It said there was no per-agent model surface, but
  `session.tools.updateSubagentSettings` existed even in 1.0.5. It said effort is never
  inherited, and that built-in prompts cannot be replaced without shadowing (1.0.14 adds
  `session.agent.setPrompt`). Its table of built-in models is stale for CLI 1.0.85. See
  [Corrections](#corrections-to-subagent-model-overridemd).

## Findings

### 1. Defining a custom agent

#### 1.1 The SDK-supplied definition (`CustomAgentConfig`)

`SDK:session.py:1239-1257` (identical at `v1.0.14` and `v1.0.15-preview.3`, where it sits at
`session.py:1262-1280`):

```python
class CustomAgentConfig(TypedDict, total=False):
    name: str                                   # Unique name of the custom agent
    display_name: NotRequired[str]
    description: NotRequired[str]
    tools: NotRequired[list[str] | None]        # List of tool names the agent can use
    prompt: str                                 # The prompt content for the agent
    mcp_servers: NotRequired[dict[str, MCPServerConfig]]
    infer: NotRequired[bool]                    # Whether agent is available for model inference
    # Skill names to preload into this agent's context at startup (opt-in; omit for none)
    skills: NotRequired[list[str]]
    # Model identifier (e.g. "claude-haiku-4.5"); runtime falls back to parent model if unavailable
    model: NotRequired[str]
    # Reasoning effort for this agent's model. When omitted, the runtime resolves
    # model configuration, then inherits the parent effort only for the same model.
    reasoning_effort: NotRequired[ReasoningEffort]
```

The wire conversion is at `SDK:client.py:4287-4316`. It builds
`{"name": agent.get("name"), "prompt": agent.get("prompt")}` unconditionally
(`:4299`), then copies `displayName`, `description`, `tools`, `mcpServers`, `infer`, `skills`,
`model` and `reasoningEffort` only when those keys are present (`:4300-4315`). Two
consequences:

- **A key outside those ten never reaches the runtime.** That includes a raw dict carrying
  `models`, `modelPolicy` or `include-custom-instructions`. The converter only copies named
  keys.
- **A missing `prompt` is sent as `"prompt": null`.** The TypedDict is `total=False`, so a type
  checker will not catch it. The prior research noted this for 1.0.5 and it still holds on
  1.0.14. The upstream doc marks `name` and `prompt` as required
  (`SDKDOC:custom-agents.md:246-257`).

Upstream semantics of each property (`SDKDOC:custom-agents.md:244-262`):

| Property | Meaning (upstream doc) |
|---|---|
| `name` | Unique identifier. **Required.** |
| `prompt` | System prompt for the agent. **Required.** These are the Agent profile's *instructions*. |
| `description` | "What the agent does. Helps the runtime select it." |
| `tools` | "Tool names the agent can use. `null` or omitted = all tools." |
| `mcpServers` | Per-agent MCP servers (`:925-945`). |
| `infer` | "Whether the runtime can auto-select this agent (default: `true`)". `infer: false` means the agent is "only invoked through explicit user requests" (`:423-435`). |
| `skills` | "Skill names to preload into the agent's context at startup." |
| `model` | "Model identifier to use while this agent runs." The code comment adds that the runtime falls back to the parent model if the named model is unavailable (`SDK:session.py:1252-1253`). |
| `reasoningEffort` | "When omitted, the SDK sends no per-agent override and the runtime resolves the effort from its own precedence: a per-call client option, the resolved model's default, or the agent definition all take priority; otherwise the runtime inherits the parent session's effort only when the subagent runs the same model as the parent. When the subagent resolves to a different model, it falls back to that model's default" (`:262`). |

**Where a selected agent's prompt goes.** The option
`SessionUpdateOptionsParams.suppress_custom_agent_prompt` is documented as: "When true, the
selected custom agent's prompt is not injected into the user message (skill context is still
injected)" (`SDK:generated/rpc.py:34407-34411`). This implies that for a *selected* (main-turn)
custom agent, the runtime injects the agent prompt into the user message rather than replacing
the system prompt. For a *subagent*, the agent runs "with its own prompt" in an isolated context
(`SDKDOC:custom-agents.md:413-421`). This is inferred from those descriptions and was not
observed on the wire.

#### 1.2 The file-based definition (`*.agent.md`)

CLIREF "Custom agent frontmatter fields" (CLIREF:1202-1217) lists `description` (required),
`include-custom-instructions`, `infer`, `mcp-servers`, `model`, `models`, `modelPolicy`, `name`,
`reasoningEffort` and `tools`. The prompt is the Markdown body. It can be at most 30,000
characters, according to the GitHub.com reference
([Custom agents configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration)).
That reference also defines `disable-model-invocation`, `user-invocable` (and says `infer` is
**Retired**), `target` and `metadata`. `AgentInfo` exposes `disable_model_invocation`,
`user_invocable`, `model_policy` and `models` (`SDK:generated/rpc.py:24861-24896`), so the
runtime models all of them. **CLIREF lists no `skills` frontmatter key**, although `AgentInfo`
carries `skills` (`:24887`) and the SDK config does too. Whether file-based agents can declare
`skills:` is not documented.

**The SDK and file surfaces are not equivalent.** Only file-based agents can express these
fields:

| Field | File frontmatter | `CustomAgentConfig` 1.0.14 |
|---|---|---|
| `models` (priority list) | yes (CLIREF:1212) | **no** |
| `modelPolicy: preferred\|required` | yes (CLIREF:1213) | **no** (can only be set live through `SubagentSettingsEntry.model_policy`; see §2.3) |
| `include-custom-instructions` | yes (CLIREF:1206) | **no** |
| `user-invocable` / `disable-model-invocation` | yes (GitHub.com reference) | **no** (`infer` is the only control) |

The built-in agents in `CLI85:definitions/*.agent.yaml` also carry `promptParts` (for example
`includeCustomAgentInstructions: false`; see `CLI85:definitions/rubber-duck.agent.yaml:12-17`).
Neither surface can author this field.

#### 1.3 Tool allow and deny lists

- **Per-agent `tools` is allowlist-only.** `None`/omitted means "the agent inherits access to
  all tools configured on the session", and an explicit list enforces least privilege
  (`SDKDOC:custom-agents.md:744-774`). `AgentInfo.tools` is documented as "Empty array means
  none; omitted means inherit defaults" (`SDK:generated/rpc.py:24893-24894`). In the file form
  a `*` anywhere in the list grants every tool (CLIREF:1216). The GitHub.com reference adds
  that unrecognised tool names are ignored, and that aliases such as `read`, `edit`, `search`,
  `execute`, `agent` and `web` are accepted.
- **No per-agent denylist exists** in `CustomAgentConfig` or in the frontmatter table.
- **Session-level filters apply to every agent.** `available_tools` is an allowlist over "the
  full merged tool catalog including built-in tools, MCP tools, and custom tools". It
  "Takes precedence over `excluded_tools`". `excluded_tools` "Applies to all tools… Ignored if
  `available_tools` is set" (`SDK:client.py:2370-2378`). The precedence can be flipped live
  through `session.options.update` `tool_filter_precedence: "available"|"excluded"`
  (`SDK:generated/rpc.py:7598-7603`, `:34412-34414`).
- **The main agent has its own denylist.** `default_agent={"excluded_tools": [...]}`
  (`SDK:session.py:1260-1270`, `SDK:client.py:4318-4333`) hides tools from the main agent while
  they "Remain available to any custom sub-agent that includes them in its `tools` array".
  Session-level exclusion wins over it (`SDKDOC:custom-agents.md:900-923`). This is the
  documented way to force an orchestrator to delegate.
- **Subagent inheritance.** The prior research cites `sdk/index.d.ts` from a CLI 1.0.75 bundle:
  "denylists union, allowlists intersect". The CLI 1.0.85 bundle ships no `sdk/index.d.ts`
  (`CLI85:` contains only `copilot-runtime`, `runtime.node`, `definitions/`, `schemas/` and
  assets), so this was **not re-verified** on the pinned runtime.

#### 1.4 Skill exposure and the closed-world policy (ADR-0015, ADR-0025)

What the SDK offers:

- **Per-agent preload.** "When specified, the **full content** of each listed skill is eagerly
  injected into the agent's context at startup—the agent doesn't need to invoke a skill tool
  … Skills are **opt-in**: agents receive no skills by default, and sub-agents do not inherit
  skills from the parent. Skill names are resolved from the session-level `skillDirectories`"
  (`SDKDOC:custom-agents.md:270-295`; the same wording appears at `SDKDOC:skills.md:384-401`).
- **Session exposure.** `enable_skills`, `skill_directories`, `disabled_skills`
  (`SDK:client.py:2456-2460`, payload at `:2786-2803`) and the runtime-only
  `includedBuiltinSkills` allowlist (`CLI85:schemas/api.schema.json`
  `SessionOpenOptions.includedBuiltinSkills`: "When specified, only these runtime-bundled
  skills are available"). `_mode.py` sends `includedBuiltinSkills=[]` and `installedPlugins=[]`
  only in `mode="empty"` (`SDK:_mode.py:316-326`; `SDKDOC:skills.md:354-368`).
- git-loopy today calls `create_session(enable_skills=True, skill_directories=…,
  disabled_skills=…)` from its `SkillExposure`
  (`git-loopy/python/git_loopy/session.py:720-742`). This is ADR-0015's closed-world
  allowlist, carried as a disabled-list.

How this meets the closed-world policy, fact by fact:

1. **Filesystem Skills are isolated, but do not infer a complete gate from that alone.**
   Agent preload names resolve from session `skill_directories` (upstream doc).
   `build_skill_exposure` copies only policy-enabled filesystem winners into a temporary
   SDK Skill root (`git-loopy/python/git_loopy/skill_exposure.py:40-75`); enabled built-in
   Skills have no copied path (`:63-69`). The installed catalog is git-loopy's own Skill source
   (ADR-0025), but this evidence does not prove how bundled or host-provided Skill names
   interact with agent preloads.
2. **Unverified: does `disabled_skills` also filter an agent's preload list?** Neither the SDK
   docs nor the schema say. A profile that names a Skill the policy disables could therefore
   either load it (bypassing ADR-0015) or silently drop it. This needs a live probe (see Open
   questions).
3. **The preload path skips the `skill` tool.** "The agent doesn't need to invoke a skill
   tool", so any enforcement git-loopy does at `skill`-tool permission time
   (`build_permission_handler(deny_skills=…, skill_policy=…)`,
   `git-loopy/python/git_loopy/session.py:702-718`) **would not see preloaded Skills**. This is
   inferred from the upstream wording.
4. **Sub-agents inherit no preloaded Skill content.** Every profile that needs eager Skill
   content must list its Skills explicitly. The absence of a preload does not establish
   whether the subagent can invoke an available Skill through the session's Skill tool.
   For a closed-world profile, validate every listed name against the frozen effective
   policy at preflight, in addition to the existing isolated root and permission gate;
   this is an integration requirement, not a behavior already implemented.

### 2. Selecting an agent, delegating to one, and the subagent's model

#### 2.1 Selecting an agent for the parent session

- **At creation:** `create_session(agent="name")`. The value is documented as "Name of the
  custom agent to pre-select at session creation. Must match a `name` in `customAgents`". This
  "is equivalent to calling `session.rpc.agent.select()` after creation, but … ensures the
  agent is active from the very first prompt" (`SDKDOC:custom-agents.md:264-268`, `:297-301`;
  code at `SDK:client.py:2301`, `:2763-2765`). `resume_session` accepts the same parameter
  (`SDK:client.py:3088`, `:3546`). The feature was added in SDK v0.2.0 (upstream `CHANGELOG.md`
  "Pre-select a custom agent at session creation", #722).
- **Mid-session** (experimental `AgentApi`, `SDK:generated/rpc.py:43506-43539`):
  `list(include_built_in_agents?, include_prompt?)`, `select(name)` ("Selects a custom agent
  for subsequent turns"), `deselect()`, `get_current()`, and `reload()` ("reloading
  definitions from disk"). Selection emits `subagent.selected` (`agentName`,
  `agentDisplayName`, `tools`; `SDK:generated/session_events.py:10377-10381`).
- **Prompt override without shadowing:** `session.rpc.agent.set_prompt({id, prompt})`. It sets
  "an in-memory authored prompt override for an available agent. For built-in agents, this
  replaces only the static base prompt while preserving runtime-owned dynamic prompt
  composition … The special `general-purpose` agent is not overrideable. Overrides are not
  persisted" (`SDK:generated/rpc.py:43517-43521`, request type at `:22336-22343`). It is
  **absent in 1.0.5** (0 matches in `SDK105:generated/rpc.py`).
- **CLI:** `copilot --agent <agent>` ("Specify a custom agent to use") appears in the installed
  CLI `1.0.89-4` `--help` and in CLIREF:530. The value is the file name without `.agent.md`
  ([Creating and using custom agents for Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)).
  A session agent, whether the default or one chosen with `--agent`, receives repository
  instructions. A custom agent spawned as a *subagent* receives them only when its file sets
  `include-custom-instructions: true` (CLIREF:1230-1250).

#### 2.2 Delegating to a named custom agent as a subagent

- **Inference.** "The runtime analyzes the user's prompt against each agent's `name` and
  `description`". If `infer` is not `false` it selects the agent, runs it "with its own prompt
  and restricted tool set" in an isolated context, streams `subagent.*` events, and folds the
  result back (`SDKDOC:custom-agents.md:413-421`).
- **By name.** The CLI dispatches through the `task` tool ("Run subagents", CLIREF:688-695).
  The [how-to](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)
  documents explicit instruction ("Use the security-auditor agent on …") as a supported way to
  invoke one. `SubagentTaskModelSource.CUSTOM_AGENT_DEFINITION`, documented as "The task
  omitted a model and the user-defined custom agent's definition supplied one"
  (`SDK:generated/session_events.py:13336-13345`), shows that task-tool dispatch of a
  user-defined custom agent is a first-class path in the pinned schema.
- **Host-initiated.** `session.rpc.tasks.start_agent(TasksStartAgentRequest(agent_type, name,
  prompt, description?, model?))` "Starts a background agent task in the session"
  (`SDK:generated/rpc.py:14122-14139`, `:43548-43552`; experimental). The `agent_type`
  docstring gives only built-in examples ("'explore', 'task', 'general-purpose'"). **Whether a
  custom agent name is accepted is not documented.** CLIREF:1217 and :1232 mention an SDK
  `session.startSubagent`, but it has **0 matches** in the 1.0.14 SDK and in
  `v1.0.15-preview.3` `python/copilot/generated/rpc.py`. That doc sentence describes a
  surface the Python pin does not have.
- **Limits.** Default depth is 6 and default concurrency depends on the plan (2 to 32).
  `subagents.maxDepth` and `maxConcurrency` are honoured only for usage-based billing
  (CLIREF:1308-1342; mirrored in `SubagentSettings.max_depth` and `max_concurrency`,
  `SDK:generated/rpc.py:37890-37905`).
- **Disable or narrow built-ins.** `create_session(excluded_builtin_agents=[…])`: "Excluded
  built-in agents are hidden from discovery and cannot be selected or invoked unless a custom
  agent with the same name is configured" (`SDK:client.py:2416-2419`). The **allowlist**
  `includedBuiltinAgents` ("only these built-ins are available … Custom agents with the same
  name remain available") exists on the wire. On 1.0.14 it is reachable only through
  `session.rpc.options.update(SessionUpdateOptionsParams(included_builtin_agents=…))`
  (`SDK:generated/rpc.py:34313-34317`), not as a `create_session` keyword. It is absent from
  1.0.5.

#### 2.3 Which model a subagent runs on

The documented precedence, highest first (CLIREF:1217): "an explicit per-call value, the
`subagents` override in `~/.copilot/settings.json`, the agent definition's
`model`/`models`/`reasoningEffort` field, then the parent session's value. A declared model or
effort that can't be honored falls back to the session's value instead of failing the
dispatch—unless the agent declares `modelPolicy: "required"`, in which case dispatch is
refused". Two further documented notes:

- When the session model is `Auto`, "subagents always inherit the resolved session model
  regardless of this field" (CLIREF:1211).
- `SubagentSettingsEntry.model_policy`, which also appears in the pinned schema, uses
  `AgentModelPolicy`: `PREFERRED` means "advisory preferences that callers may override", and
  `REQUIRED` means "Require subagent execution to use one of the authored models"
  (`SDK:generated/session_events.py:12675-12680`).

Pinned-SDK control points:

| Control | Scope | Source |
|---|---|---|
| `CustomAgentConfig.model` / `reasoning_effort` | per agent definition | `SDK:session.py:1252-1257` |
| task-tool `model` argument (the parent LLM chooses) | per call | `SubagentTaskModelSource.TASK_ARGUMENT`, `SubagentModelSelectionSource.EXPLICIT_OVERRIDE` (`SDK:generated/session_events.py:13318-13345`) |
| `session.rpc.tools.update_subagent_settings(UpdateSubagentSettingsRequest(subagents=SubagentSettings(agents={agent_type: SubagentSettingsEntry(model, effort_level, context_tier, model_policy, auto_invoke)}, disabled_subagents=[…])))` | live session override, keyed by agent type; "takes precedence over persisted user settings until cleared" | `SDK:generated/rpc.py:37845-37905`, `:43994-43998` |
| `TasksStartAgentRequest.model` | host-started background task | `SDK:generated/rpc.py:14136-14137` |

The resolved model is **observable**:

- `subagent.started` carries `model`, `model_selection_source`, `task_model_source` and
  `agent_type` (`SDK:generated/session_events.py:10404-10417`).
- `subagent.configured` carries the resolved `model`, `reasoning_effort` and `context_tier`
  (`:10246-10251`).
- `subagent.completed` adds `configured_model_preference`, `configured_model_matches_actual`,
  `explicit_model_override`, `first_dispatched_model` and `model_override_reason`
  (`:10159-10176`).

This lets a Run tell a silent fallback apart from an honoured profile model.

### 3. Discovery and programmatic supply

- **SDK-supplied agents** are programmatic and write nothing. `custom_agents=[…]` is sent on
  `session.create` and `session.resume` (`SDK:client.py:2752-2755`, `:3534-3537`). The upstream
  doc's only definition example is this in-memory form (`SDKDOC:custom-agents.md:26-90`).
  `session.agent.set_prompt` and `update_subagent_settings` are in-memory and must be
  re-applied on resume.
- **File-based agents** (CLIREF:1219-1228):
  - *Project:* `.github/agents/` or `.claude/agents/`. The CLI "walks upward from your current
    working directory to the Git root … the deepest directory taking highest priority", and
    `.github/agents/` beats `.claude/agents/`.
  - *User:* `~/.copilot/agents/`.
  - *Plugin:* `<plugin>/agents/`.
  - *Added root:* `.github/agents/` under `--add-dir` or the SDK's `additionalDirectories`,
    loaded "as trusted configuration".
  - Org and enterprise agents live in `/agents/` of the org's `.github`/`.github-private`
    repository
    ([About custom agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents)).
  - The runtime's source enum is `builtin`, `inherited`, `plugin`, `project`, `remote`, `user`
    (`SDK:generated/rpc.py:541-549`).
- **Precedence between tiers is contradictory in GitHub's own docs.** CLIREF:1228 says
  "User-level agents have lower priority than project-level agents. Plugin agents have the
  lowest priority." The CLI how-to says "If you have custom agents with the same name in both
  locations, the one in your home directory will be used, rather than the one in the
  repository." The GitHub.com reference says the lowest level (the repository) overrides org,
  which overrides enterprise, deduplicated by filename. **Unresolved on the pinned 1.0.85.**
- **Discovery without a session.** The server-level experimental `client.rpc.agents.discover(
  AgentsDiscoverRequest(project_paths?, exclude_host_agents?))` "Discovers custom agents across
  user, project, plugin, and remote sources". `exclude_host_agents=True` "omit[s] the host's
  agents (the user-level agent directory and all plugin agents)". `get_discovery_paths` returns
  the canonical creation directories (`SDK:generated/rpc.py:663-715`, `:42703-42715`).
  `session.agent.list(include_built_in_agents?, include_prompt?)` lists a session's effective
  agents, with `path` set only for file-based agents (`:24879-24882`, `:30592-30607`).
- **Can file discovery be switched off per session? Not in 1.0.14 on any surface found.**
  - `exclude_host_agents` exists only on the server-level `agents.discover` and
    `getDiscoveryPaths` calls, not on `create_session` or `SessionOpenOptions`
    (key list in `CLI85:schemas/api.schema.json` `definitions/SessionOpenOptions`).
  - `enable_config_discovery` is now documented only as "Enables runtime discovery of supported
    configuration" (`SDK:client.py:2446-2448`). The 1.0.5 wording, which named only MCP configs
    and skill directories (`SDK105:client.py:1814-1815`), is gone, so its reach over agents is
    **unspecified** on 1.0.14.
  - `custom_agents_local_only` is described only as "Whether custom agents default to
    local-only execution" (`CLI85:schemas/api.schema.json`
    `SessionOpenOptions.customAgentsLocalOnly`). It defaults to `True` only in `mode="empty"`
    (`SDK:_mode.py:254-259`). Whether it suppresses `remote` (org or enterprise) agents is not
    stated.
  - `mode="empty"` also sends `installedPlugins=[]` (`SDK:_mode.py:323`), which removes the
    *plugin* tier by construction.
  - `config_directory` (sent as `configDir`, `SDK:client.py:2767-2769`) would relocate the
    user-level `~/.copilot/` tier. The docs do not state this for agents; it is an inference.
- **Consequence for git-loopy today.** git-loopy passes neither `custom_agents` nor
  `excluded_builtin_agents` and uses the default `mode="copilot-cli"`
  (`git-loopy/python/git_loopy/session.py:730-742`, `SDK:client.py:826`). Every project, user,
  plugin and remote agent the runtime discovers is therefore presumably offered to the Run's
  model for inference. On this host that includes `~/.copilot/agents/wayfinder.agent.md`. The
  repository itself has no `.github/agents/` or `.claude/agents/`. This is the agent analogue
  of the Skill leak ADR-0015 closed. It is inferred from the docs and the schema, not observed
  (see Open questions).

### 4. The built-in agents on the pinned CLI 1.0.85

The shipped definitions `CLI85:definitions/*.agent.yaml` are `code-review`, `explore`,
`rem-agent`, `research`, `rubber-duck`, `security-review` and `task`, plus a `sidekick/`
directory. Their authored model fields:

| Agent | 1.0.85 file | installed CLI 1.0.89-4 file | CLIREF built-in table (live docs) |
|---|---|---|---|
| `explore` | `model: gpt-5.4-mini`, `reasoningEffort: low` (`explore.agent.yaml:7-8`) | `model: [gpt-5.6-luna, gpt-5.4-mini]`, `reasoningEffort: low` | gpt-5.4-mini |
| `task` | `model: claude-haiku-4.5` (`task.agent.yaml:7`) | `model:` (list) | gpt-5.6-luna → gpt-5.4-mini → claude-haiku-4.5 |
| `research` | `model: claude-sonnet-4.6` (`research.agent.yaml:7`) | `model: claude-sonnet-5` | claude-haiku-4.5 |
| `code-review`, `security-review`, `rem-agent` | no `model:` | no `model:` | code-review and security-review: claude-sonnet-4.6 |
| `rubber-duck` | no `model:` ("selected dynamically at runtime", `rubber-duck.agent.yaml:9`) | no `model:` | "complementary model" |

The live docs describe neither pinned file set exactly. The docs' built-in table may describe
*runtime-resolved* defaults rather than authored ones. Read built-in model claims per CLI
version.

## Newer SDK and feed status

**Upstream facts, from GitHub:**

- **Stable:** `v1.0.14` is the latest non-prerelease, published 2026-09-16T05:17:39Z
  (`gh api repos/github/copilot-sdk/releases`). The pin is already on it.
- **Prerelease:** `v1.0.15-preview.0` through `.3` (2026-09-21 to 2026-09-24). Relative to
  1.0.14 for this question:
  - `python/copilot/session.py` `CustomAgentConfig` is unchanged (`:1262-1280`), and so is
    `_convert_custom_agent_to_wire_format` (checked with `diff`).
  - `python/copilot/_mode.py` and `docs/features/custom-agents.md` are byte-identical.
  - `AgentInfo`, `TasksStartAgentRequest`, `SubagentSettingsEntry` and `AgentsDiscoverRequest`
    are unchanged, except that `AgentInfo` gains `reasoning_effort` ("Authored reasoning effort
    for this agent. Applied on selection to models that support it"; `generated/rpc.py:27147`
    +47).
  - It adds `session.customizations.reload` ("Reloads all repository and user customizations
    … custom agents, extensions, and skills"), `session.workflow.agent` ("Runs one
    dynamic-workflow-scoped subagent") and plugin, marketplace and connector RPCs. None of them
    adds a per-session "no file agents" switch or new `CustomAgentConfig` fields.
  - The CLI version a preview wheel pins cannot be read from source, because `CLI_VERSION` is
    injected at publish. It was not read from the wheel either (no package download was made).
- **Feature dating** (upstream tags, `python/copilot/session.py`):
  - `CustomAgentConfig.reasoning_effort` is absent at `v1.0.7` and present from `v1.0.8`.
  - `CustomAgentConfig.skills` and `model` already exist in 1.0.5
    (`SDK105:session.py:1062-1077`).
  - `includedBuiltinAgents`, `session.agent.setPrompt`, `AgentInfo.prompt`, `models` and
    `modelPolicy` are absent in 1.0.5 and present in 1.0.14.

**Corporate Python feed (`python -m git_loopy.sdk_feed`):**

Ran `uv run --no-sync --project git-loopy/python python -m git_loopy.sdk_feed` on
2026-09-25; output verbatim:

```text
no finding: the corporate feed carries nothing newer than github-copilot-sdk==1.0.14
```

The check reads the configured Microsoft simple index and compares every listed release
against the manifest pin (`git-loopy/python/git_loopy/sdk_feed.py:20-21,60-74,83-87,
141-176`). Its output establishes no **listed** newer release, stable or prerelease;
it does not distinguish an absent release from one the feed declined to list. The pin
is installed locally, but the check is not a test of whether a fresh installation
would be authorized. No package or metadata was requested from public registries.

## Corrections to `subagent-model-override.md`

That document was captured against SDK 1.0.5, CLI 1.0.67 (pinned) and 1.0.75 (read). Checked
against 1.0.14 and 1.0.85:

| Prior claim | Status | Evidence |
|---|---|---|
| "There is no alternative model-setting surface" (§2) | **Wrong, even for 1.0.5.** `session.tools.updateSubagentSettings` with a per-agent `SubagentSettingsEntry(model, effort_level, context_tier)` exists in 1.0.5. 1.0.14 adds `model_policy` and `auto_invoke`. CLIREF documents the persisted `subagents` settings and the `/subagents` picker. | `SDK105:generated/rpc.py:22214-22225`, `:26231-26235`; `SDK:generated/rpc.py:37845-37862`; CLIREF:1213, :1217 |
| "effort is not inherited … a shadowed subagent runs at backend default" (§5) | **Superseded.** It was true of the doc text quoted at the time. On 1.0.14, omitted effort inherits the parent's effort when the model is the same; otherwise the resolved model's default applies. | `SDK:session.py:1255-1257`; `SDKDOC:custom-agents.md:262`; CLIREF:1214 |
| "SDK bump likely all that is required" for per-agent effort | **Done.** The field landed in v1.0.8 and the pin is 1.0.14. | upstream tags v1.0.7 / v1.0.8 |
| "Runtime discovery does not expose prompts" (§1) | **Version-bound.** True for 1.0.5 (its `AgentInfo` has no `prompt`). 1.0.14 `AgentInfo.prompt` is returned with `include_prompt=True`. | `SDK105:generated/rpc.py:15426-15466`; `SDK:generated/rpc.py:24883-24886`, `:30592-30607` |
| The prompt fork is mandatory in form (shadowing needs prompt text) | **Partly superseded.** 1.0.14 `session.agent.set_prompt` replaces a built-in's base prompt in memory *without* shadowing and keeps runtime prompt composition (so `promptParts` survives). A model change needs no prompt at all through `update_subagent_settings`. | `SDK:generated/rpc.py:43517-43521` |
| `includedBuiltinAgents` "not exposed by Python SDK 1.0.5" | Still true for `create_session` on 1.0.14. It is now reachable through `session.rpc.options.update`. | `SDK:generated/rpc.py:34313-34317` |
| `enable_config_discovery` covers MCP and skill directories only | **Version-bound.** That was the 1.0.5 wording. On 1.0.14 it reads "supported configuration" with no enumeration. | `SDK105:client.py:1814-1815`; `SDK:client.py:2446-2448` |
| Built-in table: `explore` → `claude-haiku-4.5`, `research` → `claude-sonnet-4.6`, `code-review` inherits | **Stale for the pin.** On 1.0.85, `explore` is `gpt-5.4-mini` at `low` and `task` is `claude-haiku-4.5`. `research` is still `claude-sonnet-4.6`, and `code-review` still has no authored model. | `CLI85:definitions/explore.agent.yaml:7-8`, `task.agent.yaml:7`, `research.agent.yaml:7` |
| `"prompt": null` is emitted when `prompt` is omitted | **Still true** on 1.0.14. | `SDK:client.py:4299` |
| Excluding a built-in plus a same-named custom agent re-binds dispatch | **Consistent** with the 1.0.14 docstring and the 1.0.85 schema wording (not live-tested). | `SDK:client.py:2416-2419`; `CLI85:schemas/api.schema.json` `SessionOpenOptions.excludedBuiltinAgents` |

## Open questions

These need a live probe or an upstream answer. Each would be settled by one throwaway SDK
session on the pinned runtime.

1. **Does session `disabled_skills` filter `CustomAgentConfig.skills` preloads, including
   bundled Skills?** If it does not, an unvalidated profile can bypass ADR-0015's allowlist.
   Probe: create a session with `disabled_skills=["x"]` and an agent with `skills=["x"]`,
   then inspect the subagent context; `session.skills.getInvoked` alone would not prove
   whether eager injection occurred.
2. **Are file-based agents (`~/.copilot/agents`, `.github/agents`, plugin, remote) offered in a
   git-loopy-shaped SDK session**, and does any of `enable_config_discovery=False`,
   `custom_agents_local_only=True`, `config_directory`, or `mode="empty"` suppress each tier?
   Probe: `session.rpc.agent.list()` and inspect `source`/`path`.
3. **If an SDK `custom_agents` entry shares a name with a file-based agent, which wins?** And
   which `AgentInfo.source` does an SDK-supplied agent report? The enum has no `sdk`/`session`
   value.
4. **Project versus user precedence** on CLI 1.0.85. GitHub's docs contradict each other (§3).
5. **Does `tasks.start_agent(agent_type=<custom name>)` accept a custom agent?** And is the
   `session.startSubagent` in CLIREF a Node-only or future SDK method?
6. **Do `*.agent.md` files accept a `skills:` key?** `AgentInfo.skills` exists, but CLIREF does
   not list it.
7. **Does per-agent tool inheritance still follow "denylists union, allowlists intersect"** on
   1.0.85? The CLI 1.0.85 bundle ships no `sdk/index.d.ts` to re-read.
8. **Vocabulary (#648):** is a subagent run an **Agent** (one live harness session)? Here it is
   a child execution sharing the parent `CopilotSession`'s event stream (envelope `agentId`,
   `SDKDOC:custom-agents.md:439-441`), not a separate session.
9. **Which CLI version does a `1.0.15-preview.*` wheel pin?** Unknown without the wheel. It
   depends on the feed.

## Method notes and limits

- **CLI help.** The pinned CLI 1.0.85 is cached only as a hostless runtime bundle
  (`CLI85:copilot-runtime` rejects `--version`/`--help`: "unsupported argument"). No `copilot`
  entrypoint is cached for it. The CLI help quoted here comes from the separately installed
  `copilot` **1.0.89-4** (`/opt/homebrew/bin/copilot`), which is newer than the pin.
- **Wire schema.** `session.create`'s `customAgents`, `agent` and `defaultAgent` fields do not
  appear in `CLI85:schemas/api.schema.json`, so the wire shape was read from SDK code only.
- **Runtime binary.** The runtime (`CLI85:runtime.node`, 72 MB native) was not decompiled. A
  `strings` scan found frontmatter keys (`infer`, `disable-model-invocation`,
  `user-invocable`, `model-policy`, `strict-tools-list`). This is corroborative only and is not
  cited as a primary source.
- **Network.** No package was installed or downloaded, and no public package registry was
  contacted. Upstream source came from `api.github.com` and documentation from
  `docs.github.com`.

## Sources

- Installed SDK 1.0.14: `git-loopy/python/.venv/lib/python3.13/site-packages/copilot/`
  (`session.py`, `client.py`, `_mode.py`, `_cli_version.py`, `generated/rpc.py`,
  `generated/session_events.py`). Line ranges are cited inline.
- Upstream tag `github/copilot-sdk@v1.0.14` (`e60d903`): `python/copilot/session.py`,
  `python/copilot/client.py` (identical to the install), `docs/features/custom-agents.md`,
  `docs/features/skills.md`, `CHANGELOG.md`.
  <https://github.com/github/copilot-sdk/tree/v1.0.14>
- Upstream tag `github/copilot-sdk@v1.0.15-preview.3`: `python/copilot/session.py`,
  `client.py`, `_mode.py`, `generated/rpc.py`, `docs/features/custom-agents.md`.
- Upstream tags `v1.0.7` and `v1.0.8`: `python/copilot/session.py` (dating of
  `reasoning_effort`).
- Stale SDK 1.0.5 install: `git-loopy/python/.venv/lib/python3.12/site-packages/copilot/`.
- Pinned CLI 1.0.85 bundle: `~/Library/Caches/github-copilot-sdk/cli/1.0.85/prebuilds/darwin-arm64/`
  (`definitions/*.agent.yaml`, `schemas/api.schema.json`).
- Installed CLI 1.0.89-4: `copilot --help`, and `~/.copilot/pkg/darwin-arm64/1.0.89-4/definitions/`.
- GitHub Docs:
  - <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference>
    (Custom agents reference, Skills reference, Subagent limits, `--agent`)
  - <https://docs.github.com/en/copilot/reference/custom-agents-configuration>
  - <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli>
  - <https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents>
- git-loopy: `git-loopy/python/pyproject.toml:30`, `git-loopy/python/git_loopy/session.py:700-742`,
  `docs/adr/0015-closed-world-skill-policy.md`, `docs/adr/0025-installed-skill-catalog.md`,
  `docs/research/subagent-model-override.md`.
