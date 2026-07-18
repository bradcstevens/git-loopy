# Customization

> Once the [Quick Start](../README.md#quick-start) is done, this is where you tailor the kit to your project — repo structure, AGENTS.md, PROMPT.md, and the per-repo skill configuration written by `/setup-agent-skills`.

## Repo structure reference

### What ships in the kit

```
.
├── CONTEXT.md                      # Domain glossary stub. Referenced by AGENTS.md, PRDs, and slice issues; extended lazily by /grill-with-docs.
├── LICENSE
├── README.md                       # Quickstart.
├── docs/                           # Kit documentation (you're reading docs/customization.md right now).
│   ├── concepts.md
│   ├── workflow.md
│   ├── runners.md
│   ├── skills-setup.md
│   └── customization.md
├── .copilot/skills/                # Vendored project-local copy of every skill the loop routes to.
│   ├── setup-agent-skills/         # ⭐ Run FIRST in a new project — scaffolds the per-repo `## Agent skills` block and docs/agents/*.md.
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
│   ├── write-a-skill/              # Author or update a skill.
│   ├── handoff/                    # Compact a long human-driven session into a continuation doc.
│   ├── microsoft-foundry/          # Azure AI Foundry helpers (delete if not on Microsoft tech).
│   └── caveman/                    # Token-compressed output mode (off by default in the loop).
└── git-loopy/
    ├── PROMPT.md                   # Agent prompt loaded each iteration.
    └── python/                     # Python reference runner (GitHub Copilot Python SDK). See git-loopy/python/README.md.
```

> As the [runner family](adr/0013-multi-language-runner-family.md) ports land, `git-loopy/` also gains `shell/`, `powershell/`, `tui/` (the shared TUI helper binary), and `conformance/` (the language-neutral parity suite). Until then, the Python runner is the only shippable member.

### What you'll add when adopting

```
├── AGENTS.md                       # Your project's agent guide — Tech stack + Feedback loops table (see below).
├── SPEC.md                         # Your brief — the input /to-prd consumes.
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

The [`CONTEXT.md`](../CONTEXT.md) stub at the repo root uses a `> 📝` placeholder convention for the notes `/grill-with-docs` fills in on demand — leave it until real vocabulary appears. For the `AGENTS.md` structure to fill in, see [Stack-agnostic defaults](#stack-agnostic-defaults); for the `SPEC.md` brief, see [`docs/skills-setup.md`](skills-setup.md#part-3--make-agentsmd-and-specmd-yours).

## `/setup-agent-skills` — the entry-point skill

This skill is the first thing to run in Copilot CLI for any new project, **before** any of the other planning or implementation skills. It does two things:

1. **Populates the `## Agent skills` block at the bottom of `AGENTS.md`** with concrete pointers to the per-repo config below.
2. **Writes `docs/agents/{issue-tracker,triage-labels,domain}.md`** — the per-repo config files that every other skill (`/to-issues`, `/triage`, `/to-prd`, `/diagnosing-bugs`, `/tdd`, `/improve-codebase-architecture`, `/zoom-out`) reads to learn which issue tracker, label vocabulary, and context layout this project uses.

The skill walks you through three decisions one at a time:

| Decision | What it controls | Defaults |
| --- | --- | --- |
| **Issue tracker** | Whether downstream skills call `gh issue create`, `glab issue create`, write a markdown file under `.scratch/`, or follow custom prose. | GitHub (if a `git remote` points at GitHub), GitLab (if it points at GitLab), local markdown (no remote), or "other" (free-form). |
| **Triage labels** | The exact strings `/triage` applies for each of the five canonical roles. | `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` — verbatim. Override per-role if your existing tracker uses different names. |
| **Domain docs** | Whether the repo has one global `CONTEXT.md` or a `CONTEXT-MAP.md` pointing to per-context files. | Single-context (most repos). Pick multi-context only if you actually have multiple bounded contexts. |

### Skip it and downstream skills will guess

If `/to-issues`, `/triage`, `/to-prd`, `/diagnosing-bugs`, `/tdd`, or `/improve-codebase-architecture` ever feel like they're missing context about your issue tracker, label vocabulary, or domain layout — that's the signal you skipped this step. Run `/setup-agent-skills` now.

### Re-running it

`/setup-agent-skills` is idempotent. Re-run it whenever you want to:

- Switch issue trackers (GitHub → GitLab → local markdown → other).
- Rename triage labels (e.g., to match a label scheme your repo already has).
- Move from single-context to multi-context (or vice versa).

It edits the existing `## Agent skills` block in place and rewrites `docs/agents/*.md`. Your hand-edits inside `docs/agents/*.md` are preserved when possible, but if you've done substantial customization there, diff before accepting the rewrite.

### Auto-bootstrap behavior

The kit provides a **two-layer auto-bootstrap** so a forgotten `/setup-agent-skills` doesn't lead to silent agent guessing (the runner layer is automatic; the interactive layer is one directive you opt into):

| Layer | Where | What it does |
| --- | --- | --- |
| **Interactive sessions** | The "First-run bootstrap" directive at the top of `AGENTS.md` (loaded into every Copilot CLI invocation — [add it yourself](#first-run-bootstrap-directive) to enable this layer) | If `docs/agents/issue-tracker.md` does not exist, the agent invokes `/setup-agent-skills` as its first action — **before** acting on the user's request — then returns to the original ask. |
| **AFK loop runner** | Preflight check in [`git-loopy/python/`](../git-loopy/python/) | If `docs/agents/issue-tracker.md` does not exist, the runner exits non-zero **before** the first iteration with a stderr message pointing the operator at `/setup-agent-skills`. Refuses to start because the skill is interactive and cannot safely run under `copilot --yolo -p`. |

The two layers compose: a human starts a fresh repo, runs `uv run --project git-loopy/python git-loopy`, gets a clear error, opens `copilot` interactively, and — if the [First-run bootstrap directive](#first-run-bootstrap-directive) is in their `AGENTS.md` — sees it auto-trigger `/setup-agent-skills`, answers the three questions, then re-runs the loop. Detection uses the existence of `docs/agents/issue-tracker.md` as the signal that the skill has run.

### First-run bootstrap directive

The interactive layer is opt-in: add this directive to the top of your `AGENTS.md` (just below the title) so it loads into every Copilot CLI invocation. The AFK-runner preflight layer above works without it — this only adds the interactive auto-trigger.

```markdown
> 🤖 **First-run bootstrap (read on every invocation).** If `docs/agents/issue-tracker.md` does **NOT** exist at the repo root, your very first action this session is to invoke `/setup-agent-skills` — **before any other work**, including the user's stated request. After it completes, return to whatever the user originally asked. If `docs/agents/issue-tracker.md` already exists, this bootstrap is satisfied; ignore this paragraph and proceed normally. The autonomous AFK loop runner (`git-loopy/python/`) refuses to start without this file, so if you are reading this directive from inside a `copilot --yolo -p` invocation, surface the inconsistency and stop.
```

## Skills reference

The kit ships with a curated subset of Copilot CLI skills, vendored under [`.copilot/skills/`](../.copilot/skills). The GitHub Copilot CLI marketplace has more skills beyond what's bundled here.

To discover more skills beyond what's vendored:

```bash
# From inside copilot:
> /find-skills <query>

# From the shell:
npx skills find <query>
```

For the breakdown of which skills the AFK loop will and won't invoke, see [`docs/runners.md` → Skill routing](runners.md#skill-routing).

## Stack-agnostic defaults

This kit doesn't care whether your project is Python, Node, Rust, Go, or something else. The single point of stack-specific configuration is the **Feedback loops** table in `AGENTS.md` — fill it in once with your project's lint / type-check / test / build commands, and both the human-driven skills and the AFK loop will read from it.

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

The AFK runner parses this exact table — the `## Feedback loops` heading, then the `Loop` and `Command` columns — and runs the runnable rows before landing work; rows still carrying a `<PLACEHOLDER>` command are skipped until you fill them in. Be specific: vague verbs like "run the tests" force agents to grep your package manifest and guess.

If you're on Azure or Microsoft tech, add **Azure conventions** and **Microsoft tooling** sections to `AGENTS.md` documenting the `SecurityControl=Ignore` tag and the `disableLocalAuth: false` default for Foundry resources. Otherwise skip them.

---

**Next:**
- [`docs/workflow.md`](workflow.md) — the seven-phase workflow these skills slot into.
- [`docs/runners.md`](runners.md) — the runner family and AFK loop reference; [`docs/wrapper-contract.md`](wrapper-contract.md) — the wrapper contract every runner implements.
- [`docs/concepts.md`](concepts.md) — the mental models behind the design.
- Back to [`README.md`](../README.md).
