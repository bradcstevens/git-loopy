# Agent Instructions

This file provides instructions and conventions for AI coding agents (GitHub Copilot
CLI, Claude Code, etc.) working in this repository.

## Tech stack

git-loopy is a **Runner family**: one Wrapper contract implemented by several
members, each in its own language, all pinned against the shared Conformance
fixtures in `git-loopy/conformance/`.

| Member | Location | Toolchain |
| --- | --- | --- |
| Python Runner (reference Orchestrator) | `git-loopy/python/` | Python 3.11+ driven by `uv` |
| shell Orchestrator | `git-loopy/shell/` | Bash 4+ (`bash` from `PATH`, not `/bin/bash`) and `jq` |
| PowerShell Orchestrator | `git-loopy/powershell/` | PowerShell 7+ (`pwsh`) |
| Rust Dashboard core | `git-loopy/tui/` | Stable Rust with `rustfmt` and `clippy` |

## Feedback loops

Run the loops below that your change touches before committing. **Integration** also
runs every one of them, top to bottom and fail-fast, over the merged worktree
(ADR-0009, `git_loopy.gate.AgentsMdGateRunner`) — so this table is not documentation
about the gate, it *is* the gate's input. A repository that declares no runnable loop
here cannot be gated at all, and every Lane merge comes back red regardless of what
it contains.

| Loop | Command | When to run |
| --- | --- | --- |
| Shell syntax | `bash -n git-loopy/shell/git-loopy.sh git-loopy/shell/install.sh git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh` | Any change under `git-loopy/shell/` |
| Python suite | `uv run --project git-loopy/python --all-extras python -m pytest -q git-loopy/python/tests` | Any change to the Python Runner, the Conformance fixtures, `PROMPT.md`, the Skill catalog, or the CI workflows |
| Rust Dashboard core | `cargo fmt --manifest-path git-loopy/tui/Cargo.toml --check && cargo clippy --manifest-path git-loopy/tui/Cargo.toml --all-targets -- -D warnings && cargo test --manifest-path git-loopy/tui/Cargo.toml` | Any change under `git-loopy/tui/` or to the Event schema the Dashboard reads |
| Shell Orchestrator suites | `for t in git-loopy/shell/tests/test-*.sh; do bash "$t" \|\| exit 1; done` | Any change under `git-loopy/shell/` or to a Conformance fixture |
| PowerShell Orchestrator suites | `for t in git-loopy/powershell/tests/test-*.ps1; do pwsh -NoLogo -NoProfile -File "$t" \|\| exit 1; done` | Any change under `git-loopy/powershell/` or to a Conformance fixture |

Use `uv run --project git-loopy/python --all-extras python git-loopy/python/targeted_tests.py` to select targeted tests; eligibility, run options and full-suite backstop rules live in [Feedback-loop rules](docs/agents/feedback-loops.md#targeted-python-tests).

For maintainer rationale on whether a command should become a gate row, see [Feedback-loop exclusions](docs/agents/feedback-loops.md).

Commands resolve their tools through `PATH` and are relative to the repository root,
because Integration runs them in a throwaway private worktree on whatever host the
operator has. Never hard-code a host-specific executable path here.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (managed via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

All five canonical triage roles use their default label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Repository workflow

When writing an ADR, run `uv run --project git-loopy/python --all-extras python -m git_loopy.adr_number` from the repository root for the next free number and its remote evidence.

Agent skills come from
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills),
the source of record. `git-loopy init` installs it at the revision this
repository pins (`git-loopy/python/git_loopy/skill_source.json`) into
`<config-home>/git-loopy/skills/`, and every Run refreshes that install — which
is the only Skill source a Run reads (ADR-0025). To type the same commands
yourself in `copilot`, install them into Copilot CLI as well:
`npx skills add bradcstevens/git-loopy-skills -g -a github-copilot`.

The same pin supplies seven **Agent profiles**, installed and repaired with
the catalog. Task type selects one of five execution profiles
(`implementation`, `bugfix`, `test`, `docs`, `chore`); unresolved types fall
back to `implementation`. The read-only Reviewer profile supplies the separate
review role; automatic Review-stage orchestration is separate delivery work.
Profiles own Required and Permitted Skills; `PROMPT.md` is their shared Run
base, and its retired `required-skills` key is refused. Every in-Run Agent and
its Subagents are isolated from persistent memory and cross-session history.
For exposure rules, the three-Skill Minimal policy, and isolation, read
[`docs/skill-policy.md`](docs/skill-policy.md#profiles-and-exposure).

For human-led planning, use `git-loopy plan [planning document]`. It launches
the pinned Planner in your interactive Copilot CLI with `/to-tickets`, outside
every Run; the graph remains a proposal until the Loop engineer approves it.
For Routing overrides, harness requirements, and launch refusals, read the
[Planner reference](git-loopy/python/README.md#planning-git-loopy-plan).

When changing a Skill or Agent profile, follow [Skills repository guidance](docs/agents/skills-repository.md).

### Chain hook

For `/next` hook mechanics and setup ownership, see [Chain hooks](docs/agents/chain-hooks.md).

### Planning documents and Task types

Issues whose titles begin with `PRD:` or `Spec:` (case-insensitive), and issues
labelled `wayfinder:map`, are planning documents, not executable tickets. Never
apply `ready-for-agent` to them; remove that label if present. Work their
implementation tickets instead.

Only a human asserts `task-type:planning` or `task-type:review`. A
`ready-for-agent` issue carrying either exact label is excluded as
`non_execution_task_type`, even alongside an execution Task type, until a human
retypes it to an execution Task type or removes `ready-for-agent` and hands it
to the Planner. The Runner never relabels it, and `git-loopy labels` neither
reports nor repairs this conflict.

### Release milestones

A `vX.Y.Z` GitHub milestone is solely the **Promotion** trigger: closing it
promotes the current matching `-alpha.N`/`-beta.N`/`-rc.N` Release line to stable.
The Release target is instead ratcheted from closed issues' `vX.Y.Z` Release-target
labels (ADR-0066); an issue with no such label changes no version. Never invent a
milestone — `gh api repos/{owner}/{repo}/milestones --jq '.[].title'` lists the
ones that exist. See `docs/releases/README.md#release-target-and-promotion`.

### Glossary former names

Older ADRs and release notes still use `CONTEXT.md` / `CONTEXT-MAP.md`, renamed
in #790 to `GLOSSARY.md` / `GLOSSARY-MAP.md`. Read those former names as the
current ones and never recreate the retired files.
