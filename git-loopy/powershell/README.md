# `git-loopy` PowerShell Orchestrator

`git-loopy/powershell/` is the **PowerShell** member of the git-loopy
[Runner family](../../docs/runners.md) — a **shippable phase-1 Orchestrator**
that implements the same language-neutral
[**Wrapper contract**](../../docs/wrapper-contract.md) as the Python reference
runner. It collects `ready-for-agent` issues, feeds one `copilot --yolo -p` turn
per **Iteration**, auto-closes finished issues, and keeps your work durable with
a **Checkpoint** and push — on **Windows, Linux, and macOS**.

This is a self-contained quickstart. The behaviour every Orchestrator shares —
the [Wrapper contract](../../docs/wrapper-contract.md), the
[per-Iteration flow, and skill routing](../../docs/runners.md#per-iteration-flow)
— lives once in `docs/` and is linked here, not copied.

> **Phase-1 scope.** This port runs the complete autonomous loop with plain
> streamed output, and supervises the shared `git-loopy-tui` when one is
> installed. `config.toml` + `init`, OpenTelemetry, and Parallel mode are
> later-phase work tracked in
> [ADR-0013](../../docs/adr/0013-multi-language-runner-family.md); see
> [Runner family](../../docs/runners.md) for the roadmap.

---

## Prerequisites

| Requirement | Notes |
| --- | --- |
| **PowerShell 7+** (`pwsh`) | On **Windows, Linux, or macOS**. Run `pwsh --version` to check. This port needs **no `jq`** — it uses PowerShell's built-in `ConvertFrom-Json`. |
| **`gh`**, authenticated | `gh auth login`. The default issue source is GitHub Issues. |
| **`git`** | On `PATH`. |
| **`copilot`** | GitHub Copilot CLI, signed in: `npm install -g @github/copilot`, then run `copilot` once. |

### Windows PowerShell 5.1 is not enough

The Windows-in-box **Windows PowerShell 5.1** is frozen and will not run this
Orchestrator. Install **PowerShell 7+** (which is also what makes this port
cross-platform) and use `pwsh`, not `powershell`:

```powershell
winget install --id Microsoft.PowerShell    # Windows
# macOS:  brew install --cask powershell
# Linux:  https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux
pwsh --version                                # 7.x
```

The launcher refuses to run under anything below 7 with an upgrade hint.

The full prerequisite walk-through, including installing and configuring the
Copilot skills, is in [`docs/skills-setup.md`](../../docs/skills-setup.md).

---

## Skills onboarding

git-loopy runs on top of a configured issue tracker. Before your first Run, do
the one-time setup from [`docs/skills-setup.md`](../../docs/skills-setup.md):

1. **Install the skills** at the user level (copy `.copilot/skills/*` into
   `~/.copilot/skills/`).
2. **Configure this repo** by running `/setup-agent-skills` inside `copilot`,
   which writes `docs/agents/issue-tracker.md`.

That file is the Orchestrator's **preflight signal** — without it the Run exits
`1` and points you back here (Wrapper contract
[§1](../../docs/wrapper-contract.md#1-preflight-phase-1-must)).

---

## Install

Run-in-place from the clone is the baseline; the PATH launcher is optional. Both
reuse the single shared [`git-loopy/PROMPT.md`](../PROMPT.md).

### Run in place (baseline)

```powershell
git clone https://github.com/bradcstevens/git-loopy
# From inside the git repository you want to work (issues labeled ready-for-agent):
pwsh -NoLogo -NoProfile -File /path/to/git-loopy/powershell/git-loopy.ps1
```

Using git-loopy as a project scaffold ([skills-setup
§1.1](../../docs/skills-setup.md#part-1--install-git-loopy-and-its-skills))? Run
it from the repo root:

```powershell
pwsh -NoLogo -NoProfile -File git-loopy/powershell/git-loopy.ps1
```

### Optional: put `git-loopy` on your PATH

`install.ps1` writes a small launcher shim (it installs **nothing else** — no
Python, no TUI helper, no package manager) that runs this clone's
`git-loopy.ps1`. On Windows it writes a `git-loopy.cmd`; on Linux and macOS a
`git-loopy` script with a `pwsh` shebang:

```powershell
pwsh -NoLogo -NoProfile -File git-loopy/powershell/install.ps1     # default bin dir
# or choose the directory:
pwsh -NoLogo -NoProfile -File git-loopy/powershell/install.ps1 -BinDir ~/bin
```

Then, from inside any git repository:

```powershell
git-loopy
```

The default install directory is `~\bin` on Windows and `$XDG_BIN_HOME` (else
`~/.local/bin`) on Linux and macOS; the installer prints a `PATH` hint if it
isn't already on `PATH`. To uninstall, delete the shim (`git-loopy.cmd` or
`git-loopy`). Move the clone? Re-run `install.ps1`.

---

## Run it

```powershell
# Unlimited iterations, default model (claude-opus-4.8 at `max` reasoning effort).
git-loopy

# Cap at 5 iterations (0 or omitted = unlimited).
git-loopy 5

# Pick a different model (bare base id); effort is left to the backend.
$env:GIT_LOOPY_MODEL = "gpt-5.6-sol"; git-loopy

# CLI flags override environment variables.
git-loopy --model gpt-5.6-sol --reasoning-effort high --max-nmt-strikes 5

# Legacy local-markdown issues (prds/<feature>/NNN-*.md).
git-loopy --issue-source prds

# Report the distribution Release version without starting Run preflight.
git-loopy --version
```

(Without the PATH launcher, run
`pwsh -NoLogo -NoProfile -File /path/to/git-loopy/powershell/git-loopy.ps1`,
passing the same arguments after the file path.)
`git-loopy --version` prints exactly `git-loopy <VERSION>` and does not require
a repository, Config, GitHub, Copilot, network access, or an Event sink.

### Native Continuation tracer

The public `continuation` command supports contract-identical completion-envelope
`publish` and read-only `reconcile`:

```powershell
git-loopy continuation capabilities
git-loopy continuation publish --input completion.json
git-loopy continuation reconcile --input reconciliation.json
```

The capability manifest is authoritative. Continuation remains off by default;
terminal rendering, Runner modes, Dispatch recording, index repair, and
concurrent Dispatch are not advertised by this distribution.

---

## Configuration surface (phase 1)

Every knob is settable by a **CLI flag** or an **environment variable**.
Resolution precedence is **CLI flag > env var > built-in default** (the
`config.toml` project/global tiers arrive in phase 3). The two denylists are the
set **union** of their CLI and env values, not an override.

| Env var | CLI flag | Default | Meaning |
| --- | --- | --- | --- |
| — | `--version` | — | Print the distribution Release version and exit before Run preflight. |
| — | `<max-iterations>` (positional) | `0` (unlimited) | Cap the Run at N Iterations. Reaching it is a clean exit. |
| `GIT_LOOPY_MODEL` | `--model ID` | `claude-opus-4.8` | Model id (bare base id). |
| `GIT_LOOPY_REASONING_EFFORT` | `--reasoning-effort` | `max` for the built-in model | `none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`. Choosing another model without an effort leaves it to the backend. |
| `GIT_LOOPY_ISSUE_SOURCE` | `--issue-source` | `github` | `github` or `prds` (legacy local markdown). |
| `GIT_LOOPY_MAX_NMT_STRIKES` | `--max-nmt-strikes N` | `3` | Consecutive no-progress Iterations before abort. |
| `GIT_LOOPY_DENY_TOOLS` | `--deny-tool TOOL` (repeatable) | empty | Tools to deny the agent (union). |
| `GIT_LOOPY_DENY_SKILLS` | `--deny-skill SKILL` (repeatable) | empty | Skills to deny the agent (union). |
| `GIT_LOOPY_SEND_TIMEOUT_SECONDS` | `--send-timeout-seconds N` | `7200` | Per-Iteration agent turn timeout. |
| `GIT_LOOPY_INTERACTIVE` | `--interactive` / `--no-interactive` | auto (on only when stdout is a TTY) | Render the Run through the shared `git-loopy-tui` helper. |
| `GIT_LOOPY_TUI_GRACE_SECONDS` | — | `5` | How long Run-end waits for the helper to exit on its own before signalling it. |

This is the phase-1 core of the shared
[environment surface](../../docs/wrapper-contract.md#11-environment-variable-surface-must-honour-the-phase-1-core);
PR mode, the model picker, OTel, and Parallel-mode variables belong to later
phases and are not read by this port yet.

### The closed-world Skill policy fails closed here

The Python Orchestrator implements the closed-world **Skill policy**
([contract §17](../../docs/wrapper-contract.md#17-closed-world-skill-policy-skill-policy-rollout-must))
first. This port has no `config.toml` tier yet, so it cannot honour one — and
running an Iteration on a *wider* capability set than the operator configured is
the outcome §17.6 exists to prevent. Every policy surface therefore **aborts
before source collection and before Copilot is invoked**, exiting `1` with a
diagnostic naming the surface:

| Surface | Detection |
| --- | --- |
| `GIT_LOOPY_ENABLED_SKILLS` | Present — including an explicit empty value, which is a real empty policy. |
| `--enable-skill SKILL` | Recognised as a policy overlay, never as an unknown option and never applied. |
| `--disable-skill SKILL` | Same. |
| `enabled_skills` | Any assignment of the key in `<repo>/git-loopy/config.toml` or `<config-home>/git-loopy/config.toml`. Detection is deliberately conservative — this port has no TOML parser, and over-detecting costs one diagnostic while under-detecting widens a Run. A quoted key is escape-decoded first (`"enabled\u005fskills"` is the same key to `tomllib`); .NET resolves the full scalar range, so unlike the shell port nothing is dropped. A commented example is not a policy. |

The deprecated legacy guards — `GIT_LOOPY_DENY_SKILLS` and `--deny-skill` — are
**not** a closed-world surface. They keep resolving and running unchanged.

Use the Python Orchestrator until this port reaches native Config parity, or
remove the policy surface from the environment and Config this port is reading.
The operator guide is [`docs/skill-policy.md`](../../docs/skill-policy.md).

---

## Replay artifacts

Each Run streams the shared **Event schema** as JSONL to stdout and appends the
same lines to a replay log:

```
.git-loopy/logs/<iso-timestamp>-<run_id>.jsonl
```

Secrets are scrubbed before a line is written. The Orchestrator keeps
`.git-loopy/` in your repo's `.gitignore` so these artifacts never land in a
commit or Checkpoint. The event vocabulary is pinned in Wrapper contract
[§12](../../docs/wrapper-contract.md#12-event-schema-phase-1-must).

---

## The live interface (`git-loopy-tui`)

The shared `git-loopy-tui` helper renders the same Event stream as a live
terminal interface. This port **supervises** it; it does not implement it. Plain
JSONL on stdout is the baseline and is always what you fall back to.

**Whether to go interactive** resolves as **CLI flag > `GIT_LOOPY_INTERACTIVE` >
auto-detect**, matching the Python member and the shell port:

- `--interactive` / `--no-interactive` decide it outright. Neither consumes a
  value, and the last one on the command line wins.
- `GIT_LOOPY_INTERACTIVE` decides it when no flag was passed. `1`/`true`/`yes`/`on`
  are on; any other non-blank value is off. A blank value is not a decision.
- Otherwise it is on only when stdout is a terminal — so pipes, redirects, CI,
  and `--parallel` Lanes stay plain text without being told to.

**Which helper runs** is resolved in one order, and the first hit wins:

| Rank | Source | Path |
| --- | --- | --- |
| 1 | clone-local | `<repo>/.git-loopy/bin/git-loopy-tui` ([ADR-0013](../../docs/adr/0013-multi-language-runner-family.md#decision)) |
| 2 | `PATH` | the first `git-loopy-tui` on your `PATH` |

On Windows the `.exe`, `.com`, `.cmd`, and `.bat` extensions are tried in that
order at each rank; elsewhere the bare name is used.

A clone-local helper is part of *this clone's* packaged distribution, so Wrapper
contract [§16](../../docs/wrapper-contract.md#16-release-and-compatibility-identity-must)
requires exact Release-version equality: on drift it is **refused** and the Run
continues in plain text. A helper found on `PATH` is a separate installation, so
Release drift only earns a **warning** and it still runs. Release equality is
never the compatibility authority in either case — that is the probe.

**The probe is the gate.** Before anything takes over the terminal, the helper is
run as `git-loopy-tui --schema-version` and must report an Event-schema range
that contains the version this port emits. A helper that fails the probe is never
started, so an incompatible one cannot leave your terminal in a half-drawn state.

**Delivery is one flushed JSON object per line on the helper's stdin** — the same
lines, in the same order, as the replay log. The helper inherits the Run's real
stdout and stderr so it can draw; only its stdin is a pipe, and that pipe is
pinned to UTF-8 so a console code page cannot mangle the Event stream.

**A live interface never fails a Run.** Anything that goes wrong with the helper —
not found, probe failure, refused Release, failing to start, dying mid-Run,
stopping reading — produces exactly **one** diagnostic on stderr, permanently
reverts the Run to plain JSONL on stdout, and leaves the exit code alone. The
helper is never respawned inside a Run: a second start would mean a second
terminal takeover and a stream with a hole in it.

**Run-end closes the helper's stdin** as the cue to draw its final frame and
restore the terminal, waits `GIT_LOOPY_TUI_GRACE_SECONDS` (default `5`) for it to
exit on its own, then signals and reaps it. The Run's exit code is decided before
teardown and is never changed by it — including when the helper itself exits
non-zero.

---

## Exit codes

| Exit | Meaning | When |
| --- | --- | --- |
| `0` | Clean — Pool empty | An Iteration's collection finds no `ready-for-agent` issues. |
| `0` | Clean — cap reached | The optional iteration cap `N` is reached. |
| `1` | Aborted — stuck | `GIT_LOOPY_MAX_NMT_STRIKES` consecutive no-progress Iterations. |
| `1` | Aborted — preflight | A precondition failed before the first Iteration (unauthenticated `gh`, missing `docs/agents/issue-tracker.md`, missing `copilot`, …). |
| `2` | Usage error | Malformed invocation (e.g. a non-numeric iteration cap). |

The full table is Wrapper contract
[§10](../../docs/wrapper-contract.md#10-exit-codes-phase-1-must).

---

## Shared behaviour (single-sourced)

Details the whole family shares are **not** duplicated here — read them once in:

- [`docs/wrapper-contract.md`](../../docs/wrapper-contract.md) — the authoritative,
  versioned specification (collection, discriminator, auto-close backstop,
  progress/Strike accounting, Checkpoint, push, exit codes, env surface, events).
- [`docs/runners.md`](../../docs/runners.md#per-iteration-flow) — the operator
  view of the per-Iteration flow, and its
  [skill routing](../../docs/runners.md#skill-routing)
  (`/diagnosing-bugs`, `/prototype`, `/tdd`, `/codebase-design`).
- [`CONTEXT.md`](../../CONTEXT.md) — the domain glossary (Run, Iteration, Pool,
  Strike, Checkpoint, Active issue, …).

The contract is enforced across every port by the
[Conformance suite](../conformance/README.md) in CI, so this PowerShell port and
the Python and shell ports never drift.

---

**Next:**
- [`git-loopy/shell/README.md`](../shell/README.md) — the Bash port (Linux/macOS; needs `jq`).
- [`docs/runners.md`](../../docs/runners.md) — the Runner family and roadmap.
- Back to [`README.md`](../../README.md).
