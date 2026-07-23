# The Wrapper Contract

> The language-neutral behavioural specification that **every** git-loopy **Orchestrator** — the
> Python reference runner and the shell, PowerShell, and future Rust ports — must satisfy. This
> is the single source of truth the [**Runner family**](../CONTEXT.md#the-runner-family)
> implements and the [**Conformance suite**](../git-loopy/conformance/README.md) pins. See
> [ADR-0013](adr/0013-multi-language-runner-family.md) for why the family exists and how it stays
> in lockstep.

**Contract version:** 1.3 (tracks the Python reference implementation in `git-loopy/python/`).

Terminology in **bold** (Run, Iteration, Pool, Strike, Checkpoint, Active issue, ...) is defined
in [`CONTEXT.md`](../CONTEXT.md). Where this spec and the Python code disagree, the code is the
temporary tie-breaker and the discrepancy is a contract bug to be reconciled — the intent is that
they never disagree, enforced by the Conformance suite.

Requirement levels use RFC-2119 **MUST / SHOULD / MAY**. Each invariant is tagged with the roadmap
phase that first requires it, so the phase-1 Conformance suite can pin the core loop before the
TUI, config, OTel, and parallel-mode phases land.

---

## 1. Preflight (phase 1, MUST)

Every Orchestrator MUST expose top-level `git-loopy --version` as an earlier public identity seam.
`--version` accepts no additional arguments, reads the distribution's shared **Release version** as
strict UTF-8 Semantic Versioning, writes exactly `git-loopy <Release version>` plus one newline to
stdout, writes nothing to stderr, and exits `0`. It MUST complete before configuration parsing,
repository discovery, dependency checks, Run preflight, Event initialization, or artifact creation.
Unavailable or invalid Release metadata MUST fail nonzero with no stdout and an explicit stderr
diagnostic; an Orchestrator MUST NOT substitute an `unknown` or compatibility version.

Before the first **Iteration**, an Orchestrator MUST verify its preconditions and, on failure,
exit `1` **before** doing any work:

- `docs/agents/issue-tracker.md` exists (the signal that `/setup-agent-skills` has run). If
  absent, exit `1` with a stderr message pointing the operator at `/setup-agent-skills`. The loop
  MUST NOT invoke `/setup-agent-skills` itself (it is interactive and unsafe under
  `copilot --yolo -p`).
- `gh` is authenticated, and `git`, `copilot` are on `PATH`. The shell port additionally requires
  `jq`.
- The resolved `PROMPT.md` exists (see §4).
- If cost estimation is active, pricing data parses (phase 3).

## 2. Collection (phase 1, MUST)

At the start of every **Iteration**, an Orchestrator MUST rebuild the **Pool** from scratch —
never cache across iterations:

- List every **open** issue labeled `ready-for-agent` via `gh issue list`.
- (PR mode, phase 3+ / opt-in) When PR support is enabled, also list open `ready-for-agent`
  pull requests.

An empty Pool at the start of an Iteration is the **clean-exit-on-empty** condition (exit `0`,
§10). The next Iteration's collection — not any sentinel — is the source of truth on whether work
remains.

## 3. Discriminator (phase 1, MUST)

The Pool MUST be filtered to issues whose body contains **both** literal section headings:

- `## What to build`
- `## Acceptance criteria`

A `## Parent` section is optional. Issues missing either required heading (bare PRDs) MUST be
skipped. In PR mode a PR is kept only if it carries an `## Agent Brief` (in its body or any
comment) — the PR analogue of the discriminator.

## 4. Prompt assembly & agent invocation (phase 1, MUST)

Each Iteration MUST feed a single `copilot --yolo -p` invocation with, at minimum:

- the filtered Pool (rendered as the issue set),
- the last **five** commits, and
- the resolved **`PROMPT.md`**.

`PROMPT.md` resolution follows project → global → packaged precedence (the project copy wins).
Within the **project** scope the Orchestrator MUST probe the lowercase `git-loopy/prompt.md` first
and then the uppercase `git-loopy/PROMPT.md` (first hit wins): the kit ships the uppercase variant,
and probing the lowercase name first keeps the override resolvable on case-sensitive filesystems
(typical on Linux) while case-insensitive ones (APFS/HFS+ on macOS, NTFS on Windows) accept either
casing. The Orchestrator MUST capture the agent process's real exit status
(not the exit status of a pipe it is teed through) so an agent crash is never mistaken for a clean
turn. Streaming/live output is rendered per port (plain text in phase 1; the **TUI helper** from
phase 2).

## 5. Auto-close backstop (phase 1, MUST)

After the agent turn, the Orchestrator MUST walk the Iteration's **new** commit messages for
GitHub closing keywords and close any still-open referenced issue **that was in this Iteration's
Pool**, with a comment pointing at the commit SHA(s).

The close-keyword match MUST be equivalent to the reference regex
(`git_loopy.wrapper.CLOSE_KEYWORD_RE`):

```
(?i)(close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)
```

- Case-insensitive.
- Matched **line by line**, splitting on `\n` only (not on `\r`, `\v`, `\f`, or Unicode line
  separators — POSIX `grep` semantics).
- Referenced issue numbers deduplicated in **first-encounter order**.
- **Pool-whitelisted:** a `Closes #N` for an `N` not in this Iteration's Pool MUST be ignored, so
  a stale or mis-numbered reference cannot act on an unrelated issue.
- **Issues only:** the backstop MUST NOT close a PR. PRs are *advanced*, never closed, by the
  Orchestrator.

Any change to the commit-message convention in `PROMPT.md` MUST be mirrored here and in the
Conformance regex fixtures.

## 6. Progress & Strike accounting (phase 1, MUST)

An Iteration "made progress" **iff** it produced at least one **agent** commit **or** at least one
wrapper closure. (PR mode: a PR head-SHA advance also counts as progress.)

- A runner-authored **Checkpoint** (§7) MUST NOT count as progress.
- An Iteration that made no progress records a **Strike**.
- `GIT_LOOPY_MAX_NMT_STRIKES` (default `3`) **consecutive** no-progress Iterations end the Run
  with exit `1` (§10). Progress resets the consecutive-strike counter.
- The legacy `<promise>NO MORE TASKS</promise>` sentinel is **informational only**: counted as a
  Strike if the Iteration made no progress, otherwise ignored.

## 7. Checkpoint (phase 1, MUST)

After accounting, if the working tree has any uncommitted **or** untracked changes, the
Orchestrator MUST stage everything (`git add -A`, honouring `.gitignore`) and make exactly one
**close-keyword-free** commit attributed to the **Active issue**, so the next Iteration starts on
a clean tree and no work is lost. A Checkpoint:

- MUST NOT contain a closing keyword (it must never auto-close an issue),
- MUST be excluded from Strike progress (§6) and from the run-summary commit tally,
- MUST warn-but-not-abort on failure (e.g. nothing to commit).

## 8. Auto-push (phase 1, MUST)

Immediately after the Checkpoint, whenever the Iteration produced **new commits** (agent commits
and/or the Checkpoint just authored), the Orchestrator MUST `git push` the current branch to its
configured upstream. Push failures — no upstream, unreachable/missing remote, auth failure, or a
non-fast-forward rejection — MUST **warn but never abort**, so a **local-only repo completes
normally**. An Iteration that produced no new local commits MAY skip the push.

## 9. Iteration cap (phase 1, MUST)

An optional positional argument `N` caps the Run at `N` Iterations. `0` or omitted means
unlimited. Reaching the cap is a **clean** exit (`0`, §10). A non-numeric argument is a usage
error (exit `2`).

## 10. Exit codes (phase 1, MUST)

| Exit | Meaning              | When                                                                 |
| ---- | -------------------- | -------------------------------------------------------------------- |
| `0`  | Clean — queue empty  | An Iteration's collection (§2) finds the Pool empty.                 |
| `0`  | Clean — cap reached  | The optional iteration cap `N` (§9) is reached.                      |
| `1`  | Aborted — stuck      | `GIT_LOOPY_MAX_NMT_STRIKES` consecutive no-progress Iterations (§6). |
| `1`  | Aborted — preflight  | A required precondition failed before the first Iteration (§1).      |
| `2`  | Usage error          | Malformed invocation (e.g. non-numeric iteration cap, §9).           |

## 11. Environment-variable surface (MUST honour the phase-1 core)

Resolution precedence across the family is **CLI flag > env var > project config > global config >
built-in default** (config tiers arrive in phase 3; phase 1 honours CLI + env + default).

| Variable                       | Phase | Default          | Meaning                                                        |
| ------------------------------ | ----- | ---------------- | -------------------------------------------------------------- |
| `GIT_LOOPY_MODEL`              | 1     | `claude-opus-4.8`| Model id (bare base id).                                       |
| `GIT_LOOPY_REASONING_EFFORT`   | 1     | `max` for the built-in model | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`; omitted and explicit `none` are distinct. A recognized model-id suffix is peeled into this field, and selecting another model without an effort leaves it omitted so the backend chooses. |
| `GIT_LOOPY_ISSUE_SOURCE`       | 1     | `github`         | `github` or `prds` (legacy local-markdown mode).              |
| `GIT_LOOPY_MAX_NMT_STRIKES`    | 1     | `3`              | Consecutive no-progress Iterations before abort.              |
| `GIT_LOOPY_INCLUDE_PRS`        | 3     | off              | `1`/`true`/`yes` to also advance `ready-for-agent` PRs.       |
| `GIT_LOOPY_INTERACTIVE`        | 2     | auto (TTY)       | `0` disables the live interface (CI-safe).                     |
| `GIT_LOOPY_MODEL_SELECT`       | 3     | off              | `1` enters the startup model picker (**ModelSelectionMode**). |
| `GIT_LOOPY_DENY_TOOLS`         | 1     | empty            | Denylist of tools (set *union* across config tiers).          |
| `GIT_LOOPY_DENY_SKILLS`        | 1     | empty            | Denylist of skills (set *union* across config tiers).          |
| `GIT_LOOPY_SEND_TIMEOUT_SECONDS`| 1    | impl default     | Per-iteration agent send timeout.                             |
| `GIT_LOOPY_OTEL_ENABLED`       | 4     | off              | `1` enables OTLP export (or `OTEL_EXPORTER_OTLP_ENDPOINT`).    |
| `GIT_LOOPY_PRICING_FILE`       | 3     | packaged         | Override pricing table for cost estimation.                   |
| `GIT_LOOPY_MAX_PARALLEL`       | 5     | `1`              | **Lane** count in **Parallel mode**.                          |
| `GIT_LOOPY_WORKTREE_SETUP`     | 5     | none             | Per-worktree setup command for **Parallel mode**.             |

## 12. Event schema (phase 1, MUST)

Every Orchestrator MUST emit its structured record as JSONL using the shared **Event schema**
(`git_loopy.events`), so the **TUI helper**, the `.git-loopy/logs/<iso>-<run_id>.jsonl` replay
log, and any external consumer read one format regardless of which port produced it.
The additive Event schema has compatibility `schema_version` **1**; changing the Wrapper contract
does not implicitly change that version. The current fixture revision is **1.1** because
Continuation added optional event types without breaking schema-1 consumers. Unknown event types
and unknown payload fields remain additive and MUST be ignored by compatible consumers.

Every line shares this envelope, with keys in a stable order (envelope keys first, then payload
keys sorted):

```json
{"ts": "2026-05-16T00:00:00.000Z", "run_id": "01HXR...", "iter": 3, "type": "...", "...": "..."}
```

The `type` **string literals** — not the constant names — are the contract, and they are pinned
here against `git_loopy.events`. Wrapper-emitted types (phase 1 core): `wrapper.run.start`,
`wrapper.run.end`, `wrapper.iteration.start`, `wrapper.iteration.end`,
`wrapper.afk_ready.collected`, `wrapper.commit.recorded`, `wrapper.checkpoint.recorded`,
`wrapper.push.recorded`, `wrapper.auto_close`, `wrapper.strike`, `wrapper.pr.advanced`,
`wrapper.ask_user.attempted`. Continuation-schema 1.1 additions:
`wrapper.continuation.reconciled`, `wrapper.continuation_dispatch.started`,
`wrapper.continuation_dispatch.ended`, and `wrapper.continuation.stopped`. These are redacted
observations only and never carry authoritative fragments, secrets, or runnable Instructions.
Dashboard Insight additions within compatibility schema 1 are `wrapper.issue.activated`,
`agent.output`, and `usage.context_window`; `wrapper.skill_policy.resolved` is also a recognized
wrapper event. Producing these additive events is capability-dependent.
Note the shape: each is dotted `wrapper.<noun>.<verb>`, with underscores used only *within* a
segment (`afk_ready`, `auto_close`, `ask_user`, `pr`, `continuation_dispatch`), and two that are
two-part (`wrapper.auto_close`, `wrapper.strike`). SDK-mapped types (emitted when the port streams
SDK events): `session.created`, `session.idle`, `session.deleted`, `assistant.message`,
`assistant.reasoning`, `tool.call`, `tool.result`, `tool.permission_requested`,
`tool.permission_denied`, `usage.tokens`. Secrets MUST be scrubbed before a line is written. Ports
MUST copy these literals verbatim from `git_loopy.events`; a drifted literal (e.g. an underscore
where a dot belongs) is a conformance failure.

Every `wrapper.run.start` MUST carry the exact distribution `release_version`, numeric
`schema_version: 1`, and an
`insight_capabilities` object with exactly these boolean keys:

```json
{
  "agent_output": true,
  "structured_agent_events": true,
  "token_usage": true,
  "context_window": false,
  "skill_consultation": true,
  "cost": true
}
```

The values above are the Python Orchestrator's current manifest. Shell and PowerShell currently
declare all six values `false`; later work may change a value to `true` only when that
Orchestrator emits the signal truthfully. `false` means unavailable. `true` with no sample yet is
still unknown. Unknown scalar values are JSON `null`; an observed count of none is `0`, and an
observed collection with no members is `[]`.

The following additive Insight payload shapes are reserved by schema 1. Existing Phase 1 traces,
including payload-free `wrapper.iteration.end` records, remain valid. When an Orchestrator begins
emitting or enriching one of these records, it MUST use the pinned shape; the downstream
Orchestrator rollout tickets own enabling those producers.

- `wrapper.issue.activated`: `issue`, UTC RFC3339 `activated_at`, and `binding_source`. Once
  produced, one event authoritatively and immutably binds an Iteration to its Active issue.
- `agent.output`: `text` and `kind`, where the only schema-1 kind is `unclassified`. Once produced,
  native CLI text MUST NOT be relabeled as SDK reasoning, assistant, tool-call, or tool-result
  data.
- `usage.context_window`: `current_tokens`, nullable `token_limit`, nullable
  `effective_target_tokens`, and nullable `effective_ceiling_tokens`.
- An enriched `wrapper.iteration.end`: `outcome`, monotonic `duration_seconds`, normalized
  `summary`, and an `issues` contribution list.

The normalized `summary` requires `model`, `tokens_in`, `tokens_out`, `observed_tokens`,
`cost_usd`, `tool_count`, `skill_call_count`, sorted-distinct `skills_consulted`, `commits`,
`auto_closures`, `pr_advances`, `strikes`, and nullable `peak_context_window`. Each issue
contribution requires `issue`, `status`, UTC RFC3339 `first_started_at`, closure-only `closed_at`,
closure-only `issue_elapsed_seconds`, `active_seconds`, `cumulative_active_seconds`,
`consumption` (`model`, `tokens_in`, `tokens_out`), nullable `cost_usd`, and nullable
`peak_context_window`. Only authoritative source closure populates closure-only fields.

Envelope and nested timestamps MUST be RFC3339 UTC with a trailing `Z`. Durations MUST be
non-negative seconds measured from a monotonic clock; renderers MUST NOT derive them by
subtracting wall-clock timestamps.

## 13. Conformance (phase 1, MUST)

Each Orchestrator MUST pass the language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md) (`git-loopy/conformance/`):

- **Discriminator** — bodies that do / don't carry both required headings (§3).
- **Close-keyword regex** — a corpus of matching and non-matching commit messages, the pool
  whitelist, issues-only, and first-encounter dedup (§5).
- **Progress / Strike accounting** — scenarios mapping (agent commits, closures, checkpoints,
  PR advances) → progressed? / strike? (§6).
- **Checkpoint message** — the runner-authored subject/body/trailer per Active issue, its
  close-keyword freedom, and its detectability (§7).
- **Exit-code table** — the input → exit-code matrix of §10.
- **Event schema** — exact type literals and envelope-first, sorted-payload JSON serialization
  (§12).

The suite is the generalized successor to the cross-runner parity test ADR-0002 deleted. A
conformance fixture change is the canonical way to evolve the contract.

## 14. Per-issue model routing (phase 3, MUST)

Wherever an Orchestrator binds an Iteration to a **single Active issue at pickup** — the
Parallel-mode **Lane** (one issue per Lane) is the only such structural pickup seam — it MUST
resolve the model and reasoning effort **from that issue's labels**, not from the frozen
run-wide default:

- **Read, never infer.** Read the routing key off the issue's `task-type:<key>` labels; the
  `task-type:` prefix is the contract. The Orchestrator MUST NOT infer the type from the title,
  body, or any other heuristic, and MUST ignore non-`task-type:` labels.
- **Resolve to one pair.** Resolve the label(s) to a single `(model, effort)` via the shared
  `[routing]` config, honouring the family precedence spine (§11): `[routing]` is a
  **config-file-only** tier that replaces the *single global default* with a per-issue-type
  default — never a flag/env tier — and any explicit `--model` / `--reasoning-effort` (flag or
  env) suppresses routing run-wide. Selection is fixed: no `task-type:` label, an unknown key,
  or ≥2 keys resolving to different pairs fall back to the global default (the unknown-key and
  conflict cases warn); one known key, or ≥2 keys resolving to the same pair, use that pair.
- **Gate and fall back.** Pass the resolved effort through the shared effort gate against the
  model roster and apply the fallback (an effort the model does not accept drops to "let the
  backend pick"; an unknown model passes through). Routed **and** default pairs are gated
  identically.
- **Pass to the single invocation.** Feed the gated `(model, effort)` to that Iteration's one
  `--model` agent invocation (§4), reusing the same pair for the Lane's integration /
  auto-resolution session, so the Lane runs entirely on the resolved pair.
- **Resolve once.** Resolve **once** per issue at pickup; the Orchestrator MUST NOT switch model
  or effort mid-session.

The **serial (single-Lane) loop** hands the whole AFK-ready Pool to one Iteration and the agent
picks the Active issue mid-session, so there is no runner-side single-issue pickup to route on:
the serial loop keeps resolving to the global default (`claude-opus-4.8 @ max`) — zero-regression.
Serial per-issue routing is out of scope.

This decision is pinned by three language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md):
[`model-roster.json`](../git-loopy/conformance/model-roster.json) (the canonical
`model → accepted efforts` sets — its keys are the supported-model set),
[`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json) (labels + config →
resolved `(model, effort)` and whether it warns), and
[`effort-gate.json`](../git-loopy/conformance/effort-gate.json) (model + requested effort → gated
result and whether it warns). The Python reference adapter drives all three against the production
`resolve_iteration_model` and `gate_reasoning_effort` seams and asserts its in-language roster
constant equals `model-roster.json`. Native-port implementation of routing is future phase-3 work.

## 15. Native Continuation boundary (Continuation rollout, MUST)

The separately versioned [Continuation contract](continuation-contract.md) governs Producer
publication, Reconciliation, Dispatch evidence, capability declarations, and future Automation.
Wrapper contract 1.3 requires every supported Orchestrator distribution to expose the same public
namespace without making Continuation part of the Run loop:

```text
git-loopy continuation capabilities
git-loopy continuation publish
git-loopy continuation reconcile
git-loopy continuation record-dispatch-result
git-loopy continuation repair-index
```

`capabilities` MUST return the native distribution's truthful **Continuation capability
manifest**, including the exact distribution `release_version` and separately declared Wrapper,
Event, Continuation, and record-format compatibility versions. Capability never grants authority.
Every other operation MUST consume exactly one
UTF-8 JSON object from stdin or an explicitly selected input file. Machine responses emit exactly
one JSON object on stdout; diagnostics use stderr. Terminal rendering is available only through
an explicit `reconcile --terminal` selection.

Command exits are independent of Run exits: success and committed or idempotent receipts use `0`;
semantic or operational rejection uses `1`; malformed invocation uses `2`. An operation present
in the namespace but not advertised as supported MUST fail closed with exit `1`, and the command
boundary MUST never perform a **Continuation action**.

Continuation mode remains `off` by default. This foundation does not authorize report mode,
execute-frontier, or concurrent Dispatch.

## 16. Release and compatibility identity (MUST)

The **Release version** is product identity, not a compatibility shortcut. `--version`,
`wrapper.run.start`, and Continuation `capabilities` MUST report the same exact Release version for
one distribution. No other Event is required to repeat it, and advancing the Wrapper contract does
not advance the Event schema, Continuation contract, or record format.

Components selected as artifacts of one packaged distribution MUST have exact Release-version
equality and fail closed on drift. An externally discovered TUI helper from another Release MAY
remain usable when Event-schema and capability negotiation prove compatibility, but the
Orchestrator MUST warn that the Release versions differ. Release equality alone MUST NOT establish
cross-release compatibility.

## 17. Changing this contract

1. Update this document and bump the **Contract version**.
2. Add or update the corresponding **Conformance** fixture(s).
3. Update **every** Orchestrator (Python + each port) to pass the new fixtures.
4. If `PROMPT.md`'s commit-message convention changed, update `CLOSE_KEYWORD_RE`
   (`git-loopy/python/git_loopy/wrapper.py`) and the shell/PowerShell equivalents together.

No Orchestrator lands a contract change alone — the Conformance suite fails any port left behind,
which is the whole point of the backbone.

---

**See also:** [`docs/runners.md`](runners.md) (the operator-facing runner reference),
[ADR-0013](adr/0013-multi-language-runner-family.md) (the family decision),
[`docs/continuation-contract.md`](continuation-contract.md) (the independent Continuation
contract),
[`CONTEXT.md`](../CONTEXT.md) (the glossary).
