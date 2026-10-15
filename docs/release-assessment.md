# Release assessment: operator guide

The **Release assessment** is an advisory, read-only service that Copilot App
launches over stdio. It reads one repository's **Release line** and returns an
**Assessment verdict** with a **review packet**. It never closes a milestone,
approves a release or writes to GitHub: closing the `vX.Y.Z` milestone, which
triggers **Promotion**, stays your decision.

## Install

The service is the `git-loopy-assessment` console script, shipped by the
`assessment` extra of the Python path:

```bash
uv tool install "git-loopy[assessment]"
```

Its service version is the installed `git-loopy` version.

## The GitHub App and its key

The service reads through a dedicated, repository-restricted, read-only GitHub
App installation. It never uses your `gh` login or an ambient token, and without
its key it fails closed.

1. Register one App per operator, install it on the repository only, and grant
   it the read permissions in the matrix below.
2. Generate a private key, and place it where only you can read it:

   ```bash
   install -m 600 ~/Downloads/app.private-key.pem ~/.config/git-loopy/assessment/app.pem
   ```

   On POSIX the service refuses a key that is not a regular file, is not owned by
   the running user, is readable by group or others, or (on macOS) carries an
   extended ACL. On Windows nothing is enforced: remove inherited permissions and
   grant read access to your account alone (`icacls <file> /inheritance:r
   /grant:r "%USERNAME%:R"`).
3. Revoke the key when the pilot ends.

## Configuration

One hand-edited TOML file,
`<config-home>/git-loopy/assessment/config.toml` (`$XDG_CONFIG_HOME` or
`~/.config`), with one table per repository.

- **The allowlist is the set of tables.** Any other repository is refused.
- `workflows`: the counted workflows, by workflow name.
- `app_id`, `installation_id`: positive numeric strings.
- `app_identity`: the App's name; `key_file`: the path to the key.
- `sandbox = true` marks a sandbox repository. Only a sandbox table may carry
  `faults`; each entry names a `read` and a `fault` (`rate-limited` or
  `pagination-cut`) and can only push a verdict toward `incomplete`. Active
  faults are stamped in the packet's provenance.
- The **assessment policy** revision is a content hash of the repository's
  table, computed at start. A configuration edit changes it only on restart.

The first git-loopy policy:

```toml
["bradcstevens/git-loopy"]
workflows = [
  "Runner family gate",
  "Cross-repo Skills proof",
  "Lint the workflows",
  "Promote Release line",
]
app_id = "123456"
installation_id = "7654321"
app_identity = "git-loopy release assessment"
key_file = "/Users/me/.config/git-loopy/assessment/app.pem"
```

A sandbox table carrying one fault entry:

```toml
["me/git-loopy-sandbox"]
workflows = ["Runner family gate"]
app_id = "123456"
installation_id = "7654322"
app_identity = "git-loopy release assessment"
key_file = "/Users/me/.config/git-loopy/assessment/app.pem"
sandbox = true
faults = [{ read = "Counted checks", fault = "rate-limited" }]
```

## Register in Copilot App

Add `git-loopy-assessment` as a **local stdio MCP server**; it has no arguments
and opens no network listener. Restart the server in Copilot App after every
configuration edit, since the policy is read once at start.

## The two tools

- `assess`: takes `repository` (an allowlisted `owner/name`) and optionally
  `milestone` (`vX.Y.Z`; required when more than one is open). Returns the
  structured result and the rendered review packet.
- `check_freshness`: takes `repository`, `milestone` and the pinned `sha`.
  Returns `fresh` or `stale`. Stale means the trunk head moved or the milestone
  is no longer open under that title; re-run `assess` rather than refresh.

Inputs are typed and bounded. None names a credential, URL, path or query, and
there is no write tool.

## The Copilot App Skill

Install the `release-assessment` Skill from
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills/tree/main/skills/release-assessment)
in Copilot App and invoke it explicitly after registering the service. It shows
the packet verbatim and checks freshness before every follow-up. Asked “can I
ship?” or “can I close it?”, it repeats the verdict and advice line rather than
answering yes or no. The only action it offers is re-running the assessment.

The Skill understands result-format versions `1` and `2` (sandbox fault
provenance). An unknown version is still displayed verbatim, but follow-ups
are declined until the Skill is updated. Without both service tools it reports
“Release assessment service is not configured.” and stops.

Runs have no assessment service. The Skill is neither Required nor Permitted
by any in-Run Agent profile, and both Run instruction copies explicitly exclude
it even if an operator enables it in the Skill policy.

## The three verdicts

| Verdict | Meaning |
| --- | --- |
| `reviewable` | Evidence is complete and no hold finding exists. **Not authorization.** |
| `hold_advised` | Evidence is complete and at least one hold finding exists. |
| `incomplete` | Some read failed or could not be classified. It beats `hold_advised` and still lists every hold already found. |

## What the assessment never does

It never authorizes, approves or gates a release, and `reviewable` never implies
the line is safe to promote. It never closes a milestone, writes a label,
comment or tag, or consults Rehearsal, Release smoke or release artifacts.
Rehearsal and Release smoke still prove the snapshot after you close the
milestone.

## Endpoint-to-permission matrix

Every request is a GET on this fixed list, plus the token exchange. Register the
App with exactly these permissions.

<!-- endpoint-matrix:start -->
| Method | Endpoint | Permission |
| --- | --- | --- |
| GET | `/repos/{repository}/installation` | App JWT |
| GET | `/app` | App JWT |
| POST | `/app/installations/{id}/access_tokens` | App JWT |
| GET | `/repos/{repository}` | Metadata |
| GET | `/repos/{repository}/commits/{ref}` | Contents |
| GET | `/repos/{repository}/contents/VERSION` | Contents |
| GET | `/repos/{repository}/milestones` | Issues |
| GET | `/repos/{repository}/actions/runs` | Actions |
| GET | `/repos/{repository}/issues` | Issues |
| GET | `/repos/{repository}/pulls` | Pull requests |
| GET | `/repos/{repository}/tags` | Contents |
| GET | `/repos/{repository}/compare/{base}...{sha}` | Contents |
| GET | `/repos/{repository}/issues/{n}/dependencies/blocked_by` | Issues |
<!-- endpoint-matrix:end -->

**Permissions to leave out.** Of the spec's candidate set (Metadata, Issues,
Pull requests, Contents, Checks, Commit statuses, Actions), no endpoint needs
**Checks** or **Commit statuses**: counted checks are workflow runs looked up
by name through Actions. Leave both off the App registration, all at read-only.
