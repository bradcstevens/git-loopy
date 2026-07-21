# Explicit skill discovery for Python SDK sessions

**Status:** superseded by ADR-0015

## Context

The Python reference Orchestrator creates each Iteration through the GitHub Copilot
Python SDK. The SDK starts its bundled CLI in headless mode, where project and user
skills are not discovered by default. As a result, a skill available to an operator's
interactive Copilot CLI can still fail inside a git-loopy Run with `Skill not found`.

The SDK offers two ways to expose those skills: enable broad config discovery, or
enable skills and provide explicit skill directories. Broad config discovery also
loads other repository configuration, including MCP server configuration and
instructions. That is a larger behavior and security surface than the missing-skills
problem requires, and conflicts with ADR-0010's caution that session configuration
must not accidentally disturb unrelated discovery.

## Decision

Every Python SDK session enables skills explicitly and supplies exactly these roots:

- `<working-directory>/.copilot/skills`, where the working directory is the process
  cwd in serial mode and the Lane's worktree in Parallel mode.
- `~/.copilot/skills`, resolving the home directory from `HOME` with `Path.home()` as
  the fallback.

The Orchestrator does not enable SDK config discovery for this purpose. Skill loading
therefore remains an intentional, skills-only capability rather than an implicit
opt-in to all Copilot repository and user configuration.

## Considered options

- **Enable skills without directories** — rejected because headless sessions then load
  only bundled skills, not project or user skills.
- **Enable broad config discovery** — rejected because it solves the symptom by also
  loading unrelated MCP and instruction configuration.
- **Rely on interactive CLI discovery** — rejected because SDK sessions use a separate
  bundled CLI process in headless mode and do not inherit the interactive CLI's loaded
  skills.

## Consequences

- Project and user skills are available consistently in serial Iterations and Parallel
  Lanes.
- git-loopy passes both roots on every Iteration without creating or preflighting
  them; the SDK loads the skills that are present.
- Plugin-provided skills outside the two explicit roots are not discovered through
  this decision.
- This decision applies to the Python reference Orchestrator's SDK integration. Other
  runner-family members must provide equivalent skill availability through their own
  runtime interfaces.
