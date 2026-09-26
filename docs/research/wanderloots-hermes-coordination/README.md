# Agents, loops & graphs: Wanderloots on Hermes subagents, goal loops, bots & Kanban

**Question:** when does a single agent that delegates stop being enough, and what does a team of
agents (profiles, group chats, a Kanban task graph) actually add?

**Captured** 2026-09-24 · **Sources:**

- Video: Wanderloots (Callum), *Subagents vs Agent Teams? Hermes Bots, Goal Loops & Kanban
  Graphs*, YouTube, 42:55, published 2026-09-19. <https://www.youtube.com/watch?v=GlW4N7p5KS8>
- Companion post: *When Do You Need An Agent Team? Choosing Your Hermes Agent & Bot Workflow
  (Kanban Plugin Tips)*, Patreon (free), published 2026-09-22.
  <https://www.patreon.com/wanderloots/posts/when-do-you-need-169987725>. It includes the 5-page
  "coordination cheat sheet" PDF (v2).
- Hermes docs pages shown on screen:
  [Subagent Delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation) ·
  [Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban) ·
  [Bot Mode](https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode)

**Method:**

- **Video transcript:** from YouTube captions.
- **Frames:** ffmpeg scene detection plus ~10 s gap-fill gave 447 candidates. Visual
  de-duplication left 155 distinct frames. Each was read and its on-screen text transcribed,
  and 77 are kept.
- **Post:** fetched in full, with all 10 inline figures and the attached PDF.
- **Reproduction:** notes are paraphrased with short quotes. Numbers, rubric scores, config
  keys, and UI labels are reproduced exactly. All figures are the authors' and are credited to
  Wanderloots; they're kept for research reference.

## Files

| File | Contents |
|---|---|
| [video-walkthrough.md](video-walkthrough.md) | Chapter-by-chapter notes (20 sections), each with the frames that show it: diagrams, UI states, docs pages, and results tables |
| [companion-post.md](companion-post.md) | The written decision guide (Part 1), the experiment write-up (Part 2), all 10 figures, and the 5-page cheat sheet |
| [hermes-ui-reference.md](hermes-ui-reference.md) | Every Hermes surface, setting, config key, and documented behavior shown, organized by feature |
| [images/video/](images/video/) | 77 frames, named `MMmSSs-<topic>.jpg` by their timestamp in the video |
| [images/post/](images/post/) | The post's 10 inline figures |
| [images/cheat-sheet/](images/cheat-sheet/) | The 5 cheat-sheet pages rendered as PNG |

## The answer in one paragraph

Start with **one chat**. Add **subagents** when parts of the work are genuinely independent.
Add a **goal loop** when a concrete check can detect unfinished work. Use a **profile** when a
specialist needs a reusable setup (model, tools, skills, memory). Then choose by how the work
connects: a **group chat** for discussion, and a **Kanban board** when work has dependencies,
reviews, blockers, handoffs you need to keep, or must be resumable tomorrow. Add structure only
to fix a failure you can name. In the author's one-run test on a bounded research task, the
**no-goal Kanban orchestrator** was fastest (4m 59s) and used the fewest total tokens
(1,769,942). It tied **main agent + subagents** on reviewed quality (24/25). The
**auto-decomposer** cost the most (2,368,325 tokens, 8m 39s, 20/25), and **goal loops** added
cost with no quality gain for this task.

## Key takeaways

1. **Smaller parent context ≠ lower total cost.** Delegation kept the parent at ~37k vs ~102k
   tokens, but total usage across agents was higher.
2. **Subagents know nothing.** They get only the `goal` + `context` the parent passes, plus the
   workspace `AGENTS.md`-chain files, but not `SOUL.md`. Brief them fully.
3. **"Delegate the work, not the judgment."** Route narrow extraction and search to cheap
   models, and keep planning and synthesis on a frontier model. Verify the configured route;
   a prompt asking for a cheaper model proves nothing.
4. **Goal loops need checkable criteria and a stop limit.** Default 20 turns; you can add
   criteria mid-run. More turns don't mean correctness and don't cap spend.
5. **`delegate_task` is a function call; Kanban is a work queue.** Kanban adds named
   persistent workers, block/unblock/re-run, human comments and gates, SQLite audit history,
   and peer coordination.
6. **Kanban roles:** the *planner* designs (the auto-decomposer, or your orchestrator profile
   with Kanban tools), the *dispatcher* starts Ready + assigned cards every 60 s, and
   *workers* execute. Cards are tasks, not necessarily different agents.
7. **Auto-decompose off ⇒ Triage cards sit forever.** Put planner cards in **Ready** with the
   orchestrator assigned.
8. **Quality and permission are separate checks.** A good draft is not permission to send,
   write canon, or publish. "A Done card is not proof of delivery": open the output file.
   In the test, only the chat report was confirmed as a durable file.
9. **Planner/worker model split:** frontier for decomposition, cheap for workers, where most
   tokens are spent. Per-profile `config.yaml`, with a per-task `--model` override.

## Relevance to git-loopy

These are my observations, not claims from the sources.

- **Same core bet.** Hermes' "subagents know nothing" and fresh-context worker cards mirror
  git-loopy's [Memento Model](../../concepts.md): durable state lives in reviewable artifacts
  (issues and commits here; Kanban rows and card comments there), not in carried
  conversation.
- **Kanban ≈ issue-tracker-as-queue.** A Hermes board (Triage → Todo → Ready → Running →
  Blocked → Review → Done, with dependencies and a 60 s dispatcher) is structurally close to
  git-loopy's issue-driven Run with label states and [parallel mode](../../parallel-mode.md).
  Worth comparing:
  - the separate *specifier* (raw idea → spec) vs git-loopy's triage/brief step
  - the `review_dispatch` lane
  - blocking on impossible goals instead of marking them done
  - idempotent create keys for automation
  - worktree-per-task when a board is bound to a project
- **Goal-mode cards ≈ an Iteration with an acceptance judge.** Hermes runs an auxiliary judge
  against the card title + body each turn, with a turn budget that **blocks for human review**
  rather than failing silently. That's a pattern to consider for acceptance checking inside an
  Iteration.
- **Cost evidence lines up with [routed-model-pricing](../routed-model-pricing.md) and
  [subagent-model-override](../subagent-model-override.md).** Frontier planner, inexpensive
  workers; workers dominate token spend.
- **Measurement hygiene.** The author's list of confounds (single run, shared history,
  non-identical prompts, non-blind review, unverified output persistence) is a good checklist
  for any git-loopy workflow comparison.
