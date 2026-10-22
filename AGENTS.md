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

For a docstring-or-prose change, run targeted Python tests instead of the full
Python suite only when publishing through a pull request (CI runs the full suite
on every pull request with no path filters) or delivering a Lane contribution in
Parallel mode (Integration runs the whole table); a serial Run pushes straight to
the default branch and still runs the full suite. To qualify, compare each changed
Python file against the merge-base yourself: both versions must parse to the same
`ast.dump` after removing module, class and function docstrings, and every other
changed file must be Markdown. Run each changed module's own
`tests/test_<module>*.py`, every test file that names a changed file (find them
with `git grep -l -F <basename> -- git-loopy/python/tests` for each changed file),
and the tracked-tree guards `tests/test_no_*.py` and `tests/test_glossary_*.py`;
Markdown-only changes run this same set too. Record the targeted tests run and
the full-suite backstop (CI or Integration). The known flakes are
`test_release_installation.py::test_reclaims_setup_children_not_just_their_parent[cancelled-init]`
(a pseudo-terminal "Operation not permitted" error under host load),
`test_release_smoke.py::test_a_run_still_in_flight_at_the_deadline_is_never_passed`
(its fixture deadline expires under host load), and
`test_attach_command.py::test_a_dashboard_fault_restores_the_terminal`
(a deadline or teardown timeout under host load; #734 fixes it, so remove this
entry when #734 lands). When a listed test fails in a full run, rerun it alone:
if it passes, report the flake and treat that failure as green without rerunning
the full suite; an unlisted failure or one that also fails alone is a real
failure. Record each encountered known flake and its result when rerun alone;
change this list only through normal review.

For maintainer rationale on whether a command should become a gate row, see [Feedback-loop exclusions](docs/agents/feedback-loops.md).

Commands resolve their tools through `PATH` and are relative to the repository root,
because Integration runs them in a throwaway private worktree on whatever host the
operator has. Never hard-code a host-specific executable path here.

## Agent skills

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

`.github/hooks/git-loopy-chain.json` registers two hooks for `/next`, both
through the repository-relative resolver `.github/hooks/git-loopy-chain.sh`.
`subagentStop` runs `chain.sh complete`, closing the finished chain hop in the
per-clone `.git-loopy/` ledger. `agentStop` runs `reenter`
(`.github/hooks/git-loopy-agent-stop.py`), which holds the parent agent open
with "Run /next now" while a completed hop is still unrouted, and gives up on a
hop after three unconfirmed requests.

The resolver finds the `/next` script at
`${COPILOT_HOME:-$HOME/.copilot}/skills/next/scripts/chain.sh`. If it is
missing, the resolver reports `no chain.sh found` on stderr and exits `0`,
rather than failing an agent that has nothing to do with the chain.
`.github/hooks/git-loopy-hook-log.sh` appends every invocation, including that
stand-down, to `.git-loopy/hook-invocations.jsonl`, so a chain that closes
nothing still leaves a record. Copilot runs repository hooks only in trusted
folders. All four files are written by `/setup-git-loopy-skills`; the
`agentStop` helper is a copy of the one bundled with that Skill, so re-run it
rather than editing the copy.

### Issue tracker

Issues live in this repo's GitHub Issues (managed via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

All five canonical triage roles use their default label strings (`needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See
`docs/agents/triage-labels.md`.

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

### Domain docs

Single-context layout: one `GLOSSARY.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
