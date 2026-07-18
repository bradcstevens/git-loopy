# Customization

> Tailor git-loopy to a repository: domain context, agent guidance, feedback
> loops, Config, prompt overrides, and the per-repo skill configuration written
> by `/setup-agent-skills`.

## Repo structure reference

### What git-loopy provides

```
.
├── AGENTS.md                       # Repository guidance and exact feedback loops.
├── CONTEXT.md                      # Shared loop-engineering vocabulary.
├── LICENSE
├── README.md                       # git-loopy front door and complete workflow overview.
├── docs/                           # Loop-engineering and Runner family references.
│   ├── concepts.md
│   ├── workflow.md
│   ├── runners.md
│   ├── skills-setup.md
│   └── customization.md
├── .copilot/skills/                # Vendored, composable planning and execution skills.
│   ├── setup-agent-skills/         # Configure tracker, labels, and domain docs first.
│   ├── intake/                     # Capture a messy request without designing it.
│   ├── grill-me/                   # Resolve a general plan or decision.
│   ├── grill-with-docs/            # Plan against CONTEXT.md and ADRs.
│   ├── wayfinder/                  # Map planning work too large for one context.
│   ├── research/                   # Gather cited primary-source evidence.
│   ├── prototype/                  # Answer logic or UI questions with runnable evidence.
│   ├── to-spec/                    # Publish the agreed destination.
│   ├── to-tickets/                 # Slice the route into dependency-aware tracer bullets.
│   ├── triage/                     # Open the ready-for-agent execution gate.
│   ├── implement/                  # Human-driven execution of one selected slice.
│   ├── tdd/                        # Red-to-green vertical-slice discipline.
│   ├── diagnosing-bugs/            # Reproduce and diagnose difficult bugs.
│   ├── codebase-design/            # Design deep modules at clean seams.
│   └── code-review/                # Review against standards and the originating spec.
└── git-loopy/
    ├── PROMPT.md                   # Agent prompt loaded each iteration.
    ├── conformance/                # Language-neutral Wrapper-contract fixtures.
    └── python/                     # Python reference runner (GitHub Copilot Python SDK). See git-loopy/python/README.md.
```

> As the [Runner family](adr/0013-multi-language-runner-family.md) ports land, `git-loopy/` also gains `shell/`, `powershell/`, and `tui/` (the shared TUI helper binary). The [Conformance suite](../git-loopy/conformance/README.md) is already shared infrastructure; until the ports land, the Python Orchestrator is the only shippable member.

### What you customize when adopting

```
├── AGENTS.md                       # Your project's agent guide — Tech stack + Feedback loops table (see below).
├── CONTEXT.md                      # Your project's domain glossary.
├── docs/
│   ├── adr/                        # Architecture decision records (created lazily by /grill-with-docs).
│   └── agents/                     # Per-repo skill config — written by /setup-agent-skills.
│       ├── issue-tracker.md        #   Where issues live (GitHub / GitLab / local markdown / other).
│       ├── triage-labels.md        #   Label vocabulary used by /triage.
│       └── domain.md               #   Single- vs multi-context layout for CONTEXT.md / ADRs.
├── prds/                           # Optional legacy local-markdown PRDs (GIT_LOOPY_ISSUE_SOURCE=prds).
├── issues/                         # Optional legacy local-markdown issues.
└── <your application code>
```

## The two files you almost always edit

- **`AGENTS.md`** — fill in **Tech stack** and the **Feedback loops** table (see [Stack-agnostic defaults](#stack-agnostic-defaults) for the exact structure). The loop reads the **Feedback loops** table to know what commands to run before committing. If lint / type-check / test / build commands are wrong here, the agent guesses and CI catches the difference. The trailing **Agent skills** block is owned by `/setup-agent-skills`; don't hand-edit it the first time around.
- **[`git-loopy/PROMPT.md`](../git-loopy/PROMPT.md)** — usually leave defaults; only change if you want different skill routing or different commit-message conventions. If you change the commit-message convention, also update the `CLOSE_KEYWORD_RE` regex used by `extract_close_refs` in [`git-loopy/python/git_loopy/wrapper.py`](../git-loopy/python/git_loopy/wrapper.py) so the auto-close backstop still matches what the agent emits.

Replace the repository's [`CONTEXT.md`](../CONTEXT.md) with the adopting
project's language as `/grill-with-docs` resolves real terms. For the
`AGENTS.md` structure, see
[Stack-agnostic defaults](#stack-agnostic-defaults); for the planning path from
domain context to spec and tickets, see [`docs/workflow.md`](workflow.md).

## `/setup-agent-skills` — the entry-point skill

This skill is the first thing to run in Copilot CLI for any new project, **before** any of the other planning or implementation skills. It does two things:

1. **Populates the `## Agent skills` block at the bottom of `AGENTS.md`** with concrete pointers to the per-repo config below.
2. **Writes `docs/agents/{issue-tracker,triage-labels,domain}.md`** — the per-repo config files that `/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`, `/diagnosing-bugs`, `/tdd`, and `/codebase-design` read to learn which issue tracker, label vocabulary, and context layout this project uses.

The skill walks you through three decisions one at a time:

| Decision | What it controls | Defaults |
| --- | --- | --- |
| **Issue tracker** | Whether downstream skills call `gh issue create`, `glab issue create`, write a markdown file under `.scratch/`, or follow custom prose. | GitHub (if a `git remote` points at GitHub), GitLab (if it points at GitLab), local markdown (no remote), or "other" (free-form). |
| **Triage labels** | The exact strings `/triage` applies for each of the five canonical roles. | `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` — verbatim. Override per-role if your existing tracker uses different names. |
| **Domain docs** | Whether the repo has one global `CONTEXT.md` or a `CONTEXT-MAP.md` pointing to per-context files. | Single-context (most repos). Pick multi-context only if you actually have multiple bounded contexts. |

### Skip it and downstream skills will guess

If `/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`, `/diagnosing-bugs`,
`/tdd`, or `/codebase-design` lacks tracker, label, or domain context, run
`/setup-agent-skills`.

### Re-running it

`/setup-agent-skills` is idempotent. Re-run it whenever you want to:

- Switch issue trackers (GitHub → GitLab → local markdown → other).
- Rename triage labels (e.g., to match a label scheme your repo already has).
- Move from single-context to multi-context (or vice versa).

It edits the existing `## Agent skills` block in place and rewrites `docs/agents/*.md`. Your hand-edits inside `docs/agents/*.md` are preserved when possible, but if you've done substantial customization there, diff before accepting the rewrite.

### Auto-bootstrap behavior

git-loopy provides a **two-layer auto-bootstrap** so a forgotten
`/setup-agent-skills` does not lead to silent guessing:

| Layer | Where | What it does |
| --- | --- | --- |
| **Interactive sessions** | The "First-run bootstrap" directive at the top of `AGENTS.md` (loaded into every Copilot CLI invocation — [add it yourself](#first-run-bootstrap-directive) to enable this layer) | If `docs/agents/issue-tracker.md` does not exist, the agent invokes `/setup-agent-skills` as its first action — **before** acting on the user's request — then returns to the original ask. |
| **Autonomous Run** | Preflight check in [`git-loopy/python/`](../git-loopy/python/) | If `docs/agents/issue-tracker.md` does not exist, the Orchestrator exits non-zero **before** the first Iteration with a stderr message pointing the loop engineer at `/setup-agent-skills`. The skill is interactive and cannot safely run inside the autonomous agent session. |

The two layers compose: a human starts a fresh repo, runs `uv run --project git-loopy/python git-loopy`, gets a clear error, opens `copilot` interactively, and — if the [First-run bootstrap directive](#first-run-bootstrap-directive) is in their `AGENTS.md` — sees it auto-trigger `/setup-agent-skills`, answers the three questions, then re-runs the loop. Detection uses the existence of `docs/agents/issue-tracker.md` as the signal that the skill has run.

### First-run bootstrap directive

The interactive layer is opt-in: add this directive to the top of your
`AGENTS.md` so it loads into every Copilot CLI invocation. The Orchestrator
preflight works without it; this directive adds the interactive auto-trigger.

```markdown
> **First-run bootstrap (read on every invocation).** If `docs/agents/issue-tracker.md` does **NOT** exist at the repo root, your very first action this session is to invoke `/setup-agent-skills` - **before any other work**, including the user's stated request. After it completes, return to whatever the user originally asked. If `docs/agents/issue-tracker.md` already exists, this bootstrap is satisfied; ignore this paragraph and proceed normally. The autonomous git-loopy Orchestrator (`git-loopy/python/`) refuses to start without this file, so if you are reading this directive from inside a `copilot --yolo -p` invocation, surface the inconsistency and stop.
```

## Skills reference

git-loopy vendors its complete workflow catalog under
[`.copilot/skills/`](../.copilot/skills). The GitHub Copilot CLI marketplace
contains additional skills for work outside this catalog.

To discover more skills beyond what's vendored:

```bash
# From inside copilot:
> /find-skills <query>

# From the shell:
npx skills find <query>
```

For the boundary between human-invoked planning skills and model-invoked
execution skills, see
[`docs/runners.md` → Skill routing](runners.md#skill-routing).

## Stack-agnostic defaults

git-loopy is stack-agnostic. Put the project's exact lint, type-check, test,
and build commands in the **Feedback loops** table in `AGENTS.md`; human-driven
skills and autonomous Iterations use the same repository feedback.

Add a `## Feedback loops` section to `AGENTS.md` with a table shaped like this (replace the `<PLACEHOLDER>` commands with your project's real ones; delete rows you don't have):

```markdown
## Feedback loops

| Loop          | Command                 | When to run                                                             |
| ------------- | ----------------------- | ----------------------------------------------------------------------- |
| Lint          | `<PM> lint`             | Any code change                                                         |
| Type-check    | `<PM> typecheck`        | Any typed change                                                        |
| Unit tests    | `<PM> test:unit`        | Any code change                                                         |
| Build         | `<PM> build`            | Anything touching routes, configs, deps                                 |
| E2E tests     | `<PM> test:e2e`         | Anything user-visible; save reports to `tests/e2e/<YYYY-MM-DD-HHMMSS>/` |
| Infra what-if | `<IAC_WHAT_IF_COMMAND>` | Any infra change                                                        |
```

The loop expects this exact table shape: the `## Feedback loops` heading, then
the `Loop` and `Command` columns. Fill in every runnable row before delegating
work. Vague verbs such as "run the tests" force each fresh agent to rediscover
the command and weaken the feedback loop.

If you're on Azure or Microsoft tech, add **Azure conventions** and **Microsoft tooling** sections to `AGENTS.md` documenting the `SecurityControl=Ignore` tag and the `disableLocalAuth: false` default for Foundry resources. Otherwise skip them.

---

**Next:**
- [`docs/workflow.md`](workflow.md) — the complete loop-engineering workflow.
- [`docs/runners.md`](runners.md) — the Runner family reference; [`docs/wrapper-contract.md`](wrapper-contract.md) — the Wrapper contract every Orchestrator implements.
- [`docs/concepts.md`](concepts.md) — the mental models behind the design.
- Back to [`README.md`](../README.md).
