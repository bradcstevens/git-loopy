# Multi-language runner family: shell + PowerShell (+ future Rust) ports

**Status:** accepted
**Supersedes:** [ADR-0002](0002-retire-bash-runner.md) (retire the bash runner) and
[ADR-0007](0007-single-python-entrypoint-retire-afk-sh.md) (single Python entrypoint)

## Context

ADR-0002 retired the second (bash) runner and made the Python runner the sole AFK runner;
ADR-0007 then deleted even the thin `afk.sh` launcher so the Python CLI was the single
canonical entrypoint. Both decisions were correct for their moment, and both rested on the same
three premises:

1. **Drift risk** — two hand-parallel implementations of the wrapper contract, policed only by a
   single cross-runner parity test.
2. **Doc fan-out** — every doc carried "pick a runner / bash vs Python" framing, doubling edit
   cost.
3. **Single audience** — the bash runner's only pitch was a minimal dependency footprint, and
   "in practice this kit's operators have `uv` available."

Premise 3 no longer holds for the audience this kit is now aimed at. The kit is being shared with
a broad set of users and customers — many of them **Windows-only**, some on **WSL**, and a
meaningful fraction **not comfortable installing Python, creating virtual environments, or
operating `uv`**. ADR-0002's own consequences section named exactly these people: *"Operators who
cannot install `uv` lose the zero-Python option."* The goal now is the inverse of ADR-0002's:
make the loop **accessible across Windows, Linux, and macOS and across skill levels**, letting an
operator run autonomous loops in the language they are already comfortable with, with the least
possible setup friction.

Reversing ADR-0002 naively would resurrect premises 1 and 2. This ADR reverses the *conclusion*
(one runner) while **structurally neutralising the two objections that justified it**, so the
family is maintainable rather than a repeat of the mistake ADR-0002 documented.

## Decision

Ship git-loopy as a **runner family**: several interchangeable **Orchestrators**, each
implementing one shared **Wrapper contract** in a different host language, all driving one shared
**TUI helper** and emitting one shared **Event schema**.

- **Family members.** The existing **Python** reference runner, plus new **shell** (bash) and
  **PowerShell** ports, plus a **Rust** port planned later. An operator picks the member that
  matches their OS and comfort; all behave identically.
- **Orchestrator + TUI-helper split.** Each port is a native-language *Orchestrator* (loop logic
  and `gh` / `git` / `copilot` plumbing: collection, discrimination, run, auto-close, strikes,
  Checkpoint, push, config, OTel). The live interface is **not** reimplemented per language:
  a single **TUI helper** — one **Rust + ratatui** codebase — compiles to the standalone
  `git-loopy-tui` binary that the shell and PowerShell Orchestrators launch and feed, and is
  **embedded in-process** by the future Rust port. The Python runner keeps its existing Textual
  renderer.
- **Full parity is the destination, delivered in phases.** The ports target full behavioural and
  feature parity with the Python runner (loop contract, live TUI, OpenTelemetry, parallel mode,
  config wizard/subcommands, cost estimation, model picker), sequenced value-first:
  1. **Orchestrator core** — the Wrapper contract with plain streaming output, emitting the shared
     Event schema; ships with the Conformance suite + CI and per-port READMEs. A complete,
     working autonomous loop on all three OSes on its own.
  2. **Shared TUI helper + distribution** — the `git-loopy-tui` Rust binary rendering the Event
     schema; both Orchestrators drive it; prebuilt binaries via GitHub Releases + Homebrew +
     `winget`/`scoop`, code-signed.
  3. **Config parity** — `config.toml` precedence chain, `init` wizard, `config get/set/list/
     path/edit`, model picker, cost estimation.
  4. **OpenTelemetry parity** — OTLP emission from the Orchestrators.
  5. **Parallel mode** — git-worktree Lanes/Waves/Integration/auto-resolution.
- **Anti-drift backbone (the answer to ADR-0002 premise 1).** The contract is a **single source
  of truth** the whole family consumes, not N hand-parallel reimplementations:
  - one **`PROMPT.md`** (already byte-identical copies) referenced by all;
  - one **Event schema** — the `git_loopy.events` JSONL vocabulary — emitted by every
    Orchestrator and consumed by the TUI helper and the replay log;
  - one language-neutral **Conformance suite** (`git-loopy/conformance/`) — golden fixtures for
    the discriminator, the close-keyword regex, progress/strike accounting, and the exit-code
    table — that **every** Orchestrator runs in CI and must pass; the generalized successor to
    the cross-runner parity test ADR-0002 deleted;
  - one **versioned written contract**, `docs/wrapper-contract.md`.
- **Single-sourced docs (the answer to ADR-0002 premise 2).** Per-port READMEs are **hybrid**:
  each is a self-contained OS-specific *quickstart* (prereqs, install, skills onboarding, a
  copy-paste run example, an env-var cheat-sheet, exit codes) so a non-technical, single-OS user
  gets running from one file, but the authoritative contract, per-iteration flow, and skill
  routing live **once** in `docs/` and are linked, not fanned per runner.
- **Layout & naming.** Everything runner-related stays under `git-loopy/`:
  `git-loopy/{python,shell,powershell,tui,conformance}/`, entry points `git-loopy.sh` /
  `git-loopy.ps1`, binary `git-loopy-tui` (hyphenated per the `git-loopy` brand rule).
- **Runtime floors.** bash **4+** (macOS operators `brew install bash`; the code may use
  associative arrays), PowerShell **7+** (`pwsh`; modern and cross-platform, so the PowerShell
  port also runs on Linux/macOS), TUI in **Rust + ratatui**. `jq` returns as a documented
  *shell-port* prerequisite (PowerShell uses built-in `ConvertFrom-Json`).
- **Distribution.** Run-in-place from the clone is the baseline; an optional `install.sh` /
  `install.ps1` adds `git-loopy` to `PATH` and (from phase 2) downloads the checksum-verified
  prebuilt `git-loopy-tui` for the detected OS/arch into `.git-loopy/bin/`. Package-manager
  distribution is a phase-2 deliverable.

## Considered options

- **Keep a single Python runner (status quo, ADR-0002/0007)** — rejected: it excludes the
  Windows-only / no-Python audience this kit now targets. The whole motivation is reach.
- **Tier B: port only the wrapper contract, skip the TUI/OTel/parallel-mode "chrome"** — a plain
  but complete loop on every OS with no live TUI. Rejected by explicit choice: the ports should
  be full peers, not a lesser tier.
- **Reproduce the live interface natively per language** (hand-rolled ANSI in bash; a
  `PwshSpectreConsole` / `Terminal.Gui` module in PowerShell) — rejected: it recreates the exact
  drift ADR-0002 warned about, now in the UI layer, and a full reactive drill-in dashboard in
  pure bash is a famously fragile ~1–3k-line undertaking. A **single shared TUI helper** is the
  only way to get full-fidelity parity without a TUI-per-language maintenance burden.
- **Go + Bubble Tea for the TUI helper** — strong on cross-compilation, and the first choice until
  the future **Rust orchestrator port** was factored in. Rejected in favour of **Rust + ratatui**
  so the compiled surface is one language: the Rust port embeds the same crate in-process (a
  single self-contained binary with a built-in TUI) while that crate also compiles to the
  standalone binary the shell/PowerShell ports drive. The cost — fiddlier cross-compilation — is
  handled by `cargo-dist` / `cross`.
- **Fully self-contained per-port READMEs** — rejected: maximal accessibility but resurrects
  doc fan-out (the contract living in four places). The hybrid model keeps the load-bearing
  contract single-sourced.
- **PowerShell 5.1 floor (in-box on Windows, zero install)** — rejected as the floor in favour of
  7+: 5.1 is frozen/legacy and Windows-only, and 7 is one `winget install` while also making the
  PowerShell port cross-platform. 5.1 is noted only as a fallback if in-box-zero-install ever
  becomes non-negotiable.

## Consequences

- The single-runner decisions in ADR-0002 and ADR-0007 are reversed; the family returns. Both
  those ADRs are marked **superseded by ADR-0013**.
- The zero-Python audience regains a first-class path — on **three** operating systems, natively.
- Maintenance cost rises, but is bounded by the backbone: a wrapper-contract change lands as one
  Conformance-suite fixture plus one edit per Orchestrator, and CI fails any port that drifts.
  The cross-runner parity test ADR-0002 deleted returns generalized to N implementations.
- `jq` is again a prerequisite — but only for the shell port, not the kit as a whole.
- The kit takes on a **compiled component** (`git-loopy-tui`) and therefore a build/release
  pipeline (cross-compiled, signed, checksummed binaries). End users still compile nothing; they
  fetch a prebuilt binary (or use a package manager). Purists will note the shell/PowerShell ports
  depend on a Rust binary for their live interface — an accepted trade for full-fidelity TUI
  parity without three hand-rolled TUIs.
- Docs shift from "single Python runner" to a **runner family** framing with one comparison table;
  `docs/runners.md`, `docs/workflow.md`, `docs/customization.md`, and the root `README.md` are
  updated once, and `docs/wrapper-contract.md` becomes the versioned spec the family implements.
- This ADR records the **decision and design**; the ports are delivered by the phased roadmap
  above as follow-up work. Until a phase lands, the **Python runner remains the reference
  implementation** and the only runnable member of the family.
