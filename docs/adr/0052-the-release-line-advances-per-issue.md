# The Release line advances per issue, and the milestone governs the stable cut

**Status:** accepted

Amends [ADR-0016](0016-single-distribution-release-version.md), which made a Release version a
thing a human writes into `VERSION` and selects with a tag. It keeps every identity rule that ADR
established and replaces only the question of *who moves the number and when*. It extends
[ADR-0029](0029-agents-infer-the-task-type.md)'s inference-plus-closed-taxonomy trade to a second
label, and gives [#466](https://github.com/bradcstevens/git-loopy/issues/466)'s fixture-claim
mechanism its first customer.

## What was actually broken

Nothing about publication. `source_release.py`, `tui_release.py`, `release_trust.py` and
`windows_channels.py` prove more about an artifact than most projects prove about a build, and
`docs/releases/README.md` describes a coherent promise: a `vX.Y.Z` milestone is "the one record
of what a Release is answerable for."

The join was empty. **Nothing ever claimed a Release.** At the time of this decision the tracker
carried 57 open issues and **9** of them a milestone; [#445](https://github.com/bradcstevens/git-loopy/issues/445)–[#465](https://github.com/bradcstevens/git-loopy/issues/465),
an entire spec's worth of Execution-host tickets, carried none. `wayfinder`, `to-spec`,
`to-tickets`, `next`, `triage` and `implement` contain no occurrence of "milestone", "version" or
"release" — the planning layer had no vocabulary for the thing the publication layer was waiting
for. A convention documented in two places and performed by nothing is not a convention.

## The decisions

**A closed `semver:` label carries the bump, and agents infer it.** Four keys —
`major`, `minor`, `patch`, `none` — written back to the tracker at **Pickup**, exactly the trade
ADR-0029 accepted for `task-type:`: agents infer, and the taxonomy closes to pay for it. The
fourth key is not decoration. ADR-0029's bargain is that a closed taxonomy can express every
outcome it admits; absence must therefore mean *unclassified*, a detectable fault, rather than
doubling as a silent "no bump". A docs issue and a labeller that crashed must not look alike.

`task-type:` was rejected as the carrier despite being free. It exists for routing — every one of
its label descriptions says so — `implementation` spans both MINOR and PATCH, and it has no key
that can express MAJOR at all. `docs/releases/README.md` already makes this argument against
overloading a workflow label with release semantics: "`ready-for-agent` says an agent *could*
start, not that a Release is *waiting*, and the two answer different questions." Same trap.

**The target ratchets; the counter counts.** A closed issue does not fold its bump into the
previous version. The `semver:` labels across the closed set determine a **Release target** by
running maximum, and each closed issue increments a prerelease counter against it:
`v0.10.0-dev.1`, `-dev.2`, `-dev.3`. A larger bump arriving mid-line raises the target and leaves
the counter alone — `0.9.1-dev.2` followed by a `semver:minor` becomes `0.10.0-dev.3`, not
`0.10.0-dev.1`.

This is the whole reason the scheme is a ratchet and not a fold. Folding per issue is
**order-dependent**: one minor and two patches integrating as `patch, patch, minor` land on
`0.10.0`, and as `minor, patch, patch` on `0.10.2` — the same three issues, the same tree on
`main`, two different numbers chosen by which Lane finished first. Lane completion order is not
deterministic. A running maximum and a count are both order-independent, so the version becomes a
fact about the work rather than about the schedule. A project that pins its behaviour in
Conformance fixtures and calls the family gate an oracle cannot have a version number decided by
scheduling luck.

A `semver:none` issue advances nothing. Wayfinder decisions, docs, tests and chores change
nothing an operator installs, and a version that moves without the tool differing is a lie told
in the one field operators use to tell builds apart.

**The bump runs post-Integration, and every member is obliged.** It executes after the merge,
inside the section ADR-0009 already serializes — `_integration_lock` (`loop.py:2305`), the lock
that exists so "the shared main worktree never sees two". It is a Wrapper-contract obligation:
the shell and PowerShell Orchestrators implement it too, and `release-version.sh` and
`GitLoopy.Release.psm1` grow a writer beside the reader they already have.

Bumping *inside a Lane's contribution* was ruled out on a mechanical fact, not a preference.
Every Lane would touch `VERSION`, `pyproject.toml`, `__init__.py` and `Cargo.toml`, so two Lanes
in flight would conflict on every Integration, on hunks whose conflict carries no meaning, and
rolling dispatch would spend its K≤3 auto-resolution budget resolving version numbers.

**Exactly one fixture may name the live version.** `release-version.json` is the authority check
and keeps `expected_release_version`. `event-schema.json` and `dashboard-insights.json` stop
baking the live number into their sample payloads — 18 sites between them today.

Those pins prove nothing the gate is for. The event-schema fixture exists to prove four members
agree on the **wire form**, and whether the number reads `0.9.0` or `0.10.0-dev.3` is not
something they can disagree about: they all read one file. What the pins *do* buy, once the
version advances per issue, is 18 rewrites per closed issue performed identically by three
implementations — a member rewriting the oracle that judges it, on a cadence. Version-copy
agreement is already proved where it belongs, by `release-version.json`'s `metadata_drift_cases`,
`source_tree_drift_cases` and `artifact_drift_cases`, which use a synthetic `1.2.3-rc.1` and
therefore never churn. **This is the blocking prerequisite**: nothing else here can ship while the
fixtures still churn.

**The milestone governs the stable line, and only the stable line.** Prereleases are label-driven
and unattended; no milestone participates. A `vX.Y.Z` milestone closing is the **promotion**
trigger that cuts `0.10.0-dev.N` to stable `0.10.0`.

This is what rescues the milestone rather than retiring it. Its old job — deciding which Release
an issue lands in — is gone, taken by the ratchet. Its new job is the one fuzzy edge left in the
design: "a full Release for MAJOR and, whenever it makes sense, MINOR as well." *Whenever it makes
sense* is not a rule. *When the milestone you promised is met* is, it is a tracker event that
already exists, and `docs/releases/README.md` already describes that event — "a milestone is
closed when its content is on `main`". A concept nothing depends on is a concept nobody
maintains, which is the honest explanation for 48 unmilestoned issues.

**The helper resolves the newest Release at or below the declared version.** `tui-install.sh`
substitutes the root `VERSION` into `release_download_url_template` (lines 162–172) and then
refuses any helper not reporting exactly that version (lines 343–346). ADR-0016 required this:
"Artifacts selected as one packaged distribution require exact Release-version equality."

Under a per-issue line that rule is a trap. Every value reaching `main` would need seven
published cross-compiled artifacts or the helper install breaks outright — against the backlog at
the time of this decision, 399 notarization runs to clear it, with no cheap or source-only
prerelease available at any point. So exact equality is **relaxed to newest-at-or-below**: a
`dev.N` line falls back to the last helper actually built, and TUI artifacts are produced at `rc`
and stable. This supersedes that one sentence of ADR-0016 and nothing else in it; the drift and
identity rules stand unchanged.

## Consequences taken deliberately

**An agent can publish a breaking Release unattended.** `semver:major` is agent-writable, a MAJOR
is exempt from milestone promotion, and a stable Release opens a pull request into
`microsoft/winget-pkgs` and ships notarized binaries to Homebrew and Scoop. The chain is: an
agent's inferred label publishes a breaking change to the world with nobody awake.

This was put three times — with the blast radius named each time, and against the precedent of
`config.py`'s routing table, which keeps a model out of write-capable lanes over measured
task-cheating on the reasoning that a safe agent "writes no files and has no metric to game"
(here it has both). Human-only `major`, second-agent confirmation, and holding package channels
back for a human were all offered and all declined. It is recorded as chosen, not overlooked, so
that a future reader finds a decision rather than an oversight.

**Release notes stop being edited prose.** `source_release.py:159` demands "committed UTF-8
**edited** release notes", and that requirement is today the only thing standing between a tag and
publication — the de facto human gate. Agents now author `dev.N` notes and a stable draft; a human
may override before promotion, and absence never blocks. Unattended publication requires this, and
the cost is real: the `v0.9.0` notes open with a human explaining a problem in a human voice, and
a fold of closed-issue titles will not be that. The `dev.N` fragments accumulate as raw material
for the stable note so the essay starts from something rather than blank.

**Releasing joins the Wrapper contract.** Waiving it for the shell and PowerShell Orchestrators
through #466's named-reason mechanism was offered and declined; a Run under any member of the
family cuts releases, and none is permitted to be the member that cannot.

## Still open

**An issue closed outside a Run.** The bump is defined post-Integration, and the Runner is what
integrates. Nothing here says what happens when a human closes a milestone-bearing issue by hand,
as [#437](https://github.com/bradcstevens/git-loopy/issues/437) was. Whether that advances the
line, or whether the line only ever moves under a Run, is undecided and deliberately not decided
here.
