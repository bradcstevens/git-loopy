# A remote Run preserves its work, halts, and Azure deletes it

**Status:** accepted design, gated on adoption proof C (#959). Implementation acceptance is tracked
by #871 and #960.

Decided by [Remote Run teardown pushes salvage before deleting the microVM](https://github.com/bradcstevens/git-loopy/issues/871).
Amends the last bullet of [ADR-0093](0093-agents-work-in-copilots-sandbox-and-a-run-may-reside-remotely.md)
decision 6.

ADR-0093 says a remote Run, however it ends, pushes its salvage Checkpoint branches to origin and
deletes its microVM. It never says who does either once the laptop is gone, and three attempts at
#871 stopped on that gap:

- The launching client holds the only Azure authority, and it closes its clients once the guest
  acknowledges the launch.
- The guest holds GitHub authority, through a PAT the egress proxy injects, and no Azure identity.
- Exit-time **Salvage** commits only to the guest's own clone, which dies with the microVM.

Azure Container Apps Sandboxes (SDK 0.1.0b4 and the platform documentation, 2026-10-21) narrow the
options:

- `autoDeletePolicy` deletes only *Stopped* sandboxes, `deleteIntervalInSeconds` after they stop.
  A Stopped sandbox keeps its disk and is not billed for compute.
- A sandbox counts as idle when nothing reaches it through ingress or the execution, shell or file
  APIs. Auto-suspend therefore stops a busy Orchestrator, so launch disables it (#860). Without
  Azure authority, nothing outside the guest can stop the microVM.
- The service models three stop reasons: `Idle`, `Disabled` and `UserStopped`. What happens when
  the guest halts itself is undocumented.
- A disk image cannot be deleted while a sandbox built from it exists.
- There is no state-change or exit event a finalizer could subscribe to.

## Decision

1. **Teardown splits by authority.** Preserving work needs GitHub's authority, which the guest
   already has. Deleting the microVM needs Azure's, which the guest never gets.
2. **A supervisor in the guest preserves.** It runs outside the Orchestrator, so it survives a
   crash, a Stop or an OOM kill. Whenever the Orchestrator exits, the supervisor:
   - Salvages every **Issue clone**.
   - Preserves everything a local **Sweep** would keep: every unresolved branch in the
     **Reserved branch namespace**, every Checkpoint-tipped branch, and any other branch left
     ahead of origin.
   - Lands each issue's work as a #905 Breadcrumb: an immutable
     `refs/heads/git-loopy-checkpoints/issue-<n>/<sha>` ref plus a Breadcrumb comment on the
     issue. Work that no single issue owns goes to one run-addressed ref beside them.

   The verb is *preserve*. *Publish* remains Integration's word for reaching base.
3. **The guest halts; Azure deletes.** Launch arms `autoDeletePolicy` with a fixed 7-day delay
   and keeps auto-suspend disabled. When preservation ends, the supervisor halts the microVM.
   Azure deletes only Stopped sandboxes, and this one stops only when its guest halts, so
   preservation always precedes deletion. No Azure credential or capability enters the guest,
   and launch grants no role.
4. **A failed preservation gets a bounded wait.** The supervisor retries for at most 30 minutes.
   Then it halts anyway, which stops compute.
   - What it could not preserve is named, by branch and SHA, on each affected issue and in a
     report on the microVM's disk.
   - Within the 7 days, a laptop client may resume the microVM and retry. After that, Azure
     deletes it.
   - This is the one place where reclamation destroys work, and it happens only in remote
     residence. Local Salvage still keeps a workspace whenever salvage fails.
   - The residence contract's "retain resources on failed salvage" becomes "stop compute and keep
     the resources for the deletion delay".
5. **The laptop is a backstop, never the trigger.** A remote Sweep (#960) works from this clone's
   receipts, both when `git-loopy remote` launches and in `git-loopy sweep`. It deletes the
   per-launch disk image Azure leaves behind, reclaims a microVM that never stopped, and retires
   spent receipts. It refuses live or unprovable Runs, and it never deletes unpreserved work.
6. **The Run record is not preserved.** Deleting the microVM deletes the Run's trace, diagnostics
   and **Summary**, and launch discloses that. How a remote Run's record should outlive Teardown
   is #961.

Adoption is gated on proof C (#959), alongside #860 and #861. The proof creates a sandbox exactly
as launch does, with auto-suspend disabled and auto-delete enabled at a short interval. It passes
only if all of these hold:

- Azure accepts that policy and reads it back.
- A root halt in the guest leaves the sandbox Stopped, neither restarted nor Failed, with its stop
  reason recorded.
- Compute allocation drops to zero.
- Azure deletes the sandbox after the interval, and its disk image is deletable afterwards.
- A control sandbox that never halts is still Running well past the interval.

If any of them fails, #871 parks and the first two options below reopen.

## Considered options

- **A managed identity on the guest's egress path.** An egress transform would inject the sandbox
  group's identity token, so the guest could delete itself on success and stop itself on failure.
  Rejected for now:
  - Every guest process could reach that authority, including Integration's unsandboxed feedback
    loops.
  - The finest documented role scope is the whole group.
  - Limiting the rule to the sandbox's own path needs a post-creation egress update, and that is
    unverified. Without it, the rule reaches every sandbox in the group, including the operator's
    credential-policy template.
- **An Azure-side reaper.** A scheduled job holding the group's identity would delete ended
  microVMs and then their images, without relying on the undocumented halt. Rejected for now: it is
  infrastructure the operator deploys and maintains, and with no exit event it can only poll.
- **Laptop-only deletion.** Rejected: the microVM would bill until the operator's next client. That
  is the forgotten VM that story 29 of Spec #856 rules out.
- **Delete at once even when preservation fails.** This was #871's original second criterion.
  Rejected: an expired PAT or a GitHub outage would destroy work that rotating the group secret
  could recover within days.
- **Never destroy unpreserved work.** Rejected: Azure's auto-delete cannot tell a preserved end
  from a failed one, so this would force one of the first two options.

## Consequences

- A cleanly ended remote Run's microVM stays Stopped for 7 days before Azure deletes it. Compute
  is not billed in that time, though Azure lists disk storage billing as coming soon. Within those
  7 days an operator can resume the microVM to inspect it or to retry preservation.
- Each launch's disk image remains until the remote Sweep reclaims it.
- Launch discloses two more facts: Teardown discards the Run record, and Azure deletes the stopped
  microVM 7 days after the Run ends.
