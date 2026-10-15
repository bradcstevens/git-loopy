# Run residence provider seam

Issue #862 implements the whole-Run placement seam from
[ADR-0093](adr/0093-agents-work-in-copilots-sandbox-and-a-run-may-reside-remotely.md),
not remote residence. Lane Issue clones are a separate workspace concern
([parallel mode](parallel-mode.md)); they do not change where the whole Run
resides.

`git_loopy.run_residence.RunResidence` owns a whole Run's placement and
transport. This is independent of a Lane's Execution host. The only production
provider is `local`; registering another provider cannot change what a plain
`git-loopy` invocation selects.

## Interface

- `launch(RunLaunch)`: receives the target repository root, original Run
  arguments, and base revision. Returns the existing driver/client exit code.
  Local runs in the invoking worktree at `HEAD` (or its exact SHA), never
  switches a checkout, and uses the existing Config/preflight/TTY/line-printer
  path. A future remote provider owns its configured infrastructure target
  and immutable-base validation.
- `discover(git=...)`: enumerates only Runs associated with this clone's
  control domain and supplies their proven liveness. Failure raises
  `RunTargetError`; it must not masquerade as an empty or partial listing.
- `open_attach(run, repo_root=...)`: opens an `AttachChannel`, whose `observe`
  method follows the Run until Detach or end. Artifact paths belong to the
  provider and need not exist on the viewing machine.
- `request_stop(run, request_id, timeout, output)`: owns delivery and
  acknowledgment. Zero means the Run acknowledged the requested stage, not
  merely that a request was submitted. Redelivery keeps the same identity.
- `teardown(run)`: owns residence resource cleanup at **Run completion**, not
  at client disconnection. Push salvage before deletion; retain resources on
  failed salvage. A destructive provider must recheck authority before deletion.
  Local adds no cleanup: existing Run/Sweep salvage and Lane cleanup stand,
  and the source checkout and Run artifacts are retained.

## Registration and routing

`register_residence(provider)` is a context manager for the application's
lifetime or a test's scope. Names must be nonempty and unique; `local` cannot
be replaced. The registry is process-local, not a machine-wide Run index.

`discover_resident_runs` aggregates every provider, stamps each result with
its authoritative residence, and retains the existing newest-first order.
`runs`, `attach`, and `stop` share this listing and the existing explicit
identity resolver. Ambiguity across providers refuses rather than guessing.
The local-only table and command messages are unchanged.

`launch_run` defaults explicitly to `local`. `teardown_run` refuses live and
unprovable Runs before routing to the owning provider. It is not called when
an attached client exits: a detached local worker already owns its lifetime
and cleanup, and no additional residence resources exist to reclaim.

## Evidence

`tests/test_run_residence.py` exercises fake-provider listing, launch inputs,
Attach/Stop transport routing, teardown, default-local selection, registration
cleanup, unavailable providers, and identity collisions. Existing public-command
and detached-worker tests continue to prove local behavior with real processes,
control locks, traces, and Stop acknowledgments.
