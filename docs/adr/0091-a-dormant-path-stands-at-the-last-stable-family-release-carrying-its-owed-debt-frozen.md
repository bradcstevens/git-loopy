# A dormant path stands at the last stable family Release, carrying its owed debt frozen

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

**Amended by [ADR-0106](0106-a-resumed-shell-path-is-delivered-outside-runs-and-activates-in-one-approved-change.md):**
a dormant path's carried debt is scheduled when the Loop engineer approves its resumption graph,
and is delivered while the path stays dormant, outside every Run. The path resumes at its Path
activation.

Decided by [Decide what the shell and PowerShell Path baselines declare and what becomes of
their open work](https://github.com/bradcstevens/git-loopy/issues/779). Builds on
[ADR-0073](0073-language-paths-release-and-gate-independently.md),
[ADR-0079](0079-a-human-opens-a-major-line-and-the-1-0-0-cut-waits-for-the-python-path.md),
[ADR-0080](0080-the-tracker-is-the-board-and-its-states-are-derived.md),
[ADR-0083](0083-the-tui-helper-is-a-python-path-artifact-and-the-1-0-0-cut-waits-for-its-parity-baseline.md)
and
[ADR-0089](0089-1-0-0-promises-an-explicit-stable-surface-and-removes-what-0-x-deprecated.md).
Amends ADR-0073's Conformance rule and ADR-0080's Pool exclusions.

ADR-0073 starts every **Language path** from a **Path baseline** and holds a **Dormant path**
there until it resumes. For the shell and PowerShell paths it left three things open. It did
not name the Release they declare once closures start minting `1.0.0-alpha.N` into `VERSION`
(ADR-0079). It did not name their contract support. And it admits only claims and role
waivers inside a path's support, while both paths already owe fixtures. This decision fixes
the baseline at the last stable Release they shipped in. It carries their owed debt into
dormancy, frozen, and keeps their issues out of every Run.

## The baseline is the last stable Release they shipped in

The shell and PowerShell Path baselines declare:

- **The last stable family Release before the split.** It is the last Release either path
  shipped in. A prerelease in `VERSION`, such as `1.0.0-alpha.N`, builds toward a target a
  dormant path never reaches, so it never counts. The first `v1.0.0` closure ends 0.x with no
  maintenance line (ADR-0079), which fixes the value by then at the latest, so the split need
  not land before that ticket. This is the version of their own that ADR-0083 said the
  dormant installers need: both Orchestrators read the root `VERSION` today.
- **Contract support: the contract that Release shipped.** The Release and its support then
  describe the same artifact. The contract of the day at the split can be ahead of every
  Release.
- **Event-schema support: compatibility schema 1.** That is the integer every envelope
  carries and the helper negotiates (ADR-0083). The Event fixture's revision is not declared
  beside it. A revision pins additive extensions independently of the schema, and Contract
  support already places the fixture's in-scope cases.

Today the values are **Release 0.11.2, Contract support 2.26 and Event schema 1**. The
decision was first taken as 0.11.1 and 2.23. While it was being recorded, 0.11.2 was promoted
by exception (a0f912b5), carrying contract 2.26 and the same shell and PowerShell code. The
decision records the rule rather than a number for that reason.

## A baseline carries the owed waivers it already had

ADR-0073 admitted only claims and role waivers inside a path's support. A Path baseline now
carries the **Owed waivers** it already had into dormancy, frozen:

- a dormant path gains no new owed waiver;
- each carried waiver keeps its tracking issue, which stays open;
- the debt is scheduled only when the path resumes.

At 2.26, both paths carry two:

- `attempt-lifecycle.json`, introduced at contract 1.26 and tracked by #553 and #554.
  Neither Orchestrator implements an Attempt lifecycle at all. Each binds a serial Pickup and
  keeps the Run-wide consecutive Strike that `progress-strikes.json` forks for it (#553,
  #704), so the waivers' reason understates the gap.
- `status-endings.json`, introduced at 2.26 and tracked by #732. Neither Orchestrator emits
  an Ending.

Paying either debt means an implementation in both ports, which is 1.0.0-era work in a
dormant path. A **Permanent waiver** would be wrong too. The contract gives both obligations
to every Orchestrator, so dormancy would be the only reason to waive them, and that is
effort, not role.

## Introduction is recorded, and later cases are scoped

ADR-0073 says fixtures introduced after a path's support need no row. Nothing records
introduction today: a fixture's `contract_version` is its latest revision, and eight
fixtures carry none.

- Each fixture records the contract version it was introduced at, written once and never
  edited. The audit backfills it from git history: for example `attempt-lifecycle.json` at
  1.26, `awaiting-merge.json` at 2.17, `rolling-priority.json` at 2.25 and
  `status-endings.json` at 2.26.
- A member owes a claim or a waiver only for fixtures introduced at or below its path's
  Contract support. Every current fixture is inside 2.26, so no row drops today. A fixture
  introduced after the baseline needs no shell or PowerShell row.
- A case that a later contract adds to an older fixture is kept off a member that cannot pass
  it by the existing per-case `distributions` selector, applied by the case's author. No
  per-case version field is added.
- Changing a case already inside a dormant path's support breaks that path's claim. That
  needs a retirement decision under ADR-0073, never a quiet edit. The same holds for contract
  rows and cases that only a dormant path still exercises: the Run-wide Strike with
  `strike_limit` and `wrapper.stop.lifted`, and the deny guards (ADR-0089). Python may stop
  producing them. They stay in the contract while a dormant support includes them.

## A dormant path's issue is a Pool exclusion

Once Path manifests are active, an executable issue owned by a dormant path is a **Pool
exclusion**. Its reason is `dormant_path`. Like every Pool exclusion it is named to the
operator, and the Board shows it as Blocked (ADR-0080). Only resuming the path, a manifest
change, lifts it. As in ADR-0088, `git-loopy labels` neither reports nor repairs it. There
are two right fixes, removing `ready-for-agent` or resuming the path, and `--apply` cannot
choose between them.

Discipline alone had already slipped. #732 was `ready-for-agent` and unblocked, so any Run
could have bound it. That work ships in no Release while the path is dormant. A
shared-authority fix that keeps a dormant gate green stays with the Python-owned issue that
caused it (ADR-0073), so the exclusion never blocks a Python change.

Until the manifests activate, a missing `path:` label still means Python (ADR-0080), so the
interim guard is tracker hygiene, applied with this decision.

## The open dormant work

- **#732** stays open as the tracking issue of the carried `status-endings` waivers. It
  leaves `ready-for-agent` now, and it is detached from Spec #729 so that Spec can close
  without it. The Spec marks its Stories 18 and 19 as carried dormant debt. The migration
  splits #732 into a `path:shell` issue and a `path:powershell` issue, one per carried row.
  Its code is e585f8d on `feat/status-insight`. When #729's Story 25 deletes that branch, the
  commit is kept under an `archive/<date>/feat/status-insight` tag, as earlier abandoned work
  was.
- **#553 and #554** stay open as the `attempt-lifecycle` tracking issues. The migration labels
  them `path:shell` and `path:powershell`, never the `path:python` that `labels --apply`
  would stamp on an unlabelled issue.
- **#704, #532 and #533** are closed as not planned. One is resumption design and two are
  backlog owed to nothing. A resumption computes its backlog against the contract of the day
  ([#648](https://github.com/bradcstevens/git-loopy/issues/648)).
- **#429** keeps only its Python part. Its criterion that the shell and PowerShell
  Orchestrators emit the Membership record is dropped.

## Amendments

- ADR-0073 admits only claims and role waivers inside a path's declared support. A Path
  baseline may now carry the owed waivers it already had into dormancy, frozen. Each fixture
  records its introduction version, and a later case in an older fixture is scoped with
  `distributions`.
- ADR-0080 makes a missing or duplicate `path:` label a Pool exclusion. An issue owned by a
  dormant path is now one too.

## Left to the PRD

The Language paths and release PRD settles:

- the manifest field that marks a path dormant, and where each dormant path's version lives;
- the audit that computes the baseline values when the manifests activate;
- the introduction field's name, its backfill, and the register gate that reads it;
- the `dormant_path` reason's wording and rank in Wrapper contract §3.1, its contract version
  and its `discriminator.json` cases;
- the carried waivers' corrected reasons, and #732's split by path.

## Considered options

- **Pin 0.11.1 and contract 2.23, the first answer.** Rejected once 0.11.2 shipped. The
  dormant paths would declare a Release older than the last one they shipped in, and a
  support below the contract that Release carries.
- **Land the split before the first `v1.0.0` ticket and take `VERSION` then.** Rejected: it
  puts the split on the critical path of all 1.0.0 work, and `VERSION` may then read a
  prerelease.
- **The contract of the day when the split lands.** Rejected: it can be ahead of every Release,
  and it ties the declaration to an arbitrary date.
- **The highest support with no owed waiver, 1.25.** Rejected: Readiness, the Release line,
  the Lease, Awaiting merge and Pin duration would fall outside dormant support, and a Python
  change to them would stop reaching the dormant gates.
- **Pay the owed debt before the split.** Rejected: both debts are implementations, not
  tests, and the map rules 1.0.0 work in the dormant paths out of scope.
- **Reclassify the debt as Permanent waivers.** Rejected: dormancy is effort, not role.
- **Declare the Event fixture revision beside schema 1.** Rejected: a second number that must
  always agree with Contract support restates it.
- **A per-case `since` version, or the frozen register rows as the record.** Rejected: the
  first adds a field to every case where `distributions` already serves. The second leaves
  introduction implicit for a path that later resumes.
- **Tracker discipline for dormant issues, or letting a Run advance them.** Rejected:
  discipline already slipped with #732, and a Run would do work that no dormant Release ships.
- **Close #732 as not planned.** Rejected once 2.26 is inside the baseline: its waivers are
  carried debt, and a carried waiver keeps its tracking issue.
