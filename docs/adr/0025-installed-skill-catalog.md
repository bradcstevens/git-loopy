# Install the Skill catalog instead of shipping it

**Status:** accepted

**Supersedes the redistribution half of** [ADR-0023](0023-pinned-external-skill-catalog.md).

ADR-0023 named `bradcstevens/git-loopy-skills` the source of record and pinned it
to an immutable revision, then kept a *vendored copy* of that catalog inside the
wheel so a Run could resolve Skills from disk. That copy is the problem this ADR
removes.

A vendored catalog is a fourth thing that can be edited: it drifted from both the
pin it claimed to be cut from and the `.copilot/skills/` tree it was generated
from, and nothing in a Run could tell. It also made the wheel a redistributor of
someone else's licensed work, and made every catalog fix wait for a git-loopy
Release. Meanwhile the layer that was supposed to make offline safe — "the wheel
always has Skills" — only guaranteed that *some* Skills were present, not the
ones the pin names.

We will stop shipping Skills. git-loopy installs the pinned catalog into its own
config scope at setup, and refreshes that install at the start of every Run.

## Decision

### One catalog, in git-loopy's config scope

- A git-loopy distribution carries **no Skills**. It carries the pin
  (`skill_source.json`), which is what an install resolves against.
- The installed catalog lives at `<config-home>/git-loopy/skills/` —
  `$XDG_CONFIG_HOME` if set, else `~/.config` — the same rule the rest of the
  global scope follows, so the catalog is a neighbour of the `config.toml` that
  governs it rather than a second convention to learn.
- That directory is a **Skill root**: it holds Skills and nothing else. The
  private git checkout the install is cut from (`skill-catalog/`) and the install
  record (`skill-catalog.json`) are siblings of it, not contents, so nothing
  bookkeeping-shaped can ever be mistaken for a Skill.
- It is machine-wide, not per-project. Scope decides where an operator's
  *settings* live; it has no say over where Skills live.

### Installed at setup, refreshed every Run

- `git-loopy init` installs the catalog **before it collects anything**. The
  Skill policy the operator is about to choose is a choice among the installed
  catalog, so an empty one would offer nothing and leave every later Run without
  Skills.
- Every Run refreshes the install from the pin before its Skill preflight. A Run
  therefore executes the revision this distribution stands behind, rather than
  whatever was last left on disk.
- A refresh that finds the recorded revision already installed, and the Skill
  root still holding what the record claims, opens no connection. The steady
  state costs a directory walk.
- An install is **wholesale**: the new revision is staged beside the Skill root
  and swapped into place, never merged into it. A Skill retired upstream is gone
  after the next Run, and an interrupted install never leaves half a catalog.
- The record stores a digest of the installed tree, so a hand-edited Skill is
  detected and *repaired* on the next Run rather than reported as current. The
  installed catalog is git-loopy's to own; an operator who wants different
  behaviour changes the pin, not the files.

### Offline

- An unreachable upstream is a **warning, not a failure**: the Run continues on
  the installed catalog and says which revision that is and whether it is the
  pinned one.
- A machine with *no* installed catalog and no way to reach upstream is a hard
  failure at preflight. That Run has no Skills to expose, and discovering it one
  Iteration later is strictly worse than refusing to start.
- Nothing unproven is ever installed. Acquisition still validates the revision,
  the layout, the licence, and the provenance material exactly as ADR-0023
  specifies; a validation failure leaves the previous install untouched.

### The installed catalog is git-loopy's only Skill source of its own

- The **one** place git-loopy itself reads Skills from is the installed catalog.
  The consuming project's `<repo>/.copilot/skills/` is no longer read: a
  repository can no longer hand a Run a Skill by committing a file.
- This replaces ADR-0023's two git-loopy-owned layers with one:

| Layer | Where it lives | Reached |
| --- | --- | --- |
| **External catalog** (source of record) | `bradcstevens/git-loopy-skills` at the pinned revision | at setup, and at the start of every Run |
| **Installed catalog** | `<config-home>/git-loopy/skills/` | from disk, every Iteration |

- The external agent client keeps reporting **its own** sources — personal,
  plugin, built-in, and custom, which is where the operator's
  `~/.copilot/skills/` shows up — and those stay in the **Skill catalog**. This
  ADR does not narrow them, because they are the operator's deliberate machine
  configuration rather than something a cloned repository can plant. What bounds
  them is unchanged: the closed-world **Skill policy** decides which are exposed,
  and a name the installed catalog also provides resolves to the installed copy.
- Setup copies no Skill into a project. A consuming repository's `.copilot/`
  tree is its own business and git-loopy no longer writes to it or reads from it.

## Considered options

- **Keep the vendored fallback and install on top of it** — rejected because two
  catalogs with a precedence rule is exactly the ambiguity this ADR exists to
  remove. A fallback is only reachable when the install failed, which is the case
  the warning already covers, and it would go stale invisibly in every other
  case.
- **Install into `~/.copilot/skills/`** — rejected because that directory is
  Copilot's, shared with everything else the operator has put there. A wholesale
  swap would delete their work, and anything short of a wholesale swap
  reintroduces merge semantics and drift.
- **Keep reading the consuming project's `.copilot/skills/` as an override** —
  rejected because a Run's Skills would again depend on the repository it is
  pointed at, so "which Skills ran" stops being answerable from the pin. A
  project that needs a different catalog is asking for a different pin.
- **Refresh on a timer, or once a day** — rejected because a Run is the unit of
  work whose reproducibility matters. Per-Run is both simpler to state and
  cheaper than it sounds: the no-op path opens no connection.
- **Fail the Run when upstream is unreachable** — rejected because it would make
  every autonomous Run depend on network reachability for content it already has
  on disk, which is the failure mode ADR-0023 was right to avoid.

## Consequences

- The wheel stops redistributing anyone else's licensed work. The third-party
  notice's job becomes naming the source an operator installs from, not
  reproducing a licence for files that are not there.
- A catalog fix reaches operators on the next pin bump rather than the next
  git-loopy Release, and reaches an individual machine on that machine's next
  Run.
- `scripts/sync_skills.py`, `SKILL_DENYLIST`, and the packaged-vs-upstream drift
  report all go away: there is no second copy to keep in sync or compare against.
- A Run now touches the network before its first Iteration. The steady state is a
  no-op, and the failure mode is a warning, but "a Run never opens a connection
  for a Skill" — ADR-0023's phrasing — is no longer true and this ADR is what
  says so.
- Skills that git-loopy itself depends on must exist upstream. Anything the
  Run instructions require, including the Continuation contract the Wrapper
  expects, is now a claim about the pinned repository rather than about this
  one — and the Required-Skill preflight is what makes that claim checkable.
- The `project` Skill source kind no longer has a producer. It stays in the
  vocabulary as a historical value the Event schema may still carry, but nothing
  in a Run can emit it.
