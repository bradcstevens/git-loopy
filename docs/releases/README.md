# Source Release notes

Each source Release commit includes edited notes at `docs/releases/v<VERSION>.md`.
The matching annotated `v<VERSION>` tag publishes those notes verbatim as the
GitHub Release description after the full Runner-family Conformance gate and
tagged source archive identity checks pass.

The source-only path relies on GitHub's automatic source archives. It does not
publish package-channel metadata, signed platform artifacts, or a TUI helper.

## Platform trust for helper artifacts

The `git-loopy-tui` helper Release (`.github/workflows/tui-release.yml`) is
gated on what each artifact can *prove* about itself, not on a signing step
having run without error. Both signers in the pinned cargo-dist degrade to a
warning when their credentials are absent, so every gate here reads the artifact
rather than the pipeline.

| Channel | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Stable (`vX.Y.Z`) | Developer ID signature, hardened runtime, accepted notary verdict, checksum | Hardware-backed signature, readable publisher, checksum | Checksum |
| Prerelease (`vX.Y.Z-rc.1`, `-dev.0`, …) | Checksum | Checksum — an **unsigned** Windows artifact is permitted here and nowhere else | Checksum |

A stable Release additionally requires a build-provenance attestation. Any
missing artifact, signature, notary verdict, publisher, checksum, or attestation
refuses the whole publication rather than shipping a partial set.

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
