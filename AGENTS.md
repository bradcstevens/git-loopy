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

Notes on what is deliberately *not* in the table, because every row in it is a
blocking gate:

- **`ruff`** carries pre-existing errors, so declaring it would make Integration
  permanently red. Run `uv run --project git-loopy/python --all-extras ruff check
  git-loopy/python` on the files you touched and keep them clean; it becomes a gate
  row once the backlog is cleared.
- **The Conformance adapter subset** the CI runs as its own fast job
  (`test_conformance.py`, `test_release_identity_conformance.py`,
  `test_continuation_scenarios.py`) is already inside the Python suite row; a second
  row would only pay for it twice.
- **`python -m git_loopy.skill_source`** (acquire and validate the pinned external
  Skill catalog, ADR-0023) reaches the network, so it can never be a gate: an
  unreachable upstream would make Integration red for a reason no change here
  caused. Its offline half — the immutable pin, the real fetch/checkout path over
  a `file://` remote, and every validation failure — is covered by
  `tests/test_skill_source.py` inside the Python suite row.

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

### Issue tracker

Issues live in this repo's GitHub Issues (managed via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

All five canonical triage roles use their default label strings (`needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
