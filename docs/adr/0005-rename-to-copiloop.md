# Rebrand ralph-afk to copiloop

**Status:** accepted

## Context

The kit ships under the name "ralph-afk": the Python distribution `ralph-afk`, the import
module `ralph_afk`, the console script `ralph-afk`, the `RALPH_*` env-var prefix, the
`ralph/` source directory, the `.ralph/` runtime-state directory, and the repository slug
`github-copilot-ralph-starter-kit`. As the kit consolidates onto a single,
globally-installable command (ADR-0006), we want a name that describes the *product* — a
GitHub Copilot SDK loop-engineer framework for orchestrating automated ralph loops — rather
than the "AFK" implementation detail.

## Decision

Rebrand the product to **copiloop**.

- The distribution, import module, and console script all become exactly `copiloop` (no
  `-afk` / `_afk` suffix anywhere).
- The env-var prefix becomes `COPILOOP_*`, and the currently-bare vars are prefixed too
  (`MODEL` → `COPILOOP_MODEL`, `REASONING_EFFORT`, `ISSUE_SOURCE`, `MAX_NMT_STRIKES`,
  `INCLUDE_PRS`, …). The standard `OTEL_EXPORTER_OTLP_ENDPOINT` stays unprefixed.
- The `ralph/` directory becomes `copiloop/`; the `.ralph/` runtime-state directory becomes
  `.copiloop/`.
- The repository slug becomes `github-copiloop`.

"Ralph loop" is deliberately **retained as a concept** — the name of the unattended,
iterative loop technique — in prose, the tagline, the README, and the domain glossary.
"ralph" survives *only* in that sense; it is retired everywhere it named the product or
appeared as a code identifier, directory, or env var.

The rename is a **hard cut with no back-compat shim**: old `RALPH_*` env vars are not
honoured.

## Considered options

- **Keep the `ralph-afk` name** — rejected: it foregrounds an implementation detail ("AFK")
  over what the tool is, and doesn't match the single-`copiloop`-command story.
- **Retire "ralph" entirely, including the concept** — rejected: "ralph loop" is a useful,
  established term for the loop *technique*; only the *product* branding needed to change.
- **Ship `RALPH_*` deprecation aliases** — rejected: the kit is pre-1.0 (v0.0.1) and a
  starter kit users fork, so a shim would be permanent code guarding a name almost nobody
  has scripted against yet.

## Consequences

- Every `ralph` / `ralph_afk` identifier, the `ralph/` and `.ralph/` directories, and all
  `RALPH_*` env vars change in one coordinated rename; existing scripts using `RALPH_*` or
  the bare `MODEL` / `REASONING_EFFORT` vars must migrate to `COPILOOP_*`.
- The GitHub repo rename to `github-copiloop` is the final step and requires the
  `bradcstevens` account (`bradstevens_microsoft` gets a 403); GitHub's automatic slug
  redirect covers old clones and links during the transition.
- The domain glossary (`CONTEXT.md`) now distinguishes **copiloop** (framework / CLI /
  brand) from a **Ralph loop** (the retained concept).
