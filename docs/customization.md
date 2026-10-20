# Customization

> Tailor git-loopy to a repository: domain context, agent guidance, feedback
> loops, Config, prompt overrides, and the per-repo skill configuration written
> by `/setup-git-loopy-skills`.

## Repo structure reference

### What git-loopy provides

```
.
├── AGENTS.md                       # Repository guidance and exact feedback loops.
├── GLOSSARY.md                     # Shared loop-engineering vocabulary.
├── LICENSE
├── README.md                       # git-loopy front door and complete workflow overview.
├── docs/                           # Loop-engineering and Runner family references.
│   ├── concepts.md
│   ├── workflow.md
│   ├── runners.md
│   ├── skills-setup.md
│   └── customization.md
└── git-loopy/
    ├── PROMPT.md                   # Agent prompt loaded each iteration.
    ├── conformance/                # Language-neutral Wrapper-contract fixtures.
    └── python/                     # Python reference runner (GitHub Copilot Python SDK). See git-loopy/python/README.md.
```

> As the [Runner family](adr/0013-multi-language-runner-family.md) ports land, `git-loopy/` also gains `shell/`, `powershell/`, and `tui/` (the shared TUI helper binary). The [Conformance suite](../git-loopy/conformance/README.md) is already shared infrastructure; until the ports land, the Python Orchestrator is the only shippable member.

### What you customize when adopting

```
├── AGENTS.md                       # Your project's agent guide — Tech stack + Feedback loops table (see below).
├── GLOSSARY.md                     # Your project's domain glossary.
├── docs/
│   ├── adr/                        # Architecture decision records (created lazily by /grill-with-docs).
│   └── agents/                     # Per-repo skill config — written by /setup-git-loopy-skills.
│       ├── issue-tracker.md        #   Where issues live (GitHub / GitLab / local markdown / other).
│       ├── triage-labels.md        #   Label vocabulary used by /triage.
│       └── domain.md               #   Single- vs multi-context layout for GLOSSARY.md / ADRs.
├── prds/                           # Optional legacy local-markdown PRDs (GIT_LOOPY_ISSUE_SOURCE=prds).
├── issues/                         # Optional legacy local-markdown issues.
└── <your application code>
```

## The two files you almost always edit

- **`AGENTS.md`** — fill in **Tech stack** and the **Feedback loops** table (see [Stack-agnostic defaults](#stack-agnostic-defaults) for the exact structure). The loop reads the **Feedback loops** table to know what commands to run before committing. If lint / type-check / test / build commands are wrong here, the agent guesses and CI catches the difference. The trailing **Agent skills** block is owned by `/setup-git-loopy-skills`; don't hand-edit it the first time around.
- **[`git-loopy/PROMPT.md`](../git-loopy/PROMPT.md)** — the shared Run instructions beneath the Agent profiles. Usually leave the defaults; customize repository-wide execution guidance or commit-message conventions, not profile selection or Skill requirements. If you change the commit-message convention, also update the `CLOSE_KEYWORD_RE` regex used by `extract_close_refs` in [`git-loopy/python/git_loopy/wrapper.py`](../git-loopy/python/git_loopy/wrapper.py) so the auto-close backstop still matches what the agent emits.

Replace the repository's [`GLOSSARY.md`](../GLOSSARY.md) with the adopting
project's language as `/grill-with-docs` resolves real terms. For the
`AGENTS.md` structure, see
[Stack-agnostic defaults](#stack-agnostic-defaults); for the planning path from
domain context to spec and tickets, see [`docs/workflow.md`](workflow.md).

### Prompt overrides and fixed profiles

Run instructions resolve **project > global > packaged default**: the first
`git-loopy/PROMPT.md` in the repository or `PROMPT.md` in git-loopy's config
home wins over the packaged prompt. That override remains the shared base;
the selected profile layers its role instructions on it. It does not replace
the profile. See the Python [prompt resolution reference](../git-loopy/python/README.md#prompt-resolution).

Remove `required-skills` from custom prompt frontmatter, even if it is `[]`.
Preflight and `git-loopy doctor` refuse that retired key. Required and
Permitted Skills come from the installed profiles alone; select optional
exposure through the [Skill policy](skill-policy.md#profiles-and-exposure).

Profiles are fixed definitions from the pinned Skills repository, installed
under `<config-home>/git-loopy/profiles/` and repaired with the catalog.
Repository and personal sources cannot override them. To change a profile,
make a paired upstream change, publish and prove it, then move the pin
(ADR-0064); editing the install is not customization. Models and reasoning
effort belong to Routing, not to profiles or `PROMPT.md`.

The Planner is the exception to prompt layering, not an override mechanism.
`git-loopy plan` generates it from the pinned planning profile and launches
the engineer's interactive Copilot CLI without the Run's `PROMPT.md`.
It works outside a Run, behind approval of the recorded issue graph. See
[Planning](../git-loopy/python/README.md#planning-git-loopy-plan) for launch
options, same-name agent refusals, and the open-world harness boundary.

## `/setup-git-loopy-skills` — the entry-point skill

This skill is the first thing to run in Copilot CLI for any new project, **before** any of the other planning or implementation skills. It does two things:

1. **Populates the `## Agent skills` block at the bottom of `AGENTS.md`** with concrete pointers to the per-repo config below.
2. **Writes `docs/agents/{issue-tracker,triage-labels,domain}.md`** — the per-repo config files that `/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`, `/diagnosing-bugs`, `/tdd`, and `/codebase-design` read to learn which issue tracker, label vocabulary, and context layout this project uses.

The skill walks you through three decisions one at a time:

| Decision | What it controls | Defaults |
| --- | --- | --- |
| **Issue tracker** | Whether downstream skills call `gh issue create`, `glab issue create`, write a markdown file under `.scratch/`, or follow custom prose. | GitHub (if a `git remote` points at GitHub), GitLab (if it points at GitLab), local markdown (no remote), or "other" (free-form). |
| **Triage labels** | The exact strings `/triage` applies for each of the five canonical roles. | `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` — verbatim. Override per-role if your existing tracker uses different names. |
| **Domain docs** | Whether the repo has one global `GLOSSARY.md` or a `GLOSSARY-MAP.md` pointing to per-context files. | Single-context (most repos). Pick multi-context only if you actually have multiple bounded contexts. |

### Skip it and downstream skills will guess

If `/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`, `/diagnosing-bugs`,
`/tdd`, or `/codebase-design` lacks tracker, label, or domain context, run
`/setup-git-loopy-skills`.

### Re-running it

`/setup-git-loopy-skills` is idempotent. Re-run it whenever you want to:

- Switch issue trackers (GitHub → GitLab → local markdown → other).
- Rename triage labels (e.g., to match a label scheme your repo already has).
- Move from single-context to multi-context (or vice versa).

It edits the existing `## Agent skills` block in place and rewrites `docs/agents/*.md`. Your hand-edits inside `docs/agents/*.md` are preserved when possible, but if you've done substantial customization there, diff before accepting the rewrite.

### Auto-bootstrap behavior

git-loopy provides a **two-layer auto-bootstrap** so a forgotten
`/setup-git-loopy-skills` does not lead to silent guessing:

| Layer | Where | What it does |
| --- | --- | --- |
| **Interactive sessions** | The "First-run bootstrap" directive at the top of `AGENTS.md` (loaded into every Copilot CLI invocation — [add it yourself](#first-run-bootstrap-directive) to enable this layer) | If `docs/agents/issue-tracker.md` does not exist, the agent invokes `/setup-git-loopy-skills` as its first action — **before** acting on the user's request — then returns to the original ask. |
| **Autonomous Run** | Preflight check in [`git-loopy/python/`](../git-loopy/python/) | If `docs/agents/issue-tracker.md` does not exist, the Orchestrator exits non-zero **before** the first Iteration with a stderr message pointing the loop engineer at `/setup-git-loopy-skills`. The skill is interactive and cannot safely run inside the autonomous agent session. |

The two layers compose: a human starts a fresh repo, runs `uv run --project git-loopy/python git-loopy`, gets a clear error, opens `copilot` interactively, and — if the [First-run bootstrap directive](#first-run-bootstrap-directive) is in their `AGENTS.md` — sees it auto-trigger `/setup-git-loopy-skills`, answers the three questions, then re-runs the loop. Detection uses the existence of `docs/agents/issue-tracker.md` as the signal that the skill has run.

### First-run bootstrap directive

The interactive layer is opt-in: add this directive to the top of your
`AGENTS.md` so it loads into every Copilot CLI invocation. The Orchestrator
preflight works without it; this directive adds the interactive auto-trigger.

```markdown
> **First-run bootstrap (read on every invocation).** If `docs/agents/issue-tracker.md` does **NOT** exist at the repo root, your very first action this session is to invoke `/setup-git-loopy-skills` - **before any other work**, including the user's stated request. After it completes, return to whatever the user originally asked. If `docs/agents/issue-tracker.md` already exists, this bootstrap is satisfied; ignore this paragraph and proceed normally. The autonomous git-loopy Orchestrator (`git-loopy/python/`) refuses to start without this file, so if you are reading this directive from inside a `copilot --yolo -p` invocation, surface the inconsistency and stop.
```

## Host yield policy on macOS

The Python Runner defaults to `host_yield = "utility"` on macOS. Its Run
process, Copilot runtime, agent tool commands and Integration feedback loops
inherit the `taskpolicy -c utility` QoS clamp: they yield CPU to foreground
work while retaining throughput on an idle host. On the Dashboard path the
detached worker is clamped; the operator's Dashboard client stays foreground.
On the line-printer path the CLI process itself is clamped.

For a host that serves no interactive work, opt out in project
`git-loopy/config.toml` or global `<config-home>/git-loopy/config.toml`:

```toml
host_yield = "none"
```

Equivalently, use `git-loopy config set --project host_yield none` (or
`--global`), or `GIT_LOOPY_HOST_YIELD=none git-loopy` for one invocation.
Precedence is **environment > project Config > global Config > platform
default**. The only values are `utility` and `none`; an unknown value, including
an explicitly empty value, refuses the Run with a diagnostic. On macOS, a
missing `taskpolicy` also refuses instead of silently running unclamped.
Restore it on `PATH` or explicitly opt out.

Other platforms keep their existing scheduling behavior (default `none`;
`utility` has no scheduling effect there). Opt-out does not undo a policy
inherited from an external launcher. This is not a CPU quota or permission
to generate unbounded load. `nice` does not protect foreground work on macOS,
and the `background` clamp throttles deadline-bound tests too far. See
[ADR-0092](adr/0092-runs-yield-to-the-operators-foreground-on-macos.md) for
the measured trade-off. Automated policy checks read process priority and
never create CPU load.

### Idle system sleep

A live local Python Run on macOS also prevents **idle system sleep** until it
exits, including when detached from its Dashboard. Display sleep and screen
lock are unchanged. macOS releases the process-owned assertion even if the Run
crashes or is killed. Remote Execution hosts, remote Run residences and other
platforms are unaffected.

This is independent of `host_yield`. To opt out, set `prevent_idle_sleep = false`
in Config, run `git-loopy config set --project prevent_idle_sleep false` (or
`--global`), or use `GIT_LOOPY_PREVENT_IDLE_SLEEP=off` for one invocation.
Precedence is **environment > project Config > global Config > true**.
The environment accepts `true/false`, `yes/no`, `on/off` or `1/0`; invalid values
refuse. The Run diagnostics log records whether its assertion is held. An
assertion that cannot be acquired refuses the Run with an opt-out remedy.

Lid close and other forced suspension can still put the host to sleep. After a
suspension longer than the Lease TTL, one diagnostic names the duration and
the Leases whose TTLs elapsed during it. This explains renewal loss; it never
reclaims a Lease or substitutes for the remote ownership fence.

## Skills reference

Three different things supply Skills, and they are not interchangeable:

| Layer | Where it lives | Who reads it |
| --- | --- | --- |
| **The external catalog** — the source of record | [`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills) at the revision git-loopy pins | nobody directly; it is what an install resolves against |
| **The installed catalog** | `<config-home>/git-loopy/skills/`, written by `git-loopy init` and refreshed at the start of every Run | **git-loopy Runs** — this is the only Skill source git-loopy provides for itself |
| **Copilot CLI's own sources** | your personal, plugin, built-in, and custom Skills, including `~/.copilot/skills/` | **Copilot CLI**, when you type a slash command yourself |

A git-loopy distribution ships **no** Skills, and a Run reads **no** Skill from
the repository it is pointed at — committing a `SKILL.md` into a project does
not hand a Run a Skill
([ADR-0025](adr/0025-installed-skill-catalog.md)). Which of Copilot's own
sources a Run may expose is decided by the closed-world
[Skill policy](skill-policy.md), and a name the installed catalog also provides
resolves to the installed copy. Installing the catalog into Copilot for your
own use is [`docs/skills-setup.md` §1.3](skills-setup.md#13-also-give-copilot-cli-the-slash-commands).

The GitHub Copilot CLI marketplace contains additional skills for work outside
this catalog. To discover them:

```bash
npx skills find <query>
```

For the boundary between human-invoked planning skills and model-invoked
execution skills, see
[`docs/runners.md` → Skill routing](runners.md#skill-routing).

## Stack-agnostic defaults

git-loopy is stack-agnostic. Put the project's exact lint, type-check, test,
and build commands in the **Feedback loops** table in `AGENTS.md`; human-driven
skills and autonomous Iterations use the same repository feedback.

Add a `## Feedback loops` section to `AGENTS.md` with a table shaped like this (replace the `<PLACEHOLDER>` commands with your project's real ones; delete rows you don't have):

```markdown
## Feedback loops

| Loop          | Command                 | When to run                                                             |
| ------------- | ----------------------- | ----------------------------------------------------------------------- |
| Lint          | `<PM> lint`             | Any code change                                                         |
| Type-check    | `<PM> typecheck`        | Any typed change                                                        |
| Unit tests    | `<PM> test:unit`        | Any code change                                                         |
| Build         | `<PM> build`            | Anything touching routes, configs, deps                                 |
| E2E tests     | `<PM> test:e2e`         | Anything user-visible; save reports to `tests/e2e/<YYYY-MM-DD-HHMMSS>/` |
| Infra what-if | `<IAC_WHAT_IF_COMMAND>` | Any infra change                                                        |
```

The loop expects this exact table shape: the `## Feedback loops` heading, then
the `Loop` and `Command` columns. Fill in every runnable row before delegating
work. Vague verbs such as "run the tests" force each fresh agent to rediscover
the command and weaken the feedback loop.

This table is not documentation *about* the gate — it **is** the gate's input. In
Parallel mode, **Integration** merges a **Lane**'s branch into a private stage and
runs these commands there, top to bottom and fail-fast (ADR-0009). Two consequences
worth knowing before you fill it in:

- **A row that is left as a `<PLACEHOLDER>`, or an absent section, is not "no gate" —
  it is a gate that cannot run.** `AgentsMdGateRunner` raises `GateError`, the runner
  treats that as red, and every Lane merge fails Integration regardless of what the
  contribution contains.
- **Every row is blocking.** A command that is already failing for unrelated reasons
  (a lint backlog, a flaky suite) makes Integration permanently red. Leave it out of
  the table until it is green, and note in prose why.
- **Every row is bounded.** Each command runs under a wall-clock bound (default one
  hour) and its whole process tree is killed when it exceeds it, so one loop waiting
  on a socket, a prompt or a lock cannot block Integration forever. A loop that
  exceeds its bound is a red gate naming that loop, reported as a *timeout* rather
  than as a test failure. Set `gate_timeout_seconds` in project or global Config
  when your slowest honest loop needs more room, for example
  `git-loopy config set --project gate_timeout_seconds 5400`.
  `GIT_LOOPY_GATE_TIMEOUT_SECONDS` overrides both scopes. There is one bound for
  every row, sized to catch hangs rather than budget performance. There is no
  value meaning "unbounded": non-positive and non-finite values are refused.
  Invalid environment or numeric Config values warn and fall through to the
  next tier; `config set` rejects them without writing.
- **Every attempted row is timed.** The Run log names the row and outcome with
  its duration and bound, including passed, failed and timed-out rows. A row
  that takes strictly more than half its bound produces one warning. Timing
  includes process startup and, on timeout, bounded process-tree cleanup.

git-loopy's own [`AGENTS.md`](../AGENTS.md) is the worked example: it declares one
row per Runner-family member, ordered cheapest-first so a fail-fast gate reports the
cheap signal soonest, with tools resolved through `PATH` rather than host-specific
paths (Integration runs in a throwaway worktree on whatever host the operator has).

`git-loopy doctor` and Run preflight warn when `python3` on `PATH` resolves
through a `shims` directory (including pyenv, asdf and mise). The report names
the shim and its backing interpreter and measures each startup in the project
directory. Each probe is bounded to ten seconds; an unavailable or timed-out
probe is reported, not mistaken for a healthy interpreter. This advisory does
not block a Run. No Python row is reported without a shim, and neither doctor
(including `--apply`) nor the Run rewrites `PATH` or the feedback-loop commands.

If you're on Azure or Microsoft tech, add **Azure conventions** and **Microsoft tooling** sections to `AGENTS.md` documenting the `SecurityControl=Ignore` tag and the `disableLocalAuth: false` default for Foundry resources. Otherwise skip them.

## Leases — running more than one Run against one tracker

A **Lease** is a run's exclusive right to work one issue. It is taken at **Pickup**,
before any agent session starts and before any worktree exists, renewed every 60
seconds while its owner lives, and it expires on its own if the owner stops. That is
what lets two runs share one tracker: an issue under another run's live Lease is
passed over silently, and a crashed run's issues free themselves with no label
cleanup and nothing for you to do (ADR-0033).

A Lease **is** a git ref on your `origin` remote — one per issue, holding a
parentless commit whose message is the record:

```bash
# Which issues are being worked right now, and by which run:
git ls-remote origin 'refs/heads/git-loopy/leases/*'

# Read one Lease's record (run_id, timestamps, ttl, host, pid):
git fetch --no-tags origin <sha> && git --no-pager show -s --format=%B <sha>

# Remove one by hand — only ever for a Lease you know is dead:
git push origin --delete refs/heads/git-loopy/leases/issue-<N>
```

Deleting a Lease by hand is almost never necessary: expiry already covers a crashed
run. Do it only if you must reclaim an issue sooner than its TTL, and only when you
know the owning run is gone — deleting a *live* run's Lease is how you get two agents
on one issue, which is the failure the Lease exists to prevent.

- **`GIT_LOOPY_LEASE_TTL_SECONDS`** sets how long a Lease survives without renewal.
  Default `300`. Raise it if your network or your host regularly stalls a run for
  minutes at a time; the cost of a longer TTL is only that a genuinely crashed run's
  issues stay parked for longer. A value below one 60-second renewal interval is
  refused (it would expire *between* beats however healthy its owner), as are
  non-integer, non-positive and `inf` values — all fall back to the default rather
  than aborting the run, and there is deliberately no value meaning "never expires".

You do not need to tune this to be safe. Expiry is an optimisation, not the safety
mechanism: before every push, comment, label write and issue close, a run re-reads
its own Lease and abandons the issue if it no longer holds it. A Lease taken from a
run that was merely slow therefore costs duplicated *effort*, never corrupted state.

### When Leases are off

A run resolves the repository it contends on from its `origin` URL — a local config
read, not a network call — and holds no Lease at all when it cannot. Four cases,
each announced in the run's diagnostics, because a run that has quietly stopped
guarding its issues looks exactly like one that is guarding them:

- the clone has **no `origin` remote**;
- `origin` **names no `owner/repo`** — a local path or a `file://` clone;
- the run uses the legacy **`prds` backend**, whose issues are files in the worktree
  rather than rows on a shared tracker, so there is nothing for two runs to contend on;
- the clone **cannot write the Lease refs**, which the run discovers the first time it
  tries and then stops trying. A read-only `origin` in a fork workflow, a ruleset that
  forbids creating refs under `refs/heads/**`, or an expired SSO authorization all land
  here. The run says so loudly and then does its work unguarded — a Lease it is not
  allowed to take would protect nothing, and refusing the work to protect nothing is
  worse. Grant the token push access to `refs/heads/git-loopy/leases/*` to restore it.

Every URL spelling of one repository resolves to one name, so a clone that fetched
over SSH and a clone that fetched over HTTPS contend on the same Lease:

```bash
# Confirm what this clone will contend on:
git remote get-url origin
```

An unleased run is not broken — it behaves exactly as every run did before ADR-0033.
It simply offers no protection against a second run working the same issue.

There is one refusal you may see against a *working* Lease setup, and it is the
mechanism succeeding rather than failing. When another live run holds an issue,
this run passes that issue over and moves to the next one — and in Parallel mode it
passes it over for the rest of the run rather than retrying it, because a lane
reservation costs no iteration and retrying would spin on a remote round trip per
turn. Start another run later and it will pick the issue up normally.

So: running two runs against one tracker is safe for **serial** iterations and for
`parallel-safe` issues worked in **Lanes**, on a clone whose `origin` names a
repository it can push Lease refs to. Check `git remote get-url origin`, then watch
the run's first diagnostics for a line saying exclusivity is off.

---

**Next:**
- [`docs/workflow.md`](workflow.md) — the complete loop-engineering workflow.
- [`docs/skill-policy.md`](skill-policy.md) — the closed-world Skill policy that decides which Skills a Run may load.
- [`docs/runners.md`](runners.md) — the Runner family reference; [`docs/wrapper-contract.md`](wrapper-contract.md) — the Wrapper contract every Orchestrator implements.
- [`docs/concepts.md`](concepts.md) — the mental models behind the design.
- Back to [`README.md`](../README.md).
