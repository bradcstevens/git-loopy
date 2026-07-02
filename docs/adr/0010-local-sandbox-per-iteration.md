# Per-Iteration local sandbox for the AFK loop

**Status:** accepted

## Context

The AFK loop drives Copilot through the Python SDK (`github-copilot-sdk`), **not** the
interactive CLI, so the documented `/sandbox` slash command is not our integration
surface. We want each agent **Iteration** to run its shell commands inside GitHub
Copilot's local (macOS seatbelt) sandbox, fresh per Iteration, to contain one issue's
filesystem blast radius. Two frictions shape the design: (1) SDK 1.0.2 `create_session()`
exposes **no** `sandbox` kwarg — though the `session.create` wire payload carries a
`sandbox_config` field — and (2) the agent itself runs `git push` / `gh` to close issues
(PROMPT.md), so the sandbox cannot cut network or keychain without breaking the
close-the-issue flow.

## Decision

Enable the sandbox by writing a `sandbox` policy block into a runner-managed
`settings.json` and pointing the SDK session at it via the existing `config_directory`
kwarg — the documented persistence location for sandbox settings. The policy is applied
at every `session.create` (**per Iteration**) with `clearPolicyOnExit: true`, which
realizes "a fresh sandbox per issue" as a **superset** (every issue boundary is also an
Iteration boundary).

The policy is **blast-radius containment**: the repo worktree plus system temp are
read/write, writes elsewhere are confined, and **network + keychain stay ON** so the
agent keeps its push / `gh` flow. It is **session-wide** — sub-agents and skills inherit
it. A three-valued `COPILOOP_SANDBOX` knob (`off | on | require`) defaults to **`on`** with
graceful degradation (warn once, emit a `sandbox-unavailable` JSONL event, continue
unsandboxed); `require` aborts at startup if the sandbox can't be confirmed. The SDK's
per-tool `sandboxed` flag is cross-checked against the intended posture to catch a
**silent bypass** (abort under `require`, degrade under `on`).

## Considered options

- **Inject `sandbox_config` into the `session.create` payload** — kept only as a
  fallback: it couples us to SDK internals that are explicitly public-preview and may
  break; used only if `config_directory` turns out not to enable sandboxing for the
  SDK-spawned CLI.
- **Bump the SDK to a version with a native `sandbox` kwarg** — deferred: each bump is a
  breaking, contract-checked migration (per the `pyproject.toml` pin note), not worth
  blocking a preview feature on.
- **Network egress lockdown (model B)** — rejected for this pass: the agent could no
  longer push / `gh`, forcing the runner to own *all* pushing plus a prompt rewrite — a
  much larger change for isolation the write-confinement model already mostly provides.
- **Docker-style ephemeral filesystem per issue** — rejected: not what the native local
  sandbox provides, and a divergence from "incorporate GitHub Copilot's sandbox feature."

## Consequences

- The runner gains a per-Iteration step that materializes a `settings.json` sandbox
  policy and passes `config_directory` to `create_session`; care is needed so
  `config_directory` does not disturb other config discovery (MCP servers, instructions).
- The "sandbox" leaves **network open by design** — it contains filesystem damage /
  leakage, not exfiltration. This is surprising and is called out so no one assumes
  egress protection.
- **Non-goals:** it does not solve concurrent-run worktree collisions (issues are worked
  sequentially within a run; two simultaneous runs still collide) and does not provide a
  clean checkout per issue.
- Cloud / remote sandboxes are **deferred**; the SDK's `cloud` / `remote_session` options
  are the future lever when ephemeral fresh-filesystem environments are needed (untrusted
  code, parallel issue execution).
- Feature is public preview and macOS-first (Windows needs Insiders; Linux differs;
  per-host network allow/block rules are unreliable) — hence default-on-with-degrade
  rather than hard-require.
