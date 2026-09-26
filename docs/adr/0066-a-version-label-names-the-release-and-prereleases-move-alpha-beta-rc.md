# A version label names the Release, and prereleases move alpha → beta → rc

**Status:** accepted

Amends [ADR-0052](0052-the-release-line-advances-per-issue.md). It keeps that decision's
ratchet, its post-Integration advance, its Wrapper-contract obligation, and the milestone as the
**Promotion** trigger. It replaces the label that carries the bump, the prerelease spelling, and
the `major` exemption.

## What changed

**The label is the Release version the issue ships in.** An issue carries one `vX.Y.Z` label,
for example `v0.11.0`, instead of one of the four `semver:` keys. The `semver:` prefix and all four
`semver:*` labels are retired. The **Bump class** survives as the internal vocabulary
(`major`, `minor`, `patch`, `none`) the Pickup classifier answers in and the
`wrapper.release.advanced` Event carries, but it is now *derived*: a label is read against the
last stable Release `M.m.p`, and it must be exactly one of that Release's three successors —
`(M+1).0.0`, `M.(m+1).0` or `M.m.(p+1)`.

Only a label that opens like a version (`^v[0-9]`) is a claim. A claim that is not a strict stable
`vMAJOR.MINOR.PATCH` (`v0.11`, `v0.11.0-beta.1`, `v01.2.3`) is refused as
`malformed_release_target_label`; two claims are `conflicting_release_target_labels`; a
well-formed claim that is not a successor (`v0.13.0`, `v0.10.0`, `v0.11.1` after `0.10.0`) is
`unreachable_release_target`. Refusal is still a diagnostic, never a veto on a published merge.

**No label means no version change.** ADR-0052 made absence an *unclassified fault* so a docs issue
and a crashed labeller could not look alike, and paid for it with a fourth key. That key is gone
by an operator decision: "no label to indicate the versioning should have a value of none". The
cost is taken deliberately: Pickup cannot tell "classified as no bump" from "not yet classified",
so an issue with no label is classified again at each Pickup. A Pickup that infers `none` writes
nothing.

**Pickup writes the ratcheted target.** The classifier still infers a closed Bump class from the
issue's own content. What it writes is `v<max(current target, successor(last stable, class))>`,
creating that label on the tracker first, because the version set is open and `init` provisions
none of it. The label therefore reads as the Release the issue will actually ship in at the time
it was picked up: a patch picked up while `0.11.0` is under way is labelled `v0.11.0`, not
`v0.10.1`. The Integration-time ratchet is unchanged, so the result stays order-independent.

**Prereleases follow SemVer 2.0.0: `X.Y.Z-alpha.N` → `-beta.N` → `-rc.N` → `X.Y.Z`.**
`-dev.N` is retired everywhere. The Release line is `(target, stage, counter)`:

- Each advancing issue adds 1 to the counter. A target raised mid-line keeps counting, as
  ADR-0052 required, so two Integration orders still land the same version.
- A line that starts from stable, or whose target is raised, is at `alpha`: a new target has
  not been through any later stage. That rule is order-independent too, because the final
  target is.
- Moving to `beta` or `rc` is an operator's decision, never a label's. It only goes forward,
  restarts the counter at 1, and writes a prerelease fragment, e.g. `0.11.0-alpha.8` →
  `0.11.0-beta.1`. It runs as
  `python -m git_loopy.release_version --advance-stage beta|rc` or through the
  `workflow_dispatch` on `release-promotion.yml`.
- A closed `vX.Y.Z` milestone promotes its line from any stage.
- Python distribution metadata spells the stages the PEP 440 way (`0.11.0a1`, `0.11.0b1`,
  `0.11.0rc1`).

**`major` is no longer exempt.** ADR-0052 let a `major` label publish a stable Release
unattended, and recorded that an agent could therefore ship a breaking change with nobody awake.
A major now starts an `alpha` line like any other bump, `v2.0.0-alpha.1` through `v2.0.0`, and
only the milestone cuts stable. The release rehearsal's stable-commit trigger (`--stable-commit`,
formerly `--major-bump`) now only ever meets a committed milestone Promotion.

## Migration taken with this decision

The published `-dev.N` prereleases (`v0.9.0-dev.0`, `v0.10.0-dev.1`, `v0.11.0-dev.1` through
`-dev.8`) were deleted as tags and GitHub Releases, not renamed. Their `0.11.0` notes are folded
into `docs/releases/v0.11.0-alpha.1.md`, and the line restarts at `0.11.0-alpha.1`. Two
consequences follow from that and were accepted:

- `v0.11.0-dev.7` and `-dev.8` were the only artifact-bearing Releases, and so the only
  downloadable **TUI helper** baseline. Until an artifact-bearing prerelease is tagged again,
  the helper resolver finds no published helper at or below the declared version and a source
  build is the path.
- SemVer ranks `alpha` and `beta` below `dev`, so an installation still on `0.11.0-dev.8` cannot
  prove `0.11.0-alpha.1` newer; it moves with `git-loopy upgrade --allow-downgrade`.
