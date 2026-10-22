# Contract-gap ledger for resuming the shell Language path

Resolves [Inventory the resumed shell path's contract obligations](https://github.com/bradcstevens/git-loopy/issues/980)
on the map [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979).

This ledger is a set of facts. It does not select a Contract support, a session interface,
a migration or a delivery order. Those decisions belong to
[Decide the resumed shell path's supported surface](https://github.com/bradcstevens/git-loopy/issues/982),
[Choose the resumed shell path's session interface](https://github.com/bradcstevens/git-loopy/issues/983)
and later map tickets. It authorizes no manifest, register, label or production change.

## Evidence horizon

| Item | Value |
| --- | --- |
| Audited revision | `origin/main` [`0ee871152855b0e9e4f729b811dae964848c9643`](https://github.com/bradcstevens/git-loopy/tree/0ee871152855b0e9e4f729b811dae964848c9643) ("chore(release): promote Release line to 0.11.3") |
| Contract of the day | Wrapper contract **2.38** ([`docs/wrapper-contract.md:10`][c-head]) |
| Shell Path baseline | [`git-loopy/paths/shell.json`][manifest]: `state: dormant`, Release **0.11.2** (`v0.11.2`, peeled `a0f912b5`), Contract support **2.26**, Event schema **[1]**, carried owed waivers `attempt-lifecycle.json` → #553 and `status-endings.json` → #834 |
| Baseline cross-check | `git show v0.11.2:docs/wrapper-contract.md` reads `Contract version: 2.26`; `git rev-parse v0.11.2^{commit}` = `a0f912b57c222f2525238d37ae268b0adb4c764f` |
| Accepted amendments applied | [ADR-0073][adr73] (as amended by ADR-0083 and ADR-0091), [ADR-0091][adr91], [ADR-0098][adr98] |
| Gate evidence at this revision | See [Production evidence: gate rows](#production-evidence-gate-rows) |
| Method | Read-only. Isolated detached worktree at the audited revision; the shared checkout was not modified. Fixture introduction backfilled from `git log --diff-filter=A` plus the contract header at that commit, as ADR-0091 prescribes. No Agent session, package install or tracker label change. |

**This snapshot is an audit horizon, not a target.** The activation decision must run a fresh
drift check against the contract of its own day: the contract moved twelve minors (2.27–2.38)
between the baseline and this snapshot, and two open pull requests already change shell-relevant
shared authority (see [Pending work](#pending-work-that-already-owns-an-obligation)).

## Classification

Every row below carries exactly one class:

- **CARRIED** — one of the two frozen baseline waivers (ADR-0091). Owed at any support.
- **NEW@v** — not owed at 2.26, but becomes a delivery obligation once Contract support reaches
  version `v`, because the contract text at `v` binds every Orchestrator (or every one that has the
  relevant seam).
- **ROLE** — excluded by contract text or an accepted ADR because of what the shell member *is*
  (no packaging channel, no Lanes, routing deferred to Config parity, Python-scoped clause),
  independent of support.
- **AT-RISK** — an existing shell claim that proves only the baseline slice and would stop proving
  the surface if support advances past a stated version, or if a carried waiver is paid.
- **OWNED** — already owned by pending delivery or cleanup work; link, do not duplicate.
- **CLAIMED** — in scope at 2.26 and claimed through a production seam; no gap found.
- **DECIDE** — the contract or ADRs leave the shell's obligation genuinely ambiguous; a later
  decision must settle it (graduated as fog, not answered here).

## Fixture ledger (33 fixtures)

`Intro` is the contract version at which the fixture first landed (backfilled; the fixtures do not
yet record it). `Rev` is the fixture's current `contract_version` field (its latest revision, not
its introduction). `Shell cases` summarises the per-case `distributions` selectors that apply to
the shell member. Register reasons are quoted from [`fixture-claims.json`][claims].

| Fixture | Intro (commit) | Rev | Register (shell) | Shell cases | Class | Note |
| --- | --- | --- | --- | --- | --- | --- |
| `agent-profiles.json` | 2.28 (`8a41a642`) | 2.33 | `waived_out_of_scope` (ADR-0091) | 0 of 49; every case `["python"]` | NEW@2.28 + DECIDE | §18's opening (2.28) binds "every session an Orchestrator opens"; §18.1–§18.4 are labelled Python. Crossing 2.28 owes a row, but no case is distributed to shell, so a claim has nothing to read until cases are scoped. |
| `attempt-lifecycle.json` | 1.26 (`ccc3c9ff`) | 2.22 | `waived_owed` #553 | all 12 family-wide | CARRIED | See [Carried waivers](#carried-waivers-and-the-strike-fork-migration). |
| `awaiting-merge.json` | 2.17 (`0c7a37b4`) | 2.17 | `claimed` | all | CLAIMED | Adapter `test-orchestrator-conformance.sh:209-263` drives `git_loopy_decide_readiness` and `git_loopy_unbound_pool_outcome`. |
| `calibration-search.json` | 1.8 (`bc528330`; file's own field says 1.1) | 2.5 | `waived_out_of_scope` | — | ROLE | Measured routing/calibration is Python-only by recorded decision ([§14 routing deferral][c-routing]). |
| `checkpoint-messages.json` | 1.0 (`3fe1daba`) | 1.0 | `claimed` | all | CLAIMED | `test-orchestrator-conformance.sh:631-643`. |
| `close-references.json` | 1.0 (`4fee7f45`) | 1.0 | `claimed` | all | CLAIMED | `test-orchestrator-conformance.sh:510-529`. Remains the input to §21's finished/partial distinction. |
| `dashboard-insights.json` | 1.3 (`a11f939d`) | — (Dashboard rev 1.20+) | `claimed` | 3 `["shell","powershell"]` producer-rollup cases | AT-RISK | Proves rollup *seam* producibility only; the contract says the shell Run loop passes constant-zero PR advances and only empty/`aborted` terminal outcomes ([§12 per-Orchestrator obligations][c-perorch]). Endings and per-attempt Routed pairs (2.26/2.27) are not shell-produced. |
| `discriminator.json` | 1.0 (`4fee7f45`) | 2.29 | `claimed` (baseline-scoped) | family cases; 14 `["python"]` `non_execution_task_type` cases excluded | AT-RISK + DECIDE | Adapter reads `baseline_exclusion_reasons` (`test-orchestrator-conformance.sh:59-74`). §3.1 says "Python MUST apply" the 2.29 reason, yet calls the vocabulary "five reasons (four on dormant Paths)". Whether a *resumed* shell above 2.29 owes the fifth reason is not stated. |
| `effort-gate.json` | 1.1 (`53162a2a`) | 1.1 | `waived_out_of_scope` | — | ROLE | Routing/effort gate Python-only pending Config parity. |
| `event-schema.json` | 1.0 (`4fee7f45`) | 2.38 (fixture rev 1.10) | `claimed` | family + 3 four-member + 6 `["python"]` Membership cadence cases | AT-RISK | Claim proves type literals and serialization. Shell's literal table already declares `wrapper.sandbox.resolved` (2.34) and `wrapper.issue.closed`/`close_failed` (2.38) ([`events.sh:16,49-50`][events]) that shell never emits. Literal parity ≠ emission. |
| `exit-codes.json` | 1.0 (`4fee7f45`) | 2.7 | `claimed` | all | AT-RISK (Strike fork) | `stuck-run-is-aborted` (`exit-codes.json:16`) pins the Run-wide Strike's exit; a shell Attempt lifecycle never ends `stuck` (§10). |
| `homebrew-tap.json` | 1.4 (`171acc32`) | — | `waived_out_of_scope` | — | ROLE | Packaging channel. |
| `host-yield.json` | 2.32 (`6988d5af`) | 2.32 | `waived_out_of_scope` (ADR-0091) | — | NEW@2.32 | §4 "Every process that does a Run's work … MUST run under the host's declared Host yield policy"; §12 requires `host_yield` on `wrapper.run.start`; §11 adds `GIT_LOOPY_HOST_YIELD`. Shell has none of the three (no `taskpolicy`/`HOST_YIELD` in `orchestrator.sh`). |
| `integration-gate.json` | 2.36 (`bfd9c89d`) | 2.36 | `waived_out_of_scope` (ADR-0091, "schedules no Parallel Lanes") | top-level `["python"]` | ROLE (at ≥2.36) | Integration gating is a Parallel-mode seam; shell declares every `parallel_capabilities` key `false` and refuses `GIT_LOOPY_MAX_PARALLEL>1` (`orchestrator.sh:1152-1180`). Crossing 2.36 needs the reason restated as role, not ADR-0091 introduction. |
| `issue-lease.json` | 2.8 (`8fa2d753`; file field empty) | 2.8 | `claimed` | all | CLAIMED + OWNED | Claim covers inspection and the pure action decision. Transport is "Python-only pending the shell and PowerShell ports under #390" at this revision; open PR #974 (#971) re-records it as Python-only under ADR-0091. |
| `issue-ordering.json` | 1.10 (`d0d7dddc`) | 1.15 | `claimed` | all | CLAIMED | Selection, Pin, read schedule (`test-orchestrator-conformance.sh:277-466`). |
| `issue-readiness.json` | 2.0 (`263719de`) | 2.0 | `claimed` | all | CLAIMED | |
| `model-roster.json` | 1.1 (`53162a2a`) | 1.18 | `waived_out_of_scope` | — | ROLE | Python-only pending Config parity. |
| `pin-duration.json` | 2.14 (`3b450996`) | 2.14 | `claimed` | all | CLAIMED | |
| `progress-strikes.json` | 1.0 (`4fee7f45`) | 2.22 (schema 3) | `claimed` | 6 family + 2 `["shell","powershell"]` Run-wide cases | AT-RISK (Strike fork) | See [Carried waivers](#carried-waivers-and-the-strike-fork-migration). |
| `release-line.json` | 2.0 (`87da2515`) | 2.13 | `claimed` | 48 three-member, 4 `["python"]` | CLAIMED | Proves the repository Release-line *writer* the shell port carries, not the shell path's own Release identity. |
| `release-trust.json` | 1.4 (`31c41181`) | — | `waived_out_of_scope` | — | ROLE | Distribution tooling. |
| `release-version.json` | 1.2 (`54a18ddb`) | — | `claimed` | all | AT-RISK (on activation) | Runtime identity now reads `baseline.release_version` from the manifest ([`release-version.sh:62`][relver]). An active shell path needs its own Release authority and `shell/vX.Y.Z` namespace (ADR-0073); the claim does not prove that yet. |
| `repository-identity.json` | 2.8 (`d0f62f66`) | 2.8 | `claimed` | all | CLAIMED | Pure resolver; register reason states it does not activate Lease transport or Pickup. |
| `rolling-priority.json` | 2.25 (`10985f3f`) | 2.25 | `waived_out_of_scope` | top-level `["python"]` | ROLE | Inside 2.26 but scoped by the contract to Python Rolling dispatch; shell is serial-only. Correct role exclusion. |
| `routing-resolution.json` | 1.1 (`53162a2a`) | 2.24 | `waived_out_of_scope` | — | ROLE | §14, §14.3, §14.4 record routing as Python-only until Config parity ([§14 routing deferral][c-routing]). |
| `sandbox.json` | 2.35 (`03bfb4f0`) | 2.35 | `waived_owed` #866 (**stale**) | 22 family-wide, 7 `["python"]` | NEW@2.35 + OWNED | ADR-0098 withdrew the exception: the row should be `waived_out_of_scope`. #866 is closed not-planned; #952 / open PR #957 own the cleanup. This is **not** a third carried waiver. Crossing 2.35 owes 22 family cases (policy 2, preflight 9, permission 5, execution 3, disclosure 3). |
| `skill-consultation.json` | 1.1 (`caa011f9`) | 1.1 | `claimed` | all | CLAIMED | Declared-false capability stays null. |
| `skill-policy.json` | 1.4 (`93fb5561`) | 1.4 | `claimed` | all | AT-RISK (on Config parity) | Proves the §17.6 fail-closed transition (`orchestrator.sh:1124`), not closed-world resolution. |
| `status-endings.json` | 2.26 (`3bface26`) | — | `waived_owed` #834 | native session-emission cases | CARRIED | |
| `tui-artifacts.json` | 1.4 (`ab559ad6`) | — | `claimed` | all | AT-RISK (on activation) | Installer selects by Release version; "No published helper exists at or below this baseline" ([paths README][paths-readme]). |
| `unbound-run-notice.json` | 2.10 (`cc8931de`) | — | `waived_out_of_scope` | — | ROLE | Presentation owned by the Python attach client / Rust Dashboard. |
| `windows-channels.json` | 1.4 (`ba57579a`) | — | `waived_out_of_scope` | — | ROLE | Distribution channel. |

Totals at the snapshot: 18 claimed, 3 `waived_owed` (two carried + one stale Sandbox row),
12 `waived_out_of_scope`. Exactly four fixtures were introduced above 2.26: `agent-profiles.json`
(2.28), `host-yield.json` (2.32), `sandbox.json` (2.35) and `integration-gate.json` (2.36).

## Support-threshold ladder (2.27 → 2.38)

What each contract minor after the baseline would add to a resumed shell path. "Python-scoped"
means the contract text names Python as the obliged party.

| Minor | Commit | Change | Shell consequence if support reaches it |
| --- | --- | --- | --- |
| 2.27 | `4c84c5b1` | Per-attempt account and inline Skip (Dashboard projection) | None on the producer ("no wire record or producer behaviour changes", [§12][c-endings]). Its value depends on Endings (CARRIED) and per-attempt Routed pairs (ROLE). |
| 2.28 | `8a41a642` | §18 Agent memory disabled at creation; `session_store_sql` refused with a recorded `tool.permission_denied` (`cross_session_history_disabled`) for Agents and Subagents | **NEW@2.28.** Needs a session seam that can disable memory and record a per-request denial. `copilot --yolo -p` has no permission handler; the interface research marks this a runtime capability, not a flag ([research][research]). |
| 2.29 | `67f698da`, `c665f87c` | `non_execution_task_type` Pool exclusion | DECIDE: §3.1 says "Python MUST apply"; 14 cases `["python"]`. |
| 2.30 | `4f576673` | §18.1–18.2 execution profiles and Skill exposure | Python-scoped ("(Python, contract 2.30)"). DECIDE whether a resumed shell owes profiles. |
| 2.31 | `e9e58f7b` | §18.3 Reviewer read-only access | Python-scoped. Shell opens no Reviewer session. |
| 2.32 | `6988d5af` | Host yield policy (§4, §11, §12 `host_yield`) | **NEW@2.32**, every Orchestrator. Implementation, not interface research. |
| 2.33 | `73f305b5` | §18.4 profile activation; Required Skills come from profiles; `PROMPT.md` `required-skills` retired | Python-scoped, but the **shared `PROMPT.md` already changed under the dormant shell** (see [Artifact compatibility](#artifact-and-helper-compatibility)). |
| 2.34 | `3705ee72` | `wrapper.sandbox.resolved` once per Run + start-output line | **NEW@2.34** ("A Run MUST emit"). Shell already declares the literal, never emits it. |
| 2.35 | `03bfb4f0` | §19 Sandbox enforcement; `sandbox.json` | **NEW@2.35** (ADR-0098). Interface-dependent: ADR-0098 and the research find prompt mode cannot meet it. |
| 2.36 | `bfd9c89d` | Integration gate timeout is inconclusive | ROLE (Parallel-only). |
| 2.37 | `cc9e7ee1` | Mid-work Membership visibility | Python-scoped ("The Python Orchestrator MUST"); `membership_cadence_cases` select Python. |
| 2.38 | `12e03eac` | §21 the Orchestrator alone publishes and closes | **NEW@2.38** for any Orchestrator that closes. Shell closes (§5 backstop), so it would owe publish-first, re-read, verified close with retry, `wrapper.issue.closed`/`close_failed`, and no `wrapper.auto_close`. Today shell closes **before** it pushes (`orchestrator.sh:3860` vs `:3885`). §21 also says the dormant ports are "`waived_out_of_scope` for this fixture" although 2.38 added no fixture; that wording needs reconciling. |

Event schema: every change in 2.27–2.38 is additive within compatibility schema **1** (Event fixture
revision 1.10). No support choice in this range requires an Event-schema support change.

## Carried waivers and the Strike-fork migration

**`attempt-lifecycle.json` → [#553](https://github.com/bradcstevens/git-loopy/issues/553).**
Shell binds a serial Pickup (`git_loopy_pick_serial`, `orchestrator.sh:2501`) but holds no lifecycle:
`git_loopy_strike_tick` (`:1033`) counts consecutive no-progress Iterations, and at the limit the loop
sets `outcome=stuck` and latches a `strike_limit` wind-down (`:3930-3945`). The fixture's 12 cases are
all family-wide. Paying it is production behavior (per-issue Strikes, `skipped` state filtering Pickup
not Pool, `all_skipped` termination, `defeated_by`), not tests around an absent seam.

Every surface the coherent migration touches at this revision:

| Surface | Today (shell) | Citation |
| --- | --- | --- |
| Contract §6 | Already branches on *having an Attempt lifecycle*; no contract edit is required to *permit* the change | [§6][c-s6] |
| Contract §10 / §10.1 | `stuck` exit and the `strike_limit` drain are described only for a Runner without a lifecycle | [§10][c-s10] |
| Contract §11 | `GIT_LOOPY_MAX_NMT_STRIKES` meaning already defined for both accountings (no rename needed) | [§11][c-s11] |
| `progress-strikes.json` | `progress-resets-consecutive-strikes` (`:108`) and `abort-at-threshold-and-remain-terminal` (`:171`) are `["shell","powershell"]`; the note says they are retained to pin the accounting the ports "actually implement" | fixture |
| Shell adapter | `test-orchestrator-conformance.sh:566-570` selects `(.distributions // ["shell"]) \| index("shell")` and asserts `strikes`/`outcome`/`max_strikes`; a lifecycle member must assert only `progress` | adapter |
| `exit-codes.json` | `stuck-run-is-aborted` (`:16`) is family-wide | fixture |
| Events | Shell emits `wrapper.strike` without issue attribution semantics of ADR-0070, and `wrapper.stop.requested{cause:"strike_limit"}` | `orchestrator.sh:3896-3945` |
| PowerShell | Shares both `["shell","powershell"]` cases and must keep them (map Out of scope); the shell-only migration must narrow selectors, not delete cases | fixture |
| ADR-0091 constraint | Changing a case inside a dormant support "needs a retirement decision under ADR-0073, never a quiet edit" | [ADR-0091][adr91] |

**`status-endings.json` → [#834](https://github.com/bradcstevens/git-loopy/issues/834).** Shell emits
no Ending. It observes only the process exit status and its own watchdog's `124`
(`orchestrator.sh:2790-2800, 3006-3030`); prompt mode with `--no-color` text gives it no
content-filter verdict and no structured session error. Endings depend on the session interface
(see the research's "Endings and progress" section).

## Role exclusions (independent of support)

Justified by contract text or accepted ADRs, not by missing implementation:

- **Routing, measured tier, Static and Dynamic routes, routing preparation** (§14–§14.6): "Routing is
  Python-only today … a recorded decision rather than an omission … MUST NOT be held to §14 until it
  reaches Config parity" ([§14][c-routing]). Covers `routing-resolution`, `model-roster`,
  `effort-gate`, `calibration-search`. Note that §3.3 and §6 still say "§14 owes [an Attempt
  lifecycle] to every Orchestrator", which is why the lifecycle is CARRIED, not ROLE.
- **Parallel mode, Rolling dispatch, Lanes, Integration, Execution host**: capability-declared
  `false`; `rolling-priority`, `integration-gate`, the contribution lifecycle and
  `execution_host` (`event-schema.json` `non_producers: ["shell","powershell"]`).
- **Python-labelled clauses**: §18.1–§18.4 (2.30, 2.31, 2.33), Membership cadence (2.37), Calibration
  records (1.16).
- **Packaging/distribution**: `homebrew-tap`, `windows-channels`, `release-trust`.
- **Presentation**: `unbound-run-notice`.
- **Rate card**: declared `false` with `rate_card: null` by contract ([§12 capabilities][c-caps]).

## Claims that would stop proving the resumed surface

1. **`progress-strikes.json` and `exit-codes.json`** — become wrong, not just insufficient, the
   moment #553 lands; they need the migration above.
2. **`discriminator.json`** — proves the four-reason baseline vocabulary only.
3. **`event-schema.json`** — literal parity for three Event types shell never produces; serializer
   claims say nothing about emission of `wrapper.sandbox.resolved`, `wrapper.issue.closed`,
   `wrapper.issue.close_failed` or Endings.
4. **`dashboard-insights.json`** — seam depth only; PR advances and `gone` are seam-reachable but not
   loop-reachable in shell.
5. **`skill-policy.json`** — proves fail-closed detection, not resolution; stops proving if shell
   reaches Config parity.
6. **`release-version.json` and `tui-artifacts.json`** — prove a dormant manifest-backed identity with
   no eligible helper; an active shell Release line needs new proof.
7. **`issue-lease.json`** — proves pure decisions; does not prove the serial Lease transport an
   active Pickup may need (ownership pending #974/#971 and #390).

## Production evidence by area

| Area | What shell does at `0ee87115` | Citation |
| --- | --- | --- |
| Session invocation | `copilot --yolo -p "$prompt" --model … --no-color [--reasoning-effort] [--deny-tool …]` per Iteration; no JSON output, no permission handler, no Sandbox flags | `orchestrator.sh:3006-3030` |
| Observable session outcomes | Exit status preserved; watchdog-terminated turn reported as `124`; NMT sentinel informational; no crash/timeout/content-filter classification | `orchestrator.sh:2790-2905` |
| Strike | Run-wide consecutive; `stuck` exit `1`; `strike_limit` drain | `orchestrator.sh:1033-1057, 3896-3973` |
| Serial Pickup | Binds before the session; Readiness and Awaiting merge admitted; Pin spent at first Pickup | `orchestrator.sh:2501, 1625, 586-621` |
| Config / routing | CLI > env > default; `config.toml` read only to *detect* `enabled_skills` and fail closed; one Run-wide model/effort; no `task-type:` routing | `orchestrator.sh:277-332, 1124, 333-570` |
| Command surface | One entry point (`git-loopy.sh [N] [options]`); no `init`, `plan`, `labels`, `attach`, `doctor` or Stop | `orchestrator.sh:71-101` |
| Closure / review | Closes Pool issues from commit keywords via `gh issue close` **before** Checkpoint and push; emits `wrapper.auto_close`; opens no Reviewer | `orchestrator.sh:3253-3310, 3860, 3883-3885` |
| Sandbox | None; `--yolo`. ADR-0098 records this as accepted dormant behavior | [ADR-0098][adr98] |
| Events | Envelope/serialization per schema 1; Run-start capability manifests; type table includes post-baseline literals | `events.sh:14-104`, `orchestrator.sh:3620-3636` |
| Host yield | Not applied, not disclosed | absent |

### Production evidence: gate rows

Run in the isolated worktree at `0ee87115` with GNU bash 5.3.15 and jq 1.8.1 on macOS (host load
15–20):

| Gate row (AGENTS.md) | Result |
| --- | --- |
| Shell syntax (`bash -n` over entry points, libs and tests) | pass |
| `test-event-conformance.sh` | pass, 12 s ("shell Event-schema conformance: ok") |
| `test-issue-lease-conformance.sh` | pass, 6 s |
| `test-orchestrator-boundary.sh` | pass, 645 s ("shell Orchestrator boundary: ok") |
| `test-orchestrator-conformance.sh` | pass, 39 s |
| `test-release-line-conformance.sh` | pass, 23 s |
| `test-repository-identity-conformance.sh` | pass, 2 s (39 cases) |
| `test-tui-install.sh` | pass, 12 s |

Green proves the dormant baseline claims still hold at this revision. It proves nothing about
NEW, CARRIED or DECIDE rows. Claim falsifiability was not re-mutated in this audit; it rests on the
register's ADR-0049 rule and the adapters cited above.

## Artifact and helper compatibility

- **Shared `PROMPT.md` drifts under the dormant port.** `git-loopy.sh` resolves the packaged prompt
  as `git-loopy/PROMPT.md` beside itself, which is shared and changed in 7 commits since `v0.11.2`
  (39+/78−). It now tells the Agent never to push or close and that "The Run publishes your commits
  and closes the issue", and refers to Agent profiles. Shell still pushes (§8) and closes from
  keywords (§5), so the instruction is compatible in effect, but the dormant baseline does not pin
  the prompt it ships. Whether a resumed path pins, forks or shares `PROMPT.md` is open.
- **Release identity.** Runtime identity reads `paths/shell.json` `baseline.release_version`
  (`release-version.sh:62`, `install.sh:164`, `orchestrator.sh:103`); root `VERSION` stays the
  repository Release-line writer's input. `tag_namespace: shell/vX.Y.Z` is reserved and unused.
- **TUI helper.** A Python Path artifact (ADR-0083). No published helper is eligible at or below
  0.11.2; a compatible helper on `PATH` still attaches by Event-schema negotiation with a drift
  warning ([paths README][paths-readme]).

## Pending work that already owns an obligation

| Work | Owns | State at audit |
| --- | --- | --- |
| [#553](https://github.com/bradcstevens/git-loopy/issues/553) | Carried `attempt-lifecycle.json` | Open, `ready-for-human`, `task-type:implementation`; no `path:` label (owned by #797) |
| [#834](https://github.com/bradcstevens/git-loopy/issues/834) | Carried `status-endings.json` | Open, `ready-for-human`, `path:shell` |
| [#952](https://github.com/bradcstevens/git-loopy/issues/952) / PR [#957](https://github.com/bradcstevens/git-loopy/pull/957) | Stale Sandbox owed row → `waived_out_of_scope`; §19 and READMEs | Open; PR bumps the contract to **2.39** |
| [#797](https://github.com/bradcstevens/git-loopy/issues/797) | `path:shell` on #553 | Open, `ready-for-human` |
| [#971](https://github.com/bradcstevens/git-loopy/issues/971) / PR [#974](https://github.com/bradcstevens/git-loopy/pull/974) | Lease transport recorded Python-only | Open; changes `issue-lease.json` scope prose only |
| [#390](https://github.com/bradcstevens/git-loopy/issues/390) | PRD naming shell/PowerShell Lease transport ports | Open |
| [#982](https://github.com/bradcstevens/git-loopy/issues/982), [#983](https://github.com/bradcstevens/git-loopy/issues/983) | Support and interface decisions | Open map decisions |

Closed and **not** revived: [#866](https://github.com/bradcstevens/git-loopy/issues/866) (Sandbox
delivery, not planned) and [#704](https://github.com/bradcstevens/git-loopy/issues/704)
(cross-language Strike, not planned).

## Inconsistencies found (record, do not fix here)

1. `fixture-claims.json` still lists `sandbox.json` shell as `waived_owed` #866 and §19 still states
   the exception; ADR-0098 withdrew it. Owned by #952/#957.
2. §21 says the dormant ports are "`waived_out_of_scope` for this fixture", but contract 2.38 added
   no fixture.
3. §4 still states each Iteration "MUST feed a single `copilot --yolo -p` invocation", which no
   §19-conforming Runner (including Python) does literally. A shell interface choice should not be
   blocked by this wording.
4. Fixtures do not yet record their introduction version (ADR-0091 "Left to the PRD"); this ledger's
   backfill is the only record. `calibration-search.json`'s own field (1.1) predates its landing
   header (1.8); `issue-lease.json`'s field is empty.
5. `paths/README.md` and `test_path_baselines.py:66-71` still encode the Sandbox exception (owned by
   #952/#957).

## Facts that need external interface research

Already answered by [the completed interface research][research] and reused here: headless
JSON-RPC is a source-supported bash candidate; prompt mode and ACP do not establish the §19
boundary; prompt JSON drops permission/Sandbox decisions and the content-filter verdict.

Still needing a runtime fact (not a decision) before the relevant obligation can be scheduled:

- Whether the selected interface can disable harness memory at creation and surface each
  `session_store_sql` request for a recorded denial for Agents **and** Subagents (§18, NEW@2.28).
- Whether it reports a per-tool `sandboxed` attestation and bypass requests to a bash client
  (§19, NEW@2.35) — source-supported in JSON-RPC, not runtime-proved.
- Whether it yields a distinguishable crash, timeout and content-filter verdict for Endings (#834).
- Whether a profile/`custom_agents` registration is reachable from bash if a resumed path takes §18.1.

Host yield (`taskpolicy -c utility` on macOS) and §21 publish-before-close are implementation work
within the port, not interface research.

## Not decided here

The supported Contract support; whether DECIDE rows (2.28 case scoping, 2.29 fifth reason, 2.30/2.33
profiles) bind a resumed shell; the session interface; the Strike-fork retirement shape; whether
`PROMPT.md` is pinned per path; Release namespace activation; delivery order. Activation must
repeat this audit against the contract of its own day.

[c-head]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L10
[c-s6]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L751-L790
[c-s10]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L816-L887
[c-s11]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L888-L910
[c-caps]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L1135-L1160
[c-endings]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L1631-L1680
[c-perorch]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L1739-L1786
[c-routing]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/wrapper-contract.md?plain=1#L2044-L2051
[manifest]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/git-loopy/paths/shell.json
[paths-readme]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/git-loopy/paths/README.md
[claims]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/git-loopy/conformance/fixture-claims.json
[events]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/git-loopy/shell/lib/events.sh#L14-L104
[relver]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/git-loopy/shell/lib/release-version.sh#L62
[adr73]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/adr/0073-language-paths-release-and-gate-independently.md
[adr91]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/adr/0091-a-dormant-path-stands-at-the-last-stable-family-release-carrying-its-owed-debt-frozen.md
[adr98]: https://github.com/bradcstevens/git-loopy/blob/0ee871152855b0e9e4f729b811dae964848c9643/docs/adr/0098-a-dormant-path-owes-the-sandbox-rules-only-once-it-resumes.md
[research]: https://github.com/bradcstevens/git-loopy/blob/58de5f9b1224706e4e4f378cb74cea5d76e54427/docs/research/shell-resumption-session-interface.md
