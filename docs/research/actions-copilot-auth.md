# The Copilot CLI on the GitHub Actions token path

**Question** ([#448](https://github.com/bradcstevens/git-loopy/issues/448), spec
[#445](https://github.com/bradcstevens/git-loopy/issues/445) §D): does the Copilot CLI that the
pinned SDK downloads actually authenticate on the **GitHub Actions** path git-loopy intends the
Actions **Execution host** to use — the built-in job token plus `copilot-requests: write`, with no
stored secret?

**Captured** 2026-08-23 · **Evidence**
[run 32653813938](https://github.com/bradcstevens/git-loopy/actions/runs/32653813938), a throwaway
workflow on a throwaway branch, since deleted · **SDK** `github-copilot-sdk==1.0.5` ·
**CLI** 1.0.67 · **Runner** `ubuntu-latest`

Eight probes, each in its own process with a curated environment, each reporting rather than
failing — a refused auth path is the finding, not a broken job.

## Answer

**Yes. The path works, and no premise in §D has to change.** A headless session on `ubuntu-latest`
authenticated from `${{ github.token }}` alone, listed the full 24-model roster, and completed a
real prompt-to-reply exchange on `claude-sonnet-5`. No `secrets.*` was read anywhere.

Three surprises, all of which make the Actions host ticket *easier* than the map assumed, and one
that makes it harder. In order of how much they change the plan:

1. **The documented installation-token trap does not reproduce on this CLI.** §D says an
   installation token MUST go through the environment path and not the SDK's token parameter,
   failing as a permissions error if you get it wrong. On CLI 1.0.67 **both** SDK token parameters
   accept the job token and complete a session. This is a *widening*, not a correction to act on —
   see "Still use the environment path" below.
2. **`copilot-requests: write` is load-bearing, and its absence is nameable.** Drop the scope and
   the same code fails with a message that says exactly that, distinct from every other 401.
3. **All three environment variables work, so `COPILOT_GITHUB_TOKEN` need not be set explicitly** —
   but it still should be, because it wins even when it is wrong.
4. **`actionlint` v1.7.12 — this repository's own pinned linter — rejects `copilot-requests` as an
   unknown permission scope.** The permanent Actions-host workflow will fail this repository's
   `workflow-lint` gate the day it lands.

## The probes

Read `authType` as *how the CLI resolved a token*, and the `models.list` column as *whether the
resolved token is entitled*. They disagree, which is the point of separating them.

| # | Auth shape | `auth.getStatus` | `models.list` | Session |
| --- | --- | --- | --- | --- |
| P5 | nothing set (control) | `Not authenticated` | `Not authenticated. Please authenticate first.` | ❌ `Session was not created with authentication info or custom provider` |
| P1 | `COPILOT_GITHUB_TOKEN` env | `env` — *via COPILOT_GITHUB_TOKEN, server-to-server* | 24 models | ✅ replied `ok` |
| P3 | `GH_TOKEN` env | `env` — *via GH_TOKEN, server-to-server* | 24 models | ✅ replied `ok` |
| P4 | `GITHUB_TOKEN` env | `env` — *via GITHUB_TOKEN, server-to-server* | 24 models | ✅ replied `ok` |
| P2 | `CopilotClient(github_token=…)` | `token` — *via token* | 24 models | ✅ replied `ok` |
| P8 | `create_session(github_token=…)` | `Not authenticated` | `Not authenticated. Please authenticate first.` | ✅ replied `ok` |
| P6 | bad `COPILOT_GITHUB_TOKEN` **+ good `GH_TOKEN`** | `env` — *via COPILOT_GITHUB_TOKEN* | `401 "unauthorized: AuthenticateToken authentication failed"` | ❌ `Authorization error, you may need to run /login` |
| P7 | `COPILOT_GITHUB_TOKEN` env, **no `copilot-requests` scope** | `env` — *via COPILOT_GITHUB_TOKEN* | `401 "checking server-to-server token: unauthorized: GitHub App Server-To-Server Token does not have copilot permission"` | ❌ `Authorization error, you may need to run /login` |

P5 is what makes the rest mean anything: with nothing set, the session is refused, so no pass below
is a pass that would have happened anyway.

### P0 — the ambient environment

An Actions job is handed **no token variable at all**. `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`,
`GITHUB_TOKEN`, `COPILOT_API_KEY`, `GITHUB_COPILOT_TOKEN`, `COPILOT_TOKEN` and
`COPILOT_SDK_AUTH_TOKEN` are all unset, and `gh auth status` reports *You are not logged into any
GitHub hosts*.

Copilot's precedence order therefore cannot reach anything the workflow does not export itself. The
last two rungs of that order — stored OAuth and `gh auth` — are structurally dead on a fresh runner,
which is exactly why P5 fails and why the host must set a variable rather than rely on one.

`${{ github.token }}` has the prefix `ghs_` and is 377 characters: an **installation** token, as §D
says, not a user token.

### The environment path (P1, P3, P4)

All three variables work and all three are recognised *by name* in the status message, which reports
`server-to-server` in each case. The CLI knows it is holding an installation token and accepts it.

`COPILOT_GITHUB_TOKEN` **does not have to be set explicitly**: exporting only `GH_TOKEN` — the
variable an Actions workflow conventionally sets so `gh` works — is enough for Copilot too. §D's
reasoning for setting it anyway survives intact and is now sharpened by P6: it is the variable to
set when Copilot and `gh` need *different* scopes, and it is also the variable that will silently
break the Run if it is set wrong.

### The token parameters (P2, P8) — the trap does not reproduce

The SDK has **two** `github_token` keywords and they are different mechanisms:

- `CopilotClient(github_token=…)` never sends the token anywhere. The SDK puts it in
  `COPILOT_SDK_AUTH_TOKEN` on the CLI subprocess's environment and adds
  `--auth-token-env COPILOT_SDK_AUTH_TOKEN --no-auto-login`
  (`copilot/client.py`). It is the environment path wearing a keyword, which is very likely *why*
  it works.
- `create_session(github_token=…)` genuinely does send the token, as a `gitHubToken` field in the
  JSON-RPC payload. It is scoped to that session only: in P8 the session completed while the client
  it belongs to still reported `Not authenticated` and could not list models.

Both accepted the installation token and both completed a session. **§D's claim that the token
parameter fails for an installation token is not true of CLI 1.0.67 with SDK 1.0.5.**

**Still use the environment path**, for three reasons that have nothing to do with the trap:

1. It is what `git_loopy.copilot_client.make_copilot_client` already produces — it passes
   `working_directory`, `base_directory` and `telemetry`, and never a token, so the CLI inherits
   `os.environ`. The Actions host needs **no client-construction change**, only a variable in the
   job.
2. P8's shape destroys the preflight (below): a session-scoped token leaves the client
   unauthenticated, so `list_models()` cannot be used to check anything.
3. The trap is documented upstream even though it did not reproduce. Nothing is gained by standing
   on the surface the vendor warns about when the supported one is already what the code does.

### The scope is load-bearing, and says so (P7)

With a job-level `permissions:` block carrying only `contents: read`, the identical code fails:

```
401 "checking server-to-server token: unauthorized:
     GitHub App Server-To-Server Token does not have copilot permission"
```

So `copilot-requests: write` is genuinely what entitles the job — the repository's Copilot settings
alone are not enough — and a host that is missing it can be told so in those words.

### Precedence is real and unforgiving (P6)

With a deliberately invalid `COPILOT_GITHUB_TOKEN` and a valid `GH_TOKEN`, the session **fails**.
`COPILOT_GITHUB_TOKEN` genuinely wins and there is no fallback to the next rung: a bad value at the
top of the order is not rescued by a good value below it.

## Two things the Actions host must not assume

**`auth.getStatus` reports presence, not validity.** In P6 it returned `isAuthenticated: true` for a
token that was 40 characters of nonsense. It answers *did I find a token*, never *is it any good*.
It cannot be a **Preflight**.

**The session error does not name the cause.** A bad token (P6) and a missing scope (P7) both
surface to the caller as the same string — `Authorization error, you may need to run /login` — with
only a request ID to tell them apart. The discriminating text exists only in `models.list`.

Together these give the green-base preflight its shape: **call `list_models()` once per host, before
dispatch, and report its message verbatim.** It is the only call that distinguishes *no token*, *bad
token* and *unentitled token*, it costs no model request, and it works precisely because the
environment path leaves the client — not just the session — authenticated. Under §D this is an
environment failure and never a **Strike**, and all three of its causes are operator configuration.

## The CLI version actually used

**1.0.67**, and the SDK **pins** it rather than floating:

- `copilot/_cli_version.py` in `github-copilot-sdk==1.0.5` carries `CLI_VERSION = "1.0.67"`,
  injected at publish time.
- It is downloaded from `github/copilot-cli` releases to
  `~/.cache/github-copilot-sdk/cli/1.0.67/copilot` on first use — the probe observed the download
  happen — and the binary self-reports `GitHub Copilot CLI 1.0.67`.

The issue's premise, *"the SDK downloads it rather than pinning it"*, is therefore half right: it
downloads a **pinned** version, and bumping the SDK pin is what moves the CLI. A fresh runner gets
1.0.67 deterministically.

⚠️ **But the pin only holds on an ephemeral host.** The cache directory is named for the pinned
version while the binary inside it can be replaced in place — the CLI ships a `copilot update`, and
the same path on the developer machine that wrote this was serving `1.0.81-7` out of the `1.0.67`
directory. Actions is immune because every job starts cold; the *local* Execution host is not, so
"which CLI ran" is only a property the Actions host can actually guarantee.

## What the Actions host ticket has to carry

1. **A variable, not a code change.** `env: COPILOT_GITHUB_TOKEN: ${{ github.token }}` on the
   session step. `make_copilot_client` is already the right shape.
2. **`permissions: copilot-requests: write`** at workflow or job level, plus whatever `contents` the
   contribution needs. No `secrets.*`.
3. **A `list_models()` preflight** whose message is reported verbatim, as the only call that names
   which of the three failures happened.
4. **A fix for the lint gate.** `actionlint` v1.7.12, pinned by `.github/workflows/workflow-lint.yml`
   and asserted by `tests/test_workflow_lint.py`, rejects `copilot-requests`:

   ```
   unknown permission scope "copilot-requests". all available permission scopes are
   "actions", "artifact-metadata", "attestations", "checks", "contents", "deployments",
   "discussions", "id-token", "issues", "models", "packages", "pages", "pull-requests",
   "repository-projects", "security-events", "statuses"
   ```

   GitHub Actions itself accepts the scope and honours it — P1 and P7 prove that from opposite
   sides — so this is purely the linter's roster being behind. The permanent workflow will need a
   linter bump or a scoped ignore, and that decision belongs to whoever lands it rather than to a
   surprise on the day.

## Out of scope, deliberately

Not probed, and none of it is load-bearing for the auth question: whether a fork PR's read-only
token is entitled; what identity a `server-to-server` session is billed to (`login` is `null`
throughout); the account's plan concurrency ceiling that §G wants for host-declared capacity; and
whether the job token can trigger downstream CI, which §E already answers *no* and discloses once
per Run.
