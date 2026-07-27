# Agent Instructions

This file provides instructions and conventions for AI coding agents (GitHub Copilot
CLI, Claude Code, etc.) working in this repository.

## Feedback loops

Run these before committing. They are also the load-bearing **Integration** gate
(ADR-0009): the runner re-runs this table in the Integration stage after every
merge, fail-fast in table order, and reverts a merge that turns any row red.

| Loop             | Command | When to run |
| ---------------- | ------- | ----------- |
| Python suite     | `uv run --project git-loopy/python --all-extras python -m pytest -q git-loopy/python/tests` | Any change — covers the reference Orchestrator, the Conformance adapters, the Wrapper-contract guards, and the docs/workflow guards |
| Rust Dashboard   | `cargo fmt --manifest-path git-loopy/tui/Cargo.toml --check && cargo clippy --manifest-path git-loopy/tui/Cargo.toml --all-targets -- -D warnings && cargo test --manifest-path git-loopy/tui/Cargo.toml` | Any change under `git-loopy/tui/` |
| Shell port parse | `bash -ec 'for f in git-loopy/shell/*.sh git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh; do bash -n "$f"; done'` | Any change under `git-loopy/shell/` |
| PowerShell parse | `pwsh -NoLogo -NoProfile -Command 'foreach ($f in (Get-ChildItem -Recurse git-loopy/powershell -Include *.ps1,*.psm1)) { $e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$e); if ($e) { throw $e[0] } }'` | Any change under `git-loopy/powershell/` |

No command in this table may contain an unescaped `|` — the table parser splits
rows on it.

The shell and PowerShell rows are **parse-only** on purpose: the full port
conformance suites fork tens of thousands of subprocesses and take tens of
minutes on a developer machine, which is too slow for a per-merge gate. Their
behavioural coverage lives in CI — `.github/workflows/runner-family-gate.yml`
runs the full Runner-family gate (Python, shell, Rust, PowerShell) on every push
and pull request. Run them locally yourself when you touch a port:

```bash
bash git-loopy/shell/tests/test-event-conformance.sh
bash git-loopy/shell/tests/test-orchestrator-conformance.sh
bash git-loopy/shell/tests/test-continuation-conformance.sh
bash git-loopy/shell/tests/test-orchestrator-boundary.sh
bash git-loopy/shell/tests/test-tui-install.sh

pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-event-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-orchestrator-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-continuation-conformance.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-orchestrator-boundary.ps1
pwsh -NoLogo -NoProfile -File git-loopy/powershell/tests/test-tui-install.ps1
```

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
