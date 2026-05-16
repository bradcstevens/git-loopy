# `ralph-afk` — Python peer variant of `ralph/afk.sh`

This is a peer variant of the bash AFK runner at [`ralph/afk.sh`](../afk.sh),
built on the [GitHub Copilot Python SDK](https://github.com/github/copilot-sdk/tree/main/python).
Both runners share [`ralph/PROMPT.md`](../PROMPT.md) and the same wrapper
contract — same `ready-for-agent` filter, same `## Parent` + `## Acceptance
criteria` discriminator, same `Closes/Fixes/Resolves #N` auto-close
backstop, same env-var surface (`MODEL`, `ISSUE_SOURCE`, `MAX_NMT_STRIKES`),
same clean-exit-on-empty / abort-on-stuck termination model.

> **Status (issue #2):** scaffold stub. This README is a placeholder; the
> full docs polish (side-by-side invocation comparison with the bash
> runner, exit-code table, env-var surface table, observability artefact
> locations, cost-figure caveat) lands in the final slice ([issue #13]).
> Today, `ralph-afk` exits cleanly after parsing its CLI/env surface — the
> iteration driver is wired in [issue #10].

[issue #10]: https://github.com/bradcstevens/github-copilot-ralph-starter-kit/issues/10
[issue #13]: https://github.com/bradcstevens/github-copilot-ralph-starter-kit/issues/13

## Why a peer variant?

See ADR [`docs/adr/0001-python-sdk-peer-variant.md`](../../docs/adr/0001-python-sdk-peer-variant.md).
TL;DR: the bash runner stays first-class for the minimal-deps audience; the
Python runner adds a richer terminal experience (streaming reasoning,
frozen iteration `Panel`s, per-iteration token + estimated-cost signal,
JSONL replay log) without forcing a Python toolchain on downstream
projects that deliberately chose the bash-only kit.

## One-time bootstrap (preview)

```bash
# From the repo root:
uv sync --project ralph/python

# With optional OpenTelemetry export (issue #12):
uv sync --project ralph/python --extra otel
```

`uv ≥ 0.4` and Python ≥ 3.11 are required. `pip ≥ 24` works as a fallback
if `uv` is not available.

## Invocation (preview)

```bash
# Default: unlimited iterations against ISSUE_SOURCE=github.
uv run --project ralph/python ralph-afk

# Cap at 50 iterations (mirrors `bash ralph/afk.sh 50`).
uv run --project ralph/python ralph-afk 50

# Pick a different model (mirrors `MODEL=... bash ralph/afk.sh`).
MODEL=gpt-5.4 uv run --project ralph/python ralph-afk

# Tolerate more no-progress iterations before aborting.
MAX_NMT_STRIKES=5 uv run --project ralph/python ralph-afk

# Legacy local-markdown mode (issue #11).
ISSUE_SOURCE=prds uv run --project ralph/python ralph-afk
```

`uv run --project ralph/python ralph-afk --help` prints the full CLI/env
surface.

## See also

- [Root `README.md`](../../README.md) — kit overview, prerequisites,
  human-driven workflow phases (`/grill-me`, `/to-prd`, `/to-issues`,
  `/triage`).
- [`ralph/afk.sh`](../afk.sh) — bash variant of the AFK runner. The source
  of truth for the wrapper-semantic rules both runners implement.
- [`ralph/PROMPT.md`](../PROMPT.md) — the shared prompt loaded into every
  iteration by both runners.
- [`docs/adr/0001-python-sdk-peer-variant.md`](../../docs/adr/0001-python-sdk-peer-variant.md)
  — load-bearing decisions for this peer variant.
