# Skills Setup

> The full, hands-on walkthrough for installing and configuring the kit's Copilot CLI skills — from `git clone` to "skills installed, this repo configured, ready to grill." The [README Quick Start](../README.md#quick-start) is the condensed version; this doc is the detailed one, with what each step does, what to expect, and how to recover when something looks off.

Read this once when you adopt the kit into a new repo. After that you rarely come back — the workflow proper lives in [`docs/workflow.md`](workflow.md) and the runner in [`docs/runners.md`](runners.md).

---

## "Skills setup" is two separate things

The single most common point of confusion is treating "install the skills" and "configure the skills" as one step. They are not, and conflating them is why a skill will sometimes guess at the wrong issue tracker or label vocabulary.

| Step | Command | Scope | What it changes | Run how often |
| --- | --- | --- | --- | --- |
| **1. Install** the skills at the user level | `cp -R .copilot/skills/* ~/.copilot/skills/` | Your machine | Makes `/intake`, `/grill-me`, `/grill-with-docs`, `/to-prd`, `/to-issues`, `/triage`, `/diagnosing-bugs`, `/tdd`, `/improve-codebase-architecture`, `/find-skills`, `/setup-agent-skills`, etc. discoverable in **any** Copilot CLI session. Pure file copy — touches nothing in the repo. | Once per machine (or per kit upgrade) |
| **2. Configure** the skills for this repo | `/setup-agent-skills` (inside `copilot`) | This repo | Edits **this repo's** `AGENTS.md` `## Agent skills` block and writes **this repo's** `docs/agents/*.md`, telling the other skills which issue tracker, label vocabulary, and context layout this project uses. | Once per repo (re-run to change trackers/labels) |

Step 1 makes the commands _exist_. Step 2 makes them _correct for this project_. You must do both, in order, before any of the planning or implementation skills will behave.

---

## Prerequisites

- **[GitHub Copilot CLI](https://docs.github.com/copilot/github-copilot-in-the-cli)** installed and authenticated: `npm install -g @github/copilot`, then run `copilot` once to sign in.
- **[`gh`](https://cli.github.com/)** on `PATH` and signed in (`gh auth login`). The loop's default issue source is GitHub Issues.
- **`git`** on `PATH`.
- **A GitHub repository** for your project.
- **Python ≥ 3.11** and **[`uv`](https://docs.astral.sh/uv/)** (or `pip ≥ 24`) for the AFK runner ([`git-loopy/python/`](../git-loopy/python/)). Only needed once you reach the loop.

You can install the skills and start grilling before Python/`uv` are present; they are only required at [Phase 6 — the AFK loop](workflow.md#phase-6--afk-loop-git-loopypython).

---

## Part 1 — Install the kit and its skills

### 1.1 Clone the kit into a new project and reset history

The kit is scaffolding, not a dependency. Clone it, drop its git history, and start your own:

```bash
git clone https://github.com/bradcstevens/git-loopy my-project
cd my-project
rm -rf .git
git init && git add -A && git commit -m "Initial commit from starter kit"
```

### 1.2 Install the vendored skills at the user level

The kit vendors every skill the workflow routes to under [`.copilot/skills/`](../.copilot/skills). Copy them to your user skills directory so `/skillname` resolves in any session on this machine:

```bash
mkdir -p ~/.copilot/skills
cp -R .copilot/skills/* ~/.copilot/skills/
```

This is a **plain copy**. It does not read your repo, edit any file in it, or configure anything — that is [Part 2](#part-2--configure-this-repo-with-setup-agent-skills). Re-run this copy after pulling a newer version of the kit to pick up skill updates.

### 1.3 Verify the skills are discoverable

Launch Copilot CLI from the project root and open the slash-command menu:

```bash
copilot
> /
```

You should see the skills listed — `grill-me`, `grill-with-docs`, `to-prd`, `to-issues`, `triage`, `setup-agent-skills`, and the rest. If they're missing, the copy in 1.2 didn't land in `~/.copilot/skills/`; re-run it and relaunch `copilot`.

---

## Part 2 — Configure this repo with `/setup-agent-skills`

### Why this runs first

`/setup-agent-skills` is the **entry point** for skill configuration in a new repo. Run it **before** any of the planning or implementation skills. It does two things:

1. **Populates the `## Agent skills` block at the bottom of `AGENTS.md`** with concrete pointers to the per-repo config below.
2. **Writes `docs/agents/{issue-tracker,triage-labels,domain}.md`** — the per-repo config files every downstream skill reads to learn which issue tracker, label vocabulary, and context layout this project uses.

Skip it and `/to-issues`, `/triage`, `/to-prd`, `/diagnosing-bugs`, `/tdd`, and `/improve-codebase-architecture` will guess at the wrong defaults — the wrong `gh`/`glab` commands, the wrong label strings, the wrong context layout.

### Running it

From the project root:

```bash
copilot
> /setup-agent-skills
```

Answer the three questions it walks you through, one at a time.

### The three questions

| Decision | What it controls | Defaults |
| --- | --- | --- |
| **Issue tracker** | Whether downstream skills call `gh issue create`, `glab issue create`, write a markdown file under `.scratch/`, or follow custom prose. This is the "#1 FAQ" — you do not need a plugin for Jira/Linear/Beads; just tell the skill what you use and it adapts. | GitHub if a `git remote` points at GitHub, GitLab if it points at GitLab, local markdown if there's no remote, or "other" (free-form prose). |
| **Triage labels** | The exact strings `/triage` applies for each of the five canonical roles. | `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` — verbatim. Accept these unless your tracker already uses different names. |
| **Domain docs** | Whether the repo has one global `CONTEXT.md` or a `CONTEXT-MAP.md` pointing at per-context files. | Single-context — correct for ~99% of repos. Pick multi-context only if you genuinely have multiple bounded contexts (large monorepo). |

For the last two, **accepting the defaults is the right move** unless you have a concrete reason not to.

### What it writes

- **`AGENTS.md` → `## Agent skills` block** (at the bottom): pointers the other skills follow to find the config below. This block is owned by the skill — don't hand-edit it the first time around.
- **`docs/agents/issue-tracker.md`**: where issues live and, optionally, whether pull requests are a request surface (`PRs as a request surface: yes`).
- **`docs/agents/triage-labels.md`**: the label vocabulary `/triage` drives.
- **`docs/agents/domain.md`**: single- vs multi-context layout for `CONTEXT.md` / ADRs.

Specs and issues are saved wherever the issue-tracker answer points (GitHub Issues by default).

### Verifying setup completed

The signal that the skill has run is the existence of `docs/agents/issue-tracker.md`:

```bash
ls docs/agents/
# domain.md  issue-tracker.md  triage-labels.md
```

The AFK runner uses this exact file as its preflight check — see [The safety net](#the-safety-net-auto-bootstrap) below.

---

## Part 3 — Make `AGENTS.md` and `SPEC.md` yours

The kit ships its own `AGENTS.md` and `CONTEXT.md` at the repo root. `/setup-agent-skills` sets up the `## Agent skills` block and the `docs/agents/*` config for you; the rest of `AGENTS.md` is yours to make describe **your** project. There is no template to copy — the load-bearing structure is documented here and in [`docs/customization.md`](customization.md).

### `AGENTS.md`

Two sections are load-bearing:

- **Tech stack** — the technology choices an agent would otherwise have to guess (framework, package manager, test runner, lint/format tools, persistence, auth, infra). Anchor each line to a canonical source so the list never drifts.
- **Feedback loops** — a `## Feedback loops` table of the exact lint / type-check / test / build commands agents run before committing. **This is the single most important thing to get right:** the AFK runner parses this table, and wrong commands mean the agent guesses and CI catches the difference. The exact table structure is in [`docs/customization.md` → Stack-agnostic defaults](customization.md#stack-agnostic-defaults).

Optionally add the [First-run bootstrap directive](customization.md#first-run-bootstrap-directive) at the top so interactive sessions auto-trigger `/setup-agent-skills`. Leave the trailing `## Agent skills` block alone — `/setup-agent-skills` owns it.

### `SPEC.md`

`SPEC.md` is the brief `/to-prd` consumes — the canonical source for your domain language, scope, and decisions. You usually produce it by grilling (`/grill-me` → `/grill-with-docs`) rather than writing it cold, but its structure is:

| Section | What goes in it |
| --- | --- |
| **Problem Statement** | The problem from the user's perspective — actor, goal, why it's hard today, what's at stake. No implementation talk. |
| **Solution** | The user-facing solution in one paragraph — what the user does in the new world, and the one or two decisions the design forces. |
| **User Stories** | A long, numbered `As a <ACTOR>, I want <CAPABILITY> so that <BENEFIT>` list — exhaustive coverage of every actor × capability pair. |
| **Implementation Decisions** | Domain rules, roles, behavioral defaults, the deep modules to build, a logical schema sketch, and hosting/deployment topology. |
| **Testing Decisions** | Which module deserves exhaustive coverage (usually the deep pure one), what's tested through public interfaces, and what's out of scope. |
| **Out of Scope** | What the project deliberately isn't doing, one line each — pre-empts scope creep. |

### `git-loopy/PROMPT.md`

Usually leave the defaults; only touch it to change skill routing or commit-message conventions.

Deeper tailoring — repo structure, editing `PROMPT.md`, re-running `/setup-agent-skills`, the skills reference — lives in [`docs/customization.md`](customization.md).

You are now set up. From here, walk the [workflow](workflow.md): `/grill-me` → `/grill-with-docs` → `/to-prd` → `/to-issues` → `/triage` → the AFK loop.

---

## The safety net: auto-bootstrap

Forgetting `/setup-agent-skills` doesn't lead to silent guessing — the kit provides a **two-layer bootstrap** keyed off whether `docs/agents/issue-tracker.md` exists (the runner layer is automatic; the interactive layer is one directive you opt into):

| Layer | Where | What it does |
| --- | --- | --- |
| **Interactive sessions** | The optional "First-run bootstrap" directive in your `AGENTS.md` ([add it yourself](customization.md#first-run-bootstrap-directive)), loaded into every Copilot CLI invocation | If `docs/agents/issue-tracker.md` is missing, the agent invokes `/setup-agent-skills` as its **first** action — before acting on your request — then returns to what you asked. |
| **AFK loop runner** | Preflight check in [`git-loopy/python/`](../git-loopy/python/) | If `docs/agents/issue-tracker.md` is missing, the runner exits non-zero **before** the first iteration, with a stderr message pointing you at `/setup-agent-skills`. It refuses to start because the skill is interactive and can't safely run under `copilot --yolo -p`. |

The two compose cleanly: run `uv run --project git-loopy/python git-loopy` on a fresh repo, get a clear error, open `copilot` interactively (if you added the directive it auto-triggers `/setup-agent-skills`; otherwise run it by hand), answer the three questions, then re-run the loop.

---

## Greenfield note — grill before you document

If you just cloned this kit, you're on a greenfield project, and the temptation is to jump straight to `/grill-with-docs` because "vocabulary is most malleable early." **Don't.** Use `/grill-me` first, until three or four terms keep recurring. Defining a glossary for entities that don't exist yet front-loads ossification — a premature glossary is the language version of premature optimization. Once a shape emerges and the same terms keep coming up, switch to `/grill-with-docs` to codify them into `CONTEXT.md`. Until then, the `CONTEXT.md` stub at the repo root is fine to leave alone. The full decision tree is in [`docs/workflow.md`](workflow.md#grill-me-vs-grill-with-docs--pick-the-right-one).

---

## Troubleshooting / FAQ

**A skill feels like it's missing context about my issue tracker, labels, or domain.**
That's the signal you skipped Part 2. Run `/setup-agent-skills` now.

**The AFK loop exits immediately with a preflight error.**
`docs/agents/issue-tracker.md` doesn't exist yet — `/setup-agent-skills` hasn't run for this repo. Open `copilot` interactively and run `/setup-agent-skills` (if you added the First-run bootstrap directive, it auto-triggers), then re-run the loop.

**`/setup-agent-skills` (or any `/skillname`) isn't recognized.**
The user-level install in 1.2 didn't land. Re-run `mkdir -p ~/.copilot/skills && cp -R .copilot/skills/* ~/.copilot/skills/` and relaunch `copilot`.

**I want to switch issue trackers, rename labels, or move to multi-context.**
`/setup-agent-skills` is idempotent — re-run it. It edits the `## Agent skills` block in place and rewrites `docs/agents/*.md`. If you've hand-edited those files substantially, diff before accepting the rewrite.

**Which issue trackers are supported?**
GitHub, GitLab, local markdown, or "other" (free-form). There's no plugin to hunt for — say what you use during setup and the skill adapts. More detail lives in [`docs/customization.md`](customization.md#setup-agent-skills--the-entry-point-skill).

**How do I discover skills beyond what's vendored?**
`/find-skills <query>` from inside `copilot`, or `npx skills find <query>` from the shell. See [`docs/customization.md` → Skills reference](customization.md#skills-reference).

---

**Next:**
- [`docs/workflow.md`](workflow.md) — the seven-phase workflow you just got set up for.
- [`docs/customization.md`](customization.md) — deeper tailoring of `AGENTS.md`, `PROMPT.md`, and the per-repo skill config.
- [`docs/runners.md`](runners.md) — the AFK loop's invocation and contract.
- Back to [`README.md`](../README.md).
