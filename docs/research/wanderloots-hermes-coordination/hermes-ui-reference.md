# Hermes Agent: feature & settings reference (as shown in the video)

A catalogue of every Hermes Agent surface, setting, and documented behavior in the
[video](video-walkthrough.md), organized by feature rather than by timestamp. **Version seen:**
Hermes desktop `v0.21.3`. Behavior may differ in other versions. Each item links to the frame
that shows it. Doc excerpts are paraphrased from the pages visible on screen:
[Subagent Delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation) and
[Kanban (Multi-Agent Board)](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban).

## Contents

1. [App shell](#1-app-shell)
2. [Capabilities: skills, tools, MCP, plugins](#2-capabilities-skills-tools-mcp-plugins)
3. [Subagent delegation (`delegate_task`)](#3-subagent-delegation-delegate_task)
4. [Goal mode (`/goal`)](#4-goal-mode-goal)
5. [Model routing & auxiliary models](#5-model-routing--auxiliary-models)
6. [Profiles, bots & group chats](#6-profiles-bots--group-chats)
7. [Kanban plugin](#7-kanban-plugin)
8. [Scheduled jobs (cron)](#8-scheduled-jobs-cron)
9. [Terminology](#9-terminology)

---

## 1. App shell

| Element | What it shows | Frame |
|---|---|---|
| Top tabs | **SESSIONS** / **BOTS**; open session tabs along the top | [home](images/video/02m26s-hermes-home.jpg) |
| Sidebar | New session (⌘N), Capabilities, Messaging, Artifacts, Scheduled jobs, Kanban (once the plugin is on), Search sessions, PINNED ("Shift-click a chat to pin"), PROJECTS (each with its sessions, model, and message count) | [home](images/video/02m26s-hermes-home.jpg) |
| Profile strip (bottom-left) | One icon per profile (W, O, R, L, W, G); hovering shows the name, e.g. `orchestrator` | [profile selector](images/video/16m31s-profile-selector.jpg) |
| Composer | Model picker (`GPT-5.6-terra`, `GPT-6-astra`) and effort (`Med`, `High`); mic and other toggles | [home](images/video/02m26s-hermes-home.jpg) |
| Status bar | Gateway state ("Gateway ready" or "Gateway inference unavailable"); workspace; **Agents** (with live subagent count, e.g. "3 subagents"); context usage (e.g. `~54.2k/272k ~20%`) with a bar; cache/efficiency %; tokens/s; RAM; "Manual" (approval mode); version + build hash | [subagents running](images/video/01m35s-subagents-running.jpg) |
| Artifacts / vault | Outputs were opened in an Obsidian vault (`Vault/Hermes/…`) | [output note](images/video/36m20s-output-note-architecture.jpg) |

## 2. Capabilities: skills, tools, MCP, plugins

- **Tabs:** Skills (125–127), Tools (25), MCP, Plugins.
- **Scope:** each tab is scoped by **"Configuring: <profile>"**. "Changes apply to new
  sessions."

**Skills** ([frame](images/video/38m57s-orchestrator-skills.jpg))

- The list is sorted by "Most used", with a usage count (×N) and an on/off toggle per skill.
- **learned** marks skills the agent created itself (`provenance: created_by: agent`).
- The detail pane shows frontmatter: name, description, version, author, license, platforms,
  `metadata.hermes.tags`, `related_skills`, and provenance. Actions: Edit / Archive.
- Footer: Skills Hub, *Update installed*, *Browse the full hub*.

**Tools** ([delegation](images/video/08m06s-capabilities-tools-task-delegation.jpg),
[orchestrator](images/video/39m05s-orchestrator-tools-kanban.jpg))

| Tool / toolset | Id | Tools | Notes |
|---|---|---|---|
| Task Delegation | `delegate_task` | 1 | Off by default in the demo. Enable it to let the parent spawn subagents. It was turned off for the orchestrator profile when that profile ran Kanban. |
| A2A | | 5 | "A2A (Agent-to-Agent) protocol v1.0 support for Hermes" |
| Kanban | | 14 | "opt-in task board tools for this platform". Required for a profile that plans or decomposes on the board. |
| Task Planning | `todo_list` | 1 | |

**Plugins** ([frame](images/video/24m32s-capabilities-plugins.jpg))

- "One row per plugin. A plugin can extend this app, the agent, or both — each half has its
  own switch." There are columns for **Desktop** and for the profile.
- *Install from Git* and a Plugin Catalog (*Browse*) are available.

| Plugin | Kind | Default | Description |
|---|---|---|---|
| Bots | Desktop, bundled | On | Bot Mode: one chat per agent, with avatars, routines, group chats, bot-to-bot messaging |
| Kanban | Desktop, bundled | Off | Multi-agent task board: board page, sidebar entry, live in-flight count in the status bar |
| Radio | Desktop, bundled | Off | Live radio with an audio-reactive waveform |

## 3. Subagent delegation (`delegate_task`)

**UI**

- **Live subagents panel** ("open agents"): "When a turn delegates work, child agents stream
  their progress here." ([frame](images/video/02m39s-no-live-subagents.jpg))
- **In-chat "N Subagents" block:** each child's brief, model, elapsed time, and state
  (brainstorming, analyzing, musing…). ([frame](images/video/01m35s-subagents-running.jpg))
- **Steer:** select a child and type in "Instructions for this subagent", then press **Steer**
  or **Stop**. The message is "Queued for the next checkpoint".
  ([frame](images/video/04m10s-steer-subagent.jpg))
- **Spawn tree modal:** "Live subagent activity for the current turn". It shows agents,
  active count, tools, and tokens (e.g. "3 agents · 2 active · 22 tools · 93.2k tok"). Each
  delegation batch is listed with its workers and every tool call (Skill View, Web Search,
  Web Extract, Terminal, Execute Code).
  ([frame](images/video/03m45s-spawn-tree.jpg), [frame](images/video/04m30s-spawn-tree-tokens.jpg))

**Documented behavior** ([frame](images/video/07m21s-docs-subagents-know-nothing.jpg))

- **"Subagents know nothing":** a fresh conversation with no parent history or tool calls.
  The only context is the `goal` and `context` fields passed to `delegate_task`.
- **Exception:** with a resolved workspace directory, subagents get the workspace's project
  context files (`.hermes.md` > `AGENTS.md` chain > `CLAUDE.md` > `.cursorrules`). `SOUL.md`
  is excluded.
- **Batches:** `tasks=[{goal, context, output_schema}]`. Keep `output_schema` forgiving:
  require only the fields you'll read.
- **Other topics on the docs page (from its table of contents):** completion delivery,
  background process lifetime, single task, parallel batch, forwarding images, independent
  completions (opt-in), durable background completions, **Model Override**, **Cost strategy:
  frontier planner, inexpensive workers**, the `/review` command and review model, inherited
  tool access, max iterations, child timeout, failure visibility, stall detection, and
  monitoring running subagents (`/agents`).

**Observed trade-off.** With delegation the parent's context stayed around ~37k tokens; without
it, ~102k. Total tokens across all agents were higher with delegation.

## 4. Goal mode (`/goal`)

- `/goal <text>` sets a **standing goal** that the agent works across turns until it's met.
  ([frame](images/video/08m37s-goal-card-turn-1.jpg))
- **Goal card:** status (*Goal active / waiting / paused*), **Turn N/20** (default max 20), a
  *Wait condition* line, *View details*, **Criteria · N** with **Add criterion** / *Clear all
  criteria*, and the subagent count.
- **Criteria can be added mid-run** and become part of the definition of done.
  ([dialog](images/video/10m12s-add-criterion-dialog.jpg),
  [added](images/video/11m05s-goal-criteria-added.jpg))
- **The judge:** after every turn a judge checks the output against the goal and criteria.
  More turns don't guarantee correctness or enforce a dollar budget.
- **Kanban goal mode:** the same engine can run inside a single Kanban card
  ([§7](#goal-mode-cards)). It shares the engine, not the state.

## 5. Model routing & auxiliary models

**Settings → Model**

- **"Applies to"** is a row of profile chips; changes apply only to the selected profile.
  ([frame](images/video/13m34s-settings-model-profile-scope.jpg))
- Other Settings sections: Chat, Appearance, Workspace, Safety, Browser, Passwords & Logins,
  Memory & Context, Voice, Advanced, Notifications, Billing, Providers, Gateways, Keyboard
  Shortcuts, Tools & Keys, Archived Chats, About.

**Auxiliary models** ([frame](images/video/28m10s-settings-auxiliary-models.jpg)). Each defaults
to "auto · use main model" and has *Set to main* / *Change*:

| Auxiliary | Used for |
|---|---|
| Title gen | Session titles |
| Review | `/review` reviewer subagent |
| Triage specifier | Kanban spec fleshing (rewrites a raw Triage idea into a task) |
| Kanban decomposer | Task decomposition (breaks a task into a card graph) |
| Profile describer | Auto profile descriptions (the "Auto" button on the Kanban board) |
| Curator | Skill-usage review |
| (also) Mixture of Agents | Separate section below |

**Ways to route cheaper models to workers** (narrated):

1. Say so in the prompt or goal.
2. Put a standing rule in the profile's `AGENTS.md`/`SOUL.md`.
3. Use auxiliary model settings.
4. For Kanban, give worker profiles their own default model. Per-profile config lives at
   `~/.hermes/profiles/<name>/config.yaml`, and the dispatcher sets `HERMES_HOME` when it
   spawns `hermes -p <assignee>`.
5. Override per task: `--model`/`--provider` at creation, `hermes kanban set-model` later, or
   the task's Model dropdown.
   ([docs](images/video/29m40s-docs-cost-strategy.jpg))

**Caveat from the companion post:** asking for a cheaper model in prose isn't proof it was
used. Verify the configured route.

## 6. Profiles, bots & group chats

- **A bot is a profile.** Each profile keeps its own model, skills, tools, memory, and
  conversation history. It is defined partly by `SOUL.md`.
  ([SOUL.md](images/video/38m24s-orchestrator-soul-md.jpg))
- **The author's profiles:** `default`, `gemma4`, `librarian`, `orchestrator`, `researcher`,
  `wanderloots-tutorials` (worker/implementer), `wiki-skill`. Rosters:
  [cards](images/video/16m40s-agent-roster.jpg), [matrix](images/video/15m12s-bot-profiles-matrix.jpg).
- **Memory:** built-in `memory.md` + `user.md` (minimal always-on context), plus an optional
  external memory. The orchestrator uses **Hindsight**.
- **Default Hermes bot:** "running as the default Hermes profile with an isolated Linux/Docker
  workspace". ([frame](images/video/16m00s-bots-hermes-scheduled-jobs.jpg))
- **Group chats:**
  - "Pick 2–6 bots. Local memberships sync through each Bot profile; cross-machine members
    stay scoped to this room." Avatar upload/generate and a group name.
    ([dialog](images/video/18m03s-new-group-chat-dialog.jpg))
  - The composer takes `@name` to direct a message or `@everyone` for all, and supports
    threads. ([room](images/video/17m42s-group-chat-research-team.jpg))
  - **Limit:** up to 3 serial rounds; a silent round or 10 messages "settles" the room. Bots
    reply or pass and can mention another bot or you for a decision.
    ([diagram](images/video/18m58s-group-chat-room-graph.jpg))
  - A group chat is discussion, not an enforced task sequence.

## 7. Kanban plugin

### Board UI

- **Header:**
  - A board dropdown ("Default 5"), with Rename…, Settings…, New board…, Export…, Import….
    ([frame](images/video/28m55s-board-menu.jpg))
  - A card count, *Filter cards…*, an orchestration-settings (people) icon, and **+ New task**.
- **First-run guide:** "You don't run the cards — agents do… No assignee, no run…"
  ([frame](images/video/23m41s-kanban-lane-guide.jpg))
- **Columns** ([frame](images/video/24m22s-kanban-columns-triage-tooltip.jpg)):

| Column | Tooltip / meaning |
|---|---|
| TRIAGE | "Raw ideas — a specifier fleshes out the spec." Auto-decomposed if enabled |
| TODO | Waiting on other cards, or unassigned |
| SCHEDULED | Waiting on a timer |
| READY | "Dependencies satisfied — assign a profile and the dispatcher runs it." |
| RUNNING | Claimed by a worker (agents' lane, hands off) |
| BLOCKED | "It's waiting on you": human input, a goal budget exhausted, an impossible goal, or an unclear task |
| REVIEW | Review agent's lane (`review_dispatch`) |
| DONE | Completed; results come back on the card |

- **Card face:** title, summary, assignee avatar (R = researcher, O = orchestrator, D =
  default, WT = wanderloots-tutorials), checklist (e.g. 0/2), comment count, dependency count,
  short id (e.g. `aed0c6`), and "working · 3m".

### New task dialog

Fields ([frame](images/video/25m54s-new-task-dialog.jpg)):

| Field | Example / default |
|---|---|
| Title | "Rough idea — a specifier will flesh it out" |
| Description | optional |
| Priority | `0` (orders multiple tasks) |
| Workspace | `scratch · board default` |
| Assignee | the board's default assignee |
| Skills (comma-separated) | e.g. `translation, github`; pins extra skills to this task |
| Model | `Profile default` ("Runs this task on a specific model and thinking depth. Unset uses the assigned profile's own.") |
| Goal mode | "worker loops until a judge agrees it's done" |
| Estimate | Model-call token estimate + size (e.g. `~45k tok · Large` → `~18k tok · Medium`) with a rationale tooltip ([45k](images/video/31m50s-task-estimate-45k.jpg), [18k](images/video/32m20s-task-estimate-18k.jpg)) |

### Card detail drawer

Contents ([running](images/video/33m30s-running-card-activity.jpg), [done](images/video/34m45s-done-card-worker-log.jpg)):

- status dropdown and id
- title
- assignee, priority, workspace path (`~/.hermes/kanban/w…`), model
- **Created by** (`dashboard` / `auto-decomposer`), created time, worker pid
- description
- **Estimate effort** ("makes a model call")
- **Dependencies:** *Blocked by* / *Blocks*
- **Comments:** "Message the running worker…" → *Send* ("Delivered to the running worker
  within a few seconds") and **Requeue with note**
- **Activity:** created, assigned, "dependencies done — promoted to Ready", "claimed by a
  worker", "tip scratch workspace", "worker started (pid)", heartbeats, completed
- **Runs:** status, profile, duration, summary
- **Worker log (tail)**

### Orchestration settings

([frame](images/video/25m48s-kanban-orchestration-settings.jpg), [off](images/video/39m18s-auto-decompose-off.jpg))

- **Orchestrator profile:** which profile plans and routes.
- **Default assignee:** used when a card has no explicit assignee.
- **Auto-decompose triage tasks:** on means the auxiliary decomposer splits Triage cards; off
  means Triage cards wait.
- **Profile descriptions:** one-line routing hints per profile, used by the decomposer. Each
  has *Save* and **Auto** (generated by the *Profile describer* auxiliary).

### Boards & projects

- **Board settings → Project:** `No project (scratch sandboxes)`, or a project. "New tasks run
  in the project's repo (a **worktree per task**); each task can still override its workspace
  at creation. Manage projects with `hermes project`."
  ([frame](images/video/29m12s-board-settings-project.jpg))
- **New project:** a name, one or more folders, and an idea saved to `IDEA.md`.
  ([frame](images/video/29m28s-new-project-dialog.jpg))

### Dispatcher

([docs](images/video/26m40s-docs-gateway-dispatcher.jpg), [diagram](images/video/26m54s-how-task-graphs-run.jpg))

- Runs **inside the gateway process** by default. It ticks every **60 s**, checks
  dependencies, and starts Ready + assigned cards.
- `config.yaml`:

  ```yaml
  kanban:
    dispatch_in_gateway: true        # default
    dispatch_interval_seconds: 60    # default
    review_dispatch: true            # default; false for human-only review boards
  ```

- Env override: `HERMES_KANBAN_DISPATCH_IN_GATEWAY=0`. Start with `hermes gateway start` (or a
  systemd user unit). No gateway means Ready cards wait, and `hermes kanban create` warns.
- `hermes kanban daemon` is deprecated (a `--force` escape hatch lasts one release). Never run
  both against one `kanban.db`, because they race on claims.
- **Idempotent create:** `hermes kanban create "<title>" --assignee <p> --idempotency-key <k>
  --json`.

### Goal-mode cards

([docs](images/video/31m16s-docs-goal-mode-cards.jpg))

- **Default:** one shot per worker (`kanban_complete` / `kanban_block`, then exit).
- **With `--goal` / `goal_mode=True`:** a Ralph-style loop. An auxiliary judge checks the
  output after each turn against the card title + body as acceptance criteria.
- **Stop conditions:** the judge agrees, the worker ends the task, or the turn budget
  (`--goal-max-turns`, default 20) runs out, which **blocks** the card for review.
- **Impossible goals:** a goal judged unachievable is blocked immediately with a reason.
  Complete / request-review are rejected on such cards.
- **When to use it:** open-ended or "keep going until X" cards. Skip it for cheap one-shot
  work, where the judge overhead isn't worth it and the dispatcher's retry/circuit-breaker
  covers transient failures.
- **Separate state:** it shares the engine with chat `/goal`, not the state.

### Lifecycle plugin hooks

([docs](images/video/29m40s-docs-cost-strategy.jpg))

- `kanban_task_claimed` fires in the dispatcher process.
- `kanban_task_completed` and `kanban_task_blocked` fire in the worker process.
- Each carries `task_id` and `profile_name`, and fires after the DB commit.
- Register hooks in the dispatcher profile to observe transitions centrally.

### Kanban vs `delegate_task`

([docs](images/video/22m30s-docs-kanban-vs-delegate-task.jpg), [guidance](images/video/23m35s-docs-kanban-vs-delegate-guidance.jpg))

- The full table is in the [walkthrough](video-walkthrough.md#10-kanban-vs-delegated-subagents-22272415).
- One sentence: `delegate_task` is a function call; Kanban is a work queue where every handoff
  is a row any profile (or human) can see and edit.
- They coexist: a Kanban worker may call `delegate_task`.

### Other docs topics visible in the Kanban page's table of contents

- completion checkpoints before the iteration cap
- "Two surfaces: the model talks through tools, you talk through the CLI"
- PR completion contracts
- core concepts
- boards (multi-project)
- managing boards from the CLI and the dashboard
- file attachments
- quick start
- bulk CLI verbs
- enabling tools for a chat profile
- how workers interact with the board
- why tools instead of shelling `hermes kanban`
- recommended handoff evidence
- the worker lifecycle
- pinning extra skills to a specific task
- per-task model override
- how the orchestrator behaves
- dashboard (GUI)
- Auto vs Manual orchestration

Sidebar docs pages nearby:

- Kanban tutorial
- Kanban worker lanes
- Kanban Multi-Gateway Deployment
- Persistent Goals
- Session Heartbeats
- Recurring Loops
- Scheduled Tasks (Cron)
- Automation Blueprints Catalog
- Codex App-Server Runtime
- Event Hooks

## 8. Scheduled jobs (cron)

- A right-hand **Scheduled Jobs** panel appears per bot. Empty state: "Schedule a prompt to
  run on a cron expression. Hermes will run it and deliver results to the destination you
  pick." with a **New cron** button.
- The author's jobs:
  - "AI Daily Intel – impact-focused daily brief" `0 8 * * *`
  - "AI Daily Intel – macro feedback sweep" `50 7 * * *`
  ([frame](images/video/16m00s-bots-hermes-scheduled-jobs.jpg))
- Companion-post guidance: if the only need is "run this every weekday", use cron. Use a board
  only when multi-stage coordination is also required.

## 9. Terminology

| Term | Meaning in Hermes / this material |
|---|---|
| Parent / child | The session agent and the subagents it spawns via `delegate_task` |
| Goal (chat) | A `/goal` standing objective with criteria, judged each turn, default 20 turns |
| Profile / bot | A persistent named agent config (model, skills, tools, memory, `SOUL.md`) |
| Group chat | A room of 2–6 bots for discussion; up to 3 serial rounds |
| Board | A Kanban task queue + history (SQLite `kanban.db`) |
| Project | Groups related work; a board can bind to a project repo (worktree per task) |
| Workspace | Where a worker handles files (`scratch` sandbox or project worktree) |
| Specifier | Auxiliary model that rewrites a raw Triage idea into a task |
| Decomposer | Auxiliary model (auto) or orchestrator profile (manual) that splits a task into cards |
| Dispatcher | Gateway-embedded loop (60 s) that starts Ready, assigned cards |
| Worker | The profile process that claims and executes a card |
| Root card | The original request card, which wakes when all its children complete |
