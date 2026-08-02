# Pin the external Skill catalog as the reproducible source of record

**Status:** accepted; the redistribution half is superseded by
[ADR-0025](0025-installed-skill-catalog.md).

> **Superseded in part.** The pin, the acquisition command, and every validation
> rule below still stand — they are what an install is proved against. What no
> longer holds is the *destination*: git-loopy ships no vendored
> `git_loopy/skills/` fallback, does not read the consuming project's
> `.copilot/skills/`, and does reach the network for its Skills, at setup and at
> the start of every Run. Read "The three-layer boundary" and the packaged
> fallback in this ADR as history; ADR-0025 states the current arrangement.

git-loopy's workflow Skill catalog has three copies with no stated relationship:
`bradcstevens/git-loopy-skills` upstream, the repo-root `.copilot/skills/` a
maintainer edits, and the `git_loopy/skills/` tree vendored into the wheel.
ADR-0015 fixed which Skills a **Run** may load; it said nothing about where the
catalog *comes from*. Without that, "the catalog git-loopy 0.2.0 ships" is a
question whose answer changes with whoever last edited a directory, and a
provenance claim in `THIRD_PARTY_LICENSES.txt` cannot be checked against
anything. We will name the external catalog the source of record, pin it to an
immutable revision, and give that pin one command that turns it into evidence.

## Decision

### Source of record and pin

- `bradcstevens/git-loopy-skills` is the source of record for git-loopy's
  workflow Skill catalog. `git-loopy/python/git_loopy/skill_source.json` records
  its URL, the exact revision git-loopy stands behind, the subdirectory the
  Skills live in, and the licence — by digest as well as by marker text — and
  provenance material an acquisition must carry.
- The pinned revision is a full 40-character commit SHA. A branch, a tag, or an
  abbreviated SHA is refused when the pin is read, so no maintenance or Release
  path can rest on a floating ref.
- The pin ships as wheel package data. A released artifact can therefore always
  answer which upstream revision it installs, without a source checkout and
  without the network.
- Moving the pin forward is a reviewed edit to one committed file, made together
  with whatever catalog change it justifies.

### Acquisition and validation

- One documented command acquires and validates the pinned revision:
  `uv run --project git-loopy/python python -m git_loopy.skill_source`. It
  fetches the commit SHA itself rather than a ref that points at it today, so a
  moved branch cannot deliver different content under the same pin. `--offline`
  re-validates an existing acquisition without contacting the remote.
- Acquisition may fetch only over `https://` or `file://`. `ssh://`, `git://`,
  and scp-style remotes are refused: acquiring the source of record is an
  anonymous, reproducible read, not something whose result depends on whose
  credentials are loaded.
- Validation fails closed, in the order a reader cares about, and each failure is
  its own named error: the checkout is not the pinned revision (or is at it while
  holding content that revision does not, including ignored files, so what would
  be validated is not the pinned content); the tree is not a well-formed Skill
  catalog (a loose file, a directory without `SKILL.md`, unreadable metadata, or
  a canonical name that disagrees with its directory); or the licence and
  provenance material the pin requires is absent, empty, or not byte-for-byte the
  notice its digest names.
- A tracked symlink is ordinary committable content, so a checkout can be clean,
  at the pinned revision, and still point `skills/`, a `SKILL.md`, the licence, or
  a provenance file at the validating host. Any manifest-named path that is a
  symlink, or that resolves outside the checkout, is refused.
- Acquisition writes only where it owns: an empty directory, or a previous
  acquisition marked inside its own `.git`. Because it force-checks-out the
  pinned revision, a directory that merely contains a `.git` is not enough to
  earn that treatment — someone's uncommitted work is not an acquisition.
- "We could not check" never reads as "it checked out". A directory that is not a
  git checkout cannot prove its revision and is refused rather than validated on
  its contents alone.

### The three-layer boundary

| Layer | Where it lives | Reached |
| --- | --- | --- |
| **External catalog** (source of record) | `bradcstevens/git-loopy-skills` at the pinned revision | only by the explicit maintainer command |
| **Packaged fallback** | `git_loopy/skills/` inside the wheel, generated from `.copilot/skills/` by `scripts/sync_skills.py` | from disk, offline, at Run time |
| **Consumer project Skills** | `<repo>/.copilot/skills/` in the consuming project | from disk, and they win (ADR-0015) |

- A **Run** never reaches the network for a Skill. Every Iteration resolves its
  Skills from the packaged fallback and the consumer project's own tree, so an
  offline operator, an air-gapped runner, and an unreachable upstream are all
  non-events.
- The packaged fallback is a deliberate *subset* of the source of record: the
  optional tool/vendor integrations are excluded by `SKILL_DENYLIST`. Skills the
  upstream has and the wheel does not are therefore normal. A Skill the **wheel
  ships that the pinned source of record does not have** is the interesting
  direction, and the command reports it as a warning.
- Consumer project Skills are outside this boundary entirely. A project may hold
  any Skill it likes under its own `.copilot/skills/`; the pin governs what
  git-loopy redistributes, never what a consuming repository is allowed to have.

## Considered options

- **Track the upstream default branch** — rejected because the catalog a given
  Release shipped would then be unknowable after the fact, which is the whole
  purpose of a provenance record.
- **Vendor a content digest of the whole catalog instead of a commit** — rejected
  because a digest cannot be fetched. A maintainer needs to *get* the reviewed
  content, and a commit SHA is both an identity and an address.
- **Acquire the catalog at Run time (or at install time) from upstream** —
  rejected because it would make autonomous Runs depend on network reachability
  and on upstream availability, and would let a Run's capabilities change without
  a reviewed commit.
- **Make the upstream repository a git submodule** — rejected because the
  vendored copy has to be readable inside a built wheel, where no submodule
  exists, and because a submodule bump is a less legible review artifact than an
  edited pin plus the catalog diff it justifies.
- **Fail the command when the packaged fallback and the pinned catalog differ** —
  rejected because the difference is a decision (`SKILL_DENYLIST`), not a defect.
  It is reported, and only the reverse direction warns.

## Consequences

- `THIRD_PARTY_LICENSES.txt` can name a checkable immediate upstream at a stated
  revision rather than an unpinned project.
- The pin is a standing invariant with teeth: the guard suite proves the
  committed pin is immutable and exercises the real acquisition path against a
  `file://` remote built in a temporary directory, so the normal Python suite
  covers acquisition and validation with no live network.
- Refreshing the catalog is a reviewed edit to one committed file: bump the pin.
  (ADR-0023 originally required regenerating a vendored tree alongside it; under
  ADR-0025 there is no vendored tree, so the pin bump is the whole change.)
- The upstream repository is load-bearing for maintenance, and — since ADR-0025
  — for a Run's Skills too, with an unreachable upstream degrading to a warning
  rather than a failure.
