# Single Python entrypoint: delete afk.sh, fold model/param into the CLI

**Status:** superseded by [ADR-0013](0013-multi-language-runner-family.md) (the shell/PowerShell
ports return as members of a runner family; model/effort selection via `--model` /
`--reasoning-effort` and the config precedence chain stand)
**Supersedes:** the `afk.sh` retention in [ADR-0002](0002-retire-bash-runner.md)

## Context

ADR-0002 retired the second (bash) runner but **kept `ralph/afk.sh`** as a thin convenience
launcher that hard-codes `MODEL=claude-opus-4.8 REASONING_EFFORT=max` and forwards to the
Python runner. That launcher is the last reason model/effort selection lives outside Python,
and it forces those parameters to be re-supplied by environment on every run. With persistent
config now arriving (ADR-0006), the launcher no longer earns its keep.

## Decision

Make the Python CLI the single, canonical entrypoint and **delete `afk.sh`**. Model and
reasoning-effort selection fold into the CLI:

- New `--model` / `--reasoning-effort` flags provide per-run overrides.
- Resolution follows **flag > env > project config > global config > built-in default**
  (ADR-0006), so the values persist once (via `copiloop init` / config) and need not be
  re-passed.
- The built-in default stays `claude-opus-4.8` / `max`, so a zero-config run still works at
  full reasoning.
- The opt-in `--select-model` live picker is **retained** as a per-run override; `copiloop
  init` reuses the same `list_models()` data (rendered as a plain-text list, no `[tui]`
  extra) to seed config.

## Consequences

- The `afk.sh` retention bullet in ADR-0002 is reversed; the single-Python-runner decision
  in ADR-0002 otherwise stands.
- The bash env-var launch contract (`MODEL` / `REASONING_EFFORT` as the launch mechanism) is
  replaced by config + flags — and those env vars are renamed to `COPILOOP_*` per ADR-0005.
- `bash ralph/afk.sh` is no longer an entry point; operators launch the loop with `copiloop`
  (or `uvx copiloop`).
