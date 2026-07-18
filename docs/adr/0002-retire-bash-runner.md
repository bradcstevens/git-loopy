# Retire the bash AFK runner — single Python runner

**Status:** superseded by [ADR-0013](0013-multi-language-runner-family.md) (the runner family
returns, with a shared-source-of-truth backbone that neutralises the drift and doc-fan-out
objections below). Historically: accepted (the retained `afk.sh` launcher was later removed — see
[ADR-0007](0007-single-python-entrypoint-retire-afk-sh.md))

## Context

The kit used to ship **two** AFK runners that implemented the same wrapper
contract end to end: a bash runner (`ralph/sh-afk.sh`) and the Python runner on
the GitHub Copilot Python SDK (`ralph/python/`). Both honoured the same
`ready-for-agent` filter, the same `## What to build` + `## Acceptance criteria`
discriminator, the same `Closes/Fixes/Resolves #N` auto-close backstop restricted
to the iteration's AFK-ready pool, the same `MODEL` / `ISSUE_SOURCE` /
`MAX_NMT_STRIKES` env surface, and the same clean-exit-on-empty /
abort-on-stuck termination model.

That duality stopped pulling its weight:

- **Drift risk.** Every wrapper-contract change had to land in two
  implementations, and a load-bearing cross-runner parity test existed only to
  catch silent regex drift between the bash and Python close-keyword extractors.
- **Doc fan-out.** Every doc carried "pick a runner / bash vs Python" framing,
  side-by-side comparison tables, and dual-runner caveats. Every doc edit cost
  twice.
- **Single audience.** The bash runner's pitch was the smallest possible
  dependency footprint (`gh`, `jq`, `git`, `copilot` and nothing else). In
  practice this kit's operators have `uv` available, and the Python runner's UX
  (Rich-rendered iteration panels, JSONL replay log, run-summary JSON, opt-in
  OpenTelemetry tracing, `--deny-tool` / `--deny-skill` permission gating, longer
  per-iteration timeout, auto-stash of dirty leftovers) is the experience we want
  every operator to get.

Older docs also linked to a `docs/adr/0001-python-sdk-peer-variant.md` that framed
the Python runner as a "peer variant" of the bash one. **That ADR was never
authored** — the "peer variant" decision lived only implicitly in code. When the
ADR log was actually started, slot `0001` was assigned to a different decision
([`0001-observer-control-model-for-interactive-runner.md`](0001-observer-control-model-for-interactive-runner.md)),
so this retirement takes the next free number, `0002`.

## Decision

Retire the bash runner. The Python runner at `ralph/python/` is the **sole** AFK
runner.

- `ralph/sh-afk.sh` is deleted.
- `ralph/afk.sh` is retained as a one-line convenience launcher that invokes
  `uv run --project ralph/python ralph-afk` with a default model — operators who
  prefer to type `bash ralph/afk.sh` keep that entry point.
- The cross-runner parity test (`tests/test_close_keyword_parity.py`) is removed.
  With no second implementation to pin against, it no longer guards a
  cross-runner contract; the close-keyword regex stays fully covered by the
  direct unit tests in `tests/test_wrapper.py`.
- All "pick a runner / bash vs Python" framing collapses to a single-runner story
  across `README.md`, `docs/`, and `templates/`. `jq` is dropped from the
  prerequisites.

The Python runner's behaviour, env-var surface, exit codes, and observability
artefacts are **unchanged**. This is a removal: no runner behaviour is added or
altered.

## Consequences

- One implementation to maintain. Wrapper-contract changes land once, with no
  cross-runner drift to police and no parity test to keep two regexes in step.
- `jq` is no longer a prerequisite. Operators who used `bash ralph/sh-afk.sh`
  switch to `bash ralph/afk.sh` or `uv run --project ralph/python ralph-afk`.
- Operators who cannot install `uv` lose the zero-Python option. The rollback is
  a single `git revert` — the change is subtractive on the runner side and
  additive here, with no schema or contract change that would survive a revert.
- The never-written `0001-python-sdk-peer-variant.md` is formally superseded: the
  "peer variant" framing is gone, and the load-bearing decision now lives in this
  ADR rather than implicitly in code.
