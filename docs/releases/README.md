# Source Release notes

Each Release-line advance writes an agent-authored `dev.N` fragment at
`docs/releases/v<VERSION>.md`. A **Promotion** composes the fragments for its
target into the stable draft at the same conventional location; a stable note a
human already wrote there is preserved rather than overwritten, because the
generated draft is a floor and not a replacement. The matching annotated
`v<VERSION>` tag publishes the committed note verbatim as the GitHub Release
description after the full Runner-family Conformance gate and tagged source
archive identity checks pass.

Publication requires notes that were **authored and committed** — non-empty,
UTF-8, and present in the tagged tree. It does not require that a human edited
them. That requirement used to be the only thing standing between a tag and
publication, which made it the de facto human gate an unattended Release line
cannot have (ADR-0052). A human may still replace any draft before its
Promotion, and should when the Release deserves an essay; publication simply
never waits for one.

A `vX.Y.Z-dev.N` version is the current **development prerelease** on the path
to stable `vX.Y.Z`: `N` counts Release-line advances against that target. It
is published for source identity and release-note history, but is not on any
package channel. Only a stable Promotion can update those channels.

The source-only path relies on GitHub's automatic source archives. It does not
publish package-channel metadata, signed platform artifacts, or a TUI helper.

## Distribution modes

Releases operate under an explicit **distribution mode** ([ADR-0059](../adr/0059-explicit-source-only-distribution-mode.md)),
with the repository policy in [`release-trust.json`](../../git-loopy/conformance/release-trust.json)
or the release declaration as the single authority:

- **`source-only`** (default): The publication promise is complete with GitHub
  source archives and committed release notes. The release flow does not launch
  helper-build, signing, attachment, or channel jobs. It requires no signing secrets,
  binary signing identities, or channel credentials to complete.
- **`artifact-bearing`**: The publication promise includes the seven compiled
  TUI helper archives, checksums, attestations, receipts, and package channels
  (Homebrew, winget, Scoop). It dispatches all helper builds and requires full
  credentials; missing credentials refuse publication without silent downgrade.

The contract is strictly enforced:
- **Single authority**: Neither secret presence nor runner presence alters the contract.
  A source-only release in an environment with signing secrets never builds or
  publishes helper artifacts; an artifact-bearing release without credentials refuses
  rather than silently downgrading to source-only.
- **Fail-closed validation**: Unknown or inconsistent distribution mode declarations
  (e.g., mismatch between tag annotation and explicit configuration) fail closed
  before any publication step runs.

### Runtime line-printer fallback

When running git-loopy from a source-only release where precompiled helper binaries
are not distributed, the Orchestrators fall back automatically to the built-in
**streaming line-printer** output. Operators still receive full progress, phase
transitions, and iteration events printed to standard output.

Operators who want the interactive full-screen dashboard can build the helper
locally from the source checkout:

```sh
cargo build --release --manifest-path git-loopy/tui/Cargo.toml
```

and ensure the compiled `git-loopy-tui` binary is available on `PATH` or placed in
`.git-loopy/bin/`.

## Release target and Promotion

A closed issue's **Bump class** label advances the Release line after
Integration. The Release target is the ratchet across those labels, while the
`dev.N` counter records each advance; see [ADR-0052](../adr/0052-the-release-line-advances-per-issue.md).
An issue's milestone neither selects nor records that target.

Each member's Release writer advances the two live version expectations in
`git-loopy/conformance/release-version.json` in the same atomic write as the
distribution metadata, and includes that fixture in its Release commit.
All other Conformance fixtures remain unchanged.

A `vX.Y.Z` **GitHub milestone** is solely the **Promotion** trigger. Closing it
starts the unattended Promotion: the matching `dev.N` line becomes stable,
`release-promotion.yml` commits it as `chore(release): promote Release line to
<VERSION>`, and its annotated `v<VERSION>` tag starts publication. A `major`
**Bump class** is exempt from the milestone and reaches that same stable state
under a Run, so for it the workflow only tags. Either way it tags every stable
Release the trunk carries that no tag reaches yet, rather than whatever `VERSION`
says at the head: a Run lands a Release-line commit per closed issue and pushes
once per Iteration, so a stable cut is routinely followed into the same push by
the next issue's `dev.N`.

Promotion only accepts a milestone that exists. List them rather than inventing
one:

```sh
gh api repos/{owner}/{repo}/milestones --jq '.[] | "\(.title)\t\(.state)"'
gh issue edit <number> --milestone "vX.Y.Z"
```

### The Promotion publishes it

`release-promotion.yml` is what turns that closure into a Release, and nobody
approves it: no protected environment, no review, no waiting. That is recorded in
[ADR-0052](../adr/0052-the-release-line-advances-per-issue.md) as a consequence
taken deliberately — an agent's inferred `semver:major` label can publish a
breaking Release unattended — and it is not to be re-added as an implementation
detail.

A Promotion needs one credential: **`RELEASE_PUBLICATION_TOKEN`**, a repository
secret carrying `contents: write`. Publication is entered by pushing an annotated
`v<VERSION>` tag, and a tag pushed with the workflow's own `GITHUB_TOKEN` starts
no workflow run at all — the Release pipeline would simply never happen, with
nothing red to say so. The Promotion is therefore refused before it commits or
tags anything when that secret is absent, rather than leaving `main` claiming a
stable version that was never published. It is not a signing or channel
credential and lives outside the protected `release` environment described below,
because a Promotion that waited on that environment's reviewers would be the
human gate this design declined.

Prereleases take no part in any of it. They consult no milestone, this workflow
tags none, and none reaches a package channel.

### Open boundary

What happens when a human closes a milestone-bearing issue outside a **Run**
remains open. The Release line advances post-Integration; whether an
out-of-Run closure advances it or only a Run can do so is deliberately
undecided ([ADR-0052](../adr/0052-the-release-line-advances-per-issue.md)).

## Platform trust for helper artifacts

The `git-loopy-tui` helper Release (`.github/workflows/tui-release.yml`) is
gated on what each artifact can *prove* about itself, not on a signing step
having run without error. Both signers in the pinned cargo-dist degrade to a
warning when their credentials are absent — absent, not empty, so the workflow
withholds an incomplete credential set instead of handing it over — and every
gate here reads the artifact rather than the pipeline.

| Channel | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Stable (`vX.Y.Z`) | Developer ID signature, hardened runtime, accepted notary verdict, checksum | Hardware-backed signature, readable publisher, checksum | Checksum |
| Prerelease (`vX.Y.Z-rc.1`, `-dev.0`, …) | Checksum | Checksum — an **unsigned** Windows artifact is permitted here and nowhere else | Checksum |

A stable Release additionally requires a build-provenance attestation. Any
missing artifact, signature, notary verdict, publisher, checksum, or attestation
refuses the whole publication rather than shipping a partial set.

The version string decides which of those a Release *needs*; the prerelease flag
on the GitHub Release is what tells an operator — and every package channel that
resolves "the stable Release" — which channel they are installing from. Those
are two answers to one question, and the unsigned Windows allowance rests
entirely on the second, so publication reads the marking back off the Release it
is about to attach to and refuses to upload when the two disagree. `--prerelease`
is applied by `source-release.yml`, deliberately a separate workflow, and the
flag stays editable afterwards; the helper pipeline therefore proves it rather
than inheriting it.

Signing runs inside `dist build`, which is the only place it can: cargo-dist
writes each `.sha256` afterwards, so a published checksum is a checksum of the
signed artifact. A ticket cannot be stapled into a bare Mach-O — stapling needs
a bundle, `.dmg`, or `.pkg` — so Gatekeeper resolves the helper's notarization
online, and `release-trust.json` records that by name rather than leaving it to
look like an oversight.

### Credentials

Signing credentials live only in the protected `release` environment. A tagged
build enters it; every pull request enters `validation`, which holds none. The
boundary is the platform's rather than a step's `if:`, so a job that never sees
a credential cannot leak one however it is edited later. The exact credential
names are declared in
[`release-trust.json`](../../git-loopy/conformance/release-trust.json), and the
release pipeline is refused if it reads any secret that fixture does not name.

Not every credential signs. A **channel credential** writes on this project's
behalf *outside* this repository — the Homebrew tap token pushes the generated
formula and opens its pull request, and the winget and Scoop tokens do the same
for theirs — so each is declared in the same fixture with the one job allowed to
read it, and confined to the same protected environment. That keeps one registry
rather than one per channel: a channel carrying its own credential list could add
one nothing reviewed.

## Package channels

A tagged **stable** Release also updates the maintained package channels. They
publish no bytes: each points at the archives the pipeline above already
verified, attested, and attached, and at the `.sha256` published beside them.
Nothing is rebuilt, re-signed, or re-hashed — recomputing a digest would be a
second chance to write down a different number than the one operators verify
against.

| Channel | Platforms | Metadata |
| --- | --- | --- |
| Homebrew (`bradcstevens/homebrew-git-loopy`) | macOS arm64/x64, Linux arm64/x64 (glibc) | [`homebrew-tap.json`](../../git-loopy/conformance/homebrew-tap.json) |
| winget (`bradcstevens/winget-pkgs` → `microsoft/winget-pkgs`) | Windows x64 | [`windows-channels.json`](../../git-loopy/conformance/windows-channels.json) |
| Scoop (`bradcstevens/scoop-git-loopy`) | Windows x64 | [`windows-channels.json`](../../git-loopy/conformance/windows-channels.json) |

winget is the one channel whose metadata leaves this project's namespace. Its
default source is the community repository, so the manifests are pushed to this
project's **fork** and the pull request is opened **across repositories** into
`microsoft/winget-pkgs`. Both repositories are named in the fixture rather than
inferred from the checkout's remotes: a pull request whose base was guessed is
one that can quietly land in a fork nobody installs from. Homebrew and Scoop are
repositories an operator adds by name, so each opens its pull request against
itself.

Channel metadata is generated and then **read back and refused** by a separate
gate, because what reaches operators is whatever is committed to the channel —
including text a human edited. Metadata is refused unless the version it
declares is this Release's, every URL resolves through the one shared download
template from the trusted host, each platform fetches that platform's artifact,
every digest is the published one, no covered platform is missing, and the
committed text is byte-for-byte the text this Release generates.

The two Windows channels prove one thing more, because on Windows an operator is
shown a **publisher** rather than a digest. That name is the Authenticode
subject the release runner observed on the artifact it had just signed, recorded
in the artifact's own `.trust.json` receipt — so both channels read that receipt
back before writing anything, and an unsigned or unattributable Windows artifact
reaches neither. Where a format has a field for it, winget's `Publisher`, the
name is pinned in the committed text and drift from the receipt is refused;
where it has none, Scoop's, the gap is recorded by name in the fixture rather
than left to look like an oversight. The same fixture records the mirror-image
gap: Scoop's `post_install` proves `--version` on the operator's own machine and
winget has no hook that can.

Prereleases never reach a channel. The version string says which channel a
Release is on and the prerelease flag says what an operator sees, and a channel
resolving "the stable Release" depends entirely on the second — so the marking is
read back off the completed Release and a disagreement refuses the update rather
than resolving it in favour of either. On Windows that rule carries the most
weight: a prerelease is exactly the Release whose Windows artifact the
platform-trust gate allows to be unsigned.

Operator instructions for each channel — installation, upgrade, helper discovery,
compatibility diagnostics, and how a channel-installed helper interacts with a
clone-local one — live with the helper, in
[its README](../../git-loopy/tui/README.md#homebrew).
