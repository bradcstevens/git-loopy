# The closed-world Skill policy

> Establish, inspect, change, migrate, and troubleshoot exactly which **Skills**
> a git-loopy **Run** may load.

Read this when you need to *operate* the Skill policy. The decision behind it is
[ADR-0015](adr/0015-closed-world-skill-policy.md); the language-neutral
requirement every Runner answers to is
[contract §17](wrapper-contract.md#17-closed-world-skill-policy-skill-policy-rollout-must);
the first-time setup path is
[`docs/skills-setup.md`](skills-setup.md#12-recommended-install-the-workflow-skill-catalog-with-git-loopy-init).

---

## Six words that are not synonyms

Most Skill-policy confusion is one of these six being read as another. They are
defined in [`CONTEXT.md`](../CONTEXT.md); what follows is what each one means
when you are holding a terminal.

**Skill catalog** — everything git-loopy can *see*. Discovery asks the same
Copilot CLI runtime the Run will use, under the same `COPILOT_HOME`, and adds
git-loopy's explicit project source (`<repo>/.copilot/skills`) and its packaged
fallback. Discovery reads **metadata only**: a name, a description, a source,
and Copilot's own enabled flag. Being in the catalog does **not** make a Skill
loadable — no instructions, scripts, or resources are read for a Skill the
policy leaves out. Inspect it with `git-loopy skills list`.

**Skill policy** — the closed-world set of canonical names one *scope* persists.
This is the thing you edit and commit. It is an **allowlist**: a Skill that
appears in a new Copilot install, a new plugin, or a git-loopy upgrade stays
disabled until you enable it by name. A project policy **replaces** the global
one rather than merging with it, and an explicitly empty policy
(`enabled_skills = []`) is a real, deliberately empty policy — not an absent
one.

**Skill baseline** — Copilot's own enabled/disabled selection, copied **once**
to seed a first policy. It is a starting point, never a live authority: after
seeding, changing a Skill's state in Copilot CLI changes nothing about a Run
until you run `git-loopy skills sync` and confirm the diff.

**Effective Skill policy** — what one specific Run actually gets: the selected
base policy, plus that invocation's temporary overlays, minus the deprecated
legacy deny guards. It is resolved **once** at preflight and frozen for every
Iteration and every parallel Lane in the Run. This is the only one of the six
that is per-Run.

**Minimal Skill policy** — exactly the **Required Skills** and nothing else. It
is the answer whenever no base policy is in effect, which makes a first CI Run
reproducible: it consults no personal or machine-global Skill source, because
Required Skills come from the packaged catalog.

**Required Skill** — a name the active `PROMPT.md` declares in its
`required-skills` frontmatter. The packaged prompt requires `diagnosing-bugs`,
`prototype`, `tdd`, `codebase-design`, `resolving-merge-conflicts`, and
`code-review`. A Run whose effective set omits one is **invalid** and stops
before its first work session — it is never silently restored. A custom prompt
with no `required-skills` metadata inherits the packaged list and warns until it
declares its own (an explicitly empty list is a valid declaration).

### Consulted Skills are a different fact entirely

A Run **Summary** records `skills_consulted` — the sorted, distinct Skills an
Iteration actually invoked, pinned by
[`skill-consultation.json`](../git-loopy/conformance/skill-consultation.json).
That is *observed behaviour after the fact*. The Skill policy is *permission
before the fact*.

They answer different questions and move for different reasons:

| | Skill policy | `skills_consulted` |
| --- | --- | --- |
| Question | Which Skills **may** this Run load? | Which Skills **did** this Iteration use? |
| Scope | One Run, frozen at preflight | One Iteration, accumulated as it runs |
| Where it lives | `config.toml`, env, flags | Run Summary + `usage`/rollup counters |
| Changing it | `git-loopy skills edit` | not editable — it is a measurement |

A Skill absent from `skills_consulted` is not evidence of a policy problem: the
agent simply had no task that matched it. A Skill the policy never enabled can
never appear there at all.

---

## Configuring a policy

Four surfaces can change a Run's capability set. They are applied in this order,
and only the first two are persisted:

| # | Surface | Scope | Persisted |
| --- | --- | --- | --- |
| 1 | `enabled_skills` in the **project** `config.toml` | this repository | yes — commit it |
| 2 | `enabled_skills` in the **global** `config.toml` | this machine | yes |
| 3 | `GIT_LOOPY_ENABLED_SKILLS` | one Run | no |
| 4 | `--enable-skill` / `--disable-skill` | one Run | no |

### 1–2. `enabled_skills` — the persisted allowlist

```toml
# <repo>/git-loopy/config.toml  (project scope), or
# ~/.config/git-loopy/config.toml  (global scope)

enabled_skills = [
  "code-review",
  "codebase-design",
  "diagnosing-bugs",
  "prototype",
  "resolving-merge-conflicts",
  "tdd",
]
```

A **project** policy *replaces* the global one — it does not merge with it. The
global policy applies only when the project Config has no `enabled_skills` key
at all. Three states that look alike are not:

| Config state | Resolves to | Why |
| --- | --- | --- |
| key absent | the next scope down, ultimately the **Minimal Skill policy** | absence means "not answered here" |
| `enabled_skills = []` | a real, empty policy | you answered: nothing |
| `enabled_skills = ["tdd"]` | exactly `tdd`, if it satisfies the Required Skills | you answered: this |

Because absence means inheritance, an empty list is the *only* way to say "this
scope deliberately enables nothing". A policy that omits a **Required Skill**
fails preflight either way — see [Troubleshooting](#troubleshooting).

A project policy is a **shared repository contract**. Every collaborator must be
able to resolve every name in it, which is why an enabled project-sourced Skill
must be git-tracked: a policy naming a Skill that exists only in your working
tree breaks for everyone else. Naming a *personal* or *plugin* Skill is allowed
and is an explicit dependency — a collaborator without it gets an actionable
preflight failure rather than a quietly narrower Run.

### 3. `GIT_LOOPY_ENABLED_SKILLS` — exact replacement, for one Run

```bash
# Replace the configured base policy outright for this Run.
GIT_LOOPY_ENABLED_SKILLS=tdd,code-review,diagnosing-bugs,prototype,codebase-design,resolving-merge-conflicts \
  git-loopy

# An explicitly empty value is a real empty policy, not "unset".
GIT_LOOPY_ENABLED_SKILLS= git-loopy    # fails preflight: no Required Skills
```

It **replaces** the selected base policy rather than adding to it, and its
presence — not its content — is what counts. Setting it to the empty string is
a deliberate empty policy; to leave the configured policy alone, do not set the
variable at all.

### 4. `--enable-skill` / `--disable-skill` — temporary overlays

```bash
# Add one Skill to whatever base policy is in effect, just for this Run.
git-loopy --enable-skill handoff

# Take one away, just for this Run.
git-loopy --disable-skill prototype

# Both name the same Skill: disable wins. The Run does not load `prototype`.
git-loopy --enable-skill prototype --disable-skill prototype
```

Both flags are repeatable and neither is persisted. They apply *on top of* the
base policy — including a base supplied by `GIT_LOOPY_ENABLED_SKILLS`. When the
two overlays conflict, **disable wins**; the safe direction is the one that
narrows.

Because an overlay does not answer the durable question, `--enable-skill` does
**not** count as having configured a policy: a Config that predates
`enabled_skills` is still offered for [migration](#migrating-a-config-that-predates-the-policy).
`GIT_LOOPY_ENABLED_SKILLS` does replace the base, so it suppresses the offer.

### The deprecated legacy deny guards

`deny_skills`, `GIT_LOOPY_DENY_SKILLS`, and `--deny-skill` are **deprecated**
final guards kept for the migration. They are not a closed-world surface:

```toml
deny_skills = ["handoff"]        # deprecated — prefer omitting it from enabled_skills
```

- They only ever **subtract** from the effective set, and they are *unioned*
  across every tier rather than replaced — a global denial cannot be lifted by a
  project Config.
- They are applied after the overlays, so they are the last word.
- They are not a way to disable a **Required Skill**: a denial that would remove
  one is a validation failure, not a quiet subtraction.

Prefer expressing intent as an allowlist — leave the name out of
`enabled_skills`. The deny guards exist so an installation that relied on them
before ADR-0015 keeps behaving, not as the way to constrain a new Run.

---

## Managing a policy — `git-loopy skills`

`git-loopy skills` owns policy **inspection and mutation**. It never installs or
removes a Skill: acquisition stays with Copilot CLI / plugin tooling
(`/find-skills`, `npx skills find`) or git-loopy's own catalog scaffold step
(`git-loopy init --project` / `--global`). The policy commands only *select from
what already exists*.

Nothing under `git-loopy skills` ever writes Copilot CLI's own settings. The
import is strictly one-way — git-loopy reads Copilot's state and **never writes
back to Copilot**.

### `git-loopy skills list` — inspect

```bash
git-loopy skills list
```

Prints one stable, **path-free** tab-separated row per canonical Skill name, in
sorted name order, under a fixed header:

```text
GIT-LOOPY  COPILOT      REQUIRED  SOURCE    NAME         DESCRIPTION
enabled    unavailable  yes       project   code-review  Review changes.
disabled   enabled      no        personal  handoff      Save and restore context.
enabled    enabled      yes       packaged  tdd          Test-driven development.
```

- **GIT-LOOPY** — `enabled` / `disabled` under the **base** policy this
  installation resolves: `GIT_LOOPY_ENABLED_SKILLS` if set, else the project
  `enabled_skills`, else the global one, else the Minimal Skill policy. It does
  **not** apply this-Run-only `--enable-skill` / `--disable-skill` overlays or
  the legacy deny guards, because those belong to an invocation rather than to
  the installation. To see what one specific Run resolved, read that Run's
  [`wrapper.skill_policy.resolved` event](#the-wrapperskill_policyresolved-audit-record).
- **COPILOT** — Copilot's own state: `enabled`, `disabled`, or `unavailable`
  when Copilot reports nothing for that name. Shown side by side so a
  divergence is visible; it has no authority, and the Run follows the
  GIT-LOOPY column.
- **REQUIRED** — `yes` when the active prompt declares it in `required-skills`.
- **SOURCE** — the winning source kind: `project`, `inherited`, `personal`,
  `plugin` (rendered `plugin:<name>`), `custom`, `builtin`, or `packaged`.

Output is stable and path-free by design, so it is safe to diff between machines
and to paste into an issue: no absolute home-directory paths appear.

Run outside a repository and the workspace-derived rows (`project`,
`inherited`) are omitted — there is no workspace to derive them from.

### `git-loopy skills edit` — change

```bash
git-loopy skills edit              # project scope inside a repo, else global
git-loopy skills edit --project
git-loopy skills edit --global
```

Opens the searchable multi-select picker, validates, then saves **one** policy
atomically. Both renderings of the picker — the full-screen one from the
optional `[tui]` extra and the plain numbered one everywhere else — drive the
same selection model and obey the same rules; the keys for each are in
[`docs/skills-setup.md`](skills-setup.md#the-skill-picker-has-two-renderings-and-one-set-of-rules).
The full-screen picker is **optional, never required**: without the extra the
plain picker runs and returns the same selection.

Two rules are enforced *in the picker*, not at save time:

- a **Required Skill** is marked and locked on — switching it off is refused in
  place, with the reason;
- an enabled project-sourced Skill that is not git-tracked is listed but locked
  off, with the reason, so you can see exactly what needs committing.

A save that would not validate is **refused and names the offending Skill**
rather than being silently corrected, and the refusal is drawn next to the
prompt — a status bar in the full-screen picker, the line above the prompt in
the plain one — so a catalog longer than the terminal cannot scroll it away.

### `git-loopy skills sync` — re-import the Skill baseline

```bash
git-loopy skills sync              # project scope inside a repo, else global
git-loopy skills sync --global
```

This is the *explicit* action that re-reads Copilot's current enabled state.
It prints the exact additions and removals first and **saves only after you
confirm**; cancelling writes nothing.

- It replaces only the Skills Copilot actually reports. git-loopy's packaged
  fallbacks, and any configured name Copilot does not know, keep their current
  state rather than being synced away.
- The proposed policy is validated before it is written, so a sync that would
  disable a Required Skill, enable an untracked project Skill, or leave an
  unresolvable name enabled fails **without touching the Config**.
- When the scope has no policy of its own, there is nothing for the baseline to
  differ from, so sync says so and offers to save the whole baseline as that
  scope's first policy.
- Copilot's settings are **never written**; the flow only ever reads them.

---

## Establishing a policy for the first time

What a starting Run finds decides what happens next. There are exactly three
startup states, and the difference between the first two matters even though
both resolve to the Minimal Skill policy:

| Startup state | What it means | What happens |
| --- | --- | --- |
| `unconfigured` | no Config resolves anywhere | first-run setup establishes the policy |
| `legacy` | Config exists, but the scope it resolves predates `enabled_skills` | a one-time [migration](#migrating-a-config-that-predates-the-policy) is offered |
| `configured` | a scope, or `GIT_LOOPY_ENABLED_SKILLS`, supplies a base policy | the policy is used as-is |

An overlay does not settle the durable question, so `--enable-skill` leaves a
`legacy` Config `legacy`. `GIT_LOOPY_ENABLED_SKILLS` replaces the base outright
and therefore reports `configured`.

### Seeding

| Situation | Seeded from |
| --- | --- |
| First **global** policy, interactively | a fresh **Skill baseline** — Copilot's current enabled state |
| First **project** policy, with a global policy already saved | the inherited **global** policy, not Copilot |
| First **project** policy, with no lower-scope policy | a fresh **Skill baseline** |
| `git-loopy init --yes`, or any unattended path | the **Minimal Skill policy** — no machine state is read |

The global-then-project rule is what makes a project policy a deliberate
*narrowing or widening of your own baseline* rather than a fresh import of
whatever your machine happened to hold that day. To re-import Copilot's state
into a scope on purpose, use `git-loopy skills sync`.

### Packaged fallback defaults

On **first** setup, a packaged Skill that Copilot's inventory does not know
about starts **enabled** — matching Copilot's own behaviour for a newly added
Skill. Once a policy exists, that grace period is over: every later catalog
addition, packaged or not, starts **disabled** until you enable it by name.
This is the closed-world rule doing its job — an upgrade must never widen a Run
behind your back.

### Unattended setup and unavailable inventory

```bash
# CI-friendly: accept every default and never prompt. Persists the
# Minimal Skill policy — the Required Skills and nothing else.
git-loopy init --yes
```

`git-loopy init --yes`, an unconfigured non-interactive Run, and a first setup
whose Copilot inventory cannot be resolved all land on the **Minimal Skill
policy** rather than importing machine-specific state. That is deliberate: a
first CI Run is reproducible precisely because it consults nothing personal or
machine-global, and Required Skills come from the packaged catalog so the
Minimal policy resolves even when no external inventory answers.

Note the asymmetry. An unavailable inventory is only fatal when you asked for
something it had to resolve — see
[`Skill inventory is unavailable for explicit policy names`](#troubleshooting).

---

## Migrating a Config that predates the policy

A Config written before ADR-0015 has no `enabled_skills` key. git-loopy does not
guess one: absence resolves to the **Minimal Skill policy**, so the installation
keeps running with a *narrower* surface than its author expected rather than a
wider one.

The first **interactive** Run on such a Config offers a one-time conversion,
before any work starts, through the same picker as `git-loopy skills edit` —
seeded from your Copilot **Skill baseline** with anything Copilot reports
disabled left unchecked. It writes the scope a Run actually resolves: project
when the repository carries Config, otherwise global.

| Outcome | Effect |
| --- | --- |
| Confirm | the selection is saved; the Run continues on it and is never asked again |
| Cancel | **nothing is written and nothing is started** — the Run exits non-zero so no Iteration proceeds on a policy you declined to choose |
| No TTY, or `--no-interactive` | nothing is prompted or persisted; the Run proceeds on the Minimal Skill policy and warns, naming `git-loopy skills edit` as the fix |

Automation therefore never blocks on the question, and never answers it either.

Two Configs are **not** legacy and are never offered migration:
`enabled_skills = []` (a real, deliberately empty policy) and any Run under
`GIT_LOOPY_ENABLED_SKILLS` (the base is replaced, so there is nothing to
convert).

### Recovering afterwards

```bash
git-loopy skills edit              # answer the question you deferred
git-loopy skills list              # see what the Run would actually load
```

---

## Troubleshooting

Every one of these is raised at **preflight**, before the first work session,
and **none of them rewrites your saved policy**. A failing Run leaves the Config
exactly as it found it, so the fix is always yours to make deliberately.

| Message on stderr | Why | Recovery |
| --- | --- | --- |
| `Enabled Skills are missing from the catalog` | a configured name resolves to nothing — a personal Skill you never installed here, a plugin you removed, or a typo | `git-loopy skills list` to see the real names, then `git-loopy skills edit` to drop or correct it |
| `Required Skills are disabled` | the effective set omits a name the active `PROMPT.md` declares in `required-skills` — a `--disable-skill` overlay, a legacy deny guard, or a `GIT_LOOPY_ENABLED_SKILLS` / `enabled_skills` value that simply does not list it | if the base came from the environment, correct or unset `GIT_LOOPY_ENABLED_SKILLS`; otherwise `git-loopy skills edit` and re-enable it, or drop the overlay / `deny_skills` entry causing the subtraction |
| `Enabled project Skills are not git-tracked` | a project policy enables a Skill under `<repo>/.copilot/skills` that is not committed, so it would not exist for a collaborator | `git add` and commit the Skill, or `git-loopy skills edit` to disable it |
| `Skill inventory is unavailable for explicit policy names` | you supplied an explicit policy but the Copilot inventory could not be resolved — Copilot missing, unauthenticated, or failing to start | fix the Copilot CLI installation / auth, then re-run; `git-loopy skills list` reports the same discovery failure in isolation |

Preflight failures exit `1` and print
`git-loopy: Skill policy preflight failed: <message>. Inspect the catalog and
configured policy with `git-loopy skills`.`

Two more diagnostics are warnings rather than failures:

- **`this Config predates the closed-world Skill policy`** on an unattended Run
  — the Run continues on the Minimal Skill policy and persists nothing. Fix with
  `git-loopy skills edit` on a terminal.
- **`active prompt has no required-skills metadata`** — a custom `PROMPT.md`
  inherited the packaged Required Skill list. Declare a `required-skills` list
  in its frontmatter (an explicitly empty list is a valid declaration) to
  complete the migration.

---

## What a Run does with the policy, and what it records

### Resolved once, frozen for the whole Run

The **Effective Skill policy** is resolved at preflight — before source
collection, before the Pool is read, before the first work session — and then
**frozen**. Every serial **Iteration** and every parallel **Lane** in that Run
shares the identical set.

That means a change made *while a Run is in flight* does not reach it: editing
`config.toml`, toggling a Skill in Copilot CLI, or installing a new Skill
changes nothing until the next Run. This is the point, not a limitation — a
long autonomous Run must not have its capability set change under it, and a
Lane must not resolve a different set from its siblings.

Enforcement is doubled. A disabled Skill is **omitted from the SDK-visible
catalog** for the Run, so the agent never sees it; and it is **denied again at
the permission gate** if something asks for it anyway.

### The `wrapper.skill_policy.resolved` audit record

One Run-scoped event records exactly what was frozen. Find it in the replay log
as the answer to "what could that Run have loaded?":

| Field | Meaning |
| --- | --- |
| `base_scope` | which policy was selected **before** any environment replacement or overlay: `project`, `global`, or `minimal` |
| `enabled` | the sorted canonical names the Run may load — the effective set, after replacement, overlays, and deny guards |
| `fallback` | why that *base scope* was `minimal` — `minimal` (nothing configured) or `migration` (an unconverted legacy Config) — and `null` when a project or global scope supplied the base |
| `legacy_denied` | the sorted names the deprecated deny guards resolved, recorded in full whether or not each one actually removed an enabled Skill |
| `migration_warning` | `true` when the active prompt declared no `required-skills` and inherited the packaged list |
| `required` | the sorted Required Skill names |
| `source_kinds` | each enabled name mapped to the source that won it (`project`, `inherited`, `personal`, `plugin`, `custom`, `builtin`, `packaged`) |

**Read `base_scope` and `fallback` as history, not as outcome.** They describe
the scope selected *before* the replacement step. A Run driven entirely by
`GIT_LOOPY_ENABLED_SKILLS`, with nothing persisted anywhere, therefore records
`base_scope: minimal` and `fallback: minimal` while the environment policy is
fully in force — `enabled` is the field that says what the Run could load.
Reading the pair as "it fell back to Required Skills only" would have you
debugging an environment variable that was honoured.

The record is **redacted by construction**. It carries canonical names and
source *kinds* only: no absolute home-directory paths, no discovery
directories, no Run-scoped exposure directory, and no Skill content ever
appears. That is what makes it safe to paste into an issue when asking why a Run
behaved as it did.

Because the policy is frozen once per Run, this event is emitted once per Run —
not once per Iteration and not once per Lane — and it is identical for a serial
Run and a parallel one.

---

## Which Runner honours a policy

The Skill policy is a family-wide contract requirement, but the **Python**
reference Orchestrator implements it first. The **shell** and **PowerShell**
ports have no `config.toml` tier yet, so rather than silently running an
Iteration on a wider capability set than you configured, they **fail closed** —
aborting before source collection and before Copilot is invoked, exiting `1`
with a diagnostic naming the surface they found.

All four surfaces above trigger it: `GIT_LOOPY_ENABLED_SKILLS` (including an
explicit empty value), `--enable-skill`, `--disable-skill`, and an
`enabled_skills` assignment in a project or global `config.toml`. The deprecated
legacy deny guards do not — they keep working everywhere.

If a native port aborts, either run the Python Orchestrator, or remove the
policy surface from the environment and Config it is reading. The per-Runner
detail is in [`docs/runners.md`](runners.md#the-closed-world-skill-policy-is-python-first).

---

**Next:**
- [`docs/skills-setup.md`](skills-setup.md) — installing the Skill catalog and establishing a policy for the first time.
- [`docs/runners.md`](runners.md) — the Runner family, invocation, and the Python-first transition.
- [`git-loopy/python/README.md`](../git-loopy/python/README.md) — the CLI reference: flags, environment variables, and `config.toml`.
- [ADR-0015](adr/0015-closed-world-skill-policy.md) — the decision.
- [contract §17](wrapper-contract.md#17-closed-world-skill-policy-skill-policy-rollout-must) — the language-neutral requirement.
