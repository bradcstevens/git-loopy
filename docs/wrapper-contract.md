# The Wrapper Contract

> The language-neutral behavioural specification that **every** git-loopy **Orchestrator** — the
> Python reference runner and the shell, PowerShell, and future Rust ports — must satisfy. This
> is the single source of truth the [**Runner family**](../CONTEXT.md#the-runner-family)
> implements and the [**Conformance suite**](../git-loopy/conformance/README.md) pins. See
> [ADR-0013](adr/0013-multi-language-runner-family.md) for why the family exists and how it stays
> in lockstep.

**Contract version:** 1.22 (tracks the Python reference implementation in `git-loopy/python/`).

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

## 2. Collection (phase 1, MUST)

At the start of every **Iteration**, an Orchestrator MUST rebuild the **Pool** from scratch —
never cache across iterations:

- List every **open** issue labeled `ready-for-agent` via `gh issue list`.
- (PR mode, phase 3+ / opt-in) When PR support is enabled, also list open `ready-for-agent`
  pull requests.

An empty Pool at the start of an Iteration is the **clean-exit-on-empty** condition (exit `0`,
§10). The next Iteration's collection — not any sentinel — is the source of truth on whether work
remains.

### 2.1 Fetch completeness (contract 1.11, MUST)

The candidate list MUST be read **to completion**, and the read MUST request `created_at` among
its fields.

`gh issue list` pages internally up to whatever `--limit` asks for, so a page *shorter* than the
requested limit proves the source had nothing more to give and a page exactly at the limit proves
nothing. An Orchestrator MUST therefore re-ask with a **doubled** limit until a page comes back
short or the ceiling is reached. A fixed limit was survivable while the Pool was ordered
newest-first: it hid the *oldest* candidates, which nothing was about to select. Under §3.2 it
hides the *newest* — so an issue filed today, including a **Priority** one, would fall outside the
window exactly when it matters most.

#### The read schedule (contract 1.15, MUST)

The schedule is **shared**, not each member's own: the first ask MUST be `100` and the ceiling
MUST be `1600`, so the walk asks `100, 200, 400, 800, 1600` and stops. `issue-ordering.json`'s
`read_schedule` declares both, and its `read_cases` pin the walk each backlog produces.

This is a §3.2 obligation wearing a §2 hat. Two members that agree on every ordering rule and
disagree on the ceiling read *different backlogs* from the same repository, and a backlog is
exactly what §3.2 orders — so the divergent answer arrives as a different **Active issue** without
either member sorting anything differently, and nothing in the Event stream says why. The three
numbers were private constants agreeing by convention alone, which is the drift the shared
`created_at` field set was named once to prevent.

The decision one page forces — `complete` when it came back short, `continue` at a doubled limit
when it was full below the ceiling, `incomplete` when it was full *at* the ceiling — MUST be a
seam the Conformance adapter can call directly, for the same reason §3.2's comparison is one: an
adapter that reproduced the walk would agree with itself while the Orchestrator read a different
backlog.

A read still full at its ceiling is **not authoritative**: it establishes neither that the Pool is
empty nor which issue is the head of the order. An Orchestrator MUST report such a read to the
operator and MUST NOT treat it as the whole backlog. It is not a Run failure — selection is
unattended, and a partial Pool is still work.

`created_at` MUST ride the collected candidate rather than being fetched later: §3.2 orders
*collected items*, so a candidate that only acquires its timestamp at an authoritative per-issue
read acquires it after the decision that needed it. A member whose JSON reader coerces
ISO-8601-shaped strings into a native date type MUST read this field through a reader that does
not — a re-rendered value is a different value, and a coercing reader is typically *wider* than
§3.2's grammar, so it would silently rescue timestamps the rest of the family calls `malformed`.

A source with no page limit — the local-markdown Pool's directory walk — is complete by
construction and MUST report itself so, including when it is empty.

## 3. Discriminator (phase 1, MUST)

The Pool MUST be filtered to issues whose body contains **both** literal section headings:

- `## What to build`
- `## Acceptance criteria`

A `## Parent` section is optional. Issues missing either required heading (bare PRDs) MUST be
skipped. In PR mode a PR is kept only if it carries an `## Agent Brief` (in its body or any
comment) — the PR analogue of the discriminator.

### 3.1 Pool exclusions (contract 1.5, MUST)

A skipped candidate MUST be reported, not dropped silently. `ready-for-agent` is a *human*
assertion — somebody deliberately triaged that issue — so an Orchestrator that declines it owes
the operator the reason. An Orchestrator MUST, for every `ready-for-agent` candidate the
discriminator rejects, report the candidate's reference and one **exclusion reason** from this
closed vocabulary:

| Reason | Meaning |
| --- | --- |
| `missing_what_to_build` | `## Acceptance criteria` present, `## What to build` absent |
| `missing_acceptance_criteria` | `## What to build` present, `## Acceptance criteria` absent |
| `missing_both_sections` | Neither required heading present |

The reason MUST be derived from the same discriminator pass that decides membership, so the
reported reason and the membership decision cannot disagree. `discriminator.json` pins the
vocabulary and the reason for every case.

A candidate the source could **not read** (a failed per-issue view, an unreadable file) is NOT an
exclusion — it was never discriminated against, and reporting it as one would send the operator to
fix headings that are probably fine. The existing warn-and-skip path continues to cover it.

Exclusions MUST be reported as `wrapper.pool.excluded` Events (§12), before the
`wrapper.afk_ready.collected` they explain, and MUST also reach the operator's own output rather
than only a debug log. An **empty eligible Pool caused entirely by exclusions** MUST read
distinctly from a Pool that simply held no `ready-for-agent` work: the two demand opposite
operator responses. It does **not** change the exit code — both remain the clean-exit-on-empty
condition (§10).

Reporting an exclusion MUST NOT change Pool membership, and MUST NOT cost an extra source
round-trip: the cheap list read already carries the body the decision is made on.

### 3.2 Selection order (contract 1.10, MUST)

The eligible Pool has **one total order**, and it is part of this contract rather than each
member's own sort. All three Orchestrators previously called `gh issue list` with no sort
qualifier and inherited GitHub's unstated `sort=created&direction=desc`; the resulting
newest-first order was a CLI default nobody decided, and an issue could be passed over
indefinitely with no mechanism to notice (ADR-0032). Two members agreeing on eligibility while
disagreeing on *which* eligible issue comes first would pick different work from identical
input, and nothing in the Event stream would say why.

An Orchestrator MUST order eligible issues ascending by, in order of significance:

1. **Priority rank** — an issue carrying the `priority` label ranks ahead of one that does not.
   **Priority** is a *human assertion*, read like `parallel-safe` and never inferred from issue
   content, and it reorders work **without changing eligibility**: a Priority issue still needs
   `ready-for-agent`, still MUST pass the §3 discriminator, and still needs `parallel-safe` to
   enter a **Lane**.
2. **Creation timestamp** — the issue's `created_at` as the source reports it, ascending, so the
   oldest eligible issue is the head of the order. It MUST be read from the source and MUST NOT
   be computed locally or mutated.
3. **Issue number** — ascending. This is what makes the order **total**: no two distinct issues
   may compare equal, so the head of the order is one issue rather than a set.

The comparison MUST be a pure function of the fetched issue fields, exposed as a production
decision seam the Conformance adapter calls directly, and MUST NOT depend on the order the
source listed the issues in. Ordering the same input twice MUST yield the same sequence.

A **Task type** MUST NOT affect the order. It selects a **Routed pair** (§14) and nothing else.

A usable `created_at` is `YYYY-MM-DDThh:mm:ss`, optionally followed by a fractional second of
one to nine digits, followed by `Z`/`z` or a `±hh:mm` offset, naming a calendar-valid date in
the years **1970-9999**. Offsets MUST be normalized to an instant before comparison — comparing
the text would order `2026-03-01T01:00:00+01:00` after `2026-03-01T00:45:00Z`, when it is
fifteen minutes earlier. The grammar is deliberately narrow: every widening is a value the
members can disagree about, and an order they disagree on is worse than a value one of them
refuses by name.

An issue whose `created_at` is absent or does not satisfy that grammar MUST sort **last within
its own priority rank** — not last overall, because a broken field is not a retracted human
assertion — and MUST be reported with one **timestamp defect** from this closed vocabulary:

| Defect | Meaning |
| --- | --- |
| `absent` | The source carried no value (a null or empty field) |
| `malformed` | A value arrived that is not a timestamp in the accepted grammar |

The Run MUST NOT fail over an undated issue. Selection is unattended — `ready-for-agent` means
*ready for autonomous execution* — so an unusable ordering field is warned about and worked
around, never blocked on.

`issue-ordering.json` pins the order, the head it selects, and the defect every undated issue
carries. Eligibility is deliberately **absent** from that fixture: it is `discriminator.json`'s
decision and the `ready-for-agent` label's, and restating it beside the order would give one
rule two homes that could disagree.

#### The Pin (contract 1.14, MUST)

An Orchestrator MUST accept `--issue N`, which **pins** issue `N` for one invocation: the
Orchestrator works `N` instead of the head of the order above. Order is a policy, and an operator
sometimes has to override it for a single Run without weakening it for everyone.

The pin **bypasses the order and nothing else** (ADR-0032), which is four separate obligations:

1. **It is applied to the finished order, not folded into the comparison.** The pinned issue is
   moved to the head and every other issue keeps its §3.2 sequence behind it. The sort key stays
   what §3.2 requires — a pure function of the fetched issue fields — because a pin is one
   operator's instruction for one invocation and is not a property of any issue. A Run therefore
   resumes oldest-first the moment its pinned issue leaves the **Pool**.
2. **It outranks Priority.** A pinned issue reached the head because an operator named it,
   whatever its labels said. Were **Priority** to win, `--issue N` would work on most
   repositories and silently do nothing on exactly the ones that use the label. `wrapper.pickup.
   bound` MUST report `reason: pin` for that binding, and `order`/`priority` for every other
   binding in the same Run — the pin explains one binding, not the whole Run.
3. **It does not bypass eligibility, and an ineligible pin FAILS the invocation.** A pinned issue
   that is closed, missing, unreadable, lacks `ready-for-agent`, or fails the §3.1 AFK-ready
   discriminator MUST end the invocation with the `preflight_failed` exit code, naming what is
   wrong — and for a discriminator failure, naming the **specific missing section**, so the
   operator can fix the issue rather than guess. It MUST NOT fall back to the head of the order:
   §3.3 makes a candidate the runner cannot take a *skip* precisely because a serial Run merely
   walked past it, whereas a pin is an operator naming an issue, and there is no next candidate
   that honours what they asked for. Silently working a different issue than the one named is
   worse than stopping. In **Parallel mode** the pin MUST additionally carry `parallel-safe`,
   because a **Lane** Pool requires it and a pinned issue that never enters the Pool would leave
   the Run working the head of the order — the same silent substitution, arrived at by omission.
4. **It weakens nothing for any other issue.** The pin promotes; it does not restrict the Pool.
   Every other candidate remains eligible on exactly the terms §3.1 and §3.2 already set.

Exactly **one** issue may be pinned per invocation; a second `--issue` MUST be rejected as a
**usage error** (§10's `usage_error`, not `preflight_failed`) rather than resolved by taking
either value. The invocation is malformed, and "the last one wins" is the silent substitution
this section exists to prevent, arrived at by a CLI parsing default.

The pin MUST be invocation-scoped, and therefore MUST NOT be expressible as a label, an
environment variable, or a persisted Config key. All three outlive the invocation: a label is
global to the tracker and would point every concurrent Run at the same issue, an environment
variable is inherited by every Run launched from that shell, and a Config key by every Run in
that checkout. A flag is the only surface whose lifetime matches the thing being expressed.

Validation MUST happen **once, at preflight**, before the Pool is read. Once per Iteration would
end a healthy Run the moment it legitimately closed the issue it was pinned to, and validating
after the Pool would make an ineligible pin indistinguishable from a backlog that simply did not
contain it. `issue-ordering.json` carries the pin as a per-case `pin` field and pins that it
outranks Priority; the *refusal* is not in that fixture, for the same reason eligibility is not.

### 3.3 Serial Pickup (contract 1.12, MUST)

A serial **Iteration** MUST bind its **Active issue** *before* the agent session starts, from the
head of the §3.2 order. Selection is the runner's decision; the agent's is task type and nothing
else.

Until contract 1.12 the serial loop handed the agent the whole Pool with an instruction to rank
it, and learned which issue had been chosen from a **Working marker** the agent wrote mid-session
(§12, `binding_source: working_marker`), with `closure`, `commit`, and `single_member_pool` as
after-the-fact fallbacks when no marker arrived. That made §3.2 unobservable in serial mode — the
runner ordered a Pool whose order nothing consumed — and made the binding retroactive: everything
the agent produced before the marker had to be re-attributed once it landed.

An Orchestrator running serially MUST:

- **Pick before the session.** Walk the ordered Pool front to back and bind the first candidate it
  admits, emitting `wrapper.issue.activated` with `binding_source: serial_pickup` and the Pickup's
  own UTC instant. The binding is **prospective**: it precedes the first `agent.output` of the
  Iteration, so no output is ever attributed retroactively.
- **Render exactly the bound issue.** The prompt's issue set MUST carry that one issue (§4), not
  the Pool. An Orchestrator MUST NOT instruct the agent to select from a set.
- **Skip and advance, never fail.** A candidate the Orchestrator cannot admit — today, one whose
  `task-type:` label refuses to resolve a **Routed pair** (§14) — MUST be passed over and the next
  candidate tried. A serial Run has no second Lane to leave the candidate for, so a refusal that
  ended the Run would end it over one mislabelled issue. Each skip MUST be reported with the
  issue and the reason, as a `wrapper.pickup.skipped` Event (§12) and not only as a diagnostic.

  **The admissible set is the member's own, and today only one member has a refusal to make.**
  Admission is whatever an Orchestrator must resolve at Pickup in order to start the session on
  the bound issue, and the only such resolution the contract names is §14's Routed pair — which
  §14 records as *"future phase-3 work"* for the native ports. A member that resolves no Routed
  pair therefore admits every candidate and binds the head of the order, which satisfies this
  rule rather than skipping it: it has nothing it can refuse. When a native port implements §14
  it acquires the refusal and this bullet with it, and its skips and its all-skipped Strike MUST
  then match the reference member's. Admission MUST NOT be widened past that into re-deciding
  eligibility — the `ready-for-agent` label and the AFK-ready discriminator settle that at
  collection (§3.1), and a second opinion at Pickup would be a second place for it to disagree.
- **Record the binding (contract 1.13).** A **Pickup** that binds MUST emit
  `wrapper.pickup.bound` (§12) carrying the issue, the selection reason — `pin` when §3.2's
  **Pin** named this candidate, else `priority` or `order` — and where the candidate sat in the
  order. Selection is the runner's decision as of contract 1.12, and a decision nobody
  can see is a decision nobody can audit: the starvation §3.2 exists to end was invisible
  precisely because being passed over left no trace. The record is emitted *after* the
  `wrapper.issue.activated` that publishes the binding, so it never describes a binding the rest
  of the stream does not contain, and every skip that ended in this binding MUST precede it.
  An Orchestrator that resolved a **Routing resolution** at this Pickup MUST carry it on the same
  record (contract 1.21, §14) rather than on an Event of its own.
- **Select and publish are two steps.** The prompt renders the candidate the Pickup *selected*,
  even when publishing its activation fails. An Orchestrator MUST NOT fall back to rendering the
  whole Pool on a failed activation: that restores the menu this section removes, on the one path
  where the runner has already privately chosen a head. The Iteration proceeds unbound — §12's
  fallbacks are exactly what that case is for — and MUST say so.
- **Distinguish "all skipped" from "empty".** A Pool that is empty ends the Run cleanly (§10). A
  non-empty Pool in which *every* candidate was skipped is an Iteration that did no work: it MUST
  count a **Strike** and the Run MUST continue, because the condition is a labelling mistake an
  operator can fix while the Run is still alive.

A **Working marker** remains part of `PROMPT.md`, but its meaning changes with the binding: it is
**attribution, not selection**. A marker naming the bound issue is confirmation. A marker naming a
different issue MUST NOT rebind — the Pickup stands, the disagreement is warned about, and the
marker is recorded rather than obeyed. This is the same immutability §12 already requires of the
first activation; contract 1.12 only moves which event is first.

Serial Pickup does not change **Rolling dispatch**: a **Lane** already binds one issue at pickup
(`binding_source: lane_pickup`) and keeps doing so — and emits the same `wrapper.pickup.bound`
record when it does, because an operator auditing selection is asking one question about a Run,
not two questions about two schedulers.

## 4. Prompt assembly & agent invocation (phase 1, MUST)

Each Iteration MUST feed a single `copilot --yolo -p` invocation with, at minimum:

- the issue set — for a serial Iteration, the **one** issue bound by §3.3; for a Lane, its own
  reserved issue,
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
  a stale or mis-numbered reference cannot act on an unrelated issue. The whitelist is the
  **Pool**, not the §3.3 bound issue: an agent that finishes a neighbouring issue on the way
  through should still close it, and narrowing the whitelist to the binding would silently drop
  those closures.
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
| `GIT_LOOPY_MODEL`              | 1     | `claude-opus-5`  | Model id (bare base id).                                       |
| `GIT_LOOPY_REASONING_EFFORT`   | 1     | `xhigh` for the built-in model | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`; omitted and explicit `none` are distinct. A recognized model-id suffix is peeled into this field, and selecting another model without an effort leaves it omitted so the backend chooses. |
| `GIT_LOOPY_ISSUE_SOURCE`       | 1     | `github`         | `github` or `prds` (legacy local-markdown mode).              |
| `GIT_LOOPY_MAX_NMT_STRIKES`    | 1     | `3`              | Consecutive no-progress Iterations before abort.              |
| `GIT_LOOPY_INCLUDE_PRS`        | 3     | off              | `1`/`true`/`yes` to also advance `ready-for-agent` PRs.       |
| `GIT_LOOPY_INTERACTIVE`        | 2     | auto (TTY)       | `0` disables the live interface (CI-safe).                     |
| `GIT_LOOPY_MODEL_SELECT`       | 3     | off              | `1` enters the startup model picker (**ModelSelectionMode**). |
| `GIT_LOOPY_DENY_TOOLS`         | 1     | empty            | Denylist of tools (set *union* across config tiers).          |
| `GIT_LOOPY_DENY_SKILLS`        | 1     | empty            | Deprecated denylist of skills (set *union* across config tiers); subtracts only (§17). |
| `GIT_LOOPY_ENABLED_SKILLS`     | 3     | unset            | Exact replacement of the configured base **Skill policy** for one Run; an explicit empty value is a real empty policy (§17). |
| `GIT_LOOPY_SEND_TIMEOUT_SECONDS`| 1    | impl default     | Per-iteration agent send timeout.                             |
| `GIT_LOOPY_OTEL_ENABLED`       | 4     | off              | `1` enables OTLP export (or `OTEL_EXPORTER_OTLP_ENDPOINT`).    |
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
`wrapper.ask_user.attempted`. Contract-1.5 addition within compatibility schema 1:
`wrapper.pool.excluded` — one per `ready-for-agent` candidate the discriminator rejected (§3.1),
carrying `issue`, `title`, and one `reason` from the closed exclusion vocabulary, emitted in source
order *before* the `wrapper.afk_ready.collected` it explains. That collection Event additionally
carries `excluded`, the count of exclusions, so a replay can tell an empty tracker apart from a
tracker whose every candidate was rejected. `wrapper.pool.excluded` is Run-scoped, never
contribution-scoped: it names work that never became a **Lane contribution**, so it carries the
collecting Iteration's `iter` and no contribution identity. Continuation-schema 1.1 additions:
`wrapper.continuation.reconciled`, `wrapper.continuation_dispatch.started`,
`wrapper.continuation_dispatch.ended`, and `wrapper.continuation.stopped`. These are redacted
observations only and never carry authoritative fragments, secrets, or runnable Instructions.
Dashboard Insight additions within compatibility schema 1 are `wrapper.issue.activated`,
`agent.output`, and `usage.context_window`; `wrapper.skill_policy.resolved` is the redacted
Run-scoped record of the frozen **Effective Skill policy** (§17). `wrapper.dashboard.fault` is
the Run-scoped record of a **Dashboard fault** — a Dashboard that raised, either while running or
while coming up, which the Run survives as an involuntary **Detach** (ADR-0024). One event covers
both, because a replay needs to tell a fault from a voluntary Detach, not one fault from another.
It carries `error_type` and the scrubbed `error` text,
so a replay can tell a Run the operator walked away from apart from one whose live view crashed
out from under them; a voluntary Detach records no fault, which is the distinction. It is
Run-scoped in **Parallel mode** too, never contribution-scoped: the fault is a fact about the
Dashboard, not about any **Lane**, and every in-flight Lane contribution — including one being
integrated — runs on to its natural outcome and keeps emitting its own events, now to the line
printer (#327). Only an
Orchestrator that hosts a Dashboard can emit it — the shell and PowerShell Orchestrators host
none and never do. Rolling-dispatch additions
within compatibility schema 1 are listed under *Rolling-dispatch contribution lifecycle* below.
Producing these additive events is capability-dependent.
Note the shape: each is dotted `wrapper.<noun>.<verb>`, with underscores used only *within* a
segment (`afk_ready`, `auto_close`, `ask_user`, `pr`, `continuation_dispatch`, `work_finished`,
`branch_observed`, `recovery_started`, `refill_turn`), and two that are
two-part (`wrapper.auto_close`, `wrapper.strike`). SDK-mapped types (emitted when the port streams
SDK events): `session.created`, `session.idle`, `session.deleted`, `assistant.message`,
`assistant.reasoning`, `tool.call`, `tool.result`, `tool.permission_requested`,
`tool.permission_denied`, `usage.tokens`, `session.error`, `model.call_failure`. Secrets MUST be
scrubbed before a line is written. Ports
MUST copy these literals verbatim from `git_loopy.events`; a drifted literal (e.g. an underscore
where a dot belongs) is a conformance failure.

The last two carry the harness's own account of a failure — its error type, its message, the
service status code where one was reached, and for a call failure the source that failed —
rather than a sentence about it, which is what lets an Orchestrator tell an exhausted quota, a
rate limit, and a rejected credential apart from a session that merely produced nothing (#403).
Mapping them is recording, not reacting: an Orchestrator MUST NOT abort a **Run**, back off, or
withhold an issue on the strength of one. As with every SDK-mapped type, a port that streams no
SDK events declares the literals and emits neither.

Contract-1.21 addition within compatibility schema 1, and an **extension of an existing record**
rather than a new type: `wrapper.pickup.bound` carries the **Routing resolution** its Pickup
reached (§14) as `model`, `effort`, `context_tier`, `routing_source`, `task_type_keys`,
`gate_warnings` and `lifecycle_position`. `model` and `effort` are the family's existing
routed-pair vocabulary — the same two words a Contribution's `summary` already uses — so a
consumer reads a Pickup's pair the way it reads a Lane's. `routing_source` is spelled in full
rather than as `source` because the same payload's `reason` answers "why" in the unrelated Pickup
vocabulary (`order` / `priority` / `pin`), and one record cannot carry two differently-scoped
answers to one word. Every one of the seven is optional-when-present, so a Runner that implements
no routing emits the binding exactly as before and stays conforming; `null` is a *value* (the
backend chooses) and not an absence. `event_schema_version` does not move: the seven are additive
payload fields, which every schema-1 consumer already ignores when unknown.

Every `wrapper.run.start` MUST carry the exact distribution `release_version`, numeric
`schema_version: 1`, and an
`insight_capabilities` object with at least these boolean keys:

```json
{
  "agent_output": true,
  "structured_agent_events": true,
  "token_usage": true,
  "context_window": true,
  "skill_consultation": true,
  "cost": true
}
```

The values above are the Python Orchestrator's current manifest. The shell and PowerShell
Orchestrators declare only `agent_output` available. Later work may change a value to `true` only
when that Orchestrator emits the signal truthfully. `false` means unavailable. `true` with no sample
yet is still unknown. Unknown scalar values are JSON `null`; an observed count of none is `0`, and
an observed collection with no members is `[]`.

### Run-scoped Insight capabilities

The six keys above are **per-distribution**: they answer "can this Orchestrator observe it at all?",
so they are the same for every Run of one binary and every port MUST declare all six. A **run-scoped**
capability answers a different question — "did *this* Run obtain it?" — and two Runs of one binary
can differ. Run-scoped keys are declared in the same object, are **accepted from any producer and
required of none**, and are listed under `insight_capabilities.run_scoped` in
`conformance/event-schema.json`.

There is one today, `rate_card` (ADR-0026). An Orchestrator that declares it MUST also carry a
`rate_card` key on `wrapper.run.start`: the **Rate card** it resolved, or `null` when it resolved
none. The card is the harness's own live per-model price listing, obtained from the same
`models.list` call that supplies the roster and the picker's premium column, resolved **once** at Run
start and held fixed for the whole Run, so every row of one **Summary** is denominated identically
even if the server reprices mid-Run. It MUST NOT be loaded from a packaged file — a pinned fixture
cannot be correct under `COPILOT_CLI_PATH` (ADR-0019).

The card is **provenance, not arithmetic**. No figure anywhere in the kit derives from it, so an
absent card never costs a figure and never affects the separate `cost` declaration. Its prices are
denominated in **AI Credits per batch of `batch_size` tokens** — the same unit the harness already
reports as *billed* — and MUST be recorded as published: separate input, output, cache-read and
cache-write prices, the batch size, the prompt-token budget and the nested `long_context` block.
Flattening them to one rate is a conformance failure: the cache prices dominate a real agent loop,
and a card recorded lossily is not a record of what a Run was billed under.

A card that cannot be fetched MUST NOT prevent a Run from starting. The Run warns on exactly the
terms the roster fetch failure already uses, declares the capability `false`, and proceeds.

```json
{
  "insight_capabilities": { "cost": true, "rate_card": true },
  "rate_card": {
    "models": {
      "claude-haiku-4.5": {
        "multiplier": 0.33,
        "discount_percent": null,
        "prices": {
          "batch_size": 1000000,
          "input_price": 0.1,
          "output_price": 0.4,
          "cache_read_price": 0.01,
          "cache_write_price": 0.125,
          "max_prompt_tokens": 128000,
          "long_context": null
        }
      }
    }
  }
}
```

All three Orchestrators declare `rate_card` (#334). The shell and PowerShell Orchestrators subscribe
to no SDK event stream and read no model listing, so they resolve no card on any Run: each declares
the capability `false` and publishes `rate_card: null` beside it. They declare it rather than omit
it because an omitted key leaves a **Dashboard** unable to tell *this Orchestrator cannot report a
rate* from *this Run's prices failed to load*, which is the collapse a separate declaration exists
to end. A port that declares it `false` MUST hold no price data and MUST fetch nothing.

Every `wrapper.run.start` MUST also carry a `parallel_capabilities` object with exactly these
boolean keys:

```json
{
  "parallel_mode": true,
  "rolling_dispatch": true,
  "integration_backlog": true,
  "adaptive_lane_limit": true,
  "contribution_events": false
}
```

`insight_capabilities` says what an Orchestrator can *observe*; `parallel_capabilities` says what it
can *schedule*. The values above are the Python Orchestrator's current manifest; the shell and
PowerShell Orchestrators declare every key `false`. `parallel_mode` is whether the Orchestrator can
fill more than one **Lane** at a time, `rolling_dispatch` whether it refills them continuously
toward the **Lane cap** rather than behind a barrier, `integration_backlog` whether it admits
finished Lane branches to the bounded backlog described below, `adaptive_lane_limit` whether its
**Effective Lane limit** reacts to **Pressure signals**, and `contribution_events` whether it emits
the **Lane contribution** lifecycle stream. Python declares `contribution_events: false` today
because those literals are reserved and have no producer: a Parallel Run still records legacy
**Wave**-shaped rows, and advertising a stream no replay contains would be the same lie as reporting
an unavailable counter as `0`.

`parallel_mode: false` is not one `false` among five. Refill, the backlog, adaptation, and the
contribution stream all presuppose Parallel mode, so an Orchestrator that declares `parallel_mode`
unavailable MUST declare every other parallel capability unavailable with it. It MUST additionally
**refuse** a requested Lane cap above 1 at preflight, naming the unsupported capability, the
distribution that cannot honour it, and the setting the operator can change — a refused Run exits
with the preflight-failure code (§10). Accepting the cap and running serially is forbidden: a
silently serial Run is byte-identical to a Parallel Run whose tracker carries no `parallel-safe`
issue, so the operator cannot tell an unimplemented feature from an unlabelled backlog.

The following additive Insight payload shapes are reserved by schema 1. Existing Phase 1 traces,
including payload-free `wrapper.iteration.end` records, remain valid. When an Orchestrator begins
emitting or enriching one of these records, it MUST use the pinned shape; the downstream
Orchestrator rollout tickets own enabling those producers.

- `wrapper.issue.activated`: `issue`, UTC RFC3339 `activated_at`, and `binding_source`. Once
  produced, one event authoritatively and immutably binds an Iteration to its Active issue.
  A serial Iteration binds at **Pickup** with `serial_pickup` (§3.3); `working_marker`,
  `closure`, `commit`, and `single_member_pool` remain in the vocabulary because a stream
  recorded before contract 1.12 carries them and MUST still replay. Parallel Lane pickup uses
  `lane_pickup`. A later marker or fallback never replaces the first binding. Output and
  Consumption observed before the event remain pending and are attributed
  when the event arrives. A record whose `activated_at` is absent or is not a resolvable instant
  is not a valid activation: it MUST NOT bind, because binding it would republish a
  non-RFC3339 `first_started_at` on every later Iteration end. The Iteration reports no issue
  contribution and the Run continues.
- `wrapper.pickup.bound` and `wrapper.pickup.skipped` (contract 1.13): `issue`, `reason`,
  `position`, and `considered` — which issue, why, where it sat in the order, and how long the
  order was. Both are **Run-scoped**: they carry no `contribution_id` and no `lane_id`, because a
  Lane's contribution identity is minted when its session starts and a Pickup happens before
  that — an Event that demanded the identity triple could never be emitted at the moment it
  describes. `reason` on a binding is one of `order`, `priority`, or `pin` — `pin` exactly when
  §3.2's **Pin** named the bound candidate, which outranks a `priority` label on the same issue;
  on a skip it is the free-text reason the candidate was passed over. `considered` is required rather than derivable:
  *the runner took the oldest* and *the runner took the only one left* are different facts about
  a backlog, and `position: 1` alone cannot tell them apart. Every skip that ended in a binding
  MUST precede that binding, which is the same ordering `wrapper.pool.excluded` already keeps
  against `wrapper.afk_ready.collected`. A stream recorded before contract 1.13 carries neither
  record and MUST still replay: a consumer that requires them to reconstruct a Run is reading a
  guarantee this schema does not make.
- `agent.output`: `text` and `kind`, where the only schema-1 kind is `unclassified`. Once produced,
  native CLI text MUST NOT be relabeled as SDK reasoning, assistant, tool-call, or tool-result
  data.
- `usage.context_window`: `current_tokens`, nullable `token_limit`, nullable
  `effective_target_tokens`, and nullable `effective_ceiling_tokens`.
- An enriched `wrapper.iteration.end`: `outcome`, monotonic `duration_seconds`, normalized
  `summary`, and an `issues` contribution list.

The normalized `summary` requires `model`, `tokens_in`, `tokens_out`, `observed_tokens`,
`tool_count`, `skill_call_count`, sorted-distinct `skills_consulted`, `commits`,
`auto_closures`, `pr_advances`, `strikes`, and nullable `peak_context_window`. Each issue
contribution requires `issue`, `status`, UTC RFC3339 `first_started_at`, closure-only `closed_at`,
closure-only `issue_elapsed_seconds`, `active_seconds`, `cumulative_active_seconds`,
`consumption` (`model`, `tokens_in`, `tokens_out`), and nullable `peak_context_window`. Only
authoritative source closure populates closure-only fields.

Cost is the harness's reported billing — optional `credits`, `premium_requests`, `cache_read` and
`cache_write`, added additively (ADR-0026). They are optional rather than required precisely so an
Orchestrator that cannot observe billing omits them rather than fabricating a figure. The
dollar-named `cost_usd` is **retired**: it was git-loopy's own list-price estimate, the price table
is deleted, and it is never repurposed to carry Credits — a consumer must never read Credits out of
a key whose name says dollars. Producers that still emit it remain conformant; consumers ignore it.

The shell and PowerShell Orchestrators emit this normalized payload from their native observable
boundaries. Iteration, Active-issue, and cumulative Active durations come from each Orchestrator's
monotonic clock; agent commits, successful wrapper closures, PR advances, and Strikes remain
observed counts. Model and token Consumption, structured tool and Skill activity, Context fill,
and Cost remain `null` because native Copilot CLI output does not expose those
measurements. The configured model is not a substitute for observed Consumption, and unavailable
counters or collections MUST NOT be reported as `0` or `[]`.

Envelope and nested timestamps MUST be RFC3339 UTC with a trailing `Z`. Durations MUST be
non-negative seconds measured from a monotonic clock; renderers MUST NOT derive them by
subtracting wall-clock timestamps.

### Rolling-dispatch contribution lifecycle

**Rolling dispatch** (Parallel mode) has no barrier round, so a Parallel record belongs to a
**Lane contribution** rather than to an **Iteration**
([ADR-0020](adr/0020-rolling-dispatch-with-bounded-green-integration.md)). These additive type
literals are reserved within compatibility schema 1. Contribution lifecycle:
`wrapper.contribution.start`,
`wrapper.contribution.work_finished`, `wrapper.integration.parked`,
`wrapper.integration.admitted`, `wrapper.integration.started`,
`wrapper.integration.branch_observed`, `wrapper.integration.recovery_started`,
`wrapper.integration.published`, and `wrapper.contribution.end`. Scheduler-scoped:
`wrapper.pool.refreshed`, `wrapper.concurrency.changed`, `wrapper.serial.requested`,
`wrapper.pipeline.quiescent`, `wrapper.rolling.refill_turn`, and
`wrapper.parallel.serial_fallback`.

- **Identity, not Lane.** Every contribution-scoped record MUST carry `contribution_id`, `issue`,
  and `lane_id`, and its envelope `iter` MUST be `null`. `lane_id` is the reusable **Lane** the
  contribution *started* in and never changes, because a Lane is refillable the moment its
  contribution is admitted to **Integration** — a record identifying only its Lane becomes
  unattributable as soon as the next contribution starts there. Consumers MUST NOT rely on a
  mutable Lane→issue lookup.
- **Stamped existing records.** A Lane's ordinary records — `assistant.*`, `tool.*`,
  `usage.tokens`, `usage.context_window`, `agent.output`, `wrapper.commit.recorded`,
  `wrapper.checkpoint.recorded`, `wrapper.auto_close` — carry the same triple when they belong to
  a contribution. The same literals remain valid, unstamped, for serial Iterations.
- **Scope separation.** A Lane contribution MUST NOT emit `wrapper.iteration.start` or
  `wrapper.iteration.end`; a serial Iteration keeps both and its positive `iter`.
- **`wrapper.contribution.end` is the finalized Parallel row and the Strike transition.** Its
  `reason` MUST at least distinguish `published`, `unchanged_branch`, `checkpoint_failed`, and
  `serial_fallback`; only `published` is Parallel progress. A publication whose runner-driven
  closure has not yet verified is *not* a contribution end. Lane-work and recovery Consumption and
  commits appear exactly once, in the originating contribution, and runner **Checkpoint** commits
  stay out of the commit total.
- **Unknown stays unknown.** `wrapper.concurrency.changed` reports the immutable configured Lane
  cap and the current effective limit, and reports a signal the Run cannot observe as `null` —
  never an estimate and never `0`. It is emitted for an authoritative transition, not per
  observation.
- **Legacy traces.** Historical **Wave** logs carry `lane_issue` and no contribution identity.
  They remain readable and MUST NOT be reinterpreted as contributions.
- **The backlog is bounded, and the bound is the whole point.** **Integration** is one serialized
  stage, and the **Integration backlog** it consumes has a high-water mark of exactly **two** — one
  contribution integrating plus one waiter. A third finisher emits
  `wrapper.integration.parked`, keeps its **Lane** occupied, and waits; admission is FIFO by
  finish order, broken by ascending issue number, and a parked contribution enters the backlog on
  `wrapper.integration.admitted`. That admission — not publication — is what frees the Lane for
  refill, which is why a record identifies its contribution and not its Lane. A full backlog is
  **Integration backpressure**: **Rolling dispatch** stops *starting* new Lane work while it holds,
  and resumes the moment a slot frees. It is a refill bound, never a pause — Lanes already running
  finish normally and nothing is cancelled. This is what makes the **Lane cap** a ceiling rather
  than a utilization promise, so a Run that never reaches its cap is not thereby faulty.
- **Integration is gated privately and recovery is bounded.** Each contribution is merged into its
  own private **Integration stage** against the latest published green base and re-runs the feedback
  loops *there*, so a red or conflicting result is never observable on the base branch and there is
  nothing to undo. `wrapper.integration.branch_observed` reports how many publications landed since
  that branch was cut, or `null` when the Run cannot observe it. A conflicting or failing
  contribution gets bounded runner-driven recovery in the same stage: each attempt emits
  `wrapper.integration.recovery_started` with its `attempt` and the immutable `max_attempts`, and
  attempts MUST NOT exceed three. Recovery Consumption and commits are counted once, in the
  originating contribution. Persistent failure ends the contribution unpublished rather than
  publishing something the loops did not pass.
- **A Run that requested Parallel mode says so.** An Orchestrator that implements Parallel mode
  SHOULD carry `parallel_mode`, `lane_cap`, and `effective_lane_limit` on `wrapper.run.start`, and
  MUST emit `wrapper.parallel.serial_fallback` once per serial **Iteration** it works because it
  found no eligible **Parallel-safe** candidate — with the eligible count and a `reason` from the
  closed vocabulary `no_parallel_safe_candidates`, `all_parallel_safe_worked`,
  `parallel_safe_unavailable`. Eligibility is a human assertion the runner never infers, so the
  reason MUST reach the operator's own output and not only the Event stream; otherwise a Run whose
  tracker carries no `parallel-safe` issue is byte-identical to a serial Run and reads as a broken
  flag. A serial turn granted while eligible Lane work remains is interleaving, not a fallback, and
  emits nothing. A Run that did not request Parallel mode emits none of these.

The `contribution_identity` and `payload_contracts` sections of
[`event-schema.json`](../git-loopy/conformance/event-schema.json) pin this vocabulary, its
`rolling_stream_cases` pin whole ordered streams — Lane refill after admission, parking against a
full backlog, bounded recovery, the serial latch, and a Parallel Run that never engaged — and the
serialization cases pin the wire form. Every family member drives those streams through its own
production serializer, including the members that schedule no Lane: an Orchestrator that cannot
*produce* a rolling record must still read and write the same bytes. Its `parallel_capabilities`
section pins each Orchestrator's manifest. As with the other reserved Insight shapes above,
producing these records is capability-dependent and the rolling-dispatch Orchestrator tickets own
enabling the producers; the Event-schema fixture revision advances with the first Orchestrator that
emits them, since that revision is what a distribution's capability manifest advertises.
[`docs/parallel-mode.md`](parallel-mode.md) is the operator-facing companion to this section.

### Calibration records (contract 1.16, Python-only)

A **Calibration** buys **Trials**, and a Trial contains an agent session that anywhere else in
git-loopy would be an **Iteration**. It deliberately is not one
([ADR-0027](adr/0027-routing-is-calibrated-by-measurement.md)). Iterations are attributed to a
**Run** and tick the **Strike** counter, and that counter is shared and consecutive — reaching
the limit ends the Run. A Trial belongs to a Calibration; an Iteration belongs to a Run.

These additive type literals are reserved within compatibility schema 1: `calibration.trial.start`
and `calibration.trial.end`. Like the **measured tier** they serve they are **Python-only**
(§14.1); the shell and PowerShell Orchestrators declare the literals so the vocabulary stays whole
and never emit them.

- **No Run, and the record says so.** Every Calibration record — the lifecycle pair *and* the
  ordinary records a Trial's session writes (`assistant.*`, `tool.*`, `usage.tokens`,
  `usage.context_window`, `agent.output`) — MUST carry `run_id: null`, a null `iter`, and the
  identity pair `calibration_id` / `trial_id`. `run_id: null` is what keeps a Trial's
  **Consumption** out of a Run's Cost totals and stops a consumer rendering a phantom Run.
- **`trial_id` is per Trial, not per Proving task.** A Calibration legitimately runs the same
  **Proving task** at several rungs, which the task pin alone could not separate.
- **Scope separation.** A Calibration MUST NOT emit `wrapper.run.start`, `wrapper.run.end`,
  `wrapper.iteration.start`, `wrapper.iteration.end` or `wrapper.strike`. It produces no Run
  summary row, no **Queue** entry and no Iteration number, and it never ends or aborts a Run.
- **An interrupted Trial leaves a `start` with no `end`.** It produced no measurement, and a
  synthesised result would put a Trial the search never scored into the record.
- **Consumers tolerate a Calibration-only stream.** A **Dashboard** or replay reader MUST handle
  a stream carrying no Run lifecycle records at all, and MUST NOT adopt a Calibration record's
  identity as a Run's.

### Renderer-neutral Dashboard seam

The language-neutral
[`dashboard-insights.json`](../git-loopy/conformance/dashboard-insights.json) fixture is the
semantic boundary between Orchestrators and live-interface implementations. It supplies normalized
Event prefixes plus injected render time, local UTC offset, and configured Run facts, then pins the
expected toolkit-neutral Dashboard and per-issue drill-in model.

The Dashboard inventory is `Header -> Queue -> Activity -> Summary`. The per-issue drill-in is
`detail header -> Iteration breakdown -> Log`. Queue columns are ordered `Issue | Status | Started
| Active | Closed | Iters | Tokens in | Tokens out | Credits | Premium`. Iteration-breakdown
columns are ordered `Contribution | Outcome | Duration | Status | Active | Tokens in | Tokens out
| Cache read | Cache write | Credits | Premium | Peak Context fill`, where `Outcome` and `Duration`
are the owning Iteration's own disposition and monotonic duration while `Status` and `Active`
remain scoped to the issue within that contribution. `Cache read` and `Cache write` are components
of `Tokens in` rather than figures beside it, so no total sums them in; they are drill-in detail
and reach no Queue or Summary column. Context
fill is current-Iteration scoped; Queue accounting and the Log aggregate one issue across
contributions; each Summary row is one Iteration or Lane contribution; and the Iteration breakdown
is the same ordered contribution set counted by `Iters`.

An unavailable value projects to an em dash, while observed none remains `0` or `[]`. A
capability an Orchestrator declares unavailable at Run start arrives as a `null` normalized
measurement, and renderers MUST project it as unknown rather than as an observed `0`, `[]`, or a
substituted configured value — including a contribution whose whole `consumption` record is
unavailable. Renderers localize UTC timestamps from the supplied display-zone input but preserve
monotonic durations.
This rule binds every renderer surface for a Run, not just the live band: the
per-Iteration frozen artifact and the run-end totals artifact project the same unknowns, and a
cumulative total is unknown only when every completed Iteration in it declared that measurement
unavailable.
Glyphs, colors, widths, responsive truncation, keybindings, and toolkit widget structure are not
contractual. Future renderer issue #143 MUST consume this seam rather than redefine its inventory
or semantic meaning.

#### Per-Orchestrator obligations at this seam

The fixture's `semantic_contract` is the single declaration of what a Dashboard *is*. Its
`projection_fields` inventory names every field of every band, its `queue_columns` and
`iteration_breakdown_columns` carry a `fields` mapping from each rendered column onto that
inventory, and `binding_sources` groups the activation vocabulary into `marker`, `serial`,
`retroactive`, and `lane`. A renderer or Orchestrator MUST NOT keep a second copy of any of these
lists.

Every Orchestrator MUST:

- Emit `wrapper.issue.activated` with a `binding_source` drawn from the declared vocabulary. A
  `retroactive` source (`closure`, `commit`, `single_member_pool`) means the Iteration was already
  running when the evidence appeared, so the issue's Active stint opens at the *Iteration* start and
  the pre-marker output belongs to that issue. **Every other source opens the stint at the binding
  itself**, and an implementation MUST decide that by testing membership of the closed `retroactive`
  group rather than by naming the prospective ones — the complement is open, so a source added later
  is prospective by default, which is what a **Pickup** and a marker both are. A `lane_pickup`
  binding never becomes the single serial Active issue.
- Treat the first authoritative binding in an Iteration as final. A later `wrapper.issue.activated`
  naming a different issue is ignored, not a rebinding.
- Declare each Insight capability once at Run start and stay consistent with it: a capability
  declared unavailable MUST arrive as `null` in every normalized measurement it feeds, and a
  capability declared available MUST NOT use `null` to mean an observed zero or an observed empty
  collection.
- Derive `duration_seconds` and every `*_active_seconds` field from the monotonic clock, so a
  wall-clock adjustment moves the rendered timestamps without moving any duration.
- Report `first_started_at`, `closed_at`, and `activated_at` as RFC3339 UTC with a trailing `Z`;
  localization to the display zone is the renderer's job, not the producer's.

The native ports carry one additional obligation. A Dashboard case whose Run start declares the
native capability manifest MUST carry a `producer_rollups` entry for *every* `wrapper.iteration.end`
in the case, naming both native distributions and the producer facts behind that Event. The shell
and PowerShell Event-schema suites rebuild those payloads through their real Iteration-rollup seams
and compare them against the Event the Python reducer consumes, so a native trace in this fixture is
one both native rollup seams actually produce rather than a hand-written approximation. Shell rollup
arithmetic is integral, so a native case MUST NOT pin a fractional `duration_seconds`.

The probe's depth is the rollup seam, not the whole Run loop: it proves the payload is *producible*,
not that today's native Run loop reaches every input it accepts. The shell and PowerShell Run loops
currently pass a constant zero for PR advances and only an empty or `aborted` terminal outcome, so a
native `pr_advances` or `gone` Iteration is seam-reachable but not yet loop-reachable there.

The boundary with #143 is deliberate and narrow: this seam fixes inventory, ordering, scope,
nullability, placeholder meaning, and localization. It fixes nothing about renderer lifecycle,
process model, threading, redraw scheduling, input handling, or widget toolkit — no Rust or TUI
lifecycle belongs here.

## 13. Conformance (phase 1, MUST)

Each Orchestrator MUST pass the language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md) (`git-loopy/conformance/`):

- **Discriminator** — bodies that do / don't carry both required headings (§3).
- **Selection order** — the total order over eligible issues: `priority` rank, `created_at`
  ascending, issue number as the tie-break that makes it total, plus the timestamp defects an
  undated issue is reported with (§3.2).
- **Close-keyword regex** — a corpus of matching and non-matching commit messages, the pool
  whitelist, issues-only, and first-encounter dedup (§5).
- **Progress / Strike accounting** — scenarios mapping (agent commits, closures, checkpoints,
  PR advances) → progressed? / strike? (§6).
- **Checkpoint message** — the runner-authored subject/body/trailer per Active issue, its
  close-keyword freedom, and its detectability (§7).
- **Exit-code table** — the input → exit-code matrix of §10.
- **Task-type taxonomy** — the closed set of permitted `task-type:` keys, and the refusal an
  unknown key meets: never a warn-and-default, and the refusal names the value and the permitted
  keys (§14).
- **Measured routing precedence** — the measured tier beside both Config scopes and an explicit
  override, and which measured statuses supply a **Routed pair** (§14.1).
- **Event schema** — exact type literals and envelope-first, sorted-payload JSON serialization
  (§12).
- **Dashboard Insights** — normalized Event prefixes and expected renderer-neutral Dashboard and
  drill-in projections, including inventory, Queue order, scopes, placeholders, and localization
  (§12).
- **Skill policy** — base-scope selection, explicit empty policy, environment replacement, Run
  overlays, disable-wins, legacy subtraction, Minimal fallback, the four validation failures, and
  the redacted resolved-policy projection (§17).

The suite is the generalized successor to the cross-runner parity test ADR-0002 deleted. A
conformance fixture change is the canonical way to evolve the contract.

## 14. Per-issue model routing (phase 3, MUST)

Wherever an Orchestrator binds an Iteration to a **single Active issue at pickup** it MUST
resolve the model and reasoning effort **from that issue's labels**, not from the frozen
run-wide default:

- **Read, never infer.** Read the routing key off the issue's `task-type:<key>` labels; the
  `task-type:` prefix is the contract. The Orchestrator MUST NOT infer the type from the title,
  body, or any other heuristic, and MUST ignore non-`task-type:` labels.
- **Resolve to one record.** Resolve the labels to a single **Routing resolution**: the gated
  model, reasoning effort and run-level context tier; the raw `task-type` keys exactly as read;
  every gate warning; a **Routing source**; and the attempt's lifecycle position. The source
  vocabulary is closed to `routed`, `defaulted_no_task_type_label`,
  `defaulted_unknown_task_type_key`, `defaulted_conflicting_task_type_keys`,
  `defaulted_explicit_override` and `escalated` — the set `routing-resolution.json` states as
  `routing_sources`. Source and lifecycle position are **separate axes**: an escalated retry has
  source `escalated`, while a same-pair crash retry keeps the source it resolved with and moves
  only its lifecycle position.
- **Select the pair.** Resolve the labels to a single `(model, effort)` via the shared
  `[routing]` config, honouring the family precedence spine (§11): `[routing]` is a
  **config-file-only** tier that replaces the *single global default* with a per-issue-type
  default — never a flag/env tier — and any explicit `--model` / `--reasoning-effort` (flag or
  env) suppresses routing run-wide (source `defaulted_explicit_override`). The taxonomy is closed
  to `planning`, `review`, `implementation`, `test`, `docs`, `chore`, and `bugfix` — the set
  `routing-resolution.json` states as `task_type_taxonomy`, matched exactly (a recased key is
  unknown): an unknown `task-type:` key is refused, naming the value and the permitted keys.
  Suppressing routing run-wide does **not** excuse the refusal — an unknown key is refused before
  the routing map is consulted. Selection among valid labels is fixed, and a key the table
  **omits** resolves to the global default *as a value* rather than short-circuiting the
  comparison: no label is `defaulted_no_task_type_label`; keys whose resolved values agree use
  that pair — `routed` when the table configures every one of them, `defaulted_unknown_task_type_key`
  when it omits any; ≥2 keys resolving to different pairs fall back to the global default under
  `defaulted_conflicting_task_type_keys`. Only that last case warns, because it is the only one
  where the operator's own labelling is ambiguous. The keys are carried on the record
  **unnormalised**, exactly as the tracker spelled them, so a readback shows what arrived rather
  than what was inferred.
- **Gate and fall back.** Pass the resolved effort through the shared effort gate against the
  model roster and apply the fallback (an effort the model does not accept drops to "let the
  backend pick"; an unknown model passes through). Then gate the run-level **context tier**
  against the resolved model, downgrading a tier the model does not offer to `default`
  ([ADR-0017](adr/0017-context-tier-and-live-context-gauge.md)). Routed, default **and**
  escalated settings are gated identically, and every gate signal is kept **on the record**
  instead of being discarded. A **reasoning-incapable** model — one whose roster entry is the
  empty set — is therefore **unroutable**: an Orchestrator MUST NOT ship a recommended pair
  naming one, because an effort supplied to such a model *hard-rejects session creation* rather
  than downgrading, and a pair the gate can only rescue is a pair that fails wherever the gate is
  not in the path.
- **Pass to the single invocation.** Feed the gated `(model, effort)` to that Iteration's one
  `--model` agent invocation (§4), reusing the same pair for the Lane's integration /
  auto-resolution session, so the Lane runs entirely on the resolved pair. A serial Iteration
  feeds its own single session the same way; a pair is never resolved and then dropped.
- **Resolve once.** Resolve **once** per issue at pickup; the Orchestrator MUST NOT switch model
  or effort mid-session.
- **Escalate a stall once (contract 1.22).** An Orchestrator that routes MUST retry an issue whose
  session ended in **silent no-progress** at a single configured **escalation rung**, resolved at
  that issue's *next* pickup and reported with source `escalated`. The rung is one `(model,
  effort)` pair, a **config-file-only** tier like `[routing]` itself, on by default; an explicit
  `--model` / `--reasoning-effort` (flag or env) suppresses escalation exactly as it suppresses
  routing, because a deliberate pin means what it says. Four properties are load-bearing and MUST
  hold: escalation is triggered by **silent no-progress alone** (a timeout answered with a slower,
  higher-reasoning pair near-guarantees a second timeout; a crash is evidence about the harness;
  an explicit no-more-tasks is the Agent stating there is nothing to do); it is **once** — one
  rung, never a ladder — and **sticky** for the rest of the Run, so the issue never falls back to
  the pair that already stalled on it; it is a **no-op** where the routed pair already equals the
  rung, and MUST then keep the source it routed with rather than claim a change that did not
  happen; and it MUST tick no **Strike**, because the mechanism that aborts a stuck Run must not
  punish trying harder. It is a property of the *issue*, not of the mode that stalled it: an
  Orchestrator with more than one pickup seam MUST feed and read one ledger from all of them.
  A mid-session switch is still forbidden — escalation is a **second pickup**, which is why it is
  stated here and not in the bullet above.
- **Publish what it resolved (contract 1.21).** An Orchestrator that resolves a Routing resolution
  MUST carry it on that Pickup's own `wrapper.pickup.bound` (§12) — the pair, the tier, the raw
  keys, the gate warnings, the source and the lifecycle position — and MUST NOT mint a second
  Event for it. The resolution happens *at* the Pickup, for the Pickup's key, so a second record
  would be one instant described twice with the same cardinality. The fields are
  **optional-when-present**: a port that resolves nothing (see the Python-only note below) says
  nothing and stays conforming, and a consumer MUST treat their absence as "this Runner does not
  route" rather than as a route it failed to report. A `null` `model` or `effort` is *present*
  and means the backend chooses — for `effort`, the accompanying gate warning is what separates
  an operator who asked for nothing from one whose effort was dropped.

Contract 1.12 gave the serial loop a structural pickup seam of its own (§3.3), and contract 1.20
applies what that seam resolves. In between, the scope deliberately did **not** move with it: a
serial Iteration resolved its Routed pair at Pickup, refused an unknown `task-type:` key there
and skipped the candidate (§3.3), but then ran on the run-wide pair — the pair it had just
resolved was discarded. That reservation is now **withdrawn**
([ADR-0037](adr/0037-routing-takes-effect-in-every-mode.md), reversing
[ADR-0027](adr/0027-routing-is-calibrated-by-measurement.md)'s *"Calibration only affects Parallel
mode"*): **the pair a Pickup resolves is the pair its session runs on, at every parallelism a Run
can have.** `git-loopy` with no flags is serial, so the old scope made the *default* invocation
the one invocation where a configured `[routing]` table changed nothing, and an Orchestrator MUST
NOT reintroduce it.

This decision is pinned by three language-neutral fixtures in the
[Conformance suite](../git-loopy/conformance/README.md):
[`model-roster.json`](../git-loopy/conformance/model-roster.json) (the canonical
`model → accepted efforts` sets — its keys are the supported-model set — beside `context_tiers`,
the tier half of the same roster),
[`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json) (labels + config →
the **Routing resolution** record and whether it warns, and the closed `routing_sources`
vocabulary every Runner reads instead of minting its own names), and
[`effort-gate.json`](../git-loopy/conformance/effort-gate.json) (model + requested effort → gated
result and whether it warns). The Python reference adapter drives all three against the production
`resolve_iteration_model` and `gate_reasoning_effort` seams and asserts its in-language roster
constant equals `model-roster.json`.

**Routing is Python-only today**, and that is a *recorded decision* rather than an omission: the
shell and PowerShell Orchestrators implement no part of this section, so neither resolves a Routed
pair and neither has one to apply or to discard. A port that routes nothing is conforming, not
behind, and MUST NOT be held to §14 until it reaches Config parity (§11, phase 3). What such a
port MUST NOT do is arrive carrying the **Parallel-only scope** contract 1.20 withdrew: a native
port that implements routing acquires the rule above with it, and applies the resolved pair to a
serial Iteration exactly as to a Lane. Cross-language routing is deferred, not discharged; this
paragraph is the deferral.

`model-roster.json` MUST carry a **`cli_version`** stamp naming the Copilot CLI its content was
captured against. Reasoning-effort capability is not vendor data: `models.list` discards CAPI's
advertised array and substitutes a table hardcoded in the CLI bundle, so the roster is a function
of **CLI version** ([ADR-0019](adr/0019-roster-derived-from-the-pinned-harness.md)) and an
unstamped roster cannot distinguish a correction from a defect. The stamp is a statement about the
fixture, not about the harness an Orchestrator spawns: where the two differ the divergence is
reportable, and reconciling them is a pinned-harness bump plus a regeneration, made as one change.

The same stamp governs `context_tiers`, the **context tier** capability
([ADR-0017](adr/0017-context-tier-and-live-context-gauge.md)) that shares the roster rather than
forming a parallel table, so the family has one lockstep point with the live catalog instead of
two. A model with **no** `context_tiers` row is unknown and its tier passes through untouched,
exactly as an off-roster model keeps its effort; a row is added only when that model's tiers were
captured for the stated `cli_version`, in the same regeneration ADR-0019 requires.

### 14.1 The measured tier

The family precedence spine (§11) gains one rung between the operator's Config and the built-in
default, so a routing table git-loopy authored can supply a **Routed pair** — but **only where the
operator is silent**:

CLI flag > env var > project Config > global Config > **measured** > built-in default

- **A hand-written entry always wins.** A `[routing]` key in either Config scope beats the measured
  entry for the same **Task type**, with no override flag and no special case, because that is the
  chain that already shipped. Measured entries fill the Task types Config leaves alone, beside the
  ones it does not.
- **An explicit override suppresses it with the rest of routing.** `--model` /
  `--reasoning-effort` (flag or env) already suppresses routing run-wide; the measured tier is
  routing, so it is suppressed too. An operator who names a pair gets that pair, and a
  **Calibration** cannot quietly reintroduce a different one.
- **Deleting the artifact is how an operator opts out.** Routing falls straight back to Config and
  the built-in defaults, with nothing else to undo.

The tier reads **one artifact**, `git-loopy/routing.measured.toml`, in the project scope dir beside
`config.toml` and in the same TOML dialect. It is:

- **Committed**, so a change arrives as a diff a human can read, question and revert (ADR-0028).
- **Current state only.** Git is the ledger: `git log -p` is every past Calibration in order,
  `git blame` names which one set a Task type's model, `git revert` undoes a bad one.
- **Machine-written and never hand-edited.** There is deliberately no measured Config scope for
  `config set` to write to, and no free-text key anywhere in the file for something to write an
  opinion into.

Each Task type's record carries one of four states, and only two of them supply a Routed pair:

| Status | Supplies a pair | What it says |
| --- | --- | --- |
| `measured` | yes | A completed Calibration with a winning pair. The only state that is *evidence*. |
| `incomplete` | no | A search that hit a ceiling or was interrupted. Carries where it stopped and **no pair at all** — a stopped search publishes no winner. |
| `demoted` | no | A pair removed after it stopped making progress on real work. The pair is cleared; which pair failed, and after how many, is kept. |
| `provisional` | yes | A pair **in force that was never measured** (ADR-0030) — what **Demotion** installs when it steps up the price staircase into a rung nobody trialled. Carries no evidence, and MUST NOT be reported as measured. It records the pair it replaced, a closed-vocabulary `reason`, and `replaced_after_no_progress` — the count of that pair's no-progress contributions that met the threshold, required because **Demotion** is the only thing that writes this state and a record of a failure that names no count is not evidence of one. |

An Orchestrator MUST NOT treat an unrecognised status as either of the two that route: a row it
cannot classify supplies nothing and falls through to the built-in default.

**The measured tier is Python-only today**, on the same terms as routing itself: the shell and
PowerShell Orchestrators implement no per-issue routing, and therefore no measured tier. They
declare it unsupported here rather than by implication — a port that reads no artifact is
conforming, not behind — and MUST NOT be held to reading, writing or reporting one. Cross-language
measured routing is deferred, not discharged; this paragraph is the deferral, and it moves when a
port reaches Config parity (§11, phase 3).

The tier is pinned by two more language-neutral fixtures:
[`routing-resolution.json`](../git-loopy/conformance/routing-resolution.json)'s
`precedence_cases` (measured + both Config scopes + an explicit override → resolved pair and the
**tier** that supplied it, including a `provisional` entry that routes while reporting itself
unmeasured), and
[`calibration-search.json`](../git-loopy/conformance/calibration-search.json) (the cheapest-first
price staircase a **Calibration** walks, and where each ceiling stops it).

### 14.2 A `task-type:` label's origin is unobservable

Routing reads a label and never infers a Task type from content **at routing time** — §14's first
rule is unchanged. What changed is who may have written the label: the **Task-type classifier**
(ADR-0029) infers a Task type from an unlabelled issue's own content, once, before routing, and
writes it back to the tracker.

An Orchestrator MUST NOT depend on a label's origin. A human-set and a classifier-written
`task-type:` label are the same string on the same issue, the tracker records no difference, and
routing MUST resolve both identically.

## 15. Native Continuation boundary (Continuation rollout, MUST)

The separately versioned [Continuation contract](continuation-contract.md) governs Producer
publication, Reconciliation, Dispatch evidence, capability declarations, and future Automation.
Wrapper contract 1.4 requires every supported Orchestrator distribution to expose the same public
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

## 17. Closed-world Skill policy (Skill-policy rollout, MUST)

A Run's capability set is a **contract**, not an accident of the operator's machine. Every
Orchestrator MUST resolve exactly which canonical Skill names a Run may load, freeze that answer
before the first Iteration, and record it. See
[ADR-0015](adr/0015-closed-world-skill-policy.md) for the decision. The language-neutral cases are
pinned by [`skill-policy.json`](../git-loopy/conformance/skill-policy.json).

### 17.1 Vocabulary

| Term | Meaning |
| --- | --- |
| **Skill catalog** | The inventory of Skills an operator may inspect and select, with one **winner** per canonical name carrying a `source_kind`. Discovery reads metadata only; catalog membership never makes a Skill available to a Run. |
| **Skill policy** | The git-loopy-owned closed-world set of names one scope persists or supplies, e.g. `enabled_skills`. |
| **Skill baseline** | The initial enabled/disabled selection copied once from the external agent client when the first policy is established. It seeds a policy and is never a live authority. |
| **Effective Skill policy** | The single immutable resolution of every policy source for one Run: enabled names, Required Skills, legacy denials, resolved source kinds, base scope, and fallback reason. |
| **Minimal Skill policy** | Exactly the **Required Skills** and nothing else. The answer whenever no base policy is in effect. |
| **Required Skill** | A name the active Run instructions declare in their `required-skills` metadata. A Run whose effective set omits one is invalid. |

A Skill is identified by **canonical name** — never by absolute path or content digest — so a
project policy stays portable. Canonical names match `[a-z][a-z0-9]*(-[a-z0-9]+)*`. The
`source_kind` vocabulary is exactly `project`, `inherited`, `personal`, `plugin`, `custom`,
`builtin`, and `packaged`. Source precedence when resolving a catalog winner is: the
Orchestrator's **installed catalog** — the pinned external Skill catalog it installs into its own
config home at setup and refreshes at the start of every Run, reported as `packaged` — then the
Copilot CLI's own project/personal/plugin/built-in/custom precedence. The consuming repository's
`<repo>/.copilot/skills` is **not** an Orchestrator Skill source, so no winner an Orchestrator
resolves carries `source_kind` `project`; the value remains in the vocabulary for older Event
streams (ADR-0025). Enabling a plugin-provided Skill MUST NOT activate the rest of its owning
plugin.

### 17.2 Source precedence and scope replacement

The base policy is selected from **one** scope, never merged across scopes:

1. **project** — the project Config's `enabled_skills`, when the key is present.
2. **global** — the global Config's `enabled_skills`, when no project key is present.
3. **minimal** — the Minimal Skill policy when neither key is present.

A present-but-empty list is a real empty policy, **not** inheritance: absence and explicit empty
MUST remain distinguishable all the way from Config parsing to the resolver.

`GIT_LOOPY_ENABLED_SKILLS` is an **exact replacement** of the selected base policy for one Run
(including an explicit empty value). Replacement changes the *names*, not the *selection*:
`base_scope` and `fallback` describe which scope the base came from, so an environment
replacement over a project policy still reports `project`, and an environment replacement with no
configured scope at all still reports `minimal`. §17.6's startup classification — not
`base_scope` — is what answers "was this installation ever configured".

The repeatable `--enable-skill` and `--disable-skill` flags are temporary Run **overlays** applied
after replacement: enable adds, disable subtracts, and **disable wins** over both the base policy
and a same-Run enable.

`deny_skills`, `GIT_LOOPY_DENY_SKILLS`, and `--deny-skill` are **deprecated final guards**. They
may only subtract from the effective set, are applied last, are reported verbatim even when they
name nothing enabled, and MUST NOT be silently dropped or weakened. A legacy denial that would
remove a Required Skill is a validation failure, not a quiet subtraction.

### 17.3 Validation failures (preflight, MUST)

Resolution MUST fail before any work begins, and MUST NOT rewrite persisted policy, when:

| Failure | Condition |
| --- | --- |
| Inventory unavailable | The catalog could not be resolved and the policy was explicitly configured. |
| Missing enabled Skills | An enabled name has no catalog winner. |
| Missing Required Skills | A Required Skill is not in the effective enabled set. |
| Untracked project Skills | An enabled winner whose `source_kind` is `project` is not git-tracked. Unreachable since ADR-0025, which removed the project Skill source; retained so an Orchestrator that still exposes one keeps failing closed. |

Each failure MUST name the offending canonical names, sorted and deduplicated. A Run with **no**
explicit policy still resolves the Minimal Skill policy even when external inventory is
unavailable, because Required Skills come from the installed catalog.

### 17.4 Freeze semantics (MUST)

The Effective Skill policy is resolved **once** at Run preflight, before source collection and
before any agent session exists, and is frozen for the entire Run: every Iteration and every
parallel **Lane** shares that one immutable boundary. Later catalog changes, Copilot CLI state
changes, or Config edits MUST NOT alter a Run in flight. Disabled Skills are omitted from the
session-visible catalog *and* denied again at the permission gate.

### 17.5 `wrapper.skill_policy.resolved` (MUST when the policy surface is implemented)

One Run-scoped Event (`iter: null`) records the frozen boundary, with exactly these payload keys:

| Key | Value |
| --- | --- |
| `base_scope` | `project`, `global`, or `minimal`. |
| `enabled` | Sorted deduplicated canonical names. |
| `fallback` | `minimal`, `migration`, or `null` when a base scope was in effect. |
| `legacy_denied` | Sorted deprecated denial names. |
| `migration_warning` | `true` when the active prompt declared no `required-skills` and inherited the packaged list. |
| `required` | Sorted Required Skill names. |
| `source_kinds` | Enabled name → resolved `source_kind`. |

Every collection projection is sorted, so two Runs with the same boundary produce byte-identical
payloads. The Event is **redacted**: it carries canonical names only. Absolute paths, home
directories, the Run-scoped exposure directory, and Skill content MUST NOT appear. Serialization
follows §12 — envelope keys first, payload keys sorted.

### 17.6 Startup state and the Python-first native transition

Before resolution an Orchestrator MUST classify what the Run found: `unconfigured` (no Config
resolves anywhere), `legacy` (Config exists but the selected scope predates `enabled_skills`), or
`configured` (a base policy is in effect from a scope or an environment replacement). A Run
overlay alone does **not** make a legacy base configured — it is temporary and persists nothing.

Skill policy is a family requirement, but the Python reference Orchestrator implements it first.
Until a port reaches Config parity it MUST **fail closed** rather than silently ignore a
configured policy: detecting `GIT_LOOPY_ENABLED_SKILLS` (including an explicit empty value),
`--enable-skill`, `--disable-skill`, or an `enabled_skills` key in a standard Config location MUST
abort before source collection and before the agent is invoked, naming the unsupported surface.
Legacy deny-only invocations continue to resolve and run unchanged. Silently proceeding with a
wider capability set than the operator configured is the one outcome this section exists to
prevent.

### 17.7 Consulted Skills are a different fact

`skill-consultation.json` measures which Skills an Iteration actually *used*; this section governs
which Skills a Run *may* use. A consulted name is per-Iteration observed behaviour, a policy name
is Run-level availability, and neither may be derived from the other.

## 18. Changing this contract

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
