#!/bin/bash
# Convenience launcher for the Python AFK runner (ralph/python/).
# This is NOT a separate runner — it just invokes `ralph-afk` with a default
# model. See ralph/python/README.md and docs/runners.md for the full surface.
#
# On an interactive run (a TTY with the `[tui]` extra, or RALPH_INTERACTIVE=1)
# the startup model + reasoning-effort picker (ModelSelectionMode) is opt-in:
# pass `--select-model` (e.g. `./afk.sh --select-model`) or set
# RALPH_MODEL_SELECT=1 to choose live from `list_models()` before the loop
# starts; the flag wins over the env var. By default the run goes straight to
# the loop on the MODEL / REASONING_EFFORT below with no prompt. Arguments are
# forwarded ("$@"), so the flag and a positional iteration cap reach the CLI.
#
MODEL=claude-opus-4.8 REASONING_EFFORT=max uv run --project ralph/python ralph-afk "$@"