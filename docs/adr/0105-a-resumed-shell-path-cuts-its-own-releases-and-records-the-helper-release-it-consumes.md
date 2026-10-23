# A resumed shell path cuts its own Releases and records the helper Release it consumes

**Status:** accepted design. Planning only: no manifest, register, workflow, contract or label
changes here. [Decide the shell resumption delivery and activation gates](https://github.com/bradcstevens/git-loopy/issues/985)
sequences the delivery.

Decided by [Decide which artifacts the resumed shell path pins or shares](https://github.com/bradcstevens/git-loopy/issues/988)
on [Wayfinder: resume the shell Language path](https://github.com/bradcstevens/git-loopy/issues/979).
Builds on [ADR-0073](0073-language-paths-release-and-gate-independently.md),
[ADR-0083](0083-the-tui-helper-is-a-python-path-artifact-and-the-1-0-0-cut-waits-for-its-parity-baseline.md)
and [ADR-0102](0102-a-resumed-shell-path-declares-contract-support-2-38-serial-and-without-config-parity.md).
ADR-0102's decisions 4 (`artifacts` stays `[]`, and a compatible helper attaches by Event-schema
negotiation) and 8 (the shell forks its own prompt) are inputs, not reopened. This decision amends
ADR-0102's decision 10 and, for a resumed shell, ADR-0083's helper resolution.

ADR-0073 gives each **Language path** its own Release line and reserves `shell/vX.Y.Z` for the
shell. The dormant shell still reads its identity from `baseline.release_version` in its
**Path manifest**, and ADR-0102 keeps that baseline as history. Nothing said where an active
shell's version lives, who advances it, or how it reads once shell and Python numbers diverge by
design. Two hazards made the question urgent. Python's Pickup writes a `vX.Y.Z` label on every
issue it picks up, so admissible `path:shell` work would advance Python's `VERSION`. And the shell
installer takes the newest helper at or below the shell's own Release, a comparison across two
namespaces: a shell `0.12.0` would never see a `1.0.0` prerelease helper.

## Decision

1. **A shell-owned authority file.** At activation the manifest's `release_authority` names
   `git-loopy/shell/release.json`, field `release_version`. Every reader of shell identity follows
   `release_authority` rather than a hardcoded field, and fails closed when it is missing or
   malformed. `baseline` is never edited, and root `VERSION` stays Python's. Resumption therefore
   also advances `release_authority` and adds `docs/releases/shell/**` to `owned_files`, which
   amends ADR-0102's decision 10.
2. **A human cuts each shell Release.** There are no prerelease stages and no milestone
   Promotion. A `path:shell` closure advances no line: Pickup infers no Release target for an
   issue owned by a non-Python path, and the Release-line advance refuses a `vX.Y.Z` label on one,
   because that label names Python's line. This Python change is an activation prerequisite.
   Between release commits, a checkout reports the last one's version.
3. **The line starts at `shell/v0.12.0` and stays 0.x.** It rises from the 0.11.2 the shell
   already reports, and a 0.x minor marks the breaking changes of ADR-0102 and ADR-0104. A shell
   1.0.0 waits for a decision that promises a shell Stable surface, and the line never tracks
   Python's numbers.
4. **Identity names the path.** `--version` prints `git-loopy shell 0.12.0`, and
   `wrapper.run.start` carries `path: "shell"` beside `release_version`, an additive change within
   Event schema 1. The tag, `--version` and Run start name the same pair. Python's unqualified
   identity is unchanged.
5. **Each shell Release records the helper Release it consumes, or none.** `release.json` names
   that Release's **Consumed Release** of the **TUI helper**: the exact helper Release it was
   proven with, or `null`. The installer installs exactly that Release, with today's checksum,
   identity and schema checks, and never compares a helper with the shell's own Release.
   Attaching still requires the Event-schema probe, and `artifacts` stays `[]`.
6. **Drift is measured against the Consumed Release.** A clone-local helper must equal the
   tree's Consumed Release, or it fails closed with a reinstall message; when none is recorded,
   the installer clears that slot. A `PATH` helper that differs, or any helper when none is
   recorded, attaches on the probe with a warning naming both Releases. Wrapper contract §15
   gains this consumer clause.
7. **Activation does not wait for a helper.** A shell Release may record none; its installer then
   stages none and says so. A recorded Consumed Release must be published and non-draft, carry the
   archive and checksum of all six macOS and Linux targets, install through the shell installer on
   macOS and Linux, and report a `wrapper_contract_version` at or above the shell's
   **Contract support**. That proof runs in the rehearsal, at publication, and whenever the
   Consumed Release changes.
8. **A vanished Consumed Release is refused, never substituted.** The installer names the missing
   Release, and plain output and `--no-tui` keep working. Before deleting a helper Release, a
   maintainer checks that no published shell Release consumes it.
9. **Publication.** A human pushes an annotated `shell/vX.Y.Z` tag on `main` after rehearsing the
   same checks on the candidate, because public tags never move. A `shell/v*` workflow refuses
   unless the manifest is active, the tag matches the authority file, the shell **Path gate**
   passes on macOS and Linux, `install.sh` from the tagged archive installs a launcher that prints
   the identity, notes are committed at `docs/releases/shell/vX.Y.Z.md`, and the helper proof
   passes when a Consumed Release is recorded. It then publishes a source-only GitHub Release that
   is never marked Latest. Every helper and upgrade resolver, `source-release.yml` and
   `tui-release.yml` match only `v`-prefixed tags, so a shell Release collides with none of them.
10. **Live proof is declared, not obtained per Release.** Each Release's notes state when
    ADR-0103's five runtime proofs last passed and on which Copilot CLI version, as ADR-0094
    declares the live proof a Promotion cannot obtain. When to re-run them belongs to the
    activation gates.

## Considered options

- **A `release_version` field in `paths/shell.json`.** Rejected: the declaration would become a
  counter edited at every Release.
- **A plain `git-loopy/shell/VERSION`.** Rejected: the Consumed Release would need a second file,
  and the pair is what a shell Release proves.
- **A per-path ratchet** with `shell/vX.Y.Z` labels, stages and milestone Promotion. Rejected for a
  serial, low-volume path: Python's writer, Pickup, label vocabulary, Promotion and Rehearsal would
  all have to become path-aware.
- **Starting at `shell/v1.0.0` or `shell/v0.11.3`.** Rejected: the first implies parity with
  Python 1.0.0, and the second understates the change and reuses Python's 0.11.3.
- **A bare `git-loopy 0.12.0`.** Rejected: it reads as an old Python Release.
- **Resolving a helper at install time**, either the helper Python's newest stable installs or the
  newest schema-compatible one. Rejected: a shipped shell Release would later install a helper it
  never proved, and the second may come from Python's alpha line.
- **Attaching only a helper already on `PATH`.** Rejected: it drops the verified installer shell
  operators already have.
- **Waiting for the Helper baseline (#592).** Rejected: it couples shell activation to Python's
  helper pipeline, and ADR-0083 promises the shell no new terminal feature.
- **A live smoke per Release.** Rejected: it spends premium requests on every cut.
- **Falling back when the Consumed Release disappears.** Rejected: it would silently substitute an
  unproven helper.

## Consequences

- PRD #791's promise that only Python's line moves survives resumption by refusal rather than by
  dormancy.
- The activation gates sequence the delivery this implies: Pickup and the Release-line advance
  scoped to the owning path; the shell readers, `source_release.py` and the path loader following
  `release_authority`; the installer's Consumed Release and drift reference; §15's consumer clause
  and qualified identity at a new contract minor; the `shell/v*` workflow and its rehearsal; and the
  paths and releases READMEs.
- PowerShell keeps its baseline authority until its own resumption decides.
