# `git-loopy` Python Reference Orchestrator

`git-loopy/python/` is the currently shippable reference Orchestrator in the
git-loopy Runner family, built on the
[GitHub Copilot Python SDK](https://github.com/github/copilot-sdk/tree/main/python).
It loads [`git-loopy/PROMPT.md`](../PROMPT.md) (or the packaged default; see
[Prompt resolution](#prompt-resolution)) each Iteration and enforces the
**Wrapper contract**: `ready-for-agent` collection, the `## What to build` plus
`## Acceptance criteria` discriminator, a `Closes/Fixes/Resolves #N`
auto-close backstop, the `GIT_LOOPY_*` configuration surface, and the
clean-on-empty / abort-on-stuck termination model.

The runner gives you a rich terminal UX — frozen iteration `Panel`s,
per-iteration token + billed-Credits signal, a JSONL replay log under
`.git-loopy/logs/`, a run-summary JSON under `.git-loopy/runs/`, and opt-in
OpenTelemetry tracing — after a one-time `uv sync` bootstrap. See the
[skills setup prerequisites](../../docs/skills-setup.md#prerequisites) and the
git-loopy
root [`README.md`](../../README.md) for positioning, and
[`docs/runners.md`](../../docs/runners.md) for the full runner reference.

`git-loopy` is the canonical command for the Python member. Shell, PowerShell,
and Rust Orchestrators are planned around the same contract
([ADR-0013](../../docs/adr/0013-multi-language-runner-family.md)). Model,
reasoning effort, and context tier are set with per-Run `--model`,
`--reasoning-effort`, and `--context-tier` flags or persisted `config.toml`
values.

---

## Installation lifecycle

These commands are **Python Runner only**. The shell and PowerShell
Orchestrators do not yet have the management-command dispatcher or the
machine-local asset model needed to manage Config, prompt provenance, the
installed catalog, and the TUI helper. They remain Runner-family members under
the shared Wrapper contract; this lifecycle is deliberately not a contract
obligation.

Choose the command by what changed:

| Command | What it does | Does not do |
| --- | --- | --- |
| `git-loopy update` | Refreshes the machine-local state the installed Release owns: the installed catalog, TUI helper, global prompt override when **Scaffold provenance** proves it untouched, and a Release-retired Config route. | Does not replace the distribution, start a Run, or write to the tracker. |
| `git-loopy upgrade` | Requires a global keep-or-migrate choice, replaces the executing distribution through its proven **Install channel**, then runs `update --routing`. | Does not guess an Install channel, write Config before handoff, update a clone it does not own, or replace a second `git-loopy` artifact on `PATH`. |
| `git-loopy uninstall` | Removes the distribution through its proven Install channel and the machine-local state. | Does not edit repository contents by default or remove a live Lane's work. |

An **Edge install** is an explicit `upgrade --edge <ref>` landing on unreleased
source. Identify it by its ref, not the source `VERSION`, because only a
published Release has a Release-version identity. **Scaffold provenance** is
the Release and digest git-loopy recorded when it scaffolded an asset; it is the
proof that allows a prompt replacement without overwriting operator prose.

| Command | Flags | Exit behavior | Worked example |
| --- | --- | --- | --- |
| `update` | `--global` (default), `--project`, `--dry-run`, `--routing [keep\|migrate\|ask]` | `0` when the chosen Config scope settles and every refreshed asset reaches the installed Release; `1` for an undecided/refused migration, ambiguous or failed repair, or asset refresh failure. | `git-loopy update --project` |
| `upgrade` | `--to <version>`, `--edge` / `--ref <ref>`, `--allow-downgrade`, `--routing [keep\|migrate\|ask]` | `0` when the required move and routing-aware `update` succeed; nonzero when the target, direction, channel, or routing choice is refused, handoff fails, or the chained install/refresh fails. | `git-loopy upgrade --routing keep` |
| `uninstall` | `--all`, `--yes` / `-y` | `0` only when every planned removal succeeds; `1` for an unconfirmed plan, an unsafe path, a live or unreadable Lane, a channel that cannot be proven, or any incomplete removal. | `git-loopy uninstall --yes` |

The refusals are intentional safeguards, not partial upgrades: a customized or
unrecorded prompt is reported rather than overwritten; an unprovable Install
channel prints a command for the operator instead of guessing; and tracked
project scope plus Run logs stay out of an uninstall unless `--all` explicitly
names the repository. If an old routing key locks the **Config** out of ordinary
commands or a Run, use `git-loopy update` for global scope, or
`git-loopy update --project` for the repository Config.

The sections below give the complete behavior and constraints for each command.

---

## One-time bootstrap

```bash
# From the repo root: install the runner's dependencies.
uv sync --project git-loopy/python

# Optional: install the OpenTelemetry extra to enable opt-in tracing.
uv sync --project git-loopy/python --extra otel
```

**Requires:** Python **≥ 3.11** on PATH, and either
[`uv`](https://docs.astral.sh/uv/) (recommended) or `pip` **≥ 24** as
a fallback. The other prerequisites (`gh` signed in, `git`, `copilot`) are listed in
[`docs/skills-setup.md`](../../docs/skills-setup.md#prerequisites).

The bootstrap is per-clone; subsequent invocations of `git-loopy` use
the cached environment under `git-loopy/python/.venv/`.

The corporate-compatible Runner pins `github-copilot-sdk==1.0.13`, which runs
Copilot CLI `1.0.83` by default. Updating the separate `copilot` command on `PATH`
does not update that harness. The refreshed roster recognizes
`gpt-6-astra` (including `max` reasoning). The pinned-harness listing did not
offer Gemini 3.8 on the account used for this upgrade, so no unverified effort
set is added for it: configured `gemini-3.8-flash` selections and efforts stay
unchanged, with the usual unknown-model warning and pass-through behavior.
Roster membership does not guarantee account availability.

SDK upgrades must move the pin, lockfile, and roster CLI-version stamp
together. Offline tests check that stamp and the installed SDK's client,
session, permission, event, and Skill-discovery interfaces. SDK-provided
Skills normalize to the existing `custom` source kind; a pathless provider
Skill still cannot enter a Run's isolated Skill exposure unless the
installed catalog supplies a filesystem-backed winner of the same name.
Global Skill discovery uses the typed metadata RPC without creating an agent
session. Discovery errors are reported rather than accepted as a partial catalog.

---

## Install (run from anywhere)

The bootstrap above is the **in-repo dev** path. To run `git-loopy` from **any**
repository, install it once as a global engine (ADR-0006). Publishing to PyPI is
deferred, so the install string points at this repo's nested package via
`#subdirectory=git-loopy/python`:

```bash
# Put a single `git-loopy` command for the published v0.9.0 Release on PATH.
uv tool install "git+https://github.com/bradcstevens/git-loopy@v0.9.0#subdirectory=git-loopy/python"

# ...then run it from inside any git repo:
cd ~/some/other/repo && git-loopy
```

For an ephemeral, npx-style run (no install), use `uvx` with the same spec (a
bare `uvx git-loopy` is reserved for a future PyPI release):

```bash
uvx --from "git+https://github.com/bradcstevens/git-loopy@v0.9.0#subdirectory=git-loopy/python" git-loopy
```

Repos already on Python/uv can instead add it as a **project-local dev
dependency** and run it through their own environment:

```bash
uv add --dev "git+https://github.com/bradcstevens/git-loopy@v0.9.0#subdirectory=git-loopy/python"
uv run git-loopy
```

A fresh install runs with **zero setup**: the default prompt ships inside the
wheel (see [Prompt resolution](#prompt-resolution)), so a bare `git-loopy` works
in a repo that has no `git-loopy/` folder at all. Persist per-run knobs in a
[`config.toml`](#persistent-config-configtoml) — hand-written, or scaffolded for
you by [`git-loopy init`](#first-run-setup-git-loopy-init) — when you want them.

The commands above are all **v0.9.0 Release** installs: `git-loopy --version`
reports `git-loopy 0.9.0`, the installation's Release identity. To choose a
different named Release, use its published tag in the same position (for
example, `@v0.8.0`). To deliberately install unreleased source, pin a full
commit SHA instead; that is an **Edge install**, whose identity is the SHA, not
the source `VERSION` value. See the root
[installation channels](../../README.md#installation-identity-and-channels)
for commands.

---

## Finding commands

`git-loopy help` is an alias for `git-loopy --help`. Both print the same
category-grouped management-command surface; the bare `git-loopy` invocation
continues to start a Run.

Shell completions and other machine consumers can read that same surface with:

```bash
git-loopy commands --json
```

It emits this stable, ordered document:

```json
{
  "schema_version": 1,
  "commands": [
    {
      "name": "init",
      "category": "Getting started",
      "summary": "First-run setup wizard for Config and Skill policy."
    }
  ]
}
```

`commands` contains every root management command exactly once, in the order
shown by root help. Each command object always has the string `name`,
`category`, and `summary` fields. `schema_version` changes only for an
incompatible shape change. `git-loopy commands` without `--json` refuses rather
than becoming a second human-facing listing.

---

## Installation identity (`git-loopy info`)

`git-loopy info` describes the Python Runner artifact currently executing: its
executable path, **Install channel** (only when that ownership can be proven),
**Release version**, resolved commit, whether that commit is a published Release,
and **Edge install** status. It also lists the Config-home assets git-loopy
installs — the Config, the prompt override, the **installed catalog**, and the
TUI helper — and what each one has drifted into. It is read-only and always
exits `0`; unavailable identity is reported as `unknown`, never treated as a
health failure, and no amount of drift changes the exit code.

```bash
git-loopy info
git-loopy info --json
```

Each asset reports one classification, judged against its **Scaffold
provenance**:

| Classification | What it means |
| --- | --- |
| `untouched` | Scaffold provenance covers it and its content still matches what that **Release version** wrote, so a refresh can replace it. |
| `customized` | Scaffold provenance covers it and cannot prove the content is git-loopy's — the content differs, no entry exists, or the record is unreadable. Treated as the operator's work, the fail-safe direction ADR-0054 asks for. |
| `unrecorded` | Scaffold provenance records nothing about it: the **installed catalog** and the TUI helper, which are machine-managed and re-cut wholesale rather than authored, and anything that is not installed. |

`present` is the separate fact of whether the asset is there at all, so "you have
not installed this" stays distinguishable from "this is installed and nothing
proves what it is". Plain text prints `not installed` for an absent asset.

Every reported asset lives in the Config home, so the **TUI helper** row is the
machine-local copy under `<config-home>/git-loopy/bin/` — the one ADR-0054 hands
to `update` and `uninstall`. A clone-local helper in `.git-loopy/bin/`, or one a
package manager put on `PATH`, is a different artifact through a different
channel: a Run still attaches to it, and `info` deliberately does not report it
as git-loopy's to refresh or remove.

`--json` emits this stable schema. Fields with unknown facts are `null`; the
`assets` array always lists the whole Config-home inventory, in a fixed order,
whether or not each entry exists on disk. Each entry carries its stable display
name, resolved path, presence, classification, and originating Release when
Scaffold provenance proves one.

```json
{
  "schema_version": 1,
  "artifact": "python-runner",
  "executable": "/path/to/git-loopy",
  "install_channel": {"name": "uv-tool", "proven": true},
  "release_version": "0.9.0",
  "resolved_commit": "0123456789abcdef0123456789abcdef01234567",
  "published": true,
  "edge_install": false,
  "assets": [
    {
      "name": "config.toml",
      "path": "/home/operator/.config/git-loopy/config.toml",
      "present": true,
      "classification": "untouched",
      "release_version": "0.9.0"
    },
    {
      "name": "PROMPT.md",
      "path": "/home/operator/.config/git-loopy/PROMPT.md",
      "present": true,
      "classification": "customized",
      "release_version": "0.8.0"
    },
    {
      "name": "installed catalog",
      "path": "/home/operator/.config/git-loopy/skills",
      "present": true,
      "classification": "unrecorded",
      "release_version": null
    },
    {
      "name": "TUI helper",
      "path": "/home/operator/.config/git-loopy/bin/git-loopy-tui",
      "present": false,
      "classification": "unrecorded",
      "release_version": null
    }
  ]
}
```

`install_channel.name` is `uv-tool`, `homebrew`, `installer-launcher`, or
`unproven`. `unproven` is deliberate: `uv tool install` and the shell installer
can both place a `git-loopy` command in the same XDG bin directory, so inferring
an owner without the shell installer's self-identifying shim could make a later
mutating command operate on the wrong artifact.

---

## Refreshing machine-local assets (`git-loopy update`)

`git-loopy update` refreshes the machine-local assets belonging to the installed
**Release version** without changing that Release. It refreshes the **installed
catalog**, resolves and downloads the newest compatible published TUI helper at
or below the installed Release into `<config-home>/git-loopy/bin/` (recording its
verified resolved identity in `git-loopy-tui.release`), and repairs
Release-retired `[routing]` keys in the global Config. Only `--project` — which
repairs a *tracked* file, and is the one exception to ADR-0054's machine-local
scope — needs a repository.

Source-only Releases carry no helper assets of their own; they can still consume
an eligible older helper under the same checksum, identity, and schema checks.
When no exact helper is published, a machine-local build that reports the installed
Runner's exact version and compatible Event schema is retained if the immutable
tag's `release-trust.json` explicitly declares `source-only`. Missing assets alone
do not establish that mode, and an unreadable index or policy remains an error.
Otherwise the verified published-helper selection remains unchanged.

That helper is one a Run attaches to. The Python Runner resolves a helper in this
order, first hit wins:

| Rank | Source | Path |
| --- | --- | --- |
| 1 | clone-local | `<repo>/.git-loopy/bin/git-loopy-tui` — what the shell and PowerShell installers stage for that clone |
| 2 | machine-local | `<config-home>/git-loopy/bin/git-loopy-tui` — what `update` installs |
| 3 | `PATH` | the first `git-loopy-tui` on your `PATH` |

Ranks 1 and 2 are components of a packaged distribution, so Wrapper contract
[§15](../../docs/wrapper-contract.md#15-release-and-compatibility-identity-must)
requires Release-version equality or a verified resolved fallback identity
(ADR-0052, #492); unrecorded or tampered drift is **refused**, leaving the Run in
plain text. A `PATH` helper is someone else's installation, so drift there is
only a warning. A machine-local refusal names `git-loopy update` as its repair,
because an `upgrade` that has outrun its `update` or unverified drift is the one
thing that produces it.

A maintenance refusal is attributed to **published identity**, never to the
scratch directory a candidate was unpacked in — that directory is gone before
the message reaches you. Three failures are distinguished, and each exits
non-zero without disturbing an existing verified installation:

| Refusal | What it means | Remedy |
| --- | --- | --- |
| `no published git-loopy-tui Release carrying <archive> is at or below <version>` | No Release at or below the installed one attaches *this host's* archive and checksum. The archive is named so a host the Release line defers is distinguishable from a helper nobody has published yet. | Wait for a Release that publishes this host's helper, or stage a clone-local helper at `<repo>/.git-loopy/bin/`. |
| `... can serve this Runner; the newest candidate, Release <version>, was rejected: ...` | A helper *was* published, and the newest one at or below the installed Release cannot decode this Runner's Event schema. Sharing a version line is not proof of interoperability. | Upgrade to a Release whose published helper speaks this Event schema; the reason names the range the candidate answers with. |
| `cannot read published helper Releases from <url>` / `cannot download release artifact <url>` | The Release index or an asset was unreachable or unreadable. | Retry once the host is reachable; nothing was activated. |

For the global `PROMPT.md` override, **Scaffold provenance** is the safety
boundary: an `untouched` override is replaced with this Release's packaged
prompt and its provenance advances. A `customized` override stays byte-identical;
the command summarizes the upstream prompt changes since the Release recorded in
its provenance, and says so plainly when that Release is the installed one and
there is nothing upstream to port. An `unrecorded` override is treated as
customized and is never replaced.

```bash
# Repair the machine-global Config and refresh installed assets.
git-loopy update

# Repair a repository Config, or preview either repair without writing anything.
git-loopy update --project
git-loopy update --global --dry-run
```

The Config repair removes a key outside the closed taxonomy, and renames a
legacy `task-type:<key>` spelling only when its bare current key is absent. A
conflicting old and current key is **reported, never guessed at**: the file
keeps both, the command exits non-zero, and the message names
`git-loopy config routing unset '<key>' --<scope>` so the operator decides which
route survives.

Before the file is replaced, the original is copied beside itself as
`config.toml.bak` (with a numeric suffix when one already exists) and that path
is reported straight away — a write that then fails must not leave a backup
nothing accounted for. The repair then rewrites the Config **in canonical
form**, so any comments it carried survive only in that backup; the command says
so whenever it writes. Each changed key is reported afterwards, in the tense the
run earned: `Removed`/`Renamed` once the write lands, `Would remove`/`Would
rename` under `--dry-run`.

A scope with nothing retired says so and is not rewritten, and a scope with no
Config at all is reported as not installed rather than as clean. `--dry-run`
covers the Config alone and names the assets it left uninspected.

The command reports each changed asset and any asset it left alone, and exits
non-zero when an asset could not be brought to the installed Release — including
an ambiguous Config repair or an **installed catalog** left behind its pinned
revision because the source could not be reached, which is reported rather than
passed off as a refresh. It never starts a Run or writes to the tracker;
`git-loopy labels --apply` remains the only command that changes the **Label
vocabulary** on GitHub.

### Explicit routing migration

```bash
# Collect a keep-or-migrate choice; blank input, q, EOF or Ctrl-C cancels.
git-loopy update --project --routing

# Supply the decision without prompting, or preview it offline.
git-loopy update --global --routing keep
git-loopy update --project --routing migrate --dry-run
```

**Keep** records `route_policy = "static"`: saved routes and the run-wide default
remain authoritative, and the Measured routing tier remains effective.
**Migrate** records `route_policy = "dynamic"`: every authored `[routing]` row
is retained, including rows identical to an old recommendation; only work
uncovered by those rows becomes Dynamic. Calibration artifacts remain unchanged
as supporting evidence, not Static pins under Dynamic policy. Remove a row you
no longer want explicitly with `config routing unset <task-type> --<scope>`.
Both choices disclose and enable strict live validation rather than silent
effort/tier correction. Legacy pairs retain their inherited run-level tier, and
only an explicitly configured `[escalation]` authorizes Static escalation.

Migration needs your own `GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY` outside Config
and an explicitly authored, verified `[route_associations]` table; no key or
model association is invented. When no associations are configured or inherited,
the interactive path asks for a TOML inline table matching exact benchmark
identities to the Copilot configurations they scored, not similar display names.
Unattended use must already have that table in Config.
It collects missing `routing_deadline_seconds`,
`routing_credit_allowance`, and `selector_concurrency` on an interactive terminal,
with **no defaults**. Existing scope/inherited values may supply them; inherited
values remain inherited rather than being copied into the project.
Explicit `GIT_LOOPY_ROUTING_DEADLINE_SECONDS`,
`GIT_LOOPY_ROUTING_CREDIT_ALLOWANCE`, and `GIT_LOOPY_SELECTOR_CONCURRENCY` values
override those saved values for this migration and are persisted in the chosen
scope. The credential is never saved. Classification and selector retries count
toward Run Consumption; post-paid in-flight billing can overshoot the allowance.
Exhaustion admits no further calls and never substitutes a cheaper selector.

The candidate Config crosses the same routing readiness seam as doctor and a
Run **before** any write. It verifies the chosen scope (plus global inheritance
and the Measured routing tier for a project), without temporary Run overrides
masking a missing prerequisite or invalid saved route. A global migration does
not judge any repository's overrides. Readiness calls no selector, classifier,
Calibration, or tracker write; its model-data requests use provider request quota.
Every later Run, proposal and Pickup checks afresh.

Without a terminal, `--routing` must find a recorded `route_policy` in the chosen
scope, inherit an explicit global choice, or receive `keep`/`migrate` explicitly.
An inherited choice remains inherited rather than creating a project override.
An outstanding decision, cancelled
question, invalid authorization, or failed readiness exits nonzero without
writing Config or refreshing assets. Operator edits detected during readiness
are not overwritten. Successful changes use an atomic write and the ordinary
numbered Config backup; unchanged recorded choices are not rewritten.
If a subsequent asset refresh fails, the successful Config migration stays saved.
`--dry-run` neither prompts nor reads live readiness and explicitly says readiness
was not checked. It requires a supplied or recorded choice.

This option migrates policy, not retired routing keys: repair those separately
with the installed Release's `update --project` or `update --global` before
retrying policy migration. `upgrade` now requires this
decision and chains the routing-aware update described below. Bare `update`
still repairs assets and retired keys without choosing a policy.

**Local Runs with saved Config now require the choice automatically.** If either
scope contains Config values but no effective Static/Dynamic policy is selected,
the Run refuses before Skill migration, model listing, detachment or Agent work.
It never prompts or writes Config to resolve this decision. Doctor reports the
same authority refusal, and the detached worker retains it. Model/effort overrides
alone are not keep-or-migrate consent. Record the choice with the commands above,
or supply `--route-policy static`/`dynamic` (`GIT_LOOPY_ROUTE_POLICY` for doctor
or unattended use) for this invocation only. Temporary authority does not settle
the next Run's decision. A selected Static path requires live Copilot eligibility,
not leaderboard access or a Route selector.

This is partial #567 work, not final default activation: a genuinely unconfigured
Run (both Config tables empty) retains its previous path. Bare init can still save
Config without a routing choice, but the subsequent Run will refuse until that
choice is supplied. Prefer `init --routing` to authorize setup before saving.

**Non-local activation is deferred.** The GitHub Actions Execution host
authenticates on another machine; the local listing cannot validate its settings.
Unselected remote Runs therefore retain their legacy path rather than being
forced into an unusable choice. Selected Static/Dynamic policies still refuse
that placement: use `--execution-host local`, or `--route-policy unselected`
(`GIT_LOOPY_ROUTE_POLICY=unselected` for doctor) to retain remote legacy behavior.
The latter is not strict routing or completed migration. No remote capability
support is implied by the local migration guard.

---

## Moving between Releases (`git-loopy upgrade`)

`git-loopy upgrade` replaces the git-loopy artifact it is **itself running from**
with a published **Release version**, through the **Install channel** that placed
it, and then runs `git-loopy update --routing` from what the move installed — landing a new
Release is precisely the event that invalidates the scaffolded assets. It needs
no repository, and it moves nothing else: a clone-local helper, the shell
Orchestrator's launcher, and anything else on your `PATH` are other channels'
artifacts.

```bash
# Move to the newest published Release, using a recorded choice or prompting.
git-loopy upgrade

# Supply the machine-global choice explicitly for unattended use.
git-loopy upgrade --routing keep
git-loopy upgrade --routing migrate

# Pin a named published Release, or move deliberately backwards.
git-loopy upgrade --to 0.9.0
git-loopy upgrade --to 0.8.0 --allow-downgrade

# Land unreleased code. Naming the ref is the opt-in.
git-loopy upgrade --edge 0123456789abcdef0123456789abcdef01234567
```

| Flag | What it lands |
| --- | --- |
| *(none)* | The newest published Release — what GitHub calls the latest release, so never a draft and never a prerelease. |
| `--to <version>` | That Release version, **verified published** first. A version nobody cut is refused here rather than becoming a failed install, or an **Edge install** you were never told about. |
| `--edge <ref>` (alias `--ref`) | That commit or ref, reported as an **Edge install**: identify it by the ref, not by the `VERSION` its source reports. A ref spelled like a Release tag is refused and pointed at `--to`. |
| `--allow-downgrade` | A move that is not provably forward of the installed Release. Required for an older Release, and for one whose direction cannot be established at all. |
| `--routing [keep\|migrate\|ask]` | Global routing consent, not a target. With no value or no flag, reuse a recorded global policy or ask on an interactive terminal. Otherwise supply the choice explicitly; no unattended default is inferred. |

Already on the Release the move resolved? Nothing is re-installed: `upgrade` says
so, but still requires routing consent and runs the routing-aware `update`.
An outstanding choice cannot be bypassed by the same-Release path.

**Consent precedes the move; readiness precedes the Config write.** An
unattended upgrade with no recorded or supplied choice exits nonzero before
handoff, with `upgrade --routing keep` / `upgrade --routing migrate` as the remedy.
Interactive cancellation also leaves the installation and Config unchanged.
Upgrade considers only global Config: a project policy or temporary Run override
does not supply global consent. It discloses retained Static routes, inherited
tiers, strict validation and the end of implicit Static escalation.

The installed Runner's `update` then collects any missing Dynamic authorization
on an interactive terminal and uses the shared readiness verdict before saving.
Keep requires no leaderboard key or selector call. A recorded choice is re-read
after installation rather than forwarded as a new explicit override of a later
operator edit. No project Config is written, and no Calibration or work starts.
Failed readiness leaves Config unchanged, but does **not** roll back a successful
distribution install. Fix the reported prerequisite and run
`git-loopy update --routing keep` or `git-loopy update --routing migrate`.
If the new Release retires a routing key, the chained routing-aware update
refuses that Config rather than applying the bare update's automatic key
repair. Config and machine-local assets remain unchanged at that step, while
the new distribution stays installed. Run `git-loopy update --global` from the
newly installed Release to perform its disclosed, backed-up key repair, then
retry `git-loopy update --routing keep` or `git-loopy update --routing migrate`.
An ambiguous repair still requires the operator to choose which route to keep.
If a deliberately selected older target lacks `update --routing`, its command
refusal is likewise nonzero, not a successful migration; upgrade never silently
falls back to a routing-unaware refresh.

**The move is a process replacement, not a write.** The chain — the channel's
install command, then `git-loopy update --routing` — replaces the running `git-loopy`, so
the executable Windows holds open is released with the process rather than
written over while locked, and the chained `update` runs from the artifact the
move installed.

**A channel is used only when it can be proven and pinned.** Both questions have
to answer yes, and each `no` changes nothing and prints the exact command
instead:

| Channel | What `upgrade` does |
| --- | --- |
| `uv-tool` | Performs the move: `uv tool install --force` against the pinned specifier. |
| `homebrew` | Refuses: a formula installs whichever version it currently publishes, so performing it would report a Release identity `upgrade` did not place. Prints `brew upgrade git-loopy`. |
| `installer-launcher` | Refuses: that launcher execs a clone you own, and moving it means updating that clone and re-running its installer — never something git-loopy does over your uncommitted work. |
| `unproven` | Refuses: `uv tool install` and the shell installer can both place a `git-loopy` command in the same directory, so moving one of them would be a guess. Prints the `uv` command in case that is the one. |

---

## Removing a machine-local installation (`git-loopy uninstall`)

`git-loopy uninstall` prints its complete removal plan and asks for confirmation
before changing anything. By default it removes the executable through its proven
**Install channel**, the global config-home, the **installed catalog** and its
record, and the machine-local TUI helper.

```bash
# Inspect the plan and confirm it interactively.
git-loopy uninstall

# Accept the printed plan without a prompt.
git-loopy uninstall --yes

# Explicitly include this repository's tracked project scope and Run logs.
git-loopy uninstall --all
```

When run in a repository, the default also reports the project `config.toml`,
`PROMPT.md`, and `.git-loopy/` Run logs it deliberately keeps. `--all` is the
explicit opt-in to remove those repository-owned paths and therefore requires a
repository. A live **Lane** refuses the entire operation before confirmation and
points at `git-loopy sweep`; uncommitted agent work is never treated as removal
residue, and a Lane whose liveness cannot be read counts as live.

`--all` widens the plan to those three named paths and no further. Whatever the
flag, `uninstall` refuses outright rather than removing a machine-local path that
encloses your repository or sits inside it — a config-home configured under a
checkout does not make that checkout git-loopy's to delete — and refuses a project
path that resolves outside the repository. A path that is itself a symbolic link
to a directory is refused too: unlinking it would orphan the tree it stands for
and following it would delete somewhere the plan never named. Anything that stops
resolving where the printed plan said it did, between the plan and your answer,
refuses as well.

If the executable's Install channel cannot be proven, `uninstall` leaves that
executable in place, prints the exact manual removal command, and still removes
the machine-local state it can prove belongs to git-loopy. It exits non-zero so
automation cannot mistake that partial result for a complete uninstall.

The shell installer's launcher is a shim that `exec`s a clone you own, so removing
it is reported as removing the launcher — not as a channel uninstall — and the
clone it pointed at is reported as deliberately kept. The command never writes to
the tracker.

---

## First-run setup (`git-loopy init`)

`git-loopy init` is an interactive wizard that installs the pinned Skill catalog
and writes a [`config.toml`](#persistent-config-configtoml) (and, by default,
scaffolds an editable `PROMPT.md` override) into a chosen **scope**, then
**exits** — it never starts the loop. You rarely run it by hand: the **first**
bare `git-loopy` in a repo with no Config anywhere auto-runs it for you on a TTY
(see [First run (auto-setup)](#first-run-auto-setup)), then continues into the
loop. Invoke it explicitly to (re)configure a scope, pin a model / reasoning
effort, establish a Skill policy, or get an editable copy of the prompt.

```bash
# Interactive: pick a scope, then a model + reasoning effort from the live list.
git-loopy init

# CI-friendly: accept every default, never prompt.
git-loopy init --yes

# Force a scope (skips the scope question).
git-loopy init --global      # ~/.config/git-loopy/ (honours $XDG_CONFIG_HOME)
git-loopy init --project     # <repo-root>/git-loopy/
```

The wizard:

- **Asks the scope first** — **global** (this machine) or **project** (this
  repo). `--global` / `--project` skip the question; outside a git repository
  only **global** is available.
- **Writes `config.toml` on successful setup** to that scope with your chosen `model` /
  `reasoning_effort`, seeded from the same live model list the `--select-model`
  picker uses.
- **Uses one continuous keyboard wizard** for scope, model, effort, routing,
  scaffold, and Skill policy. It needs an interactive terminal — **both** stdin
  and stdout, since the fullscreen wizard paints the screen it reads answers
  from; redirect either (`git-loopy init > log`) and it refuses up front rather
  than drawing into a pipe. Use `--yes` for non-interactive setup.
  `up`/`down` move, `space` toggles a Skill, `enter` advances, `esc` goes back a
  step (and cancels on the first, where there is nowhere back to), `ctrl+c`
  cancels outright, and `ctrl+s` jumps to the end. It composes the same model and
  Skill pickers the rest of git-loopy uses, and ends on a review screen listing
  scope, the config path it will write, model, effort, routing, scaffold, and how
  many Skills are enabled, over `Save` / `Back` / `Cancel` — so collect-then-commit
  is something you see rather than something you are promised. `Back` returns to
  the step you picked, so correcting one answer does not restart setup.
- **Installs the workflow Skill catalog first**, before collecting anything —
  because the **Skill policy** you are about to choose is a choice among the
  installed catalog. It clones
  [`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
  at the revision this distribution pins (`git_loopy/skill_source.json`) into
  `<config-home>/git-loopy/skills/`. That location is **machine-wide and
  scope-independent**: `--global` and `--project` install to the same place and
  only decide which Config the rest of setup writes. git-loopy ships no Skills
  of its own, and every later Run refreshes this install against the pin
  ([ADR-0025](../../docs/adr/0025-installed-skill-catalog.md)).
- **Then offers (default yes)** to scaffold an editable `PROMPT.md` override
  into the scope — project `./git-loopy/PROMPT.md`, global
  `~/.config/git-loopy/PROMPT.md`. Nothing is written into `.copilot/skills/`,
  in this repository or any other: a project Skill tree is not a Skill source a
  Run reads. See [`docs/skills-setup.md`](../../docs/skills-setup.md).
- **Records scaffold provenance** in `scaffold-provenance.json` beside the
  scope's editable assets. Each Config and prompt that this invocation writes
  records its Release version and SHA-256 digest; a later init preserves an
  entry for an existing prompt it leaves untouched.
- **Cancelling** (`q`, `quit`, or EOF / Ctrl-C at any prompt) saves **no Config,
  prompt override, Skill policy, or tracker label**, starts nothing, and exits
  non-zero. That is the whole guarantee, and it is deliberately narrower than
  "nothing was written": the Skill install above is the wizard's *first* act,
  before a single answer is collected, and it is machine-wide rather than scoped.
  When that install actually changed something, the cancellation names the
  catalog root and revision it left behind instead of denying it — a
  prerequisite you can inspect, not an operator choice you never confirmed
  ([ADR-0058](https://github.com/bradcstevens/git-loopy/blob/9d33e78b8aba97ae16ee5a133aae1fca78905ed0/docs/adr/0058-init-precedes-the-run-and-clients-do-not-own-its-lifetime.md)).

  ```text
  git-loopy init cancelled; no Config, prompt override, Skill policy, or
  tracker label was written. The prerequisite Skill catalog install is
  machine-wide and remains at ~/.config/git-loopy/skills (revision 4f1c2a9e8b03).
  ```

### Explicit routing setup (opt-in)

`git-loopy init --routing [keep|migrate|ask]` composes first setup with the same
authorization and live routing-readiness verdict as `update --routing`, doctor,
and a Run. **This is not final default activation:** bare init and auto-setup
retain their existing routing policy.

Choose `migrate` for Dynamic uncovered work or `keep` for strict Static policy.
Omit the argument (or use `ask`) to inherit a recorded choice or decide at the
terminal with no default. The fullscreen wizard still collects scope, model,
optional Static routes, prompt and Skills; its review discloses that terminal
routing authorization follows before anything is saved. The default routing
answer adds **no Static rows**. Recommended Static values remain an explicit
choice, and authored routes still outrank Dynamic work.
The custom walk adds or replaces only the rows you explicitly choose. Skipped
and unvisited task types preserve saved rows and acquire no recommended seed;
remove an unwanted saved row with `config routing unset`, not by skipping it.

Supply your own `GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY` in the environment before
Dynamic setup. Setup never requests a key in an echoed prompt or saves one.
Missing deadline, per-Run credit allowance and selector concurrency are collected
with no invented values. Missing associations are authored as a TOML inline
table, for example `{"<AA id>" = "<Copilot model>@<scored effort>"}`; verify the
identity/revision/effort correspondence yourself. A model without an effort dial
uses its bare identifier. Current scores and authenticated harness eligibility
must then agree. No selector, classifier or Calibration session is started.

`--yes` never prompts and is **not** routing consent: supply `keep`/`migrate` or
inherit an explicit recorded choice, plus the required authorization for Dynamic
work. Saved/inherited model, effort, prompt and Skill policy remain untouched
on this path; absent model/effort resolve normally, and only unconfigured prompt
and Skill policy are scaffolded. Inherited limits and associations are not
copied, while explicitly supplied environment bounds are saved. Keep needs no
leaderboard credential or routing allowance.

Cancellation, invalid authorization or failed readiness saves no Config, prompt,
Skill policy, scaffold provenance or tracker labels. Edits detected during the
live read to the chosen Config, inherited global Config, prompt or Measured
routing artifact abort rather than being overwritten or approved against stale
inputs. The prerequisite machine-wide Skill catalog may remain, as described
above. After successful setup, later Run failures leave those saved choices
intact; proposal and Pickup must validate fresh inputs, not trust setup readiness.

### Tracker labels the wizard ensures

Run inside a repository, `init` also **ensures the label vocabulary the loop
reads exists in that repository's tracker**. Without it a fresh clone has no
`ready-for-agent`, so every **Pool** is empty, no `parallel-safe`, so
**Parallel mode** can never engage, and no `priority`, so every eligible issue
ranks the same — and nothing in a Run says why.

| Label | Role |
| ----- | ---- |
| `needs-triage` | Maintainer needs to evaluate this issue. |
| `needs-info` | Waiting on the reporter for more information. |
| `ready-for-agent` | Fully specified, ready for autonomous execution — this is what fills the **Pool**. |
| `ready-for-human` | Requires human implementation. |
| `wontfix` | Will not be actioned. |
| `parallel-safe` | Human assertion, applied *alongside* `ready-for-agent`, that the issue may be worked concurrently in its own **Lane**. Never inferred. |
| `priority` | Human assertion that the issue is worked **ahead of older ones**. Never inferred. It reorders and nothing else — a `priority` issue still needs `ready-for-agent`, still has to be AFK-ready, and still needs `parallel-safe` to take a Lane. |

- The five triage roles are read from this repository's own
  [`docs/agents/triage-labels.md`](../../docs/agents/triage-labels.md) mapping,
  so a tracker that renamed a role gets *its* label strings rather than the
  canonical defaults. With no such file the canonical defaults are used.
  `parallel-safe` and `priority` are not triage roles and always use their own
  names — three Orchestrators read those exact strings.
- **Only absent labels are created.** A label that already exists keeps its
  colour and description exactly as they are, and a re-run creates nothing.
- `init` reports which labels it created and which already existed.
- If the tracker is unreachable or the credential may not create labels, the
  step is **skipped with a message naming the labels still missing**, and the
  rest of setup still succeeds.
- Outside a git repository (`init --global` with no repo) there is no tracker to
  write to, so the step does not run.

### Reconciling the vocabulary (`git-loopy labels`)

`init` **ensures** — it creates what is absent and leaves what exists alone — and
it runs *once*. So a label added to the vocabulary after `init` last ran never
lands on that tracker, and a colour or description that drifts stays drifted,
both silently. `git-loopy labels` is the reconcile path:

```bash
# Report only. Reads the tracker and changes nothing.
git-loopy labels

# Write the difference back: create what is missing, correct what drifted.
git-loopy labels --apply
```

```text
matched needs-triage
matched needs-info
drifted ready-for-agent (description)
...
missing priority
2 labels differ from the vocabulary; 12 match. Re-run with --apply to write the difference.
```

- **Reporting is the default**; applying is an explicit flag.
- **Every vocabulary label gets a verdict** — `missing`, `drifted` (naming the
  attribute that differs), or `matched` — so "is this label read at all?" is
  answerable, not just "is anything wrong?".
- The five roles resolve through
  [`docs/agents/triage-labels.md`](../../docs/agents/triage-labels.md), so a
  **renamed role is neither missing nor drift**. `parallel-safe`, `priority`, and
  the `task-type:` labels compare on their literal strings.
- **Additive only.** Labels the tracker carries outside the vocabulary are never
  reported, never edited, and never deleted, and nothing is ever renamed —
  a rename would detach every issue already carrying the label.
- **Idempotent.** Applying twice reports no difference the second time.
- An unreachable or unauthorised tracker **warns and exits non-zero**; a
  difference on its own is a finding, not a failure, and exits `0`.
- Nothing is rolled back. An unauthorised credential is refused on the *first*
  write, so it costs no partial write; a failure after some labels landed is
  reported with an exact account of what did, and idempotence makes a re-run
  finish the job.

Hand-editing `config.toml` directly stays fully supported — `init` is a
convenience over it, not a replacement. To inspect or change persisted settings
afterwards without hand-finding the file, use the
[`git-loopy config`](#managing-config-git-loopy-config) subcommands.

### First run (auto-setup)

The **very first** bare `git-loopy` — when no `config.toml` resolves in *either*
scope — sets itself up:

- On an **interactive TTY** it auto-runs the wizard above, then checks the Run's
  preconditions on the Config it just wrote. A missing routing choice refuses
  work with the `update --routing` or `--route-policy` remedy above. The terminal test is the same one
  explicit `git-loopy init` applies — stdin *and* stdout — so `git-loopy > log`
  on a fresh clone takes the no-TTY path below rather than opening a fullscreen
  wizard against a pipe.
- Cancelling aborts the whole command: it saves no operator choice, starts no
  worker, and exits non-zero — an aborted setup never starts an unconfirmed
  loop. See the cancellation guarantee above for what the prerequisite catalog
  install may legitimately leave behind.
- With **no TTY** (CI, pipes) it **never prompts**: it falls back to the built-in
  defaults and goes straight to the loop, so automated runs can't hang on the
  wizard.
- Once Config exists in either scope, a bare `git-loopy` skips the wizard entirely
  and checks the Run's preconditions, including routing authority.

**Setup finishing is not the loop starting.** `init` saves and exits; the Run is
a separate act with its own preconditions (a git repository, `copilot` on
`PATH`, a usable model). When one of those refuses, your saved setup stays
exactly as you confirmed it — nothing rolls it back — but no issue work starts
and the command exits non-zero naming the blocker and its remedy:

```text
git-loopy: warning: the Run worker exited 1 before it recorded any activity;
no issue work started. Its startup diagnostics follow
(.git-loopy/logs/run-20260919-101500-diagnostics.log).
  git-loopy: error: copilot is not on PATH. Install the GitHub Copilot CLI and
  re-run git-loopy.
```

On a TTY the loop runs as a **detached worker** the terminal client watches, so
the client reports that worker's exit status and echoes the tail of its startup
diagnostics rather than showing an empty Dashboard and exiting `0`. The handoff
uses ordinary scrollback — setup's wizard leaves the screen before the client
takes it — so a blocked startup is readable after the fact instead of being
erased by a screen restore.

---

## Invocation

```bash
# Unlimited iterations, default model (claude-opus-5 at `max` reasoning effort).
uv run --project git-loopy/python git-loopy

# Cap at 50 iterations.
uv run --project git-loopy/python git-loopy 50

# Pick a different model + reasoning effort for one run. Flags are the top of
# the chain (flag > env > project config > global config > built-in default).
uv run --project git-loopy/python git-loopy --model gpt-5.6-sol --reasoning-effort max

# Explicitly request no reasoning. This is different from omitting the effort,
# which lets the backend choose when no configured/default effort applies.
uv run --project git-loopy/python git-loopy --model gpt-5.6-sol --reasoning-effort none

# Opt into the live model + reasoning-effort picker (ModelSelectionMode) at
# startup — off by default (equivalently set GIT_LOOPY_MODEL_SELECT=1).
uv run --project git-loopy/python git-loopy --select-model

# Tolerate more no-progress iterations before aborting (default: 3).
GIT_LOOPY_MAX_NMT_STRIKES=5 uv run --project git-loopy/python git-loopy

# Deprecated: deny a tool or skill at the SDK permission gate (repeatable,
# additive with GIT_LOOPY_DENY_TOOLS / the deprecated GIT_LOOPY_DENY_SKILLS).
# `--deny-skill` is deprecated — prefer the closed-world Skill policy below.
uv run --project git-loopy/python git-loopy --deny-tool bash --deny-skill handoff

# Closed-world **Skill policy** (ADR-0015): temporarily widen or narrow the
# configured allowlist for one Run. Repeatable; disable wins on a conflict.
uv run --project git-loopy/python git-loopy --enable-skill handoff
uv run --project git-loopy/python git-loopy --disable-skill prototype

# Replace the configured base Skill policy outright for one Run. An explicitly
# empty value is a real empty policy, not "unset".
GIT_LOOPY_ENABLED_SKILLS=tdd,code-review uv run --project git-loopy/python git-loopy

# Every Run uses rolling dispatch. The selected Execution host declares its
# Lane ceiling; `parallel-safe` still decides which issues can occupy a Lane.
# A Run with no Lane work uses the serial Iteration driver and reports the
# existing degraded or serial-fallback Event.
uv run --project git-loopy/python git-loopy

# Use the legacy local-markdown mode (prds/<feature>/NNN-*.md).
GIT_LOOPY_ISSUE_SOURCE=prds uv run --project git-loopy/python git-loopy

# Report the distribution Release version without starting Run preflight.
uv run --project git-loopy/python git-loopy --version
```

`uv run --project git-loopy/python git-loopy --help` prints the full CLI
surface including verbosity flags (`-v`, `-vv`, `-vvv`) and
`--no-reasoning`. `git-loopy --version` prints exactly
`git-loopy <VERSION>` and does not require a repository, Config, GitHub,
Copilot, network access, or the TUI.

---

## Listing this clone's Runs (`git-loopy runs`)

```bash
git-loopy runs
```

```
RUN                         STATE    STARTED               SCOPE
01K6Z9QWERTYUIOPASDFGHJKLZ  live     2026-09-19T11:04:02Z  /src/app
01K6Z7H4TCXV0YQ9M2N8B3RJ5D  dead     2026-09-19T09:51:40Z  /src/app/.worktrees/lane-2
01K6Z4B1GKAP7WS3E6D9F2TQXN  unknown  2026-09-18T22:17:09Z  /src/app
```

Every Run of **this clone** — the worktree you invoked it from plus every other
worktree `git worktree list` registers — newest first. Run it from any of those
worktrees and you get the same listing, including Runs that were started from a
different one. `RUN` is the full Run identity, and `SCOPE` is the worktree the
Run published its artefacts in; together they are what the forthcoming Attach
and Stop commands target by.

The domain is the clone, never the machine. An independent clone of the same
repository has its own Runs and its own listing; nothing here scans for them,
and there is no "newest Run" default that could let an unnamed gesture reach a
Run you did not name.

`STATE` is what *this host can prove*, read from the per-Run control artifact's
advisory lock (`flock` on macOS and Linux, `LockFileEx` on native Windows) and
from nothing else — never a pid, a heartbeat, or the presence of a leftover
file, since a Run that ended normally leaves its artefacts behind on purpose:

| State     | Means                                                                                     |
| --------- | ----------------------------------------------------------------------------------------- |
| `live`    | The lock is still held: a worker process is running this Run right now.                    |
| `dead`    | The lock is free: the Run has ended. Its artefacts remain readable.                        |
| `unknown` | The lock could not be read — no control artefact was ever published, or it is unreadable.  |

`unknown` is not a softer `dead`. Being unable to prove a Run has finished is
never treated as permission to control or reclaim its work, which is the same
rule `git-loopy sweep` already follows.

The listing is purely observational: it starts no agent work, sends no Stop,
reclaims nothing, and never reaches your issue tracker. It needs only `git`.

---

## Exit codes

| Exit                  | Code | When                                                                                                                                                                                                                                                |
| --------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clean — Pool empty    | `0`  | Start of an Iteration finds the ready-for-agent Pool empty.                                                                                                                                                                                        |
| Clean — iteration cap | `0`  | Positional `<max-iterations>` reached without natural termination.                                                                                                                                                                                |
| Aborted — stuck       | `1`  | `GIT_LOOPY_MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress.                                                                                                                                                                             |
| Aborted — preflight   | `1`  | Pre-loop setup failed: not inside a git repo, `gh` not authed or not on PATH, `CopilotClient` construction failed, writers bundle failed, or unknown `GIT_LOOPY_ISSUE_SOURCE`. Surfaces cleanly via stderr. |

---

## Env-var surface

| Env var                           | Default                        | Notes                                                                                                                                                                                                            |
| --------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GIT_LOOPY_MODEL`                           | `claude-opus-5`                | Copilot CLI model id (the `--model` flag overrides this). Use a **bare base id** — model id and reasoning effort are separate axes (a suffixed id like `claude-opus-4.7-xhigh` is rejected as "not available"). A recognised trailing `-<effort>` segment is peeled off into `GIT_LOOPY_REASONING_EFFORT` for backward compatibility. With ModelSelectionMode enabled (`--select-model` or `GIT_LOOPY_MODEL_SELECT=1`) this value is the startup picker's pre-selected cursor and the model the run uses is whatever you confirm there; on a default run (picker off) it is the model the run uses directly. |
| `GIT_LOOPY_REASONING_EFFORT`                | `max` (built-in default model only) | One of `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max`, case-insensitive (the `--reasoning-effort` flag overrides this). Explicit `none` requests no reasoning; an omitted value lets the backend choose when no configured/default effort applies. Precedence: this env var (validated; an invalid value aborts exit `1`) → a `-<effort>` suffix on `GIT_LOOPY_MODEL` → the built-in default (`max`, applied only when `GIT_LOOPY_MODEL` is unset) → unset. A model without configurable reasoning (`auto`, `claude-sonnet-4.5`, `claude-haiku-4.5`) forces this to **unset** (the CLI hard-rejects `session.create` otherwise); an unknown model warns and passes the value through to the CLI. On an interactive run **with ModelSelectionMode enabled** (`--select-model` / `GIT_LOOPY_MODEL_SELECT`) this is the startup picker's **pre-selected effort** (the picker's stage 2 is auto-skipped for a reasoning-incapable model) and the effort the run uses is whatever you confirm there; on a default run (picker off) it is the effort the run uses directly. |
| `GIT_LOOPY_CONTEXT_TIER`                    | `default`                       | Root-session tier: `default` or `long_context`. `--context-tier` wins, then this value, project Config, global Config, and the default. It constrains every **Routing resolution**, including a legacy `[routing]` model/effort pair, but does **not** suppress per-task-type routing. |
| `GIT_LOOPY_ROUTE_POLICY`                    | unset (`unselected`)            | Which **Route policy** this Run uses. `unselected` is absence of consent: local saved Config requires an explicit Static/Dynamic choice before work. Staged no-Config and non-local paths retain legacy behavior. `static` selects the **Static route** (ADR-0057): your `model` / `reasoning_effort` / `context_tier` are verified against the **authenticated harness this Run spawns** and then honoured exactly, rather than being passed through the built-in model roster's capability gate. `--route-policy` wins, then this value, project Config, global Config. A settings combination the harness does not support **fails before any work** instead of being quietly downgraded. `dynamic` selects the **Dynamic route** (ADR-0057): each issue's pair is elected from live Artificial Analysis evidence by a bounded **Route selector**, and needs `GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY` plus a deadline, a credit allowance, a selector concurrency and a verified `[route_associations]` table. An explicit run-wide model or effort override bypasses those dynamic prerequisites, but its Static settings must still pass live harness validation. |
| `GIT_LOOPY_CLASSIFIER_MODEL`                | unset (cheapest live pair)     | The model the **Task-type** and **Bump-class classifiers** run on. Each reads an unlabelled issue's own content and writes a closed `task-type:` or `semver:` label back at **Pickup** (ADR-0029, ADR-0052). Deliberately **not** `GIT_LOOPY_MODEL`: borrowing the run-wide default would let it decide every issue's task type, and so every **Routed pair**, as an unmeasured prior that appears nowhere as a routing input. Unset does not fall back to `GIT_LOOPY_MODEL` — it falls back to the **cheapest pair on the live roster**, so the prior is named and overridable rather than inherited. Classification spends **AI Credits**, folded into the run's cost; it never ticks a **Strike** and is never counted as an **Iteration**. |
| `GIT_LOOPY_CLASSIFIER_REASONING_EFFORT`     | unset (cheapest live pair)     | The reasoning effort both classifiers run at, resolved alongside `GIT_LOOPY_CLASSIFIER_MODEL` and held to the same effort vocabulary. Same precedence chain (env → project → global), same independence from `GIT_LOOPY_REASONING_EFFORT`. |
| `GIT_LOOPY_ISSUE_SOURCE`                    | `github`                       | `github` or `prds`. `prds` walks `prds/<feature>/NNN-*.md` files.                                                                                                                                                |
| `GIT_LOOPY_MAX_NMT_STRIKES`                 | `3`                            | Consecutive no-progress iterations before aborting exit `1`. Integer ≥ 1.                                                                                                                                        |
| `GIT_LOOPY_WORKTREE_SETUP`         | unset (auto-detect)            | A shell command run in each freshly created **Lane** worktree, before that Lane's agent session starts, to prepare its environment (install deps, create a venv, ...) so the feedback loops can run there. Runs once per Lane creation with `cwd` set to the worktree. When unset/blank, a best-effort auto-detect picks a common install command for the project type (`uv.lock`→`uv sync`, `package-lock.json`→`npm ci`, `package.json`→`npm install`, `requirements.txt`→`pip install -r requirements.txt`, `go.mod`→`go mod download`, ...). A non-zero setup exit is surfaced in the diagnostics log but does not abort the Lane. |
| `GIT_LOOPY_GATE_TIMEOUT_SECONDS`   | `3600` (one hour)              | The wall-clock bound each **feedback loop** the **Integration** gate runs must finish within. Integration re-runs the merged worktree's own `AGENTS.md` loops unattended after every Lane merge, so a loop waiting on a socket, a prompt or a lock would otherwise block the gate forever. On expiry the loop's whole process group is killed and the gate goes **red naming that loop, as a timeout** — kept distinct from a non-zero exit, because a timeout is not a test failure. |
| `GIT_LOOPY_CREDIT_BUDGET_USD_PER_HOUR` | unset (contraction unavailable) | The authoritative AI-credit ceiling adaptation judges this Run's burn against. Without it, credit pressure is unknown rather than estimated. Credit never gates capacity or expansion; it only contracts the effective Lane limit under sustained burn. A malformed or non-positive value reads as unset rather than aborting the Run. |
| `GIT_LOOPY_HOST_LOAD_BUDGET`       | `1.0`                           | The tolerated run-queue depth **per CPU**, so `1.0` means "keep every core busy but do not queue". Host/setup pressure is reported as the ratio of the observed one-minute load average per CPU to this budget. Host-load observability starts and ceilings the controller at the bound Execution host's declared capacity; a platform without a load average remains at the static-safe limit. Same malformed-reads-as-default rule. |
| `GIT_LOOPY_ENABLED_SKILLS`            | unset                          | **Exact replacement** of the configured base **Skill policy** (ADR-0015) for one Run — comma-separated canonical Skill names. *Presence*, not content, is what counts: an explicitly empty value is a real empty policy (which then fails preflight for omitting the **Required Skills**), while leaving it unset keeps the project / global `enabled_skills`. Because it replaces the base policy it also suppresses the one-time legacy-Config migration offer. See [`docs/skill-policy.md`](../../docs/skill-policy.md). |
| `GIT_LOOPY_DENY_TOOLS`                | _(empty)_                      | Comma-separated tool denylist. **Unioned** with `--deny-tool` CLI flags — CLI does NOT override env (security-positive divergence).                                                                              |
| `GIT_LOOPY_DENY_SKILLS`               | _(empty)_                      | **Deprecated** final guard (contract §17.2): comma-separated skill denylist for the `skill` meta-tool's `arguments.skill` field. **Unioned** with `--deny-skill` CLI flags and across config tiers — it only ever subtracts, and a denial that would remove a Required Skill is a validation failure rather than a quiet subtraction. Prefer omitting the name from `enabled_skills`. |
| `GIT_LOOPY_OTEL_ENABLED`              | unset (disabled)               | Truthy (`1`, `true`, `yes`, `on`) enables OpenTelemetry tracing. Requires the `[otel]` extra. When disabled, `opentelemetry` is never imported — base install pays zero cost.                                    |
| `OTEL_EXPORTER_OTLP_ENDPOINT`     | unset                          | Presence (non-empty) also enables OTel tracing — matches the conventional OTel-ecosystem activation pattern.                                                                                                     |
| `GIT_LOOPY_SEND_TIMEOUT_SECONDS`      | `7200` (2 h)                   | Per-Iteration `send_and_wait` timeout. The SDK's default of `60` is far too short for autonomous Iterations that frequently run 30+ minutes.                                                                      |
| `GIT_LOOPY_MODEL_SELECT`              | unset (picker off)             | Truthy (`1`, `true`, `yes`, `on`) opts the interactive run into **ModelSelectionMode** — the one-time startup model + reasoning-effort picker. Off by default, so an ordinary interactive run goes straight to the loop on the configured model/effort with no prompt. The `--select-model` / `--no-select-model` flag **wins** over this env var when the two disagree. When requested on a non-TTY run, the run warns and falls back to the configured model. |

CLI flags (`--version`, `--model ID`, `--reasoning-effort EFFORT`,
`--context-tier TIER`,
`--route-policy POLICY`,
`-v` / `-vv` / `-vvv`,
`--no-reasoning`, `--enable-skill` / `--disable-skill`, `--deny-tool`,
`--deny-skill` (deprecated), `--select-model` / `--no-select-model`,
`--issue N`)
are the runner's only non-positional flags. `--model` / `--reasoning-effort`
are per-run overrides at the **top** of the precedence chain (they win over
env, project / global config, and the built-in default). `--context-tier`
follows that precedence for the run-wide work-tier constraint without
suppressing a static route. `--route-policy` selects the **Route policy**
([below](#the-route-policy-and-the-static-route)). See `git-loopy --help` for the
full list.

`--issue N` **pins** one issue for one invocation (ADR-0032): the run works
issue `N` instead of the head of the selection order, and every other issue
keeps its place in that order behind it. The pin **bypasses order and nothing
else** — a pinned issue still has to be eligible, and a pin that is closed,
missing, unreadable, lacks `ready-for-agent`, fails the AFK-ready body
discriminator, or lacks `parallel-safe` **fails the
invocation** rather than falling back to normal order, because silently working
a different issue than the one you named is worse than stopping. The refusal
names what is wrong, down to the specific missing `##` section. It is
deliberately a flag and not a label or an env var: those are globally scoped and
would point every concurrent run at the same issue, which is the opposite of
what pinning is for. At most one issue may be pinned per invocation; a second
`--issue` is a usage error. There is no config-file or environment equivalent.

---

## Persistent Config (`config.toml`)

For a from-anywhere install (ADR-0006) the same knobs can be persisted in a
hand-editable `config.toml`, so a bare `git-loopy` needs no wrapper script. Two
scopes are read, resolved in this order (highest wins), **key by key**:

```
CLI flag  >  env var  >  project config  >  global config  >  built-in default
```

- **project** — `<repo-root>/git-loopy/config.toml` (checked into, or ignored
  per, the repo).
- **global** — `$XDG_CONFIG_HOME/git-loopy/config.toml` (honouring
  `$XDG_CONFIG_HOME`), else `~/.config/git-loopy/config.toml`.

Keys are flat and named after the knob (env var minus the `GIT_LOOPY_` prefix,
lower-cased):

```toml
model = "gpt-5.6-sol"
reasoning_effort = "max"
context_tier = "long_context"
route_policy = "static"    # opt in to the Static route; omit to keep today's behaviour
classifier_model = "gpt-5.4-mini"
classifier_effort = "low"
issue_source = "github"
max_nmt_strikes = 5
demotion_threshold = 3
include_prs = true
otel_enabled = false
send_timeout_seconds = 7200
enabled_skills = ["tdd", "code-review"]
deny_tools = ["bash"]
deny_skills = []   # deprecated final guard — prefer omitting from enabled_skills
```

The **persisted** knobs are `model`, `reasoning_effort`, `context_tier`, `route_policy`,
`classifier_model`,
`classifier_effort`, `issue_source`,
`include_prs`, `max_nmt_strikes`, `demotion_threshold`, `otel_enabled`,
`send_timeout_seconds`, `enabled_skills`, and the two denylists. The
model/effort **capability gate** (below) still applies to a config-supplied
model. The two denylists are
**unioned** across all four sources (CLI ∪ env ∪ project ∪ global) — never
overridden — matching the security-positive env-var behavior. **Per-run-only**
knobs are never read from a file: the positional `<max-iterations>` cap, `-v`
verbosity, and `--no-reasoning`. A
malformed `config.toml` aborts the run with a clean stderr message (exit `1`),
never a traceback.

`enabled_skills` is the one key that does **not** union: it is the closed-world
**Skill policy** allowlist (ADR-0015), and a project value *replaces* the global
one rather than merging with it. An absent key inherits the next scope down and
ultimately resolves to the **Minimal Skill policy** (the Required Skills only),
while `enabled_skills = []` is a real, deliberately empty policy. Manage it with
`git-loopy skills list` / `git-loopy skills edit` / `git-loopy skills sync`
rather than by hand; the complete operator guide — seeding, migration, CI
behaviour, the resolved-policy audit event, and every preflight failure with its
recovery command — is [`docs/skill-policy.md`](../../docs/skill-policy.md).

---

## Managing Config (`git-loopy config`)

`git-loopy config` is a convenience surface over hand-editing `config.toml` (which
stays fully supported). Dispatch is fast — like `init`, it imports no SDK or
renderer. Scope selection matches the [`init`](#first-run-setup-git-loopy-init)
wizard: `--global` / `--project`, defaulting to **project** inside a git repo
else **global**; `set` / `edit` / `path` target one scope, while `get` / `list`
report the **effective merged** value across every source.

```bash
# Persist one key to a scope's config.toml (no editor). Scope defaults to
# project-in-a-repo, else global; --global / --project force it.
git-loopy config set model gpt-5.6-sol
git-loopy config set deny_tools "bash, write"   # list keys take a comma list
git-loopy config set --global reasoning_effort high

# Show the EFFECTIVE value a run would use, merged across
# CLI > env > project > global > measured > built-in default (not just one file).
git-loopy config get model
git-loopy config get task-type:docs  # the Routed pair AND the tier behind it
git-loopy config list                # every persisted key, then the routing map

# Print the resolved config.toml location(s) — scriptable.
git-loopy config path                # both scopes, labelled
git-loopy config path --project      # just the one path

# Open the scope's config.toml in $VISUAL / $EDITOR (seeds an empty file first).
git-loopy config edit --global
```

- **`set <key> <value>`** coerces `<value>` to the key's type (bool / int /
  float / comma-separated list), merges it into that scope's existing
  `config.toml` (sibling keys survive), and writes — no editor. An unknown key or
  an un-coercible value is a clean stderr error (exit `1`).
- **`get <key>` / `list`** resolve through the same precedence chain a real run
  uses, so they report the **effective** value (env vars and both config scopes
  folded in), not one file's raw contents. Values go to **stdout**, warnings to
  **stderr**, so they script cleanly.
- **Routing provenance.** `get task-type:<key>` reports a **Routed pair** with
  the **tier** that supplied it, and `list` names the tier for every routing
  entry it prints. The tier is one of `CLI flag`, `environment variable`,
  `project Config`, `global Config`, `measured`, `provisional (unmeasured)`, or
  `built-in default` — read
  straight off the resolver's own merge, so the name cannot disagree with the
  value. A `measured` entry comes from the machine-written
  [`git-loopy/routing.measured.toml`](../../docs/adr/0028-measured-routing-is-a-committed-tier.md)
  artifact, which is why
  a model nobody typed can appear in the effective config;
  `provisional (unmeasured)` comes from the same artifact but names a pair that is
  **in force without ever having been measured** — what
  [Demotion](../../docs/adr/0030-demotion-is-measured-per-pair.md) installs when it
  steps *up* the price staircase into a rung nobody trialled, recorded under the
  artifact's `provisional` status with the pair it replaced and why, so an
  unmeasured pair never reads as evidence; `built-in default`
  means no tier named that Task type at all, so a repository that has never been
  **Calibrated** is never mistaken for one that has. An explicit `--model` /
  `--reasoning-effort` (or its env equivalent) suppresses routing run-wide, and
  the report says so rather than naming a tier whose value is not in force. A
  hand-written `[routing]` entry beats a provisional one exactly as it beats a
  measured one — the fourth status changes what is *reported*, never the chain.
- **Routing takes effect in every mode.** A **Routed pair** is resolved *per
  issue* at **Pickup**, and every unit of work has a pickup: a serial Iteration
  binds one issue before its session starts exactly as a **Parallel mode**
  **Lane** does, so the model/effort pair and run-level context tier the Pickup
  resolved are the settings the session runs on with one Lane (the serial
  fallback) and at any width. A static `[routing]` pair inherits that tier;
  dynamic routing is not activated by this configuration. The whole chain, the
  `measured` tier included, is live out of the box, so `get` / `list` report a
  winning tier with nothing to qualify it. Until ADR-0037 routing was scoped to
  Parallel mode and the serial loop discarded the pair it had just resolved —
  which
  made the default invocation the one invocation where a configured `[routing]`
  table changed nothing. A **Calibration** is scoped the same way, for the same
  reason: what it measures now takes effect wherever the Run is worked.
- **The Task-type taxonomy is closed** to `planning`, `review`,
  `implementation`, `test`, `docs`, `chore`, and `bugfix`. Anything else is
  **refused**, naming the value and the permitted keys — never warned about and
  quietly routed to the global default, because the classifier writes these
  labels unattended and the label-writing path *creates* a label before
  attaching it, so an invented key would become a permanent tracker label
  routing to the default forever. A Config written before the taxonomy closed
  can still carry one, and it blocks a Run and every `config` read surface until
  it is gone: clear it with `git-loopy config routing unset <type>`, which is
  the one routing op that accepts a key outside the seven, precisely so
  complying does not require hand-editing TOML.
- **`path`** prints the resolved `config.toml` path(s) — both scopes labelled by
  default, or a single bare path with `--global` / `--project`. There is no
  measured scope for `path` or `edit`: the artifact is machine-written and never
  hand-edited, so you drop a measured value by deleting the file and overrule
  one by writing a `[routing]` entry that wins.
- **`edit`** opens the scope's file in `$VISUAL` (else `$EDITOR`); it seeds an
  empty `config.toml` first if none exists, and errors if neither editor var is
  set.

The settable keys are exactly the [persisted knobs](#persistent-config-configtoml)
above (`model`, `reasoning_effort`, `context_tier`, `route_policy`,
`classifier_model`, `classifier_effort`,
`issue_source`, `max_nmt_strikes`, `demotion_threshold`,
`include_prs`, `otel_enabled`, `send_timeout_seconds`,
`deny_tools`, `deny_skills`). Per-run-only knobs are never persisted, so they are
not `config` keys.

### The Route policy and the Static route

`route_policy` (flag: `--route-policy`, env: `GIT_LOOPY_ROUTE_POLICY`) chooses
how this Run decides what each issue runs on:

- **`unselected`** — absence of a decision, not consent. Local saved Config with this
  effective policy refuses before work and names the keep-or-migrate remedy.
  Staged no-Config/non-local paths and historical records retain the built-in **model roster**'s
  capability gate, an effort the roster says the model cannot take is dropped to
  "let the backend pick", a context tier the roster does not list for that model
  is downgraded to `default`, and the run keeps going.
- **`static`** — the **Static route**
  ([ADR-0057](https://github.com/bradcstevens/git-loopy/blob/8023ddc78f6867319daba440184e59825d32e84b/docs/adr/0057-live-evidence-guides-per-issue-routing.md)).
  Your
  `model`, `reasoning_effort` and `context_tier` are one atomic choice, verified
  against the **model listing of the authenticated Copilot harness this Run
  actually spawns** — its eligibility for *your* account, its reasoning-effort
  dial, and the context tiers it prices — and then used exactly as selected.

Under `static` the run **refuses rather than rescues**. A model your account may
not use, a model the harness never listed, an effort outside the model's dial,
an effort on a model with no dial at all, a context tier the harness does not
offer for that model, or a harness that could not be read — each ends the run
with exit `1` **before any issue is picked up**, naming the setting and which
entry it came from:

```
git-loopy: the selected Static route was refused: [routing] docs: 'gpt-5-mini'
does not accept reasoning effort 'max'. It accepts: low, medium.
```

That check covers the whole config in one pass — the run-wide default, every
`[routing]` entry, and an explicitly configured escalation rung — so a broken
route in a table you rarely exercise is caught at the start of the run rather
than six iterations in.

Three details worth knowing:

- **A model with no reasoning-effort dial is sent no effort argument at all.**
  That is a different thing from the effort *value* `none`, which is sent as a
  value to a model whose dial offers it.
- **Your existing config is already a Static route.** A `model` /
  `reasoning_effort` pair, or a `[routing]` entry, is a valid static choice and
  inherits the run-level `context_tier`. Nothing is migrated for you: selecting
  the policy is the only thing that changes behaviour.
- **A static route does not escalate on its own.** git-loopy ships a default
  **escalation rung** that retries a stalled issue on a stronger pair. Under
  `static` that built-in rung does not apply, because a route that promotes
  itself was never static. Write an explicit `[escalation]` block if you want
  one — it is verified like any other route.

One combination is refused outright: `route_policy = "static"` with a
non-`local` `execution_host`. A `github-actions` contribution opens its session
on a GitHub-hosted runner that authenticates as *itself*, so this machine's
model listing is not the listing that would run it — approving a route against
the wrong installation is exactly what the policy exists to prevent. Run
locally, or leave `route_policy` unset for that placement.

### `route_policy = "dynamic"` — elect each issue's route from live evidence

`dynamic` routes **one issue at a time** from current public benchmark
evidence instead of from a pair you wrote down. It is opt-in, off by default,
and starts no dynamic work unless every prerequisite is present:

```toml
route_policy = "dynamic"
routing_deadline_seconds = 120          # or --routing-deadline-seconds
routing_credit_allowance = "5.00"       # or --routing-credit-allowance
selector_concurrency = 2                # or --selector-concurrency

[route_associations]
# benchmark identity -> the Copilot configuration you have verified it names
"gpt-5.6-terra" = "gpt-5.6-terra@high"
```

and `GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY` in the environment. The key is read from the
environment **only** — never written into Config, never serialized into a
detached child, never echoed into diagnostics — and nothing from your
repository is sent to the leaderboard service.

An explicit run-wide `--model` or `--reasoning-effort` override (or its
`GIT_LOOPY_` environment equivalent) suppresses Dynamic routing completely:
it needs no leaderboard access, routing limits, association table, or Route
selector. Its model, effort, and tier are still verified against the live
harness and either honoured exactly or refused, never silently corrected.
A context-only `--context-tier` / `GIT_LOOPY_CONTEXT_TIER` override fixes the work
tier without suppressing Dynamic model/effort selection. Only verified candidates
supporting that exact tier may be offered; no fitting candidate means no assessment
or Dynamic work, not a tier downgrade. The strongest Route selector is still elected
independently and uses the smallest tier fitting its own input, even when it cannot
serve as the work model at the requested tier. Persisted `context_tier` remains the
inherited tier for Static pairs; it is not a context-only Run override.

`git-loopy doctor` and a Run share routing **preflight**:
missing authorization or limits, non-finite bounds, selector concurrency outside
1–64, unverifiable execution placement, and invalid Static settings produce the
same refusal. Doctor spends no routing credits and rewrites no routes, including
under `--apply`. Doctor evaluates Config and environment, not flags on a
separate future Run; to check a run-wide override, supply its
`GIT_LOOPY_MODEL` / `GIT_LOOPY_REASONING_EFFORT` equivalent to doctor, or
`GIT_LOOPY_CONTEXT_TIER` for a context-only override.
They also read current Artificial Analysis evidence and authenticated Copilot
capabilities through the same live-readiness module used by proposal and Pickup.
Unavailable sources, an empty verified candidate intersection, or exhausted
assessment bounds produce a refusal with a remedy, without a selector,
classification, Calibration, or tracker write. These reads use the provider's
request quota, but spend no routing AI Credits.

A successful readiness row is **not a Pickup or an issue-fit guarantee**: no
issue context has been assessed, and proposal and Pickup must check fresh inputs
again. A failed Dynamic-readiness row makes doctor nonzero but does not prevent a
Run from reaching eligible Static work or recovering on a later fresh check.
Missing or invalid Dynamic prerequisites leave uncovered work unavailable for that
Run: no Route selector or fallback work session is started for it. A Task-type
classifier may still discover a retained Static route without leaderboard access,
but only within the explicitly authorized routing deadline, allowance and
concurrency. Its usage remains Run Consumption; missing, invalid or exhausted limits admit
no classification. Retained Static routes still undergo live validation.
Restore the prerequisites before starting a
new Dynamic Run; unlike a transient source outage, a Run with no authorized
routing setup cannot recover by inventing it later. Setup and migration still
refuse to save an unready Dynamic choice, and doctor remains nonzero.
Outstanding migration authority, unsupported placement, and invalid Static
settings still stop the Run. The routing deadline starts before live preflight,
including a listing shared with retained Static validation. The Run carries that
ledger into routing; neither completed validation nor missing leaderboard access
grants a new assessment budget. Expiration still permits already-classified
Static work on freshly validated settings.

**Activation status (#567): incomplete.** `init --routing`, `update --routing`,
and the update chained by `upgrade` offer explicit keep-or-migrate authorization and this shared readiness
verdict. Composed first-setup and saved-migration serial/Lane cases observe actual
session settings and canonical records, including fresh outages after setup.
They also carry saved choices through outcome-aware retries, attempt/allowance
exhaustion, fresh cross-Run reuse, and pending issue-publication recovery without
rewriting Config or duplicating tracker comments.
Saved-setup coverage also exercises model/effort overrides without leaderboard
access and context-only controls in both modes. Work-tier authority survives
detached startup, fresh proposal/Pickup capability checks, and cross-Run reuse;
changed evidence or eligibility cannot carry an obsolete proposal into work.
Saved-setup Consumption cases also compose concurrent classifier/selector billing,
allowance exhaustion and post-paid overshoot with actual work in both modes.
An open routing session's observed bill closes admission immediately; completion
and cancellation preserve the charge exactly once, including additional billing
reported during cancellation cleanup. Malformed cleanup billing cannot swallow
cancellation or undo valid charges already observed. An assessment returned after the authorized
deadline is refused without publishing its classification or proposal; its
Consumption remains visible, and already-bound work may finish.
Upgrade requires consent before handoff, including when the target is already
installed. Its saved global choice reaches actual serial/Lane sessions, with
retained Static routes and fresh post-setup outages covered.
Local Run migration authority is now enforced before work, with temporary
flag/environment and recorded project/global recovery exercised by the shared
`routing-resolution.json` Conformance cases. They observe retained Static rows,
inherited tiers, run-wide and context-only overrides, and loss of access/evidence
after migration through actual sessions and canonical/Dashboard readback.
The shared `first_setup` cases also start without either Config scope, collect
guided project/global answers through the real Textual `init --routing` walk,
and carry repaired readiness failures into actual serial/Lane work. Missing access, invalid bounds,
no verified candidates and unavailable evidence/capabilities save no choices
or tracker labels; Static setup needs no Dynamic access or limits. Canonical
Pickup, Dashboard and final tracker comments agree with actual session settings,
and subsequent Runs preserve the saved Config. These are opt-in setup obligations,
not evidence that bare init or auto-setup activates Dynamic defaults.
That walk exposed and fixed Skill discovery trying to nest an event loop inside
the fullscreen wizard; discovery now completes on its own joined worker before
the wizard can save, with validation failures still propagated without writes.
Non-local activation, the remaining composed activation matrix and the remaining
Wrapper/Conformance activation obligations still precede the final default
change. Existing Config is not migrated implicitly.
Python issue-owning serial and
Lane sessions are the implementation scope; shell/PowerShell activation remains
deferred, and this does not add Subagent or Integration routing.

The Run's startup readback distinguishes uncovered Dynamic work awaiting Pickup
from a configured default pair. No fixed escalation rung does not disable
permitted Dynamic retries; they reselect from outcome evidence. Retained Static
routes and explicit escalation are shown exactly as configured under either
selected policy, without applying legacy offline-roster effort downgrades.
Historical unselected-policy readbacks remain unchanged.

What happens per issue:

1. **Classification first.** An unlabelled issue gets its **Task type** before
   anything routes, because the assessment is told what kind of work it is
   looking at rather than left to guess.
2. **A Static route still wins.** A `[routing]` entry, an explicit
   `--model` / env pin, or a configured `[escalation]` rung is an instruction,
   and git-loopy will not spend a selector call to contradict one. Those routes
   are verified against the harness exactly as under `static`.
3. **The Route selector elects itself deterministically** — the highest
   Intelligence Index among configurations that are both verified by your
   `[route_associations]` table *and* runnable on your authenticated harness, at
   its matched effort, in the smallest context tier that fits the input.
4. **A bounded, read-only assessment** sees the issue, its acceptance criteria,
   the task type, your declared **Feedback loops**, and any *measured* rows from
   `measured-routing.json`. It does not read your tree, run Trials, or do the
   work.
5. **Revalidation at Pickup.** Evidence and eligibility are re-read before the
   work session opens. Unchanged inputs do not buy a second selector call; a
   candidate that changed or became ineligible does not start on its old route.
   A *later Run* revalidates the same way against the decision its own history
   already records — see "Reusing a route a previous Run already elected" below.
6. **Provenance before work.** A `wrapper.routing.resolved` Event records which
   evidence elected the route, when each source was read, which selector
   assessed it, and what it cost — and it is written *before* the session opens.
   If that record cannot be written, the route is refused.
7. **A permitted retry reassesses.** When the **Attempt lifecycle** grants an
   issue another attempt, its next Pickup elects again — and this time the
   assessment is also told what the earlier attempts ran on and how they ended.
   There is no reserved escalation rung under `dynamic`; the first election may
   already take the strongest configuration available.

Two properties are worth knowing before you turn it on:

- **It refuses; it never falls back.** An unreachable source, an exhausted
  allowance or deadline, an empty verified intersection, an invalid selector
  answer, or unreadable eligibility each produce an explicit *unavailable*
  decision. git-loopy will not quietly run the issue on your run-wide default —
  under this policy that default was never verified, precisely because the
  selector was meant to replace it. In parallel mode the candidate is passed
  over for the rest of the Run rather than immediately retried, so one issue
  cannot spend the whole allowance.
- **Routing costs credits.** Classification and selector calls count toward
  `routing_credit_allowance` and toward the Run's **Consumption**. Billing
  already in flight when a bound is reached is disclosed rather than hidden, and
  no further routing call is admitted afterwards. Observed credits count
  immediately, not only when the billed session finishes. Cancellation retains
  that charge; completion does not charge it again.
  These sessions remain **Run**-only **Consumption**: the CLI includes them in Run
  totals and names their subtotal separately; the **Dashboard** shows that subtotal
  in its **Summary** band. They do not inflate an **Iteration**, **Lane contribution**
  or issue's work bill, even while Pool preparation runs beside work. A refused
  route still reports the assessment already billed. Missing billing stays
  unknown rather than becoming a zero or a complete-looking subtotal.

### Reusing a route a previous Run already elected

A selector call costs credits, and most of the time nothing that would change
its answer has moved between one Run and the next. So a later Run reads its own
`.git-loopy/logs/` history, finds the routing record this issue already got, and
tries to **revalidate** it rather than buying the same answer twice.

Revalidation is not a cache lookup. The Run still fetches the live evidence and
re-reads your harness's current capabilities, exactly as a first election does —
what it skips is only the selector session. It reuses the recorded route only
when every relevant input still matches: the issue and its rendered context, the
Route policy, the current eligibility of the model, the evidence, and the
attempt history. If any of those moved — you edited the issue, re-labelled its
task type, or the model left your plan — the Run elects again inside the same
credit allowance, and says which recorded decision stopped validating.

You can see which happened without recomputing anything:

```text
⇌ routing #42  revalidated — reused without a new assessment  (01JD…)  decided 2026-09-18T20:00:00.000Z
⇌ routing #43  reassessed — a recorded route no longer validates  (01JD…)
⇌ routing #44  assessed — no reusable route for this issue
```

The same three states appear in the Dashboard's Lane log, and the
`wrapper.routing.resolved` record carries `routing_reuse`, `reused_proposal_id`
and `reused_validated_at` for anything reading the trace later.

Three boundaries are worth knowing:

- **It is local to this clone.** The history it reads is this checkout's own Run
  logs, so a second clone or a fresh CI runner elects once for itself before it
  has anything to revalidate. There is no shared routing store — that would be a
  second authority for a decision that is supposed to have one.
- **A reused route is not a shortcut past anything else.** It cannot authorize
  work on its own, cannot carry a model past your current harness capabilities,
  does not create an attempt, and still loses to a `[routing]` entry, an
  explicit `--model` pin or a configured escalation rung.
- **The tracker is never the source.** Route comments and labels below are
  output only; nothing reads them back to decide what to run.

### Preparing routes ahead of the work

A selector call sits on the critical path: the issue that is about to be worked
has to wait for it. So while an agent session is running, git-loopy assesses the
*other* issues it has already established as eligible, and a later Pickup that
reaches one finds the assessment already made.

What this is not, and it matters more than what it is:

- **It does not decide what gets worked.** Nothing is reordered, reserved or
  claimed. Your Pool order and the ordinary admission rules are untouched, and
  an issue that was only prepared is exactly as pending as it was.
- **It does not bind anything.** The Pickup is still authoritative. It re-reads
  the live evidence and your harness's current capabilities, compares them
  against what the proposal was made under, and reassesses whatever moved — so
  editing an issue after it was prepared costs you a second selector call and
  never a stale route. A proposal that aged out while the Run worked something
  else is thrown away rather than bound.
- **It does not run when your Run does not.** There is no daemon. Preparation
  lives on the Run's own event loop, holds its proposals in memory, and stops
  when the Run stops.
- **A slow tail does not hold the next Pickup.** Preparation may continue
  between Iterations. Pickup joins only its own in-flight assessment and
  interrupts unrelated preparation to free routing capacity. Interrupted
  assessments are recorded as unavailable, not as reusable proposals; their
  eventual Pickup may reassess only within the remaining allowance. Cancellation
  does not undo provider billing already incurred.

It stays inside the bounds you already configured: `selector_concurrency` caps
how many assessments overlap, `routing_credit_allowance` caps what they may
spend, and preparation stops for the rest of the Run the moment either is gone —
the work already routed carries on regardless. Candidates that are blocked,
unreadable or otherwise not currently eligible are left alone and cost nothing.
Each candidate is re-read when its preparation starts, not just when the Pool
was collected. Classification shares the same admission limits and occurs
before the Static-route check. An already-classified issue your `[routing]`
table covers costs no classifier or selector call. A previous Run's decision
is offered for fresh revalidation, not assumed to match.

```text
⇢ route prepared #43  proposal claude-opus-5 @ high (default)  valid until 2026-09-19T09:05:00.000Z
⇢ route prepared #44  static route applies — no selector call bought
⇢ route prepared #45  an earlier decision is available for Pickup revalidation
⇢ route prepared #46  not prepared — its Pickup decides for itself  (quota_exhausted)
```

Those four states are also on the `wrapper.routing.prepared` Event and in the
Dashboard's Lane log, always phrased as a proposal. If you are reading a trace
later, that is the distinction to hold on to: `wrapper.routing.prepared` is what
was assessed in advance, and `wrapper.routing.resolved` is what a session
actually ran on.

Proposal readback includes its rationale and evidence provenance while leaving
the Queue's final route unset. Retrieval timestamps say when a source was read,
not when its benchmark was measured. Pickup re-reads issue eligibility, current
evidence and capabilities, and the repository's declared Feedback-loop commands
and local measurements. Relevant changes require another selection; unchanged
inputs reuse the proposal without another selector call. A refused Dynamic
candidate does not prevent eligible Static work behind it from proceeding.

### Route comments and labels

After a final static or Dynamic Routing resolution is durably recorded,
git-loopy projects a materially changed assignment onto its GitHub issue. The
append-only comment carries an idempotency identity, exact model/effort/context
values, a concise safe rationale, and a provenance reference. The issue also
carries one `git-loopy-route:` label for the same triple; it is compact and
collision-resistant, while the comment and local event retain the exact values.

These tracker writes are observational. They never become Routing input, never
change a Task type, and never override Config. A failed comment or label write
does not stop the already-recorded work: its local delivery state remains
pending or failed, is retried finitely on a later Run, and appears separately in
the Dashboard. A failed local final-resolution record is different and starts no
work.

### What a later attempt is told

Only the endings that say something about the *work* count against a
configuration. A session that crashed, was refused by content policy, ran out of
time, or reported there was nothing to do tells the next election about your
harness, your clock or your backlog — not about the model. Exactly one ending is
evidence about the configuration: the session that ran to the end, claimed no
failure, and left nothing behind.

Even that does not blacklist anything. Every eligible configuration stays a
candidate at every attempt; what changes is that re-electing one an earlier
attempt failed to solve the task on has to say why, and an answer that repeats it
silently is rejected as invalid output rather than accepted as a route. An issue
can simply be hard, and a Run that demoted a route for a network blink would
spend the rest of its life avoiding whatever was running at the time.

The record keeps the two axes apart. `wrapper.routing.resolved` carries
`lifecycle_position` and `attempt` beside the elected `model` / `effort` /
`context_tier`, plus the `prior_attempts` the election was handed and any
`repeat_justification` it gave — so a reassessed retry that re-elects the same
configuration is still tellable from a first election that happened to agree.
Nothing changes inside a session that is already running: the route is fixed for
that Agent, and reassessment happens at the next Pickup.

Long-running advancing work keeps a bounded assessment history: earlier
task-solving failures stay visible alongside recent advances, and omitted
advances still count toward the session ordinal and invalidate reuse.
Progress neither spends nor refunds lifecycle attempts. An explicitly configured
Static escalation rung wins even when it equals the Run default.

Rolling dispatch retains its existing one-Lane-per-issue collision guard. A
permitted retry of Lane work is reassessed when a later serial Pickup admits
it; dynamic routing does not create another Lane or guarantee a retry that the
scheduler has not admitted.

The same remote-placement refusal applies: `route_policy = "dynamic"` with a
non-`local` `execution_host` is refused before any work, for the reason above.

### The Task-type classifier's pair

`classifier_model` / `classifier_effort` (env: `GIT_LOOPY_CLASSIFIER_MODEL`,
`GIT_LOOPY_CLASSIFIER_REASONING_EFFORT`) name the pair the **Task-type
classifier** and **Bump-class classifier** run on. At **Pickup**, each reads an
unlabelled issue's own content: the former writes a `task-type:` label for
**Routing** to read, and the latter writes a `semver:` label for the **Release
line** to read (ADR-0029, ADR-0052).

Two properties are worth knowing before you set it:

- **It is deliberately not the run-wide `model`.** Borrowing that would let your
  run-wide default decide every issue's task type, and so every routed pair — an
  unmeasured prior governing every routing decision, and one that would show up
  nowhere as a routing input. Leaving these keys unset does **not** fall back to
  `model`; it falls back to the **cheapest pair on the live roster**.
- **The taxonomies are closed.** A proposal outside the seven `task-type:` keys
  or four `semver:` keys is refused, never written. A Bump class the classifier
  cannot determine is reported rather than defaulted.
- **What they infer is written back to your tracker.** The proposed key is
  applied to the issue as a `task-type:` or `semver:` label, unattended and with
  no review step — that is what makes the corpus inspectable, correctable and
  reusable instead of re-inferred on every run. An issue that already carries a
  valid label is never relabelled; the check is re-asked of the tracker
  immediately before the write, so a label you apply while a run is classifying
  the issue still wins. The write is idempotent and non-fatal.

Classification spends **AI Credits** like any other session, and that spend is
folded into the run's cost. It never ticks a **Strike** and is never counted as
an **Iteration**, so a classifier that cannot answer can never end an unattended
run.

---

## Calibration (`git-loopy calibrate`)

A **Calibration** measures which model + reasoning-effort pair a **Task type**
should route to, by replaying **Proving tasks** — issues this repository has
already closed — against a price staircase. It spends **AI Credits** to do it.

`calibrate` has three modes: two that report and spend nothing, and one that
measures. Neither report runs a **Trial**, spawns a session, creates a worktree,
writes a `task-type:` label, invokes the **Task-type classifier**, or consumes a
credit.

```bash
# What does this repository's corpus support, and what is routing today?
git-loopy calibrate --status

# What would a Calibration measure, and what would it cost?
git-loopy calibrate --dry-run

# Measure every eligible Task type. Prints the plan, then asks.
git-loopy calibrate

# Re-measure one Task type without paying for all seven.
git-loopy calibrate docs
```

Every mode is **repository-scoped** and refuses outside a git repository — the
**Proving set** *is* this repository's closed history and the **Measured
routing** artifact is a tracked file in it, so off-repo there is nothing to
report and nothing to measure.

- **`--status`** answers *what does my corpus support?* Per Task type it prints
  the current **Routed pair** and the tier that produced it — read through the
  same precedence chain `git-loopy config get` walks, not a second one that
  could disagree — the count of replayable **Proving tasks**, and, for a
  measured pair, whether the live roster now offers a **cheaper unmeasured
  pair** than the measured winner. That last line is the only fact here that can
  change an answer: a staircase is walked cheapest-first, so every rung *below*
  the winner was already measured and rejected; a cheaper pair that is not among
  the walked rungs is one the measurement never saw, and the artifact is stale
  rather than wrong.
- **`--dry-run`** answers *what would this cost?* It prints the candidate
  staircase, the specific Proving tasks each rung would replay, the per-Task-type
  **AI-Credit** and wall-clock ceilings, and the maximum **Trial** count those
  ceilings and that staircase imply.

`--status` also reports whether the **Task-type classifier**'s pinned pair has
moved since the artifact was written. The classifier runs on the cheapest rung of
the live roster, so it moves when the roster does — and a **Proving set**
stratified by one pin but compared against work labelled by another is comparing
across a taxonomy that shifted underneath it, so a pin change recommends a
Proving set refresh.

### Measuring (`git-loopy calibrate [<task-type>]`)

A bare `git-loopy calibrate` measures every **eligible** Task type; naming one
measures exactly that one, so re-measuring `docs` does not pay for all seven. A
Task type is eligible when it has the five admitted Proving tasks a promotion
needs; every ineligible one is **skipped with its shortfall printed**, and the
eligible ones still run.

**A Calibration is always an explicit act.** Nothing else in git-loopy starts
one — not the first Run, not preflight, not a roster change — and a test pins
that as a fact about the call graph rather than a convention. A vendor shipping a
model on a Tuesday must not turn your unattended overnight Run into a benchmark
suite. Routing improves when you ask for it.

Before anything is spent it prints the plan — the Task types, the candidate
staircase, the Proving tasks, both ceilings and the maximum **Trial** count —
through the *same* renderer `--dry-run` uses, and then asks:

- On an **interactive terminal** you are asked to confirm. Declining runs no
  Trial, creates no worktree and spends nothing.
- On a **non-interactive terminal** it refuses unless you passed `--yes`. You
  typed the command; a script that inherited it did not, and the two are
  indistinguishable from inside the process.

**It measures for every mode.** A pair a Calibration fixes is applied at the
**Pickup** of whatever works the issue, so a serial Run spends the measurement
exactly as a Parallel one does (ADR-0037). Until then `calibrate` refused a
serial run outright, because the pair it measured would have been discarded.

Progress is printed as rungs are walked and Trials complete — plain lines with
no cursor control, so `git-loopy calibrate --yes | tee calibration.log` is a
readable log rather than a file full of escape sequences. Costs are **AI
Credits** throughout; no USD figure appears anywhere, and a Trial the harness did
not bill reports `unknown AI Credits` rather than zero.

`GIT_LOOPY_CALIBRATE_CONCURRENCY=N` runs N Trials at once, each in its own worktree.
A rung's first Trial is a probe run alone, so a rung that fails costs what it
costs serially.

**What it writes**, once, at the end, into `git-loopy/routing.measured.toml`:

| Outcome | What lands in the artifact |
| --- | --- |
| A rung went five of five | `measured`: the winning pair, every rung walked, the Proving tasks measured, and the provenance stamp |
| An **AI-Credit** or wall-clock ceiling stopped the walk | `incomplete`: the rung reached out of the number available, and **no pair** — the incumbent keeps routing. The summary names which ceiling stopped it |
| Every rung was walked and none was unanimous | `incomplete`, same shape — a finding, so the next operator does not buy the identical walk |
| You interrupted it | Whatever was measured is kept, recorded as unfinished, no winner published, and `calibrate` exits `130` |
| The Task type was skipped, or the harness reported no credits | **Nothing** — the incumbent record is left exactly as it was |

The artifact is a **tracked file**, so the result arrives as a diff you can read,
question and `git revert`. That is the whole reason it is committed rather than
cached. No worktree, branch or temporary state survives completion, failure or
interruption.

### A Run tells you when re-calibrating could change an answer

At **Run** preflight — every Run, before any work starts — git-loopy holds the
live roster against the artifact and warns on stderr in exactly three cases: a
**cheaper unmeasured pair** below a measured winner, a measured winner the roster
no longer offers, and a moved classifier pin. Anything else is silent, and that
silence is the point: a warning that fires on routine vendor churn trains you to
ignore it, and under *cheapest that clears the bar* a **dearer** new model is
structurally incapable of winning while the incumbent still passes. A vendor
shipping a flagship model on a Tuesday therefore produces nothing.

Four things it deliberately does not do:

- **It never starts a Calibration.** A vendor's release schedule must never
  become a trigger for your spend. The notification names
  `git-loopy calibrate --status` and `git-loopy calibrate`; you decide.
- **It never stops a Run.** An unreachable roster, an absent **Rate card**, an
  absent artifact and a malformed one all end in silence or one warning.
  Observability is not a precondition for doing work.
- **It costs no extra round trip.** The comparison reads the same live listing
  the Rate card already resolved, and a repository with no artifact never touches
  the roster at all.

Three properties are worth knowing:

- **A thin corpus is refused, not measured.** A Task type with fewer than five
  replayable Proving tasks cannot support a Calibration, and `--status` says so
  naming exactly what is missing rather than reporting a number and leaving you
  to compare it against a threshold you would have to know. Measuring against
  three tasks would produce an artifact indistinguishable from one measured
  against fifty.
- **Every count says what it counts.** A **mined candidate** is a closed issue
  that passes the replay rules — a closing commit, a well-formed body, at least
  one changed test path. An **admitted task** is one that has since been
  *replayed with its real historical fix* and shown to fail before it and pass
  after, which is the property that makes a replay mean anything and the only
  thing that filters out a base commit that was red for unrelated reasons. These
  are different numbers, the second is never larger than the first, and the
  reports never print one where the other belongs. Admission runs tests, so it
  costs wall clock and no AI Credits, and neither `--status` nor `--dry-run` can
  reach it.
- **Costs are in AI Credits, never dollars.** A credit is the unit the platform
  bills in and the unit the run log already records; converting to currency would
  invent a rate that changes without notice.

### A pair that stops making progress is demoted

A **Calibration** measures five **Trials**. That is a deliberately thin sample,
and it is affordable only because production catches what it gets wrong: the
search does not have to be right, it has to be cheap and **reversible**.
**Demotion** is the reversal.

At the end of every Parallel **Run**, git-loopy counts each **Routed pair**'s
**Lane contributions** that finished without publishing anything. A pair that
reaches `demotion_threshold` such contributions in one Run (default **3**) loses
its **Measured routing** entry, and the Task type is assigned **the next pair up
the price staircase**.

Up, not down, and into a rung nobody measured. Cheapest-first stops at the first
rung that passes, so every measured rung sits *below* the winner and failed — the
set of measured pairs above it is empty by construction. The replacement is
therefore written under the `provisional` status carrying the pair it replaced
and the count that triggered it, so an unmeasured pair can never be read as
evidence, and `config get routing` attributes it to its own
`provisional (unmeasured)` tier.

It is deliberately narrow:

- **It is not the Strike counter.** Strikes are one Run-scoped counter every Lane
  shares, and any Lane's progress resets it, so a good pair's commit erases the
  strikes a bad one was accumulating. Demotion counts per pair; the Strike limit
  keeps its own unchanged job of ending a Run that is going nowhere.
- **It never demotes a hand-written entry.** A `[routing]` entry you typed is your
  decision, however badly its pair performs, and this system does not overrule
  those — the artifact is only consulted where the entry actually in force is the
  one it wrote.
- **It notifies; it starts no search.** A demoted Task type prints that it needs
  re-calibrating and names `git-loopy calibrate <task-type>`. An implicit trigger
  would turn an unattended overnight Run into a benchmark suite.
- **It steps once.** A pair already `provisional` is reported, not stepped again,
  so a bad streak cannot walk a Task type to the top of the staircase unattended
  while its re-calibrate notification goes unanswered.
- **It writes and commits once**, naming only `routing.measured.toml`, after the
  Run has ended and no Lane is running. The commit is the review: read it, and
  `git revert` it if you disagree.
- **Nothing demotes in serial mode**, where nothing routes.

---

## Prompt resolution

The prompt loaded each iteration resolves like the model/effort config —
**project > global > packaged default** (ADR-0006), first hit wins:

1. **project** — `<repo-root>/git-loopy/PROMPT.md` (lowercase `prompt.md` is also
   accepted, for case-sensitive filesystems).
2. **global** — `$XDG_CONFIG_HOME/git-loopy/PROMPT.md` (honouring
   `$XDG_CONFIG_HOME`), else `~/.config/git-loopy/PROMPT.md`.
3. **packaged default** — a `PROMPT.md` shipped **inside the wheel**, so a bare
   run in a repo with no `git-loopy/` folder still has a working prompt.

Only the packaged default is guaranteed present; drop a `PROMPT.md` into either
scope to override it (a project file overrides a global one, which overrides the
packaged default). The seam lives in `git_loopy.loop._read_prompt`.

---

## Supported models

`GIT_LOOPY_MODEL` accepts any id the Copilot CLI exposes, but the runner ships a
capability matrix (`git_loopy/config.py` → `MODEL_REASONING_EFFORTS`)
that gates `GIT_LOOPY_REASONING_EFFORT` per model. A model not in this table is
**warned** about once and passed through unchanged (the CLI is the final
authority). A model with an empty effort set is sent **no** reasoning
effort — the CLI hard-rejects `session.create` otherwise.

The accepted effort vocabulary is `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, and `max`. ModelSelectionMode and `init` offer only the subset the
selected model advertises. The string `none` is an explicit request for no
reasoning; an omitted effort remains unset so the backend can choose.

| Model id                      | Reasoning efforts                        |
| ----------------------------- | ---------------------------------------- |
| `auto`                        | _(none - effort forced unset)_           |
| `claude-sonnet-5`             | `low` `medium` `high` `xhigh` `max`      |
| `claude-sonnet-4.6`           | `low` `medium` `high` `max`              |
| `claude-sonnet-4.5`           | _(none - effort forced unset)_           |
| `claude-haiku-4.5`            | _(none - effort forced unset)_           |
| `claude-opus-5` (default)     | `low` `medium` `high` `xhigh` `max`      |
| `claude-opus-4.8`             | `low` `medium` `high` `xhigh` `max`      |
| `claude-opus-4.7`             | `low` `medium` `high` `xhigh` `max`      |
| `claude-opus-4.6`             | `low` `medium` `high` `max`              |
| `gpt-6-astra`                 | `low` `medium` `high` `xhigh` `max`      |
| `gpt-5.5`                     | `none` `low` `medium` `high` `xhigh`     |
| `gpt-5.4`                     | `none` `low` `medium` `high` `xhigh`     |
| `gpt-5.3-codex`               | `low` `medium` `high` `xhigh`            |
| `gpt-5.4-mini`                | `none` `low` `medium` `high` `xhigh`     |
| `gpt-5-mini`                  | `low` `medium` `high`                    |
| `gemini-3.1-pro-preview`      | `low` `medium` `high`                    |
| `gemini-3.6-flash`            | `minimal` `low` `medium` `high`          |
| `gemini-3.5-flash`            | `minimal` `low` `medium` `high`          |
| `gpt-5.6-luna`                | `none` `low` `medium` `high` `xhigh` `max` |
| `gpt-5.6-sol`                 | `none` `low` `medium` `high` `xhigh` `max` |
| `gpt-5.6-sol-fast`            | `none` `low` `medium` `high` `xhigh` `max` |
| `gpt-5.6-terra`               | `none` `low` `medium` `high` `xhigh` `max` |
| `grok-4.5`                    | `low` `medium` `high`                    |
| `grok-4.6`                    | `low` `medium` `high` `xhigh`            |
| `mai-code-1.1-flash`          | `low` `medium` `high`                    |
| `mai-code-1-flash-picker`     | `low` `medium` `high`                    |

This fallback covers all 19 models returned by the SDK-pinned CLI `1.0.83`
on the upgrade account, plus seven retained compatibility entries:
`claude-sonnet-4.6`, `claude-sonnet-4.5`, `claude-opus-4.6`, all three Gemini
rows, and `mai-code-1-flash-picker`. Those seven were not offered by that
account; their retained efforts are not a claim of current availability.
`gemini-3.8-flash` is not one of those existing entries and remains off-roster:
its configured model and effort pass through with a warning rather than being
gated against an unverified effort set. Live Harness capabilities remain
authoritative for the Run.

The retired
`claude-opus-4.5` id and the renamed `mai-code-1-flash-internal` id are not
official choices; persisted legacy ids still use the unknown-model
warn-and-pass-through path so the Copilot CLI remains the final authority.

Cost is not derived from this list. It is the **AI Credits** the harness
reported billing (ADR-0026), so a model git-loopy has never heard of costs
exactly what the harness says it cost — and a Run the harness did not bill
renders `—` rather than a fabricated estimate.

---

## Observability artefacts

The Python runner writes three artefacts per invocation, all under the
**repo root**. Directories are created lazily on first write; a process
that exits before producing any output leaves no on-disk footprint. The
runner appends `.git-loopy/` to `.gitignore` once (idempotent) on first run
so the artefacts don't get accidentally committed.

| Artefact          | Path                                            | Format                                                                                                            |
| ----------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Event log         | `.git-loopy/logs/<iso>-<run_id>.jsonl`              | Append-only JSONL, one envelope per line, replay-grade. Flushed after every write so a crash leaves a partial-but-parseable file. |
| Run summary       | `.git-loopy/runs/<iso>-<run_id>.json`               | Per-iteration counter rollup (duration, tokens, tool / skill / commit / auto-closure / strike counts). Written on close. |
| Process diag.     | stderr **and** `.git-loopy/logs/<iso>-<run_id>.log` | Human-readable diagnostics. The stderr stream is primary; the `.log` file is the mirror.                          |

`<iso>` is a filesystem-safe `YYYY-MM-DDTHH-MM-SSZ` timestamp;
`<run_id>` is a 26-char Crockford-base32 ULID. The three files for a
single invocation share the same stem, so `ls .git-loopy/logs/` and
`ls .git-loopy/runs/` line up by-eye.

The run-summary JSON schema is documented at the top of
[`git_loopy/persist.py`](git_loopy/persist.py).

---

## Cost figures

Cost is what the harness reported billing, denominated in **AI Credits** —
never tokens multiplied by a price table git-loopy maintains itself
(ADR-0026). The premium-request count and the cache read/write split ride
alongside it, on the same telemetry.

- git-loopy publishes **no USD figure**. The harness's own Rate card is
  denominated in Credits too, and nothing on any surface the kit reads is
  dollar-denominated by its schema, so a USD column would need a
  Credits-to-USD rate that exists nowhere. ADR-0018 rejected that
  operator-supplied constant and ADR-0026 upheld the rejection.
- The list-price estimate, the packaged `pricing.toml` and the
  `GIT_LOOPY_PRICING_FILE` override are **deleted** (#330). Nothing replaced
  them: an estimate git-loopy computed itself was never the bill.
- An unreported figure renders `—` (em dash), **never** `0`, so a consumer
  can tell "nobody said" from "free". A total is reported only when every
  figure summed into it is known, so an unreported Iteration never silently
  understates a Run.
- An Orchestrator that cannot report Cost at all says so in its own words,
  which is a different fact from a Run that could have reported it and saw
  nothing.

---

## OpenTelemetry tracing (opt-in)

Install the extra and set either env var:

```bash
uv sync --project git-loopy/python --extra otel

# Activate by either of:
GIT_LOOPY_OTEL_ENABLED=1 uv run --project git-loopy/python git-loopy
# or
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run --project git-loopy/python git-loopy
```

When enabled, the runner emits the following span tree per invocation:

```
git_loopy.run                          (root, one per git-loopy invocation)
└─ git_loopy.iteration                  (attrs: iter, issue, issues)
   ├─ git_loopy.collect_issues
   ├─ git_loopy.session                 (wraps the SDK session lifecycle)
   │  └─ <SDK-emitted spans>             (nest here via W3C context propagation)
   └─ git_loopy.enforce_closures
```

When disabled (default), `opentelemetry` is never imported and the
runner pays **zero observability cost**.

---

## See also

- git-loopy root [`README.md`](../../README.md) — positioning, the loop
  engineer, the skill catalog, and the complete workflow
  (`/grill-with-docs`, `/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`).
- [`docs/runners.md`](../../docs/runners.md) — the full runner reference:
  per-iteration flow, exit conditions, commit-message contract, and skill
  routing.
- [`git-loopy/PROMPT.md`](../PROMPT.md) — the project prompt override loaded each
  iteration (see [Prompt resolution](#prompt-resolution) for the
  project > global > packaged chain).
