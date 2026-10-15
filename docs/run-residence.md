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

## Azure target setup (#869)

The Python Runner supports **setup only**; this does not yet register a remote
`RunResidence`, launch a VM, or change a plain Run's `local` default. The future
Azure residence consumes `remote_setup.AzureTarget.from_config(project_table)`.
Setup's `SandboxProvider.validate_target` seam is read-only and independent of
the existing Run transport/lifecycle interface.

Install the Python distribution with the `remote` extra through your configured
package feed. Sign in to Azure CLI as the intended operator (`az login`), then,
from the target repository:

```sh
git-loopy remote setup \
  --subscription 11111111-2222-3333-4444-555555555555 \
  --resource-group agents \
  --sandbox-group runs \
  --credential-policy github-policy
```

All arguments are resource identifiers, **never a PAT**. The command checks
control-plane access to the group, data-plane access to the policy sandbox, and
the existence of the referenced secret key. Missing access, missing or ineffective
injection, and unavailable SDK support refuse with remediation and leave Config
unchanged. Azure CLI authentication is explicit: git-loopy does not use
`DefaultAzureCredential` or discover service-principal environment credentials.

### Credential policy reference

The Azure preview API attaches an egress policy to a **sandbox**, not its group.
`--credential-policy` therefore names an existing operator-owned template sandbox
in the selected group. Keep it available; setup neither creates nor deletes it.
This reference is a documented mapping to the current Azure API, not an invented
group-level rule resource. The future launch provider must revalidate and apply
that policy before executing any guest code; a saved setup is not permanent
authorization or proof that a remote Run can start.

The operator configures that sandbox's egress proxy in Azure:

- Traffic inspection is `Full` or `Partial`.
- The first matching rule for each of `github.com`, `api.github.com`,
  `api.githubcopilot.com`, `api.individual.githubcopilot.com`,
  `api.business.githubcopilot.com`, and `api.enterprise.githubcopilot.com`
  is an unrestricted `Transform` that **Sets** exactly one `Authorization`
  header from a sandbox-group **secret reference**, formatted `Bearer {value}`.
  `*.githubcopilot.com` is also accepted. Broad credential-bearing wildcard
  rules, shadowed rules, path/method-limited matches, host denies, literal
  credentials and managed-identity substitutions are refused.
- All required hosts use the same secret/key reference. Keep the fine-grained
  PAT there, scoped to the target repository with **Copilot Requests** permission.
  Setup verifies reference existence using the secret **key metadata** API,
  never the secret-value/peek API. PAT scope, expiry and GitHub permission grants
  remain the operator's responsibility: setup does not retrieve the credential
  to introspect it or claim to prove its validity.

Only this non-secret table is saved in `<repository>/git-loopy/config.toml`,
preserving unrelated Config values:

```toml
[remote]
subscription = "11111111-2222-3333-4444-555555555555"
resource_group = "agents"
sandbox_group = "runs"
credential_policy = "github-policy"
```

The target is project-scoped and read through `AzureTarget`, not merged into
Lane-host or ordinary local-Run options. Manage it with `remote setup`, or
hand-edit and revalidate; `config set` is not a bypass for validation.
No policy, header value, token, secret value, or provider error body is written
to Config or a credential file, or placed on a command line.

### Optional toolchain scaffold

When neither `.devcontainer` nor `.devcontainer.json` exists, setup offers to
write `.devcontainer/devcontainer.json` with the base image
`mcr.microsoft.com/devcontainers/base:ubuntu`. Only an explicit `y`/`yes` consents;
empty input, EOF, cancellation, or another answer writes no scaffold. Existing
paths (including symlinks) are left alone, and a concurrent file creator is never
overwritten. Declining still saves the validated target, with a reminder that a
devcontainer is required before remote launch. Customize the scaffold for the
repository's toolchain. It does not install git-loopy or Copilot's Sandbox
prerequisites; layering those belongs to remote launch, as ADR-0093 specifies.

### Offline evidence and API sources

`test_remote_setup.py` covers CLI dispatch with a fake `SandboxProvider`,
project Config round-trip/preservation, refused access, and consent behavior.
`test_azure_target.py` runs the actual pinned `azure-containerapps-sandbox`
`0.1.0b4` SDK against a fake HTTP transport and synthetic credential. It allows
only group GET, policy-sandbox GET, and secret-key metadata GET; no live Azure,
credential discovery, provisioning, guest execution, or secret reads occur.

API contracts were verified against that installed SDK and:

- [Azure Sandbox architecture and its two API planes](https://learn.microsoft.com/azure/container-apps/sandboxes-overview#architecture)
- [Egress policy scope, first-match evaluation and secret-reference transforms](https://learn.microsoft.com/azure/container-apps/sandboxes-egress-policies)
- [Python SDK quickstart and SandboxGroup Data Owner access](https://learn.microsoft.com/azure/container-apps/sandboxes-quickstart-python-sdk)
