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
  (`test_conformance.py`, `test_release_identity_conformance.py`) is already
  inside the Python suite row; a second
  row would only pay for it twice.
- **The native Windows timezone proof** in `runner-family-gate.yml` repeats Rust
  format/lint checks and runs the native timezone API and executable regressions
  on Windows. Those tests are already part of the Rust suite above on a Windows
  host; another row would either repeat them there or require Windows APIs on an
  operator's non-Windows host. CI supplies that additional platform evidence.
- **`python -m git_loopy.skill_source`** (acquire and validate the pinned external
  Skill catalog, ADR-0023) reaches the network, so it can never be a gate: an
  unreachable upstream would make Integration red for a reason no change here
  caused. Its offline half — the immutable pin, the real fetch/checkout path over
  a `file://` remote, and every validation failure — is covered by
  `tests/test_skill_source.py` inside the Python suite row.
- **`git-loopy doctor --apply`** refreshes that same catalog before it repairs a
  Skill policy (#518), so it inherits the same exclusion for the same reason. Its
  offline half — the pin comparison, the `absent`/`drifted`/`matching` verdicts,
  the "refresh, do not prune" rule they impose on a missing-Skill row, and the
  refresh itself over a `file://` remote — is covered by
  `tests/test_skill_install.py` and `tests/test_doctorcmd.py` inside the Python
  suite row.
- **`python -m git_loopy.release_rehearsal`** (prove a promoted stable snapshot
  before any public tag exists, ADR-0059) *runs this table* over its candidate,
  so declaring it as a row would make Integration gate itself recursively. It is
  release proof rather than a Lane gate: `release-promotion.yml` runs it per
  candidate and creates no tag without it, and the boundary itself — both
  triggers, every drift and refusal, the publication-input binding — is covered
  offline by `tests/test_release_rehearsal.py` inside the Python suite row.

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

### Chain hook

`.github/hooks/git-loopy-chain.json` registers the `subagentStop` hook used by
`/next` to run `chain.sh complete`, closing the finished chain hop in the
per-clone `.git-loopy/` ledger. The committed hook resolves the `/next` script
from `$HOME/.copilot/skills/next/chain.sh`; operators with a different install
location can set `GIT_LOOPY_NEXT_CHAIN`. If the script is missing, the hook
fails with an actionable diagnostic instead of silently leaving the ledger row
open.

### Issue tracker

Issues live in this repo's GitHub Issues (managed via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

All five canonical triage roles use their default label strings (`needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See
`docs/agents/triage-labels.md`.

Issues whose titles begin with `PRD:` or `Spec:` (case-insensitive) are planning
documents, not executable tickets. Never apply `ready-for-agent` to them; remove
that label if present. Work their implementation tickets instead.

### Release milestones

A `vX.Y.Z` GitHub milestone is solely the **Promotion** trigger: closing it
promotes the current matching `dev.N` Release line to stable. The Release target
is instead ratcheted from closed issues' **Bump class** labels. Never invent a
milestone — `gh api repos/{owner}/{repo}/milestones --jq '.[].title'` lists the
ones that exist. See `docs/releases/README.md#release-target-and-promotion`.

### Domain docs

Single-context layout: one `CONTEXT.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
