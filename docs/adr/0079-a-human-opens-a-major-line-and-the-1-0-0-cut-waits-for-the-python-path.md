# A human opens a major line, and the 1.0.0 cut waits for the Python path

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Amends [ADR-0066](0066-a-version-label-names-the-release-and-prereleases-move-alpha-beta-rc.md).
Decided by [Decide how the 1.0.0 cut is triggered](https://github.com/bradcstevens/git-loopy/issues/666).

The Python path's 1.0.0 is cut the way ADR-0066 already cuts every stable Release: closing the
`v1.0.0` milestone starts the **Promotion**. There is no dedicated cut issue and no temporary
release-policy change. ADR-0066 had already removed the original hazard — a `major` label no longer
publishes a stable Release unattended — so what remained was who opens the major line, and what the
cut must prove before it tags.

**Only a human opens a major line.** Every 1.0.0-scope ticket carries an explicit `v1.0.0` label
when it is written, so the Pickup classifier never guesses it. Pickup never writes a label for an
inferred `major`: it writes nothing and reports that a major needs a human-applied label. This holds
in every repository git-loopy runs in. The Release target only ratchets upward, so a classifier
that read one ordinary issue as breaking would move the whole line to the next major's `alpha`, and
the only way back is an operator's by-exception commit. Opening a major line is a product decision,
as Promotion already is.

**The first `v1.0.0` ticket to integrate ends 0.x.** From then on, the Release line is
`1.0.0-alpha.N` and every later Pickup writes `v1.0.0`. No 0.x maintenance line is kept: a 0.11
defect found after that point is fixed in 1.0.0 only. Testers reach the work through the existing
human-tagged `1.0.0-alpha`/`-beta`/`-rc` prereleases and `git-loopy upgrade`. The operator still
decides every stage move, and the completion bar for them belongs to
[Define end-to-end completion and its quality guardrails](https://github.com/bradcstevens/git-loopy/issues/662).

**The cut proves the language-path split in code.** The release rehearsal refuses a Python stable
candidate at `1.0.0` or above unless the repository has an active Python **Path manifest** that owns
the unqualified `vX.Y.Z` namespace ([ADR-0073](0073-language-paths-release-and-gate-independently.md)).
Every stable commit reaches a tag only through that rehearsal, whether a closed milestone or an
operator's by-exception promotion produced it, so the refusal cannot be bypassed by choosing the
other route. Keeping the milestone open until the split lands is good tracker hygiene, but nothing
depends on it.

## Considered options

- **A final cut issue, or a temporary policy change.** Both add a second cut mechanism beside the
  milestone Promotion that ADR-0066 already provides.
- **Interim 1.0.0 work labelled as the next 0.x minor, with one final `v1.0.0` ticket.** That would
  publish a 0.x stable Release containing part of the breaking 1.0.0 work.
- **Letting the classifier infer `major`, or making the guard opt-in per repository.** In either
  case, one misreading still moves the line irreversibly.
- **Tracker discipline alone for the path split.** A by-exception promotion never meets the
  milestone, so only a refusal in the rehearsal covers both routes.
- **A `release/0.11` maintenance branch.** No release workflow supports one, and the Python path is
  the only active path with a small user base.
