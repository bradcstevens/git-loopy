# ISSUES

Issues for this iteration are provided at the start of context. They come from one of these sources, distinguishable by the delimiter used in each block:

- **GitHub Issues** (default — see `docs/agents/issue-tracker.md`): each block is headed `=== Issue #N: <title> [labels: ...] ===` and may include a `--- Recent comments (newest first, up to 5) ---` section. The `gh` CLI is the contract for reading/commenting/closing — never edit issues by URL or web UI.
- **GitHub pull requests** (only when `docs/agents/issue-tracker.md` sets `PRs as a request surface: yes`): each block is headed `=== PR #N: <title> [labels: ...] (branch: <head-branch>) ===`. These are existing PRs that a human (or `/triage`) marked `ready-for-agent` for you to push forward — see **Pull-request mode** below. Same `gh` contract; never merge or close them.
- **Local markdown** (legacy `ISSUE_SOURCE=prds`): each block is headed `=== <path> ===` where the path is `prds/<feature>/NNN-*.md`, sibling to a `prd.md`. Archived issues live in `prds/<feature>/done/`.

Every issue you receive is **AFK-ready** — the wrapper script has already filtered to issues that carry the `ready-for-agent` label and have a `## What to build` plus `## Acceptance criteria` section (a `## Parent` section is optional). Do not pick up anything else. Do not work on the parent PRD itself. PR blocks are AFK-ready by a different test — they carry an `## Agent Brief` (in the PR body or a comment); follow that brief.

You've also been passed the last few commits. Read them to understand what work has been done in prior iterations and avoid redoing it.

If after filtering AFK-ready issues you genuinely have no work, output `<promise>NO MORE TASKS</promise>` and stop.

# DOMAIN AWARENESS

Before exploring code or proposing changes, read `docs/agents/domain.md` for the consumer rules. If `CONTEXT.md` (root) exists, treat it as the glossary; if `docs/adr/` exists, respect any ADRs that touch the area you're about to change. Use the project's vocabulary in issue comments, commit messages, test names, and module names. When in doubt about how a section of code fits in, go up a layer of abstraction and map the relevant modules and callers (in the project's glossary vocabulary) before drilling in.

# TASK SELECTION

Pick exactly one task. Prioritise in this order — and at each priority, use the skill noted in parentheses if applicable:

1. **Critical bugfixes** — use `/diagnosing-bugs` to build a feedback loop, reproduce, hypothesise, instrument, and only then fix. Never patch a hard bug without a reproducing signal.
2. **Development infrastructure** (tests, types, dev scripts, CI) — no specific skill; just get the loop healthy. This unblocks every later task, so it outranks features.
3. **Tracer bullets for new features** — for non-trivial state/data-model or UI decisions, sketch with `/prototype` first (LOGIC branch for state, UI branch for visuals), then implement the slice with `/tdd`. A tracer bullet is a thin, end-to-end vertical slice through every layer.
4. **Polish and quick wins** — implement with `/tdd`.
5. **Refactors** — use `/codebase-design` to find deepening opportunities (a lot of behaviour behind a small interface at a clean seam) first, then implement the agreed change with `/tdd`.

If you're about to commit to a non-trivial plan (cross-cutting refactor, ambiguous requirements, new module boundary), pause and stress-test that plan against the domain docs (`docs/agents/domain.md`, `CONTEXT.md`, and any ADRs under `docs/adr/`) before you start — does it fit the existing vocabulary and decisions? Cheap stress-test, big save when you're wrong.

# DECLARE YOUR ACTIVE ISSUE (working marker)

Once you've picked your single task — and **before** you start exploring — declare it up front by emitting a **working marker** on its own line, exactly: `<working issue=N>`, where `N` is the number of the issue you selected (for `prds` mode, the number from its filename).

This is an **additive** live-attribution signal: on the interactive path the runner taps it to light up the **active issue** and start its live timer the moment you declare it, and the per-run **queue** uses it to attribute this iteration's work to that issue. It is silently ignored on the non-interactive path.

The marker changes nothing else: you still **pick exactly one task** by the priority order above, and you still close the issue with a `Closes #N` close-keyword exactly as described under **COMMIT** / **THE ISSUE** below. If you omit the marker, the runner infers the active issue from your commit-time `Closes #N` backstop. Emit it once per iteration, for the single issue you chose.

# SKILLS NOT TO INVOKE

Many skills in `.copilot/skills` exist for **human-driven sessions or upstream work** and are out of scope for this autonomous loop. Leave the ones below alone — they're grouped by *why* they don't belong in an unattended iteration.

**Upstream issue-creation & requirements capture** — these run *before* the loop; the loop only works tickets that a human (or `/triage`) has already marked `ready-for-agent`:

- `/triage` — relabels issues into the `ready-for-agent` pool.
- `/to-spec`, `/to-tickets` — create or relabel issues (a spec and its sliced tickets) upstream of the loop.
- `/to-questionnaire`, `/intake`, `/wayfinder` — capture and shape requirements into specs/tickets; the loop consumes their output, it doesn't produce it.

**Human-in-the-loop skills** — they need a person to answer, so they can't run unattended. Most are `disable-model-invocation: true`; `/grilling` is model-invocable but still needs a human to grill:

- `/grill-me`, `/batch-grill-me`, `/grill-with-docs`, `/grilling` — interrogate a human about a plan or decision.
- `/improve-codebase-architecture` — a human-driven architecture review; for autonomous refactors use `/codebase-design` instead.
- `/teach` — walks a human through an area of the code.

**Session-management, setup & authoring** — irrelevant to a fresh one-shot `copilot -p` iteration:

- `/handoff` — pointless here because each iteration is a fresh one-shot invocation; persistence happens via commits and (sparingly) issue comments, not handoff docs.
- `/implement` — a human-driven "implement this spec end-to-end" orchestrator; this loop already *is* that orchestration (it picks one task, drives `/tdd`, and commits), so invoking it would just nest a second driver.
- `/setup-agent-skills`, `/writing-great-skills` — install or author skills, not loop work.

The guidance the excluded and now-removed skills used to carry still holds and is already inlined above: favour reviewable output over token compression while running unattended, go up a layer to map an unfamiliar area before drilling in, stress-test plans against the domain docs, and reach for deep-module design via `/codebase-design`.

# EXPLORATION

Explore the repo for the task you've selected. Stay within the area the issue touches; don't grand-tour the codebase. If you're unfamiliar with an area, go up a layer first and map its modules and callers before drilling in.

# IMPLEMENTATION

Use `/tdd` to complete the task. Vertical slices only — one test, one minimal implementation, repeat. No horizontal slicing (don't write all tests then all code).

# FEEDBACK LOOPS

Before committing, run the feedback loops defined in `AGENTS.md` that are relevant to what you changed. AGENTS.md is loaded into your context — read its **Feedback loops** table for the exact commands for this repo.

Run only the loops your change touched. If a loop's tooling doesn't exist yet (the repo is pre-scaffold), the only loops you can verify are the acceptance criteria on the issue itself. Once a loop is wired up, use it.

# COMMIT

Make a git commit. The commit message must:

1. Reference the issue with a **GitHub closing keyword** (`Closes #N`, `Fixes #N`, or `Resolves #N`) for GitHub mode, or include the issue path for `prds` mode. The wrapper relies on this exact form (case-insensitive `close[sd]?|fix(es|ed)?|resolve[sd]?` directly followed by `#N`) as a backstop — if you forget to call `gh issue close`, it will close the issue for you, but only if the commit message uses one of those keywords.
2. Include key decisions made
3. Include files changed
4. Note any blockers or follow-ups for the next iteration

# THE ISSUE

## GitHub mode (default)

When the task is complete, run this **FINAL SEQUENCE** in order. Do NOT end the turn until every step has succeeded:

1. **Re-fetch state.** `gh issue view <N> --json state,labels -q '{state,labels}'`. If state is already `CLOSED` or the issue has been moved off `ready-for-agent`, do nothing — someone else worked it — and end the turn.
2. **Close the issue** with a single wrap-up comment that links the commit SHA(s):

   ```bash
   gh issue close <N> --comment "$(cat <<'EOF'
   Implemented in <commit-sha>.

   <one-paragraph summary of the change in domain-language terms>

   Follow-ups (if any):
   - …
   EOF
   )"
   ```

3. **Verify the closure landed.** `gh issue view <N> --json state -q .state` must print `CLOSED`. If it doesn't, retry the close once. If it still fails, post a `gh issue comment <N> --body "..."` describing the failure and end the turn — the wrapper will pick up the closure on the next iteration as long as the commit message contains `Closes #<N>`.
4. **Do NOT** modify labels. The closure is the signal.
5. **Commit and Push** local changes to GitHub each time you complete and close an issue.

When the task is **not** complete and you want to record substantive progress (a real partial step, a discovered blocker, or a design pivot):

- Post **at most one** comment per iteration with `gh issue comment <N> --body-file <path>`.
- Comment only when there's genuinely new information for the next iteration to read. Never comment merely to say "no progress" or to narrate exploration.
- Prefer rolling all wrap-up content into the eventual close comment over leaving a trail of progress chatter.
- Do **not** write `Closes #N` in any partial-progress commit message — the wrapper will auto-close. Use `Refs #N` or `Progress on #N` instead.

Never modify the parent PRD issue (typically `#1`, identifiable from each slice's `## Parent` section when present). Never relabel any issue.

## Pull-request mode

When a block is headed `=== PR #N: ... (branch: <head-branch>) ===`, you're advancing an **existing** pull request, not closing an issue. The work happens on the PR's own branch:

1. **Check out the PR branch.** `gh pr checkout <N>` switches your worktree to `<head-branch>`. The wrapper restores the base branch at the start of the next iteration, so you don't need to switch back manually — but don't start unrelated work while checked out.
2. **Read the brief.** Find the `## Agent Brief` (in the PR body or a comment) plus any review threads, and implement exactly what it asks using `/tdd`.
3. **Run the relevant feedback loops** from `AGENTS.md`, same as issue mode.
4. **(Optional) Self-review with `/code-review`.** On a non-trivial diff, run `/code-review` over your changes to catch bugs or drift from the brief before you commit.
5. **Commit to the PR branch** with a message recording key decisions and files changed. Do **not** use a `Closes #N` / `Fixes #N` / `Resolves #N` keyword for the PR's number — you are not closing it.
6. **Push to the PR branch.** `git push`. The wrapper detects progress by the PR's head SHA moving, so a successful push is what registers as an advance.
7. **Comment progress** with `gh pr comment <N> --body-file <path>` only when there's genuinely new information — same restraint as issue comments.
8. **Never merge or close the PR.** A human reviews and merges it in QA. Your job is to push the diff forward, not to land it.

If the branch has diverged and you hit merge conflicts, use `/resolving-merge-conflicts` to work through the ones you can resolve from the diff and the brief. If the PR is in a state you genuinely can't advance (conflicts you can't resolve without more context, a brief blocked on a dependency), post one comment explaining the blocker and end the turn.

## Local-markdown mode (legacy)

If issues were passed in `=== <path> ===` form:

- On completion: move the issue file from `prds/<feature>/NNN-*.md` to `prds/<feature>/done/NNN-*.md` (create `done/` if needed). Do not renumber, do not touch the sibling `prd.md`, do not move across feature folders.
- On partial progress: append a brief note to the bottom of the issue file describing what was done and what's blocking.

# FINAL RULES

- ONLY WORK ON A SINGLE TASK per iteration.
- After completing a task, do **not** emit `<promise>NO MORE TASKS</promise>`. Just end the turn — the wrapper's next iteration will re-collect the AFK-ready pool and decide whether anything is left. Emitting NMT in an iteration where you did work is treated by the wrapper as a signal that you're confused, not as a clean termination.
- If after triaging the provided issues you genuinely have nothing actionable (e.g., every issue is blocked on a dependency you can't satisfy without picking another one first), output `<promise>NO MORE TASKS</promise>` and stop. The wrapper tolerates this only if no work was done; if you repeatedly emit NMT while AFK-ready issues remain, the wrapper will abort with a non-zero exit so a human can investigate.
