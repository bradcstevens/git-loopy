# A dormant path owes the Sandbox rules only once it resumes

**Status:** accepted design; implementation acceptance is tracked by #952.

Decided by [Shell Orchestrator meets the Sandbox rules](https://github.com/bradcstevens/git-loopy/issues/866)
and [PowerShell Orchestrator meets the Sandbox rules](https://github.com/bradcstevens/git-loopy/issues/868).
Amends [ADR-0093](0093-agents-work-in-copilots-sandbox-and-a-run-may-reside-remotely.md) decision 7,
and with it the exception in Wrapper contract §19. Applies
[ADR-0091](0091-a-dormant-path-stands-at-the-last-stable-family-release-carrying-its-owed-debt-frozen.md)
as written.

ADR-0093 gave the **Sandbox** rules to every Orchestrator. It expected the shell and PowerShell
ports to meet them with `copilot -p --sandbox` and no auto-approved bypass. §19 then recorded both
ports as owing `sandbox.json` (#866, #868). That was a narrow exception to ADR-0091's rule that a
**Dormant path** gains no new owed waiver, and it rested on a flag change. No flag change can meet
§19.

## Prompt mode cannot meet the Sandbox rules

The blocked attempts recorded on #866 and #868 (Copilot CLI 1.0.101-0) and a recheck on 1.0.102-1
agree:

- **No preflight.** `copilot sandbox` manages only the proxy's certificate authority, and
  `copilot config` reads saved settings. Nothing outside a session reports effective managed policy,
  enforcement or host support, so a required but unenforceable Sandbox cannot be refused before the
  first work Agent starts.
- **No fail-closed application.** Prompt mode applies its Sandbox options, but when that fails it logs
  the failure and carries on. On an unsupported host Copilot prints a warning, and sandboxed commands
  then fail.
- **No bypass denial.** Managed policy may leave `allowBypass` on, and no prompt-mode flag denies a
  bypass request: permission rules match commands, paths and URLs, not a request's bypass. JSON output
  filters out the permission and Sandbox decisions, so a denial cannot be recorded against its tool call.

The Python Runner meets §19 through runtime session calls that prompt mode does not offer: settings,
enforcement status, host support, session options and a permission handler. Meeting §19 in either
port therefore needs a new runtime adapter, written in bash and again in PowerShell.

## Decision

The exception is withdrawn, and ADR-0091 applies unchanged. `sandbox.json` was introduced at
contract 2.35, above the shell and PowerShell Path baselines' Contract support of 2.26. Neither
member owes it a row: their `sandbox.json` rows become `waived_out_of_scope`, like every other
fixture introduced after the baseline. The fixture's cases and `distributions` stay. A path that
resumes, and whose Contract support then passes 2.35, owes a claim or a waiver like any other member.
That resumption chooses the interface: a runtime adapter in the port's own language, or a prompt-mode
interface Copilot may add that proves the policy before the first prompt.

Until then the dormant ports keep launching Agents with `copilot --yolo -p`, with no Sandbox
preflight, no fail-closed check and no bypass denial. An operator who needs a Sandbox uses the
Python Runner. #952 brings §19, the Conformance register and their READMEs in line. #866 and #868
close as not planned, and Spec #856's story 31 is out of scope while those paths are dormant.

## Considered options

- **Authorize runtime adapters in both ports now.** Rejected: it is new implementation in two
  dormant ports that no 1.0.0 Release ships, which ADR-0091 schedules only when a path resumes.
- **Wait for a prompt-mode interface, tracked as a blocker.** Rejected: no such interface is
  announced, and the port work would still land in a dormant path.
- **Keep the debt owed, parked beside the carried baseline waivers.** Rejected: owing it adds nothing
  the register does not already enforce. A resumed path's Contract support brings `sandbox.json` back
  into scope, and these rows would be the only owed waivers that are not baseline debt.
