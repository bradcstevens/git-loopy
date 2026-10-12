# 1.0.0 promises an explicit Stable surface and removes what 0.x deprecated

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide what 1.0.0 removes from 0.x and what its stable surface
promises](https://github.com/bradcstevens/git-loopy/issues/778). Builds on
[ADR-0067](0067-python-retires-the-mode-switches.md),
[ADR-0073](0073-language-paths-release-and-gate-independently.md) and
[ADR-0079](0079-a-human-opens-a-major-line-and-the-1-0-0-cut-waits-for-the-python-path.md).
Amends [ADR-0015](0015-closed-world-skill-policy.md),
[ADR-0057](0057-live-evidence-guides-per-issue-routing.md),
[ADR-0080](0080-the-tracker-is-the-board-and-its-states-are-derived.md),
[ADR-0082](0082-an-acceptance-ticket-closes-a-code-graph-and-the-orchestrator-closes-its-spec.md)
and
[ADR-0088](0088-only-execution-task-types-run-and-a-planning-or-review-typed-issue-is-a-pool-exclusion.md).

Semantic Versioning holds 1.0.0 to its public API until 2.0.0, and no document declared
git-loopy's. The docs describe public surfaces and internals side by side, and only
`info --json` and `commands --json` were called stable. 0.x gets no maintenance line
(ADR-0079). Whatever 1.0.0 removes breaks a 0.11 user once, and whatever it keeps, it keeps
until 2.0.0. This decision declares the Python path's **Stable surface** and removes what
0.x already marked deprecated or legacy.

## The Stable surface is an explicit allow-list

The Python path's 1.0.0 promises exactly what its **Stable surface** lists. Anything it
does not list may change in any 1.x Release. Promising "everything documented" was
rejected. It would freeze internals until 2.0.0, and every new page of documentation would
widen the promise without anyone deciding to.

Promised until 2.0.0:

- **Commands and flags.** The `git-loopy` command, its commands, and their documented flags
  and values. `commands --json` names the commands only, so the list names the flags.
- **Exit codes.** The codes and reasons of Wrapper contract §10. The retired code 3 is never
  reused.
- **Config.** The documented keys and sections and their meanings, the project and global
  scopes and their file locations, the precedence spine, and strict loading (below).
- **Environment.** The documented user-facing `GIT_LOOPY_*` variables.
- **Tracker vocabulary.** The label roles and strings git-loopy reads or writes: the triage
  roles and their mapping, `task-type:*`, `parallel-safe`, `wayfinder:*`, `vX.Y.Z`, and
  `path:<key>` once it ships. The list also names the AFK-ready headings `## What to build`
  and `## Acceptance criteria`, and what marks a planning document: a `PRD:` or `Spec:`
  title, or the `wayfinder:map` label.
- **Run instructions.** Where a custom `PROMPT.md` lives, and that it replaces the packaged
  instructions.
- **Machine-readable output.** `info --json` and `commands --json`, through their
  `schema_version`.
- **The versioned authorities.** The Run logs (`.git-loopy/logs/*.jsonl`), the Wrapper
  contract and the Conformance fixtures, through the versions the Python **Path manifest**
  declares (ADR-0073). No 1.x Release drops a version that 1.0.0 declares.

Not promised:

- the `git_loopy` import API, the `python -m git_loopy.*` modules, and
  `git-loopy-assessment`, an optional tracer that is not yet operational;
- host, calibration, smoke-test and test variables;
- human-readable text: help, diagnostics, the line printer, and the TUI and web screens;
- defaults and recommendations: the default model and effort, the Routing recommendations,
  the Skill catalog pin, Skill and **Agent profile** content, and the packaged `PROMPT.md`
  text;
- `.git-loopy/` state other than the Run logs, install records, and caches;
- the TUI helper's own command line and the web board's HTTP endpoints;
- deprecated surfaces and migration aids (below);
- the shell and PowerShell paths, which keep their own **Path baselines**.

**Config is strict from 1.0.0.** An unknown key or section in either scope is refused at
preflight, naming the key and its file. Today the loader ignores one silently, so every key
1.0.0 removes would become a setting that is accepted and ignored, the defect ADR-0067
names. Strict loading makes every removed key and every typo loud, with no refusal written
per key. It also fails closed across 1.x minors: an older Release refuses a key a newer
minor added rather than running without it. A team that shares project Config upgrades
before anyone uses a new key.

The environment stays lenient, because the `GIT_LOOPY_*` namespace is shared with the
Skills repository (`GIT_LOOPY_NEXT_CHAIN`). A variable that 1.0.0 removes is refused by
name.

## How 1.x changes it

- A minor may add a promised item: a command, flag, value, Config key, variable, label or
  exit reason.
- A minor may deprecate a promised item. It keeps working and warns when it is used, and the
  documentation and release notes say so. It stays on the Stable surface until it is
  removed.
- Only a major may remove a promised item or change its meaning incompatibly. A major
  removes only what some 1.x already deprecated, so a user of the latest 1.x sees a warning
  before anything breaks.
- An item that is not promised may change in any Release, with a release note where users
  would notice.

Only a human-applied label opens a major line (ADR-0079), so no removal moves the Release
target on its own.

The promise is enforced in code. The Stable surface is committed as a machine-readable
manifest, and the human-readable document is checked against it. A test in the Python suite
fails when a manifest entry no longer matches the live surface: the CLI parser, the Config
key registry, the documented variables, the exit-code fixture or the label vocabulary. An
entry may leave the manifest only while the Release line is heading for a new major, which
the test reads from the Release line. Integration and the **Rehearsal** both run the suite,
so no further Rehearsal refusal is needed.

## What 1.0.0 removes

By default, 1.0.0 removes every surface that 0.x marked deprecated or legacy. Using a
removed flag, variable or key is a preflight refusal that names its replacement. It is
never accepted and ignored (ADR-0067). `update` repairs whatever has one unambiguous fix
and reports the rest, and `doctor` and `labels` do the same behind `--apply`. There is one
exception: a removal that needs work outside 1.0.0's four workstreams. That surface ships
deprecated and off the Stable surface, and a later 1.x minor removes it.

| Surface | 1.0.0 | Replacement |
| --- | --- | --- |
| `deny_skills`, `GIT_LOOPY_DENY_SKILLS`, `--deny-skill` | Removed | `enabled_skills`, `GIT_LOOPY_ENABLED_SKILLS`, `--disable-skill` |
| Config with no `enabled_skills` in any scope (the `legacy` startup state) | Removed | An explicit **Skill policy**, which `update` records |
| The `prds` local-markdown source | Removed | GitHub Issues |
| `required-skills` in custom Run instructions | Removed by ADR-0086 | The **Agent profiles** |
| Running an issue typed `planning` or `review` | Removed by ADR-0088 | A **Pool exclusion** |
| `--route-policy unselected` and the non-local legacy default | Deprecated | `static` or `dynamic` |

**The deny guards.** All three subtract last from the **Effective Skill policy**, which
`--disable-skill` and `enabled_skills` can already narrow. Only one power is theirs alone.
`deny_skills` is a union across scopes, while `enabled_skills` comes from one scope, so a
global deny is the only way a machine can narrow a project's committed policy. That veto
makes a Run's capability set depend on the operator's machine, which Wrapper contract §17
exists to prevent, so the veto goes with the guards.

ADR-0015 forbids removing or weakening the guards implicitly. A refusal is explicit, and a
repair must never weaken a deny: `update` folds a `deny_skills` list into `enabled_skills`
only where no Run could then load a name it denied. Otherwise it reports the key, and the
refusal stands.

**The legacy Skill-policy state.** Today, Config with no `enabled_skills` in any scope or in
the environment opens a one-time picker on a terminal. Unattended, it runs the **Minimal
Skill policy** with a warning. From 1.0.0 it does neither: a Run on it refuses and names
`git-loopy update`. `update` records an explicit global policy and reports what it wrote.
`doctor` reports the state, and `doctor --apply` makes the same write.
`git-loopy skills edit` widens the policy. This follows the routing precedent: saved Config
with no recorded Route policy already refuses until `update --routing` records one
(ADR-0057).

**The `prds` local-markdown source.** Its Agent closes a ticket by moving the ticket's file
into `done/`. So its work can never reach the **Orchestrator**'s closure on an approved
**Review verdict** (ADR-0072), and the **Completion rate** cannot count it (ADR-0085). Its
files carry no labels and no dependencies, so it has no **Task type**, no **Readiness**, no
**Acceptance ticket** and no **Spec closure**. It also cannot read the
`.scratch/<feature>/issues/` tickets that the Skills repository's local tracker writes.

`issue_source = "prds"` and `GIT_LOOPY_ISSUE_SOURCE=prds` are refused, naming GitHub Issues.
`issue_source` stays on the Stable surface, with `github` its only value, so a later source
can arrive in a minor. A local source that meets ADR-0072 is a fresh effort.

**`unselected` is the exception.** For one Run it keeps the routing that predates Route
policies, and it is the default for a non-local Run with no recorded policy. Removing it
means deciding remote routing defaults. A remote Run can select only Static, with a host
capability report, and remote Dynamic election stays refused (ADR-0057). That work belongs
to routing, not to a 1.0.0 workstream. 1.0.0 keeps `unselected` and the non-local legacy
default, deprecated and off the Stable surface, and naming `unselected` warns.
`--route-policy` is promised with `static` and `dynamic`. The 1.x minor that completes
non-local activation removes both.

## Migration aids are not promised

1.0.0 ships aids whose only job is to move 0.x state forward:

- the `route-labels migrate` command;
- the keep-or-migrate choice (`--routing keep|migrate|ask` on `init`, `update` and
  `upgrade`);
- `update`'s repairs of retired Config;
- the refusals that name a replacement;
- the `GIT_LOOPY_PRICING_FILE` warning.

None is on the Stable surface, so `route-labels` is not among the promised commands. Any 1.x
minor may drop an aid once the state it migrates is gone.

The named refusal for each removed `GIT_LOOPY_*` variable is the exception, and it stays
until 2.0.0. The environment is lenient, so without that refusal the variable would be
silently ignored. The other refusals can go safely. A removed flag without its refusal still
fails as a usage error (exit 2), and a removed key still fails as an unknown key.

## The dormant paths

The shell and PowerShell Orchestrators read only `GIT_LOOPY_*` variables and flags. Their
only use of `config.toml` is to detect an `enabled_skills` key. Finding one makes them
refuse, because they lack the closed-world policy (Wrapper contract §17.6). They never read
the `deny_skills` key. So no Python change to Config can silently change a dormant Runner,
and a Python removal owes the dormant paths nothing in Config.

They keep their own surfaces, their deny guards included, at their Path baselines
(ADR-0073). A removed surface may be named in a shared contract row, such as
`GIT_LOOPY_DENY_SKILLS`. That row stays, honoured only by the paths that declare it, as
ADR-0067 left `GIT_LOOPY_MAX_PARALLEL`. A Skill policy that `update` records makes a dormant
Runner on that machine refuse loudly. It already does so wherever `init` has run since the
closed-world policy.

## Amendments

- ADR-0015 kept the deny guards as deprecated final guards "during migration". It also gave
  Config without `enabled_skills` a one-time picker and an unattended Minimal fallback. On
  the Python path, 1.0.0 ends that migration. The guards are refused, and such Config
  refuses until `update` records a policy.
- ADR-0057 keeps `unselected` and the non-local legacy default. From 1.0.0 both are
  deprecated and off the Stable surface, and a 1.x minor removes them when non-local
  activation completes.
- ADR-0080, ADR-0082 and ADR-0088 say how the local-markdown source fares under them. From
  1.0.0 the Python path has no local-markdown source, so those lines describe 0.x only.

## Left to the PRD

The Language paths & release PRD settles:

- the manifest's format, the document's home, and the test;
- every entry on the list: which flags, values, keys, variables and labels;
- the refusals' wording and the repair rules, including what policy `update` records. Under
  ADR-0086 the Minimal Skill policy is only `code-review`, `tdd` and `diagnosing-bugs`, and
  it exposes no Permitted Skill. An unattended legacy Run today gets the six Required Skills
  of the packaged instructions;
- the Wrapper contract's changes and version: the local-markdown lines of §2, §3, §11 and
  §12, and the deny guards and startup states of §11 and §17;
- the Event-schema change to `wrapper.skill_policy.resolved` (`legacy_denied`, the
  `migration` fallback, and `migration_warning`), and the Conformance fixtures;
- how long each migration aid stays;
- the operator documentation: the Python README, `docs/skill-policy.md` and
  `docs/runners.md`.

## Considered options

- **Promise everything documented**, or everything documented minus a list of internals.
  Rejected: either one freezes internals the docs describe, and new documentation widens
  the promise without a decision.
- **Carry deprecated surfaces into 1.x**, on the Stable surface or off it. Rejected: on it,
  they are frozen until 2.0.0. Off it, users keep depending on surfaces nothing promises,
  and 1.0.0 is the one Release where users expect removals.
- **Remove every deprecated surface, `unselected` included.** Rejected: it pulls remote
  routing defaults into 1.0.0.
- **Keep `deny_skills` as a supported machine-level veto.** Rejected: it lets the
  operator's machine change a project's committed capability set.
- **Resolve Config without `enabled_skills` to the Minimal Skill policy, silently.**
  Rejected: an attended operator would lose the picker and see nothing. Saved Config
  without a recorded Route policy already refuses.
- **Keep the `prds` source, bring it under review in 1.0.0, or replace it with a
  `.scratch/` source.** Rejected: keeping it bypasses ADR-0072. Either rebuild is new work
  for a source that no 1.0.0 workstream needs.
- **Lenient Config that warns on an unknown key.** Rejected: nobody reads a warning in an
  unattended Run, and an older minor would run without a key a newer one added.
- **Promise the migration aids.** Rejected: they would outlive the 0.x state they exist to
  move.
- **A document without enforcement, or a dedicated Rehearsal refusal.** Rejected: the
  first drifts, and the second repeats what the suite inside the Rehearsal already checks.
- **A major that removes without prior deprecation.** Rejected: the latest 1.x would give
  no warning.
- **Keep shared Config readable by the dormant paths.** Rejected as unneeded: they read no
  Config value.
