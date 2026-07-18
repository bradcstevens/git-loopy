# GitHub Copilot Ralph Starter Kit

A starter kit for running an **AFK (away-from-keyboard) AI coding loop** on the **GitHub Copilot CLI**. Drop it into a new repo, fill in `AGENTS.md`, point the loop at a kanban of triaged GitHub Issues, and let an agent implement them autonomously while you do something else.

**What you get:**

- A **Python AFK runner** on the GitHub Copilot Python SDK — [`git-loopy/python/`](git-loopy/python/). The reference implementation of git-loopy's [**runner family**](docs/adr/0013-multi-language-runner-family.md); shell + PowerShell ports for Linux/macOS/Windows are on the roadmap.
- A **vendored copy of every Copilot CLI skill** the workflow routes to — [`.copilot/skills/`](.copilot/skills).

Stack-agnostic: customize one **Feedback loops** table in `AGENTS.md` and the rest of the kit follows.

---

## Prerequisites

- [GitHub Copilot CLI](https://docs.github.com/copilot/github-copilot-in-the-cli) installed and signed in (`npm install -g @github/copilot`, then run `copilot` once).
- [`gh`](https://cli.github.com/) and `git` on `PATH`; `gh` signed in (`gh auth login`).
- A GitHub repository for your project (the loop's default issue source).
- Python **>= 3.11** and [`uv`](https://docs.astral.sh/uv/) (or `pip >= 24`) — only needed once you reach the AFK loop, and only for the Python reference runner (the planned shell/PowerShell ports will need no Python; see [ADR-0013](docs/adr/0013-multi-language-runner-family.md)).

Detailed prerequisites are in [`docs/skills-setup.md`](docs/skills-setup.md#prerequisites).

---

## Quick Start

From `git clone` to running the AFK loop. Every step below has a detailed walkthrough in [`docs/skills-setup.md`](docs/skills-setup.md).

```bash
# 1. Clone the kit into a new project and reset git history.
git clone https://github.com/bradcstevens/git-loopy my-project
cd my-project
rm -rf .git && git init && git add -A && git commit -m "Initial commit from starter kit"

# 2. Install the vendored skills at the user level (once per machine).
mkdir -p ~/.copilot/skills
cp -R .copilot/skills/* ~/.copilot/skills/

# 3. Configure this repo — run /setup-agent-skills FIRST, before any other skill.
#    It writes docs/agents/{issue-tracker,triage-labels,domain}.md and the
#    AGENTS.md `## Agent skills` block that every downstream skill reads.
copilot
> /setup-agent-skills

# 4. Make AGENTS.md describe YOUR project: fill in the Tech stack and the
#    load-bearing Feedback loops table, then capture your brief in SPEC.md.
#    docs/customization.md has the AGENTS.md structure; docs/skills-setup.md the full walkthrough.
```

Then walk the skills workflow, inside `copilot`, up to the loop:

```text
/grill-me          # align on the change (greenfield: start here)
/grill-with-docs   # once vocabulary stabilises, compile CONTEXT.md + docs/adr/
/to-prd            # publish the brief as the parent PRD issue
/to-issues         # slice the PRD into vertical-slice issues
/triage            # label ready-for-agent work for the loop
```

Finally, kick off the autonomous AFK loop and walk away:

```bash
uv run --project git-loopy/python git-loopy        # unlimited iterations
uv run --project git-loopy/python git-loopy 50     # cap at 50 iterations
```

> **Run `/setup-agent-skills` first.** Installing the skills (step 2) only makes the commands _exist_; `/setup-agent-skills` (step 3) makes them _correct for this repo_ — it writes your issue tracker, labels, and context layout, and sets up the `AGENTS.md` `## Agent skills` block. Skip it and the planning skills guess — though the AFK runner refuses to start without it and interactive sessions auto-trigger it. Full walkthrough: [`docs/skills-setup.md`](docs/skills-setup.md).

You don't need every phase — the skills are independent, so pick what helps. The end-to-end workflow is documented in [`docs/workflow.md`](docs/workflow.md).

---

## Where to go next

| Doc | Read when... |
| --- | --- |
| [`docs/skills-setup.md`](docs/skills-setup.md) | You're **adopting the kit** and want the detailed install + `/setup-agent-skills` walkthrough, with verification and troubleshooting. |
| [`docs/concepts.md`](docs/concepts.md) | You want to understand **why** the workflow is shaped this way — the Smart Zone and Memento Model the kit is built around. |
| [`docs/workflow.md`](docs/workflow.md) | You're ready to walk the **end-to-end workflow** (Idea -> Intake -> Grill -> Brief -> PRD -> Issues -> Triage -> AFK loop -> QA), including the `/grill-me` vs `/grill-with-docs` decision. |
| [`docs/runners.md`](docs/runners.md) | You need the **runner reference** — invocation, per-iteration flow, exit conditions, commit-message contract, and skill routing. |
| [`docs/customization.md`](docs/customization.md) | You need to **tailor the kit** — repo structure, `AGENTS.md`/`PROMPT.md`, re-running `/setup-agent-skills`, and the skills reference. |
| [`git-loopy/python/README.md`](git-loopy/python/README.md) | You want the runner's **bootstrap, env-var surface, observability artefacts, and OpenTelemetry tracing**. |

First-time reading order: [`docs/skills-setup.md`](docs/skills-setup.md) -> [`docs/concepts.md`](docs/concepts.md) -> [`docs/workflow.md`](docs/workflow.md) -> [`docs/runners.md`](docs/runners.md) -> [`docs/customization.md`](docs/customization.md) on demand.

---

## License

MIT — see [`LICENSE`](LICENSE).
