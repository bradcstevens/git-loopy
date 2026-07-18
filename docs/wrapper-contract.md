# The Wrapper Contract

> The language-neutral behavioural specification that **every** git-loopy **Orchestrator** — the
> Python reference runner and the shell, PowerShell, and future Rust ports — must satisfy. This
> is the single source of truth the [**Runner family**](../CONTEXT.md#the-runner-family)
> implements and the [**Conformance suite**](../git-loopy/conformance/README.md) pins. See
> [ADR-0013](adr/0013-multi-language-runner-family.md) for why the family exists and how it stays
> in lockstep.

**Contract version:** 1.0 (tracks the Python reference implementation in `git-loopy/python/`).

Terminology in **bold** (Run, Iteration, Pool, Strike, Checkpoint, Active issue, ...) is defined
in [`CONTEXT.md`](../CONTEXT.md). Where this spec and the Python code disagree, the code is the
temporary tie-breaker and the discrepancy is a contract bug to be reconciled — the intent is that
they never disagree, enforced by the Conformance suite.

Requirement levels use RFC-2119 **MUST / SHOULD / MAY**. Each invariant is tagged with the roadmap
phase that first requires it, so the phase-1 Conformance suite can pin the core loop before the
TUI, config, OTel, and parallel-mode phases land.

---

## 1. Preflight (phase 1, MUST)

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

`PROMPT.md` resolution follows project → global → packaged precedence (the project copy at
`./git-loopy/PROMPT.md` wins). The Orchestrator MUST capture the agent process's real exit status
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
`wrapper.ask_user.attempted`. Note the shape: each is dotted `wrapper.<noun>.<verb>`, with
underscores used only *within* a segment (`afk_ready`, `auto_close`, `ask_user`, `pr`), and two
that are two-part (`wrapper.auto_close`, `wrapper.strike`). SDK-mapped types (emitted when the port
streams SDK events): `session.created`, `session.idle`, `session.deleted`, `assistant.message`,
`assistant.reasoning`, `tool.call`, `tool.result`, `tool.permission_requested`,
`tool.permission_denied`, `usage.tokens`. Secrets MUST be scrubbed before a line is written. Ports
MUST copy these literals verbatim from `git_loopy.events`; a drifted literal (e.g. an underscore
where a dot belongs) is a conformance failure.

## 13. Conformance (phase 1, MUST)

Each Orchestrator MUST pass the language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md) (`git-loopy/conformance/`):

- **Discriminator** — bodies that do / don't carry both required headings (§3).
- **Close-keyword regex** — a corpus of matching and non-matching commit messages, the pool
  whitelist, issues-only, and first-encounter dedup (§5).
- **Progress / Strike accounting** — scenarios mapping (agent commits, closures, checkpoints,
  PR advances) → progressed? / strike? (§6).
- **Exit-code table** — the input → exit-code matrix of §10.
- **Event schema** — exact type literals and envelope-first, sorted-payload JSON serialization
  (§12).

The suite is the generalized successor to the cross-runner parity test ADR-0002 deleted. A
conformance fixture change is the canonical way to evolve the contract.

## 14. Changing this contract

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
[`CONTEXT.md`](../CONTEXT.md) (the glossary).
