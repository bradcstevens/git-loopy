# Research: Copilot SDK/API concurrency & cost ceilings

> **Wayfinder research asset** for [map #131](https://github.com/bradcstevens/git-loopy/issues/131) · resolves ticket [#132](https://github.com/bradcstevens/git-loopy/issues/132).
> Produced by a `/research` subagent, spot-checked by the charting session. Repo-internal facts were verified; external GitHub Copilot billing/rate-limit facts are tagged `[Documented]`/`[Inferred]`/`[Unknown]` and summarised under **Open gaps**.
> **Cite-drift note:** a few `file:line` references are approximate — e.g. the SDK dependency is `github-copilot-sdk==1.0.5` at `git-loopy/python/pyproject.toml:36` (not :62). Verify a line before quoting it in the ADR.

---

## 1. SDK Concurrency

### Which SDK provides `CopilotClient` and `create_session`?

**[Observed-in-repo]** The SDK is **`github-copilot-sdk==1.0.5`**, pinned in `git-loopy/python/pyproject.toml` (dependencies list) and locked in `git-loopy/python/uv.lock`. Runtime deps: `httpx`, `pydantic`, `python-dateutil`. Import path is `from copilot import CopilotClient, CopilotSession` (`session.py`).

**[Inferred]** The SDK wraps the **GitHub Copilot CLI binary** as a long-running subprocess over JSON-RPC (the wheel ships `copilot/_jsonrpc.py`, `copilot/_cli_download.py`). `pyproject.toml` records that an older SDK "hung waiting for `session.idle`" — evidence of the subprocess/protocol architecture.

**[Observed-in-repo]** One `CopilotClient` **per `git-loopy` invocation** (`loop.py` — lazy `_make_client`, `await client.stop()` in `finally`). `create_session` accepts `working_directory`, which Parallel mode uses to pin each Lane to its own worktree: "one client can host N isolated in-process sessions" (`session.py`).

**[Observed-in-repo — no cap]** There is **no semaphore, throttle, or concurrency cap** in git-loopy. Wave dispatch is a bare `asyncio.gather` over the Lanes. N is bounded only by `config.parallel` (`--parallel N` / `GIT_LOOPY_MAX_PARALLEL`, default `_DEFAULT_MAX_PARALLEL = 3` at `cli.py:102`) and the count of eligible `parallel-safe` issues in the pool. *(Charting session confirmed: `grep -c Semaphore loop.py` = 0.)*

**[Inferred]** Whether the CLI subprocess itself caps sessions is **not publicly documented**. ADR-0008 rejected subprocess-per-agent isolation "once per-session `working_directory` was confirmed in the SDK" — implying the subprocess multiplexes sessions, but with no stated upper bound.

---

## 2. API Rate Limits

**[Documented]** GitHub moved from premium-request billing to token-metered **"AI Credits"** on **2026-06-01** (1 credit = $0.01). Per-token rates for `claude-opus-4.8` (git-loopy's default model): input $5.00 / cached input $0.50 / cache write $6.25 / output $25.00 per 1M tokens. Source: https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing

**[Observed-in-repo — mismatch flag]** git-loopy's `pricing.toml` uses **Anthropic provider list prices** ($15 in / $75 out per MTok) with an explicit caveat that these are "useful for cost-shape signal only," not Copilot's actual billing. So displayed cost figures **overstate** real Copilot spend by ~3× for Opus 4.8.

**[Documented]** Legacy annual plans keep request-based billing: `claude-opus-4.8` carries a **27× multiplier**. Quotas: Pro 300/mo, Pro+ 1500/mo, Business 300/user/mo, Enterprise 1000/user/mo. At 27×, a Pro user gets ~11 sessions/month. Source: https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/model-multipliers-for-annual-plans

**[Documented — insufficient detail]** GitHub publishes that rate limiting exists but **no numeric per-minute / concurrency threshold**. Source: https://docs.github.com/en/copilot/concepts/usage-limits

**[Inferred from community reports]** `copilot-cli 1.0.*` in agent mode reportedly hits: *"you've hit a rate limit … Please try again in 1 minute."* Throttling surfaces as **HTTP 429 + `Retry-After`**. git-loopy does **not** auto-retry on 429 — a rate-limited Lane is caught and logged as **no-progress**, and the Wave proceeds without it.

**[Unknown]** Exact numeric requests/minute, requests/hour, or concurrent-session thresholds.

---

## 3. Cost Behavior

**[Observed-in-repo]** `UsageTally` (`usage.py`) accumulates `tokens_in`/`tokens_out`/`model` ("first non-None model wins; tokens sum") and `.cost()` delegates to `pricing.estimate_cost(...)`. `pricing.py` computes exact-decimal USD from `pricing.toml` provider list prices (Opus 4.8: $15 in / $75 out per MTok, 200k window). The module docstring warns the CLI "bills on a premium-request quota that the SDK does not expose."

**[Inferred]** N Lanes multiply token spend ~linearly (N × single-session). Rough Opus 4.8 estimate at Copilot's $5/$25 rates: **~$0.38–$1.50 per session per iteration**; N=3 → **~$1.13–$4.50 per Wave**, plus the Integration/auto-resolution agent's own session(s).

**[Observed-in-repo]** No prompt-cache discount across Lanes — each iteration starts a fresh `CopilotSession` (clean conversation buffer, per the Memento model).

**[Unknown]** Whether GitHub applies any bulk/batch discount for concurrent sessions from one identity.

---

## 4. Practical N: Host-Resource Ceilings

**[Observed-in-repo]** Each Lane runs `git worktree add -b <branch> <path> <base>` (`git.py`), **synchronously and sequentially before** the `asyncio.gather` barrier (`loop.py` calls `_setup_lane_worktree(lane)` per Lane in a pre-gather loop). Worktrees share `.git/` objects (no object duplication) but create a full working-tree checkout in a sibling dir `<repo_root>.worktrees/<run_id>/issue-<N>`. Each also runs a per-Lane setup command (`GIT_LOOPY_WORKTREE_SETUP` or auto-detect — e.g. `uv sync` / `npm ci`).

**[Inferred]** CPU: the load is the **feedback-loop subprocesses** (tests/linters/compilers) running concurrently across Lanes — N Lanes each running a test suite saturates N cores. The post-Wave Integration gate re-runs full loops **serially** in the base worktree. Memory: ~50–200 MB/session for token/stream buffers → ~600 MB at N=3 before build tooling. File handles: O(10)/Lane (JSON-RPC conn, JSONL writer, subprocess handles); safe well past N=10.

### Practical ceiling summary

| Resource | Binding ceiling | Notes |
|---|---|---|
| `parallel-safe` issues in pool | Primary functional cap | N ≤ pool size; <2 eligible ⇒ serial |
| API rate limit | External, undocumented per-minute | 429 ⇒ Lane = no-progress; degrades, not crashes |
| Monthly AI-credits quota | Hard financial ceiling | N drains quota N× faster |
| Worktree setup time | Sequential pre-barrier cost | `npm ci` × N Lanes before any agent starts |
| Disk (worktree checkouts) | Linear with repo size × N | Node repos: ~1 GB/Lane plausible; 14 GB CI disk at N≥3 |
| CPU (feedback-loop subprocesses) | Physical core count | N test suites in parallel |
| Memory | ~100 MB/Lane streams | Plus per-Lane venvs/tooling |
| SDK subprocess session cap | Unknown | Confirmed ≥3 (default); upper bound unverified |

---

## Bottom line: what bounds N

- **Pool size is the primary practical bound** — the scheduler can only dispatch as many Lanes as there are eligible `parallel-safe` issues; <2 eligible already falls back to serial. [Observed-in-repo]
- **The API rate limit is an invisible, undocumented per-minute ceiling.** Hitting it logs a Lane as no-progress and burns quota with no output. A rolling scheduler should add **backpressure** (shrink N after a Wave sees multiple 429s) — git-loopy has none today. [Documented + Inferred]
- **Monthly AI-credits quota is the hard financial ceiling**; continuous N drains it N× faster. A scheduler should track burn rate and throttle. [Documented + Inferred]
- **Sequential per-Lane worktree setup can negate the win** — heavy `npm ci`/`uv sync` × N runs *before* the barrier. Rolling dispatch should overlap setup with running Lanes, not front-load it. [Observed-in-repo]
- **Disk is a real constraint** for large repos + compiled deps (N full checkouts). [Inferred]
- **SDK subprocess session cap is unknown** beyond the confirmed default of 3; N=10/20/50 needs an experiment. [Inferred]

---

## Open gaps (need an experiment or private knowledge)

1. **SDK subprocess session cap** — max concurrent `create_session` before failures/serialization. Test N=5/10/20.
2. **Per-minute rate-limit numeric threshold** — measure time-to-first-429 at various N.
3. **Real per-iteration token consumption** — not instrumented beyond `UsageTally`; needs live measurement against Copilot's actual token rates.
4. **SDK retry/backoff on 429** — does the CLI subprocess retry internally (stalling the gather) or fail fast? Compiled SDK code is opaque.
5. **Long-lived stability under sustained N≥3** — `test_loop_parallel.py` uses a fake client, not the real SDK; hours-long concurrent operation is unvalidated.
6. **Integration-agent session accounting** — auto-resolution (ADR-0009, up to K=3) spawns extra sessions, so N+K sessions can briefly run, exceeding the intended N ceiling with no accounting.
