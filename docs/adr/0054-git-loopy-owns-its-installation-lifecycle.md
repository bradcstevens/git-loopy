# git-loopy owns its installation lifecycle

**Status:** accepted

Settles whether the `git-loopy` command may update, upgrade, and uninstall itself, and where the
line between those three falls. The starting request was a list of eight commands. Half of them
turned out to be one decision — the CLI claiming a domain its own glossary currently pushes away —
and the other half turned out to already exist or to be arguments about `--help`.

`CONTEXT.md`'s **init** entry draws the line this ADR moves: *"install is the separate act of
putting the `git-loopy` command on PATH."* Installation has never been the CLI's business. It is
now, with a boundary that says exactly how far.

## What was already true

Six facts shaped every decision below. Four of them are latent defects that no command currently
owns.

**Two different products install a command named `git-loopy`, into the same directory.**
`uv tool install "git+…#subdirectory=git-loopy/python"` (`README.md:103`) puts the Python Runner's
shim on `PATH`. `install.sh` puts the shell Orchestrator's launcher into
`${XDG_BIN_HOME:-$HOME/.local/bin}` under the same name (`shell/install.sh:49,65`). The launcher is
not a copy: it is *"a small shim that `exec`s **this clone's** `git-loopy.sh` by absolute path — the
installer never copies the Orchestrator out of the tree"* (`shell/install.sh:9-11`). So one name
resolves to two artifacts with two unrelated upgrade procedures, and last-installer-wins decides
which.

**The install channel must carry Release identity.** The documented command pins a published Release
tag rather than resolving the default branch. `read_runtime_release_version` reads the checked-in
`VERSION` file (`release_version.py:76-79`), which is a source constant, so an unpinned channel
could otherwise leave two machines reporting `git-loopy 0.9.0` while hundreds of commits apart.
The documented Release channel keeps that identity coherent with ADR-0052,
`release_trust.py`, `conformance/release-version.json`, and a `homebrew.py` that refuses a formula
whose fields drifted from the Release it names.

**A scaffolded `PROMPT.md` shadows the packaged one forever.** Precedence is *"project, global, then
packaged"* (`prompt.py:204`), and the scaffold's frontmatter declares only `required-skills` — no
version. An operator who ran `init` at v0.5.0 has been running v0.5.0's Run instructions ever since,
silently, and nothing detects it.

**A Release can lock an operator out of their own Config.** `config.py:286-296` records it plainly:
the closed task-type taxonomy (#375) *"refuses an unknown key at every write seam, but a Config
written before it already carries one, and every surface that reads that file — a Run, `config
list`, `config get`, `config routing set` — is refused by the same key. Without a stated remedy the
operator is locked out of the Config they have to correct, and hand-edited TOML is the only way
back."*

**The Skill catalog is the one piece of state that already solved this.** Every Run refreshes the
installed catalog (ADR-0025), and `skill_install.py:9-11` records *"what is installed, and from
where"* in `skill-catalog.json`. Nothing else git-loopy installs carries provenance.

**Project scope is version control, and the tracker is other people's.**
`git-loopy/config.toml` and `git-loopy/PROMPT.md` are tracked files. `labelscmd.run_labels` refuses
without a repo root (`labelscmd.py:83`) and writes to a shared remote.

## The decisions

**The boundary is the distribution.** `update` refreshes the machine-local state git-loopy
installed; `upgrade` replaces the distribution itself and then chains `update`, because landing a
new Release is precisely the event that invalidates the scaffolded assets. `uninstall` removes both.

`update` therefore owns the `PROMPT.md` override, `config.toml`, the Skill catalog, and the
`git-loopy-tui` helper. It reaches the network and it is not repo-scoped.

**Applying the Label vocabulary stays out of `update`.** It was the one candidate on the wrong side
of the line — not machine-local, not even on the machine. Folding it in would have made `update`
require a repo root and `gh` auth, and would have meant an operator typing `git-loopy update` to
unstick a stale `PROMPT.md` also mutated a repository their team shares. `labels --apply` remains
the only door to the tracker.

**`upgrade` moves between published Releases.** `git-loopy upgrade` resolves the newest Release and
installs that tag; `--to`/`--ref`/`--edge` are the escape hatches for a named Release or an
unreleased commit. Because an `--edge` install can sit on a commit whose `VERSION` lies, `info`
reports the resolved commit and whether it is published — not the `VERSION` string alone.

This makes the documented install command wrong, not just `upgrade`. A first install that is
unversioned followed by upgrades that are versioned is an incoherent story, so pinning the
documented channel to a Release is part of this decision rather than a follow-on.

**`upgrade` moves exactly one artifact: the one it is running from.** It resolves its own channel
from its own executable path, performs the upgrade by `exec`ing the owning package manager — which
replaces the process rather than writing over a file Windows has locked — and when the channel
cannot be *proven*, it refuses and prints the exact command to run.

Refusing looks like a cop-out and is not. The alternative is a tool running `git pull` inside a
clone it does not own, over an operator's uncommitted work, because it guessed. The name collision
above means that guess is not hypothetical.

**`uninstall` is machine-local and never edits a repository's contents.** By default it removes the
executable (under the same prove-or-instruct rule), the global config-home, the installed Skill
catalog and its record, and the helper. `--all` additionally removes project scope and the
`.git-loopy/` logs.

Project scope is excluded from the default because those files are tracked. Deleting them is not
uninstalling; it is an unstaged source change appearing in someone's `git status`, in files their
teammates share, with reach that depends on which directory the operator happened to be standing
in. Logs are excluded because they are the only record of what ran and what it cost.

**A live Lane worktree is a hard refuse, not a cleanup.** #452 exists to salvage a *dirty* Lane
workspace, which establishes that uncommitted agent work in a Lane is an expected state rather than
an anomaly. `uninstall` refuses while one is live and points at the sweep (#455).

**Scaffolded assets carry provenance, and prose is never clobbered.** `init` records the Release and
a digest of what it scaffolded, mirroring `skill-catalog.json`. That single record makes "is this
`PROMPT.md` customized?" decidable: digest matches, it is untouched and safe to replace; digest
differs, it is the operator's. For the customized case `update` reports the drift and shows what
changed upstream — it does not merge and it does not overwrite. An override with no record predates
the record and is treated as customized, which is the fail-safe direction.

**Config is repaired; prose is reported.** The asymmetry is deliberate. `config.toml` is structured
against a closed taxonomy, so a violation has exactly one correct resolution and no authorial intent
to preserve. `PROMPT.md` is prose the operator was explicitly invited to write, and merging
instructions that steer an autonomous agent is not something a tool should attempt unattended.

The lockout breaks the symmetry independently: report-only on `PROMPT.md` still leaves a working
tool, whereas report-only on a refused Config leaves the operator exactly where `config.py:286-296`
already puts them. `update` repairs only what is mechanically decidable, backs the file up first,
prints what changed, and offers `--dry-run`. `task_type_refusal` can finally name a remedy other
than hand-editing TOML.

## Consequences

**This is Python-Runner-only, and deliberately absent from the Wrapper contract.** The shell
Orchestrator parses no verbs at all, so family parity here is not "add eight commands" but "build a
subcommand dispatcher in bash and again in PowerShell, then host the least valuable commands on it."
It would ship a `doctor` diagnosing a Config that member cannot manage and a `commands` listing a
surface missing `init` and `config` — both of which are ADR-0013 phase 3, which is not done.

Declaring the obligation in the contract now and implementing it later was considered and rejected,
because that is the exact shape of #444, #431, and #435 — three open issues, each about something
written into the contract that no member implements. A contract row lands when a second member
implements, not before. Family parity is tracked as unmilestoned backlog blocked on phase 3.

**`git-loopy config` is unaffected.** It already ships `set`/`get`/`list`/`path`/`edit`/`routing`
and is pinned at `CONTEXT.md:601`. It needed discoverability, not code.

**`commands` is justified by shell completions today, not Conformance.** The Conformance
justification — asserting every member exposes the same surface — only becomes true if family parity
ever lands. `--help` becomes categorized in the same change, so the thing operators reflexively type
is the good one.

**`update` is not the only command that repairs.** ADR-0055's amendment admits `doctor --apply`,
following the `git-loopy labels` house pattern, and divides the two by cause: a repair for something
a Release caused belongs to `update`; a repair for something broken independently of a Release
belongs to `doctor --apply`. The **Config** repair described above stays with `update` under that
rule, because a retired taxonomy key is Release-caused.
