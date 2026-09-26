# Source Release notes

Each Release-line advance writes an agent-authored prerelease fragment at
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

A `vX.Y.Z-alpha.N`, `vX.Y.Z-beta.N` or `vX.Y.Z-rc.N` version is the current
**prerelease** on the path to stable `vX.Y.Z`, spelled the Semantic Versioning
2.0.0 way ([ADR-0066](../adr/0066-a-version-label-names-the-release-and-prereleases-move-alpha-beta-rc.md)):
`N` counts Release-line advances within the current stage. It is published for
source identity and release-note history, but is not on any package channel.
Only a stable Promotion can update those channels. The earlier `-dev.N`
prereleases are retired; their tags and GitHub Releases were removed and the
`0.11.0` ones are folded into `v0.11.0-alpha.1.md`.

The source-only path relies on GitHub's automatic source archives. It does not
publish package-channel metadata, signed platform artifacts, or a TUI helper.

## Distribution modes

Releases operate under an explicit **distribution mode**,
with the repository policy in [`release-trust.json`](../../git-loopy/conformance/release-trust.json)
as the single authority:

- **`source-only`**: The publication promise is complete with GitHub
  source archives and committed release notes. The release flow does not launch
  helper-build, signing, attachment, or channel jobs. It requires no signing secrets,
  binary signing identities, or channel credentials to complete.
- **`artifact-bearing`**: The publication promise includes the seven compiled
  TUI helper archives, checksums, receipts, and the trust evidence required by
  the Release's channel. Stable Releases additionally require attestations and
  update package channels (Homebrew, winget, Scoop); prereleases update none.
  It dispatches all helper builds; artifact trust gates refuse publication
  rather than silently downgrading to source-only.

This repository currently declares **`artifact-bearing`**. The artifact-bearing
`v0.11.0-dev.7` and `-dev.8` prereleases were removed with the retired `-dev.N`
line (ADR-0066), so no published Release carries the helper baseline described
below until an artifact-bearing prerelease is tagged again. Every earlier tag was
source-only and is left exactly as published.

The contract is strictly enforced:
- **Single authority**: Neither secret presence nor runner presence alters the contract.
  A source-only release in an environment with signing secrets never builds or
  publishes helper artifacts; an artifact-bearing release whose trust gate fails
  refuses rather than silently downgrading to source-only.
- **Fail-closed validation**: Unknown or inconsistent distribution mode declarations
  (e.g., mismatch between requested publication mode and repository policy) fail closed
  before any publication step runs.

The trust policy must exist in the tagged commit and the publication worktree's
copy must match it. An untracked policy cannot supply
a promise the tag never made. A source-only Rehearsal forwards its explicit mode
to that verifier and refuses a committed artifact-bearing policy rather than
returning a source-only Publication input for it. Unreadable policies, including
invalid UTF-8, are publication refusals, not implicit defaults.

### The downloadable helper baseline

An artifact-bearing Release is a downloadable helper baseline. It publishes
`git-loopy-tui` for all seven declared targets, each with its checksum and trust
receipt. `v0.11.0-dev.7` was the first; it was removed with the retired `-dev.N`
line, so the next artifact-bearing prerelease re-establishes the baseline:

| Platform | Triple |
| --- | --- |
| macOS arm64 | `aarch64-apple-darwin` |
| macOS x64 | `x86_64-apple-darwin` |
| Windows x64 | `x86_64-pc-windows-msvc` |
| Linux arm64 (glibc) | `aarch64-unknown-linux-gnu` |
| Linux x64 (glibc) | `x86_64-unknown-linux-gnu` |
| Linux arm64 (musl) | `aarch64-unknown-linux-musl` |
| Linux x64 (musl) | `x86_64-unknown-linux-musl` |

These are **prerelease**-channel artifacts: checksums and trust receipts are
required and verified, while Developer ID signing, macOS notarization, Windows
publisher evidence, and build attestation remain stable-only gates that this
channel does not claim. A supported installation consumes the baseline through
`git-loopy update` — including the `update` chained by `git-loopy upgrade` — with
no Rust toolchain and no source checkout.

**Known gap.** The Windows archive is built, verified, and published like every
other target, but the helper does not yet implement the Runner's
attachment/control protocol on Windows, so a Windows operator cannot yet attach
the Dashboard to a Run. That is the outstanding obligation of
[#459](https://github.com/bradcstevens/git-loopy/issues/459); no declared target
was dropped to conceal it.

### Consuming an older helper from a source-only Runner Release

Source-only describes publication, not Dashboard compatibility. Python maintenance
(`git-loopy update`) resolves the newest published helper at or below the installed
Runner's Release version. It verifies the checksum, the helper's resolved Release
identity, and its Event-schema compatibility before activation. A newer helper is
not substituted, and an incompatible older one is not made compatible merely by
being downloadable (ADR-0052; #591).

A matching machine-local source build is retained when its version and schema
match the installed Runner, no exact helper is published, and the immutable tag's
trust policy explicitly declares `source-only`. This preserves a locally built
Dashboard without inferring publication mode from missing assets. Unreadable
release history or policy still refuses maintenance.

The shell and PowerShell installers also resolve the newest non-draft helper
Release at or below the declared version, requiring the host's archive and
checksum. They verify the resolved identity and retain its install record beside
the helper; failed activation restores the previous helper and record together
(#492). Like Python, each resolver reads at most ten pages and explicitly refuses
an incomplete index at that limit. Their `--no-tui` / `-NoTui` options skip that download; they do not install
a Dashboard. The built-in **line-printer** remains a diagnostic/plain-output path,
not a replacement for the Python Runner's required terminal interface (ADR-0053).

A Runner Release older than the baseline still has no exactly matching published
helper. Such a Runner resolves the newest eligible *older* helper as described
above; where none exists, build from the matching source checkout rather than
treating a source-only Release as a binary download:

```sh
cargo build --release --manifest-path git-loopy/tui/Cargo.toml
```

and ensure the compiled `git-loopy-tui` binary is available on `PATH` or placed in
`.git-loopy/bin/`.

## Release target and Promotion

### Releasing a completed batch with `/release`

The user-invoked
[`/release` skill](https://github.com/bradcstevens/git-loopy-skills/blob/main/docs/release.md)
operates in the repository where git-loopy completed the work. It groups all
unreleased, integrated issue completions into one release and follows that
project's own versioning and publication procedure. Install it in your agent from
the external skills catalog; this does not change git-loopy's pinned Run catalog
or add publication authority to an Agent.

For this repository, `/release` promotes the existing Release target through the
Promotion path below. It does not bump again per issue, invent a milestone, or
bypass candidate proof. Its stable batch includes the development fragments
since the previous stable release. Missing prerequisites are reported as blockers,
and an already-published batch is a no-op.

### Advancing the target

A closed issue's `vX.Y.Z` **Release-target** label advances the Release line
after Integration. The label must be one of the last stable Release's three
successors — after `0.10.0`, only `v0.10.1`, `v0.11.0` or `v1.0.0` — and an issue
with no such label changes no version. Pickup infers the **Bump class** and
writes the label, creating it on the tracker; `git-loopy init` provisions none.
The Release target is the ratchet across those labels, while the prerelease
counter records each advance; see
[ADR-0052](../adr/0052-the-release-line-advances-per-issue.md) and
[ADR-0066](../adr/0066-a-version-label-names-the-release-and-prereleases-move-alpha-beta-rc.md).
An issue's milestone neither selects nor records that target.

A line that starts from stable, or whose target a larger label raises, is at
`alpha`. Moving it to `beta` or `rc` is an operator's decision: it only goes
forward, restarts the counter at 1, and writes that prerelease's fragment.
Run the **Promote Release line** workflow by hand (`workflow_dispatch`, choosing
`beta` or `rc`), which commits the advance to `main`, or do the same locally and
commit the result:

```sh
uv run --project git-loopy/python --all-extras \
  python -m git_loopy.release_version --repository-root . --advance-stage beta
```

Either way the prerelease tag stays a human act, like every prerelease tag.

Each member's Release writer advances the two live version expectations in
`git-loopy/conformance/release-version.json` in the same atomic write as the
distribution metadata, and includes that fixture in its Release commit.
All other Conformance fixtures remain unchanged by version advancement alone.
Separately reviewed SDK or contract changes may intentionally edit their own
fixtures, such as the roster's CLI provenance stamp when the SDK pin changes;
those edits are not made by the Release-version writer.

A `vX.Y.Z` **GitHub milestone** is solely the **Promotion** trigger. Closing it
starts the unattended Promotion: the matching line becomes stable from whatever
stage it has reached, `release-promotion.yml` commits it as `chore(release):
promote Release line to <VERSION>`, and its annotated `v<VERSION>` tag starts
publication. It is the only way a stable value reaches `main`: a `major` is a
prerelease like any other bump. On every push the workflow still tags every
stable Release the trunk carries that no tag reaches yet, rather than whatever
`VERSION` says at the head, so a stable commit whose tag failed to push is found
again, even when the next issue's prerelease advance has already followed it.

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
taken deliberately — a closed milestone publishes without further review — and
it is not to be re-added as an implementation detail. An agent's inferred label
can no longer publish a stable Release on its own (ADR-0066).

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

### Rehearsing a candidate before its tag exists

A **Promotion** changes the tree the Runner-family gate has to judge, so a green
development ancestor proves nothing about the distribution that would be
published from it. During v0.10.0 that cost two failures in a row: a stable
transformation left the live version fixture behind, and the repair snapshot
then passed ordinary CI while failing the rule that a tagged commit must itself
carry the version bump. Recovering moved an already-public tag.

So every candidate is **rehearsed** before a tag for it exists
([ADR-0059](../adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)).
`git_loopy.release_rehearsal` constructs the complete proposed stable commit,
its annotated candidate tag and its generated source archive inside a throwaway
clone **whose remote is removed before a single ref is written**, then proves
that exact snapshot:

- every distribution Release-version copy and the one live
  `release-version.json` fixture agree, and no other Conformance fixture churns;
- the annotated tag resolves to the commit under test, whose own history carries
  the `VERSION` bump — a nearby repair commit is refused however green it is;
- the committed release notes are non-empty UTF-8 in the tagged tree, and stable
  notes a human already wrote are what gets published;
- the generated archive extracts to `git-loopy-<version>/` and every
  Orchestrator in it reports that Release version;
- every runnable `AGENTS.md` feedback loop is green **on the candidate**, not on
  its ancestor.

What it returns is the **publication input**: the commit, tree, annotated tag
object, committed notes and their digest, the archive and its digest, the trunk
commit the candidate was built from, and the explicit distribution promise. That
promise is *stated*, never inferred — a source-only rehearsal proves committed
notes and a source archive and refuses to certify any other mode, because it has
no evidence for one.

```sh
uv run --project git-loopy/python --all-extras \
  python -m git_loopy.release_rehearsal \
  --repository-root . \
  --workspace "$RUNNER_TEMP/candidate" \
  --archive-output "$RUNNER_TEMP/git-loopy-source.tar" \
  --distribution-mode source-only \
  --stable-commit --candidate-commit "$commit"
```

`release-promotion.yml` runs exactly that per candidate before it creates a tag,
and the step is `-eo pipefail`, so a refusal ends it with no tag created and
nothing pushed. A rehearsal needs no publication credential and touches no
tracker; it reads the trunk and writes only inside its own workspace.

Two boundaries this **does not** move. Proof is of content, so repairing a
candidate makes a *new* candidate that has to be rehearsed again — that is what
`confirm_publication_input` refuses on, and it is also why concurrent work on
`main` cannot retarget a proof: the input binds a commit SHA, and a moved trunk
is visible in `base_commit` rather than silently substituted. And the rehearsal
is not a bypass: `source-release.yml` still gates the pushed tag, and the
release-only real-host smoke ADR-0059 requires before a stable publication
(below) is not yet a condition of the Promotion — composing the two is #590.

### The release smoke

A rehearsal proves content. The **release smoke** proves the operator path of
that exact content on real hosts: `git_loopy.release_smoke` takes the
**publication input** a rehearsal returned, recomputes its proof, re-digests
its archive, and installs *that archive* into a clean `uv tool` installation
under a private tool directory. An existing local installation, the current
`main`, and a published Release are never what it runs — the operator
environment it builds has a fresh `HOME` and config home, no ambient Copilot
or GitHub credential, and no other `git-loopy` or `git-loopy-tui` on `PATH`.

Through the installed candidate's public commands it then proves, against
disposable work:

- `git-loopy init` cancelled in a real terminal writes no Config, and a saved
  `init --yes` does;
- per Execution host (`local`, then `github-actions`), one Run of one
  disposable issue is listed by `git-loopy runs` by its explicit identity,
  announces that host in its trace, starts its work, accepts an independent
  `git-loopy attach <run-id>` that announces the helper fallback and detaches
  with the Run still going, acknowledges `git-loopy stop <run-id>` at drain and
  then at cancel, records its end, and leaves its work on a recoverable
  `git-loopy/<run>/issue-<N>` branch.

The deterministic offline matrix stays responsible for every fault scenario —
native Windows liveness, several clients, timeout, redelivery, temporary EOF,
terminal restoration. The smoke proves the composed path once per candidate and
replaces none of it. Its own admission, verdict, evidence and cleanup logic is
covered offline through the same entry point by `tests/test_release_smoke.py`.

**Authorization and limits are explicit.** Nothing is guessed and nothing is
unbounded; a missing value blocks the smoke before any service is touched.

| Setting | Flag | Environment | Workflow source |
| --- | --- | --- | --- |
| The one credential the smoke spends | — | `GIT_LOOPY_SMOKE_TOKEN` | secret `RELEASE_SMOKE_TOKEN` |
| Disposable sandbox repository | `--sandbox-repository` | `GIT_LOOPY_SMOKE_REPOSITORY` | variable `RELEASE_SMOKE_REPOSITORY` |
| Work limit (Runs started) | `--max-runs` | `GIT_LOOPY_SMOKE_MAX_RUNS` | variable `RELEASE_SMOKE_MAX_RUNS` |
| Time limit, seconds | `--deadline-seconds` | `GIT_LOOPY_SMOKE_DEADLINE_SECONDS` | variable `RELEASE_SMOKE_DEADLINE_SECONDS` |
| Spend limit, premium requests | `--spend-limit-premium-requests` | `GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS` | variable `RELEASE_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS` |

An ambient `GH_TOKEN`, `GITHUB_TOKEN` or stored `gh` login is never
authorization. The token needs repository contents, issues and Actions write on
the sandbox and Copilot requests for the Agent. The sandbox must be a
repository that carries the **`git-loopy-release-smoke`** topic, is not
archived, and is never `bradcstevens/git-loopy`: every smoke force-pushes the
candidate's tree to its default branch (so the GitHub Actions host dispatches
the candidate's own `lane-contribution.yml`) and opens its issues there, marked
with the smoke's `smoke-id`. Production issues are never smoke input.

A Run is admitted only while the work limit, the deadline and the known spend
all allow it. Spend is read from the Run's own billing records; spend that
cannot be proved admits nothing further. A Run still live at the deadline — or
a GitHub Actions contribution a stage-two Stop did not cancel — is in-flight
uncertainty, never a pass.

**Verdicts and exit codes.** The evidence is JSON
(`git-loopy.release-smoke/1`) naming the smoke id, the candidate's proof,
commit, tag and archive digest, the host platform and interpreter, the
Execution hosts, the limits and what the ledger spent, every observation, the
residue, and a content digest.

| Verdict | Exit | Meaning |
| --- | --- | --- |
| `passed` | 0 | every required observation was made on every requested host |
| `failed` | 1 | the candidate did the wrong thing, or is not the proved candidate |
| `blocked` | 2 | authorization, a limit, the sandbox or a service was missing, unmarked, exhausted or unavailable |
| `inconclusive` | 3 | an outcome could not be proved — a missing observation, an unprovable spend, a Run that ended too early |

`confirm_smoke_evidence` is what a Promotion reads it through: it refuses
evidence that did not pass, was edited after it was recorded, proves a
different candidate's proof, or skipped an Execution host — so evidence can
never be reused for changed content.

**Cleanup touches only what the smoke provably owns.** It closes the issues
that carry its own `smoke-id` and deletes the `git-loopy/<run>/…` branches of
Runs it started and saw end. Everything else — another smoke's issue, a branch
it cannot attribute, anything of a Run that may still be live — is left in
place and listed as residue in the evidence. A Run whose trace shows a
contribution that never ended, whose GitHub Actions dispatch is still running,
or that could not be identified, counts as possibly live. A workspace whose Run
may still be live is kept too. A smoke interrupted by `SIGTERM` or `SIGINT` is
`blocked`, and still reclaims and writes its evidence.

```sh
GIT_LOOPY_SMOKE_TOKEN=… GIT_LOOPY_SMOKE_REPOSITORY=owner/git-loopy-smoke \
uv run --project git-loopy/python --all-extras \
  python -m git_loopy.release_smoke \
  --publication-input "$RUNNER_TEMP/publication-input.json" \
  --workspace "$RUNNER_TEMP/smoke" \
  --evidence-output "$RUNNER_TEMP/release-smoke-evidence.json" \
  --max-runs 2 --deadline-seconds 3600 --spend-limit-premium-requests 20
```

`release-smoke.yml` runs the rehearsal and then this, by `workflow_dispatch`
(`candidate_commit` or `promote_milestone`) or as a reusable `workflow_call`,
and uploads the evidence whatever the verdict. It has no protected environment
and waits for no approval, so once the secret and variables are configured it
runs unattended. The workflow refuses a deadline above 6000 seconds, which leaves
the smoke step (110 minutes) time to reclaim its work. It is
source-only: it installs from the proved source archive and needs `uv`, `git`,
`gh` and the Copilot CLI on the host, none of which a published helper supplies.
It is not an Integration feedback loop — it reaches GitHub, Copilot and the
package index — and it tags and publishes nothing.

### Publishing a proved input, and retrying one

Publication is the separate act that makes a proved snapshot public, and
`git_loopy.release_publication.publish_release` is the whole of it. It takes the
**publication input** a rehearsal returned and nothing else: it never reads the
current head, and it never recomposes an identity the rehearsal already decided.
What it publishes is the *proved tag object itself*, fetched out of the rehearsal
workspace and pushed as-is, so "what was proved" and "what is public" cannot end
up being two different objects.

It is written to be run again. Every step is reconciled against what the remote
actually holds before anything is written, and read back afterwards:

| The remote already has | What publication does |
| --- | --- |
| nothing | pushes the proved tag, then creates the Release |
| the tag at the proved commit, no Release | creates only the Release — it never retags |
| the tag and a Release that agrees | nothing; a successful no-op |
| the tag at **another** commit | refuses; a public tag never moves |
| a Release that disagrees about marking, title, committed notes, or carries an asset | refuses, having written nothing |

The refusals are refusals, not repairs. A mismatching Release is never
overwritten and a tag is never replaced, because the absence of a Release object
is no evidence that nobody fetched the tag behind it. The published tag is also
never the only thing carrying its commit: a candidate the trunk does not already
contain is refused before the push.

A failed write is never read as "nothing happened". A rejected push and a
Release creation whose response was lost are both resolved by asking the remote
what is actually there — a state that agrees with the input is a success, one
that disagrees is a refusal, and a host that cannot answer is neither. That is
also what makes two Promotion runs racing on the same trunk safe: the loser
reconciles the winner's tag instead of forcing its own.

**Recovering a failed publication.** There are two moves, and replacing a public
tag is not one of them.

1. *The tag is public and correct.* Run the same publication input again. It
   resumes whatever is missing — usually the Release — and retags nothing. This
   is the normal recovery, including after a partial or interrupted run.
2. *The content was wrong.* Cut a **new Release version**. Fix the defect, let
   the Release line advance, and rehearse and publish the new candidate. The
   wrong version stays where it is.

Moving an already-public `v0.10.0` during its recovery is what
[ADR-0059](../adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)
was written about, and it is not precedent. Before a tag exists a candidate may
instead be repaired and rehearsed again — that is the whole point of rehearsing
first — but after it exists there is no third option.

`publish_release` deliberately has no command line of its own yet, and no
workflow calls it. ADR-0059 requires a stable publication to sit behind both the
full pre-tag proof *and* a bounded real-host smoke, and that smoke is not yet
composed into the Promotion; an entry point added before it is would be exactly
the shortcut the ADR refuses. Wiring the two together is the composed Promotion's job, and until then
`source-release.yml` remains what publishes the Release for a pushed tag.

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
| Prerelease (`vX.Y.Z-alpha.1`, `-beta.1`, `-rc.1`, …) | Checksum | Checksum — an **unsigned** Windows artifact is permitted here and nowhere else | Checksum |

A stable Release additionally requires a build-provenance attestation. Any
missing artifact, signature, notary verdict, publisher, checksum, or attestation
refuses the whole publication rather than shipping a partial set.

The version string decides which of those a Release *needs*; the prerelease flag
on the GitHub Release is what tells an operator -- and every package channel that
resolves "the stable Release" -- which channel they are installing from. Those
are two answers to one question, and the unsigned Windows allowance rests
entirely on the second, so publication reads the marking back off the Release it
is about to attach to and refuses to upload when the two disagree. `--prerelease`
is applied by `source-release.yml`, deliberately a separate workflow, and the
flag stays editable afterwards; the helper pipeline therefore proves it rather
than inheriting it.

Signing runs inside `dist build`, which is the only place it can: cargo-dist
writes each `.sha256` afterwards, so a published checksum is a checksum of the
signed artifact. A ticket cannot be stapled into a bare Mach-O -- stapling needs
a bundle, `.dmg`, or `.pkg` -- so Gatekeeper resolves the helper's notarization
online, and `release-trust.json` records that by name rather than leaving it to
look like an oversight.

### Downloadable baseline and completion proof

**No verified downloadable helper baseline is named yet.** On 2026-09-20,
public Release readback still showed no attached helper assets, including for
`v0.11.0-dev.4`. #592 remains open until an explicitly artifact-bearing
prerelease delivers the full set. A successful build or source Release is not
that baseline, and existing public tags and source-only promises must not be
rewritten to create one.

The supported set comes from
[`tui-artifacts.json`](../../git-loopy/conformance/tui-artifacts.json):

| Platform | Architectures | Archive |
| --- | --- | --- |
| macOS | arm64, x64 | `.tar.xz` |
| Windows | x64 | `.zip` |
| Linux glibc | arm64, x64 | `.tar.xz` |
| Linux musl | arm64, x64 | `.tar.xz` |

Every archive has its declared `.sha256` and `.trust.json` sidecars. Native
build runners execute their own helper to prove Release identity, Event-schema
compatibility, and a minimal Run. Cross-target verification proves archive
shape and metadata; it does not claim native execution.

The helper publication job now ends with `git_loopy.tui_release verify-published`.
It requires the tagged `artifact-bearing` policy and complete locally verified
build outputs, reads the public Release identity and prerelease marking, and
downloads all 21 promised files from the canonical URLs. Every downloaded byte
must match those build outputs, pass the existing checksum and trust gates,
and each archive must contain the declared helper. Stable readback also verifies
each archive's public attestation against the explicit repository. Unpromised
helper assets are refused. A final Release readback
rejects identity or asset changes during verification; download counters are
not identity.

Missing assets, failed downloads, changed bytes, or unavailable trust evidence
fail the publication job, keeping downstream package-channel jobs blocked.
Cancellation is incomplete publication, not permission to switch modes.
The readback command is read-only: it never moves a tag or repairs an asset.

Run it from the exact tagged checkout with the verified build outputs:

```sh
PYTHONPATH=git-loopy/python python -m git_loopy.tui_release verify-published \
  --repository-root . --artifact-dir release-artifacts \
  --tag-ref "v<version>" --distribution-mode artifact-bearing
```

Stable verification additionally needs `--attestation <build-bundle>` and
authenticated `gh attestation verify` access. It does not waive signing,
notarization, publisher identity, or any pre-publication proof.

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
