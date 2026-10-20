# Run residence provider seam

Issue #862 implements the whole-Run placement seam from
[ADR-0093](adr/0093-agents-work-in-copilots-sandbox-and-a-run-may-reside-remotely.md),
and #870 adds detached Azure launch. Lane Issue clones are a separate workspace concern
([parallel mode](parallel-mode.md)); they do not change where the whole Run
resides.

`git_loopy.run_residence.RunResidence` owns a whole Run's placement and
transport. This is independent of a Lane's Execution host. The only production
provider registered for ordinary client commands is `local`; registering another provider cannot change what a plain
`git-loopy` invocation selects.

## Interface

- `launch(RunLaunch)`: receives the target repository root, original Run
  arguments, and base revision. Returns the existing driver/client exit code.
  Local runs in the invoking worktree at `HEAD` (or its exact SHA), never
  switches a checkout, and uses the existing Config/preflight/TTY/line-printer
  path. The remote launch provider owns its configured infrastructure target
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

Setup does not register a remote `RunResidence`, launch a VM, or change a plain
Run's `local` default. Azure launch consumes
`remote_setup.AzureTarget.from_config(project_table)`.
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
group-level rule resource. The launch provider revalidates and applies
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

## Detached Azure launch (#870)

`git-loopy remote [Run arguments]` resolves the same Run Config as a plain Run.
It registers the remote residence only for this launch; a plain Run stays local.
Before allocating a VM it refuses an unpublished local default branch, a missing
devcontainer (including one not published to origin), or a `github-actions` Lane
Execution host. Origin must be a credential-free `github.com` HTTPS or SSH URL;
the guest always clones its HTTPS form at the verified default-branch revision.

In addition to `remote setup`, configure an **existing** Azure Container Registry:

```toml
[remote]
subscription = "11111111-2222-3333-4444-555555555555"
resource_group = "agents"
sandbox_group = "runs"
credential_policy = "github-policy"
registry = "yourregistry"
```

Re-running setup preserves `registry`. The launching machine needs Docker with
Linux x64 builds, the Dev Container CLI (`devcontainer`), and Azure CLI access to
push/delete images in that registry and import/create resources in the Sandbox
group. Install tools through your approved feeds. Launch never creates a
registry, grants a role, signs in, or answers an authorization prompt.

The Dev Container CLI builds the committed `.devcontainer/devcontainer.json`
or `.devcontainer.json`, not untracked laptop files. Its build receives an
isolated home and an allowlisted environment, not the operator's credentials.
The devcontainer must supply `gh`, and its build must use approved package
sources. The added layer supports Azure Linux 3 and Debian/Ubuntu: it installs
slirp4netns/libslirp, bubblewrap, iptables, Git, Python and Node/npm. Azure Linux
uses Microsoft's signed extended repository. `/dev/net/tun` is initialized
after microVM boot. A wheel made from the launching git-loopy installation
preserves its Release version and dependency metadata; the SDK's exact CLI pin
is installed from npm, including its mandatory Linux x64 package. Missing
packages fail rather than changing versions or falling back to a public feed.

Python/npm feeds come from `UV_DEFAULT_INDEX`/`PIP_INDEX_URL` and
`npm_config_registry`/`NPM_CONFIG_REGISTRY`; `NUGET_SOURCE` selects NuGet's source.
When unset, the Microsoft corporate package-feed defaults are retained in the
isolated build and guest home. These are package indexes, not HTTP proxies.
Credential-bearing feed URLs and direct-URL installation dependencies are
refused. Registry authentication uses an Azure CLI token on the host, over
stdin to Docker and in the disk-image import request; it never enters a build
argument or the guest. Temporary OCI tags and local build directories are
reclaimed after disk import.

Every VM is **created with auto-suspend and auto-delete disabled**. The pinned
Azure SDK's create helper cannot express disabled auto-suspend, so launch uses
the same `2026-02-01-preview` REST creation contract through Azure Core, then
reads the policy back. All other provider operations use the pinned SDK.
The egress policy contains secret references only; launch additionally denies
Copilot's token-exchange endpoint so a derived credential cannot return to the
guest. The guest uses public nonsecret auth placeholders. No laptop token,
Azure credential, Copilot home, or general environment is copied into it.

The operator's Copilot `sandbox` block is forwarded unchanged. The detached
Orchestrator calls the existing Run path with `residence="remote"` and local
Lanes, requiring Sandbox enforcement even when `sandbox.enabled=false`.
Launch acknowledges only after the guest records its `remote` Sandbox
disclosure. Compute is disclosed as **metered to the Azure subscription**,
without an estimate, both at launch and in the Run-start record.

**Delivery boundary:** automatic salvage/VM teardown is #871; remote `runs`,
`attach` and Stop transport is #872. Until those land, inspect the VM through
Azure's execution API; its files are under `/var/lib/git-loopy/` and its Run
trace is under `repository/.git-loopy/logs/`. The VM and imported disk remain
after the Run ends and continue to incur Azure charges until reclaimed.
Preserve and push salvage before deleting them. A failed pre-launch VM is
reclaimed; an unconfirmed execution request retains it because the detached
Orchestrator may already be running. The error identifies the VM or launch label
to inspect before retrying.

`test_remote_launch.py` covers the public command, fake-provider refusals and
guest payload, real Azure SDK/REST wire requests over an offline transport,
image-build inputs, remote always-on Sandbox routing, and a real detached child
that writes its evidence after the fake launching client has exited. No live
Azure resources or credentials are required by the suite.
