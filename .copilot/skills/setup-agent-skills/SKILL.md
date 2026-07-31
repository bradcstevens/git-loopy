---
name: setup-agent-skills
description: Bootstrap a GitHub-ready project and configure it for the engineering skills.
disable-model-invocation: true
---

# Setup Agent Skills

Bootstrap one target directory with:

- **GitHub-ready repository baseline** — generic repository files and GitHub collaboration templates
- **git-loopy AFK tooling** — the `git-loopy/` loop runner, cloned into the repo from the starter kit
- **Issue tracker** — where issues live (GitHub by default; local markdown is also supported out of the box)
- **Triage labels** — the strings used for the six canonical labels
- **Domain docs** — where `CONTEXT.md` and ADRs live, and the consumer rules for reading them

Establish one absolute `PROJECT_ROOT` first. Every read, write, and command after
selection targets that directory.

## Process

### 1. Select `PROJECT_ROOT`

Capture the invocation directory, then determine whether it is already inside a
GitHub project:

- `git rev-parse --show-toplevel` succeeds, and
- either `gh repo view` succeeds for that worktree or one of its remote URLs points
  at `github.com`.

When both conditions hold, set `PROJECT_ROOT` to the worktree root and skip the
directory question.

Otherwise ask:

> This directory is not an existing GitHub project. Set up the current directory
> (recommended), or choose a different directory?

When the user chooses a different directory, ask for its path. Resolve relative
paths against the invocation directory and create the selected directory when it
does not exist. Verify that the final absolute path is writable.

This step is complete when exactly one writable absolute `PROJECT_ROOT` is selected.

### 2. Explore

Inspect `PROJECT_ROOT` without assuming it is empty:

- `git remote -v` and `.git/config` — is it already a Git repository, and where is
  it hosted?
- `AGENTS.md` and `CLAUDE.md` at the repo root — does either exist? Is there already an `## Agent skills` section in either?
- `CONTEXT.md` and `CONTEXT-MAP.md` at the repo root
- `docs/adr/` and any `src/*/docs/adr/` directories
- `docs/agents/` — does this skill's prior output already exist?
- `.scratch/` — sign that a local-markdown issue tracker convention is already in use
- The paths listed in [`repository-scaffold.md`](./repository-scaffold.md) — which
  baseline files already exist?
- Is the `triage` skill installed? (a `triage` skill folder alongside this one, or `triage` in your available skills.) This decides whether Section B runs at all.
- Monorepo signals — a `pnpm-workspace.yaml`, a `workspaces` field in `package.json`, or a populated `packages/*` with its own `src/`. Present only in a genuinely large multi-package repo; their absence means single-context, which is almost every repo.

This step is complete when every listed signal has been accounted for.

### 3. Create the GitHub-ready baseline

Read [`repository-scaffold.md`](./repository-scaffold.md), then apply its entire
manifest additively in `PROJECT_ROOT`. Initialize a local Git repository there when
the target is not already in a worktree. Existing files are project-specific
replacements for the baseline and remain intact.

This step is complete when every manifest path exists or was already present, with
all pre-existing content preserved.

### 4. Fetch the git-loopy tooling

Clone only the
[`git-loopy/`](https://github.com/bradcstevens/git-loopy/tree/main/git-loopy)
directory from the starter kit into `PROJECT_ROOT`. Use the shallow, blobless,
sparse fetch below:

```bash
: "${PROJECT_ROOT:?Select PROJECT_ROOT before fetching git-loopy}"

if [ -e "$PROJECT_ROOT/git-loopy" ]; then
  echo "$PROJECT_ROOT/git-loopy already exists — skipping."
else
  TMP="$(mktemp -d)" || {
    echo "Failed to create a temporary directory" >&2
    exit 1
  }
  cleanup() { rm -rf -- "$TMP"; }
  trap cleanup EXIT

  if git clone --depth 1 --filter=blob:none --sparse \
      https://github.com/bradcstevens/git-loopy.git "$TMP/repo" &&
    git -C "$TMP/repo" sparse-checkout set git-loopy &&
    mv "$TMP/repo/git-loopy" "$PROJECT_ROOT/git-loopy"; then
    cleanup
    trap - EXIT
    echo "Cloned git-loopy/ into $PROJECT_ROOT/git-loopy"
  else
    status=$?
    echo "Failed to clone git-loopy/ into $PROJECT_ROOT" >&2
    exit "$status"
  fi

fi
```

If `git-loopy/` already exists, leave it untouched and tell the user. This is
expected when the skill runs inside the starter kit itself.

### 5. Present findings and ask

Summarise what's present and what's missing. Then take the sections in order — one section, one answer, then the next.

Lead each section with the recommended answer so the user can accept it in a word. Give a one-line explainer only when the choice genuinely branches; skip the section entirely when exploration already settled it (Section B when `triage` isn't installed, Section C when there's no monorepo).

**Section A — Issue tracker.**

> Explainer: The "issue tracker" is where issues live for this repo. Skills like `to-tickets`, `triage`, `to-spec`, and `qa` read from and write to it — they need to know whether to call `gh issue create`, write a markdown file under `.scratch/`, or follow some other workflow you describe. Pick the place you actually track work for this repo.

Default posture: these skills were designed for GitHub. If a `git remote` points at GitHub, propose that. If a `git remote` points at GitLab (`gitlab.com` or a self-hosted host), propose GitLab. Otherwise (or if the user prefers), offer:

- **GitHub** — issues live in the repo's GitHub Issues (uses the `gh` CLI)
- **GitLab** — issues live in the repo's GitLab Issues (uses the [`glab`](https://gitlab.com/gitlab-org/cli) CLI)
- **Local markdown** — issues live as files under `.scratch/<feature>/` in this repo (good for solo projects or repos without a remote)
- **Other** (Jira, Linear, etc.) — ask the user to describe the workflow in one paragraph; the skill will record it as freeform prose

Record the choice in `docs/agents/issue-tracker.md`. The GitHub and GitLab templates carry a "PRs as a request surface" flag, defaulted **off** — leave it off and don't raise it; a user who wants external PRs in the triage queue can flip the flag in the file later.

**Section B — Triage label vocabulary.** Skip this section entirely if the `triage` skill isn't installed (exploration told you) — an uninstalled skill needs no labels.

If it is installed, ask exactly one question:

> Do you want to keep the default triage labels? (recommended: **yes**)

The defaults are the six rows in
[`triage-labels.md`](./triage-labels.md). `parallel-safe` is additive: it may
coexist with a lifecycle label and means the issue is safe to implement
concurrently with other workstreams. On **yes**, write the template as-is. Only if
the user says no — usually because their tracker already uses other names — collect
the overrides so `triage` applies existing labels instead of creating duplicates.

**Section C — Domain docs.** Default to **single-context** — one `CONTEXT.md` + `docs/adr/` at the repo root. This fits almost every repo; write it without asking.

Offer **multi-context** — a root `CONTEXT-MAP.md` pointing to per-context `CONTEXT.md` files — only when exploration found monorepo signals. Then confirm which layout they want.

### 6. Confirm and write

**Pick the file to edit:**

- If `CLAUDE.md` exists, edit it.
- Else if `AGENTS.md` exists, edit it.
- If neither exists, ask the user which one to create — don't pick for them.

Never create `AGENTS.md` when `CLAUDE.md` already exists (or vice versa) — always edit the one that's already there.

Show the user a draft of:

- The `## Agent skills` block to add to the selected `CLAUDE.md` or `AGENTS.md`
- The contents of `docs/agents/issue-tracker.md`, `docs/agents/domain.md`, and `docs/agents/triage-labels.md` (the last only when `triage` is installed)

Let them edit before writing.

If an `## Agent skills` block already exists in the chosen file, update its contents in-place rather than appending a duplicate. Don't overwrite user edits to the surrounding sections.

The block:

```markdown
## Agent skills

### Issue tracker

[one-line summary of where issues are tracked]. See `docs/agents/issue-tracker.md`.

### Triage labels

[one-line summary of the label vocabulary]. See `docs/agents/triage-labels.md`.

### Domain docs

[one-line summary of layout — "single-context" or "multi-context"]. See `docs/agents/domain.md`.
```

Include the `### Triage labels` sub-block, and write `docs/agents/triage-labels.md`, only when `triage` is installed and Section B ran. When it isn't, both are omitted.

Then write the docs files using the seed templates in this skill folder as a starting point:

- [issue-tracker-github.md](./issue-tracker-github.md) — GitHub issue tracker
- [issue-tracker-gitlab.md](./issue-tracker-gitlab.md) — GitLab issue tracker
- [issue-tracker-local.md](./issue-tracker-local.md) — local-markdown issue tracker
- [triage-labels.md](./triage-labels.md) — label mapping (only if `triage` is installed)
- [domain.md](./domain.md) — domain doc consumer rules + layout

For "other" issue trackers, write `docs/agents/issue-tracker.md` from scratch using the user's description.

This step is complete when the chosen agent instruction file contains one current
`## Agent skills` block and every applicable `docs/agents/*.md` file has been
written.

### 7. Offer the project brief

Only after every preceding write is complete, ask the final setup decision:

> Would you like to provide a project overview brief before setup completes?

This is the last setup decision. When the answer is **no**, finish without another
question. When the answer is **yes**, ask the user to provide the overview text,
then read [`project-brief.md`](./project-brief.md) and apply every rule there. The
content request collects the chosen brief; it does not reopen setup decisions.

The yes branch is complete when `BRIEF.md` contains the polished brief and
`README.md` contains the matching managed overview.

### 8. Done

Tell the user the setup is complete, name `PROJECT_ROOT`, and say which engineering
skills will now read from these files. Mention they can edit `docs/agents/*.md`
directly later — re-running this skill is only necessary if they want to switch
issue trackers or restart from scratch. Ask no further questions.
