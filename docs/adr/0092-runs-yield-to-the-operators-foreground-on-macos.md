# Runs yield to the operator's foreground on macOS

**Status:** accepted

A local Run shares the operator's CPU, and a Lane ceiling does not bound the
work one Agent's tools can launch. The Python Runner applies the **Host yield
policy** at the Run process boundary: macOS defaults to the `utility` QoS
clamp, inherited by the Copilot runtime, tool descendants and Integration
feedback loops. The foreground Dashboard client is not clamped.

## Why utility, not nice or background

[#788](https://github.com/bradcstevens/git-loopy/issues/788) records the
2026-10-12 measurements that settle
[#816](https://github.com/bradcstevens/git-loopy/issues/816). Beside 32 busy
processes, twelve default-priority foreground processes retained 10.52 cores
under `taskpolicy -c utility`, compared with 10.18 without load and only 2.62
under `nice -n 10`. Niceness did not protect the operator.

The `background` clamp protected foreground work too, but held eight busy
children to 1.63 and 1.49 cores and failed the Attach terminal test's
15-second read in both trials. Under `utility` that test passed in 30.54 and
26.01 seconds. We choose yielding under contention without imposing
background's idle-host throughput penalty.

## Boundary and operator authority

The Dashboard path launches its detached worker through `taskpolicy -c
utility`; the line-printer path re-execs its CLI process through the same
launcher before starting the harness. The CLI keeps its PID and stdio. A
PID-bound, consumed internal environment marker prevents re-exec recursion
without exempting a descendant Run. Merely passing `-c utility -p PID`
does not apply the clamp, even though taskpolicy exits successfully.

`host_yield` resolves **GIT_LOOPY_HOST_YIELD > project Config > global Config
> platform default**. `none` opts out for non-interactive hosts; it does not
raise priority inherited from another launcher. Unknown values are errors.
A missing or unlaunchable taskpolicy is a refusal with an opt-out remedy,
not permission to run silently unclamped.

Other platforms keep their existing scheduling behavior. Shell and PowerShell
parity and Wrapper/Event disclosure are separate follow-ups
([#820](https://github.com/bradcstevens/git-loopy/issues/820),
[#817](https://github.com/bradcstevens/git-loopy/issues/817)); Linux/Windows
policies, load budgets and process reaping remain open in #788. This is a
scheduling policy, not resource isolation or a CPU quota.

## Load-free proof

Automated checks read `ps -o pri` through the real CLI and detached-worker
launches and a local stand-in runtime launched by the real Copilot SDK.
They observe the driver, runtime, tool child and an actual Integration gate
feedback-loop command: `20` under utility, or the inherited priority on
opt-out (`31` on an unclamped host). The detached launch leaves its parent
unchanged. The same checks work inside an already clamped Integration gate.
No automated check recreates the CPU-load experiment.
