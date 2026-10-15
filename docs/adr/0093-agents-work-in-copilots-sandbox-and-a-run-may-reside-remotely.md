# Agents work in Copilot's Sandbox, and a Run may reside remotely

**Status:** accepted design; implementation acceptance is tracked by the
delivery issues.

**Partially supersedes:** [ADR-0068](0068-execution-hosts-own-contributions-clients-own-terminals.md),
in two places only: its refusal of local blast-radius containment, and its
placement of Lane workspaces inside the clone's git directory. Its two
**Isolation grades**, Execution host model, client model and meter rule stand.

## Context

ADR-0010's spike (CLI 1.0.67) found no way to sandbox the headless SDK session
a Run drives, and ADR-0068 then refused local containment by grade. On
2026-10-14, with `github-copilot-sdk` 1.0.17rc5 and CLI 1.0.98, that fact
changed:

- `--sandbox` passed to the headless runtime, and a saved `sandbox` block read
  through `config_directory` or `COPILOT_HOME`, are both still ignored: tools
  report `sandboxed=None` and a write to `$HOME` succeeds.
- Calling `session.options.update` with a `sandboxConfig` right after
  `create_session` **is** enforced: tools report `sandboxed=true`, the `$HOME`
  write fails with `Operation not permitted`, and `git` authenticates through
  Copilot's masking proxy. `gh` fails TLS until the proxy's certificate
  authority is trusted (`copilot sandbox ca trust`).
- A worktree under the clone's `.git` cannot commit from a Sandbox without
  granting the shared git directory, which holds every other issue's refs. A
  `git clone --shared` granted read/write, with the source object store and
  `~/.gitconfig` read-only, commits in the Sandbox and leaves the source
  untouched.

The operator also wants to start a Run, close the laptop, and know the
Orchestrator and every issue it picks up keep working remotely and securely.
Copilot's cloud sandbox would be the natural home, but GitHub documents it as
interactive-only, and the headless CLI confirms it: `create_session(cloud=…)`
answers `Unhandled method session.create`, and `sessions.open {kind:"cloud"}`
answers `Unsupported native server host request
'request_sessions_open_remote'`, with or without `--experimental`.

## Decision

**Sandbox** means GitHub Copilot's own sandbox, as GitHub defines it. It is
disclosed once per Run beside the Isolation grade and is never a grade:
GitHub itself places local sandboxing at the light end of isolation, with
best-effort file tools.

1. **Every Agent session works in a Sandbox** — serial Iterations, Lane
   contributions and Recovery sessions. Integration's feedback loops are the
   Runner's own processes and stay outside it.
2. **The policy is the operator's.** git-loopy forwards the operator's Copilot
   `sandbox` settings unchanged, adding only what git needs: the issue's clone
   read/write, the source object store and git config read-only. Every
   per-command bypass is denied, whatever `allowBypass` says: no human is
   present to judge one.
3. **Each issue gets its own Issue clone**, a self-contained clone sharing the
   source's objects read-only, replacing the worktree inside `.git`. It is the
   boundary of that issue's Sandbox.
4. **Fail closed.** A Run that requires a Sandbox refuses at preflight when the
   host cannot enforce it, and aborts an Agent whose tool reports anything but
   `sandboxed=true`. This is the silent bypass ADR-0010 warned about.
5. **Run residence** is `local` or `remote`, and `local` is the default. A plain
   `git-loopy` Run always resides locally and sandboxes exactly when the
   operator's `sandbox.enabled` is true (or managed policy requires it). An
   operator who never runs `git-loopy remote` never leaves the local Sandbox.
6. **`git-loopy remote [run arguments]`** starts a Run whose whole Orchestrator
   lives in one Azure Container Apps Sandbox microVM; `git-loopy remote setup`
   records and validates the Azure target. Inside it:
   - every issue's Agents still work in their own local Copilot Sandbox, which
     is always on regardless of `sandbox.enabled`, with the policy forwarded
     from the launching machine;
   - the Run clones the target's origin, and refuses to start while the
     operator's local default branch is ahead of it;
   - Lanes use the `local` Execution host inside the VM; selecting
     `github-actions` as the Lane host is refused, but Agents and Integration
     may still trigger and read CI workflows;
   - the GitHub and Copilot credential, a fine-grained PAT scoped to the target
     repository with Copilot Requests, is injected by the egress proxy and never
     exists inside the guest;
   - the toolchain comes from the target repository's `.devcontainer`, with
     git-loopy and the Sandbox's Linux prerequisites layered on; without one,
     the Run refuses and `remote setup` offers to generate one;
   - `attach`, `runs` and Stop are the same client operations as for a local
     Run, carried over the Container Apps execution API;
   - when the Run ends for any reason, salvage Checkpoint branches are pushed
     to origin and the VM is deleted. Compute is disclosed as metered to the
     Azure subscription, with no estimate, under ADR-0068's meter rule.
7. **The Sandbox rules are a Wrapper-contract obligation** with a Conformance
   fixture owed by every Runner-family member; shell and PowerShell meet it
   through `copilot -p --sandbox` and stop auto-approving bypasses. Remote
   residence starts in the Python Runner only.

Adopting Azure Container Apps Sandboxes is gated on two proofs: a detached
Orchestrator survives the VM's auto-suspend rules, and Copilot's Linux Sandbox
(bubblewrap and its network-namespace prerequisites) runs inside the microVM.

## Considered options

- **A third Isolation grade for the local Sandbox:** rejected; it would
  overstate a containment GitHub itself calls lightweight and best-effort.
- **A git-loopy-defined sandbox policy:** rejected; the operator's Copilot
  settings are the policy's home, and a second vocabulary would drift.
- **Keep worktrees and grant the shared `.git`:** rejected; any issue could
  rewrite another issue's branches, making per-issue Sandboxes nominal.
- **Degrade like Copilot does when the Sandbox is unavailable:** rejected for
  unattended Runs; a Run that says "sandboxed" and is not is worse than none.
- **Copilot cloud sandboxes for remote Runs:** rejected for now because they
  have no headless path. Revisit when the headless CLI accepts
  `sessions.open {kind:"cloud"}`.
- **GitHub Actions or Codespaces for remote Runs:** rejected as the first
  remote residence; a hosted job stops at six hours and a codespace at twelve,
  shorter than a Run.
- **Tokens as VM secrets, or an operator login inside the VM:** rejected; a
  secret present in the guest is readable by every process there.

## Consequences

- The Lane workspace lifecycle (location, Sweep, salvage) moves to Issue
  clones; reclamation still keys on the Reserved branch namespace, not a path.
- Isolation grade disclosure is unchanged; Sandbox state (`local`, `off`, or
  the remote Run's always-on Sandbox) becomes a second, separate disclosure.
- Operators using `gh` inside a Sandbox must trust Copilot's proxy certificate
  authority once; until then `gh` fails TLS while `git` works.
