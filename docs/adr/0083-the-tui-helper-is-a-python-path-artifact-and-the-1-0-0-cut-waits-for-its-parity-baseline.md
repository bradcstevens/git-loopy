# The TUI helper is a Python-path artifact, and the 1.0.0 cut waits for its parity baseline

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

**Amended by [ADR-0105](0105-a-resumed-shell-path-cuts-its-own-releases-and-records-the-helper-release-it-consumes.md):**
a resumed shell path installs the exact helper Release its own Release records as its Consumed
Release, rather than the newest helper at or below its Release version, and measures drift
against it.

Decided by [Decide whether the Rust Dashboard helper stays inside the Python 1.0.0
boundary](https://github.com/bradcstevens/git-loopy/issues/665). Builds on
[ADR-0073](0073-language-paths-release-and-gate-independently.md) (every published
artifact has exactly one owning path) and amends it and
[ADR-0081](0081-the-web-board-is-a-foreground-loopback-client-of-the-clone.md).

ADR-0073 gives every published artifact one owning **Language path**; other paths
consume a compatible release through a contract or schema. The **TUI helper**
(`git-loopy-tui`) was never assigned one. ADR-0013 made it the family's one shared
renderer, ADR-0073 listed Rust as a path, and ADR-0081 assumed the helper sat in that
dormant Rust path. In practice it is already part of the Python distribution. The helper
release refuses a tag unless `VERSION`, its `Cargo.toml` and the tag agree. Python
refuses a clone-local or machine-local helper on Release drift (Wrapper contract §15).
`git-loopy update` and `uninstall` manage it (ADR-0054). Its release tooling is Python
code. It is also the repository's only Rust code: the Rust Orchestrator is still only
planned.

The TUI helper is a **Path artifact** of the Python path, implemented in Rust. Python's
**Path manifest** lists it, its feedback loop belongs to Python's **Path gate**, and it
keeps the unqualified `vX.Y.Z` Release identity. The shell and PowerShell paths consume
it through Event-schema negotiation, as they already do.

## Status parity, not new screens

In 1.0.0 the helper keeps its screens and renders every Queue **Status** and Event the
Python Run adds, starting with ADR-0072's `reviewing` and `review-handoff` and its
`wrapper.review.started`, `wrapper.review.verdict` and `wrapper.review.handoff` Events.
Each lands in Python's Dashboard projection and in the helper in the same change, pinned
by the shared Dashboard fixture both already claim. A helper that misses one is not
neutral. It decodes an unknown Event as a no-op, so a row under review would stay
`active` or `integrating`.

**Board columns**, the card drawer and the Subagent tree stay web-only (ADR-0080,
ADR-0081). The helper attaches to one Run and reads only its trace, while the board needs
the tracker and every live Run.

## No Rust path until a Rust Orchestrator exists

No Rust path is declared. 1.0.0 has Python, shell and PowerShell manifests and no
`path:rust` label, and issues that change `git-loopy/tui/` are `path:python`. If a Rust
Orchestrator is started, it opens the Rust path with its own manifest and consumes the
Dashboard library as a released dependency, never as shared source. The mechanism is
decided then.

When the manifests land, the helper's Conformance member is renamed from `rust` to
`tui`, so it cannot collide with that Orchestrator's member. As a member of the Python
path it claims or role-waives every fixture in Python's **Contract support**, as it
already does for every current fixture.

## Operators get the helper from a prerelease-channel baseline

No helper is published today. The repository declares `source-only`, and no surviving
Release carries helper assets. The artifact-bearing `v0.11.0-dev.7` and `-dev.8` were
removed with the `-dev.N` line (ADR-0066).

The 1.0.0 cut waits for a **Helper baseline**
([#592](https://github.com/bradcstevens/git-loopy/issues/592)): the full seven-target
set, published on a human-tagged artifact-bearing 1.0.0 prerelease under the existing
prerelease trust policy. That policy needs checksums and trust receipts, not signatures.
`v1.0.0` itself stays source-only. `git-loopy update` and the installers already resolve
the newest helper at or below the installed Release (#492, #591), so a stable Runner
installs that helper.

Homebrew, winget and Scoop update only from an artifact-bearing stable Release. They stay
dormant until the stable signing, notarization, attestation and channel credentials
exist, which keeps those credentials off the cut's critical path. The stable trust gates
are neither relaxed nor bypassed.

## The rehearsal proves parity

An additive Status leaves the Event-schema integer at 1. A baseline published before
that Status landed therefore still passes the compatibility probe, but shows the row
stale.

The release rehearsal refuses a Python stable candidate at 1.0.0 or above unless the
newest verified helper at or below it was built against the candidate's revision of the
shared Dashboard fixture. This follows ADR-0079's reasoning: a by-exception Promotion
never meets the milestone, so only the rehearsal covers both routes. The mechanism, for
example a fixture revision in the helper's `--schema-version` answer, belongs to the
Language paths and release PRD. In practice, a Dashboard change late in the prerelease
line means a human tags another artifact-bearing prerelease.

## Compatibility stays negotiated

- **Event schema.** The helper's decode range advances with the version Python emits, in
  the same change. It keeps decoding schema 1 while the dormant shell and PowerShell
  paths emit it. Raising its minimum is a retirement decision under ADR-0073, not a
  manifest edit.
- **Distribution identity.** Wrapper contract §15 and ADR-0054 stay as they are. A
  clone-local or machine-local helper remains a component of the Python distribution. It
  fails closed on Release drift unless it matches the installer-recorded Release at or
  below the Runner. A `PATH` helper is a separate installation, so its drift only earns a
  warning.

## What a terminal operator is promised

- **The Dashboard.** On a terminal, a Python 1.0.0 Run renders the **Dashboard** through
  the helper that `git-loopy update` installs on all seven declared targets, with no Rust
  toolchain.
- **The line printer.** It takes over when no compatible helper is present and after a
  **Dashboard fault**. It is also the output of every non-terminal Run (ADR-0058,
  ADR-0068).
- **The web board.** `--web` adds the browser board on macOS and Linux. On native Windows
  the helper is the only rich view, because ADR-0081 refuses the web board there.
- **Shell and PowerShell.** Both paths are dormant. They may attach a compatible helper,
  and no new terminal feature is promised for them.

`docs/runners.md` and `docs/releases/README.md` change to say this when it ships, not
before.

## Considered options

- **A Rust-path artifact that Python consumes.** The Rust path is dormant for 1.0.0, so
  the active path's only terminal Dashboard would be frozen and would misreport the
  Review stage. Python would also have to drop its §15 check and its `update` and
  `uninstall` management, and its release tooling would serve another path.
- **Splitting the crate, with the library in Rust and the binary in Python.** Every
  semantic and presentational decision lives in the library, so parity would change a
  dormant artifact.
- **Freezing the helper, or dropping it from the 1.0.0 promise.** A frozen helper
  contradicts the Run, and dropping it leaves native Windows with only the line printer.
- **A terminal Board screen.** It would need a new board feed with tracker reads and
  every live Run, duplicating the web board.
- **An artifact-bearing `v1.0.0`.** It would put paid signing credentials on the cut's
  critical path, and the `v0.11.0` helper pipeline already failed at notarization.
- **No downloadable helper.** The TUI would reach only operators who build it with
  `cargo`.
- **A schema-compatible baseline, or tracker discipline alone.** The first lets a stale
  helper ship with the stable. The second misses a by-exception Promotion (ADR-0079).

## Consequences

- ADR-0073 loses Rust from its list of paths until a Rust Orchestrator starts. ADR-0081
  still leaves the helper unchanged for `--web`, but now because the web board needs
  nothing from it, not because a Rust path is dormant.
- The Language paths and release PRD owns the remaining mechanics.
  `release-trust.json`'s single `distribution_mode` must let a prerelease be
  artifact-bearing beside a source-only stable. The dormant installers read the root
  `VERSION`, so they need a declared version of their own after the split.
- #592 joins 1.0.0 scope. Its `v0.11.0` Release-target label is stale and becomes
  `v1.0.0` when the 1.0.0 tickets are written.
- The glossary's **TUI helper** and **Orchestrator** entries keep describing today's
  shared helper until the manifests ship.
