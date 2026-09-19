# Verify the promoted snapshot before publishing an immutable tag

**Status:** accepted; publication automation must be brought into conformance
before the next release claims this guarantee.

Amends the publication ordering of
[ADR-0052](0052-the-release-line-advances-per-issue.md), not its Release target,
Bump class, or unattended Promotion policy. Closing an existing matching
milestone and the major-bump exemption remain the triggers. This adds machine
proof, not a protected environment, human review, or approval per Promotion.

The v0.10.0 development snapshot passed the family gate, but Promotion changed
its metadata and exposed a stale live version fixture. A repair snapshot then
passed ordinary CI while failing the publication rule that the tagged commit
must itself carry a version bump. Checking a nearby commit was not proof of the
distribution being published. Recovering by moving an already-public tag also
changed an identity an operator could already have fetched.

## Prove the exact snapshot before exposing the tag

Prepare the complete stable commit, its annotated candidate tag, and its source
archive before publishing a tag. The exact proposed stable commit must pass
every Runner-family gate, all distribution/live-fixture version checks,
committed-note checks, and the annotated-tag/version-bump-commit and source
archive verifiers. A green development ancestor or repair commit is not a
substitute. Changing the candidate invalidates proof for the previous candidate;
a concurrent change to main must never silently change what gets tagged.

Verification failure creates no public tag or GitHub Release. Once published,
the remote identity, release marking, notes, and source archive are read back
against the verified publication input. Preparing a candidate and publishing it
are separate from inferring a new Release target: a planning document does not
assign that target or invent a milestone.

## Source-only is an explicit promise

The next release is source-only: the GitHub Release carries the committed notes
and GitHub supplies the source archives. This mode must be selected explicitly,
not inferred from whether signing credentials happen to be present. It launches
no helper-build, signing, helper-attachment, or package-channel publication jobs
and requires no signing credentials. Manual cancellation of those jobs is not
an implementation of the mode.

This is not a weakening of the trust requirements for an artifact-bearing
Release. Missing credentials cannot silently downgrade that promise to
source-only. Delivering signed helpers or updating Homebrew, winget, or Scoop is
outside this next-release scope.

## A public tag is immutable; a failed publication is resumable

Once public, a tag is never moved, including when no GitHub Release object was
successfully created. If content must change, it needs a new Release version.
Before publication, a candidate may instead be repaired and reverified.

A retry reconciles the same verified publication input. An existing Release is
accepted only when its identity, marking, distribution promise, and committed
notes agree. A mismatch is an explicit refusal, not a reason to overwrite the
Release, recreate its tag, or declare success. Interrupted or ambiguous network
responses require readback rather than an assumption that the write failed.
Matching already-published state is a successful no-op, not a duplicate
publication. Failed-tag replacement during the v0.10.0 recovery is not precedent
for normal future release procedure.

## Proof includes a bounded real-host smoke

Deterministic offline tests cover the supported platform/host matrix and
failure, retry, identity-drift, and terminal-control scenarios. The highest
publication boundary is the Promotion/publication entry point reaching actual
Git refs, generated archives, and Release readback; inspecting workflow YAML or
passing a writer helper test alone is insufficient.

Stable publication additionally requires a release-only smoke on clean
installations of the candidate using both local and actual GitHub Actions
Execution hosts and the public operator commands. It uses disposable work,
explicitly configured credentials, and finite work, time, and spend limits.
It must not change production issues. Limits or authorization are never
invented to make the check run; unavailable or inconclusive proof blocks
publication with an actionable diagnostic.

These live checks stay outside the network-independent Integration feedback
loops declared in AGENTS.md. Their dependency on an external service is paid
at release time, not on every Lane merge. Once authorized and configured, they
run unattended; this is not a new recurring human gate.

## Considered and rejected

- Tag first and repair after a failed gate: the public source identity has
  already escaped before it is proved.
- Reuse green development CI: Promotion itself changes the tree the gate must
  judge.
- Retag while a GitHub Release is absent: absence of that object does not prove
  nobody fetched the public tag.
- Infer source-only mode from missing secrets: a partially configured
  artifact-bearing Release would appear to succeed with a different promise.
- Make the live smoke advisory: a known unavailable proof would still permit
  the next stable Release to advertise both Execution hosts.
