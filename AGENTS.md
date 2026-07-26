# Agent Instructions

This file provides instructions and conventions for AI coding agents (GitHub Copilot
CLI, Claude Code, etc.) working in this repository.

## Feedback loops

These mirror `.github/workflows/runner-family-gate.yml` — the anti-drift gate for
the whole Runner family. Run them from the repository root; the runner's own
Integration gate parses this table and runs each row in order, so a command here
must be exactly the command CI runs.

| Loop              | Command                                                                              | When to run                                            |
| ----------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| Python suite      | `uv run --project git-loopy/python --all-extras python -m pytest -q git-loopy/python/tests` | Any change to the Python Orchestrator, its tests, or a tracked file one of its static guards reads |
| Shell suite       | `for f in git-loopy/shell/*.sh git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh; do bash -n "$f" \|\| exit 1; done; for t in git-loopy/shell/tests/test-*.sh; do bash "$t" \|\| exit 1; done` | Any change under `git-loopy/shell/` |
| PowerShell suite  | `for t in git-loopy/powershell/tests/test-*.ps1; do pwsh -NoLogo -NoProfile -File "$t" \|\| exit 1; done` | Any change under `git-loopy/powershell/` |
| Rust Dashboard core | `cargo fmt --manifest-path git-loopy/tui/Cargo.toml --check && cargo clippy --manifest-path git-loopy/tui/Cargo.toml --all-targets -- -D warnings && cargo test --manifest-path git-loopy/tui/Cargo.toml` | Any change under `git-loopy/tui/` |

A change to the shared Conformance fixtures, `docs/wrapper-contract.md`, or the
Event schema touches every Orchestrator — run all four.

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
