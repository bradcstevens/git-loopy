# Skills Setup

> Install git-loopy's pinned Copilot CLI skill catalog, configure it for a
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
| **1. Install** the workflow skill catalog | `git-loopy init --project` or `git-loopy init --global` | Your machine, in git-loopy's config home | Installs the pinned workflow skill catalog so `/grill-with-docs`, `/wayfinder`, `/research`, `/to-spec`, `/to-tickets`, `/triage`, `/implement`, `/tdd`, `/code-review`, and the rest are discoverable. | Once per machine; every Run refreshes it against the pin |
| **2. Configure** the skills for this repo | `/setup-agent-skills` (inside `copilot`) | This repo | Edits **this repo's** `AGENTS.md` `## Agent skills` block and writes **this repo's** `docs/agents/*.md`, telling the other skills which issue tracker, label vocabulary, and context layout this project uses. | Once per repo (re-run to change trackers/labels) |

Step 1 makes the commands _exist_. Step 2 makes them _correct for this project_. You must do both, in order, before any of the planning or implementation skills will behave.

---

## Prerequisites

- **[GitHub Copilot CLI](https://docs.github.com/copilot/github-copilot-in-the-cli)** installed and authenticated: `npm install -g @github/copilot`, then run `copilot` once to sign in.
- **[`gh`](https://cli.github.com/)** on `PATH` and signed in (`gh auth login`). The loop's default issue source is GitHub Issues.
- **`git`** on `PATH`.
- **A GitHub repository** for your project.
- **Python ≥ 3.11** and **[`uv`](https://docs.astral.sh/uv/)** (or `pip ≥ 24`) for the recommended `git-loopy init` install and the Python reference Orchestrator ([`git-loopy/python/`](../git-loopy/python/)).

If Python or `uv` is not present yet, install the skills into Copilot CLI
directly with the catalog's own [`skills` CLI](#13-also-give-copilot-cli-the-slash-commands)
and start planning before the
[execution phase](workflow.md#execution-phase-autonomous). `git-loopy init` is
still required before the execution phase, because only it installs the catalog
a Run reads.

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

### 1.2 Recommended: install the workflow skill catalog with `git-loopy init`

Install the [`git-loopy` command](../git-loopy/python/README.md#install-run-from-anywhere),
then run setup for the scope whose Config you want to establish:

```bash
# Recommended for one repository.
git-loopy init --project

# User-level equivalent: configure every repository.
git-loopy init --global
```

Setup's **first** act is to install the workflow skill catalog, before it
collects anything from you. It clones
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
at the revision git-loopy pins and writes the Skills into git-loopy's own config
home — `$XDG_CONFIG_HOME/git-loopy/skills/`, or `~/.config/git-loopy/skills/`
when that variable is unset. This is machine-wide and scope-independent: both
commands above install to the same place, and the `--project` / `--global`
choice only decides which Config the rest of setup writes. Every subsequent Run
re-checks that install against the pin and repairs it, so the catalog cannot
drift (see [ADR-0025](adr/0025-installed-skill-catalog.md)).

That install is the only Skill source git-loopy provides for itself. It no
longer copies Skills into `./.copilot/skills/` or `~/.copilot/skills/`, and it
no longer reads the repository's `./.copilot/skills/` at all — a repository
cannot hand a Run a Skill by committing one. Copilot CLI's own sources (your
personal, plugin, built-in, and custom Skills, including `~/.copilot/skills/`)
are still in the catalog, still bounded by the Skill policy below, and lose any
name the installed catalog also provides. See
[§1.3](#13-also-give-copilot-cli-the-slash-commands) if you want these commands
available directly in Copilot CLI. Setup still
offers to scaffold an editable `PROMPT.md` override; accept it if you want to
tune the Run instructions. This installs the commands but does not configure
their issue tracker, labels, or domain layout - that is
[Part 2](#part-2--configure-this-repo-with-setup-agent-skills).

Interactive `init` also establishes the scope's **Skill policy** — the
closed-world set of Skills a Run may expose — through the same searchable
picker as [`git-loopy skills edit`](../git-loopy/python/README.md). It is seeded
from an existing lower-scope git-loopy policy, or, when none exists, from a
fresh Copilot Skill baseline; Required Skills cannot be saved disabled, and an
enabled project Skill that is not git-tracked blocks the save. `git-loopy init
--yes` persists the **Minimal Skill policy** — exactly the Required Skills —
without contacting the machine's Copilot inventory, which keeps a first CI setup
reproducible. Change a saved policy later with `git-loopy skills edit`.

Setup is where a policy is *established*; everything else about operating one —
the Config surfaces, the `git-loopy skills` commands, the resolved-policy audit
event, the Python-first family transition, and every preflight failure with its
recovery command — is in [`docs/skill-policy.md`](skill-policy.md).

#### The Skill picker has two renderings, and one set of rules

Every path that asks you to choose Skills — `init`, `skills edit`, and the
one-time legacy migration below — opens the *same* picker over the *same*
selection state. Only the drawing differs, and git-loopy picks the drawing for
you:

| | Full-screen picker | Plain picker |
| --- | --- | --- |
| Used when | stdout is a terminal **and** the `[tui]` extra is installed | anywhere else — a pipe, CI, `--no-interactive`, or no `[tui]` extra |
| Search | type to filter, live | type the text, then Enter |
| Toggle | `Space` on the highlighted row | the row's number |
| Clear the filter | delete the search text | an empty line |
| Move | `Up` / `Down` | — the list is numbered |
| Save | `Enter` | `done`, then `y` at the confirmation |
| Cancel | `Esc` or `Ctrl+C` | `q` |

The full-screen picker is **optional, never required**. `pip install
'git-loopy[tui]'` (or `uv sync --extra tui`) enables it; without the extra the
plain picker runs and nothing is lost — the two are interchangeable and return
the same selection. git-loopy probes for the extra without importing it, so a
base install never pays for a dependency it does not have.

Both renderings obey identical rules, because both read one shared model:

- **Filtering never changes a selection.** Skills you enabled that the current
  search hides stay enabled and are saved. Canonical Skill names never contain a
  space, which is exactly why `Space` is free to mean "toggle" while you type.
- **Required Skills are marked and locked on.** Switching one off is refused in
  place, with the reason shown; it is not a save-time surprise.
- **An untracked project Skill is visible but locked off.** It stays listed —
  with the reason — so you can see what would need committing, rather than
  vanishing from a catalog you are trying to reason about.
- **Save is refused, not silently corrected**, whenever the selection would not
  validate; the refusal names the offending Skill.

Every refusal is shown where you are about to type: the full-screen picker
updates its status bar, and the plain picker redraws the reason *below* the
list, on the line above the prompt — so a long catalog cannot scroll away the
one line that explains why the last answer changed nothing.

Copilot's own enabled state has no authority over a saved Skill policy — once
established, the policy changes only through an explicit git-loopy action. When
you *do* want to re-import it, `git-loopy skills sync` is that explicit action:

```bash
# Show what importing Copilot's current Skill baseline would change, then confirm.
git-loopy skills sync            # project scope inside a repository, else global
git-loopy skills sync --global
```

Sync prints the exact additions and removals before writing anything and saves
only after you confirm. When the scope has no policy of its own — it inherits a
lower-scope one, or falls back to the unconfigured default — there is nothing
for the baseline to already match, so sync says so and offers to save the whole
baseline as that scope's first policy. It replaces only the Skills Copilot
actually reports:
Skills from git-loopy's installed catalog — and any configured name Copilot does
not know — keep their current state rather than being synced away. The proposed
policy is validated first, so a sync that would disable a Required Skill or
leave an unresolvable name enabled fails without touching the Config. Cancelling writes nothing, and no path ever writes back to
Copilot's own settings.

#### Upgrading a Config written before Skill policies existed

A Config that predates the closed-world Skill policy carries no
`enabled_skills` key. git-loopy will not guess one for it: an absent key is not
"expose everything", it resolves to the **Minimal Skill policy** — the Required
Skills and nothing else — so an unmigrated Config keeps running, just with a
narrower surface than its author expected.

The first interactive `git-loopy` run on such a Config offers a one-time
migration through the same picker as `skills edit`, seeded from your current
Copilot Skill baseline with any Skill Copilot reports **disabled** left
unchecked. It is offered once, before any work starts, and writes the scope a
Run actually resolves — project when the repository carries Config, otherwise
global:

```text
git-loopy: this project Config predates the closed-world Skill policy. Choose
the Skills this installation may load; the selection is saved once and never
asked again.
```

- Confirming saves the selection and the Run continues on the policy you just
  established; later Runs see a configured Config and are never asked again.
- Cancelling writes nothing **and starts nothing** — the Run exits non-zero so
  no Iteration silently proceeds on a policy you declined to choose.
- Without a TTY, or with `--no-interactive`, nothing is prompted or persisted:
  the Run proceeds on the Minimal Skill policy and prints a warning naming
  `git-loopy skills edit` as the fix. Automation therefore never blocks.
- A Config with `enabled_skills = []` is a real policy — a deliberately empty
  one — not a legacy Config, and is never offered for migration.
- `GIT_LOOPY_ENABLED_SKILLS` replaces the policy for that Run outright, so it
  also suppresses the offer. `--enable-skill` is a temporary overlay on top of
  the base policy and does not, so the underlying Config is still offered for
  migration.

### 1.3 Also: give Copilot CLI the slash commands

git-loopy's install is for git-loopy's *own* Runs. Copilot CLI reads a
different set of directories, so a slash command you want to type yourself has
to be installed into Copilot separately. Install it from the same source of
record git-loopy pins, using that catalog's own [`skills`
CLI](https://www.skills.sh):

```bash
# Every skill in the catalog, for your user, into GitHub Copilot CLI.
npx skills add bradcstevens/git-loopy-skills -g -a github-copilot

# Or just the one you want.
npx skills add bradcstevens/git-loopy-skills --skill=next -g -a github-copilot

# See what the catalog carries without installing anything.
npx skills add bradcstevens/git-loopy-skills --list
```

To pick up later catalog changes:

```bash
npx skills update
```

This is the **supported install and update flow for Copilot CLI**, and it is
independent of `git-loopy init` in both directions:

| | `git-loopy init` | `npx skills add` |
| --- | --- | --- |
| Installs into | `<config-home>/git-loopy/skills/` | Copilot CLI's own directories (`-g` for your user) |
| Read by | **git-loopy Runs** | **you**, typing a slash command in `copilot` |
| Tracks | the revision **this git-loopy pins** | the catalog's current tip, until you `npx skills update` |
| Required? | yes — a Run has no other Skill source | no — skipping it changes nothing about what a Run can do |

Because the two track different revisions, they can differ: git-loopy moves its
pin on its own schedule, and `npx skills update` moves yours. That is
deliberate — a Run's Skills are answerable from git-loopy's pin alone, whatever
you have installed for yourself. Drop `-g` to install into the current project
instead; git-loopy neither writes to nor reads from a project Skill tree, so
that choice is entirely between you and Copilot CLI.

See [`docs/skill-catalog-source.md`](skill-catalog-source.md) if you maintain
the catalog or need to audit which revision a Run installed.

### 1.4 Verify the skills are discoverable

Confirm git-loopy resolved the catalog it just installed:

```bash
git-loopy skills
```

You should see `code-review`, `implement`, `tdd`, `setup-agent-skills`, and the
rest, each attributed to the installed catalog. If the list is empty, re-run
`git-loopy init` and read the install line it prints — an unreachable network on
a machine that has never installed the catalog is the one case setup cannot
recover from on its own.

If you also did [§1.3](#13-also-give-copilot-cli-the-slash-commands), launch
Copilot CLI from the project root and open the slash-command menu to check the
copy landed:

```bash
copilot
> /
```

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
The catalog install in Part 1 did not land in the intended scope. Re-run
`git-loopy init --project` or `git-loopy init --global` and relaunch
`copilot`. Note that `git-loopy init` installs the catalog **git-loopy** reads;
a slash command you type yourself comes from Copilot CLI's own sources, so
install it there too with
[`npx skills add`](#13-also-give-copilot-cli-the-slash-commands).

**I want to switch issue trackers, rename labels, or move to multi-context.**
`/setup-agent-skills` is idempotent — re-run it. It edits the `## Agent skills` block in place and rewrites `docs/agents/*.md`. If you've hand-edited those files substantially, diff before accepting the rewrite.

**Which issue trackers are supported?**
GitHub, GitLab, local markdown, or "other" (free-form). There's no plugin to hunt for — say what you use during setup and the skill adapts. More detail lives in [`docs/customization.md`](customization.md#setup-agent-skills--the-entry-point-skill).

**How do I discover skills beyond the installed catalog?**
`npx skills find <query>` from the shell. See [`docs/customization.md` → Skills reference](customization.md#skills-reference).

---

**Next:**
- [`docs/workflow.md`](workflow.md) — the complete planning-to-review workflow.
- [`docs/skill-policy.md`](skill-policy.md) — operating the closed-world Skill policy: Config surfaces, `git-loopy skills`, migration, audit, and troubleshooting.
- [`docs/customization.md`](customization.md) — deeper tailoring of `AGENTS.md`, `PROMPT.md`, and the per-repo skill config.
- [`docs/runners.md`](runners.md) — the Runner family, invocation, and contract.
- Back to [`README.md`](../README.md).
