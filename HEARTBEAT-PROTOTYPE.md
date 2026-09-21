# PROTOTYPE — Lease heartbeat feasibility (ADR-0033 slice 5)

**Throwaway.** Nothing here is production, gated, sourced, or imported. It exists
to answer one question and to be deleted.

## The question

ADR-0033 slice 5 says to prove the heartbeat early:

> **Prove the shell implementation early** — Bash has no threads, and
> `orchestrator.sh:~1740`'s ticker subprocess is the only precedent. If the
> heartbeat cannot be made to work in all three languages, this design degrades
> to a long fixed TTL and the Lease becomes materially weaker; discover that in
> this slice, not after slice 4 has shipped.

So: can Bash, PowerShell and Python each run a Lease renewer that is independent
of agent progress, **and does not outlive a `SIGKILL`ed parent**?

The second half is the one that decides whether the design works at all. An
orphaned renewer keeps heartbeating after its Run is dead, so the Lease **never
expires** and crash recovery — the entire purpose of the TTL — silently stops
working. That failure is invisible: the Lease looks healthy forever.

## Verdict: feasible in all three. ADR-0033's fallback is NOT triggered.

The heartbeat works in Bash, PowerShell and Python, so the Lease does not
degrade to a long fixed TTL. But the two most obvious Bash patterns — including
the precedent the ADR names — are both silently broken, and so is the most
natural reading of "Python first (asyncio)".

| Port | Pattern | Result |
| --- | --- | --- |
| Bash | `( while sleep; … ) &` — the `orchestrator.sh:2555` ticker, verbatim | **LEAK** — orphan renewed forever (2 → 6 beats after `SIGKILL`) |
| Bash | death-pipe: block on a pipe the parent holds, exit on EOF | **LEAK** — see below |
| Bash | poll the parent's PID each tick (`kill -0 $parent_pid`) | **OK** — stopped at `SIGKILL`, zero orphans |
| PowerShell | `Start-Job` (separate `pwsh` process) | **OK** — child exits when the parent runspace pipe breaks |
| PowerShell | `Start-ThreadJob` (in-process) | **OK** |
| Python | asyncio task, session `await`ed | **OK** — 9/10 beats |
| Python | asyncio task, one blocking call on the loop | **STARVED** — **0 beats** |

## Three findings that would each have shipped a broken Lease

### 1. Bash: the precedent the ADR points at must not be copied verbatim

`git_loopy_monotonic_clock_start` (`orchestrator.sh:2555`) is stopped by an
explicit `kill -TERM` from the parent. A `SIGKILL`ed parent never sends it, so
the subprocess is reparented and runs on. For a tick counter that is harmless.
For a Lease renewer it is fatal. The renewer must bind itself to its owner —
polling `kill -0 <parent_pid>` each tick is sufficient and is the only one of the
three Bash shapes tested that survived.

### 2. Bash: the race-free death-pipe is unsafe *because of the agent session*

The textbook fix for PID-reuse is to have the renewer block on a pipe the parent
holds open and exit on EOF. It leaked here, and the reason generalises: **every
process the parent spawns inherits the write end.** Confirmed directly — a plain
`sleep 30 &` showed `10u FIFO` among its descriptors. In the real runner that
child is the agent session itself, which holds the pipe open for the entire
session, so the renewer never sees EOF. Bash cannot mark the descriptor
close-on-exec, so this shape needs care it does not look like it needs.

### 3. Python: `asyncio` alone is not enough, because nothing in the runner is async

`create_subprocess_*` appears in **zero** files under `git_loopy/`, while
blocking `subprocess.run` appears in 12+ modules including `git.py` and `gh.py`.
Every git and gh call therefore blocks the event loop. A renewer that is a plain
asyncio task on that loop is starved by the very first one — the spike measured
**0 beats** across a blocking call.

The magnitude matters: `DEFAULT_GATE_TIMEOUT_SECONDS = 3600.0` (`gate.py:105`) is
**12× the 300s TTL**. A single Integration gate would expire a Lease held by a
live, healthy Run — a false steal produced by the mechanism meant to prevent it.

So the Python renewer needs a **dedicated thread** (or async subprocess
throughout), not merely an asyncio task. Slice 5 should say so.

## Run it

```bash
bash git-loopy/shell/lib/heartbeat-driver.prototype.sh      # Bash, 3 shapes
bash git-loopy/powershell/heartbeat-driver.prototype.sh     # PowerShell, 2 shapes
bash git-loopy/powershell/heartbeat-verify.prototype.sh     # Start-Job is really out-of-process
python3 git-loopy/python/git_loopy/heartbeat.prototype.py   # asyncio starvation
```

Renewal is simulated by appending to a file; intervals are seconds rather than
60s. This spike is about process lifetime and scheduling, not git transport.

## What this does not answer

Ref CAS, retry, the fence, and the mirror. Those are slices 2, 3, 4 and 6 and are
untouched here.
