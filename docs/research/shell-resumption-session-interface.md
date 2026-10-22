# Session interfaces for the resumed shell Language path

Research for [Establish viable session interfaces for the resumed shell path](https://github.com/bradcstevens/git-loopy/issues/981),
a child of [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979).
Recorded 2026-10-22. This is a factual interface inventory, not a support or architecture decision.
It lives on a throwaway research branch and is not intended to merge.

## Finding

The headless Copilot runtime's JSON-RPC interface is a source-supported candidate
for a native bash adapter. It exposes session, policy, permission and event
boundaries that ordinary prompt mode does not expose as a complete documented
control interface. This research establishes neither a working bash adapter nor
runtime compliance. ACP is a separate documented protocol, but its documented
surface does not establish the required Copilot-specific Sandbox policy and
attestation boundary.

There is a material update to the historical prompt-mode evidence: CLI 1.0.103
contains an early awaited Sandbox-options application as well as a later
catch-and-continue application path. Do not repeat the unqualified claim that
every prompt-mode policy-application failure is ignored. This nuance does not
establish compliance with Wrapper contract section 19.

## Evidence horizon and method

- Repository audit snapshot: `0ee871152855b0e9e4f729b811dae964848c9643`,
  Wrapper contract 2.38. Shell remains dormant at support 2.26. Read the map,
  research question, ADR-0098 and the earlier attempts and closure on
  [Shell Orchestrator meets the Sandbox rules](https://github.com/bradcstevens/git-loopy/issues/866).
- Safe local help and source inspection identified installed Copilot CLI
  1.0.103. Local implementation observations below refer to that version's
  `cli-main.js` and bundled SDK declarations, not a portable API promise.
- GitHub's release API reported SDK v1.0.26, published 2026-10-22 at
  16:38:40 UTC, at commit
  [`f527e6134f9b38980af8aa796b4c959458a88769`](https://github.com/github/copilot-sdk/tree/f527e6134f9b38980af8aa796b4c959458a88769).
  Its [package source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/package.json#L1-L9)
  identifies CLI 1.0.103. This is release/source evidence, not a corporate-feed
  availability claim or permission to download a package.
- GitHub Docs observations use snapshot
  [`481d4e35beb53953e277d0b11ba4a18f8c8ade4f`](https://github.com/github/docs/tree/481d4e35beb53953e277d0b11ba4a18f8c8ade4f).
- No work Agent/model turn, runtime session probe, dependency installation,
  saved-setting change or infrastructure provisioning was performed. Corporate
  feeds were not bypassed. Source-confirmed capability is not live behavioral
  proof.

ADR-0098 withdraws the dormant Sandbox exception. Support advancing through 2.35
would bring the fixture into scope; it is not a third carried baseline waiver.
The pending register cleanup is owned by
[The dormant shell and PowerShell paths owe no Sandbox row](https://github.com/bradcstevens/git-loopy/issues/952).
See [ADR-0098](../adr/0098-a-dormant-path-owes-the-sandbox-rules-only-once-it-resumes.md).

## Conditional capability matrix

Requirements depend on the later support decision. "Source" below means a
released interface or implementation was inspected, not that a bash adapter
successfully exercised it.

| Capability | Headless runtime evidence | Prompt mode and ACP limits |
| --- | --- | --- |
| Saved settings | `user.settings.get` includes defaults, but excludes repository and enterprise overlays; it is not the full effective policy. [RPC source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L37884-L37891) | Installed `copilot config --help` describes saved files, not effective enforcement. |
| Managed policy | `session.managedSettings.get` waits for application and returns the retained effective session snapshot. A separate sessionless resolver has caveats discussed below. [RPC source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L41531-L41539) | No equivalent outside-session CLI inspection command was established. The ACP documentation supplies no equivalent policy API. |
| Enforcement preflight | `session.sandbox.getEnforcementStatus` returns required/blocked state and an optional reason. [Result shape](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L27500-L27519), [method](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L40214-L40220) | Installed `copilot sandbox --help` exposes certificate-authority management, not this machine-readable pre-work result. |
| Host support | `sandbox.getHostSupport` reports backend support and capabilities. [Source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L27620-L27639) | Installed help describes warnings and subsequent failed sandboxed operations on unsupported hosts, not an external preflight API. |
| Fail-closed application | Creation-time `sandboxConfig` is documented as applied before MCP servers/tools start, with conflicts failing create/resume. `session.options.update` also acknowledges application. Compatible runtime behavior remains to be proved. [Creation option](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/types.ts#L3171-L3178), [update method](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L41691-L41701) | Local prompt implementation has both early awaited and later caught application paths; see the recheck below. |
| Bypass and permissive retry denial | Typed shell permission requests carry `requestSandboxBypass` and `requestSandboxPermissive`; the latter remains an escalation. [Event fields](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/session-events.ts#L10218-L10242) | Installed permission help matches commands/paths/tools/URLs, not those predicates. Dropping `--yolo` is not equivalent. ACP documentation does not establish equivalent fields. |
| Per-tool evidence | Permission events carry request identity; completion can include tool-call identity and decision source. Adapter-owned denial reasons and attribution still need proof. [Request](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/session-events.ts#L10170-L10185), [completion](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/session-events.ts#L11463-L11485) | Prompt JSON drops permission and Sandbox decision events in the inspected source. |
| Tool attestation | `tool.execution_complete.data.sandboxed` exists but is optional. Missing/false attestation must not be treated as success. [Event source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/session-events.ts#L8363-L8408), [contract](../wrapper-contract.md#19-sandbox-contract-235-must) | A tool-completion stream alone does not repair absent preflight or permission control; ACP documentation does not establish this field. |
| Session lifecycle | Create, send, abort and detach are distinct operations. `abort()` cancels current work; disconnect uses detach and preserves session data. [Session implementation](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/session.ts#L2707-L2779) | Prompt flags create/resume sessions, but installed `copilot sessions --help` lists only import. ACP documents `session/new`. |
| Timeout and crash | A `sendAndWait` timeout ends the wait, not the work; explicit cancellation/teardown remains necessary. Transport loss and session errors are distinct observations. [Timeout contract](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/session.ts#L840-L855), [transport errors](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/client.ts#L3497-L3533) | Bash can observe process deadlines/exits, but clean exit is not progress; process-tree cancellation was not tested. |
| Content filtering | `assistant.usage.contentFilterTriggered` is an explicit verdict; finish reason is supplementary. [Source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/session-events.ts#L7304-L7328) | Inspected prompt JSON drops `assistant.usage`, losing this verdict channel. ACP equivalence was not established. |
| Routing, profiles and isolation | Session creation forwards model, effort, context, custom agents, skills, memory and discovery controls, subject to runtime compatibility. [Client source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/client.ts#L1833-L1940) | Prompt flags are not equivalent to the complete permission/policy boundary. ACP fixes some configuration at server startup rather than per session. [ACP limits](https://github.com/github/docs/blob/481d4e35beb53953e277d0b11ba4a18f8c8ade4f/content/copilot/reference/copilot-cli-reference/acp-server.md#L37-L53) |

## Native bash integration boundary

The SDK documents an external `copilot --headless --port <port>` server, and
its own client launches headless mode over stdio. These are concrete integration
candidates, not a requirement to make Python the shell Orchestrator.
[Getting started](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/docs/getting-started.md#L1929-L1955),
[client launch](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/client.ts#L3180-L3195).

SDK JSON-RPC frames use UTF-8 byte-counted `Content-Length` headers:
[framing implementation](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/python/copilot/_jsonrpc.py#L388-L403).
ACP instead uses NDJSON over stdio or TCP:
[ACP transport](https://github.com/github/docs/blob/481d4e35beb53953e277d0b11ba4a18f8c8ade4f/content/copilot/reference/copilot-cli-reference/acp-server.md#L71-L80).
They are not interchangeable transports.

A native adapter would need framing, correlation, asynchronous events,
permission handling while other calls are outstanding, EOF/error detection,
deadlines and owned-process cleanup. This is an engineering inference from
the [client transport and dispatch](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/client.ts#L3497-L3628),
not an assertion that such a bash adapter exists.

The inspected SDK declares protocol 3 and checks its handshake:
[protocol source](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/sdkProtocolVersion.ts#L7-L18),
[handshake](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/client.ts#L2806-L2820).
Protocol acceptance is not proof every additive field is honored. The source
warns that older runtimes may ignore injected managed settings:
[compatibility caveat](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/types.ts#L3256-L3258).

Current permission wiring consumes `permission.requested` events and replies
through `session.permissions.handlePendingPermissionRequest`. Requests already
resolved by hooks skip the SDK handler; hook/config discovery and resolution
precedence therefore belong inside the proof boundary.
[Event handling](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/session.ts#L1513-L1521),
[permission dispatch](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/session.ts#L1714-L1764),
[reply method](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L42020-L42031).

## Prompt-mode recheck

Local CLI 1.0.103 source inspection found:

- `cli-main.js:2112`: helper `i_n` initially applies `sandboxConfig` and, for
  `managed_sandbox_policy_conflict`, retries remaining options without that
  snapshot.
- `cli-main.js:2131`: the outer launch awaits that helper before executing
  the prompt.
- `cli-main.js:2156`: the executor applies options again, catches failure,
  logs `Failed to apply prompt-mode session options`, and continues.
- `cli-main.js:2055-2056`: prompt JSON excludes `permission.requested`,
  `permission.completed`, `sandbox.decision`, `assistant.usage` and idle events.

These are local implementation observations, not a tested end-to-end guarantee.
They qualify ADR-0098's older failure-application observation, but do not establish
a documented prompt interface combining effective policy, host preflight,
acknowledged required policy, bypass-specific decisions and attributable evidence
before work starts. No claim of section 19 compliance follows from `--sandbox`.

ACP remains public preview with different configuration limits. Its documented
behavior can forward unsupported slash-command text to the model, so inventing
a `/sandbox status` probe would not be a safe no-model inspection.
[ACP reference](https://github.com/github/docs/blob/481d4e35beb53953e277d0b11ba4a18f8c8ade4f/content/copilot/reference/copilot-cli-reference/acp-server.md#L17-L53),
[slash commands](https://github.com/github/docs/blob/481d4e35beb53953e277d0b11ba4a18f8c8ade4f/content/copilot/reference/copilot-cli-reference/acp-server.md#L194-L244).

## Endings and progress

The protocol does not decide repository progress. The contract counts Agent
commits, wrapper closure and PR head advance, excludes Checkpoints, and makes
the legacy sentinel informational:
[Wrapper contract section 6](../wrapper-contract.md#6-progress--strike-accounting-phase-1-must).

The reference gathers session errors, explicit content-filter verdicts and
the no-more-tasks sentinel:
[observation](../../git-loopy/python/git_loopy/session_outcome.py#L320-L365).
Its reconciliation order is crash, timeout, actual progress, no-more-tasks,
content-filtered, then no-progress:
[reconciliation](../../git-loopy/python/git_loopy/session_outcome.py#L462-L509).
Actual progress suppresses the last three, but not crash or timeout.

Consequently, silence, idle, successful RPC or an Agent's assertion cannot
substitute for external Git/tracker reconciliation after terminal handling.
Unknown outcomes must remain unknown; interface gaps do not justify fabricating
Endings or a claimed fixture.

## Remaining proof obligations

- The sessionless resolver's generated descriptions conflict about whether
  an organization policy helper runs:
  [request field](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L18194-L18198),
  [method documentation](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/generated/rpc.ts#L37915-L37923).
  Do not present either interpretation as verified behavior.
- Authentication alone does not establish managed-policy loading.
  `enableManagedSettings` and model-default enforcement have explicit controls:
  [settings](https://github.com/github/copilot-sdk/blob/f527e6134f9b38980af8aa796b4c959458a88769/nodejs/src/types.ts#L3192-L3227).
  Prove policy for the correct identity and actual session.
- Creation-time Sandbox application is source-supported but was not
  exercised. The installed SDK declaration also documents it at
  `copilot-sdk/types.d.ts:2601-2608`.
- Host support is not proof every operation works. Installed help notes that
  Linux probing does not establish every network-namespace prerequisite.
  Local `sandbox-exec` availability is likewise not complete policy proof.
- Sandbox confinement is not a machine Isolation grade. Installed help
  distinguishes OS shell confinement, best-effort built-in file edits,
  optional MCP/LSP confinement and remote MCP limits; the contract keeps
  Sandbox and Isolation separate.
- No bash adapter, managed-policy/helper probe, hook-precedence probe,
  complete Subagent attestation path or process-tree teardown was tested.
  These remain later implementation/acceptance obligations, not reasons to
  claim a prompt-mode workaround or select an architecture here.

The Python production seams illustrate intended ordering:
[prompt-free management preflight](../../git-loopy/python/git_loopy/run_sandbox.py#L30-L80),
[policy preservation](../../git-loopy/python/git_loopy/session.py#L226-L261),
[escalation denial](../../git-loopy/python/git_loopy/session.py#L532-L549),
[session setup](../../git-loopy/python/git_loopy/session.py#L1013-L1045),
and [attestation](../../git-loopy/python/git_loopy/session.py#L1142-L1162).
Its fake-transport tests prove reference handling and serializer boundaries,
not live compatibility for a new shell adapter:
[session tests](../../git-loopy/python/tests/test_session_sandbox.py#L20-L35),
[management tests](../../git-loopy/python/tests/test_run_sandbox.py#L13-L48).

## Handoff

The factual interface survey is complete: native headless JSON-RPC is a
source-supported candidate; a complete prompt-mode or ACP policy boundary is
not established. Runtime proof and external interface gaps remain explicit.
No supported surface, implementation interface, exception or activation has
been chosen. The human support and interface decisions on the map still own
those choices.
