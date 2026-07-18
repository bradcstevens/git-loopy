# Skills Setup

> Install git-loopy's vendored Copilot CLI skills, configure them for a
> repository, and prepare the human-led planning phase of a loop-engineering
> workflow.

Read this once when you adopt git-loopy in a repository. The complete
planning-to-review path lives in [`docs/workflow.md`](workflow.md), and the
autonomous execution reference lives in [`docs/runners.md`](runners.md).

---

## "Skills setup" is two separate things

The single most common point of confusion is treating "install the skills" and "configure the skills" as one step. They are not, and conflating them is why a skill will sometimes guess at the wrong issue tracker or label vocabulary.

| Step | Command | Scope | What it changes | Run how often |
| --- | --- | --- | --- | --- |
| **1. Install** the skills at the user level | `cp -R .copilot/skills/* ~/.copilot/skills/` | Your machine | Makes `/intake`, `/grill-with-docs`, `/wayfinder`, `/research`, `/to-spec`, `/to-tickets`, `/triage`, `/implement`, `/tdd`, `/code-review`, and the rest discoverable in **any** Copilot CLI session. The copy touches nothing in the target repo. | Once per machine (or per git-loopy upgrade) |
| **2. Configure** the skills for this repo | `/setup-agent-skills` (inside `copilot`) | This repo | Edits **this repo's** `AGENTS.md` `## Agent skills` block and writes **this repo's** `docs/agents/*.md`, telling the other skills which issue tracker, label vocabulary, and context layout this project uses. | Once per repo (re-run to change trackers/labels) |

Step 1 makes the commands _exist_. Step 2 makes them _correct for this project_. You must do both, in order, before any of the planning or implementation skills will behave.

---

## Prerequisites

- **[GitHub Copilot CLI](https://docs.github.com/copilot/github-copilot-in-the-cli)** installed and authenticated: `npm install -g @github/copilot`, then run `copilot` once to sign in.
- **[`gh`](https://cli.github.com/)** on `PATH` and signed in (`gh auth login`). The loop's default issue source is GitHub Issues.
- **`git`** on `PATH`.
- **A GitHub repository** for your project.
- **Python ≥ 3.11** and **[`uv`](https://docs.astral.sh/uv/)** (or `pip ≥ 24`) for the Python reference Orchestrator ([`git-loopy/python/`](../git-loopy/python/)). They are only needed once you start autonomous execution.

You can install the skills and start planning before Python or `uv` is present;
they are required for the [execution phase](workflow.md#execution-phase-autonomous).

---

## Part 1 — Install git-loopy and its skills

### 1.1 Clone git-loopy into a new project and reset history

When using the repository as a project scaffold, clone it, drop its history,
and start your own:

```bash
git clone https://github.com/bradcstevens/git-loopy my-project
cd my-project
rm -rf .git
git init && git add -A && git commit -m "Initialize project from git-loopy"
```

### 1.2 Install the vendored skills at the user level

git-loopy vendors the complete skill catalog used by the workflow under
[`.copilot/skills/`](../.copilot/skills). Copy it to your user skills directory
so `/skillname` resolves in any session on this machine:

```bash
mkdir -p ~/.copilot/skills
cp -R .copilot/skills/* ~/.copilot/skills/
```

This is a **plain copy**. It does not read your repo, edit any file in it, or
configure anything - that is [Part 2](#part-2--configure-this-repo-with-setup-agent-skills).
Repeat the copy after updating git-loopy to pick up skill changes.

### 1.3 Verify the skills are discoverable

Launch Copilot CLI from the project root and open the slash-command menu:

```bash
copilot
> /
```

You should see `grill-me`, `grill-with-docs`, `wayfinder`, `to-spec`,
`to-tickets`, `triage`, `implement`, `setup-agent-skills`, and the rest. If
they are missing, the copy in 1.2 did not land in `~/.copilot/skills/`; repeat
it and relaunch `copilot`.

---

## Part 2 — Configure this repo with `/setup-agent-skills`

### Why this runs first

`/setup-agent-skills` is the **entry point** for skill configuration in a new repo. Run it **before** any of the planning or implementation skills. It does two things:

1. **Populates the `## Agent skills` block at the bottom of `AGENTS.md`** with concrete pointers to the per-repo config below.
2. **Writes `docs/agents/{issue-tracker,triage-labels,domain}.md`** — the per-repo config files every downstream skill reads to learn which issue tracker, label vocabulary, and context layout this project uses.

Skip it and `/to-spec`, `/to-tickets`, `/triage`, `/wayfinder`,
`/diagnosing-bugs`, `/tdd`, and `/codebase-design` may guess at the wrong
tracker, label strings, or domain layout.

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

The Python Orchestrator uses this exact file as its preflight check - see
[The safety net](#the-safety-net-auto-bootstrap) below.

---

## Part 3 — Make `AGENTS.md` and the domain docs yours

git-loopy includes `AGENTS.md` and `CONTEXT.md` at the repo root.
`/setup-agent-skills` configures the `## Agent skills` block and
`docs/agents/*`; make the rest describe **your** project. The load-bearing
structure is documented here and in [`docs/customization.md`](customization.md).

### `AGENTS.md`

Two sections are load-bearing:

- **Tech stack** — the technology choices an agent would otherwise have to guess (framework, package manager, test runner, lint/format tools, persistence, auth, infra). Anchor each line to a canonical source so the list never drifts.
- **Feedback loops** — a `## Feedback loops` table of the exact lint / type-check / test / build commands agents run before committing. **This is the single most important thing to get right:** autonomous Iterations need fast, deterministic feedback rather than guesses. The exact table structure is in [`docs/customization.md` → Stack-agnostic defaults](customization.md#stack-agnostic-defaults).

Optionally add the [First-run bootstrap directive](customization.md#first-run-bootstrap-directive) at the top so interactive sessions auto-trigger `/setup-agent-skills`. Leave the trailing `## Agent skills` block alone — `/setup-agent-skills` owns it.

### `CONTEXT.md`, ADRs, and specs

Use `/grill-with-docs` to sharpen the repository's shared language in
`CONTEXT.md` and record consequential decisions under `docs/adr/`. Once the
planning context has reached shared understanding, `/to-spec` synthesizes that
discussion and publishes the destination to the configured issue tracker. A
spec carries the problem, solution, user stories, implementation and testing
decisions, and explicit exclusions; `/to-tickets` then turns it into the route.

### `git-loopy/PROMPT.md`

Usually leave the defaults; only touch it to change skill routing or commit-message conventions.

Deeper tailoring — repo structure, editing `PROMPT.md`, re-running `/setup-agent-skills`, the skills reference — lives in [`docs/customization.md`](customization.md).

You are now set up. From here, walk the [workflow](workflow.md):
`/grill-with-docs` (or `/wayfinder`) -> `/to-spec` -> `/to-tickets` ->
`/triage` -> a git-loopy Run -> human review.

---

## The safety net: auto-bootstrap

Forgetting `/setup-agent-skills` does not lead to silent guessing. git-loopy
uses a **two-layer bootstrap** keyed off whether
`docs/agents/issue-tracker.md` exists:

| Layer | Where | What it does |
| --- | --- | --- |
| **Interactive sessions** | The optional "First-run bootstrap" directive in your `AGENTS.md` ([add it yourself](customization.md#first-run-bootstrap-directive)), loaded into every Copilot CLI invocation | If `docs/agents/issue-tracker.md` is missing, the agent invokes `/setup-agent-skills` as its **first** action — before acting on your request — then returns to what you asked. |
| **Autonomous Run** | Preflight check in [`git-loopy/python/`](../git-loopy/python/) | If `docs/agents/issue-tracker.md` is missing, the Orchestrator exits non-zero **before** the first Iteration, with a stderr message pointing you at `/setup-agent-skills`. The skill is interactive and cannot safely run inside the autonomous agent session. |

The two compose cleanly: run `uv run --project git-loopy/python git-loopy` on a fresh repo, get a clear error, open `copilot` interactively (if you added the directive it auto-triggers `/setup-agent-skills`; otherwise run it by hand), answer the three questions, then re-run the loop.

---

## Greenfield note — grill before you document

On a greenfield project, the temptation is to jump straight to
`/grill-with-docs` because vocabulary is most malleable early. Do not define a
glossary for entities that do not exist yet. Use `/grill-me` until three or four
terms recur, then switch to `/grill-with-docs` to codify them in `CONTEXT.md`.
The decision guide is in
[`docs/workflow.md`](workflow.md#grill-me-or-grill-with-docs-pick-the-right-one).

---

## Troubleshooting / FAQ

**A skill feels like it's missing context about my issue tracker, labels, or domain.**
That's the signal you skipped Part 2. Run `/setup-agent-skills` now.

**The git-loopy Run exits immediately with a preflight error.**
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
- [`docs/workflow.md`](workflow.md) — the complete planning-to-review workflow.
- [`docs/customization.md`](customization.md) — deeper tailoring of `AGENTS.md`, `PROMPT.md`, and the per-repo skill config.
- [`docs/runners.md`](runners.md) — the Runner family, invocation, and contract.
- Back to [`README.md`](../README.md).
