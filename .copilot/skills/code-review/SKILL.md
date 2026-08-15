---
name: code-review
description: Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes — Standards (does the code follow this repo's documented coding standards?) and Spec (does the code match what the originating issue/PRD asked for?). Runs both reviews in parallel sub-agents and reports them side by side in an HTML report. Use when the user wants to review a branch, a PR, work-in-progress changes, or asks to "review since X".
---

Two-axis review of the diff between `HEAD` and a fixed point the user supplies:

- **Standards** — does the code conform to this repo's documented coding standards?
- **Spec** — does the code faithfully implement the originating issue / PRD / spec?

Both axes run as **parallel sub-agents** so they don't pollute each other's context, then this skill aggregates their findings into a side-by-side HTML report.

The issue tracker should have been provided to you — run `/setup-agent-skills` if `docs/agents/issue-tracker.md` is missing.

This skill is the **Transition owner** for the review transition. Every review it publishes is one `Review head` **Action occurrence** pinned to one exact durable head, and its outcome is either an `Address review findings` successor or a `Publish head` successor.

## Process

### 1. Pin the fixed point and the reviewed head

Whatever the user said is the fixed point — a commit SHA, branch name, tag, `main`, `HEAD~5`, etc. If they didn't specify one, ask for it.

Resolve the **reviewed head** with `git rev-parse HEAD` and record it. That SHA — not the branch name, not the ticket, not the session — is this review occurrence's identity. A review is only reviewable when the candidate is already committed: a dirty worktree, an in-progress merge or rebase, or unstaged work means there is no exact head to pin, so stop and say what has to be committed first.

Because the occurrence is the head, a review result never transfers to another head. When the head has moved since a prior review — remediation commits, a rebase, an amend, or a conflict resolution — that prior occurrence is retired and this is a **new** `Review head` occurrence that must be performed from scratch. Never reuse, inherit, or carry forward a prior review's completion.

Capture the diff command once: `git diff <fixed-point>...HEAD` (three-dot, so the comparison is against the merge-base). Also note the list of commits via `git log <fixed-point>..HEAD --oneline`.

Before going further, confirm the fixed point resolves (`git rev-parse <fixed-point>`) and the diff is non-empty. A bad ref or empty diff should fail here — not inside two parallel sub-agents.

### 2. Identify the spec source

Look for the originating spec, in this order:

1. Issue references in the commit messages (`#123`, `Closes #45`, GitLab `!67`, etc.) — fetch via the workflow in `docs/agents/issue-tracker.md`.
2. A path the user passed as an argument.
3. A PRD/spec file under `docs/`, `specs/`, or `.scratch/` matching the branch name or feature.
4. If nothing is found, ask the user where the spec is. If they say there isn't one, the **Spec** sub-agent will skip and report "no spec available".

### 3. Identify the standards sources

Anything in the repo that documents how code should be written, such as `CODING_STANDARDS.md` or `CONTRIBUTING.md`.

On top of whatever the repo documents, the Standards axis always carries the **smell baseline** below — a fixed set of Fowler code smells (_Refactoring_, ch.3) that applies even when a repo documents nothing. Two rules bind it:

- **The repo overrides.** A documented repo standard always wins; where it endorses something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature Envy"), never a hard violation — and, like any standard here, skip anything tooling already enforces.

Each smell reads *what it is* → *how to fix*; match it against the diff:

- **Mysterious Name** — a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
- **Duplicated Code** — the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy** — a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps** — the same few fields or params keep travelling together (a type wanting to be born). → bundle them into one type, pass that.
- **Primitive Obsession** — a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches** — the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery** — one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change** — one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality** — abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains** — long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man** — a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest** — a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

### 4. Spawn both sub-agents in parallel

Send a single message with two `Agent` tool calls. Use the `general-purpose` subagent for both.

**Standards sub-agent prompt** — include:

- The full diff command and commit list.
- The list of standards-source files you found in step 3, **plus the smell baseline from step 3** pasted in full — the sub-agent has no other access to it.
- The brief: "Report — per file/hunk where relevant — (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Distinguish hard violations from judgement calls — documented-standard breaches can be hard, but baseline smells are always judgement calls, and a documented repo standard overrides the baseline. Skip anything tooling enforces. Under 400 words."

**Spec sub-agent prompt** — include:

- The diff command and commit list.
- The path or fetched contents of the spec.
- The brief: "Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Under 400 words."

If the spec is missing, skip the Spec sub-agent and note this in the final report.

### 5. Aggregate into an HTML report

Present the two reports under `## Standards` and `## Spec` headings, verbatim or lightly cleaned. Do **not** merge or rerank findings — the two axes are deliberately separate (see _Why two axes_).

Render the aggregate as a self-contained HTML file written to the OS temp directory so nothing lands in the repo. Resolve the temp dir from `$TMPDIR`, falling back to `/tmp` (or `%TEMP%` on Windows), and write to `<tmpdir>/code-review-<timestamp>.html` so each run gets a fresh file. Open it for the user — `xdg-open <path>` on Linux, `open <path>` on macOS, `start <path>` on Windows — and tell them the absolute path.

The report uses **Tailwind via CDN** for layout and styling, and renders the two axes as independent side-by-side columns. Every finding carries a location, a quoted hunk as evidence, the cited standard or spec line, and a one-sentence fix. Severity is two-valued: documented-standard breaches may be hard violations, baseline smells are always judgement calls.

See [HTML-REPORT.md](HTML-REPORT.md) for the full HTML scaffold, palette, finding-card anatomy, and styling guidance.

End with a one-line summary in the chat as well as in the report footer: total findings per axis, and the worst issue _within each axis_ (if any). Don't pick a single winner across axes — that's the reranking the separation exists to prevent.

### 6. Publish the review transition

The review of that exact head is now durable, so publish its semantic delta.

First check that publication is available at all. `git-loopy continuation capabilities` reports `operations.publish`; that capability — not the Continuation *mode*, which is a Run-level automation setting and never a publication feature flag — decides whether there is a native command to publish through. When the command is absent or reports `publish` unsupported, report the review result and the successor now due, and stop. That is an absent capability, not a failure.

Then record the transition's own durable evidence. `transition.evidence` accepts `issue-comment` references only, so post one comment on the ticket or pull request carrying the review outcome and the reviewed head, and reference that comment. The report file and the head are not transition evidence; they are the Action's `basis` and `target`.

Publish with the native command, reading the one JSON completion envelope from a file:

```bash
git-loopy continuation publish --input <request.json>
```

The envelope carries `"transition": {"owner": "code-review", "evidence": [<that issue-comment>]}` and exactly one successor for that head:

- **Findings exist** → one `Address review findings` Action. Its **Basis** is the durable findings themselves — the comment, the report, and the quoted hunks, cited per finding — not "the review said so". Its `occurrence` names the reviewed head, its `target` is the ticket or branch under remediation, and its `instruction` is `/implement`. Publish it together with the new `Review head` Action it returns to, in one fragment, so its `completion_condition` can be an `action-completed` reference to that sibling's key — a local reference must name another Action in the same fragment and may never name itself. Remediation produces a changed head, which retires this occurrence and requires a **new** `Review head` Action against the new head. Publish that return edge explicitly: findings never end the lifecycle at remediation.
- **Clean, and the reviewed head is not yet published** → one `Publish head` Action targeting that exact head, with `/push` as its Instruction and a `branch-head-equals` completion condition naming the branch and the reviewed SHA, which is exactly what the push will make true. A clean review authorizes publication of *this* head only; publishing any other head does not inherit it.
- **Clean, and the reviewed head is already published unchanged** → there is no successor to add. That is still a recognized durable transition, so publish a shared `no-guidance` completion with reason `no-successor-created` rather than publishing nothing: "nothing follows" is a claim this transition owes the record.

Every Action carries exactly one interaction classification and its evidence: `AFK-safe` with a `transition-owner-attestation` whose `owner` matches `completion.transition.owner`, or `HITL-required` with a complete `human-boundary` — `kind`, `reason`, and a durable typed `resolution_condition`. An incomplete boundary is rejected, not treated leniently.

Choose every completion condition from the contract's registry, and only one that is **not already satisfied when the Action is published**: an Action reconciliation completes on its first read has been published dead.

Retiring the prior review occurrence is provable only on the immutable-revision chain. Call `reconcile` with `revision_protocol: true`, pass its exact `observation` and ordered `parents` to `publish`, and carry one `completion.retirements` receipt naming the `predecessor_revision_id`, the retired `action_key`, and a `reason` of `completed` or `supersession`. The recurrence must carry a **distinct** `occurrence`; re-declaring the retired one is the same occurrence and proves no retirement.

A review that is session-only advice — no committed head, or a head the user asked you not to record — is `ephemeral-only`. Render it and stop; it never becomes shared guidance.

### 7. Treat a failed publication as repair required

The review of that head happened whether or not publication succeeded, so a failure after it strands real evidence. A rejected, errored, or `repair_required` receipt is reported as **repair required**, naming the reviewed head, the transition owner, and the operation that failed. Never fall back to session-only advice and never report a success-shaped result: a missing Producer revision must not look like a completed review.

### Human boundaries this skill does not cross

A finding that needs a person is published as its own hard-HITL Action, never resolved inside the review:

- Authority the session does not already hold — a login, MFA prompt, new token or secret, an OAuth consent screen, or a permission or scope expansion needed to read a private dependency, a protected branch, or a required check — is an `Authorize operation` Action, always `HITL-required`, carrying a complete `human-boundary` whose `reason` is `credential-required`, `consent-required`, or `privilege-expansion` and whose `resolution_condition` is a durable typed condition. Never prompt for or work around that boundary while unattended.
- Subjective acceptance the two axes cannot settle stays a `Perform manual validation` Action owned by the implementation transition, `HITL-required` with a `subjective-validation` boundary; do not restate it as a standards finding.

## Why two axes

A change can pass one axis and fail the other:

- Code that follows every standard but implements the wrong thing → **Standards pass, Spec fail.**
- Code that does exactly what the issue asked but breaks the project's conventions → **Spec pass, Standards fail.**

Reporting them separately stops one axis from masking the other.

At the conclusion of a `/code-review` session, run the `/next` skill.