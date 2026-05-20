# ADR 0001 — Python SDK peer variant of the AFK runner

- **Status:** Accepted
- **Date:** 2026-05-16
- **Established by:** Issue #2 — scaffold of `ralph/python/`
- **Parent PRD:** Issue #1 — *GitHub Copilot SDK Python variant of the AFK runner (`ralph/python`)*

## Context

The kit ships `ralph/sh-afk.sh` as a pure-bash autonomous loop on top of the
GitHub Copilot CLI's `--output-format json` event stream. The bash runner
is austere by design — no Python, no Docker, no sandbox — and serves the
minimal-dependency audience well. With the Copilot CLI now publishing an
[official Python SDK](https://github.com/github/copilot-sdk/tree/main/python)
(`github-copilot-sdk` on PyPI) that exposes the same JSON-RPC server as a
typed async client with first-class event streams, telemetry hooks, and
permission callbacks, we have an opportunity to ship a richer terminal
experience and structured observability without giving up the bash variant.

Two load-bearing decisions had to be made before any code landed. This ADR
exists because both decisions are hard to reverse once docs reference both
runners, surprising without context (a future reader sees two runners and
wonders why), and the result of a real trade-off.

---

## Decision 1: Peer variant — not replacement, not subprocess wrapper

`ralph/python/` is a peer to `ralph/sh-afk.sh`. The bash runner stays
first-class and fully supported. Both runners read `ralph/PROMPT.md` and
honour the same wrapper contract:

| Rule | Both runners enforce |
|---|---|
| Stale-worktree guard | `git diff --quiet` + `git diff --cached --quiet` before each iteration. |
| AFK-ready filter | `ready-for-agent` label **plus** body contains both `## Parent` and `## Acceptance criteria`. |
| Last-5-commits prompt prefix | `git log -n 5 --format='%H%n%ad%n%B---'` prepended to the prompt. |
| Auto-close backstop | `(close[sd]?\|fix(es\|ed)?\|resolve[sd]?)\s+#[0-9]+` (case-insensitive), restricted to the iteration's AFK-ready pool. |
| NMT strikes | Default 3 consecutive no-progress iterations before aborting; overridable via `MAX_NMT_STRIKES`. |
| `<promise>NO MORE TASKS</promise>` | Informational only — counted as a strike if no-progress, ignored if work was done. |
| Clean exit (0) on empty AFK pool | First-check-at-iteration-start. |
| Abort (1) on `MAX_NMT_STRIKES` reached | Stuck-agent surfacing. |
| Positional `<max-iterations>` | Identical (`0` / omitted = unlimited). |
| Env vars `MODEL` / `ISSUE_SOURCE` / `MAX_NMT_STRIKES` | Identical. |
| `ISSUE_SOURCE=github` (default) + `ISSUE_SOURCE=prds` (legacy) | Both supported. |

Python-specific safety extension: after an SDK iteration completes, tracked
dirty leftovers are preserved with `git stash push -u` before the next
iteration starts. The bash runner still surfaces the same situation by
aborting on the next iteration's stale-worktree guard.

Two alternatives were rejected:

- **Replacement.** Would violate the kit's "without losing existing
  functionality" promise and force a Python toolchain on every downstream
  adopter, including ones who deliberately chose the bash-only kit for its
  zero-Python footprint.
- **Python wraps bash via subprocess.** The `--output-format json | jq`
  pipe the bash runner consumes loses event fidelity (per-tool args,
  reasoning deltas, permission events). Wrapping the bash runner gives a
  strictly worse signal than wiring the SDK directly — there is no upside
  to that path.

### Consequence: parity is locked down by a test

The cross-runner regex contract is the single most load-bearing test in
the Python suite. Issue #3's `tests/test_close_keyword_parity.py` runs
**both** the bash regex (via a `grep -iEo` subprocess) **and** the Python
`CLOSE_KEYWORD_RE` against a shared corpus and asserts identical issue-
number output. If that test ever fails, **the failure is the spec** —
bash or Python has drifted and the corpus tells you which case broke.

### Consequence: future wrapper-rule changes are paired changes

A rule change that lands in `ralph/sh-afk.sh` without a matching change in
`ralph/python/ralph_afk/wrapper.py` (and vice versa) is a regression in
the cross-runner contract. The parity test catches close-keyword drift;
non-regex rule changes (e.g. AFK-ready discriminator) must be paired by
hand and noted in the commit that introduces the change.

---

## Decision 2: Memento Model preserved at the session level, not the process level

The kit's [`README.md`](../../README.md#the-memento-model) defines the
**Memento Model**:

> Every iteration starts from zero (system prompt + `AGENTS.md` + the
> issue). The agent forgets everything between iterations.

The bash runner achieves this by re-spawning a fresh `copilot` subprocess
per iteration. Three kinds of freshness are conflated in that invocation:

1. **Fresh OS process** (re-spawned `copilot` subprocess).
2. **Fresh JSON-RPC server** (re-bound port/stdio).
3. **Fresh model context** (no conversation history).

**Only (3) is load-bearing for the Memento guarantee.** The model's
behaviour depends on its context window, not on the process that hosts the
server. The Python variant therefore:

- Constructs **one long-running `CopilotClient` per `ralph-afk` invocation**.
- Creates **a fresh `Session` per iteration** — so (3) is preserved.
- Reuses the JSON-RPC transport across iterations — so (1) and (2) are
  not paid for again per iteration.

### Consequence: the renderer subscribes once, not per iteration

The session slice (issue #9) subscribes the Rich renderer to
`session.on(event)` for the lifetime of each `IterationSession`. Because
the `CopilotClient` is reused, the renderer does not pay re-binding cost
between iterations.

### Consequence: process startup is paid once, not per iteration

Hundreds of milliseconds of subprocess startup per iteration are saved.
For a 24-hour AFK run with thousands of iterations, this matters.

### Consequence: a crashed iteration must not corrupt subsequent ones

If the SDK `Session` for iteration N enters a bad state (uncaught
exception, transport hiccup), the next iteration's `IterationSession`
constructor must be free of state from the previous one. The session
slice's `__aexit__` must clean up unconditionally, even on error paths.

---

## Status

Accepted by issue #2. Subsequent slices (issues #3-#13) build on both
decisions and must not contradict them without superseding this ADR.
