# GitHub Copilot Ralph Starter Kit

A starter kit for running an **AFK (away-from-keyboard) AI coding loop** on top of the **GitHub Copilot CLI**. Drop the templates, skills, and runner script into a new repo, fill in `AGENTS.md`, point the loop at a kanban of triaged GitHub Issues, and let an agent implement them autonomously while you do something else.

> Inspired by the [AI Engineer Workshop 2026](https://github.com/mattpocock/ai-engineer-workshop-2026-project) workflow, ported to the GitHub Copilot CLI.

**What you get:**

- **Two interchangeable AFK runners** — pick whichever fits your environment:
  - [`ralph/afk.sh`](ralph/afk.sh) — pure-bash, minimum dependencies (`gh`, `jq`, `git`, `copilot`).
  - [`ralph/python/`](ralph/python/) — Python peer variant on the GitHub Copilot Python SDK, richer terminal UX (frozen iteration `Panel`s, per-iteration token + estimated-cost signal, JSONL replay log, run-summary JSON, opt-in OpenTelemetry tracing). See [`ralph/python/README.md`](ralph/python/README.md).

  Both runners share [`ralph/PROMPT.md`](ralph/PROMPT.md) and the same wrapper contract — same `ready-for-agent` filter, same `## Parent` + `## Acceptance criteria` discriminator, same `Closes/Fixes/Resolves #N` auto-close backstop, same env-var surface (`MODEL`, `ISSUE_SOURCE`, `MAX_NMT_STRIKES`), same clean-exit-on-empty / abort-on-stuck termination model. Pick the one that suits your environment; the workflow around it is identical.
- Per-repo configuration templates: [`AGENTS.md`](AGENTS.md) (loaded into every agent invocation), [`CONTEXT.md`](CONTEXT.md) (the domain glossary, normally created lazily by `/grill-with-docs`), and [`templates/BRIEF.template.md`](templates/BRIEF.template.md) (the brief that `/to-prd` consumes).
- A vendored copy of every Copilot CLI skill the loop knows how to route to, under [`.copilot/skills/`](.copilot/skills) — alignment (`/grill-me`, `/grill-with-docs`), planning (`/to-prd`, `/to-issues`, `/triage`), implementation (`/diagnose`, `/prototype`, `/tdd`, `/improve-codebase-architecture`, `/zoom-out`), and meta (`/find-skills`, `/setup-agent-skills`, `/write-a-skill`, `/handoff`, `/caveman`).

**Stack-agnostic.** Customize the **Feedback loops** table in `AGENTS.md` once for your project's lint / type-check / test / build commands; both the human-driven skills and the AFK loop read from it.

---

## Core Mental Models

Before touching anything, internalize these two constraints — everything in this workflow flows from them.

### The Smart Zone / Dumb Zone

LLMs degrade as context grows. Attention relationships scale quadratically with tokens. A practical threshold: **~100k tokens is your smart zone ceiling**, regardless of whether the model advertises 200k or 1M. Past that you're in the dumb zone — the model starts making stupid decisions.

**Implication:** Size every task so it fits inside the smart zone. Never let the AI bite off more than fits.

### The Memento Model

Every iteration starts from zero (system prompt + `AGENTS.md` + the issue). The agent forgets everything between iterations. This is a feature, not a bug — **optimize for it** rather than fighting it with compaction. A cleared context is always a known, clean state. Compacted sediment is unpredictable.

`ralph/afk.sh` invokes a fresh `copilot --yolo -p` per iteration on purpose.

---

## Prerequisites

**Shared by both runners:**

- [GitHub Copilot CLI](https://docs.github.com/copilot/github-copilot-in-the-cli) installed and signed in: `npm install -g @github/copilot` then run `copilot` once to authenticate.
- [`gh`](https://cli.github.com/) on PATH and signed in (`gh auth login`).
- `git` on PATH.
- A GitHub repository for your project (the loop's default issue source).

**`ralph/afk.sh` (bash variant) additionally needs:**

- [`jq`](https://jqlang.org/) on PATH. (The Python variant parses JSON directly; it does **not** require `jq`.)

**`ralph/python/` (Python variant) additionally needs:**

- Python **≥ 3.11** on PATH.
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip` **≥ 24** for the one-time `uv sync --project ralph/python` bootstrap. See [`ralph/python/README.md`](ralph/python/README.md) for details.

---

## Quick Start

```bash
# 1. Clone this kit into a new project and remove its git history.
git clone https://github.com/bradcstevens/github-copilot-ralph-starter-kit my-project
cd my-project
rm -rf .git
git init && git add -A && git commit -m "Initial commit from github-copilot-ralph-starter-kit"

# 2. Install the skills at the user level so /skillname works in any session.
#    Or run /setup-agent-skills from inside copilot to do this interactively.
mkdir -p ~/.copilot/skills
cp -R .copilot/skills/* ~/.copilot/skills/

# 3. Fill in the templates. Each file has a "How to use this template" header —
#    grep for placeholders to find what's left to replace.
$EDITOR AGENTS.md      # project description, tech stack, feedback loops
$EDITOR CONTEXT.md     # domain glossary (or delete and let /grill-with-docs create it)
$EDITOR ralph/prompt.md  # loop-specific routing rules (usually leave defaults)

# 4. Create a brief and walk through the workflow.
cp templates/BRIEF.template.md BRIEF.md
$EDITOR BRIEF.md
copilot
> /grill-me  # converge on a design before producing artifacts
```

You don't need to use every phase. The skills are independent — pick what helps.

---

## Pick a Runner: `ralph/afk.sh` vs `ralph/python/`

Both runners implement the **same wrapper contract** — same `ready-for-agent` filter, same `## Parent` + `## Acceptance criteria` discriminator, same `Closes/Fixes/Resolves #N` auto-close backstop, same env-var surface, same termination model. Pick the one that suits your environment.

| Surface                          | `ralph/afk.sh` (bash)                                                | `ralph/python/` (Python SDK)                                                                                                                  |
| -------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Invocation                       | `bash ralph/afk.sh`                                                  | `uv run --project ralph/python ralph-afk`                                                                                                      |
| Positional arg (iteration cap)   | `bash ralph/afk.sh 50` (0 / omitted = unlimited)                     | `uv run --project ralph/python ralph-afk 50` (0 / omitted = unlimited)                                                                         |
| `MODEL`                          | env var (default `claude-opus-4.7-xhigh`)                            | env var (same default)                                                                                                                         |
| `ISSUE_SOURCE`                   | env var; `github` (default) or `prds`                                | env var; same                                                                                                                                  |
| `MAX_NMT_STRIKES`                | env var (default `3`)                                                | env var (same default)                                                                                                                         |
| Exit `0` — clean                 | empty AFK-ready pool **or** iteration cap reached                    | empty AFK-ready pool **or** iteration cap reached                                                                                              |
| Exit `1` — aborted               | `MAX_NMT_STRIKES` tripped **or** stale worktree                      | `MAX_NMT_STRIKES` tripped **or** stale worktree **or** preflight/setup failure (gh not authed, prompt file missing, malformed pricing, etc.)   |
| Observability artefacts          | stdout/stderr only                                                   | `.ralph/logs/<iso>-<run_id>.jsonl` (replay JSONL) + `.ralph/runs/<iso>-<run_id>.json` (per-iteration rollup) + `.ralph/logs/<iso>-<run_id>.log` (stderr mirror) |
| Terminal UX                      | streamed text                                                        | Rich-rendered iteration `Panel`s, per-iteration token + estimated-cost signal, run-end summary table                                           |
| OpenTelemetry tracing            | n/a                                                                  | opt-in via `uv sync --project ralph/python --extra otel` + `RALPH_OTEL_ENABLED=1` (or `OTEL_EXPORTER_OTLP_ENDPOINT`)                            |
| Extra prerequisites              | `jq`                                                                 | Python ≥ 3.11, `uv` (or `pip ≥ 24`)                                                                                                            |

### When to use which

**Use `ralph/afk.sh` when** you want the smallest possible dependency footprint — `gh`, `jq`, `git`, `copilot` and nothing else. The bash runner is stack-agnostic (it doesn't care that your project happens to be Python, Node, Rust, or something else) and is the right default for repos that deliberately chose a zero-Python, zero-npm toolchain.

**Use `ralph/python/` when** you want the richer terminal experience — frozen iteration `Panel`s showing tool calls / tokens / estimated cost, a JSONL replay log under `.ralph/logs/` you can grep through later, a run-summary JSON for post-hoc analysis, and (optionally) OpenTelemetry tracing of the full SDK + wrapper span tree. The extra dependencies (Python ≥ 3.11, `uv`) are one-time and stay scoped to `ralph/python/` — they do not touch your project's runtime.

The cost figure surfaced by the Python runner is an **estimate** based on provider list prices (not Copilot's premium-request billing). See [`ralph/python/README.md`](ralph/python/README.md) for the full caveat.

---

## Repo Structure Reference

```
.
├── AGENTS.md                       # Per-repo agent config (template). Read into every Copilot CLI invocation.
├── CONTEXT.md                      # Domain glossary (template). Referenced by AGENTS.md, PRDs, and slice issues.
├── LICENSE
├── README.md
├── templates/
│   └── BRIEF.template.md           # Copy to BRIEF.md and fill in before running /to-prd.
├── .copilot/skills/                # Vendored project-local copy of every skill the loop routes to.
│   ├── grill-me/                   # Phase 1 alignment interview.
│   ├── grill-with-docs/            # Stress-test a plan against CONTEXT.md and docs/adr/.
│   ├── to-prd/                     # Brief → published PRD issue.
│   ├── to-issues/                  # PRD → AFK-ready slice issues.
│   ├── triage/                     # Label state machine (needs-triage / ready-for-agent / …).
│   ├── diagnose/                   # Disciplined bug repro → fix loop.
│   ├── prototype/                  # Sketch logic or UI before committing to a slice.
│   ├── tdd/                        # Red → green → refactor discipline for slice implementation.
│   ├── improve-codebase-architecture/  # Surface deepening / refactor candidates.
│   ├── zoom-out/                   # Higher-level map of an unfamiliar area.
│   ├── find-skills/                # Discover other installed skills on demand.
│   ├── setup-agent-skills/         # One-shot installer that copies .copilot/skills/* into ~/.copilot/skills/.
│   ├── write-a-skill/              # Author or update a skill.
│   ├── handoff/                    # Compact a long human-driven session into a continuation doc.
│   ├── microsoft-foundry/          # Azure AI Foundry helpers (delete if not on Microsoft tech).
│   └── caveman/                    # Token-compressed output mode (off by default in the loop).
└── ralph/
    ├── afk.sh                      # Autonomous loop (bash). Clean exit on empty queue; aborts on MAX_NMT_STRIKES consecutive no-progress iterations; auto-close backstop for forgotten `gh issue close` calls.
    ├── PROMPT.md                   # Shared agent prompt loaded each iteration (both runners).
    └── python/                     # Python peer variant of the AFK runner on the GitHub Copilot Python SDK — same wrapper contract, richer terminal UX. See ralph/python/README.md.
```

When you adopt this in a real project, you'll typically add:

```
├── BRIEF.md                        # Filled-in copy of templates/BRIEF.template.md.
├── docs/
│   ├── adr/                        # Architecture decision records (created lazily).
│   └── agents/                     # Per-repo skill config (issue-tracker.md, triage-labels.md, domain.md).
├── prds/                           # Optional legacy local-markdown PRDs (ISSUE_SOURCE=prds).
├── issues/                         # Optional legacy local-markdown issues.
└── <your application code>
```

---

## The Full Workflow

```
Idea → Grill → Brief → PRD → Issues → Triage → [AFK loop] → QA → Repeat
 ^                                                                  |
 └────────────────────── new issues ───────────────────────────────┘
```

Every step up to "AFK loop" is **human-in-the-loop**. Once you kick off the loop, you go AFK. QA is yours again — it's where you impose taste.

### Phase 1 — Alignment (`/grill-me`)

**Goal:** Reach a shared design concept with the agent before producing any artifacts.

This is the most important phase and the one most people skip. The "specs to code" antipattern — generating specs without keeping the existing code in the loop — produces plans that don't survive contact with the codebase.

```bash
copilot
> /grill-me  # then paste or reference your starting brief
```

The skill interviews you relentlessly, walks each branch of the design tree, gives its recommended answer before asking each question, and asks one question at a time. Sessions can run 20–80+ questions. **Don't let it jump to a plan prematurely.** The output you want is alignment, not a document.

### Phase 2 — Brief

Once aligned, capture the result in `BRIEF.md` using `templates/BRIEF.template.md`. The brief is the canonical source for your domain language, scope, and decisions — anchor `AGENTS.md`, the PRD, and slice issues back to it.

### Phase 3 — PRD (`/to-prd`)

```bash
> /to-prd  # in the same session as /grill-me, while context is still warm
```

Publishes the brief as the parent PRD issue in your GitHub Issues tracker (the canonical destination — see `docs/agents/issue-tracker.md` if `/setup-agent-skills` has been run for your repo). The PRD becomes the parent every slice issue links back to via its `## Parent` section.

### Phase 4 — Slice Issues (`/to-issues`)

```bash
# Start a new session (Memento Model).
copilot
> /to-issues
```

The skill re-explores the codebase, quizzes you on slice boundaries, and creates one GitHub Issue per **vertical slice** (schema + service + UI through every layer — never horizontal). Each issue carries `## Parent` and `## Acceptance criteria`, which are the two sections `ralph/afk.sh` looks for when filtering AFK-ready work.

### Phase 5 — Triage (`/triage`)

```bash
> /triage
```

Walks the open issues through the five-label state machine (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). Only `ready-for-agent` issues are picked up by the AFK loop. The canonical label list lives in `docs/agents/triage-labels.md`.

### Phase 6 — AFK Loop (`ralph/afk.sh` or `ralph/python/`)

Pick a runner — see [Pick a Runner](#pick-a-runner-ralphafksh-vs-ralphpython) above. The flow below describes `ralph/afk.sh`; the Python peer variant implements the **same** per-iteration flow and **same** exit conditions. For Python-specific bootstrap, env vars, and observability artefacts, see [`ralph/python/README.md`](ralph/python/README.md).

```bash
# Unlimited iterations, default model (claude-opus-4.7-xhigh).
bash ralph/afk.sh

# Cap at 50 iterations.
bash ralph/afk.sh 50

# Pick a different model.
MODEL=gpt-5.4 bash ralph/afk.sh

# Tolerate more no-progress iterations before aborting (default: 3).
MAX_NMT_STRIKES=5 bash ralph/afk.sh

# Use the legacy local-markdown mode (prds/<feature>/NNN-*.md).
ISSUE_SOURCE=prds bash ralph/afk.sh
```

**Per-iteration flow:**

1. **Stale-worktree guard.** Refuses to start if the working tree is dirty (uncommitted changes from a previous iteration would otherwise get absorbed into the next one).
2. **Collect.** Pulls every open issue labeled `ready-for-agent` via `gh issue list`, then filters to those whose body contains both `## Parent` and `## Acceptance criteria` (skips bare PRDs).
3. **Run.** Feeds the filtered set, the last five commits, and `ralph/prompt.md` to a fresh `copilot --yolo -p` invocation. Streams the agent's reasoning, tool calls, and tool output to the terminal. Captures Copilot's exit code via `PIPESTATUS` so a crash isn't mistaken for a clean turn.
4. **Auto-close backstop.** Walks new commits for GitHub closing keywords (`Closes/Fixes/Resolves #N`, case-insensitive) **restricted to issue numbers that were in this iteration's AFK-ready pool**. Any referenced issue that's still open gets closed by the wrapper with a comment pointing at the commit SHA(s). The pool whitelist prevents a stale or mis-numbered `Closes #N` from acting on an unrelated issue.
5. **Progress accounting.** An iteration "made progress" if it produced commits or wrapper closures. Otherwise it counts as a strike.

**Exit conditions:**

| Exit                  | Code | When                                                                                   |
| --------------------- | ---- | -------------------------------------------------------------------------------------- |
| Clean — queue empty   | `0`  | Start of an iteration finds the AFK-ready pool empty.                                  |
| Clean — iteration cap | `0`  | Optional positional arg `N` reached without natural termination.                       |
| **Aborted — stuck**   | `1`  | `MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress.                 |

The legacy `<promise>NO MORE TASKS</promise>` sentinel is now **informational only**: the wrapper counts it as a strike if the iteration made no progress, otherwise ignores it. The next iteration's collection is always the source of truth on whether work remains.

**Commit-message contract.** The auto-close backstop relies on commit messages following the GitHub closing-keyword convention:

- **Completion commits:** `Closes #N`, `Fixes #N`, or `Resolves #N` (case-insensitive forms — `close[sd]?`, `fix(es|ed)?`, `resolve[sd]?` — followed by whitespace then `#N`).
- **Partial-progress commits:** use `Refs #N` or `Progress on #N` so the wrapper does **not** auto-close.

`prompt.md` instructs the agent in this contract and also lays out a **FINAL SEQUENCE** for issue closure (re-fetch state → `gh issue close` → verify state is `CLOSED` → retry once → fall through to wrapper backstop). If you customize `prompt.md`, keep that contract intact or the backstop will misfire.

**Skill routing.** `prompt.md` directs each iteration's work to the right skill: `/diagnose` for hard bugs, `/prototype` for sketchy areas, `/tdd` for slice implementation, `/improve-codebase-architecture` for refactors, `/grill-with-docs` for plan stress-testing, and `/zoom-out` when the agent needs a higher-level map first.

### Phase 7 — QA

Your turn again. Review the merged work, file follow-up issues, run `/triage` to relabel anything that needs human attention, and start the loop again.

---

## Customization

The **two files you almost always edit** before running the loop in a real repo:

- **`AGENTS.md`** — fill in **Tech stack** and **Feedback loops**. The loop reads the **Feedback loops** table to know what commands to run before committing. If lint / type-check / test / build commands are wrong here, the agent guesses and CI catches the difference.
- **`ralph/prompt.md`** — usually leave defaults; only change if you want different skill routing or different commit-message conventions. If you change the commit-message convention, also update the regex in `extract_close_refs` inside `ralph/afk.sh` so the auto-close backstop still matches what the agent emits.

The **template files** (`AGENTS.md`, `CONTEXT.md`, `templates/BRIEF.template.md`) each include a `> 📝` placeholder convention and a `> 🗑️ DELETE IF NOT APPLICABLE` convention. Grep for `<[A-Z_]` to find what's left to replace.

---

## Skills Reference

Run `npx skills find <query>` or `/find-skills` from inside Copilot CLI to discover more skills beyond what's vendored here. The Matt Pocock skill library at [`mattpocock/ai-engineer-workshop-2026-project`](https://github.com/mattpocock/ai-engineer-workshop-2026-project) is the canonical upstream.

Skills the AFK loop **will not invoke** (out of scope for unattended runs): `/triage`, `/to-prd`, `/to-issues` (they create or relabel issues — human-driven), `/handoff` (pointless inside a one-shot iteration), `/caveman` (reviewability beats compression while running unattended).

---

## License

MIT — see [`LICENSE`](LICENSE).
