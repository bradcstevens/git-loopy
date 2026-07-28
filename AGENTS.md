# Agent Instructions

This file provides instructions and conventions for AI coding agents (GitHub Copilot
CLI, Claude Code, etc.) working in this repository.

## Agent skills

Agent skills are located in `.copilot/skills/*/SKILL.md`

### Issue tracker

Issues live in this repo's GitHub Issues (managed via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

All five canonical triage roles use their default label strings (`needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.

## Feedback loops

Run every command from the **repository root**. This table is the single source of
truth for the repo's own quality bar: an Iteration runs the rows relevant to what it
changed, and the runner-side Integration gate (`git_loopy.gate.AgentsMdGateRunner`,
ADR-0009) runs them all — fail-fast, in order — after each Lane merge. Keep it in
sync with `.github/workflows/runner-family-gate.yml`, which is the same set on CI.

| Loop              | Command                                                                                            | When to run                                             |
| ----------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Rust format       | `cargo fmt --manifest-path git-loopy/tui/Cargo.toml --check`                                         | Any change under `git-loopy/tui/`                       |
| Rust lint         | `cargo clippy --manifest-path git-loopy/tui/Cargo.toml --all-targets -- -D warnings`                 | Any change under `git-loopy/tui/`                       |
| Rust tests        | `cargo test --manifest-path git-loopy/tui/Cargo.toml`                                                | Any change under `git-loopy/tui/`                       |
| Shell syntax      | `bash -n git-loopy/shell/git-loopy.sh git-loopy/shell/install.sh git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh` | Any change under `git-loopy/shell/`     |
| Python tests      | `uv run --project git-loopy/python --all-extras python -m pytest -q git-loopy/python/tests`          | Any change under `git-loopy/python/`, `.copilot/skills/` |
| Shell smoke       | `for t in git-loopy/shell/tests/test-*.sh; do bash "$t" \|\| exit 1; done`                             | Any change under `git-loopy/shell/`                     |
| PowerShell smoke  | `for t in git-loopy/powershell/tests/test-*.ps1; do pwsh -NoLogo -NoProfile -File "$t" \|\| exit 1; done` | Any change under `git-loopy/powershell/`             |

Prerequisites: `uv`, a Rust toolchain (`cargo` + `rustfmt` + `clippy`), Bash 4+
(`brew install bash` on macOS), `jq`, and PowerShell 7+ (`pwsh`).

Not gate loops: `ruff` and `ty` are red at baseline across the tree and are not run
by CI, so they are advisory only — do not add them here until the tree is clean.
Workflow linting (`actionlint`) lives in `.github/workflows/workflow-lint.yml`; it
needs a Go toolchain, so it stays a CI-only check.
