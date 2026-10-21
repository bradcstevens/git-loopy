# Agent Instructions

This file provides instructions and conventions for AI coding agents (GitHub Copilot
CLI, Claude Code, etc.) working in this repository.

## Tech stack

git-loopy is a **Runner family**: one Wrapper contract implemented by several
members, each in its own language, all pinned against the shared Conformance
fixtures in `git-loopy/conformance/`.

| Member | Location | Toolchain |
| --- | --- | --- |
| Python Runner (reference Orchestrator) | `git-loopy/python/` | Python 3.11+ driven by `uv` |
| shell Orchestrator | `git-loopy/shell/` | Bash 4+ (`bash` from `PATH`, not `/bin/bash`) and `jq` |
| PowerShell Orchestrator | `git-loopy/powershell/` | PowerShell 7+ (`pwsh`) |
| Rust Dashboard core | `git-loopy/tui/` | Stable Rust with `rustfmt` and `clippy` |

## Feedback loops

Run the loops below that your change touches before committing. **Integration** also
runs every one of them, top to bottom and fail-fast, over the merged worktree
(ADR-0009, `git_loopy.gate.AgentsMdGateRunner`) — so this table is not documentation
about the gate, it *is* the gate's input. A repository that declares no runnable loop
here cannot be gated at all, and every Lane merge comes back red regardless of what
it contains.

| Loop | Command | When to run |
| --- | --- | --- |
| Shell syntax | `bash -n git-loopy/shell/git-loopy.sh git-loopy/shell/install.sh git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh` | Any change under `git-loopy/shell/` |
| Python suite | `uv run --project git-loopy/python --all-extras python -m pytest -q git-loopy/python/tests` | Any change to the Python Runner, the Conformance fixtures, `PROMPT.md`, the Skill catalog, or the CI workflows |
| Rust Dashboard core | `cargo fmt --manifest-path git-loopy/tui/Cargo.toml --check && cargo clippy --manifest-path git-loopy/tui/Cargo.toml --all-targets -- -D warnings && cargo test --manifest-path git-loopy/tui/Cargo.toml` | Any change under `git-loopy/tui/` or to the Event schema the Dashboard reads |
| Shell Orchestrator suites | `for t in git-loopy/shell/tests/test-*.sh; do bash "$t" \|\| exit 1; done` | Any change under `git-loopy/shell/` or to a Conformance fixture |
| PowerShell Orchestrator suites | `for t in git-loopy/powershell/tests/test-*.ps1; do pwsh -NoLogo -NoProfile -File "$t" \|\| exit 1; done` | Any change under `git-loopy/powershell/` or to a Conformance fixture |

For a docstring-or-prose change, run targeted Python tests instead of the full
Python suite only when publishing through a pull request (CI runs the full suite
on every pull request with no path filters) or contributing a Lane in Parallel
mode (Integration runs the whole table); a serial Run pushes straight to the
default branch and still runs the full suite. To qualify, compare each changed
Python file against the merge-base yourself: both versions must parse to the same
`ast.dump` after removing module, class and function docstrings, and every other
changed file must be Markdown. Run each changed module's own
`tests/test_<module>*.py`, every test file that names a changed file (find them
with `git grep -l -F <basename> -- git-loopy/python/tests` for each changed file),
and the tracked-tree guards `tests/test_no_*.py` and `tests/test_glossary_*.py`;
Markdown-only changes run this same set too. Record the targeted tests run and
the full-suite backstop (CI or Integration). The known flakes are
`test_release_installation.py::test_reclaims_setup_children_not_just_their_parent[cancelled-init]`
(a pseudo-terminal "Operation not permitted" error under host load),
`test_release_smoke.py::test_a_run_still_in_flight_at_the_deadline_is_never_passed`
(its fixture deadline expires under host load), and
`test_attach_command.py::test_a_dashboard_fault_restores_the_terminal`
(a deadline or teardown timeout under host load; #734 fixes it, so remove this
entry when #734 lands). When a listed test fails in a full run, rerun it alone:
if it passes, report the flake and treat that failure as green without rerunning
the full suite; an unlisted failure or one that also fails alone is a real
failure. Record each encountered known flake and its result when rerun alone;
change this list only through normal review.

Notes on what is deliberately *not* in the table, because every row in it is a
blocking gate:

- **`ruff`** carries pre-existing errors, so declaring it would make Integration
  permanently red. Run `uv run --project git-loopy/python --all-extras ruff check
  git-loopy/python` on the files you touched and keep them clean; it becomes a gate
  row once the backlog is cleared.
- **The Conformance adapter subset** the CI runs as its own fast job
  (`test_conformance.py`, `test_release_identity_conformance.py`) is already
  inside the Python suite row; a second
  row would only pay for it twice.
- **The native Windows timezone proof** in `runner-family-gate.yml` repeats Rust
  format/lint checks and runs the native timezone API and executable regressions
  on Windows. Those tests are already part of the Rust suite above on a Windows
  host; another row would either repeat them there or require Windows APIs on an
  operator's non-Windows host. CI supplies that additional platform evidence.
- **`python -m git_loopy.skill_source`** (acquire and validate the pinned external
  Skill catalog, ADR-0023) reaches the network, so it can never be a gate: an
  unreachable upstream would make Integration red for a reason no change here
  caused. Its offline half — the immutable pin, the real fetch/checkout path over
  a `file://` remote, and every validation failure — is covered by
  `tests/test_skill_source.py` inside the Python suite row.
- **`git-loopy doctor --apply`** refreshes that same catalog before it repairs a
  Skill policy (#518), so it inherits the same exclusion for the same reason. Its
  offline half — the pin comparison, the `absent`/`drifted`/`matching` verdicts,
  the "refresh, do not prune" rule they impose on a missing-Skill row, and the
  refresh itself over a `file://` remote — is covered by
  `tests/test_skill_install.py` and `tests/test_doctorcmd.py` inside the Python
  suite row.
- **`python -m git_loopy.sdk_feed`** reports when the corporate Python feed
  carries a `github-copilot-sdk` release newer than the pin in the Runner
  manifest (#639). It reaches the network, so it can never be a gate: an
  unreachable feed would make Integration red for a reason no change here
  caused. Its offline half — the pin read from the manifest, and the
  comparison that reports a newer release, distinguishes a prerelease from a
  stable upgrade, and refuses to treat a feed error as no upgrade — is covered
  by `tests/test_sdk_feed.py` inside the Python suite row. Run it when working
  in this repository and surface any finding; a finding does not change the
  pin, and a feed error is never "no upgrade available".
- **A paired Skill change** (ADR-0064) is authored in a sibling clone of
  `bradcstevens/git-loopy-skills`, published, proved, then pinned. It is a
  working surface, not a loop: proving it reaches that clone, and an
  unreachable upstream would make Integration red for a reason no change here
  caused. The Iteration runs that proof itself. The gate still runs from this
  repository alone.
- **The cross-repo Skills proof** in `cross-repo-skills-proof.yml` (#632) acquires
  that same pinned catalog and then runs the three Python-suite checks that read
  it — that the profiles' Required and Permitted Skills resolve at the pin,
  that Skill reference routing resolves, and that the Skill-authored label vocabulary
  matches what this repository provisions — each of which otherwise skips rather
  than fails when the catalog is not acquired. It inherits the same network
  exclusion as `python -m git_loopy.skill_source` above for the same reason: an
  unreachable `bradcstevens/git-loopy-skills` would make Integration red for a
  reason no change here caused. CI supplies this as a required job precisely
  because Integration cannot, the same way it supplies the native Windows
  timezone proof above; the three checks stay offline-safe skips everywhere
  else, and the job's own shape is covered by
  `tests/test_cross_repo_skills_proof_workflow.py` inside the Python suite row.
- **`python -m git_loopy.release_rehearsal`** (prove a promoted stable snapshot
  before any public tag exists, ADR-0059) *runs this table* over its candidate,
  so declaring it as a row would make Integration gate itself recursively. It is
  release proof rather than a Lane gate: `release-promotion.yml` runs it per
  candidate and creates no tag without it, and the boundary itself — both
  triggers, every drift and refusal, the publication-input binding — is covered
  offline by `tests/test_release_rehearsal.py` inside the Python suite row.

Commands resolve their tools through `PATH` and are relative to the repository root,
because Integration runs them in a throwaway private worktree on whatever host the
operator has. Never hard-code a host-specific executable path here.

## Agent skills

Agent skills come from
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills),
the source of record. `git-loopy init` installs it at the revision this
repository pins (`git-loopy/python/git_loopy/skill_source.json`) into
`<config-home>/git-loopy/skills/`, and every Run refreshes that install — which
is the only Skill source a Run reads (ADR-0025). To type the same commands
yourself in `copilot`, install them into Copilot CLI as well:
`npx skills add bradcstevens/git-loopy-skills -g -a github-copilot`.

The same pin supplies seven **Agent profiles**, installed and repaired with
the catalog. Task type selects one of five execution profiles
(`implementation`, `bugfix`, `test`, `docs`, `chore`); unresolved types fall
back to `implementation`. The read-only Reviewer profile supplies the separate
review role; automatic Review-stage orchestration is separate delivery work.
Profiles own Required and Permitted Skills; `PROMPT.md` is their shared Run
base, and its retired `required-skills` key is refused. Every in-Run Agent and
its Subagents are isolated from persistent memory and cross-session history.
For exposure rules, the three-Skill Minimal policy, and isolation, read
[`docs/skill-policy.md`](docs/skill-policy.md#profiles-and-exposure).

For human-led planning, use `git-loopy plan [planning document]`. It launches
the pinned Planner in your interactive Copilot CLI with `/to-tickets`, outside
every Run; the graph remains a proposal until the Loop engineer approves it.
For Routing overrides, harness requirements, and launch refusals, read the
[Planner reference](git-loopy/python/README.md#planning-git-loopy-plan).

### Skills repository

[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
is a working surface this project changes, not merely consumes. A Skill prompt is
authored there and mirrored nowhere: there is no prompt tree in this checkout
to edit, and the installed catalog is repaired back to the pin rather than
kept as an edit surface (ADR-0025, ADR-0064).

A working clone is expected as a sibling of this checkout, at
`../git-loopy-skills` relative to the repository root. Acquire one with:

```bash
git clone https://github.com/bradcstevens/git-loopy-skills.git ../git-loopy-skills
```

That clone is the only place to author. Do not edit
`<config-home>/git-loopy/skills/`, `<config-home>/git-loopy/skill-catalog/`, or
`.git-loopy/skill-source/` — the first two are the install a Run refreshes, and
the third is a force-checkout of the pin. Pointing any of them at work in
progress discards it.

The order a paired change lands is a requirement, not a convention, and the
pin is last. Publish the Skill change upstream, prove the resulting revision
against git-loopy, and only then move the pin. A pin that moves before the
proof is the failure this sequence exists to prevent: the pin is what every
installation refreshes from.

1. **Publish.** Commit the Skill edit in the sibling clone and push it, so the
   change is a full 40-character SHA on
   `bradcstevens/git-loopy-skills`. An uncommitted tree is not a published
   revision.
2. **Prove.** Run
   `uv run --project git-loopy/python python -m git_loopy.skill_candidate ../git-loopy-skills`
   against a checkout of that published revision, clean of later edits. A pass
   says the cross-repo proofs hold. It does not move the pin. A dirty tree is
   a preview, not this proof.
3. **Pin last.** Only after that proof passes, edit
   `git-loopy/python/git_loopy/skill_source.json` to that SHA.

The git-loopy half of a paired change names the upstream revision the pin
moves to and the Skill edit it carries, so the two halves are reviewable
together. A pin bump that leaves a reader inferring which Skill edit it
carries is not that change.

This working surface is not a feedback-loop row. The Integration gate reaches
no network, and the declared feedback loops still run from this repository
alone. An unreachable upstream must not make Integration red. Proving a
candidate is the Iteration's own step (ADR-0064).

### Chain hook

`.github/hooks/git-loopy-chain.json` registers two hooks for `/next`, both
through the repository-relative resolver `.github/hooks/git-loopy-chain.sh`.
`subagentStop` runs `chain.sh complete`, closing the finished chain hop in the
per-clone `.git-loopy/` ledger. `agentStop` runs `reenter`
(`.github/hooks/git-loopy-agent-stop.py`), which holds the parent agent open
with "Run /next now" while a completed hop is still unrouted, and gives up on a
hop after three unconfirmed requests.

The resolver finds the `/next` script at
`${COPILOT_HOME:-$HOME/.copilot}/skills/next/scripts/chain.sh`. If it is
missing, the resolver reports `no chain.sh found` on stderr and exits `0`,
rather than failing an agent that has nothing to do with the chain.
`.github/hooks/git-loopy-hook-log.sh` appends every invocation, including that
stand-down, to `.git-loopy/hook-invocations.jsonl`, so a chain that closes
nothing still leaves a record. Copilot runs repository hooks only in trusted
folders. All four files are written by `/setup-git-loopy-skills`; the
`agentStop` helper is a copy of the one bundled with that Skill, so re-run it
rather than editing the copy.

### Issue tracker

Issues live in this repo's GitHub Issues (managed via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

All five canonical triage roles use their default label strings (`needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See
`docs/agents/triage-labels.md`.

Issues whose titles begin with `PRD:` or `Spec:` (case-insensitive), and issues
labelled `wayfinder:map`, are planning documents, not executable tickets. Never
apply `ready-for-agent` to them; remove that label if present. Work their
implementation tickets instead.

Only a human asserts `task-type:planning` or `task-type:review`. A
`ready-for-agent` issue carrying either exact label is excluded as
`non_execution_task_type`, even alongside an execution Task type, until a human
retypes it to an execution Task type or removes `ready-for-agent` and hands it
to the Planner. The Runner never relabels it, and `git-loopy labels` neither
reports nor repairs this conflict.

### Release milestones

A `vX.Y.Z` GitHub milestone is solely the **Promotion** trigger: closing it
promotes the current matching `-alpha.N`/`-beta.N`/`-rc.N` Release line to stable.
The Release target is instead ratcheted from closed issues' `vX.Y.Z` Release-target
labels (ADR-0066); an issue with no such label changes no version. Never invent a
milestone — `gh api repos/{owner}/{repo}/milestones --jq '.[].title'` lists the
ones that exist. See `docs/releases/README.md#release-target-and-promotion`.

### Domain docs

Single-context layout: one `GLOSSARY.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.
