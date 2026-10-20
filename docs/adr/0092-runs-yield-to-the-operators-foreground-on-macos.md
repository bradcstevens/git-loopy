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

## A live local Run prevents idle system sleep

The Python Runner also holds a process-owned IOKit
`PreventUserIdleSystemSleep` assertion from the point its Run becomes
liveness-visible through cleanup. It is released on every return or exception;
macOS releases it on process death, including a hard kill. No helper process
can retain it after the Run dies. The detached Run owns it, not an attached
Dashboard client, so Detach does not release it.

Keeping unattended work awake takes nothing from the operator's foreground.
This assertion changes neither display sleep nor screen lock, asserts no user
activity, and changes no system power settings. It is independent of the
Host yield policy. It applies only to a local Run with a local Execution host
on macOS; remote Run residences, remote Execution hosts and other platforms
are unchanged.

`prevent_idle_sleep` defaults to `true`, with **GIT_LOOPY_PREVENT_IDLE_SLEEP >
project Config > global Config > default**. `false` opts out. Invalid values
are errors. The Run diagnostics log says `held`, `disabled`, `remote` or
`unsupported`; an acquisition failure says `not held` and refuses with the
opt-out remedy instead of silently promising protection it could not obtain.

The host can still suspend, for example on lid close. A shared per-Run
suspension clock compares macOS continuous time (includes sleep) with absolute
time (excludes sleep), so CPU contention, blocking work and wall-clock
corrections are not called suspension. On resume after more than the configured
Lease TTL, one diagnostic gives the suspension duration and the held Leases
whose last acknowledged TTL elapsed during it, including contributions waiting
for Integration. The observer polls independently of Agent output and checks
before Pickup, fencing and release can change the held set. Already elapsed or
released Leases are not attributed to a later suspension.

This is an explanation of renewal loss, not an ownership judgment.
[ADR-0033](0033-one-issue-one-run-lease.md) is unchanged: the remote fence still
decides ownership, terminal loss remains latched, and no Lease is taken back.
Injected-clock tests pin the TTL threshold and once-only report; native,
load-free checks prove the assertion's type, owner and normal/crash cleanup.

## Load-free proof

Automated checks read `ps -o pri` through the real CLI and detached-worker
launches and a local stand-in runtime launched by the real Copilot SDK.
They observe the driver, runtime, tool child and an actual Integration gate
feedback-loop command: `20` under utility, or the inherited priority on
opt-out (`31` on an unclamped host). The detached launch leaves its parent
unchanged. The same checks work inside an already clamped Integration gate.
No automated check recreates the CPU-load experiment.
