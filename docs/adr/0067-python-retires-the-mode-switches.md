# Python retires the mode switches

**Status:** accepted

Amends [ADR-0008](0008-across-issue-parallelism-via-git-worktrees.md) for the Python
Orchestrator only. That decision's opt-in `--parallel` / `COPILOOP_MAX_PARALLEL`, and the
later interactive switches, stay the shell and PowerShell fork. They do not stay a Python
flag.

## Decision

A bare `git-loopy` on the Python distribution is always **Parallel mode** and always
attachable. There is no flag and no environment variable that selects either.

Removed, with nothing replacing them: `--parallel` on `run` and on `calibrate` (including
the refuse-to-spend-at-one guard), `--interactive`, `--no-interactive`,
`GIT_LOOPY_MAX_PARALLEL`, `GIT_LOOPY_INTERACTIVE`, `GIT_LOOPY_LANE_ADAPT`, the `parallel`
field on `RunConfig`, and the resolution code behind them. Passing a removed flag, or
exporting a removed variable, is a preflight refusal that names what replaced it. Accepting
the switch and ignoring it is the defect this removal exists to prevent. `--version` still
exits before that check.

**Always attachable means a Dashboard is available, never required.** A terminal spawns the
client; a non-terminal runs the line printer. The startup picker and the auto-`init` wizard
remain prompts that run before the Run detaches.

**Serial survives as an Iteration driver, not a dispatch mode.** A Run with no Lane work
still reaches the same outcomes through that driver, and the degraded and serial-fallback
Events are the only way an operator learns it went serial.

The contract rows for `GIT_LOOPY_INTERACTIVE` and `GIT_LOOPY_MAX_PARALLEL` stay MUST rows.
They are honoured only by a member whose parallel capability manifest exposes that operator
choice. Python does not declare the choice and refuses both variables. Shell and PowerShell
keep their flags, their variables, and their precedence; deleting the rows would break two
members to serve a Python need, and faking the rows as no-ops would ship the ignored switch
deliberately. No new manifest key is added: the existing pins already say which member
schedules Lanes, and a new key would rewrite the Conformance assertions this retirement
leaves unchanged.
