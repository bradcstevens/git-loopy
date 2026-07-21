# Closed-world Skill policy for reproducible Runs

**Status:** accepted

ADR-0014 made skills available to Python SDK sessions by exposing project and
personal skill roots. That solved missing-skill failures, but it also made a Run's
capabilities depend on whatever happened to be present on the operator's machine and
ignored the enabled/disabled selections maintained by GitHub Copilot CLI. We will
separate skill discovery from skill exposure: a broad metadata catalog remains
inspectable, while a git-loopy-owned, closed-world policy controls exactly which
canonical skill names a Run may load. This ADR supersedes ADR-0014.

## Decision

### Catalog and identity

- A Skill policy identifies skills by canonical name, not absolute path or content
  digest.
- The catalog is resolved through the exact Copilot CLI runtime used by the Python SDK,
  with the same `COPILOT_HOME`, and is augmented by git-loopy's explicit project source
  (`<repo>/.copilot/skills`) and packaged skill catalog.
- Source precedence is: git-loopy's explicit project source, then Copilot CLI's normal
  project/personal/plugin/built-in/custom precedence, then git-loopy's packaged
  fallback.
- Catalog discovery may read skill metadata, but disabled skill instructions, scripts,
  and resources are not loaded into the model's session.
- A plugin-provided skill is selectable as an individual Skill. Enabling it does not
  implicitly activate the rest of its owning plugin.

### Policy and precedence

- Persisted Config uses `enabled_skills = ["name", ...]` as an explicit allowlist.
  Newly discovered or newly packaged names remain disabled until explicitly enabled.
- A project policy replaces the global policy. If the project key is absent, the
  global policy applies. An explicit empty list is a real empty policy, not
  inheritance.
- Project policy is a shared, versionable repository contract. Any enabled
  project-sourced skill must be git-tracked.
- `GIT_LOOPY_ENABLED_SKILLS` is an exact environment replacement for the configured
  base policy. Repeatable `--enable-skill` and `--disable-skill` flags are temporary
  Run overlays; disable wins.
- Existing `deny_skills`, `GIT_LOOPY_DENY_SKILLS`, and `--deny-skill` remain
  deprecated final guards during migration. They can only subtract from the effective
  set and are never removed or weakened implicitly.

### Setup, defaults, and migration

- Copilot CLI is a one-time seed, not a live authority. The first configured policy is
  initialized from the current operator-specific `enabled` state reported by the SDK's
  Copilot runtime. Later Copilot changes affect git-loopy only through an explicit
  import/sync.
- A new project policy starts from an existing global git-loopy policy. Only when no
  lower-scope policy exists does setup take a fresh Copilot baseline.
- Interactive `git-loopy init` and `git-loopy skills` show a searchable multi-select
  view with git-loopy state, Copilot state, source, description, and Required status.
  The base install has a plain-terminal fallback; the optional TUI provides an
  arrow/space picker.
- On first setup, packaged skills absent from Copilot's inventory start enabled,
  matching Copilot's behavior for a newly added skill. Once a policy exists, future
  catalog additions start disabled.
- `git-loopy init --yes`, an unconfigured non-interactive Run, an unavailable
  first-setup inventory, and unattended migration use the Minimal Skill policy rather
  than importing machine-specific state.
- Existing Config without `enabled_skills` gets a one-time interactive migration
  picker seeded from Copilot state with legacy denials unchecked. Until migration is
  saved, non-interactive Runs use the Minimal Skill policy.

### Required Skills and validation

- `PROMPT.md` carries a restricted YAML-frontmatter `required-skills` list. The
  packaged prompt requires `diagnosing-bugs`, `prototype`, `tdd`,
  `codebase-design`, `resolving-merge-conflicts`, and `code-review`.
  `code-review` must be available but remains conditionally invoked.
- A legacy custom prompt without metadata inherits the packaged Required Skill list
  with a warning until it declares its own list, including an explicit empty list.
- Setup or sync mirrors conflicting Copilot state in the picker but cannot save until
  every Required Skill is enabled.
- Preflight fails before work begins when the Copilot inventory cannot be resolved for
  a configured policy, an enabled name is missing, a Required Skill is disabled, or a
  project policy resolves an enabled project skill that is not git-tracked. Failures
  never rewrite policy.

### Management and Run behavior

- `git-loopy skills` owns policy inspection and mutation; skill acquisition/removal
  remains with Copilot/plugin tooling or git-loopy's existing scaffold step.
- List and picker views show git-loopy and Copilot states side by side. Changes are
  import-only and never write Copilot CLI settings.
- Explicit sync previews and confirms its diff, replaces the CLI-reported subset of the
  selected git-loopy policy, and preserves selections for git-loopy-only sources.
- The Effective Skill policy is resolved once at Run preflight and frozen across every
  Iteration and parallel Lane. Disabled skills are omitted from the SDK-visible catalog
  and denied again at the permission gate.
- A redacted preflight event records policy scope, enabled names, resolved source
  kinds, and legacy denials without absolute home-directory paths.
- Skill policy becomes a language-neutral wrapper-contract requirement. The Python
  reference Orchestrator implements it first; native ports must fail clearly rather
  than silently ignore a configured policy until their config-parity work lands.

## Considered options

- **Live-mirror Copilot CLI on every Run** — rejected because machine-global changes
  would silently alter autonomous behavior.
- **Persist only disabled names, as Copilot CLI does** — rejected because newly
  discovered skills would become enabled implicitly.
- **Allow only repository-local skills** — rejected because operators still need to
  select trusted personal, plugin, built-in, and custom skills.
- **Pin absolute paths or content digests** — rejected because project policies must be
  portable and normal source precedence is part of the Copilot experience.
- **Load an enabled skill's entire plugin** — rejected because this policy governs
  Skills only, not agents, MCP servers, hooks, extensions, or other plugin capabilities.

## Consequences

- Package installation scope no longer changes skill behavior; Config scope and the
  resolved policy do.
- Fresh unattended Runs are deterministic and do not inherit personal skill content.
- Shared project policies may name personal or plugin skills as explicit dependencies;
  collaborators without a resolvable skill of that name receive an actionable
  preflight failure.
- Catalog changes remain visible without expanding a Run's capability set.
- The SDK integration, Config resolver, first-run migration, CLI/TUI management,
  event schema, wrapper contract, conformance fixtures, and documentation all require
  coordinated updates.
