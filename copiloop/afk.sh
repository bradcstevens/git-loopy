#!/bin/bash
# Convenience launcher for the Python copiloop runner (copiloop/python/).
# This is NOT a separate runner — it just invokes `copiloop` with a default
# model. See copiloop/python/README.md and docs/runners.md for the full surface.
#
# On an interactive run (a TTY with the `[tui]` extra, or COPILOOP_INTERACTIVE=1)
# the startup model + reasoning-effort picker (ModelSelectionMode) is opt-in:
# pass `--select-model` (e.g. `./afk.sh --select-model`) or set
# COPILOOP_MODEL_SELECT=1 to choose live from `list_models()` before the loop
# starts; the flag wins over the env var. By default the run goes straight to
# the loop on the COPILOOP_MODEL / COPILOOP_REASONING_EFFORT below with no
# prompt. Arguments are forwarded ("$@"), so the flag and a positional
# iteration cap reach the CLI.
#
COPILOOP_MODEL=claude-opus-4.8 COPILOOP_REASONING_EFFORT=max uv run --project copiloop/python copiloop "$@"