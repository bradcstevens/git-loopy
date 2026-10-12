# The Planner is generated from the pin and launched into the Loop engineer's own harness

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided by [Decide how a Loop engineer's harness gets the named
Planner](https://github.com/bradcstevens/git-loopy/issues/773). Builds on
[ADR-0071](0071-the-planner-stays-outside-the-run.md),
[ADR-0078](0078-one-agent-profile-per-task-type.md) and
[ADR-0086](0086-agent-profiles-are-markdown-beside-the-skills-proved-and-installed-with-the-catalog.md),
and keeps [ADR-0025](0025-installed-skill-catalog.md)'s rule that git-loopy writes into
no agent-client directory. Amends ADR-0078 and ADR-0086 for the `planning` profile.

ADR-0086 made `profiles/planning.md` the **Planner**'s definition of record and left
open how a **Loop engineer**'s harness gets it. git-loopy generates a named agent from
that profile and launches the Loop engineer's own Copilot CLI on it.

## `git-loopy plan` launches it

`git-loopy plan [planning document]` is a thin launcher that runs outside every **Run**.
It does three things:

1. It refreshes the **installed catalog** from the pin, the way a Run's preflight does.
   In the steady state this opens no connection. An unreachable upstream warns. A machine
   with no install and no reachable upstream refuses.
2. It generates the Planner agent from the verified installed profile root into a root
   inside git-loopy's own config scope.
3. It execs the Loop engineer's interactive harness:
   `copilot --add-dir <root> --agent git-loopy-planner -i "/to-tickets <planning document>"`.

The session is the Loop engineer's own: interactive and human-driven. ADR-0071 therefore
holds. git-loopy hosts no decomposition, and the graph stays a proposal until its recorded
revision is approved. Copilot CLI is the Planner's only supported harness in 1.0.0.

git-loopy writes nothing into `~/.copilot`. `--add-dir` loads the root's
`.github/agents` and `.github/skills` as trusted configuration for that one session.
The agent is generated at launch from an install that was just verified, so no stored
agent exists to drift from the pin.

## What the generated agent carries

- **Prompt.** The body of `profiles/planning.md`, alone. It sits on the repository's own
  instructions, which Copilot CLI gives every `--agent` session. It never sits on
  `PROMPT.md`, which is the Run instructions for executing one picked-up issue.
- **Required Skills.** `/to-tickets` is preloaded twice. The launch prompt invokes it
  through the documented slash-command path, and the agent's `skills:` frontmatter
  preloads it as well. That key is undocumented, but a live probe on Copilot CLI 1.0.96
  showed it preloads (#773). Because of the launch prompt, the Required Skill does not
  depend on that key alone.
- **Skills.** The root exposes exactly the planning profile's Required ∪ Permitted
  Skills, linked from the installed pinned catalog. The probe showed that an add-dir
  root's Skills win over same-named personal Skills, so the Planner drafts with the
  Skill revisions the Runs execute. The Loop engineer's other Skills stay as their
  harness provides them. git-loopy cannot close that world, so the planning row stays
  advisory, as ADR-0078 says.
- **Access.** `profiles/planning.md` declares `access: full`, and the agent keeps the
  harness's full toolset. The Planner's Skills write ticket files, `CONTEXT.md`, ADRs,
  prototypes and research notes, and they publish through `gh`. A file agent could not
  express `read-only` faithfully anyway, because it has no permission denials
  (ADR-0086). The guard is ADR-0071's atomic approval.
- **Model.** None. The launcher passes `--model`, `--effort` and `--context` from
  **Routing**'s resolution for the `planning` **Task type**, through the same pure seam
  Pickup uses, so Routing stays the only model authority. The recommended `planning`
  pair is `gpt-6.1-sol` at `xhigh`. An explicit `--model` or `--reasoning-effort` on
  `git-loopy plan` suppresses Routing, as it does for a Run, and `/model` still works
  mid-session. Dynamic routing does not apply, because there is no issue and therefore
  no evidence.
- **Name.** The file is `git-loopy-planner.agent.md`, with the display name `Planner`.
  It is user-invocable and not model-invocable, so no other agent can dispatch the
  Planner (ADR-0078).

## A shadowed Planner refuses to launch

The same probe showed that a same-named personal agent wins over an add-dir agent, and
so does a same-named repository agent. A shadowed Planner would silently run someone
else's prompt under the Planner's name. The launcher therefore refuses to start, and
names the file to remove, when `git-loopy-planner.agent.md` exists in either place:

- the personal agent directory: `$COPILOT_HOME/agents`, or `~/.copilot/agents`;
- a repository agent directory: `.github/agents` or `.claude/agents`, from the working
  directory up to the Git root.

Plugin agents are documented as the lowest priority, and organization and enterprise
agents as lower than repository agents. The launcher checks neither.

## The Skills repository's side

- The `to-tickets` persona and its shell wrapper retire, and the Planner replaces them.
  `wayfinder` and `to-spec` stay as that repository's own interactive personas. They are
  not Agent profiles, git-loopy never reads them, and their model pins are that
  repository's business.
- In a repository configured for git-loopy, `/to-spec`'s closing step and `/next`'s
  decomposition route hand off to a fresh `git-loopy plan <spec>`. They no longer run
  `/to-tickets` in the same session. Every fact the approved graph relies on must be
  written into its issues (ADR-0076). A fresh session that drafts from the durable Spec
  makes the graph depend on the Spec, not on conversation the Spec never captured. A
  Spec too thin to slice from shows up as a Planner that has to ask. In other
  repositories, those Skills keep `/to-tickets`.
- These are paired changes (ADR-0064), and they land after `git-loopy plan` ships.

## Amendments

- ADR-0078 says profiles "are never written as `.agent.md` files". That still holds for
  every in-Run profile. The Planner's agent is the exception: it is generated outside any
  git-loopy session, into git-loopy's own config scope.
- ADR-0086 layers each profile's body on `PROMPT.md`. The `planning` body stands alone.
  The six in-Run profiles keep the layering, and the validator still requires a non-empty
  body.
- ADR-0086 left the `planning` profile's `access` unstated. It is `full`.

ADR-0086 gave three reasons for rejecting `.agent.md` profiles: a git-loopy session never
offers file agents, the format documents no `skills` key, and an installed file agent
would run a role delta without the Run instructions or Routing. None of them applies to
the Planner. It runs in the Loop engineer's own session, which does offer file agents. The
documented launch prompt also preloads its Required Skill. Its role delta needs no Run
instructions, and the launcher applies Routing.

## Left to the PRD

The PRD settles the root's path, whether the root links or copies the Skills, atomic
regeneration when launches run concurrently, passing further arguments through to
`copilot`, and the minimum Copilot CLI version.

## Considered options

- **No Planner agent in 1.0.0, with the human running `/to-tickets` directly.**
  Rejected: the profile's role delta would be dead text, and "the named Planner" would be
  a name only.
- **The Skills repository commits a generated agent and ships it through its link script
  and plugin.** Rejected: it is a second channel, decoupled from git-loopy's pin. It waits
  on [Ship the skills as a Copilot CLI
  plugin](https://github.com/bradcstevens/git-loopy-skills/issues/102), which ships Skills
  only and is blocked, so it would reach no Loop engineer except the maintainer. It would
  also reverse ADR-0086's "nothing is generated between `.github/agents` and
  `profiles/`".
- **Generating the agent at install, refresh and repair, and documenting `--add-dir`.**
  Rejected: it adds a third recorded root, and the Required Skill would depend on the
  undocumented `skills:` key alone.
- **Warning instead of refusing when the Planner is shadowed.** Rejected: a shadowed
  Planner is the silent failure that the refusal exists to prevent.
- **Opening the session on the Loop engineer's own default model, or on the `/to-tickets`
  Skill header.** Rejected: Routing is the only model authority (ADR-0078). Reading the
  header would also make git-loopy depend on a Skill's private metadata.
- **Folding `wayfinder` and `to-spec` into the Planner.** Rejected: both work upstream of
  the graph. Folding them in would widen the Planner past ADR-0071's role, which is to
  draft a complete graph from planning documents.
- **Keeping same-session decomposition.** Rejected: the graph could lean on conversation
  that the Spec never captured.
