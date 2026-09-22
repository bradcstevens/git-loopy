# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Planning documents**: Titles beginning with `PRD:` or `Spec:` (case-insensitive),
  and issues labelled `wayfinder:map`, are reference documents, not work for
  git-loopy. Never label them `ready-for-agent`; remove that label if present,
  without closing the document or changing other labels. `git-loopy labels`
  reports an open document that still carries the role, and `git-loopy labels
  --apply` performs that removal. Pickup refuses the same documents, including
  an explicit `--issue` pin.
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue; its **decision tickets** are that
issue's children. This repository has GitHub's native sub-issues *and* native issue
dependencies enabled, so both use the canonical representation — never a body convention.

- **Map**: one issue labelled `wayfinder:map`, titled `Wayfinder: <name>`, holding the
  Destination / Notes / Decisions-so-far / Not-yet-specified / Out-of-scope body.
  `gh issue create --label wayfinder:map --title "Wayfinder: ..." --body-file <file>`.
  A map is a planning document, not work: label it `ready-for-human`, never
  `ready-for-agent`. Pickup refuses a `wayfinder:map` issue — by that label,
  not by a `Wayfinder:` title — and an explicit `--issue` pin is refused as a
  planning document.
- **Child ticket**: an issue linked to the map as a GitHub **sub-issue**, labelled
  `wayfinder:<type>` (`research`, `prototype`, `grilling`, or `task`). Create the issue,
  then attach it by its numeric **database id**:
  ```bash
  child=$(gh api repos/{owner}/{repo}/issues/<child-number> --jq .id)
  gh api --method POST repos/{owner}/{repo}/issues/<map-number>/sub_issues \
    -F sub_issue_id="$child"
  ```
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible edge,
  so the frontier renders in the tracker without opening the map. Again by database id,
  not `#number` or `node_id`:
  ```bash
  blocker=$(gh api repos/{owner}/{repo}/issues/<blocker-number> --jq .id)
  gh api --method POST repos/{owner}/{repo}/issues/<blocked-number>/dependencies/blocked_by \
    -F issue_id="$blocker"
  ```
- **Frontier query**: the map's open children that are unblocked and unclaimed, first in
  map order. `gh api --paginate repos/{owner}/{repo}/issues/<map-number>/sub_issues` lists
  the children; drop any that are closed, carry an assignee, or report
  `issue_dependencies_summary.blocked_by > 0` (open blockers only — the live gate).
  `--paginate` is not optional: the endpoint defaults to 30 per page, and a map big
  enough to need one is exactly the map that overflows it. Without the flag the frontier
  silently stops at the 30th child and the map reads as finished while tickets sit
  unqueried.
- **Claim**: `gh issue edit <n> --add-assignee @me`, before any work. That assignee *is*
  the claim, so it is the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then
  append the gist plus link to the map's Decisions-so-far.

The five `wayfinder:` labels are part of git-loopy's tracker vocabulary: `git-loopy init`
creates them and `git-loopy labels` reports them. See
[triage-labels.md](./triage-labels.md#wayfinder-labels).

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
