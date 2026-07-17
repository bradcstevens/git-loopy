# Rebrand copiloop to git-loopy

**Status:** accepted

## Context

ADR-0005 renamed the kit from `ralph-afk` to `copiloop`. In use, `copiloop` reads and
pronounces awkwardly, so we are renaming the product a second time — to **git-loopy** —
before the name accretes any more surface. The current name spans the Python distribution
`copiloop`, the import module `copiloop`, the console script `copiloop`, the `COPILOOP_*`
env-var prefix, the `copiloop/` source directory (holding `copiloop/python/copiloop/`), the
`.copiloop/` runtime-state directory, the `copiloop.*` OpenTelemetry span prefix, and a
handful of code identifiers (`CopiloopApp`, the `Copiloop-Checkpoint` commit trailer).

Unlike `copiloop`, the new name contains a hyphen, and **Python import packages cannot
contain hyphens** (`import git-loopy` is a syntax error). So where the previous rename kept
the distribution, module, and console script as one identical token, this one must split the
name into two written forms.

## Decision

Rebrand the product to **git-loopy**.

- The distribution (PyPI) name and the console command are **`git-loopy`**; the importable
  package, its directory, and the OTel span prefix are **`git_loopy`** (underscore, the only
  form Python accepts). This split is the standard Python packaging convention
  (distribution-with-hyphen, module-with-underscore).
- Because the executable is named `git-loopy`, `git loopy` (space) also works as a git
  subcommand wherever it is on `PATH`. `git-loopy` stays the canonical spelling in docs and
  `--help`; the README notes the `git loopy` alias in one line. We take on **no** git-plugin
  contract (no `git help loopy` man page).
- The env-var prefix becomes **`GIT_LOOPY_*`** (`COPILOOP_MODEL` → `GIT_LOOPY_MODEL`, etc.),
  matching the `git_loopy` module. The standard `OTEL_EXPORTER_OTLP_ENDPOINT` stays
  unprefixed.
- Directories: the outer product folder `copiloop/` becomes `git-loopy/`, the Python package
  `copiloop/python/copiloop/` becomes `git-loopy/python/git_loopy/`, and the `.copiloop/`
  runtime-state directory becomes `.git-loopy/`. The rule is: hyphen for the CLI/product/
  on-disk brand, underscore only where Python forces it.
- Code identifiers follow the module form: `CopiloopApp` → `GitLoopyApp`, the
  `Copiloop-Checkpoint` commit trailer → `GitLoopy-Checkpoint`, `copiloop.run` spans →
  `git_loopy.run`.
- The version stays `0.0.1`: because the distribution name itself changes, `git-loopy 0.0.1`
  is simply the first release under the new name.
- The GitHub repository slug is renamed to **`git-loopy`** as part of this change, resolving
  the long-pending slug rename (issue #57, which ADR-0005 deferred and never completed).

Unlike `ralph`, whose "Ralph loop" *technique* was deliberately retained, **`copiloop` was
pure product branding and is retired in full** — nothing survives. The "Ralph loop"
technique and the "AFK" descriptive terms are untouched by this rename.

The rename is a **hard cut with no back-compat shim**, exactly as ADR-0005: `COPILOOP_*` env
vars are not honoured, and the legacy `.copiloop/` runtime directory is not read.

The two retired brands are now guarded by a single unified regression tripwire,
`test_no_retired_branding.py`, which folds in the former `test_no_ralph_branding.py` and
scans the tracked surface for both `ralph-afk` and `copiloop` branding in one pass.

## Considered options

- **Keep `copiloop`** — rejected: it reads and pronounces awkwardly, which was the whole
  motivation for revisiting the name.
- **Make every form identical again** (`git_loopy` as the command too, or `gitloopy` with no
  separator) — rejected: `git-loopy` reads best on the command line and is the idiomatic
  distribution spelling; the two-form split is universal in Python packaging and is pinned by
  the guard test, so the small mental overhead is worth the better ergonomics.
- **Market `git loopy` as a first-class git subcommand** — rejected: the motivation was
  pronounceability, not being a git plugin; owning the git-subcommand contract (man pages,
  `git help loopy`) is cost with no matching goal. The alias still works for free.
- **Ship `COPILOOP_*` deprecation aliases** — rejected for the same reason ADR-0005 rejected
  `RALPH_*` aliases: the kit is pre-1.0 and forked, so a shim would be permanent guard code
  for a name almost nobody has scripted against long-term.
- **Two separate guard tests** (a `copiloop` sibling mirroring the `ralph` one) — rejected in
  favour of one unified guard: two near-identical whole-tree scans would drift in their
  exemption lists.

## Consequences

- Every `copiloop` / `Copiloop` / `COPILOOP_` identifier, the `copiloop/` and `.copiloop/`
  directories, and the `copiloop.*` span names change in one coordinated rename; existing
  shells exporting `COPILOOP_*` and existing `.copiloop/` runtime directories stop being
  honoured and must migrate to `GIT_LOOPY_*` / `.git-loopy/`.
- The distribution-vs-module split (`git-loopy` vs `git_loopy`) is a permanent property of
  the name: command lines, PyPI, and prose use `git-loopy`; `import`, directories, env vars,
  and span names use `git_loopy`.
- The repo slug rename to `git-loopy` closes issue #57; GitHub's automatic slug redirect
  covers old clones and links during the transition.
- The domain glossary (`CONTEXT.md`) now names **git-loopy** as the framework / CLI / brand
  and records `copiloop` (alongside `ralph-afk`) as a retired name.
- Cleaning up `.gitignore` as part of this rename also removes a stale `.ralph/` ignore line
  that had been silently failing the retired-branding guard.
