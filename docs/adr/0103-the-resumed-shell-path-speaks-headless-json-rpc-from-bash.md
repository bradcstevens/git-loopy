# The resumed shell path speaks headless JSON-RPC from bash

**Status:** accepted design. Planning only: no adapter is implemented and no manifest, register
or label changes.

Decided by [Choose the resumed shell path's session interface](https://github.com/bradcstevens/git-loopy/issues/983)
on [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979),
from the [interface research](https://github.com/bradcstevens/git-loopy/blob/58de5f9b1224706e4e4f378cb74cea5d76e54427/docs/research/shell-resumption-session-interface.md)
(Copilot CLI 1.0.103, SDK protocol 3). Serves [ADR-0102](0102-a-resumed-shell-path-declares-contract-support-2-38-serial-and-without-config-parity.md),
whose Contract support 2.38 floor needs §18 history denial, §19 Sandbox and observable Endings.

The dormant shell launches each Iteration with `copilot --yolo -p`. Prompt mode has no
pre-work policy preflight, and its JSON output drops permission, Sandbox and `assistant.usage`
events, so it cannot meet §18 or §19 or see a content-filter verdict ([ADR-0098](0098-a-dormant-path-owes-the-sandbox-rules-only-once-it-resumes.md)).
ACP is a public preview with no Sandbox policy or attestation boundary. The headless runtime's
JSON-RPC interface exposes all of these, and the Python Runner meets §18 and §19 through it.

## Decision

1. **Headless JSON-RPC, owned by the shell path.** A native adapter in Bash 4+ and `jq`, the
   member's existing toolchain, speaks `Content-Length`-framed JSON-RPC (byte-counted under
   `LC_ALL=C`). No Python or Node SDK helper, and no compiled helper: either would make another
   runtime the Orchestrator or add a toolchain and artifact to the path.
2. **Topology.** Each Run starts one `copilot --headless` server as a stdio child and opens one
   fresh session per Iteration. The Run owns the server's process tree and tears it down on exit,
   signal and wind-down. No TCP port.
3. **Compatibility gate.** The adapter checks the protocol-version handshake and refuses the Run
   on a mismatch before any work. The shell docs state the minimum Copilot CLI version.
4. **Preflight.** Before the first work Agent, a prompt-free management session with memory
   disabled reads managed settings, enforcement status and host support. Its result is the Run's
   `wrapper.sandbox.resolved`. A required but blocked Sandbox refuses the Run before any work. The
   sessionless resolver is not used, because its documented behavior is ambiguous.
5. **Sessions.** Every work session is created with memory disabled and its Sandbox configuration
   applied at creation, so a conflict fails closed. Creation carries only the Run-wide model,
   effort and context: no custom agents or profiles. The §17.6 Skill fail-closed check is unchanged.
6. **Permissions.** The adapter is the only permission handler: no `--yolo`, no auto-approve. It
   denies Sandbox bypass and permissive-retry requests, denies `session_store_sql` with
   `cross_session_history_disabled`, applies the existing deny-tool list and approves ordinary
   tools. Every denial is recorded as `tool.permission_denied`, attributed to its request and
   tool call.
7. **Attestation.** When the Sandbox is required, a tool completion whose `sandboxed` is missing or
   `false` is unattested, never sandboxed, and is handled as §19 prescribes.
8. **Observable outcomes.** The adapter reports crash (transport EOF, server exit or
   `session.error`), timeout (its own deadline, then abort and teardown; a wait timeout alone never
   means the work ended), content-filtered (`assistant.usage.contentFilterTriggered`) and the
   informational no-more-tasks sentinel. Progress comes only from Git and tracker reconciliation.
   Anything else is unknown and stays unknown. Mapping observations to Endings belongs to
   [Decide how the shell Attempt lifecycle, Strikes and Endings fit together](https://github.com/bradcstevens/git-loopy/issues/984).
9. **Unproved means blocked.** These are activation-evidence blockers, not assumptions: a live
   bash round trip; managed policy loaded for the actual identity and session; hook-versus-handler
   permission precedence; Subagent attestation and history denial; process-tree teardown. Any that
   cannot be proved keeps the path dormant under ADR-0102's floor, with no prompt-mode workaround.
10. **Contract wording.** §4's "a single `copilot --yolo -p` invocation" is amended to
    interface-neutral wording by a delivery slice, not here.

## Considered options

- **Prompt mode with `--sandbox`.** Rejected: no preflight, no bypass denial, no permission or
  content-filter evidence.
- **ACP.** Rejected: preview, NDJSON with server-start configuration, and no Sandbox boundary.
- **An SDK helper in Python or Node.** Rejected: the shell path would depend on another path's
  runtime for its Orchestrator, which the map rules out.
- **A compiled helper owned by the shell path.** Rejected: a new toolchain and published artifact
  for a member whose point is Bash and `jq`.
