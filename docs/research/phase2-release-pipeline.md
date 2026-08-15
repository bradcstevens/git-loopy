<!--
  research: phase2-release-pipeline.md
  Branch: research/phase2-release-pipeline
  Closes: #142  (feeds: #139 — Phase 2 build-and-release-pipeline decision)
  Researched: 2026-07-19
-->

# Phase 2 Release Pipeline Research: Cross-Compiled, Signed, Checksummed Rust Binaries

**Status:** research complete — feeds issue #139  
**ADR reference:** [ADR-0013 §phase-2](../../adr/0013-multi-language-runner-family.md) — the
`git-loopy-tui` Rust+ratatui binary must ship as prebuilt, cross-compiled, code-signed,
checksummed binaries via GitHub Releases + Homebrew/winget/scoop + an `install.sh` /
`install.ps1` that downloads the checksum-verified binary into `.git-loopy/bin/`.

---

## 1. cargo-dist / `dist` (axodotdev) — Capabilities & Maintenance Verdict

### 1.1 Maintenance & Naming Status (the critical question)

| Concern | Status (as of 2026-07) |
|---------|------------------------|
| Archived? | **No.** Active. |
| Transferred? | **No.** Still `axodotdev/cargo-dist`. |
| Renamed? | Partially: the CLI binary is now also marketed as `dist` (not `cargo-dist`) but the crate name and repo are unchanged. Both names appear in docs. |
| Funding change? | No public funding change. axodotdev continues to develop it. |
| Latest release | **v0.32.0 — May 2026** ([axodotdev/cargo-dist releases](https://github.com/axodotdev/cargo-dist/releases)) |
| Release cadence | Consistent monthly-ish releases throughout 2024–2026; no dormancy. |
| License | Apache-2.0 / MIT — unchanged. |

**Verdict: cargo-dist is healthy and actively maintained.** The "rename" concern amounts to a branding layer (`dist` = new preferred CLI name); the repo, crate, and TOML key (`[workspace.metadata.dist]`) are unchanged. This is not a fork or maintainer handover.

### 1.2 Core Capabilities

cargo-dist is a *complete release pipeline generator* for Rust (and other) binaries. Running
`dist init` in a repo produces a `release.yml` that implements the full plan/build/host/publish/announce pipeline on GitHub Actions.

**Target matrix** ([dist config reference](https://axodotdev.github.io/cargo-dist/book/reference/config.html)):

| Target triple | Notes |
|---|---|
| `x86_64-apple-darwin` | Built on `macos-*` runner (native) |
| `aarch64-apple-darwin` | Built on `macos-*` runner (native, Apple Silicon) |
| `x86_64-unknown-linux-gnu` | Built on `ubuntu-*` runner (native) |
| `aarch64-unknown-linux-gnu` | Built via container/cross on x86 runner |
| `x86_64-unknown-linux-musl` | Built via container/cross on x86 runner |
| `aarch64-unknown-linux-musl` | Built via container/cross on x86 runner |
| `x86_64-pc-windows-msvc` | Built on `windows-*` runner (native) |

> `aarch64-pc-windows-msvc` is **not** in cargo-dist's supported target list as of v0.32.0.
> cargo-dist itself ships 7 targets (see
> [`dist-workspace.toml`](https://github.com/axodotdev/cargo-dist/blob/main/dist-workspace.toml))
> — confirming the set above.

**Generated installers** (configurable via `installers = [...]` in `dist.toml`):

- `shell` — `curl | sh` installer that detects OS/arch and downloads the right tarball
- `powershell` — `irm | iex` installer (equivalent, Windows)
- `homebrew` — Homebrew formula auto-pushed to a tap repo
- `msi` — Windows MSI installer (bundled, single-arch)
- `npm` — npm wrapper that fetches and re-exports the binary

**Checksums:** SHA-256 by default; SHA-512, SHA3-256/512, BLAKE2s/b also supported
([checksum config](https://axodotdev.github.io/cargo-dist/book/reference/config.html)).

**GitHub Attestations:** Supported since ~v0.20; enabled via `github-attestations = true`; uses
`actions/attest-build-provenance`; verifiable with `gh attestation verify`.

**SBOM & audit:** Optional `cargo-cyclonedx` (CycloneDX SBOM) and `cargo-auditable`
(embedded dependency tree) integrations available
([supply chain docs](https://axodotdev.github.io/cargo-dist/book/supplychain-security/index.html)).

### 1.3 Code Signing Support

#### Windows (built-in since v0.15.0)
cargo-dist has native integration with **SSL.com eSigner** cloud signing. Setup:
1. Purchase an EV Code Signing certificate from SSL.com (~$300–400/year for OV, more for EV).
2. Enroll in eSigner cloud signing; save TOTP secret.
3. Add 4 GitHub Secrets (`SSLDOTCOM_USERNAME`, `SSLDOTCOM_PASSWORD`, `SSLDOTCOM_TOTP_SECRET`,
   `SSLDOTCOM_CREDENTIAL_ID`).
4. Add `ssldotcom-windows-sign = "prod"` to `dist.toml`.

The generated workflow will codesign all Windows EXEs and MSIs automatically.
([Windows signing guide](https://axodotdev.github.io/cargo-dist/book/supplychain-security/signing/windows.html))

> **Note:** As of the CA/Browser Forum rule change effective **February 2025**, all
> Authenticode signing keys *must* be HSM-backed. Software key storage (storing a `.pfx` in
> a GitHub Secret and calling `signtool.exe` directly) is **no longer valid** for new/renewed
> certs. Cloud HSM signing services (SSL.com eSigner, Azure Artifact Signing, SignPath) are
> now the only practical CI path.

#### macOS (no built-in support as of v0.32.0)
cargo-dist does **not** have built-in macOS notarization. The page
`/book/supplychain-security/signing/macos.html` returns 404. macOS signing and notarization
must be done as a custom build step in CI. See §4 below.

### 1.4 Limitations

- **No `aarch64-pc-windows-msvc`** target support in the generated matrix (Windows ARM64).
- **No macOS notarization** out of the box.
- **SSL.com-only** for built-in Windows signing (Azure Trusted Signing and SignPath require
  manual CI steps or `allow-dirty = ["ci"]` customization).
- **ARM64 Linux cross-compilation** relies on custom containers
  (`quay.io/pypa/manylinux_2_28_x86_64`) rather than native ARM runners — works but adds
  build time.
- cargo-dist re-generates `release.yml` on `dist init`; manual edits to the generated file
  require `allow-dirty = ["ci"]` or will be overwritten.
- Homebrew publishing requires a dedicated tap repo with a PAT (`HOMEBREW_TAP_TOKEN`).

---

## 2. `cross` (cross-rs/cross) — Role Alongside cargo-dist

### 2.1 What cross does
`cross` is a `cargo build`-compatible wrapper that transparently pulls Docker images for the
target triple and runs the compilation inside the container. This solves two hard problems:
1. Building Linux musl (statically linked) binaries from a glibc host.
2. Building Linux ARM64/ARM32 binaries from an x86_64 host without native ARM runners.

**macOS cross-compilation is explicitly not supported** — Apple does not license macOS Docker
images, so there is no macOS container to run inside. macOS targets always require native runners.

### 2.2 Target coverage relevant to git-loopy-tui

| Target | cross needed? | Notes |
|---|---|---|
| `x86_64-apple-darwin` | No | Native `macos-*` runner |
| `aarch64-apple-darwin` | No | Native `macos-15` (Apple Silicon) runner or cross-compiled via `--target aarch64-apple-darwin` on the same runner |
| `x86_64-unknown-linux-gnu` | No | Native `ubuntu-*` runner |
| `aarch64-unknown-linux-gnu` | **Sometimes** | GitHub now offers `ubuntu-22.04-arm` native runners; cross still works |
| `x86_64-unknown-linux-musl` | **Yes** | Needs musl-tools; cross provides a clean environment |
| `aarch64-unknown-linux-musl` | **Yes** | No native runner; cross Docker image handles it |
| `x86_64-pc-windows-msvc` | No | Native `windows-*` runner |
| `aarch64-pc-windows-msvc` | No | Native `windows-11-arm` runner (GitHub now provides these) |

### 2.3 How it composes with cargo-dist

cargo-dist can trigger cross via `[dist.github-custom-runners.TARGET.container]` blocks in
`dist-workspace.toml`. Atuin (the best reference implementation) uses:

```toml
[dist.github-custom-runners.aarch64-unknown-linux-gnu.container]
image = "quay.io/pypa/manylinux_2_28_x86_64"
host = "x86_64-unknown-linux-musl"

[dist.github-custom-runners.aarch64-unknown-linux-musl.container]
image = "quay.io/pypa/manylinux_2_28_x86_64"
host = "x86_64-unknown-linux-musl"
```

([atuin dist-workspace.toml via the generated release.yml](https://github.com/atuinsh/atuin/blob/main/.github/workflows/release.yml))

Alternatively, `bottom` uses a custom action wrapper (`ClementTsang/cargo-action`) that calls
`cross` directly with a pinned `CROSS_VERSION` SHA for reproducibility.

**Recommendation for git-loopy-tui:** Use cargo-dist's container-based cross-compilation for
musl targets rather than invoking cross manually. For the Phase 2 first-cut target matrix
(§6), this means adding the two `github-custom-runners` container blocks to `dist-workspace.toml`.

---

## 3. Signing in CI on GitHub Actions

### 3.1 macOS: Developer ID + notarytool

**Requirements:**
- Apple Developer Program membership ($99/year)
- Developer ID Application certificate (generated in developer.apple.com)
- App Store Connect API key for notarytool authentication (preferred over app-specific passwords)

**Secrets needed in GitHub:**
| Secret | Value |
|---|---|
| `MACOS_CERT_P12_BASE64` | `base64 < DeveloperID.p12` |
| `MACOS_CERT_PASSWORD` | P12 export password |
| `MACOS_SIGN_IDENTITY` | e.g. `Developer ID Application: Acme Corp (TEAMID)` |
| `NOTARYTOOL_KEY_ID` | App Store Connect API key ID |
| `NOTARYTOOL_ISSUER_ID` | Issuer UUID |
| `NOTARYTOOL_KEY_P8_BASE64` | `base64 < AuthKey_KEYID.p8` |

**CI steps (must run on a `macos-*` runner):**

```yaml
- name: Import Developer ID certificate
  env:
    P12: ${{ secrets.MACOS_CERT_P12_BASE64 }}
    P12_PWD: ${{ secrets.MACOS_CERT_PASSWORD }}
  run: |
    echo "$P12" | base64 --decode > /tmp/cert.p12
    security create-keychain -p "" build.keychain
    security default-keychain -s build.keychain
    security unlock-keychain -p "" build.keychain
    security import /tmp/cert.p12 -k build.keychain -P "$P12_PWD" -T /usr/bin/codesign
    security set-key-partition-list -S apple-tool:,apple: -s -k "" build.keychain

- name: Sign binary
  env:
    SIGN_ID: ${{ secrets.MACOS_SIGN_IDENTITY }}
  run: |
    codesign --verbose --force --timestamp --options runtime \
      --sign "$SIGN_ID" target/release/git-loopy-tui

- name: Notarize
  env:
    KEY_ID: ${{ secrets.NOTARYTOOL_KEY_ID }}
    ISSUER: ${{ secrets.NOTARYTOOL_ISSUER_ID }}
    KEY_P8: ${{ secrets.NOTARYTOOL_KEY_P8_BASE64 }}
  run: |
    echo "$KEY_P8" | base64 --decode > /tmp/authkey.p8
    zip -r notarize.zip target/release/git-loopy-tui
    xcrun notarytool submit notarize.zip \
      --key /tmp/authkey.p8 --key-id "$KEY_ID" --issuer "$ISSUER" \
      --wait
    xcrun stapler staple target/release/git-loopy-tui || true  # staple to binary if zip
```

**Important notes:**
- `--options runtime` is required for notarization (hardened runtime).
- `stapler` works on app bundles and disk images; for bare binaries you can staple the `.zip`
  that was submitted, or simply rely on Gatekeeper's online check (which works without stapling
  for direct downloads).
- notarytool requires Xcode 14+ (available on all current GitHub-hosted macOS runners).
- Apple Developer Program costs $99/year (individual) or $299/year (organization) — covers both
  Developer ID signing and notarization.

**Reference:** [Apple notarizing macOS software docs](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

### 3.2 Windows: Authenticode — Three Practical Paths

As of **February 2025**, the CA/Browser Forum requires all code signing private keys to be
HSM-backed. Software key storage in GitHub Secrets is no longer valid for new/renewed certs.

#### Option A: Azure Artifact Signing (formerly Trusted Signing) — **recommended for git-loopy**

| Property | Value |
|---|---|
| Cost | **$9.99/month** (Basic, 5,000 signatures/month) |
| Key storage | Cloud HSM managed by Azure (FIPS 140-2 Level 2+) |
| CI integration | Official GitHub Action: `Azure/artifact-signing-action` |
| Identity validation | Individual or org — validated by Microsoft |
| Smartscreen | Builds SmartScreen reputation gradually (not instant) |
| Availability | US, EU, UK, Canada |

```yaml
- name: Sign Windows binary
  uses: azure/artifact-signing-action@v0
  with:
    azure-tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    azure-client-id: ${{ secrets.AZURE_CLIENT_ID }}
    azure-client-secret: ${{ secrets.AZURE_CLIENT_SECRET }}
    endpoint: https://eus.codesigning.azure.net/
    certificate-profile-name: ${{ vars.AZURE_CERT_PROFILE }}
    files-folder: target/x86_64-pc-windows-msvc/release/
    files-folder-filter: exe
```

References:
- [Azure Artifact Signing pricing](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/)
- [Melatonin guide: Azure Artifact Signing in GitHub Actions](https://melatonin.dev/blog/code-signing-on-windows-with-azure-trusted-signing/)
- [Scott Hanselman's guide](https://www.hanselman.com/blog/automatically-signing-a-windows-exe-with-azure-trusted-signing-dotnet-sign-and-github-actions)

#### Option B: SSL.com eSigner (cargo-dist native)

cargo-dist has built-in support via `ssldotcom-windows-sign = "prod"`. SSL.com EV OV cert is
~$300–400/year, requires TOTP-based cloud HSM. Simplest path if already using cargo-dist
end-to-end and want zero custom YAML.

#### Option C: SignPath (OSS-friendly)

| Property | Value |
|---|---|
| Open source | **Free** via SignPath Foundation (requires application) |
| Commercial | $500–2,000/year (Starter–Basic Team) |
| CI integration | `signpath/github-action-submit-signing-request` |
| Key storage | Managed HSM |
| Smartscreen | OV/EV cert — established reputation |

`bottom` (ClementTsang/bottom) uses SignPath for its Windows signing:
```yaml
- uses: signpath/github-action-submit-signing-request@b9d91eadd323de506c0c81cf0c7fe7438f3360fd
  with:
    api-token: "${{ secrets.SIGNPATH_API_TOKEN }}"
    organization-id: "06b1a1ff-74e1-4d9d-93b1-fa8180c67727"
    project-slug: "bottom"
    signing-policy-slug: "${{ steps.get-signing-slug.outputs.slug }}"
```
([bottom build_releases.yml](https://github.com/ClementTsang/bottom/blob/main/.github/workflows/build_releases.yml))

For an OSS project like git-loopy, SignPath Foundation (free) or Azure ($9.99/mo) are the two
most attractive options. **Recommendation: Azure Artifact Signing** for simplicity and lower
ongoing cost.

#### Signing-not-yet justification
For Phase 2 first-cut (see §6), it's reasonable to **ship unsigned but checksummed** for an
initial alpha/beta, then add signing in a follow-up PR. SmartScreen reputation takes time to
build regardless. The `install.ps1` bypasses SmartScreen (no Mark-of-the-Web), so most
git-loopy users (who will use the installer) won't hit the warning anyway.

---

## 4. Comparable Projects — Release Pipeline References

### 4.1 atuin (atuinsh/atuin) — **cargo-dist canonical reference**

atuin uses **cargo-dist v0.31.0** verbatim with no customization of the generated workflow.
This is the closest match to the git-loopy-tui use case: a Rust binary, distributed to all
major platforms via GitHub Releases + shell/PowerShell installers.

Key config excerpt from
[`atuinsh/atuin` dist-workspace.toml](https://github.com/atuinsh/atuin/blob/main/Cargo.toml)
(embedded in Cargo.toml):

```toml
[workspace.metadata.dist]
cargo-dist-version = "0.31.0"
ci = "github"
installers = ["shell", "powershell", "homebrew", "msi"]
targets = [
  "aarch64-apple-darwin",
  "aarch64-unknown-linux-gnu",
  "aarch64-unknown-linux-musl",
  "x86_64-apple-darwin",
  "x86_64-unknown-linux-gnu",
  "x86_64-unknown-linux-musl",
  "x86_64-pc-windows-msvc",
]
github-attestations = true
```

Generated workflow:
[`.github/workflows/release.yml`](https://github.com/atuinsh/atuin/blob/main/.github/workflows/release.yml)
— autogenerated by dist, header confirms:
```
# This file was autogenerated by dist: https://axodotdev.github.io/cargo-dist
# Copyright 2022-2024, axodotdev
```

Pipeline stages: `plan` → `build-local-artifacts` (matrix per target) → `build-global-artifacts`
(checksums + installers) → `host` → `announce` (GitHub Release).

GitHub Attestations are emitted via `actions/attest-build-provenance@v3` inside
`build-local-artifacts`.

### 4.2 bottom (ClementTsang/bottom) — **SignPath + cross reference**

bottom ships the most complete release matrix of any comparable TUI project: 16+ targets
including musl, ARM, PowerPC, RISC-V, Android, FreeBSD, NetBSD. Key aspects:

- **`cross` usage:** All musl and exotic targets use cross via `ClementTsang/cargo-action`.
  Pinned to a specific cross commit SHA for reproducibility:
  `CROSS_VERSION: "git:588b3c99db52b5a9c5906fab96cfadcf1bde7863"`
- **Windows signing:** SignPath via `signpath/github-action-submit-signing-request`.
- **Artifact attestation:** `actions/attest-build-provenance@v3`.
- **Packages:** `.deb`, `.rpm` (Linux), `.msi` (Windows), `.zip` (Windows), `.tar.gz` (all).

Workflow:
[`.github/workflows/build_releases.yml`](https://github.com/ClementTsang/bottom/blob/main/.github/workflows/build_releases.yml)

### 4.3 yazi (sxyazi/yazi) — **minimal hand-rolled, no signing**

yazi uses a hand-rolled matrix with no signing. Key aspects:
- Native runners for macOS (x86_64 and aarch64-apple-darwin) and Windows
  (x86_64-pc-windows-msvc, aarch64-pc-windows-msvc).
- Uses `cross-rs` Docker containers directly (`ghcr.io/cross-rs/$TARGET:edge`) for musl builds
  via a `container:` key on the job.
- No code signing, no checksums beyond GitHub's built-in artifact hashing.
- Releases via `softprops/action-gh-release@v3` with draft mode.
- Also publishes to Snapcraft (snap store) for Linux.

Workflow:
[`.github/workflows/draft.yml`](https://github.com/sxyazi/yazi/blob/main/.github/workflows/draft.yml)

### 4.4 gitui (gitui-org/gitui) — **hand-rolled, Homebrew bump**

gitui uses a custom matrix across 4 runners (ubuntu-latest, macos-latest, windows-latest,
ubuntu-22.04 for ARM). Key aspects:
- ARM toolchains fetched manually from ARM's developer downloads.
- macOS builds both arm64 (native) and x86_64 (via `rustup target add x86_64-apple-darwin`).
- Linux: MUSL via `rustup target add x86_64-unknown-linux-musl` + `musl-tools`.
- Windows: Release `.msi` + `.zip`.
- Homebrew via `mislav/bump-homebrew-formula-action@v3`.
- **No signing** of any kind.

Workflow:
[`.github/workflows/cd.yml`](https://github.com/gitui-org/gitui/blob/main/.github/workflows/cd.yml)

---

## 5. Recommended Tooling for git-loopy-tui

### Primary: **cargo-dist** (with manual macOS notarization step)

**Why:**
1. atuin proves the exact pattern works for a popular Rust CLI — 7-target matrix, shell +
   PowerShell + Homebrew installers, checksums, GitHub Attestations, all generated from a
   ~30-line `dist.toml` config.
2. cargo-dist generates the `install.sh` and `install.ps1` that ADR-0013 requires, with
   checksum verification built in.
3. Homebrew and winget/scoop support is built-in (Homebrew via formula push; winget via the
   MSI installer pathway).
4. The tool is healthy and actively maintained (v0.32.0, May 2026).
5. Adding new targets later is a one-line `dist.toml` change.

**Gaps to fill manually:**
- **macOS notarization:** Add a post-build step on the macOS job to `codesign` + `notarytool`.
  This requires `allow-dirty = ["ci"]` to prevent cargo-dist from overwriting the customized
  `release.yml`, or use cargo-dist's `custom-jobs` / `post-build` hooks.
- **Windows signing:** Use `ssldotcom-windows-sign = "prod"` (SSL.com) OR add an Azure Artifact
  Signing step post-build. For a new OSS project, deferring signing to a follow-up PR is
  reasonable.

### Secondary: **cross** (used via cargo-dist containers, not standalone)

Use cargo-dist's `github-custom-runners.TARGET.container` blocks to handle musl targets;
don't invoke cross directly in the workflow YAML. This keeps the pipeline idiomatic and
prevents version drift.

---

## 6. Target Matrix Recommendation

### Phase 2 first-cut (ship these on day 1)

| Target | Runner | Method | Priority |
|---|---|---|---|
| `aarch64-apple-darwin` | `macos-15` | native | P0 — primary macOS |
| `x86_64-apple-darwin` | `macos-15` (cross-target) | `--target x86_64-apple-darwin` | P0 — Intel Mac |
| `x86_64-pc-windows-msvc` | `windows-2022` | native | P0 — primary Windows |
| `x86_64-unknown-linux-gnu` | `ubuntu-22.04` | native | P0 — primary Linux |
| `aarch64-unknown-linux-gnu` | container | cargo-dist container | P1 — Linux ARM64 (Pi, Ampere) |
| `x86_64-unknown-linux-musl` | container | cargo-dist container | P1 — static Linux binary |
| `aarch64-unknown-linux-musl` | container | cargo-dist container | P2 — static ARM Linux |

### Later (add after first-cut stabilizes)

| Target | Reason to defer |
|---|---|
| `aarch64-pc-windows-msvc` | Not yet in cargo-dist's matrix; requires `windows-11-arm` runner; small install base |
| `armv7-unknown-linux-gnueabihf` | Niche; cross only; add if user demand materializes |
| `x86_64-unknown-freebsd` | FreeBSD; cross only; needs explicit request |

### Installer matrix

| Installer | Config key | Notes |
|---|---|---|
| Shell (`install.sh`) | `"shell"` | Required by ADR-0013 |
| PowerShell (`install.ps1`) | `"powershell"` | Required by ADR-0013 |
| Homebrew formula | `"homebrew"` | Needs a `bradcstevens/homebrew-git-loopy` tap repo |
| MSI | `"msi"` | Windows native installer; enables winget via MSI hash |

Scoop: cargo-dist doesn't generate a scoop bucket manifest natively. Options:
(a) use `softprops/action-gh-release` + a hand-maintained scoop bucket, or
(b) use the community action `lukesampson/scoop-autoupdate`.

---

## 7. Proposed `dist-workspace.toml` Skeleton

```toml
[workspace]
members = ["cargo:."]

[dist]
cargo-dist-version     = "0.32.0"
ci                     = "github"
installers             = ["shell", "powershell", "homebrew", "msi"]
tap                    = "bradcstevens/homebrew-git-loopy"
targets = [
  "aarch64-apple-darwin",
  "x86_64-apple-darwin",
  "x86_64-pc-windows-msvc",
  "x86_64-unknown-linux-gnu",
  "aarch64-unknown-linux-gnu",
  "x86_64-unknown-linux-musl",
  "aarch64-unknown-linux-musl",
]
checksum               = "sha256"
github-attestations    = true
pr-run-mode            = "plan"
hosting                = "github"
install-updater        = false

# Cross-compilation containers for musl/ARM Linux
[dist.github-custom-runners.aarch64-unknown-linux-gnu.container]
image  = "quay.io/pypa/manylinux_2_28_x86_64"
host   = "x86_64-unknown-linux-musl"

[dist.github-custom-runners.aarch64-unknown-linux-musl.container]
image  = "quay.io/pypa/manylinux_2_28_x86_64"
host   = "x86_64-unknown-linux-musl"

[dist.github-custom-runners.x86_64-unknown-linux-musl.container]
image  = "quay.io/pypa/manylinux_2_28_x86_64"
host   = "x86_64-unknown-linux-musl"

# Windows signing (SSL.com eSigner — or remove and use Azure separately)
# ssldotcom-windows-sign = "prod"
```

---

## 8. Key Source Citations

| Source | URL |
|---|---|
| cargo-dist releases | https://github.com/axodotdev/cargo-dist/releases |
| cargo-dist dist-workspace.toml (self-hosted) | https://github.com/axodotdev/cargo-dist/blob/main/dist-workspace.toml |
| cargo-dist installers reference | https://axodotdev.github.io/cargo-dist/book/installers/index.html |
| cargo-dist Windows signing | https://axodotdev.github.io/cargo-dist/book/supplychain-security/signing/windows.html |
| cargo-dist supply chain | https://axodotdev.github.io/cargo-dist/book/supplychain-security/index.html |
| cargo-dist config reference | https://axodotdev.github.io/cargo-dist/book/reference/config.html |
| cross-rs/cross | https://github.com/cross-rs/cross |
| Apple notarization docs | https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution |
| Azure Artifact Signing pricing | https://azure.microsoft.com/en-us/pricing/details/artifact-signing/ |
| Azure signing GitHub Action | https://github.com/Azure/artifact-signing-action |
| Melatonin Azure Trusted Signing guide | https://melatonin.dev/blog/code-signing-on-windows-with-azure-trusted-signing/ |
| SignPath GitHub Action | https://github.com/SignPath/github-action-submit-signing-request |
| SignPath Foundation (OSS) | https://signpath.org |
| atuin release.yml | https://github.com/atuinsh/atuin/blob/main/.github/workflows/release.yml |
| bottom build_releases.yml | https://github.com/ClementTsang/bottom/blob/main/.github/workflows/build_releases.yml |
| gitui cd.yml | https://github.com/gitui-org/gitui/blob/main/.github/workflows/cd.yml |
| yazi draft.yml | https://github.com/sxyazi/yazi/blob/main/.github/workflows/draft.yml |
