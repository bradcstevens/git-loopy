# Run-from-anywhere distribution: global engine, per-project assets

**Status:** accepted

## Context

Today the runner can only be launched from inside this repo's tree
(`uv run --project ralph/python ralph-afk`), and the loop reads its prompt from
`<target-repo>/ralph/PROMPT.md`. To use the loop in another project you had to copy the
`ralph/` folder into it. We want a single command that runs from *any* repository, plus
persisted settings so model / effort / etc. need not be re-passed on every run.

## Decision

Distribute copiloop as a **globally-installed engine with per-project assets and config**.

- **Distribution.** `uv tool install` from the git repo puts a single `copiloop` command on
  PATH; `uvx` provides the ephemeral, npx-like run. **Publishing to PyPI is explicitly
  deferred** to a later, separate effort. A project-local install (a dev dependency +
  `uv run copiloop`) remains an *optional, documented* path for repos already on Python/uv.
- **Assets.** A default `PROMPT.md` ships **inside the wheel** (as `pricing.toml` already
  does), so `copiloop` runs in any repo with zero setup. `copiloop init` scaffolds editable,
  overridable copies of the prompt and skills into a chosen scope. Runtime prompt resolution
  is **project (`./copiloop/PROMPT.md`) > global (`~/.config/copiloop/PROMPT.md`) > packaged
  default**.
- **Config.** Persistent TOML at `~/.config/copiloop/config.toml` (global, honouring
  `$XDG_CONFIG_HOME`) and `./copiloop/config.toml` (project). Effective settings merge
  key-by-key with precedence **CLI flag > env var > project config > global config >
  built-in default** (denylists union across sources).
- **"Global vs project" describes *which config and assets resolve*, not two different
  binaries.** The engine is installed once, globally; scope is chosen when you `copiloop
  init` — the npx-style "global or project?" prompt lives there, not in the binary install.

## Considered options

- **Publish to PyPI now (`pipx` / `uvx copiloop`)** — rejected for now: it adds a
  release/versioning pipeline, requires owning the `copiloop` PyPI name, and pins forkers to
  a fixed artifact, which fights the fork-and-customize nature of a starter kit. Retained as
  an explicit future option.
- **A first-class project-scoped binary** — rejected: `uv tool install` is user-global, and
  a truly project-scoped binary would force every target repo (often not even a Python
  project) to become a uv/Python project.
- **Require `copiloop init` before any run (no packaged default)** — rejected: it defeats
  "just works from anywhere"; the packaged default makes a bare run succeed with zero files.

## Consequences

- The default `PROMPT.md` moves into the wheel as package data; `loop.py`'s prompt lookup
  gains the global and packaged-default fallbacks.
- The install string carries `#subdirectory=copiloop/python` (the package stays nested under
  the repo) — a one-time documented detail; the *run* command is just `copiloop`.
- A persistent-config layer and a `copiloop init` / `copiloop config` surface are introduced
  (the CLI gains subcommands, which it did not have before).
- Target repos may carry both a committed `copiloop/` (prompt + config) and a gitignored
  `.copiloop/` (run artifacts).
